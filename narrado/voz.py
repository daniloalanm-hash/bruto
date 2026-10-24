"""Monta a trilha de voz: junta só as palavras mantidas, encurta pausas longas e trata o som.

Regras fixas do Danilo:
- SEMPRE equalizar para a voz ficar encorpada (grave, calor, presença).
- Redução de ruído SÓ quando o ruído de fundo da gravação pedir (medido nos silêncios).
"""
import os
import re

from comum import ffmpeg, run

PAD_IN, PAD_OUT = 0.25, 0.40   # até onde se procura o som real antes/depois de cada trecho
FOLGA_IN, FOLGA_OUT = 0.03, 0.06  # folga mínima depois do fim real do som (não come consoante)
GAP_KEEP = 0.30                # pausas menores que isso ficam como estão (respiro natural dentro da frase)
GAP_EMENDA = 0.70              # fala contígua na gravação (nada descartado no meio) com pausa até isso fica INTEIRA,
                               # mesmo atravessando trechos do silencedetect: emendar ali só cria um degrau no áudio
GAP_NEW = 0.06                 # pausas maiores viram isso (ritmo de vídeo curto)
RUIDO_LIMITE = -58             # dB médio nos silêncios acima do qual a redução de ruído liga


def ruido_de_fundo(src, palavras=None):
    """Piso de ruído da gravação (dB): nível dos trechos mais silenciosos (10º percentil em janelas de 50 ms).

    Não depende da transcrição: respirações e falas descartadas não contam como "silêncio".
    """
    r = run(["ffmpeg", "-hide_banner", "-i", src, "-ac", "1", "-ar", "48000", "-af",
             "asetnsamples=n=2400,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
             "-f", "null", "-"])
    niveis = sorted(float(x) for x in re.findall(r"RMS_level=(-?[\d.]+)", r.stderr) if float(x) > -120)
    if len(niveis) < 20:
        return -90.0
    return niveis[len(niveis) // 10]


def envelope(src, passo=0.01):
    """Nível (dB) do áudio a cada 10 ms, para achar onde a fala começa e termina de verdade."""
    import subprocess
    import numpy as np
    bruto = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                           capture_output=True).stdout
    x = np.frombuffer(bruto, dtype=np.float32)
    n = int(16000 * passo)
    if len(x) < n:
        return np.array([-90.0]), passo
    x = x[: len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((x ** 2).mean(axis=1)) + 1e-9)
    return db, passo


SEGURA = 0.12   # quedas de volume menores que isso não encerram a palavra (o "s" de "três", o "e" final de "sete")


def bordas_reais(db, passo, s, e):
    """(início, fim) do som da fala em torno de [s, e], até PAD_IN/PAD_OUT além do que a transcrição marcou.

    Finais de palavra costumam ser baixos (sibilante, vogal átona) e vir depois de uma pequena queda;
    por isso o fim é o último trecho com voz antes de um silêncio de pelo menos SEGURA.
    """
    import numpy as np
    piso = float(np.percentile(db, 10))
    limiar = max(piso + 14, -58.0)
    segura = max(int(SEGURA / passo), 1)

    def varre(k, fim, passo_k):
        ultimo, abaixo = k, 0
        while k != fim:
            if db[k] > limiar:
                ultimo, abaixo = k, 0
            else:
                abaixo += 1
                if abaixo >= segura:
                    break
            k += passo_k
        return ultimo

    k_e = min(int(e / passo), len(db) - 1)
    k_fim = int(min((e + PAD_OUT) / passo, len(db) - 1))
    ce = (varre(k_e, k_fim, 1) + 1) * passo
    k_s = max(int(s / passo), 0)
    k_ini = int(max((s - PAD_IN) / passo, 0))
    cs = varre(k_s, k_ini, -1) * passo
    return min(cs, s), max(ce, e)


def _vale(db, passo, t0, t1, primeiro=True):
    """Instante de menor volume entre t0 e t1 (o "vão" entre duas palavras). Entre vários quase iguais
    (até 3 dB do mínimo), o primeiro (para fim de trecho) ou o último (para começo)."""
    import numpy as np
    a, b = max(int(t0 / passo), 0), min(int(t1 / passo), len(db) - 1)
    if b <= a:
        return t0
    trecho = db[a:b + 1]
    ok = np.where(trecho <= trecho.min() + 3)[0]
    k = ok[0] if primeiro else ok[-1]
    return (a + k) * passo


