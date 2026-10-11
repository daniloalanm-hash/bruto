"""Render vertical 1080x1920: cortes dinâmicos por assunto, transição a cada tópico e legenda em blocos."""
import os
import re
import shutil

from comum import FPS, W, H, ffmpeg, norm, run

CUT = 1.7        # duração alvo de cada corte (s)
TRANS = 0.25     # transição de tópico: flash + punch de zoom + desfoque que some
FONTE = "Anton"
TAM_LEGENDA = 168
Y_LEGENDA = 1180

_crops = {}


def area_util(src, t):
    """Detecta tarjas pretas (letterbox) do trailer para recortar só a imagem útil."""
    k = (src, int(t))
    if k not in _crops:
        r = run(["ffmpeg", "-hide_banner", "-ss", f"{t:.2f}", "-i", src, "-t", "1.5", "-vf",
                 "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"])
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        _crops[k] = m[-1] if m else None
    return _crops[k]


LEAK_DUR = 0.9    # light leak na saída do gancho: ~0,9s, pico logo depois do corte
LEAK_ANTES = 0.30  # começa um pouco antes do corte (cobre o fim do gancho)


def light_leak(destino, dur=LEAK_DUR, semente=7):
    """Gera um light leak (vazamento de luz quente) que começa e termina preto.

    Aplicado com mistura "screen": preto não muda nada, as manchas clareiam a imagem por baixo.
    """
    import numpy as np
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
        # envelope: sobe rápido, pico em ~35%, some suave; primeiro e último quadro pretos
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


PAN_DUR = 0.30     # whip pan para a direita na saída do gancho
WOOSH_DUR = 0.55   # woosh: sobe até o corte e some logo depois
WOOSH_GANHO = 0.5  # volume do woosh em relação à voz (a voz já sai em -14 LUFS)


def woosh(destino, dur=WOOSH_DUR, sr=48000, semente=3):
    """Gera um woosh (ruído com filtro que varre do grave ao agudo) em WAV mono, pico em ~55% da duração."""
    import wave
    import numpy as np
    rng = np.random.default_rng(semente)
    n = int(dur * sr)
    ruido = rng.standard_normal(n + 2048).astype(np.float64)
    janela, passo = 2048, 512
    hann = np.hanning(janela)
    saida = np.zeros(n + janela)
    soma = np.zeros(n + janela)
    freqs = np.fft.rfftfreq(janela, 1 / sr)
    for ini in range(0, n, passo):
        p = ini / n
        centro = 250 * (14 ** p)                 # varre de ~250 Hz a ~3,5 kHz
        largura = centro * 0.9
        filtro = np.exp(-0.5 * ((freqs - centro) / largura) ** 2) + 0.15 * np.exp(-0.5 * (freqs / 400) ** 2)
        bloco = np.fft.irfft(np.fft.rfft(ruido[ini:ini + janela] * hann) * filtro)
        saida[ini:ini + janela] += bloco * hann
        soma[ini:ini + janela] += hann ** 2
    saida = saida[:n] / np.maximum(soma[:n], 1e-6)
    t = np.linspace(0, 1, n)
    env = np.where(t < 0.55, (t / 0.55) ** 2.2, np.exp(-((t - 0.55) / 0.12)))
    saida *= env
    saida = saida / (np.abs(saida).max() + 1e-9) * 0.9
    with wave.open(destino, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((saida * 32767).astype(np.int16).tobytes())
    return destino


def _fx_transicao(ativo):
    if not ativo:
        return ""
    if ativo == "leak":  # o light leak entra por cima depois; aqui só o desfoque que some
        return f",gblur=sigma=10:enable='lt(t,{TRANS * 0.6})'"
    return (f",gblur=sigma=18:enable='lt(t,{TRANS * 0.5})',gblur=sigma=7:enable='between(t,{TRANS * 0.5},{TRANS})'"
            f",fade=t=in:st=0:d={TRANS * 0.6}:color=white")


def seg_video(src, t0, dur, out, zoom_in, trans):
    z = f"1+0.07*t/{dur:.3f}" if zoom_in else f"1.07-0.07*t/{dur:.3f}"
    if trans:
        z = f"({z})+0.22*max(0\\,1-t/{TRANS})"
    c = area_util(src, t0)
    pre = f"crop={c[0]}:{c[1]}:{c[2]}:{c[3]}," if c and int(c[1]) >= 300 else ""
    vf = (f"{pre}crop=ih*9/16:ih,scale={W}:{H},"
          f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame,"
          f"crop={W}:{H},setsar=1,fps={FPS},eq=contrast=1.06:saturation=1.12:gamma=1.1" + _fx_transicao(trans))
    ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur:.3f}", "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", out)


