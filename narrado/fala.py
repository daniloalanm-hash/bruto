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


# modo "pacote" (vários trechos por janela do Whisper): mais rápido em tese, mas no teste lado a lado ganhou
# pouco tempo e errou mais (palavra trocando de tomada). O padrão é "isolado": cada trecho sozinho.
JANELA = 26.0      # s de fala por chamada do Whisper (ele sempre processa janelas de 30 s)
SEPARADOR = 1.5    # silêncio entre trechos empacotados na mesma janela


def _empacotar(trechos):
    """Agrupa trechos numa mesma janela do Whisper. Ele gasta o mesmo tempo para 1 s ou 30 s de áudio;
    transcrever 60+ trechos um a um (gravação com muitos erros) levava mais de 15 min.

    Os trechos são intercalados (janela k recebe os trechos k, k+G, k+2G...): tomadas repetidas da mesma frase
    ficam lado a lado na gravação e, se caíssem na mesma janela, o Whisper juntaria/omitiria a repetição.
    """
    n = len(trechos)
    durs = [b - a for a, b in trechos]
    g = max(1, int(-(-(sum(durs) + SEPARADOR * n) // JANELA)))
    while True:
        grupos = [list(range(k, n, g)) for k in range(g)]
        if g >= n or all(sum(durs[i] for i in gr) + SEPARADOR * (len(gr) - 1) <= JANELA or len(gr) == 1
                         for gr in grupos):
            return [gr for gr in grupos if gr]
        g += 1


def _distribuir(segs, mapa, trechos):
    """Devolve cada palavra de uma janela empacotada ao seu trecho, com o tempo na gravação original.

    O tempo por palavra do Whisper escorrega para o silêncio entre trechos (fim de frase atrasado, começo
    adiantado), então a decisão é por SEGMENTO (frase), cujo tempo é mais confiável: o segmento vai inteiro para
    o trecho com que mais se sobrepõe. Só um segmento que atravessa dois trechos é dividido palavra a palavra,
    pelo ponto confiável de cada uma (início dentro de um trecho; senão o fim; senão o mais próximo).
    """
    def sobre(a0, a1, b0, b1):
        return max(0.0, min(a1, b1) - max(a0, b0))

    def por_palavra(ws, we):
        for m in mapa:
            if m[0] - 0.05 <= ws <= m[1]:
                return m
        for m in mapa:
            if m[0] <= we <= m[1] + 0.05:
                return m
        meio = (ws + we) / 2
        return min(mapa, key=lambda m: min(abs(meio - m[0]), abs(meio - m[1])))

    saida = []
    for s0, s1, palavras in segs:
        if not palavras:
            continue
        s0, s1 = min(s0, palavras[0][1]), max(s1, palavras[-1][2])
        ov = [sobre(s0, s1, m[0], m[1]) for m in mapa]
        tocados = [k for k, v in enumerate(ov) if v > 0.25]
        unico = mapa[max(range(len(mapa)), key=lambda k: ov[k])] if len(tocados) <= 1 and max(ov) > 0 else None
        for t, ws, we in palavras:
            ini, fim, ci = unico or por_palavra(ws, we)
            a = trechos[ci][0]
            x0 = min(max(ws, ini), fim)
            x1 = min(max(we, x0), fim)
            saida.append({"w": t, "s": round(a + x0 - ini, 3), "e": round(a + x1 - ini, 3), "c": ci})
    # palavra espremida (tempo zero no fim/começo do trecho): divide o tempo com a vizinha, para a legenda
    # aparecer e o corte não engolir o som dela
    for k, w in enumerate(saida):
        if w["e"] - w["s"] >= 0.06:
            continue
        ant = saida[k - 1] if k and saida[k - 1]["c"] == w["c"] else None
        prox = saida[k + 1] if k + 1 < len(saida) and saida[k + 1]["c"] == w["c"] else None
        if ant and ant["e"] - ant["s"] > 0.2 and w["s"] >= ant["e"] - 0.01:
            corte = round(max(ant["s"] + 0.1, ant["e"] - 0.3), 3)
            ant["e"], w["s"] = corte, corte
            w["e"] = max(w["e"], round(corte + 0.1, 3))
        elif prox and prox["e"] - prox["s"] > 0.2 and w["e"] <= prox["s"] + 0.01:
            corte = round(min(prox["e"] - 0.1, prox["s"] + 0.3), 3)
            w["s"] = min(w["s"], round(corte - 0.1, 3))
            w["e"], prox["s"] = corte, corte
    return saida


def transcrever(src, modelo="medium", dicas="", modo="isolado", model=None):
    """Transcreve os trechos falados com tempo por palavra; cada palavra sabe de qual trecho (tomada) veio.

    Trechos curtos são empacotados (com silêncio entre eles) numa mesma janela do Whisper, e cada palavra
    volta para o seu trecho pelo instante em que foi dita.
    """
    import numpy as np
    from faster_whisper import WhisperModel
    audio = _audio16k(src)
    dur = len(audio) / 16000
    trechos, ultimo_fim = trechos_de_fala(src)
    if dur - ultimo_fim > 0.15:
        trechos.append((max(ultimo_fim - 0.08, 0), dur))
    if not trechos:
        trechos = [(0, dur)]
    model = model or WhisperModel(modelo, device="cpu", compute_type="int8")
    palavras = []
    if modo in ("isolado", "curto"):
        # cada trecho sozinho (mais fiel; "curto" usa janela do tamanho do trecho em vez de 30 s)
        for ci, (a, b) in enumerate(trechos):
            seg = audio[int(a * 16000):int(b * 16000)]
            extra = {"chunk_length": int(min(30, max(8, -(-(b - a + 1) // 1))))} if modo == "curto" else {}
            res, _ = model.transcribe(seg, language="pt", word_timestamps=True, vad_filter=False,
                                      condition_on_previous_text=False, beam_size=5,
                                      initial_prompt=dicas or None, **extra)
            for sg in res:
                for w in sg.words:
                    t = w.word.strip()
                    if t:
                        palavras.append({"w": t, "s": round(a + w.start, 3), "e": round(a + w.end, 3), "c": ci})
        print(f"transcrição ({modo}): {len(trechos)} trechos, {len(palavras)} palavras", flush=True)
        return palavras
    sep = np.zeros(int(SEPARADOR * 16000), np.float32)
    grupos = _empacotar(trechos)
    for grupo in grupos:
        partes, mapa, pos = [], [], 0.0  # mapa: (início no pacote, fim no pacote, trecho)
        for k, ci in enumerate(grupo):
            a, b = trechos[ci]
            seg = audio[int(a * 16000):int(b * 16000)]
            if k:
                partes.append(sep)
                pos += SEPARADOR
            partes.append(seg)
            mapa.append((pos, pos + len(seg) / 16000, ci))
            pos += len(seg) / 16000
        res, _ = model.transcribe(np.concatenate(partes), language="pt", word_timestamps=True, vad_filter=False,
                                  condition_on_previous_text=False, beam_size=5,
                                  initial_prompt=dicas or None)
        segs = [(sg.start, sg.end, [(w.word.strip(), w.start, w.end) for w in sg.words if w.word.strip()])
                for sg in res]
        doja = _distribuir(segs, mapa, trechos)
        palavras += doja
    palavras.sort(key=lambda w: (w["c"], w["s"]))  # as janelas são intercaladas: volta à ordem da gravação
    print(f"transcrição: {len(trechos)} trechos em {len(grupos)} janelas, {len(palavras)} palavras", flush=True)
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


NUM_EN = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
          "8": "eight", "9": "nine"}
NUM_PT = {"0": "zero", "1": "um", "2": "dois", "3": "tres", "4": "quatro", "5": "cinco", "6": "seis", "7": "sete",
          "8": "oito", "9": "nove"}


def sim(a, b):
    k = (a, b)
    if k not in _sim_cache and (re.search(r"\d", a) and re.search(r"[a-z]", a + b)):
        # "5m" x "fivem", "4k" x "quatrok": compara também com os dígitos por extenso
        r = max(_sim_base(a, b), *(_sim_base("".join(t.get(ch, ch) for ch in a), b) for t in (NUM_EN, NUM_PT)))
        _sim_cache[k] = r
    return _sim_base(a, b) if k not in _sim_cache else _sim_cache[k]


def _sim_base(a, b):
    k = ("base", a, b)
    if k not in _sim_cache:
        r = 1.0 if a == b else SequenceMatcher(None, a, b).ratio()
        if r < 0.85 and len(a) >= 4 and len(b) > len(a) and b.startswith(a):
            r = 0.85  # palavra cortada no meio pelo Whisper ("Grave..." de "Graveyard")
        _sim_cache[k] = r
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
            cc.append(alvo if (len(t) >= 3 or re.search(r"\d[a-z]", t)) and sim(t, alvo) >= 0.75 else t)
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


PALAVRAS_LIGACAO = {
    "e", "o", "a", "os", "as", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "um", "uma",
    "que", "se", "por", "pra", "para", "com", "ao", "aos", "mas", "ou", "eh", "ah", "tipo", "ja", "nao",
}
ALUCINACAO = re.compile(r"legendas pela comunidade(?: (?:do |da )?amara ?\.? ?org)?|amara ?\.org|obrigad[oa] por assistir|inscreva-se no canal|"
                        r"legendado por|transcri[cç][aã]o por", re.I)


RUIDOS = {"ahem", "hmm", "hm", "hmmm", "uhm", "ehm", "mhm", "ahn"}


def tirar_alucinacao(palavras):
    """Tira as frases que o Whisper inventa no silêncio ("Legendas pela comunidade Amara.org"), mesmo quando
    vêm grudadas numa fala de verdade no mesmo trecho; o resto do trecho continua valendo."""
    fora = set()
    por_c = {}
    for k, w in enumerate(palavras):
        por_c.setdefault(w["c"], []).append(k)
    for ks in por_c.values():
        # trecho "falado rápido demais para ser real": o Whisper repetiu as dicas (nome do jogo) num ruído ou
        # respiro — ex.: "Car Parking Multiplayer 2" inteiro em 0,08 s. Gente fala no máximo ~7 palavras/s.
        ws = [palavras[k] for k in ks]
        dur = ws[-1]["e"] - ws[0]["s"]
        if len(ws) >= 2 and dur < 0.12 * len(ws) and sum(1 for w in ws if w["e"] - w["s"] < 0.03) >= len(ws) / 2:
            fora.update(ks)
            continue
        texto, pos = "", []
        for k in ks:
            ini = len(texto) + (1 if texto else 0)
            texto = f"{texto} {palavras[k]['w']}" if texto else palavras[k]["w"]
            pos.append((ini, len(texto), k))
        for m in ALUCINACAO.finditer(texto):
            for a, b, k in pos:
                if a < m.end() and b > m.start():
                    fora.add(k)
    return [w for k, w in enumerate(palavras) if k not in fora]


def _tirar_desgarradas(ws, S):
    """Palavra que o alinhamento jogou longe do resto da tomada ("Qual É O jogo" -> "nem O calendário"):
    um pedacinho de 1 ou 2 palavras de ligação, separado da sequência principal por um salto no roteiro.
    Se ficasse, contaria como "dito por último" e apagaria a frase inteira de uma tomada anterior."""
    marc = [w for w in ws if w["idx"] is not None]
    if len(marc) < 3:
        return
    corridas, atual = [], [marc[0]]
    for w in marc[1:]:
        if 0 < w["idx"] - atual[-1]["idx"] <= 3:
            atual.append(w)
        else:
            corridas.append(atual)
            atual = [w]
    corridas.append(atual)
    if len(corridas) < 2:
        return
    maior = max(corridas, key=len)

    def forte(w):  # palavra de conteúdo que realmente bate com a do roteiro naquela posição
        a = norm(w["w"])
        return a not in PALAVRAS_LIGACAO and len(a) >= 3 and sim(a, S[w["idx"]][0]) >= 0.75
    for r in corridas:
        if r is maior:
            continue
        so_ligacao = len(r) <= 2 and all(norm(w["w"]) in PALAVRAS_LIGACAO or len(norm(w["w"])) <= 2 for w in r)
        # pedaço encaixado só por posição ("Mas e você? Qual o" -> "os títulos nem o"): nenhuma palavra bate
        if so_ligacao or not any(forte(w) for w in r):
            for w in r:
                w["idx"] = None
                w.pop("rot", None)


def erro_de_ouvido(a, b, cru=""):
    """Diferença típica de transcrição, não de fala: uma letra trocada no meio da palavra ("jugar" x "jogar")
    ou palavra cortada pelo Whisper ("grave" de "graveyard"). Flexão real ("missão" x "missões") muda o final."""
    if b.startswith(a):
        # só palavra que o Whisper marcou como cortada ("Grave..."); "ganhar" x "ganharam" é flexão de verdade
        return len(a) >= 4 and len(b) > len(a) + 1 and bool(re.search(r"(\.\.\.|…|[-‐])$", cru))
    if b.endswith(a):
        return False  # contração do roteiro ("praquele") x o que foi dito ("aquele"): vale o falado
    if len(a) >= 5 and 2 <= len(b) - len(a) <= 3:
        # sílaba engolida pelo Whisper ("replay" de "roleplay"): o falado aparece inteiro, em ordem, no roteiro
        it = iter(b)
        if all(ch in it for ch in a):
            return True
    if len(a) == len(b) >= 4:
        dif = [i for i in range(len(a)) if a[i] != b[i]]
        return len(dif) == 1 and dif[0] < len(a) - 2
    return False


def juntar_hifens(palavras):
    """O Whisper separa palavra com hífen ("quinta" + "-feira", "E" + "-Day"): junta de volta."""
    saida = []
    for w in palavras:
        if not norm(w["w"]) or norm(w["w"]) in RUIDOS:
            continue  # traço/pontuação solta ou pigarro ("Ahem"): não é fala e atrapalha o alinhamento
        ant = saida[-1] if saida else None
        if ant and w["w"].startswith("-") and ant["c"] == w["c"] and w["s"] - ant["e"] < 0.35:
            ant["w"] = ant["w"].rstrip(",.") + w["w"]
            ant["e"] = w["e"]
            continue
        saida.append(dict(w))
    return saida


def editar(palavras, cenas, nomes=()):
    """Decide o que fica (última tomada de cada parte), a cena de cada palavra e o texto da legenda."""
    palavras = tirar_alucinacao(juntar_hifens(palavras))
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
        a, b = norm(w["w"]), S[x][0]
        s = sim(a, b)
        w["rot"] = s == 1.0 or nome_proprio(x) or (troca and s >= 0.55) or erro_de_ouvido(a, b, w["w"])

    # 1) alinha cada trecho ao roteiro e mapeia palavra -> posição no roteiro
    validos = set()
    esperado = 0
    for c, ws in sorted(por_trecho.items(), key=lambda kv: kv[1][0]["s"]):
        C = [norm(w["w"]) for w in ws]
        if not any(C):
            continue
        p, nota, ops, _ = _alinha_trecho(S, C, esperado)
        iguais = sum(i2 - i1 for tag, i1, i2, j1, j2 in ops if tag == "equal") if ops else 0
        # palavras de conteúdo que bateram (não conta "e", "o", "de"...): tentativa que parou no começo
        # ("E o…") não pode valer como a última tomada daquele trecho e derrubar a tomada completa
        conteudo = sum(1 for tag, i1, i2, j1, j2 in (ops or []) if tag == "equal"
                       for k in range(j1, j2) if C[k] not in PALAVRAS_LIGACAO and (len(C[k]) >= 3 or C[k].isdigit()))
        if nota < 0.42 or iguais < 2 or conteudo < 1:
            continue  # comentário fora do roteiro, ruído, teste de microfone ou começo abandonado
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
                        sx = sim(norm(w["w"]), S[x][0])
                        if ponta and sx < 0.5 and (not nome_proprio(x) or sx < 0.45):
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
        _tirar_desgarradas(ws, S)
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
            abandonada = w["idx"] is None and re.search(r"(\.\.\.|…|[-‐])$", w["w"])
            if abandonada:
                continue  # palavra que você começou e largou ("Como..."): sai do áudio
            if a < k < b and not w["manter"]:
                # dentro de uma tomada mantida: só fica se não for repetição já dita depois
                if w["idx"] is None or not any(o["manter"] and o["idx"] == w["idx"] and o is not w for o in palavras):
                    w["manter"] = True
            elif k < a and w["idx"] is None and k == a - 1:
                w["manter"] = True  # palavra de ligação no começo da tomada ("Mas e pra você")

    # 3c) fim (ou começo) de frase mal ouvido: "…que ama Survival War" no lugar de "horror". Se a tomada
    #     mantida termina em palavras sem correspondência e as posições seguintes do roteiro não foram
    #     ditas em lugar nenhum, essas palavras são o final da frase: ficam (com a grafia do roteiro)
    ditos = {w["idx"] for w in palavras if w["manter"] and w["idx"] is not None}
    for c, ws in por_trecho.items():
        mant = [k for k, w in enumerate(ws) if w["manter"] and w["idx"] is not None]
        if c not in validos or not mant:
            continue
        a, b = mant[0], mant[-1]
        cauda = ws[b + 1:]
        if cauda and len(cauda) <= 2 and all(w["idx"] is None and not w["manter"] for w in cauda):
            pos = list(range(ws[b]["idx"] + 1, ws[b]["idx"] + 1 + len(cauda)))
            if all(x < len(S) and x not in ditos and S[x][1] == S[ws[b]["idx"]][1] for x in pos):
                for w, x in zip(cauda, pos):
                    w.update({"idx": x, "manter": True, "rot": True})
                    ditos.add(x)
        cabeca = ws[:a]
        if cabeca and len(cabeca) <= 2 and all(w["idx"] is None and not w["manter"] for w in cabeca):
            pos = list(range(ws[a]["idx"] - len(cabeca), ws[a]["idx"]))
            if all(x >= 0 and x not in ditos and S[x][1] == S[ws[a]["idx"]][1] for x in pos):
                for w, x in zip(cabeca, pos):
                    w.update({"idx": x, "manter": True, "rot": True})
                    ditos.add(x)

    # 3d) palavra fora do roteiro colada (sem pausa) na borda de uma tomada mantida, fechando a tomada
    #     ("bilhão | de dólares", "Isso é | mais que o dobro", "Just | See" = Justice): fica, até 3 palavras —
    #     senão o áudio corta no meio de uma frase falada de uma vez.
    COLADO = 0.12

    def abandonada(w):
        return bool(re.search(r"(\.\.\.|…|[-‐])$", w["w"]))
    for c, ws in por_trecho.items():
        if c not in validos:
            continue
        k = 0
        while k < len(ws):
            if not ws[k]["manter"]:
                k += 1
                continue
            a = k
            while k + 1 < len(ws) and ws[k + 1]["manter"]:
                k += 1
            b = k
            k += 1
            # o improviso colado só fica se fecha a tomada (vai até o fim/começo dela): senão é o começo
            # de uma tentativa que foi largada ("Você vai comprar o novo…")
            j = b + 1
            while j < len(ws) and j - b <= 3 and not ws[j]["manter"] and ws[j]["idx"] is None and \
                    not abandonada(ws[j]) and ws[j]["s"] - ws[j - 1]["e"] < COLADO:
                j += 1
            if j == len(ws) and j > b + 1:
                ult = ws[b]
                for w in ws[b + 1:j]:
                    w["manter"] = True
                    w["estendida"] = True
                prim = ws[b + 1]
                if ult["idx"] is not None:
                    alvo = S[ult["idx"]][0]
                    a1, junto = norm(ult["w"]), norm(ult["w"]) + norm(prim["w"])
                    if sim(junto, alvo) > sim(a1, alvo) + 0.1 or \
                            (alvo.startswith(a1) and len(a1) < len(alvo) and sim(junto, alvo) >= 0.6):
                        prim.update({"junta": True, "idx": ult["idx"]})  # "Just"+"See" = "Justice": legenda na anterior
                        ult["rot"] = True
                ult["pontua_depois"] = ws[j - 1]["i"]
            j = a - 1
            while j >= 0 and a - j <= 3 and not ws[j]["manter"] and ws[j]["idx"] is None and \
                    not abandonada(ws[j]) and ws[j + 1]["s"] - ws[j]["e"] < COLADO:
                j -= 1
            if j == -1 and a > 0:
                for w in ws[0:a]:
                    w["manter"] = True

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
            # traz junto só as palavras em volta até esbarrar no que já foi dito em outra tomada ou numa
            # palavra largada no meio ("Você | vai comprar o novo…" -> só "Você")
            def serve(o):
                return o is w or (not re.search(r"(\.\.\.|…|[-‐])$", o["w"]) and
                                  (o["idx"] is None or o["idx"] not in ditos_ate_aqui))
            iw = ws.index(w)
            a2 = iw
            while a2 > 0 and serve(ws[a2 - 1]):
                a2 -= 1
            b2 = iw
            while b2 + 1 < len(ws) and serve(ws[b2 + 1]):
                b2 += 1
            for o in ws[a2:b2 + 1]:
                o["manter"] = True
                if o["idx"] is not None:
                    ditos_ate_aqui.add(o["idx"])

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
                leg = re.sub(r"[.,:;!?…]+$", "", w["w"])
                if S[w["idx"]][2][:1].islower() and leg[:1].isupper() and not leg.isupper():
                    leg = leg[0].lower() + leg[1:]  # "Pouco meses" no meio da frase do roteiro: minúscula
                w["legenda"] = leg + (pont.group(0) if pont else "")
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
    # pontuação do roteiro que caía no meio ("bilhão. de dólares"): vai para o fim do que foi estendido
    por_i = {w["i"]: w for w in palavras}
    for w in palavras:
        k = w.get("pontua_depois")
        if k is None or not w["manter"] or k not in por_i:
            continue
        pont = re.search(r"[.,:;!?…]+$", w["legenda"])
        alvo = por_i[k]
        if pont and alvo["legenda"]:
            w["legenda"] = w["legenda"][:pont.start()]
            alvo["legenda"] = re.sub(r"[.,:;!?…]+$", "", alvo["legenda"]) + pont.group(0)

    return palavras, faltando
