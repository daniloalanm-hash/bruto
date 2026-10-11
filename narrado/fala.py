"""Fala: transcrição por trechos, voz de IA e a edição automática das tomadas.

Edição automática (o que eu fazia à mão): cada trecho falado é alinhado ao roteiro. Quando a mesma
parte do roteiro aparece mais de uma vez (você errou e repetiu), fica só a ÚLTIMA versão; o que
foi dito antes e regravado depois é descartado. A legenda usa a grafia do roteiro (nomes próprios,
números, pontuação) sempre que a palavra falada corresponde a ela.
"""
import re
import subprocess
from difflib import SequenceMatcher

from comum import norm, run

SILENCIO_DB = -38
SILENCIO_MIN = 0.22


# ---------------------------------------------------------------- transcrição
def _audio16k(src):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def trechos_de_fala(src):
    """Divide a gravação nos silêncios: cada tentativa/regravação vira um trecho separado."""
    r = run(["ffmpeg", "-hide_banner", "-i", src, "-af", f"silencedetect=n={SILENCIO_DB}dB:d={SILENCIO_MIN}",
             "-f", "null", "-"])
    ini = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    fim = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    out, cur = [], 0.0
    for s, e in zip(ini, fim):
        if s - cur > 0.15:
            out.append((max(cur - 0.08, 0), s + 0.08))
        cur = e
    return out, cur


def transcrever(src, modelo="medium", dicas=""):
    """Transcreve cada trecho isoladamente (sem contexto do anterior), com tempo por palavra."""
    from faster_whisper import WhisperModel
    audio = _audio16k(src)
    dur = len(audio) / 16000
    trechos, ultimo_fim = trechos_de_fala(src)
    if dur - ultimo_fim > 0.15:
        trechos.append((max(ultimo_fim - 0.08, 0), dur))
    if not trechos:
        trechos = [(0, dur)]
    model = WhisperModel(modelo, device="cpu", compute_type="int8")
    palavras = []
    for ci, (a, b) in enumerate(trechos):
        seg = audio[int(a * 16000):int(b * 16000)]
        res, _ = model.transcribe(seg, language="pt", word_timestamps=True, vad_filter=False,
                                  condition_on_previous_text=False, beam_size=5,
                                  initial_prompt=dicas or None)
        for s in res:
            for w in s.words:
                t = w.word.strip()
                if t:
                    palavras.append({"w": t, "s": round(a + w.start, 3), "e": round(a + w.end, 3), "c": ci})
    print(f"transcrição: {len(trechos)} trechos, {len(palavras)} palavras")
    return palavras


