"""Efeitos gerados na hora (sem arquivos de terceiros, sem licença): sons de transição/ênfase e o light leak.

Todos os sons saem em float (-1..1, 48 kHz mono) e são somados numa única trilha de efeitos,
que entra por baixo da voz no render.
"""
import os
import shutil
import wave

import numpy as np

from comum import FPS, W, H, ffmpeg

SR = 48000


# ---------------------------------------------------------------- sons

def _filtro_varredura(n, f0, f1, largura_rel=0.9, semente=3, grave=0.15):
    """Ruído com filtro passa-faixa que varre de f0 a f1 (base do woosh)."""
    rng = np.random.default_rng(semente)
    ruido = rng.standard_normal(n + 2048)
    janela, passo = 2048, 512
    hann = np.hanning(janela)
    saida = np.zeros(n + janela)
    soma = np.zeros(n + janela)
    freqs = np.fft.rfftfreq(janela, 1 / SR)
    for ini in range(0, n, passo):
        p = ini / max(n, 1)
        centro = f0 * ((f1 / f0) ** p)
        filtro = np.exp(-0.5 * ((freqs - centro) / (centro * largura_rel)) ** 2)
        if grave:
            filtro = filtro + grave * np.exp(-0.5 * (freqs / 400) ** 2)
        bloco = np.fft.irfft(np.fft.rfft(ruido[ini:ini + janela] * hann) * filtro)
        saida[ini:ini + janela] += bloco * hann
        soma[ini:ini + janela] += hann ** 2
    return saida[:n] / np.maximum(soma[:n], 1e-6)


def _normaliza(x, pico=0.9):
    return x / (np.abs(x).max() + 1e-9) * pico


def som_woosh(dur=0.55, pico_em=0.55, semente=3):
    """Woosh: sobe até o pico (pico_em da duração) e some rápido."""
    n = int(dur * SR)
    x = _filtro_varredura(n, 250, 3500, semente=semente)
    t = np.linspace(0, 1, n)
    env = np.where(t < pico_em, (t / pico_em) ** 2.2, np.exp(-((t - pico_em) / 0.12)))
    return _normaliza(x * env)


def som_pop(dur=0.09):
    """Pop curto e seco (bolha): seno com queda rápida de afinação + clique suave."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 900 * np.exp(-t * 28) + 380
    fase = 2 * np.pi * np.cumsum(f) / SR
    env = np.minimum(t / 0.002, 1) * np.exp(-t * 55)
    return _normaliza(np.sin(fase) * env, 0.8)


def som_impacto(dur=1.1):
    """Impacto grave (boom de trailer): seno descendo de 90 a 38 Hz + corpo de ruído grave + ataque."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 38 + 52 * np.exp(-t * 6)
    corpo = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.2)
    rng = np.random.default_rng(11)
    ruido = rng.standard_normal(n)
    # ruído passa-baixa simples (média móvel) só no ataque
    k = 60
    grave = np.convolve(ruido, np.ones(k) / k, mode="same") * np.exp(-t * 18) * 3
    ataque = rng.standard_normal(n) * np.exp(-t * 160) * 0.25
    x = corpo + grave + ataque
    x = np.tanh(x * 1.6)  # saturação leve: soa mais cheio em caixinha de celular
    return _normaliza(x, 0.95)


def som_glitch(dur=0.28, semente=5):
    """Glitch digital: rajadas curtas de ruído e tom quadrado picotado."""
    n = int(dur * SR)
    rng = np.random.default_rng(semente)
    t = np.arange(n) / SR
    x = np.zeros(n)
    pos = 0
    while pos < n:
        tam = int(rng.uniform(0.012, 0.04) * SR)
        fim = min(pos + tam, n)
        if rng.random() < 0.55:
            f = rng.uniform(300, 2400)
            x[pos:fim] = np.sign(np.sin(2 * np.pi * f * t[pos:fim])) * rng.uniform(0.3, 0.8)
        elif rng.random() < 0.7:
            x[pos:fim] = rng.standard_normal(fim - pos) * rng.uniform(0.2, 0.6)
        pos = fim + int(rng.uniform(0, 0.012) * SR)
    env = np.minimum(t / 0.004, 1) * np.clip(1 - (t - dur * 0.7) / (dur * 0.3), 0, 1)
    return _normaliza(x * env, 0.7)