def trecho_de_fundo(db, passo, dur):
    """Início de um trecho só com o ruído de fundo da sala (sem voz, mas sem ser silêncio absoluto), usado nas
    pausas entre as falas: emendar voz com silêncio digital deixa um "buraco" audível (o fundo some e volta)."""
    import numpy as np
    n = max(int(dur / passo) + 2, 3)
    if len(db) <= n:
        return 0.0
    piso = float(np.percentile(db, 10))
    janelas = np.lib.stride_tricks.sliding_window_view(db, n)
    ok = np.where((janelas.max(axis=1) < piso + 6) & (janelas.min(axis=1) > piso - 12))[0]
    if not len(ok):
        ok = [int(np.argmin(np.abs(janelas.mean(axis=1) - piso)))]
    return float(ok[len(ok) // 2] * passo)


def definir_cortes(blocos, todas, db, passo):
    """Onde cortar cada bloco (cs/ce). O som real manda (bordas_reais); a palavra vizinha descartada só limita
    o corte no VÃO de menor volume entre as duas — o tempo que o Whisper dá para a vizinha costuma invadir a
    nossa palavra (sobretudo entre tomadas diferentes), e cortar ali come o começo/fim da fala."""
    import numpy as np
    piso = float(np.percentile(db, 10))
    limiar = max(piso + 14, -58.0)
    for b in blocos:
        # o Whisper às vezes marca o começo/fim da frase dentro do silêncio (0,3-0,4 s antes da voz): aperta para
        # o som real, senão entra ruído de fundo no começo e a legenda/chamada fica adiantada em relação à voz
        k0, k1 = int(b["s"] / passo), int(b["e"] / passo)
        acima = np.where(db[k0:k1 + 1] > limiar)[0]
        if len(acima):
            onset, offset = (k0 + acima[0]) * passo, (k0 + acima[-1] + 1) * passo
            if onset - b["s"] > 0.05:
                b["pal"][0]["s"] = b["s"] = round(min(onset - 0.02, b["pal"][0]["e"] - 0.05), 3)
            if b["e"] - offset > 0.08:
                b["pal"][-1]["e"] = b["e"] = round(max(offset + 0.04, b["pal"][-1]["s"] + 0.05), 3)
        som_s, som_e = bordas_reais(db, passo, b["s"], b["e"])
        cs, ce = max(som_s - FOLGA_IN, 0.0), som_e + FOLGA_OUT
        if b["primeiro"] > 0:
            ant = todas[b["primeiro"] - 1]
            if cs < ant["e"] + 0.02:
                ini = max(ant["s"] + 0.6 * (ant["e"] - ant["s"]), ant["e"] - 0.25)
                cs = max(cs, _vale(db, passo, ini, b["s"] + 0.03, primeiro=False))
            if not ant["manter"]:  # idem no começo: o fim da tomada descartada não entra
                k = int((b["s"] + 0.03) / passo)
                ki = max(int(cs / passo), 2)
                while k > ki and not (db[k - 1] < limiar and db[k - 2] < limiar and db[k - 3] < limiar):
                    k -= 1
                if k > ki:
                    cs = max(cs, k * passo - 0.03)
        if b["ultimo"] + 1 < len(todas):
            prox = todas[b["ultimo"] + 1]
            if ce > prox["s"] - 0.02:
                fim = min(prox["s"] + 0.4 * (prox["e"] - prox["s"]), prox["s"] + 0.25)
                ce = min(ce, _vale(db, passo, b["e"] - 0.03, fim, primeiro=True) + 0.01)
            if not prox["manter"]:
                # a tomada descartada continua logo depois ("vaga, | a proposta…"): corta no 1º silêncio de
                # verdade depois da palavra, senão o começo da frase descartada vaza ("a" dobrado)
                k = int((b["e"] - 0.03) / passo)
                kf = min(int(ce / passo), len(db) - 3)
                while k < kf and not (db[k] < limiar and db[k + 1] < limiar and db[k + 2] < limiar):
                    k += 1
                if k < kf:
                    ce = min(ce, k * passo + 0.03)
        b["cs"], b["ce"] = cs, max(ce, cs + 0.05)


PAUSA_MAX = 0.35   # pausa/respiro maior que isso DENTRO de um bloco é encurtada
PAUSA_RESTO = 0.10  # quanto de silêncio fica de cada lado do corte


def partir_pausas(blocos, db, passo):
    """Pausa longa no meio da fala (hesitação, respiro, o Whisper esticou a palavra por cima do silêncio):
    corta o miolo do silêncio e deixa ~0,2 s. Como o corte cai em silêncio de verdade, não vira soluço.
    Devolve a nova lista de blocos; palavras que atravessavam o silêncio são aparadas para o lado do som."""
    import numpy as np
    fala = db[db > np.percentile(db, 10) + 14]
    if not len(fala):
        return blocos
    limiar = float(np.median(fala)) - 12  # respiro fica ~10-11 dB abaixo da fala: conta como pausa
    quieto = db < limiar
    # tolera picos curtinhos (estalo, ruído) dentro da pausa: suaviza com janela de 90 ms
    k = 9
    quieto = np.convolve(quieto.astype(float), np.ones(k) / k, mode="same") >= 0.5
    novos = []
    anterior_pont = True

    def cauda(t0, maximo=0.35):
        """Fim real do som depois de t0: rabo de sílaba ("…e dois", "-ria") que o filtro suavizado tomou por
        silêncio. Vai até o último quadro ainda perto do nível de fala (limiar - 6 dB) nos próximos 0,35 s."""
        i0 = int(t0 / passo)
        ult = None
        for i in range(i0, min(i0 + int(maximo / passo), len(db))):
            if db[i] > limiar - 6:
                ult = i
        return (ult + 1) * passo if ult is not None else t0

    for b in blocos:
        k0, k1 = int(b["cs"] / passo), int(b["ce"] / passo)
        cortes = []
        i = k0
        while i < k1:
            if quieto[i]:
                j = i
                while j < k1 and quieto[j]:
                    j += 1
                a_, b_ = i * passo, j * passo
                # pausa no meio da FRASE (sem vírgula/ponto antes) é hesitação: quase some (~0,1 s);
                # depois de pontuação fica um respiro curto (~0,2 s)
                antes = [w for w in b["pal"] if w["s"] < a_]
                pont = bool(antes) and bool(re.search(r"[.,!?;:…]$", str(antes[-1].get("legenda") or antes[-1]["w"])))
                limite, resto = (PAUSA_MAX, PAUSA_RESTO) if pont else (0.14, 0.03)
                # só pausa no MEIO (as pontas do bloco já são tratadas pelo definir_cortes)
                if b_ - a_ > limite and a_ > b["cs"] + 0.15 and b_ < b["ce"] - 0.15:
                    # final de palavra que morre baixinho ("própri-a", "…e dois") fica abaixo do limiar
                    # suavizado e parecia pausa: o corte só começa depois do rabo real do som (pelo volume;
                    # o fim que o Whisper dá costuma se esticar por cima da hesitação, não serve)
                    x0 = max(a_, cauda(a_)) + resto
                    x1 = b_ - resto
                    if x1 - x0 > 0.06:
                        cortes.append((x0, x1))
                i = j
            else:
                i += 1
        # respiro/silêncio colado nas pontas do bloco: depois de pontuação fica ~0,12 s; no meio da frase
        # (a frase continua no bloco seguinte, ex.: "só… colocar") quase nada, senão vira gaguejada
        pont_fim = bool(re.search(r"[.,!?;:…]$", str(b["pal"][-1].get("legenda") or b["pal"][-1]["w"])))
        pont_ini = anterior_pont
        anterior_pont = pont_fim
        m_ini, m_fim = (0.12 if pont_ini else 0.06), (0.18 if pont_fim else 0.08)  # fim de palavra decai devagar
        b["meio_frase"] = not pont_fim
        k = int(b["cs"] / passo)
        while k < k1 and quieto[k]:
            k += 1
        if (k - k0) * passo > m_ini + 0.05:
            b["cs"] = max(b["cs"], k * passo - m_ini)
        k = k1
        while k > k0 and quieto[k - 1]:
            k -= 1
        if (k1 - k) * passo > m_fim + 0.05:
            b["ce"] = min(b["ce"], cauda(k * passo) + m_fim)
        if not cortes:
            novos.append(b)
            continue
        inicio = b["cs"]
        pedacos = []
        for x0, x1 in cortes:
            pedacos.append((inicio, x0))
            inicio = x1
        pedacos.append((inicio, b["ce"]))
        def lado(w):  # palavra que atravessa o corte fica do lado onde tem mais som (sem mexer no tempo dela)
            for pi, (c0, c1) in enumerate(pedacos):
                if min(w["e"], c1) - max(w["s"], c0) > 0:
                    melhor = max(range(len(pedacos)), key=lambda q: min(w["e"], pedacos[q][1]) - max(w["s"], pedacos[q][0]))
                    return melhor
            return 0
        for pi, (c0, c1) in enumerate(pedacos):
            pal = [w for w in b["pal"] if lado(w) == pi]
            nb = dict(b, cs=c0, ce=c1, pal=pal)
            nb["interno_ini"] = pi > 0
            nb["interno_fim"] = pi < len(pedacos) - 1
            novos.append(nb)
        print(f"voz: pausa no meio da fala encurtada em {len(cortes)} ponto(s) perto de "
              f"\"{' '.join(w['w'] for w in b['pal'][:4])}\"", flush=True)
    return novos


def normalizar(src, destino, alvo=-14.0, pico=-1.5):
    """Leva a voz a -14 LUFS com ganho fixo (loudnorm em 2 passadas, modo linear)."""
    import json as _json
    r = run(["ffmpeg", "-hide_banner", "-i", src, "-af", f"loudnorm=I={alvo}:TP={pico}:LRA=11:print_format=json",
             "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        ffmpeg("-i", src, "-af", f"loudnorm=I={alvo}:TP={pico}:LRA=11,aresample=48000", "-ar", 48000, destino)
        return
    d = _json.loads(m.group(0))
    ffmpeg("-i", src, "-af",
           f"loudnorm=I={alvo}:TP={pico}:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
           f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:offset={d['target_offset']}:"
           f"linear=true,aresample=48000", "-ar", 48000, destino)


def cadeia_tratamento(ruido, modelo_rnn, modo="gravada", forcar_nr=None):
    """Filtros de áudio. EQ de corpo sempre; denoise conforme o ruído medido (ou escolha da tela)."""
    nr = (ruido > RUIDO_LIMITE) if forcar_nr is None else forcar_nr
    partes = ["highpass=f=75"]
    if nr and modo == "gravada":
        partes.append(f"arnndn=m={modelo_rnn}:mix=0.9" if os.path.exists(modelo_rnn) else "afftdn=nr=12:nf=-35")
        partes.append("afftdn=nr=8:nf=-40")
    if modo == "gravada":
        # expansor suave: respiração e ruído entre as falas descem ~12 dB antes da compressão (que os traria para
        # cima), sem cortar a voz — o ataque é rápido para não comer o começo das palavras
        lim = min(max(10 ** ((ruido + 16) / 20), 0.0008), 0.02)
        partes.append(f"agate=threshold={lim:.5f}:ratio=2:range=0.25:attack=3:release=180:knee=4")
        partes += ["bass=g=5:f=150:w=0.7", "equalizer=f=280:t=q:w=1.2:g=1.5", "equalizer=f=3600:t=q:w=1.0:g=2.5",
                   "deesser=i=0.35", "acompressor=threshold=-22dB:ratio=3.5:attack=4:release=90:makeup=3"]
    else:  # voz de IA já sai limpa: só dá corpo e presença
        partes += ["bass=g=3:f=150:w=0.7", "equalizer=f=3600:t=q:w=1.0:g=1.5",
                   "acompressor=threshold=-20dB:ratio=2.5:attack=5:release=90:makeup=2"]
    # volume final: ganho FIXO (loudnorm em 2 passadas, linear) — o loudnorm de 1 passada é um controle
    # automático de ganho que sobe as pausas e respirações (o "chiado" entre as falas)
    partes += ["alimiter=limit=0.89"]
    return ",".join(partes), nr


def montar(src, palavras, destino, modelo_rnn, modo="gravada", forcar_nr=None):
    """Corta a gravação nas palavras mantidas e devolve a duração final.

    Escreve em cada palavra mantida os tempos na timeline final ("ns"/"ne").
    """
    todas = sorted(palavras, key=lambda w: w["s"])
    mant = [w for w in todas if w["manter"]]
    if not mant:
        raise RuntimeError("Nenhuma fala ficou na edição.")
    pos = {id(w): k for k, w in enumerate(todas)}

    # blocos contínuos: palavras seguidas da mesma tomada, sem pausa longa entre elas
    blocos = []
    for w in mant:
        k = pos[id(w)]
        b = blocos[-1] if blocos else None
        vao = w["s"] - todas[k - 1]["e"] if k else 9.0
        if b and b["ultimo"] == k - 1 and (vao < GAP_KEEP or vao < GAP_EMENDA):
            b["e"], b["ultimo"] = w["e"], k
            b["pal"].append(w)
        else:
            blocos.append({"s": w["s"], "e": w["e"], "primeiro": k, "ultimo": k, "pal": [w]})
    # palavrinha solta ("e", "em", "o") que sobrou do começo de uma tomada abandonada: emendada sozinha entre duas
    # tomadas vira um soluço no áudio. Melhor perder a palavrinha do que deixar o picote.
    soltos = []
    for b in blocos:
        w = b["pal"][0]
        nucleo = re.sub(r"[^\wÀ-ÿ]", "", w["w"])
        prox = todas[b["ultimo"] + 1] if b["ultimo"] + 1 < len(todas) else None
        if (len(blocos) > 1 and len(b["pal"]) == 1 and len(nucleo) <= 3 and w["e"] - w["s"] < 0.45
                and prox is not None and not prox["manter"]):
            w["manter"] = False
            soltos.append(w["w"])
    if soltos:
        blocos = [b for b in blocos if b["pal"][0]["manter"]]
        print(f"voz: palavras soltas de tomada abandonada removidas: {soltos}")
    db, passo = envelope(src)
    definir_cortes(blocos, todas, db, passo)
    blocos = partir_pausas(blocos, db, passo)

    filt, partes, t = [], [], 0.0
    t_fundo = trecho_de_fundo(db, passo, GAP_NEW)
    # conferência automática: emenda que corta no meio de som de voz (vizinha descartada colada) vira aviso
    import numpy as np
    lim_qa = max(float(np.percentile(db, 10)) + 14, -58.0) + 6
    def nivel(tt):
        k = int(tt / passo)
        return float(db[max(k - 1, 0):k + 2].max()) if len(db) else -99.0
    avisos = []
    for bi, b in enumerate(blocos):
        dur = max(b["ce"] - b["cs"], 0.05)
        filt.append(f"[0:a]atrim={b['cs']:.3f}:{b['ce']:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=0.012,afade=t=out:st={max(dur - 0.06, 0):.3f}:d=0.06[a{bi}]")
        partes.append(f"[a{bi}]")
        for w in b["pal"]:  # palavra esticada pelo Whisper sobre o silêncio cortado: presa ao pedaço dela
            w["ns"] = round(t + min(max(w["s"], b["cs"]), b["ce"]) - b["cs"], 3)
            w["ne"] = round(t + max(min(w["e"], b["ce"]), max(w["s"], b["cs"])) - b["cs"], 3)
        ant = todas[b["primeiro"] - 1] if b["primeiro"] > 0 else None
        prox = todas[b["ultimo"] + 1] if b["ultimo"] + 1 < len(todas) else None
        if ant is not None and not ant["manter"] and not b.get("interno_ini") and nivel(b["cs"]) > lim_qa:
            avisos.append({"t": round(t, 2), "onde": "começo", "texto": " ".join(w["w"] for w in b["pal"][:3])})
        if prox is not None and not prox["manter"] and not b.get("interno_fim") and nivel(b["ce"]) > lim_qa:
            avisos.append({"t": round(t + dur, 2), "onde": "fim", "texto": " ".join(w["w"] for w in b["pal"][-3:])})
        t += dur
        if bi < len(blocos) - 1 and not (b.get("meio_frase") or b.get("interno_fim")):
            filt.append(f"[0:a]atrim={t_fundo:.3f}:{t_fundo + GAP_NEW:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.008,afade=t=out:st={GAP_NEW - 0.008:.3f}:d=0.008[g{bi}]")
            partes.append(f"[g{bi}]")
            t += GAP_NEW
    ruido = ruido_de_fundo(src, todas) if modo == "gravada" else -90.0
    cadeia, nr = cadeia_tratamento(ruido, modelo_rnn, modo, forcar_nr)
    filt.append("".join(partes) + f"concat=n={len(partes)}:v=0:a=1[cut]")
    filt.append(f"[cut]{cadeia}[out]")
    previo = destino + ".pre.wav"
    ffmpeg("-i", src, "-filter_complex", ";".join(filt), "-map", "[out]", "-ac", "1", "-ar", 48000, previo)
    normalizar(previo, destino)
    os.remove(previo)
    print(f"voz: {len(blocos)} blocos, {t:.1f}s, ruído {ruido:.1f} dB, redução de ruído {'sim' if nr else 'não'}")
    return {"duracao": round(t, 3), "ruido_db": round(ruido, 1), "reducao_ruido": nr, "avisos": avisos}