def voz_ia(texto, destino, voz="pt-BR-AntonioNeural", ritmo="+4%"):
    """Gera a narração por IA (Edge TTS, gratuito) com o tempo de cada palavra."""
    import asyncio
    import edge_tts

    async def _go():
        com = edge_tts.Communicate(texto, voz, rate=ritmo, boundary="WordBoundary")
        pal = []
        with open(destino, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    pal.append({"w": ch["text"], "s": round(s, 3), "e": round(s + ch["duration"] / 1e7, 3), "c": 0})
        return pal
    return asyncio.run(_go())


# ---------------------------------------------------------------- alinhamento ao roteiro
def tokens_roteiro(cenas):
    """Lista de palavras do roteiro: (normalizada, cena, palavra original com pontuação)."""
    out = []
    for ci, c in enumerate(cenas):
        for w in c["texto"].split():
            n = norm(w)
            if n:
                out.append((n, ci, w))
    return out


_sim_cache = {}


def sim(a, b):
    k = (a, b)
    if k not in _sim_cache:
        _sim_cache[k] = 1.0 if a == b else SequenceMatcher(None, a, b).ratio()
    return _sim_cache[k]


def _alinha_trecho(S, C, esperado):
    """Melhor posição do trecho C dentro do roteiro S.

    Variações da mesma palavra ("missões"/"missão") contam como iguais dentro da janela, e janelas
    longe de onde a fala deveria estar (logo depois do trecho anterior, ou voltando um pouco numa
    regravação) perdem pontos. Devolve (inicio, nota, opcodes, C_canonico).
    """
    Sn = [s[0] for s in S]
    n = len(C)
    melhor = (0, -9.0, None, C)
    for p in range(0, max(1, len(Sn) - max(1, n // 2) + 1)):
        janela = Sn[p:p + n + 3]
        cc = []
        for t in C:
            alvo = max(janela, key=lambda v: sim(t, v)) if janela else t
            cc.append(alvo if len(t) >= 3 and sim(t, alvo) >= 0.75 else t)
        sm = SequenceMatcher(None, janela, cc, autojunk=False)
        r = sm.ratio()
        d = p - esperado
        if d < -40:
            r -= 0.006 * (-40 - d)
        elif d > 8:
            r -= 0.006 * (d - 8)
        if r > melhor[1] + 1e-9:
            melhor = (p, r, sm.get_opcodes(), cc)
    return melhor


def editar(palavras, cenas, nomes=()):
    """Decide o que fica (última tomada de cada parte), a cena de cada palavra e o texto da legenda."""
    S = tokens_roteiro(cenas)
    por_trecho = {}
    for i, w in enumerate(palavras):
        w.update({"i": i, "idx": None, "manter": False, "cena": None, "legenda": w["w"]})
        por_trecho.setdefault(w["c"], []).append(w)

    termos_nome = {norm(t) for n in nomes for t in str(n).split() if len(norm(t)) >= 3}

    def nome_proprio(x):
        o = S[x][2]
        return any(ch.isdigit() for ch in o) or S[x][0] in termos_nome or \
            (o[:1].isupper() and x > 0 and not re.search(r"[.!?:]$", S[x - 1][2]))

    def usar_roteiro(w, x, troca=False):
        """Legenda com a grafia do roteiro só quando é a mesma palavra, um nome próprio/número, ou
        (troca=True) um erro de transcrição evidente ("graça" no lugar de "gasta").
        Variações reais da fala ("missões" x "missão", "tempo" x "tamanho") ficam como foram ditas."""
        s = sim(norm(w["w"]), S[x][0])
        w["rot"] = s == 1.0 or nome_proprio(x) or (troca and s >= 0.55)

    # 1) alinha cada trecho ao roteiro e mapeia palavra -> posição no roteiro
    validos = set()
    esperado = 0
    for c, ws in sorted(por_trecho.items(), key=lambda kv: kv[1][0]["s"]):
        C = [norm(w["w"]) for w in ws]
        if not any(C):
            continue
        p, nota, ops, _ = _alinha_trecho(S, C, esperado)
        iguais = sum(i2 - i1 for tag, i1, i2, j1, j2 in ops if tag == "equal") if ops else 0
        if nota < 0.42 or iguais < 2:
            continue  # comentário fora do roteiro, ruído ou teste de microfone
        validos.add(c)
        ultimo_op = len(ops) - 1
        for oi, (tag, i1, i2, j1, j2) in enumerate(ops):
            if tag == "equal":
                for k in range(i2 - i1):
                    ws[j1 + k]["idx"] = p + i1 + k
                    usar_roteiro(ws[j1 + k], p + i1 + k)
            elif tag == "replace":
                fal = ws[j1:j2]
                rot = [x for x in range(p + i1, p + i2) if x < len(S)]
                if not rot:
                    continue
                # nas pontas do trecho a janela do roteiro é folgada: encosta a parte falada no que alinhou
                if oi == 0 and len(rot) > len(fal):
                    rot = rot[-len(fal):]
                elif oi == ultimo_op and len(rot) > len(fal):
                    rot = rot[:len(fal)]
                if len(fal) == len(rot):
                    # mesma quantidade de palavras: é a mesma frase com erro de transcrição ou troca de palavra
                    ponta = oi == 0 or oi == ultimo_op
                    for w, x in zip(fal, rot):
                        if ponta and sim(norm(w["w"]), S[x][0]) < 0.5 and not nome_proprio(x):
                            continue  # palavra de ligação na ponta ("as", "mas"): não é aquela do roteiro
                        w["idx"] = x
                        usar_roteiro(w, x, troca=True)
                else:
                    juntos_f = "".join(norm(w["w"]) for w in fal)
                    juntos_r = "".join(S[x][0] for x in rot)
                    parecido = SequenceMatcher(None, juntos_f, juntos_r).ratio()
                    tem_nome = any(nome_proprio(x) for x in rot)
                    if parecido >= 0.7 and tem_nome:
                        # ex.: falado "of the walker", roteiro "of Dawnwalker,": a legenda segue o roteiro
                        for k, w in enumerate(fal):
                            w["idx"] = rot[min(k, len(rot) - 1)]
                            w["rot"] = True
                            if k >= len(rot):
                                w["junta"] = True  # o texto já está na palavra anterior
                    elif abs(len(fal) - len(rot)) <= 1:
                        for w, x in zip(fal, rot):  # só para saber a posição; a legenda segue o que foi dito
                            w["idx"] = x
        mapeados = [w["idx"] for w in ws if w["idx"] is not None]
        if mapeados:
            esperado = max(mapeados) + 1

    # 2) de trás para frente: cada parte do roteiro fica só com a última vez em que foi dita
    limite = len(S) + 1
    for w in reversed(palavras):
        if w["c"] not in validos or w["idx"] is None or w.get("junta"):
            continue
        if w["idx"] < limite:
            w["manter"] = True
            limite = w["idx"]
    # sobra de palavra "juntada" acompanha a palavra anterior da mesma tomada
    for k, w in enumerate(palavras):
        if w.get("junta") and k > 0 and palavras[k - 1]["c"] == w["c"]:
            w["manter"] = palavras[k - 1]["manter"]

    # 3) palavras sem correspondência ("Mas", interjeições) ou mal alinhadas ficam se estiverem
    #    no meio de uma tomada que ficou
    for c, ws in por_trecho.items():
        if c not in validos:
            continue
        mant = [k for k, w in enumerate(ws) if w["manter"]]
        if not mant:
            continue
        a, b = mant[0], mant[-1]
        for k, w in enumerate(ws):
            if a < k < b and not w["manter"]:
                # dentro de uma tomada mantida: só fica se não for repetição já dita depois
                if w["idx"] is None or not any(o["manter"] and o["idx"] == w["idx"] and o is not w for o in palavras):
                    w["manter"] = True
            elif k < a and w["idx"] is None and k == a - 1:
                w["manter"] = True  # palavra de ligação no começo da tomada ("Mas e pra você")

    # 3b) improviso curto entre duas partes mantidas ("Mas e você?" no lugar de "Você"): se traz uma
    #     palavra do roteiro que ficou faltando exatamente naquele ponto, o trecho inteiro fica
    ditos_ate_aqui = {w["idx"] for w in palavras if w["manter"] and w["idx"] is not None}
    for c, ws in sorted(por_trecho.items(), key=lambda kv: kv[1][0]["s"]):
        if len(ws) > 5 or any(w["manter"] for w in ws):
            continue
        antes = [w for w in palavras if w["manter"] and w["idx"] is not None and w["e"] <= ws[0]["s"] + 0.05]
        depois = [w for w in palavras if w["manter"] and w["idx"] is not None and w["s"] >= ws[-1]["e"] - 0.05]
        if not antes or not depois:
            continue
        lacuna = [x for x in range(antes[-1]["idx"] + 1, depois[0]["idx"]) if x not in ditos_ate_aqui]
        achou = None
        for w in ws:
            for x in lacuna:
                if sim(norm(w["w"]), S[x][0]) >= 0.75:
                    achou = (w, x)
                    break
            if achou:
                break
        if achou:
            w, x = achou
            w["idx"] = x
            ditos_ate_aqui.add(x)
            for o in ws:
                o["manter"] = True

    # 4) cena de cada palavra + texto da legenda com a grafia do roteiro
    ultima_cena = 0
    mantidas = [w for w in palavras if w["manter"]]
    for k, w in enumerate(mantidas):
        if w["idx"] is not None and w["idx"] < len(S):
            w["cena"] = S[w["idx"]][1]
            if w.get("junta"):
                w["legenda"] = ""
            elif w.get("rot"):
                w["legenda"] = S[w["idx"]][2]
            else:
                pont = re.search(r"[.,:;!?…]+$", S[w["idx"]][2])
                w["legenda"] = re.sub(r"[.,:;!?…]+$", "", w["w"]) + (pont.group(0) if pont else "")
        else:
            prox = next((o for o in mantidas[k + 1:] if o["idx"] is not None), None)
            w["cena"] = S[prox["idx"]][1] if prox and prox["c"] == w["c"] else ultima_cena
        ultima_cena = w["cena"]
    # garante ordem de cenas não decrescente (uma palavra solta não volta para a cena anterior)
    for k in range(1, len(mantidas)):
        if mantidas[k]["cena"] < mantidas[k - 1]["cena"]:
            mantidas[k]["cena"] = mantidas[k - 1]["cena"]
    # partes do roteiro que não foram ditas em nenhuma tomada (a tela avisa)
    ditos = {w["idx"] for w in mantidas if w["idx"] is not None}
    faltando = [S[x][2] for x in range(len(S)) if x not in ditos]
    for w in palavras:
        w.pop("junta", None)
        w.pop("rot", None)
    return palavras, faltando