def som_zoom(dur=0.45):
    """Zoom: woosh mais curto e agudo com cauda grave."""
    x = som_woosh(dur, pico_em=0.6, semente=9)
    t = np.arange(len(x)) / SR
    sub = np.sin(2 * np.pi * (60 + 40 * np.exp(-t * 8)) * t) * np.exp(-np.maximum(t - dur * 0.6, 0) * 12)
    sub[: int(dur * 0.6 * SR)] *= np.linspace(0, 1, int(dur * 0.6 * SR)) ** 2
    return _normaliza(x + 0.5 * sub)


SONS = {
    "woosh": (som_woosh, 0.55 * 0.55),   # (gerador, segundos até o pico — alinhado ao momento do evento)
    "pop": (som_pop, 0.0),
    "impacto": (som_impacto, 0.0),
    "glitch": (som_glitch, 0.02),
    "zoom": (som_zoom, 0.45 * 0.6),
}


def trilha_efeitos(eventos, total, destino):
    """Soma os efeitos numa trilha só. eventos = [(segundo_do_evento, nome, ganho)]."""
    n = int((total + 1.5) * SR)
    trilha = np.zeros(n)
    cache = {}
    for t, nome, ganho in eventos:
        if nome not in SONS:
            continue
        gerar, pico = SONS[nome]
        if nome not in cache:
            cache[nome] = gerar()
        som = cache[nome]
        ini = int(max(t - pico, 0) * SR)
        fim = min(ini + len(som), n)
        trilha[ini:fim] += som[: fim - ini] * ganho
    trilha = np.clip(trilha, -0.98, 0.98)
    with wave.open(destino, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((trilha * 32767).astype(np.int16).tobytes())
    return destino


# ---------------------------------------------------------------- light leak

LEAK_DUR = 0.9


def light_leak(destino, dur=LEAK_DUR, semente=7):
    """Light leak (manchas de luz quente) que começa e termina preto; aplicado com mistura "screen"."""
    from PIL import Image
    from tempfile import mkdtemp
    rng = np.random.default_rng(semente)
    w, h = 270, 480
    n = max(2, int(round(dur * FPS)))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cores = np.array([[255, 95, 20], [235, 40, 70], [255, 170, 50], [255, 120, 40]], np.float32) / 255
    manchas = []
    for k in range(4):
        manchas.append({
            "x0": rng.uniform(-0.4, 0.2) * w, "x1": rng.uniform(0.8, 1.4) * w,
            "y": rng.uniform(0.1, 0.9) * h, "r": rng.uniform(0.35, 0.7) * w,
            "cor": cores[k % len(cores)], "fase": rng.uniform(0, 0.25),
        })
    pasta = mkdtemp()
    for i in range(n):
        p = i / (n - 1)
        env = np.sin(np.pi * min(p / 0.35, 1) / 2) if p < 0.35 else np.cos(np.pi / 2 * (p - 0.35) / 0.65)
        env = 0.0 if i in (0, n - 1) else float(env) ** 1.3
        img = np.zeros((h, w, 3), np.float32)
        for m in manchas:
            q = min(max(p + m["fase"], 0), 1)
            cx = m["x0"] + (m["x1"] - m["x0"]) * q
            d2 = ((xx - cx) ** 2 + ((yy - m["y"]) * 0.6) ** 2) / (m["r"] ** 2)
            img += np.exp(-d2)[..., None] * m["cor"]
        img = np.clip(img * 0.62 * env, 0, 1)
        Image.fromarray((img * 255).astype(np.uint8)).save(os.path.join(pasta, f"{i:03d}.png"))
    ffmpeg("-framerate", FPS, "-i", os.path.join(pasta, "%03d.png"), "-vf",
           f"scale={W}:{H}:flags=bicubic,gblur=sigma=20,format=yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 16, destino)
    shutil.rmtree(pasta, ignore_errors=True)
    return destino