def seg_imagem(src, dur, out, k, trans):
    n = max(1, int(round(dur * FPS)))
    pans = ["x='iw/2-(iw/zoom/2)'", f"x='(iw-iw/zoom)*on/{n}'", f"x='(iw-iw/zoom)*(1-on/{n})'"]
    punch = f"+0.22*max(0\\,1-on/{int(TRANS * FPS)})" if trans else ""
    vf = (f"scale=-2:2400,crop=1350:2400,"
          f"zoompan=z='1.05+0.10*on/{n}{punch}':{pans[k % 3]}:y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS},"
          f"setsar=1,eq=contrast=1.05:saturation=1.1" + _fx_transicao(trans))
    ffmpeg("-loop", 1, "-i", src, "-frames:v", n, "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", out)


QUADROS_JL = 8     # padrão do J-cut/L-cut: a voz adianta ou atrasa ~8 quadros em relação à troca de imagem
MIN_CENA = 1.0    # nenhuma cena fica com menos que isso por causa do deslocamento


def tipo_entrada(projeto, cena, ordem):
    """J, L ou seco para a troca de tópico. 'auto' alterna J e L (começando por J)."""
    escolha = str(projeto["cenas"][cena].get("entrada") or "auto").upper()
    if escolha in ("J", "L", "SECO"):
        return escolha.lower() if escolha == "SECO" else escolha
    return "J" if ordem % 2 == 1 else "L"


def linha_tempo(projeto):
    """Início/fim de cada cena na timeline final, a partir das palavras mantidas, com J-cut/L-cut nas trocas."""
    mant = [w for w in projeto["palavras"] if w.get("manter") and "ns" in w]
    total = projeto["duracao_voz"] + 0.6
    inicios = {}
    for w in mant:
        inicios.setdefault(w["cena"], w["ns"])
    cenas = sorted(inicios)
    # deslocamento escolhido na tela (projeto["jl_quadros"]); J: imagem troca depois da fala, L: antes
    try:
        desloc = max(0, min(int(projeto.get("jl_quadros", QUADROS_JL)), 30)) / FPS
    except (TypeError, ValueError):
        desloc = QUADROS_JL / FPS
    # ponto de corte da imagem em cada troca de tópico
    cortes, tipos = {}, {}
    for k in range(1, len(cenas)):
        c, ant = cenas[k], cenas[k - 1]
        fala = inicios[c]
        lim_a = (cortes.get(ant, 0.0) if k > 1 else 0.0) + MIN_CENA   # a cena anterior não pode encolher demais
        prox = inicios[cenas[k + 1]] if k + 1 < len(cenas) else total
        lim_b = prox - MIN_CENA                                       # nem a cena nova
        tipo = tipo_entrada(projeto, c, k)
        t = fala
        if tipo == "J":
            t = fala + desloc
        elif tipo == "L":
            t = fala - desloc
        t = min(max(t, lim_a), max(lim_b, lim_a))
        if (tipo == "J" and t <= fala + 0.02) or (tipo == "L" and t >= fala - 0.02):
            tipo, t = "seco", fala  # sem espaço para deslocar: corte seco
        cortes[c], tipos[c] = t, tipo
    faixas = []
    for k, c in enumerate(cenas):
        a = 0.0 if k == 0 else cortes[c]
        b = cortes[cenas[k + 1]] if k + 1 < len(cenas) else total
        faixas.append((c, a, b))
    return faixas, total, tipos


