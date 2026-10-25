"""Revisão automática do vídeo pronto: aponta os minutos que merecem uma olhada antes de publicar.

Cada aviso: {"t": segundos, "tipo": ..., "texto": explicação curta}. Nada aqui muda o vídeo.
"""
import os
import re
import subprocess

FORA_DE_JOGO = ("materia", "proprio", "commons", "trilha", "")


def _quadros_pequenos(video, fps=1, w=32, h=18):
    """Quadros minúsculos em tons de cinza (1 por segundo) para comparar imagem com imagem."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", video, "-vf",
                        f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    dados = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(dados) // (w * h)
    return dados[: n * w * h].reshape(n, h, w).astype(np.int16)


def cenas_repetidas(video, fim_util):
    """Mesmo trecho de imagem aparecendo duas vezes no vídeo (trailer em loop, plano reaproveitado)."""
    import numpy as np
    q = _quadros_pequenos(video)
    n = min(len(q), int(fim_util))
    if n < 20:
        return []
    planos = q[:n].reshape(n, -1)
    # quadro sem detalhe (preto, tela lisa) não conta: igual a qualquer outro preto
    detalhe = planos.std(1) > 12
    avisos, ja = [], set()
    for i in range(n):
        if not detalhe[i] or i in ja:
            continue
        dif = np.abs(planos[i + 10:] - planos[i]).mean(1) if i + 10 < n else np.array([])
        for j in np.nonzero(dif < 4.0)[0][:3]:
            j = int(j) + i + 10
            # repetição de verdade = pelo menos 3 s seguidos iguais (um quadro parecido é coincidência)
            k = 1
            while i + k < n and j + k < n and detalhe[i + k] and np.abs(planos[i + k] - planos[j + k]).mean() < 5.0:
                k += 1
            if k >= 3:
                avisos.append({"t": float(j), "tipo": "repetida",
                               "texto": f"imagem repetida ({k} s iguais ao trecho de {int(i) // 60}:{int(i) % 60:02d})"})
                ja.update(range(i, i + k))
                ja.update(range(j, j + k))
                break
    return avisos


def silencio(video, fim_util):
    """Buraco no áudio (nem voz, nem jogo, nem cama) — parece vídeo quebrado."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-af", "silencedetect=n=-45dB:d=2.0", "-vn",
                        "-f", "null", "-"], capture_output=True, text=True)
    avisos = []
    for a, b in re.findall(r"silence_start: ([\d.]+).*?silence_end: ([\d.]+)", r.stderr, re.S):
        a, b = float(a), float(b)
        if a > 1.0 and a < fim_util:
            avisos.append({"t": round(a, 1), "tipo": "silencio", "texto": f"{b - a:.0f} s sem som nenhum"})
    return avisos


def _norm(p):
    return re.sub(r"[^\wà-ÿ]", "", str(p).lower())


def falas_repetidas(palavras):
    """Sobra de tomada na voz: a mesma sequência de 2-4 palavras dita duas vezes em poucos segundos."""
    ativas = sorted((w for w in palavras if w.get("manter") and w.get("ns") is not None and _norm(w.get("w"))),
                    key=lambda w: w["ns"])
    avisos, ult = [], -99.0
    for n in (4, 3, 2):
        for i in range(len(ativas) - 2 * n + 1):
            a = [_norm(w["w"]) for w in ativas[i:i + n]]
            b = [_norm(w["w"]) for w in ativas[i + n:i + 2 * n]]
            if a == b and sum(len(x) for x in a) >= 6 and ativas[i + n]["ns"] - ativas[i]["ns"] < 6:
                t = float(ativas[i]["ns"])
                if abs(t - ult) > 4:
                    avisos.append({"t": round(t, 1), "tipo": "fala_repetida",
                                   "texto": f'fala repetida: "{" ".join(w["w"] for w in ativas[i:i + n])}"'})
                    ult = t
    return avisos


def midia_de_outro_jogo(projeto, mapa, banco, timeline):
    """Imagem de um jogo aparecendo enquanto a narração fala de outro (fora a abertura e a montagem final)."""
    cenas = projeto.get("cenas") or []
    avisos, ult = [], -99.0
    for t0, t1, mid, mudo in mapa:
        m = banco.get(mid) or {}
        jm = m.get("jogo") or ""
        if jm in FORA_DE_JOGO or mudo or m.get("personagem"):
            continue
        meio = (t0 + t1) / 2
        tl = next((x for x in timeline if x["inicio"] <= meio < x["fim"]), None)
        if not tl or tl["cena"] == 0 or tl["cena"] >= len(cenas):
            continue
        c = cenas[tl["cena"]]
        do_topico = {c.get("jogo")} | set(c.get("jogos") or [])
        do_topico.discard(None)
        if do_topico and jm not in do_topico and meio - ult > 8:
            avisos.append({"t": round(t0, 1), "tipo": "outro_jogo",
                           "texto": f"imagem de {jm} no trecho sobre {', '.join(sorted(do_topico))}"})
            ult = meio
    return avisos


def revisar(projeto, video, banco, mapa, timeline, total, cauda=0.0):
    fim_util = max(0.0, total - cauda - 1.0)  # a tela final (montagem + cartões) fica fora
    avisos = []
    for nome, f in (("repetidas", lambda: cenas_repetidas(video, fim_util)),
                    ("silencio", lambda: silencio(video, fim_util)),
                    ("falas", lambda: falas_repetidas(projeto.get("palavras") or [])),
                    ("jogo", lambda: midia_de_outro_jogo(projeto, mapa, banco, timeline))):
        try:
            avisos += f()
        except Exception as e:
            print(f"aviso: revisão automática ({nome}) falhou: {e}", flush=True)
    avisos.sort(key=lambda a: a["t"])
    print(f"revisão automática: {len(avisos)} pontos {avisos[:12]}", flush=True)
    return avisos[:30]


if __name__ == "__main__":  # teste manual: python qa.py video.mp4
    import sys
    print(cenas_repetidas(sys.argv[1], 10 ** 6), silencio(sys.argv[1], 10 ** 6))
    os._exit(0)
