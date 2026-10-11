"""Monta a trilha de voz: junta só as palavras mantidas, encurta pausas longas e trata o som.

Regras fixas do Danilo:
- SEMPRE equalizar para a voz ficar encorpada (grave, calor, presença).
- Redução de ruído SÓ quando o ruído de fundo da gravação pedir (medido nos silêncios).
"""
import os
import re

from comum import ffmpeg, run

PAD_IN, PAD_OUT = 0.10, 0.28   # até onde se procura o som real antes/depois de cada trecho
FOLGA_IN, FOLGA_OUT = 0.03, 0.05  # folga mínima depois do fim real do som (não come consoante)
GAP_KEEP = 0.30                # pausas menores que isso ficam como estão (respiro natural dentro da frase)
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


def bordas_reais(db, passo, s, e):
    """(início, fim) do som da fala em torno de [s, e]: estende enquanto ainda há voz, até PAD_IN/PAD_OUT."""
    import numpy as np
    piso = float(np.percentile(db, 10))
    limiar = max(piso + 12, -52.0)
    k = min(int(e / passo), len(db) - 1)
    fim = int(min((e + PAD_OUT) / passo, len(db) - 1))
    while k < fim and db[k] > limiar:
        k += 1
    ce = k * passo
    k = max(int(s / passo), 0)
    ini = int(max((s - PAD_IN) / passo, 0))
    while k > ini and db[k - 1] > limiar:
        k -= 1
    cs = k * passo
    return cs, ce


def cadeia_tratamento(ruido, modelo_rnn, modo="gravada", forcar_nr=None):
    """Filtros de áudio. EQ de corpo sempre; denoise conforme o ruído medido (ou escolha da tela)."""
    nr = (ruido > RUIDO_LIMITE) if forcar_nr is None else forcar_nr
    partes = ["highpass=f=75"]
    if nr and modo == "gravada":
        partes.append(f"arnndn=m={modelo_rnn}:mix=0.9" if os.path.exists(modelo_rnn) else "afftdn=nr=12:nf=-35")
        partes.append("afftdn=nr=8:nf=-40")
    if modo == "gravada":
        partes += ["bass=g=5:f=150:w=0.7", "equalizer=f=280:t=q:w=1.2:g=1.5", "equalizer=f=3600:t=q:w=1.0:g=2.5",
                   "deesser=i=0.35", "acompressor=threshold=-22dB:ratio=3.5:attack=4:release=90:makeup=3"]
    else:  # voz de IA já sai limpa: só dá corpo e presença
        partes += ["bass=g=3:f=150:w=0.7", "equalizer=f=3600:t=q:w=1.0:g=1.5",
                   "acompressor=threshold=-20dB:ratio=2.5:attack=5:release=90:makeup=2"]
    partes += ["alimiter=limit=0.89", "loudnorm=I=-14:TP=-1.5:LRA=6", "aresample=48000"]
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
        if b and b["ultimo"] == k - 1 and todas[k - 1]["c"] == w["c"] and w["s"] - todas[k - 1]["e"] < GAP_KEEP:
            b["e"], b["ultimo"] = w["e"], k
            b["pal"].append(w)
        else:
            blocos.append({"s": w["s"], "e": w["e"], "primeiro": k, "ultimo": k, "pal": [w]})
    db, passo = envelope(src)
    for b in blocos:  # corta onde o som realmente começa/acaba, sem invadir a palavra vizinha
        ant = todas[b["primeiro"] - 1]["e"] if b["primeiro"] > 0 else 0.0
        prox = todas[b["ultimo"] + 1]["s"] if b["ultimo"] + 1 < len(todas) else b["e"] + 1
        som_s, som_e = bordas_reais(db, passo, b["s"], b["e"])
        b["cs"] = max(ant + 0.02, som_s - FOLGA_IN, 0.0)
        b["ce"] = min(prox - 0.02, som_e + FOLGA_OUT)

    filt, partes, t = [], [], 0.0
    for bi, b in enumerate(blocos):
        dur = max(b["ce"] - b["cs"], 0.05)
        filt.append(f"[0:a]atrim={b['cs']:.3f}:{b['ce']:.3f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=0.012,afade=t=out:st={max(dur - 0.06, 0):.3f}:d=0.06[a{bi}]")
        partes.append(f"[a{bi}]")
        for w in b["pal"]:
            w["ns"] = round(t + w["s"] - b["cs"], 3)
            w["ne"] = round(t + w["e"] - b["cs"], 3)
        t += dur
        if bi < len(blocos) - 1:
            filt.append(f"anullsrc=r=48000:cl=mono,atrim=0:{GAP_NEW}[g{bi}]")
            partes.append(f"[g{bi}]")
            t += GAP_NEW
    ruido = ruido_de_fundo(src, todas) if modo == "gravada" else -90.0
    cadeia, nr = cadeia_tratamento(ruido, modelo_rnn, modo, forcar_nr)
    filt.append("".join(partes) + f"concat=n={len(partes)}:v=0:a=1[cut]")
    filt.append(f"[cut]{cadeia}[out]")
    ffmpeg("-i", src, "-filter_complex", ";".join(filt), "-map", "[out]", "-ac", "1", "-ar", 48000, destino)
    print(f"voz: {len(blocos)} blocos, {t:.1f}s, ruído {ruido:.1f} dB, redução de ruído {'sim' if nr else 'não'}")
    return {"duracao": round(t, 3), "ruido_db": round(ruido, 1), "reducao_ruido": nr}