def _ts(t):
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def legenda_ass(projeto, destino):
    AMARELO, BRANCO = r"{\c&H00D4FF&}", r"{\c&HFFFFFF&}"
    linhas = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
              "[V4+ Styles]",
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
              "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
              "MarginR, MarginV, Encoding",
              f"Style: Cap,{FONTE},{TAM_LEGENDA},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,11,6,5,40,40,0,1",
              "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    destaques = {k: {norm(x) for x in c.get("destaques", [])} for k, c in enumerate(projeto["cenas"])}
    pal = [w for w in projeto["palavras"] if w.get("manter") and "ns" in w and str(w.get("legenda", "")).strip()]
    blocos, cur = [], []
    for k, w in enumerate(pal):
        if cur and (w["cena"] != cur[-1]["cena"] or w["ns"] - cur[-1]["ne"] > 0.6):
            blocos.append(cur); cur = []
        cur.append(w)
        fim = bool(re.search(r"[.,:;?!…]$", w["legenda"]))
        txt = " ".join(x["legenda"] for x in cur)
        if len(cur) == 3 or fim or (len(cur) == 2 and len(txt) > 11):
            blocos.append(cur); cur = []
    if cur:
        blocos.append(cur)
    for bi, b in enumerate(blocos):
        ini = b[0]["ns"]
        fim = blocos[bi + 1][0]["ns"] if bi + 1 < len(blocos) and blocos[bi + 1][0]["ns"] - b[-1]["ne"] < 0.6 \
            else b[-1]["ne"] + 0.35
        partes = []
        for w in b:
            t = re.sub(r"[.,:;…]+$", "", w["legenda"]).upper()
            partes.append((AMARELO if norm(w["legenda"]) in destaques.get(w["cena"], ()) else BRANCO) + t)
        pop = r"{\an5\pos(540," + str(Y_LEGENDA) + r")\fscx70\fscy70\t(0,90,\fscx108\fscy108)\t(90,160,\fscx100\fscy100)}"
        linhas.append(f"Dialogue: 0,{_ts(ini)},{_ts(fim)},Cap,,0,0,0,,{pop}{' '.join(partes)}")
    with open(destino, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))


def renderizar(projeto, banco, pasta_raw, voz_wav, pasta_trab, destino, pasta_fontes, progresso=None):
    pasta_trab = os.path.abspath(pasta_trab)
    seg_dir = os.path.join(pasta_trab, "seg")
    shutil.rmtree(seg_dir, ignore_errors=True)
    os.makedirs(seg_dir)
    faixas, total, tipos = linha_tempo(projeto)
    # transição na saída do gancho (1ª troca de tópico): "pan" (whip pan à direita + woosh), "leak" ou "pan_leak"
    estilo = str(projeto.get("transicao_gancho") or "pan")
    if len(faixas) < 2:
        estilo = "nenhuma"
    usar_leak = estilo in ("leak", "pan_leak")
    usar_pan = estilo in ("pan", "pan_leak")
    lista, k = [], 0
    usados_cena = []
    for fi, (c, a, b) in enumerate(faixas):
        dur = b - a
        n = max(1, round(dur / CUT))
        midias = [m for m in projeto["cenas"][c].get("midias", []) if m in banco] or list(banco)[:1]
        usados_cena.append({"cena": c, "inicio": round(a, 2), "fim": round(b, 2), "cortes": [],
                            "entrada": tipos.get(c, "inicio")})
        for j in range(n):
            mid = midias[j % len(midias)]
            m = banco[mid]
            rep = j // len(midias)
            d = dur / n
            out = os.path.join(seg_dir, f"{k:04d}.mp4")
            # 1ª troca (saída do gancho) ganha light leak; as outras, flash + desfoque
            if j == 0 and fi == 1 and estilo != "nenhuma":
                trans = "leak" if usar_leak else False  # o pan é feito depois, sobre o vídeo montado
            else:
                trans = j == 0 and fi > 0
            src = os.path.join(pasta_raw, m["arquivo"])
            if m["tipo"] == "v":
                seg_video(src, m["t"] + rep * 2.5, d, out, k % 2 == 0, trans)
            else:
                seg_imagem(src, d, out, k, trans)
            usados_cena[-1]["cortes"].append(mid)
            lista.append(out)
            k += 1
        if progresso:
            progresso(fi + 1, len(faixas))
    lst = os.path.join(pasta_trab, "lista.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in lista)
    visual = os.path.join(pasta_trab, "visual.mp4")
    ffmpeg("-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", visual)
    ass = os.path.join(pasta_trab, "legenda.ass")
    legenda_ass(projeto, ass)
    entradas = ["-i", visual, "-i", voz_wav]
    grafo, v, a = [], "[0:v]", "[1:a]apad"
    corte = faixas[1][1] if len(faixas) > 1 else 0.0
    if usar_pan:
        # whip pan para a direita: a imagem do gancho sai pela esquerda e a nova entra pela direita,
        # com desfoque de movimento horizontal; cada lado congela meio pan para cobrir a sobreposição
        meio = PAN_DUR / 2
        grafo.append(f"{v}split[pa][pb]")
        grafo.append(f"[pa]trim=0:{corte:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={meio:.3f}[ga]")
        grafo.append(f"[pb]trim=start={corte:.3f},setpts=PTS-STARTPTS,tpad=start_mode=clone:start_duration={meio:.3f}[gb]")
        grafo.append(f"[ga][gb]xfade=transition=slideleft:duration={PAN_DUR:.3f}:offset={corte - meio:.3f},"
                     f"gblur=sigma=40:sigmaV=0.01:enable='between(t,{corte - meio:.3f},{corte + meio:.3f})'[vp]")
        v = "[vp]"
        # woosh com o pico (55% do som) no corte
        som = woosh(os.path.join(pasta_trab, "woosh.wav"))
        entradas += ["-i", som]
        atraso = max(int((corte - WOOSH_DUR * 0.55) * 1000), 0)
        grafo.append(f"[2:a]volume={WOOSH_GANHO},adelay={atraso}:all=1,apad[sw]")
        grafo.append("[1:a]apad[vz];[vz][sw]amix=inputs=2:duration=first:normalize=0[am]")
        a = "[am]"
    if usar_leak:
        # light leak por cima (mistura screen) começando um pouco antes do corte do gancho
        leak = light_leak(os.path.join(pasta_trab, "leak.mp4"))
        idx = len(entradas) // 2
        entradas += ["-i", leak]
        grafo.append(f"[{idx}:v]tpad=start_duration={max(corte - LEAK_ANTES, 0.0):.3f}:start_mode=add:color=black,"
                     f"format=gbrp[lk]")
        grafo.append(f"{v}format=gbrp[vb];[vb][lk]blend=all_mode=screen:shortest=0,format=yuv420p[vl]")
        v = "[vl]"
    grafo.append(f"{v}subtitles={ass}:fontsdir={pasta_fontes},fade=t=in:st=0:d=0.15[v]")
    if a.endswith("apad"):
        grafo.append(f"{a}[a]")
        a = "[a]"
    ffmpeg(*entradas, "-filter_complex", ";".join(grafo),
           "-map", "[v]", "-map", a, "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", 20,
           "-c:a", "aac", "-b:a", "192k", "-ar", 48000, "-movflags", "+faststart", destino)
    return usados_cena, total
