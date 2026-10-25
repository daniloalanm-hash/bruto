"""Render vertical 1080x1920: cortes dinâmicos por assunto, transições, legenda, título do gancho, emojis e efeitos.

Ordem da montagem:
  1. cada corte vira um segmento (movimento de câmera + efeito de entrada do tópico: flash, zoom, glitch);
  2. segmentos concatenados; por cima: pans (xfade), light leak, zoom de ênfase nas palavras destacadas;
  3. legenda + título do gancho (ASS) e emojis (PNG) queimados por último, sempre nítidos;
  4. voz + trilha de efeitos (woosh, pop, impacto, glitch) mixados.
"""
import math
import os
import re
import shutil
import subprocess

import cartoes
import efeitos
from comum import FPS, W, H, duracao, ffmpeg, norm, run

CUT = 1.7        # duração alvo de cada corte (s)
TRANS = 0.25     # duração do efeito de entrada de tópico (flash/zoom/glitch)
PAN_DUR = 0.30   # whip pan
LEAK_ANTES = 0.30
QUADROS_JL = 8   # padrão do J-cut/L-cut
ESPACO_ENFASE = 2.2  # mínimo entre dois zooms/pops de ênfase (s)
MIN_CENA = 1.0   # nenhuma cena fica com menos que isso por causa do deslocamento J/L

# ---------------------------------------------------------------- opções de estilo (projeto)

ESTILOS_LEGENDA = {
    # nome: (fonte, tamanho, contorno, sombra, caixa_no_destaque)
    "classico": ("Anton", 168, 11, 6, False),
    "caixa": ("Anton", 156, 10, 0, True),
    "moderna": ("Montserrat Black", 132, 9, 5, False),
    # estilo referência (TikTok de notícia de games): UMA palavra por vez, menor, inclinada
    "palavra": ("Anton", 118, 8, 5, False),
}
CORES = {  # ASS usa BGR
    "amarelo": "00D4FF", "verde": "5AF03A", "vermelho": "3B3BFF", "ciano": "FFE522", "rosa": "D84FFF",
}
ALTURA_PADRAO = 0.615  # centro da legenda (fração da altura); 1180 px

TRANSICOES = ("flash", "zoom", "glitch", "pan_dir", "pan_esq", "leak", "seco")
AUTO_TRANSICOES = ("zoom", "pan_esq", "glitch", "flash", "pan_dir")

EMOJI_FONTE = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
EMOJI_PALAVRAS = [  # (raízes da palavra destacada, emoji) — em ordem de prioridade
    (("morte", "morre", "morto", "encerr", "deslig", "fim", "fecha", "cancel", "acabou"), "💀"),
    (("dinheiro", "fatur", "preco", "lucro", "bilh", "milh", "reais", "dolar", "venda", "receita", "caro"), "💰"),
    (("chocante", "repentin", "surpre", "inacredit", "absurd", "louc", "bizarr"), "😱"),
    (("recorde", "campe", "vence", "vitoria", "melhor", "lider", "topo"), "🏆"),
    (("hype", "incrivel", "sucesso", "explod", "brabo", "insano", "fogo", "lancamento", "lanca"), "🔥"),
    (("tempo", "dias", "prazo", "anos", "espera", "atras"), "⏳"),
    (("data", "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro",
      "outubro", "novembro", "dezembro"), "📅"),
    (("erro", "bug", "problema", "falha", "crise", "polemic", "critica"), "⚠️"),
    (("gratis", "graca", "free", "presente", "gift"), "🎁"),
    (("jogador", "players", "comunidade", "fas", "galera"), "👥"),
    (("comenta", "opiniao", "acha"), "💬"),
]

_crops = {}

# ---------------------------------------------------------------- formato (vertical 9:16 ou horizontal 16:9)

LAYOUTS = {
    "vertical": {"W": 1080, "H": 1920, "cx": 540, "tit_y": 0.42, "tit_larg": (900, 960), "tit_max": 12,
                 "leg_altura": 0.615, "leg_escala": 1.0, "leg_limite": (0.35, 0.82), "fg": (90, 520, -120),
                 "cobre": 1.25, "zoom_v": 0.07, "emoji": True},
    # YouTube: legenda menor embaixo, cortes mais longos, movimento de câmera mais calmo
    "horizontal": {"W": 1920, "H": 1080, "cx": 960, "tit_y": 0.46, "tit_larg": (1340, 1440), "tit_max": 24,
                   "leg_altura": 0.86, "leg_escala": 0.58, "leg_limite": (0.5, 0.92), "fg": (240, 140, 0),
                   "cobre": 1.15, "zoom_v": 0.04, "emoji": False},
}
L = dict(LAYOUTS["vertical"])


def definir_formato(projeto):
    """Ajusta dimensões e layout de todos os módulos de render para o formato do projeto."""
    global W, H, CUT
    import comum
    nome = "horizontal" if (projeto or {}).get("formato") == "horizontal" else "vertical"
    L.clear()
    L.update(LAYOUTS[nome])
    W, H = L["W"], L["H"]
    CUT = 3.2 if nome == "horizontal" else 1.7
    for mod in (comum, efeitos, cartoes):
        mod.W, mod.H = W, H
    return nome


def area_util(src, t):
    """Detecta tarjas pretas (letterbox) do trailer para recortar só a imagem útil."""
    k = (src, int(t))
    if k not in _crops:
        r = run(["ffmpeg", "-hide_banner", "-ss", f"{t:.2f}", "-i", src, "-t", "1.5", "-vf",
                 "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"])
        m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
        _crops[k] = m[-1] if m else None
    return _crops[k]


# ---------------------------------------------------------------- segmentos

PUNCH = {"flash": 0.22, "zoom": 0.38, "glitch": 0.08, "leak": 0.12}


def _fx_transicao(tipo):
    """Efeito de entrada aplicado no 1º corte de um tópico (os pans e o leak são feitos depois)."""
    if tipo == "flash":
        return (f",gblur=sigma=18:enable='lt(t,{TRANS * 0.5})',gblur=sigma=7:enable='between(t,{TRANS * 0.5},{TRANS})'"
                f",fade=t=in:st=0:d={TRANS * 0.6}:color=white")
    if tipo == "zoom":
        return f",gblur=sigma=26:enable='lt(t,{TRANS * 0.45})',gblur=sigma=9:enable='between(t,{TRANS * 0.45},{TRANS})'"
    if tipo == "glitch":
        return (",rgbashift=rh=-34:bh=34:enable='lt(t,0.09)'"
                ",rgbashift=rh=22:gv=-14:bh=-22:bv=10:enable='between(t,0.09,0.18)'"
                ",rgbashift=rh=-10:bh=10:enable='between(t,0.18,0.26)'"
                ",noise=alls=45:allf=t:enable='lt(t,0.22)'"
                ",eq=contrast=1.35:saturation=1.5:enable='lt(t,0.07)'")
    if tipo == "leak":
        return f",gblur=sigma=10:enable='lt(t,{TRANS * 0.6})'"
    return ""


_dur_fonte = {}
# todo segmento com a mesma taxa e a mesma base de tempo: a junção sem reencodar depende disso
SEG_PADRAO = ("-r", str(FPS), "-video_track_timescale", "15360")


def _inicio_seguro(src, t0, dur):
    """O trecho nunca passa do fim do arquivo (ffmpeg devolveria um segmento mais curto e o vídeo inteiro
    ficaria dessincronizado do áudio — a imagem congela no fim)."""
    if src not in _dur_fonte:
        try:
            _dur_fonte[src] = duracao(src)
        except Exception:
            _dur_fonte[src] = 0
    total = _dur_fonte[src]
    if total and t0 + dur > total - 0.15:
        t0 = max(0.0, total - dur - 0.15)
    return t0


def _quadros_exatos(dur):
    """Segmento sempre com o número exato de quadros: se a fonte acabar antes, repete o último quadro."""
    n = max(1, int(round(dur * FPS)))
    return n, f",tpad=stop_mode=clone:stop_duration={dur + 1:.3f}"


_alt_fonte = {}
_ultimo = {}


def _nitidez(src):
    """Vídeo de baixa resolução (gameplay antigo, 360p): realce leve para não ficar borrado ao ampliar."""
    if src not in _alt_fonte:
        try:
            r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                     "-of", "csv=p=0", src])
            _alt_fonte[src] = int(r.stdout.strip().split()[0])
        except Exception:
            _alt_fonte[src] = 1080
    return ",unsharp=5:5:0.9:3:3:0.4" if _alt_fonte[src] < 700 else ""


MOLDURA = {"ativa": False, "alt": 1500}  # vertical: cena numa janela ~4:5 sobre a própria cena desfocada


def _fundo_moldura(rotulo_in, rotulo_out):
    """Fundo desfocado (barato: desfoca em baixa resolução e amplia)."""
    return (f"{rotulo_in}crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})',scale=216:384,boxblur=10:2,"
            f"scale={W}:{H},eq=brightness=-0.10:saturation=1.15{rotulo_out}")


def seg_video(src, t0, dur, out, zoom_in, trans):
    t0 = _inicio_seguro(src, t0, dur)
    if MOLDURA["ativa"]:
        return _seg_video_moldura(src, t0, dur, out, zoom_in, trans)
    n, pad = _quadros_exatos(dur)
    zv = L["zoom_v"]
    z = f"1+{zv}*t/{dur:.3f}" if zoom_in else f"{1 + zv}-{zv}*t/{dur:.3f}"
    if trans in PUNCH:
        z = f"({z})+{PUNCH[trans]}*max(0\\,1-t/{TRANS})"
    c = area_util(src, t0)
    pre = f"crop={c[0]}:{c[1]}:{c[2]}:{c[3]}," if c and int(c[1]) >= 300 else ""
    vf = (f"{pre}crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})',scale={W}:{H},"
          f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame,"
          f"crop={W}:{H},setsar=1,fps={FPS},eq=contrast=1.06:saturation=1.12:gamma=1.1" + _nitidez(src) +
          _fx_transicao(trans) + pad)
    r = ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur + 0.5:.3f}", "-vf", vf, "-an", "-frames:v", n,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)
    _ultimo["seg"] = {"t0": round(t0, 2), "crop": c, "fonte_s": _dur_fonte.get(src), "stderr": (r.stderr or "")[-300:],
                      "bytes": os.path.getsize(out) if os.path.exists(out) else -1}


def _seg_video_moldura(src, t0, dur, out, zoom_in, trans):
    """Estilo referência: mostra ~40% da largura do trailer (em vez de ~32% no corte 9:16), numa janela
    de 1080x1500 centrada; em cima e embaixo, a própria cena desfocada."""
    n, pad = _quadros_exatos(dur)
    hf = MOLDURA["alt"]
    zv = L["zoom_v"]
    z = f"1+{zv}*t/{dur:.3f}" if zoom_in else f"{1 + zv}-{zv}*t/{dur:.3f}"
    if trans in PUNCH:
        z = f"({z})+{PUNCH[trans]}*max(0\\,1-t/{TRANS})"
    c = area_util(src, t0)
    pre = f"crop={c[0]}:{c[1]}:{c[2]}:{c[3]}," if c and int(c[1]) >= 300 else ""
    grafo = (f"[0:v]{pre}fps={FPS},split[a][b];" + _fundo_moldura("[a]", "[bg]") + ";"
             f"[b]crop='min(iw,ih*{W}/{hf})':'min(ih,iw*{hf}/{W})',scale={W}:{hf},"
             f"scale=w='trunc({W}*({z})/2)*2':h='trunc({hf}*({z})/2)*2':eval=frame,crop={W}:{hf},"
             f"eq=contrast=1.06:saturation=1.15:gamma=1.08{_nitidez(src)}[fg];"
             f"[bg][fg]overlay=0:{(H - hf) // 2},setsar=1" + _fx_transicao(trans) + pad)
    r = ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur + 0.5:.3f}", "-filter_complex", grafo, "-an", "-frames:v", n,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)
    _ultimo["seg"] = {"t0": round(t0, 2), "crop": c, "fonte_s": _dur_fonte.get(src), "stderr": (r.stderr or "")[-300:],
                      "bytes": os.path.getsize(out) if os.path.exists(out) else -1}


def _recorte(m):
    """Filtro de recorte vertical (faixa sem texto) para screenshots de loja."""
    rc = m.get("recorte") if m else None
    if not rc:
        return ""
    y0, y1 = float(rc[0]), float(rc[1])
    return f"crop=iw:ih*{y1 - y0:.4f}:0:ih*{y0:.4f},"


def seg_imagem(src, dur, out, k, trans, encaixar=False, pre=""):
    n = max(1, int(round(dur * FPS)))
    if encaixar:
        # print/foto inteira (sem cortar as laterais) sobre fundo desfocado da própria imagem
        punch = f"+{PUNCH[trans]}*max(0\\,1-t/{TRANS})" if trans in PUNCH else ""
        z = f"(1.0+0.05*t/{dur:.3f}{punch})"
        vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,"
              f"eq=brightness=-0.12[bg];[b]scale={W - L['fg'][0]}:{H - L['fg'][1]}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2{L['fg'][2]:+d},setsar=1,fps={FPS},"
              f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H}" + _fx_transicao(trans))
        ffmpeg("-loop", 1, "-t", f"{dur + 0.5:.3f}", "-i", src, "-filter_complex", vf, "-frames:v", n,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)
        return
    pans = ["x='iw/2-(iw/zoom/2)'", f"x='(iw-iw/zoom)*on/{n}'", f"x='(iw-iw/zoom)*(1-on/{n})'"]
    punch = f"+{PUNCH[trans]}*max(0\\,1-on/{int(TRANS * FPS)})" if trans in PUNCH else ""
    if MOLDURA["ativa"]:
        hf = MOLDURA["alt"]
        cw, ch = int(W * 1.25) // 2 * 2, int(hf * 1.25) // 2 * 2
        grafo = (f"[0:v]{pre}split[a][b];" + _fundo_moldura("[a]", "[bg]") + ";"
                 f"[b]scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},"
                 f"zoompan=z='1.05+0.10*on/{n}{punch}':{pans[k % 3]}:y='ih/2-(ih/zoom/2)':d={n}:s={W}x{hf}:fps={FPS},"
                 f"eq=contrast=1.05:saturation=1.12[fg];[bg]fps={FPS}[bgf];"
                 f"[bgf][fg]overlay=0:{(H - hf) // 2}:shortest=1,setsar=1" + _fx_transicao(trans))
        ffmpeg("-loop", 1, "-i", src, "-filter_complex", grafo, "-frames:v", n,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)
        return
    # cobre 1350x2400 (paisagem ou retrato, ex.: screenshot de celular) e faz o Ken Burns
    cw, ch = int(W * L["cobre"]) // 2 * 2, int(H * L["cobre"]) // 2 * 2
    vf = (f"{pre}scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},"
          f"zoompan=z='1.05+0.10*on/{n}{punch}':{pans[k % 3]}:y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS},"
          f"setsar=1,eq=contrast=1.05:saturation=1.1" + _fx_transicao(trans))
    ffmpeg("-loop", 1, "-i", src, "-frames:v", n, "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)


def seg_cartao(src, dur, out, trans):
    """Cartão gerado (manchete/número): entra com um "pop" (zoom que assenta em 0,22 s) e respira devagar.
    Sem Ken Burns de recorte — o texto do cartão não pode sair da tela."""
    n = max(1, int(round(dur * FPS)))
    punch = f"+{PUNCH[trans]}*max(0\\,1-t/{TRANS})" if trans in PUNCH else ""
    z = f"(1.0+0.025*t/{dur:.3f}+0.07*max(0\\,1-t/0.22){punch})"
    vf = (f"scale={W}:{H},setsar=1,fps={FPS},"
          f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H}" + _fx_transicao(trans))
    ffmpeg("-loop", 1, "-t", f"{dur + 0.2:.3f}", "-i", src, "-vf", vf, "-frames:v", n,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)


def seg_video_proprio(src, t0, dur, out, trans):
    """Clipe enviado pelo usuário: vertical vira tela cheia; horizontal fica inteiro sobre fundo desfocado."""
    t0 = _inicio_seguro(src, t0, dur)
    n, pad = _quadros_exatos(dur)
    punch = f"+{PUNCH[trans]}*max(0\\,1-t/{TRANS})" if trans in PUNCH else ""
    z = f"(1.0+0.04*t/{dur:.3f}{punch})"
    vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,"
          f"eq=brightness=-0.12[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},"
          f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H}" + _fx_transicao(trans) + pad)
    ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur + 0.5:.3f}", "-filter_complex", vf, "-an", "-frames:v", n,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)


# ---------------------------------------------------------------- YouTube "trailer corrido" (fórmula dos tops)

FADE_CAP = 0.35      # mergulho no preto entre um jogo e outro (cada lado)
CORTE_FINAL = 1.7    # encerramento (depois da última fala): montagem rápida dos jogos do vídeo
CAUDA_YT = 20.0      # tela final do YouTube: os cartões ("assista também", inscrever) cabem nos últimos 5-20 s
CORTE_ABERTURA = 2.4  # montagem da abertura: um plano curto de cada jogo da lista


_dims = {}


def _tamanho(src):
    if src not in _dims:
        r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", src])
        try:
            w, h = (int(x) for x in r.stdout.strip().split("\n")[0].split(",")[:2])
            _dims[src] = (w, h)
        except Exception:
            _dims[src] = None
    return _dims[src]


def area_do_trecho(src, t0, dur):
    """Imagem útil do trecho (tarja preta fora) em fração [x, y, w, h], por 6 quadros do próprio trecho.
    Mais firme que o cropdetect: um quadro claro no meio (flash, transição) não desfaz a tarja."""
    import numpy as np
    w, h = 192, 108
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t0:.3f}", "-i", src, "-t", f"{max(dur, 0.5):.3f}",
                        "-vf", f"fps={6 / max(dur, 0.5):.4f},scale={w}:{h},format=gray", "-frames:v", "6",
                        "-f", "rawvideo", "-"], capture_output=True)
    q = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(q) // (w * h)
    if n < 2:
        return None
    q = q[: n * w * h].reshape(n, h, w)
    col = (q[:, 27:81, :].max(1) > 40).mean(0)
    lin = (q[:, :, 48:144].max(2) > 40).mean(0)
    cs, ls = np.nonzero(col >= 0.5)[0], np.nonzero(lin >= 0.5)[0]
    if len(cs) < w * 0.4 or len(ls) < h * 0.4:
        return None
    x0, x1, y0, y1 = int(cs[0]), int(cs[-1]) + 1, int(ls[0]), int(ls[-1]) + 1
    # tarja de verdade é simétrica; cena escura num lado só (terror, noite) não é tarja
    if abs(x0 - (w - x1)) > w * 0.04:
        x0, x1 = 0, w
    if abs(y0 - (h - y1)) > h * 0.05:
        y0, y1 = 0, h
    return [x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h]


def _delogo(src, marca):
    """Apaga (borra) a logo fixa de site que veio no vídeo — caixa em fração da imagem original."""
    d = _tamanho(src) if marca else None
    if not d:
        return ""
    w, h = d
    x, y = max(1, int(marca[0] * w)), max(1, int(marca[1] * h))
    bw, bh = min(int(marca[2] * w), w - x - 2), min(int(marca[3] * h), h - y - 2)
    return f"delogo=x={x}:y={y}:w={bw}:h={bh}," if bw > 4 and bh > 4 else ""


def seg_corrido(src, t0, dur, out, fade_in=False, fade_out=False, marca=None, area=None):
    """Trecho do trailer tocando direto (sem zoom nem efeito), com mergulho no preto na troca de jogo."""
    t0 = _inicio_seguro(src, t0, dur)
    n, pad = _quadros_exatos(dur)
    # tarja: o trecho (cropdetect) acha plano 4:3 no meio de trailer; a medida do vídeo inteiro (area) acha a
    # tarja que o cropdetect não vê (logo de site dentro dela). Vale a que recorta mais nas laterais.
    c = area_do_trecho(src, t0, dur)
    if area and (not c or area[2] < c[2] - 0.02):
        c = area
    if c and (c[2] < 0.985 or c[3] < 0.985):
        x, y, w, h = c
        pre = f"crop=trunc(iw*{w:.4f}/2)*2:trunc(ih*{h:.4f}/2)*2:trunc(iw*{x:.4f}):trunc(ih*{y:.4f}),"
    else:
        pre = ""
    pre = _delogo(src, marca) + pre
    fx = ""
    if fade_in:
        fx += f",fade=t=in:st=0:d={FADE_CAP:.2f}"
    if fade_out and dur > FADE_CAP + 0.2:
        fx += f",fade=t=out:st={dur - FADE_CAP:.3f}:d={FADE_CAP:.2f}"
    vf = (f"{pre}crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})',scale={W}:{H},setsar=1,fps={FPS},"
          f"eq=contrast=1.03:saturation=1.06" + _nitidez(src) + pad + fx)
    ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur + 0.5:.3f}", "-vf", vf, "-an", "-frames:v", n,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", *SEG_PADRAO, out)


def e_corrido(projeto):
    return projeto.get("edicao") == "corrido" and L["W"] > L["H"]


def cenas_de_capitulo(projeto):
    """Tópicos que abrem um capítulo (menos a abertura): é onde entra o respiro, o preto e a ficha."""
    return [k for k, c in enumerate(projeto["cenas"]) if k > 0 and str(c.get("capitulo") or "").strip()]


def norm_txt(t):
    import unicodedata
    t = unicodedata.normalize("NFD", str(t).lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def pausa_da_cena(cena):
    """Segundos de "pausa com o som do jogo" depois do tópico (YouTube): a narração para e o jogo aparece."""
    try:
        return min(max(float(cena.get("pausa") or 0), 0.0), 20.0)
    except (TypeError, ValueError):
        return 0.0


def precisa_respiro(projeto):
    return L["W"] > L["H"] and (e_corrido(projeto) or any(pausa_da_cena(c) for c in projeto.get("cenas", [])))


def com_respiro(projeto):
    """Cópia do projeto com silêncio na voz: N s antes de cada capítulo (modo corrido, o trailer sobe nesse
    respiro) e a pausa pedida depois de um tópico (o som do jogo aparece: música, criatura, cena).
    Devolve (cópia, [(ponto de corte na voz original, segundos)]). A voz é emendada igual por quem chamar."""
    import copy
    pr = copy.deepcopy(projeto)
    if not precisa_respiro(pr):
        return pr, []
    try:
        g = min(max(float(pr.get("respiro", 4) or 0), 0.0), 10.0)
    except (TypeError, ValueError):
        g = 4.0
    mant = sorted((w for w in pr["palavras"] if w.get("manter") and "ns" in w and w.get("cena") is not None),
                  key=lambda w: w["ns"])
    cortes = []
    if g > 0 and e_corrido(pr):
        caps = set(cenas_de_capitulo(pr))
        vistos = set()
        for i, w in enumerate(mant):
            if i == 0 or w["cena"] in vistos or w["cena"] not in caps:
                continue
            vistos.add(w["cena"])
            ant = mant[i - 1]
            if ant["cena"] == w["cena"]:
                continue
            cortes.append((round((ant.get("ne", ant["ns"]) + w["ns"]) / 2, 3), g))
    for ci, c in enumerate(pr["cenas"]):
        pz = pausa_da_cena(c)
        if not pz:
            continue
        idx = [i for i, w in enumerate(mant) if w["cena"] == ci]
        if not idx:
            continue
        ult = mant[idx[-1]]
        fim = ult.get("ne", ult["ns"])
        prox = mant[idx[-1] + 1]["ns"] if idx[-1] + 1 < len(mant) else None
        t = fim + 0.15 if prox is None else min(fim + 0.15, (fim + prox) / 2)
        cortes.append((round(t, 3), pz))
    cortes.sort()
    for w in pr["palavras"]:
        if "ns" in w:
            soma = sum(gg for c, gg in cortes if c <= w["ns"])
            if soma:
                w["ns"] = round(w["ns"] + soma, 3)
                if "ne" in w:
                    w["ne"] = round(w["ne"] + soma, 3)
    pr["duracao_voz"] = float(pr["duracao_voz"]) + sum(gg for _, gg in cortes)
    pr["respiro_cortes"] = [list(x) for x in cortes]
    return pr, cortes


def voz_com_respiro(voz_wav, cortes, destino):
    """A voz com silêncio em cada ponto de corte [(t, segundos)]."""
    if not cortes:
        return voz_wav
    lim = [0.0] + [c for c, _ in cortes] + [None]
    partes = []
    for i in range(len(lim) - 1):
        a, b = lim[i], lim[i + 1]
        tr = f"atrim=start={a:.3f}" + (f":end={b:.3f}" if b is not None else "")
        pad = f",apad=pad_dur={cortes[i][1]:.3f}" if b is not None else ""
        partes.append(f"[0:a]{tr},asetpts=PTS-STARTPTS{pad}[p{i}]")
    n = len(partes)
    grafo = ";".join(partes) + ";" + "".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[a]"
    ffmpeg("-i", voz_wav, "-filter_complex", grafo, "-map", "[a]", "-c:a", "pcm_s16le", destino)
    return destino


def _faixa_midia(m):
    """(início, fim) tocável de uma mídia de vídeo do banco."""
    a = float(m.get("t", 0))
    return a, a + float(m.get("dur", 3))


def montar_corrido(projeto, banco, faixas, seg_dir, pasta_raw=""):
    """Decide os trechos do modo corrido: cada jogo toca o trailer dele em sequência (de onde parou, pulando só
    os planos ruins), a abertura é uma montagem rápida dos jogos da lista e cada capítulo fecha no fim do trailer
    (logo do jogo)."""
    cenas = projeto["cenas"]
    caps = set(cenas_de_capitulo(projeto))
    primeiro_cap = min(caps) if caps else None
    g_resp = float(projeto.get("respiro", 4) or 0) if (projeto.get("respiro_cortes") and e_corrido(projeto)) else 0.0
    brutos, imgs, ordem = {}, {}, []
    for c in cenas:
        j = c.get("jogo")
        if j and j not in ordem:
            ordem.append(j)
    for mid, m in banco.items():
        if m.get("cartao") or m.get("capa") or m.get("jogo") in (None, "cartao", "materia", "commons"):
            continue
        chave_j = ("p", m["personagem"]) if m.get("personagem") else m["jogo"]  # personagem: reservado ao tópico dele
        if m["tipo"] == "v":
            brutos.setdefault(chave_j, []).append((m["arquivo"], *_faixa_midia(m), mid))
        else:
            imgs.setdefault(chave_j, []).append(mid)
    # planos bons vizinhos viram um trecho contínuo só (o trailer corre com os cortes dele)
    vids = {}
    for j, lst in brutos.items():
        lst.sort()
        faixas_j = []
        for arq, a, b, mid in lst:
            if faixas_j and faixas_j[-1][0] == arq and a <= faixas_j[-1][2] + 0.35:
                faixas_j[-1][2] = max(faixas_j[-1][2], b)
            else:
                faixas_j.append([arq, a, b, mid])
        vids[j] = [f for f in faixas_j if f[2] - f[1] >= 1.0]
    cursor = {}  # jogo -> [índice do trecho, posição]
    visitas = {}
    esgotado, alterna, n_foto = {}, {}, {}

    def tomar(j, d):
        """Pedaços (mid, t0, dur) que cobrem d segundos do material do jogo j, continuando de onde parou."""
        lst = vids.get(j)
        if not lst:
            return []
        i, pos = cursor.get(j, [0, None])
        out, falta, voltas = [], d, 0
        fotos = imgs.get(j) or []
        while falta > 0.02 and voltas < 3 * len(lst) + 3:
            if esgotado.get(j) and fotos and alterna.get(j) == "foto" and falta > 2.0:
                # o vídeo do jogo já passou inteiro: intercala as imagens oficiais antes de repetir trecho
                k_img = n_foto.get(j, 0)
                n_foto[j] = k_img + 1
                d_img = falta if falta - 3.5 < 1.5 else 3.5
                out.append((fotos[k_img % len(fotos)], 0.0, d_img))
                falta -= d_img
                alterna[j] = "video"
                continue
            arq, a, b, mid = lst[i]
            p = a if pos is None else max(pos, a)
            disp = b - p
            if disp < 0.9:
                if i + 1 >= len(lst):
                    esgotado[j] = True
                    if not out or banco[out[-1][0]]["tipo"] == "v":
                        alterna[j] = "foto"
                i, pos, voltas = (i + 1) % len(lst), None, voltas + 1
                continue
            take = min(falta, disp, 6.0 if esgotado.get(j) else 1e9)
            if esgotado.get(j):
                alterna[j] = "foto"
            if 0 < falta - take < 0.8:  # não deixa um pedacinho solto no fim
                take = max(take - (0.8 - (falta - take)), 0.5)
            out.append((mid, p, take))
            falta -= take
            pos = p + take
        cursor[j] = [i, pos]
        return out

    def final_do_trailer(j, d):
        """Os últimos d segundos do arquivo em uso (logo/título do jogo), se ainda não foram mostrados."""
        lst = vids.get(j)
        if not lst:
            return None
        i, pos = cursor.get(j, [0, None])
        arq, a, b, mid = lst[i]
        try:
            total_arq = _dur_fonte.get(arq) or duracao(os.path.join(pasta_raw, arq))
        except Exception:
            return None
        ini = total_arq - d - 0.3
        if ini < (pos if pos is not None else a) + 1.0 or "_g_" in arq:  # gameplay longa não tem logo no fim
            return None
        return (mid, ini, d)

    def plano_abertura(j):
        lst = [f for f in vids.get(j, []) if f[2] - f[1] >= CORTE_ABERTURA + 0.3] or vids.get(j)
        if not lst:
            return None
        v = visitas.get(j, 0)
        visitas[j] = v + 1
        arq, a, b, mid = lst[(len(lst) // 2 + v) % len(lst)]  # do meio do trailer: a parte de ação
        return (mid, max(a, min((a + b) / 2 - CORTE_ABERTURA / 2, b - CORTE_ABERTURA)), CORTE_ABERTURA)

    tarefas, lista, usados, k = [], [], [], 0
    giro = 0
    for fi, (c, a, b) in enumerate(faixas):
        dur = b - a
        jogo = cenas[c].get("jogo") or (ordem[0] if ordem else None)
        prox_cap = fi + 1 < len(faixas) and faixas[fi + 1][0] in caps
        abre_cap = c in caps
        usados.append({"cena": c, "inicio": round(a, 2), "fim": round(b, 2), "cortes": [],
                       "entrada": "preto" if abre_cap else "corrido", "transicao": "fade" if abre_cap else "inicio"})
        pecas = []
        if primeiro_cap is not None and c < primeiro_cap and len([j for j in ordem if vids.get(j)]) >= 3:
            # abertura: montagem com um plano de cada jogo da lista (na ordem dos capítulos)
            com_video = [j for j in ordem if vids.get(j)]
            t_acum = 0.0
            while t_acum < dur - 0.05:
                d = min(CORTE_ABERTURA, dur - t_acum)
                if dur - t_acum - d < 0.8:
                    d = dur - t_acum
                pl = plano_abertura(com_video[giro % len(com_video)])
                giro += 1
                pecas.append((pl[0], pl[1], d))
                t_acum += d
        else:
            fim = None
            if prox_cap and g_resp:
                fim = final_do_trailer(jogo, min(g_resp + 1.5, dur * 0.5)) if dur > g_resp + 3 else None
            final_m = 0.0
            if fi == len(faixas) - 1 and len([j for j in ordem if vids.get(j)]) >= 1:
                final_m = min(float(projeto.get("cauda", CAUDA_YT) or 0), max(dur - 2.0, 0.0))
            corpo = dur - (fim[2] if fim else 0) - final_m
            pecas = []
            for pn in cenas[c].get("personagens") or []:  # o personagem citado aparece primeiro
                fatia = min(8.0, corpo * 0.6) - sum(p[2] for p in pecas)
                if fatia < 1.0:
                    break
                pv = tomar(("p", pn), fatia)
                if not pv and imgs.get(("p", pn)):
                    pv = [(imgs[("p", pn)][0], 0.0, min(3.5, fatia))]
                pecas += pv
            pecas += tomar(jogo, corpo - sum(p[2] for p in pecas))
            if not pecas and corpo > 0.05:
                # jogo sem vídeo: as imagens dele (inteiras), 4 s cada
                ims = imgs.get(jogo) or [x for j in ordem for x in imgs.get(j, [])][:6]
                nn = max(1, round(corpo / 4))
                pecas = [(ims[i % len(ims)], 0.0, corpo / nn) for i in range(nn)] if ims else []
            if fim:
                pecas.append(fim)
            if final_m > 0.5:
                # encerramento dinâmico: cortes rápidos com os melhores planos de todos os jogos (sem o som deles;
                # a cama sonora sobe sozinha quando a voz acaba)
                com_video = [j for j in ordem if vids.get(j)]
                t_acum, g2 = 0.0, 0
                while t_acum < final_m - 0.05:
                    d = min(CORTE_FINAL, final_m - t_acum)
                    if final_m - t_acum - d < 0.8:
                        d = final_m - t_acum
                    pl = plano_abertura(com_video[(g2 * 3 + 1) % len(com_video)])
                    g2 += 1
                    pecas.append((pl[0], pl[1], d, True))
                    t_acum += d
        if not pecas:  # nada de material: repete o último trecho em vez de quebrar o vídeo
            ult = tarefas[-1] if tarefas else None
            pecas = [(ult[1], ult[2].get("t", 0), dur)] if ult else []
        for pi, peca in enumerate(pecas):
            mid, t0, d = peca[:3]
            m = dict(banco[mid], t=t0, corrido=banco[mid]["tipo"] == "v", mudo=len(peca) > 3)
            m["fi"] = abre_cap and pi == 0
            m["fo"] = prox_cap and pi == len(pecas) - 1
            out = os.path.join(seg_dir, f"{k:04d}.mp4")
            tarefas.append((k, mid, m, None, d, out, None, 0, [mid]))
            usados[-1]["cortes"].append(mid)
            lista.append(out)
            k += 1
    return tarefas, lista, usados


def audio_corrido(tarefas, pasta_raw, pasta_trab):
    """O som dos trailers na mesma sequência dos trechos (silêncio em imagem), para tocar por baixo da voz."""
    pasta = os.path.join(pasta_trab, "som_jogo")
    shutil.rmtree(pasta, ignore_errors=True)
    os.makedirs(pasta)
    arqs = []
    for k, mid, m, src, d, out, *_ in tarefas:
        try:
            d = duracao(out)  # a duração real do trecho de imagem (mantém o som sincronizado)
        except Exception:
            pass
        wav = os.path.join(pasta, f"{k:04d}.wav")
        ok = False
        if (m.get("corrido") or m.get("som")) and m["tipo"] == "v" and not m.get("mudo"):
            src = os.path.join(pasta_raw, m["arquivo"])
            t0 = _inicio_seguro(src, m.get("t", 0), d)
            fades = ["afade=t=in:d=0.5", f"afade=t=out:st={max(d - 0.6, 0):.3f}:d=0.6"] if m.get("som") else []
            if m.get("fi"):
                fades.append(f"afade=t=in:st=0:d={FADE_CAP:.2f}")
            if m.get("fo"):
                fades.append(f"afade=t=out:st={max(d - FADE_CAP, 0):.3f}:d={FADE_CAP:.2f}")
            if m.get("som") and not m.get("corrido"):
                fades.append("loudnorm=I=-17:TP=-2:LRA=11")  # pausa com som do jogo: no volume certo sozinha
            af = ",".join(["aresample=48000", "aformat=sample_fmts=s16:channel_layouts=stereo", "apad",
                           f"atrim=0:{d:.4f}", "afade=t=in:d=0.04", f"afade=t=out:st={max(d - 0.04, 0):.3f}:d=0.04"] + fades)
            import subprocess
            r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t0:.3f}", "-i", src,
                                "-t", f"{d + 0.3:.3f}", "-vn", "-map", "0:a:0?", "-af", af, "-ac", "2", "-ar", "48000",
                                "-c:a", "pcm_s16le", wav], capture_output=True, text=True)
            ok = r.returncode == 0 and os.path.exists(wav) and os.path.getsize(wav) > 1000
        if not ok:
            ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{d:.4f}", "-c:a", "pcm_s16le", wav)
        arqs.append(wav)
    lst = os.path.join(pasta, "lista.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in arqs)
    destino = os.path.join(pasta_trab, "som_jogo.wav")
    ffmpeg("-f", "concat", "-safe", 0, "-i", lst, "-c:a", "pcm_s16le", destino)
    return destino


# ---------------------------------------------------------------- linha do tempo

def tipo_entrada(projeto, cena, ordem):
    """J, L ou seco para a troca de tópico. 'auto' alterna J e L (começando por J)."""
    escolha = str(projeto["cenas"][cena].get("entrada") or "auto").upper()
    if escolha in ("J", "L", "SECO"):
        return escolha.lower() if escolha == "SECO" else escolha
    return "J" if ordem % 2 == 1 else "L"


def linha_tempo(projeto):
    """Início/fim de cada cena na timeline final, a partir das palavras mantidas, com J-cut/L-cut nas trocas."""
    mant = [w for w in projeto["palavras"] if w.get("manter") and "ns" in w and w.get("cena") is not None]
    cauda = float(projeto.get("cauda", CAUDA_YT if projeto.get("formato") == "horizontal" else 0) or 0)
    total = projeto["duracao_voz"] + 0.6 + cauda
    inicios = {}
    for w in mant:
        inicios.setdefault(w["cena"], w["ns"])
    cenas = sorted(inicios)
    corrido = e_corrido(projeto)
    caps = set(cenas_de_capitulo(projeto)) if corrido else set()
    try:
        desloc = max(0, min(int(projeto.get("jl_quadros", QUADROS_JL)), 30)) / FPS
    except (TypeError, ValueError):
        desloc = QUADROS_JL / FPS
    cortes, tipos = {}, {}
    for k in range(1, len(cenas)):
        c, ant = cenas[k], cenas[k - 1]
        fala = inicios[c]
        lim_a = (cortes.get(ant, 0.0) if k > 1 else 0.0) + MIN_CENA
        prox = inicios[cenas[k + 1]] if k + 1 < len(cenas) else total
        lim_b = prox - MIN_CENA
        tipo = tipo_entrada(projeto, c, k)
        if corrido and c in caps:
            tipo = "preto"  # fórmula dos tops: o próximo jogo entra ~1 s antes da fala, depois do respiro
        t = fala
        if tipo == "J":
            t = fala + desloc
        elif tipo == "L":
            t = fala - desloc
        elif tipo == "preto":
            t = fala - 0.9
        t = min(max(t, lim_a), max(lim_b, lim_a))
        if (tipo == "J" and t <= fala + 0.02) or (tipo == "L" and t >= fala - 0.02):
            tipo, t = "seco", fala
        cortes[c], tipos[c] = t, tipo
    faixas = []
    for k, c in enumerate(cenas):
        a = 0.0 if k == 0 else cortes[c]
        b = cortes[cenas[k + 1]] if k + 1 < len(cenas) else total
        faixas.append((c, a, b))
    return faixas, total, tipos


def transicoes(projeto, faixas):
    """Transição de cada troca de tópico {cena: tipo}. Saída do gancho: projeto["transicao_gancho"]."""
    res = {}
    auto_k = 0
    for fi, (c, a, b) in enumerate(faixas):
        if fi == 0:
            continue
        if fi == 1:
            g = str(projeto.get("transicao_gancho") or "pan")
            res[c] = {"pan": "pan_dir", "leak": "leak", "pan_leak": "pan_leak"}.get(g, g if g in TRANSICOES else "pan_dir")
            continue
        esc = str(projeto["cenas"][c].get("transicao") or "auto")
        if esc in TRANSICOES:
            res[c] = esc
        elif projeto.get("transicoes_modo") == "por_jogo" and L["H"] > L["W"]:
            # estilo referência: whip pan (com woosh) quando muda o JOGO; mesmo jogo = corte seco
            ant = faixas[fi - 1][0]
            if projeto["cenas"][c].get("jogo") != projeto["cenas"][ant].get("jogo"):
                res[c] = "pan_dir" if auto_k % 2 == 0 else "pan_esq"
                auto_k += 1
            else:
                res[c] = "seco"
        elif L["W"] > L["H"]:
            # YouTube: efeito só na virada de capítulo (pan + woosh); dentro do capítulo, corte seco ou zoom leve
            if projeto["cenas"][c].get("capitulo"):
                res[c] = "pan_dir" if auto_k % 2 == 0 else "pan_esq"
                auto_k += 1
            else:
                res[c] = "zoom" if fi % 4 == 0 else "seco"
        else:
            res[c] = AUTO_TRANSICOES[auto_k % len(AUTO_TRANSICOES)]
            auto_k += 1
    return res


# ---------------------------------------------------------------- legenda, título e emojis

def _ts(t):
    t = max(t, 0)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def marcar_destaques(projeto):
    """ids (posição em projeto["palavras"]) das palavras destacadas: aceita palavra única e expressão
    ("morte definitiva", "15 de janeiro de 2027") — todas as palavras da expressão ficam destacadas."""
    marcadas = set()
    por_cena = {}
    for i, w in enumerate(projeto["palavras"]):
        if w.get("manter") and "ns" in w and str(w.get("legenda", "")).strip():
            por_cena.setdefault(w.get("cena"), []).append((i, norm(w["legenda"])))
    for c, seq in por_cena.items():
        if c is None or c >= len(projeto["cenas"]):
            continue
        toks = [t for _, t in seq]
        for frase in projeto["cenas"][c].get("destaques", []):
            alvo = [norm(x) for x in str(frase).split() if norm(x)]
            if not alvo:
                continue
            for k in range(len(toks) - len(alvo) + 1):
                if toks[k:k + len(alvo)] == alvo:
                    marcadas.update(i for i, _ in seq[k:k + len(alvo)])
    return marcadas


def blocos_legenda(projeto):
    por_palavra = projeto.get("legenda_estilo") == "palavra" and L["H"] > L["W"]
    pal = [w for w in projeto["palavras"] if w.get("manter") and "ns" in w and str(w.get("legenda", "")).strip()
           and w.get("cena") is not None]
    blocos, cur = [], []
    for w in pal:
        if cur and (w["cena"] != cur[-1]["cena"] or w["ns"] - cur[-1]["ne"] > 0.6):
            blocos.append(cur)
            cur = []
        cur.append(w)
        fim = bool(re.search(r"[.,:;?!…]$", w["legenda"]))
        txt = " ".join(x["legenda"] for x in cur)
        # palavra por palavra: só junta palavrinha curta ("A", "DE") com a seguinte
        if por_palavra and not (len(cur) == 1 and len(w["legenda"]) <= 2 and not fim):
            blocos.append(cur)
            cur = []
            continue
        if len(cur) == 3 or fim or (len(cur) == 2 and len(txt) > 11):
            blocos.append(cur)
            cur = []
    if cur:
        blocos.append(cur)
    saida = []
    for bi, b in enumerate(blocos):
        ini = b[0]["ns"]
        fim = blocos[bi + 1][0]["ns"] if bi + 1 < len(blocos) and blocos[bi + 1][0]["ns"] - b[-1]["ne"] < 0.6 \
            else b[-1]["ne"] + 0.35
        saida.append({"pal": b, "ini": ini, "fim": fim, "cena": b[0]["cena"]})
    return saida


PALAVRAS_CURTAS = {"A", "O", "E", "DE", "DA", "DO", "NA", "NO", "EM", "UM", "AS", "OS", "AO", "À"}


def _quebra_titulo(txt, max_linha=14):
    """Quebra a chamada em no máximo 3 linhas SEM perder palavra: se não couber, aumenta o limite por linha
    (a manchete ajusta o tamanho de cada linha à largura da tela). Palavra curta ("A", "DE") não fica
    pendurada no fim da linha: desce junto com a palavra seguinte ("DOMINOU / A STEAM")."""
    palavras = txt.split()
    if not palavras:
        return []

    def quebra(m):
        linhas, cur = [], ""
        for p in palavras:
            if cur and len(cur) + 1 + len(p) > m:
                linhas.append(cur)
                cur = p
            else:
                cur = f"{cur} {p}".strip()
        return linhas + ([cur] if cur else [])

    m = max_linha
    linhas = quebra(m)
    while len(linhas) > 3:
        m += 1
        linhas = quebra(m)
    for i in range(len(linhas) - 1):
        ws = linhas[i].split()
        if len(ws) > 1 and ws[-1] in PALAVRAS_CURTAS:
            linhas[i] = " ".join(ws[:-1])
            linhas[i + 1] = f"{ws[-1]} {linhas[i + 1]}"
    return linhas


def legenda_ass(projeto, destino, corte_gancho):
    """Legenda em blocos (estilo/cor/altura do projeto) + título grande do gancho. Devolve os blocos."""
    nome_estilo = projeto.get("legenda_estilo") if projeto.get("legenda_estilo") in ESTILOS_LEGENDA else "classico"
    if nome_estilo == "palavra" and L["W"] > L["H"]:
        nome_estilo = "classico"  # YouTube: frases curtas embaixo leem melhor que palavra solta
    fonte, tam, contorno, sombra, caixa = ESTILOS_LEGENDA[nome_estilo]
    cor = CORES.get(projeto.get("legenda_cor"), CORES["amarelo"])
    try:
        chave_alt = "legenda_altura" if L["W"] < L["H"] else "legenda_altura_h"  # cada formato tem a sua altura
        altura = min(max(float(projeto.get(chave_alt, L["leg_altura"])), L["leg_limite"][0]), L["leg_limite"][1])
    except (TypeError, ValueError):
        altura = L["leg_altura"]
    tam, contorno, sombra = int(tam * L["leg_escala"]), max(3, int(contorno * L["leg_escala"])), int(sombra * L["leg_escala"])
    y = int(H * altura)
    destaque = (rf"{{\c&H000000&\3c&H{cor}&\bord{contorno + 16}\shad0}}" if caixa else rf"{{\c&H{cor}&}}")
    normal = rf"{{\c&HFFFFFF&\3c&H000000&\bord{contorno}\shad{sombra}}}" if caixa else r"{\c&HFFFFFF&}"
    linhas = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0", "",
              "[V4+ Styles]",
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
              "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
              "MarginR, MarginV, Encoding",
              f"Style: Cap,{fonte},{tam},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,1,0,1,"
              f"{contorno},{sombra},5,40,40,0,1",
              f"Style: Tit,Anton,132,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,10,0,5,60,60,0,1",
              f"Style: TitG,Anton,{TIT_TAM},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,13,8,5,30,30,0,1",
              f"Style: TitBox,Anton,{TIT_TAM},&H00000000,&H00000000,&H00{cor},&H00{cor},0,0,0,0,100,100,1,0,3,20,0,5,30,30,0,1",
              f"Style: Marca,Montserrat Black,{32 if L['H'] > L['W'] else 28},&H8CFFFFFF,&H8CFFFFFF,&HB4000000,&HB4000000,0,0,0,0,100,100,3,0,1,1.5,0,5,0,0,0,1",
              "Style: Fundo,Anton,10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
              f"Style: Capit,Montserrat Black,46,&H00000000,&H00000000,&H00{cor},&H00{cor},0,0,0,0,100,100,1,0,3,14,0,7,0,0,0,1",
              "Style: CapitN,Montserrat Black,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,2,0,1,3,2,7,0,0,0,1",
              "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    marcadas = {id(projeto["palavras"][i]) for i in marcar_destaques(projeto)}
    sem_legenda = projeto.get("legenda_estilo") == "nenhuma"
    esconde_ate = fim_titulo(projeto, corte_gancho) if projeto.get("titulo_estilo", "manchete") != "simples" else None
    blocos = blocos_legenda(projeto)
    for b in blocos:
        partes = []
        for w in b["pal"]:
            t = re.sub(r"[.,:;…]+$", "", w["legenda"]).upper()
            partes.append((destaque if id(w) in marcadas else normal) + t)
        b["palavras_destaque"] = [norm(w["legenda"]) for w in b["pal"] if id(w) in marcadas]
        b["destaque"] = bool(b["palavras_destaque"])
        pop = rf"{{\an5\pos({L['cx']},{y})\fscx70\fscy70\t(0,90,\fscx108\fscy108)\t(90,160,\fscx100\fscy100)}}"
        if nome_estilo == "palavra":  # inclinada, entra rápida (sem crescer demais)
            pop = rf"{{\an5\pos({L['cx']},{y})\fax-0.14\fscx86\fscy86\t(0,70,\fscx104\fscy104)\t(70,120,\fscx100\fscy100)}}"
        ini = b["ini"]
        if esconde_ate:
            if b["fim"] <= esconde_ate + 0.15:
                continue  # a chamada ocupa a tela: a legenda falada desse trecho não aparece
            ini = max(ini, esconde_ate)
        if not sem_legenda:
            linhas.append(f"Dialogue: 0,{_ts(ini)},{_ts(b['fim'])},Cap,,0,0,0,,{pop}{' '.join(partes)}")
    # YouTube: título do capítulo no canto quando ele começa (o mesmo dos capítulos da descrição)
    if L["W"] > L["H"]:
        vistos, n_cap = set(), 0
        for b in blocos:
            cap = str(projeto["cenas"][b["cena"]].get("capitulo") or "").strip()
            if not cap or b["cena"] in vistos:
                continue
            vistos.add(b["cena"])
            n_cap += 1
            if (b["cena"] == 0 or b["ini"] < 3 or f"cartao_cap_{b['cena']}" in projeto.get("banco", {})
                    or (e_corrido(projeto) and projeto["cenas"][b["cena"]].get("ficha"))):
                continue  # a abertura já tem a chamada; capítulo com cartela grande não repete no canto
            a0, a1 = b["ini"] + 0.1, b["ini"] + 3.6
            ent = r"{\move(-700,96,90,96,0,260)\t(3200,3500,\alpha&HFF&)}"
            linhas.append(f"Dialogue: 4,{_ts(a0)},{_ts(a1)},CapitN,,0,0,0,,{{\\move(-700,52,96,52,0,260)\\t(3200,3500,\\alpha&HFF&)}}"
                          f"CAPÍTULO {n_cap - 1}")
            linhas.append(f"Dialogue: 4,{_ts(a0)},{_ts(a1)},Capit,,0,0,0,,{ent}{cap.upper()}")
    # marca d'água do canal (discreta, o vídeo todo)
    marca = str(projeto.get("marca_dagua") or "").strip()
    if marca and not logo_do_canal(projeto):
        fim_v = max([b["fim"] for b in blocos] + [1.0]) + 1.0
        if L["W"] > L["H"]:
            pos = rf"\an3\pos({W - 36},{H - 26})"
        else:
            pos = rf"\an5\pos({L['cx']},{int(H * 0.845)})"
        linhas.append(f"Dialogue: 0,{_ts(0)},{_ts(fim_v + 5)},Marca,,0,0,0,,{{{pos}}}{marca}")
    # chamada dos primeiros ~2 s
    fim_tit = fim_titulo(projeto, corte_gancho)
    if fim_tit:
        linhas += titulo_ass(str(projeto.get("titulo_estilo") or "manchete"), str(projeto["titulo_tela"]).strip(), cor, fim_tit)
    with open(destino, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    return blocos, y


def fim_titulo(projeto, corte_gancho):
    """Até quando a chamada do gancho fica na tela (None se não houver chamada)."""
    if not (projeto.get("titulo_gancho", True) and str(projeto.get("titulo_tela") or "").strip()):
        return None
    return min(2.4, max(corte_gancho - 0.05, 1.3))


TIT_TAM = 176   # tamanho-base da chamada do gancho (px); cada linha é escalada para ocupar a largura
FONTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fontes")


def _largura_texto(texto, tam=TIT_TAM, fonte="Anton-Regular.ttf"):
    """Largura do texto como o libass desenha (calibrado: Anton sai a ~58% da medida do Pillow)."""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(os.path.join(FONTES_DIR, fonte), tam).getlength(texto) * 0.583
    except Exception:
        return len(texto) * tam * 0.27


CAPS = 0.5     # altura das maiúsculas do Anton no libass = 0,5 × tamanho
DESVIO = 0.0625  # com \an5 as maiúsculas ficam ~6% do tamanho abaixo do ponto: compensa


def titulo_ass(estilo, titulo, cor, fim):
    """Eventos ASS da chamada do gancho (o fundo desfocado/escuro é feito no ffmpeg).

    manchete: linhas gigantes ocupando a largura da tela, entrando "batendo" uma após a outra com tremida;
              a linha-chave num adesivo na cor de destaque, levemente inclinado;
    faixa:    faixa colorida de ponta a ponta atravessa a tela com a linha-chave; o resto em branco gigante;
    simples:  texto menor no topo (estilo antigo).
    """
    ls = _quebra_titulo(titulo.upper(), L["tit_max"] if estilo != "simples" else L["tit_max"] + 2)
    cx = L["cx"]
    if estilo == "simples":
        y_tit = int(H * 0.25)
        texto = r"\N".join(ls)
        if len(ls) > 1:
            texto = r"\N".join(ls[:-1]) + rf"\N{{\c&H{cor}&}}" + ls[-1]
        anim = (rf"{{\an5\pos({L['cx']},{y_tit})\fscx60\fscy60\alpha&HFF&\t(0,120,\alpha&H00&\fscx106\fscy106)"
                rf"\t(120,220,\fscx100\fscy100)\fad(0,180)}}")
        return [f"Dialogue: 1,{_ts(0.05)},{_ts(fim)},Tit,,0,0,0,,{anim}{texto}"]
    ev = []
    chave = len(ls) - 1  # linha-chave: a última (é onde costuma estar o nome/fato)
    largura_alvo = [L["tit_larg"][0] if i == chave else L["tit_larg"][1] for i in range(len(ls))]
    escalas = [min(max(largura_alvo[i] / max(_largura_texto(l), 1), 0.3), 2.6) for i, l in enumerate(ls)]
    caps = [TIT_TAM * CAPS * e for e in escalas]
    pad = 34
    gaps = 46
    total = sum(caps) + gaps * (len(ls) - 1) + 2 * pad
    if total > H * 0.8:  # horizontal: a chamada não pode passar da altura da tela
        f = H * 0.8 / total
        escalas = [e * f for e in escalas]
        caps = [c * f for c in caps]
        total = H * 0.8
    y = H * L["tit_y"] - total / 2
    sai = int((fim - 0.05) * 1000)
    for i, linha in enumerate(ls):
        if i == chave:
            y += pad
        yc = int(y + caps[i] / 2)
        y += caps[i] + gaps
        e = escalas[i]
        ep = int(e * 100)
        yt = int(yc - TIT_TAM * DESVIO * e)  # ponto do texto para as maiúsculas ficarem centradas em yc
        d = 110 * i
        larg = _largura_texto(linha) * e
        if estilo == "faixa" and i == chave:
            alt = int(caps[i] + 2 * pad)
            ev.append(rf"Dialogue: 2,{_ts(0.02 + d / 1000)},{_ts(fim)},Fundo,,0,0,0,,"
                      rf"{{\an7\move(-{W},{yc - alt // 2},0,{yc - alt // 2},0,170)\p1\1c&H{cor}&\1a&H00&\bord0\shad0\fad(0,150)}}"
                      rf"m 0 0 l {W} 0 {W} {alt} 0 {alt}{{\p0}}")
            ev.append(rf"Dialogue: 3,{_ts(0.02 + d / 1000)},{_ts(fim)},TitG,,0,0,0,,"
                      rf"{{\an5\q2\move(-600,{yt},{cx},{yt},0,170)\fscx{ep}\fscy{ep}\1c&H000000&\bord0\shad0\fad(0,150)}}{linha}")
            continue
        if i == chave:
            # adesivo: retângulo do tamanho da palavra, na cor de destaque, inclinado; texto preto por cima
            bw, bh = int(larg + 2 * pad + 20), int(caps[i] + 2 * pad)
            comum = rf"\org({cx},{yc})\frz3\alpha&HFF&\t({d},{d + 70},\alpha&H00&)\t({sai - 160},{sai},\alpha&HFF&)"
            slam = rf"\fscx145\fscy145\t({d},{d + 160},\fscx100\fscy100)"
            ev.append(rf"Dialogue: 2,{_ts(0.02)},{_ts(fim)},Fundo,,0,0,0,,"
                      rf"{{\an5\pos({cx},{yc}){comum}{slam}\p1\1c&H{cor}&\bord0\shad0}}"
                      rf"m 0 0 l {bw} 0 {bw} {bh} 0 {bh}{{\p0}}")
            ev.append(rf"Dialogue: 3,{_ts(0.02)},{_ts(fim)},TitG,,0,0,0,,"
                      rf"{{\an5\q2\pos({cx},{yt}){comum}\fscx{int(ep * 1.45)}\fscy{int(ep * 1.45)}"
                      rf"\t({d},{d + 160},\fscx{ep}\fscy{ep})\1c&H000000&\bord0\shad0}}{linha}")
            continue
        tremida = (rf"\t({d + 150},{d + 185},\frz{2.5 if i % 2 else -2.5})"
                   rf"\t({d + 185},{d + 225},\frz{-1.5 if i % 2 else 1.5})\t({d + 225},{d + 260},\frz0)")
        ev.append(rf"Dialogue: 2,{_ts(0.02)},{_ts(fim)},TitG,,0,0,0,,"
                  rf"{{\an5\q2\pos({cx},{yt})\fscx{int(ep * 1.45)}\fscy{int(ep * 1.45)}\alpha&HFF&\t({d},{d + 70},\alpha&H00&)"
                  rf"\t({d},{d + 150},\fscx{ep}\fscy{ep}){tremida}\t({sai - 160},{sai},\alpha&HFF&)}}{linha}")
    return ev


def escolher_emojis(projeto, blocos):
    """No máximo 1 emoji por tópico, só em bloco com palavra destacada (ou o emoji que o roteiro sugeriu)."""
    if not projeto.get("emoji" if L["emoji"] else "emoji_h", L["emoji"]) or not os.path.exists(EMOJI_FONTE):
        return []
    eventos, usados_cena = [], set()
    n_topicos = len({b["cena"] for b in blocos})
    limite = max(1, round(n_topicos * 0.5))
    ja_usados = set()
    for b in blocos:
        c = b["cena"]
        if c in usados_cena or len(eventos) >= limite or not b.get("destaque"):
            continue
        # o emoji sugerido pelo roteiro entra no 1º bloco com destaque do tópico; senão, pela palavra destacada
        emo = str(projeto["cenas"][c].get("emoji") or "").strip()[:4] or None
        if not emo:
            for palavra in b["palavras_destaque"]:
                for raizes, e in EMOJI_PALAVRAS:
                    if any(palavra.startswith(r) for r in raizes):
                        emo = e
                        break
                if emo:
                    break
        if emo and emo not in ja_usados:
            ja_usados.add(emo)
            usados_cena.add(c)
            eventos.append({"ini": b["ini"], "fim": max(b["fim"], b["ini"] + 0.7), "emoji": emo})
    return eventos


def emoji_png(emo, destino, tam=240):
    from PIL import Image, ImageDraw, ImageFont
    fonte = ImageFont.truetype(EMOJI_FONTE, 109)
    img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((8, 8), emo, font=fonte, embedded_color=True)
    caixa = img.getbbox()
    if not caixa:
        return None
    img = img.crop(caixa)
    lado = max(img.size)
    quad = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    quad.paste(img, ((lado - img.size[0]) // 2, (lado - img.size[1]) // 2))
    quad.resize((tam, tam), Image.LANCZOS).save(destino)
    return destino


# ---------------------------------------------------------------- zoom de ênfase

def expr_zoom_enfase(tempos, amp=0.05):
    """Expressão de zoom: entra rápido (0,08 s), segura ~0,45 s e volta suave (0,3 s) em cada tempo."""
    partes = []
    for a in tempos:
        partes.append(
            f"if(between(t\\,{a:.3f}\\,{a + 0.83:.3f})\\,"
            f"if(lt(t-{a:.3f}\\,0.08)\\,(t-{a:.3f})/0.08\\,if(lt(t-{a:.3f}\\,0.53)\\,1\\,1-(t-{a + 0.53:.3f})/0.3))\\,0)")
    if not partes:
        return None
    return f"1+{amp}*min(1\\,{'+'.join(partes)})"


# ---------------------------------------------------------------- render

def blocos_com_destaque(projeto):
    """Blocos da legenda com a marcação de destaque (para zoom/pop), sem gerar o ASS."""
    marcadas = {id(projeto["palavras"][i]) for i in marcar_destaques(projeto)}
    blocos = blocos_legenda(projeto)
    for b in blocos:
        b["palavras_destaque"] = [norm(w["legenda"]) for w in b["pal"] if id(w) in marcadas]
        b["destaque"] = bool(b["palavras_destaque"])
    return blocos


def legendar(projeto, limpo, pasta_trab, destino, pasta_fontes, corte_gancho=None, total=None, base=None):
    """Queima legenda, chamada do gancho e emojis sobre o vídeo limpo (o áudio é copiado).

    É o único passo de um ajuste "só de legenda": não refaz cenas, voz nem efeitos.
    """
    definir_formato(projeto)
    if precisa_respiro(projeto) and "respiro_cortes" not in projeto:
        projeto = dict(com_respiro(projeto)[0], timeline=projeto.get("timeline"))  # só legenda: mesmos tempos do vídeo
    pasta_trab = os.path.abspath(pasta_trab)
    os.makedirs(pasta_trab, exist_ok=True)
    tl = projeto.get("timeline") or []
    if total is None:
        total = float(projeto.get("duracao") or (projeto["duracao_voz"] + 0.6))
    if corte_gancho is None:
        corte_gancho = tl[1]["inicio"] if len(tl) > 1 else total
    ass = os.path.join(pasta_trab, "legenda.ass")
    blocos, y_leg = legenda_ass(projeto, ass, corte_gancho)
    # base = (entradas, grafo, rótulo do vídeo, rótulo do áudio) do render: tudo numa passada só (YouTube),
    # sem gravar o vídeo limpo intermediário — um encode inteiro a menos
    if base:
        entradas, grafo, v = list(base[0]), list(base[1]), base[2]
    else:
        entradas, grafo, v = ["-i", limpo], [], "[0:v]"
    grafo.append(f"{v}subtitles={ass}:fontsdir={pasta_fontes}[vs]")
    v = "[vs]"
    tit_ate = fim_titulo(projeto, corte_gancho) or 0
    for i, e in enumerate(escolher_emojis(projeto, blocos)):
        if e["ini"] < tit_ate + 0.1:
            continue  # nunca por cima da chamada do gancho
        png = emoji_png(e["emoji"], os.path.join(pasta_trab, f"emoji{i}.png"))
        if not png:
            continue
        entradas += ["-i", png]
        idx = entradas.count("-i") - 1
        a0, a1 = e["ini"], e["fim"]
        y_emo = y_leg - int(285 * L["leg_escala"])
        grafo.append(f"{v}[{idx}:v]overlay=x=(W-w)/2:y='{y_emo}-36*max(0\\,1-(t-{a0:.3f})/0.16)':"
                     f"enable='between(t\\,{a0:.3f}\\,{a1:.3f})'[ve{i}]")
        v = f"[ve{i}]"
    # tela final do YouTube: depois da última fala, a montagem fica desfocada e escura (os cartões do YouTube
    # aparecem por cima, à esquerda e à direita) e a logo do canal entra pequena no alto
    if L["W"] > L["H"]:
        cauda_v = float(projeto.get("cauda", CAUDA_YT) or 0)
        te = total - cauda_v + 0.4
        if cauda_v >= 5 and te > 5:
            grafo.append(f"{v}boxblur=10:1:enable='gte(t\\,{te:.3f})',"
                         f"eq=brightness=-0.22:saturation=0.8:enable='gte(t\\,{te:.3f})'[vte]")
            v = "[vte]"
            logo_f = logo_do_canal(dict(projeto, marca_logo=True)) or ""
            if logo_f:
                entradas += ["-loop", "1", "-t", f"{cauda_v + 1:.2f}", "-i", logo_f]
                idx = entradas.count("-i") - 1
                grafo.append(f"[{idx}:v]format=rgba,scale=300:-1,fade=t=in:st=0:d=0.5:alpha=1,"
                             f"setpts=PTS+{te + 0.2:.3f}/TB[lgf]")
                grafo.append(f"{v}[lgf]overlay=x=(W-w)/2:y=70:eof_action=pass:enable='gte(t\\,{te + 0.2:.3f})'[vtf]")
                v = "[vtf]"
    # logo grande do canal no centro quando a narração fala o nome dele (YouTube)
    if L["W"] > L["H"] and projeto.get("logo_anim", True):
        import cta as _cta
        for i, (t0, png) in enumerate(_cta.momentos_canal(projeto)):
            padrao, dur_l = _cta.gerar_logo(png, os.path.join(pasta_trab, f"logo_anim{i}"), largura=W)
            entradas += ["-framerate", str(FPS), "-i", padrao]
            idx = entradas.count("-i") - 1
            grafo.append(f"[{idx}:v]format=rgba,setpts=PTS+{t0:.3f}/TB[lga{i}]")
            grafo.append(f"{v}[lga{i}]overlay=x=(W-w)/2:y=(H-h)/2:eof_action=pass:"
                         f"enable='between(t\\,{t0:.3f}\\,{t0 + dur_l:.3f})'[vlga{i}]")
            v = f"[vlga{i}]"
    # animação de like / inscrever-se / sininho quando a narração pede (YouTube)
    if projeto.get("cta_anim", L["W"] > L["H"]):
        import cta
        tempos_cta = cta.momentos_todos(projeto)
        if tempos_cta:
            padrao, dur_cta, _ = cta.gerar(os.path.join(pasta_trab, "cta"), largura=W if W > H else 1700)
            for i, t0 in enumerate(tempos_cta):
                entradas += ["-framerate", str(FPS), "-i", padrao]
                idx = entradas.count("-i") - 1
                y_cta = int(H * (0.60 if W > H else 0.70))
                grafo.append(f"[{idx}:v]format=rgba,setpts=PTS+{t0:.3f}/TB[cta{i}]")
                grafo.append(f"{v}[cta{i}]overlay=x=(W-w)/2:y={y_cta}:eof_action=pass:"
                             f"enable='between(t\\,{t0:.3f}\\,{t0 + dur_cta:.3f})'[vc{i}]")
                v = f"[vc{i}]"
    # YouTube: número de impacto falado vira cartão com o número contando + gráfico (canto superior direito)
    if L["W"] > L["H"] and projeto.get("graficos", True):
        import grafico
        for i, (t0, num) in enumerate(grafico.momentos(projeto, tl, total)):
            try:
                g = grafico.gerar(num, os.path.join(pasta_trab, f"grafico{i}"), pasta_fontes, largura=W)
            except Exception as e:
                print(f"aviso: gráfico do número {num.get('valor')} não gerado: {e}", flush=True)
                g = None
            if not g:
                continue
            padrao, dur_g, (gw, _) = g
            entradas += ["-framerate", str(FPS), "-i", padrao]
            idx = entradas.count("-i") - 1
            grafo.append(f"[{idx}:v]format=rgba,setpts=PTS+{t0:.3f}/TB[gr{i}]")
            grafo.append(f"{v}[gr{i}]overlay=x=W-w-{int(30 * W / 1920)}:y={int(40 * H / 1080)}:eof_action=pass:"
                         f"enable='between(t\\,{t0:.3f}\\,{t0 + dur_g:.3f})'[vgr{i}]")
            v = f"[vgr{i}]"
    # YouTube (fórmula dos tops): ficha do jogo no canto inferior esquerdo quando o capítulo dele começa
    if e_corrido(projeto):
        for i, (t0, png) in enumerate(fichas_png(projeto, tl, pasta_fontes, pasta_trab)):
            entradas += ["-loop", "1", "-t", f"{FICHA_DUR + 0.5:.2f}", "-i", png]
            idx = entradas.count("-i") - 1
            grafo.append(f"[{idx}:v]format=rgba,fade=t=out:st={FICHA_DUR - 0.35:.2f}:d=0.35:alpha=1,"
                         f"setpts=PTS+{t0:.3f}/TB[fc{i}]")
            x = f"'if(lt(t-{t0:.3f}\\,0.3)\\,-w+(w+44)*(t-{t0:.3f})/0.3\\,44)'"
            grafo.append(f"{v}[fc{i}]overlay=x={x}:y=H-h-44:eof_action=pass:"
                         f"enable='between(t\\,{t0:.3f}\\,{t0 + FICHA_DUR:.3f})'[vf{i}]")
            v = f"[vf{i}]"
    # logo do canal (semitransparente, canto inferior direito) no lugar do @ em texto
    logo = logo_do_canal(projeto)
    if logo:
        entradas += ["-loop", "1", "-i", logo]
        idx = entradas.count("-i") - 1
        larg = 170 if W > H else 150
        grafo.append(f"[{idx}:v]format=rgba,scale={larg}:-1,colorchannelmixer=aa=0.62[logo]")
        y_logo = "H-h-34" if W > H else f"{int(H * 0.845)}-h/2"
        x_logo = "W-w-40" if W > H else "(W-w)/2"
        grafo.append(f"{v}[logo]overlay=x={x_logo}:y={y_logo}:shortest=1[vlg]")
        v = "[vlg]"
    grafo.append(f"{v}fade=t=in:st=0:d=0.15[v]")
    audio = ["-map", base[3], "-c:a", "aac", "-b:a", "192k", "-ar", 48000] if base else ["-map", "0:a", "-c:a", "copy"]
    ffmpeg(*entradas, "-filter_complex", ";".join(grafo), "-map", "[v]", *audio, "-t", f"{total:.2f}",
           "-c:v", "libx264", "-preset", "veryfast" if base else "faster", "-crf", 19 if base else 20,
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", destino)


FICHA_DUR = 6.0
LOGOS = {"jogandosemplano": os.path.join(os.path.dirname(os.path.abspath(__file__)), "marca", "jogandosemplano.png")}


def logo_do_canal(projeto):
    """Logo do canal quando a marca d'água é de um canal com logo cadastrada (só no YouTube)."""
    chave = re.sub(r"[^a-z0-9]", "", str(projeto.get("marca_dagua") or "").lower())
    arq = LOGOS.get(chave)
    return arq if arq and os.path.exists(arq) and L["W"] > L["H"] and projeto.get("marca_logo", True) else None


def tempos_ficha(projeto, timeline):
    """Instante em que cada ficha entra (logo depois do mergulho no preto do capítulo)."""
    inicio = {t["cena"]: t["inicio"] for t in (timeline or [])}
    return [inicio[k] + 0.5 for k in cenas_de_capitulo(projeto)
            if k in inicio and isinstance(projeto["cenas"][k].get("ficha"), dict)]


def fichas_png(projeto, timeline, pasta_fontes, pasta_trab):
    inicio = {t["cena"]: t["inicio"] for t in (timeline or [])}
    saida = []
    for k in cenas_de_capitulo(projeto):
        f = projeto["cenas"][k].get("ficha")
        if not isinstance(f, dict) or k not in inicio:
            continue
        titulo = cartoes.titulo_da_ficha(projeto["cenas"][k], f)
        if not titulo:
            continue
        png = os.path.join(pasta_trab, f"ficha{k}.png")
        try:
            cartoes.ficha(titulo, cartoes.linhas_da_ficha(f), pasta_fontes, png, escala=1.45 * W / 1920)
            saida.append((inicio[k] + 0.5, png))
        except Exception as e:
            print(f"aviso: ficha do tópico {k} não gerada: {e}", flush=True)
    return saida


def conferir_video(arquivo, total):
    """Procura imagem congelada (>1,2 s parada) e tela preta no vídeo sem legenda. Devolve [{t, tipo, dur}]."""
    r = run(["ffmpeg", "-hide_banner", "-i", arquivo, "-vf",
             "freezedetect=n=-55dB:d=1.2,blackdetect=d=0.6:pix_th=0.03:pic_th=0.99", "-an", "-f", "null", "-"])
    avisos = []
    ini = None
    for ln in r.stderr.splitlines():
        m = re.search(r"freeze_start: ([\d.]+)", ln)
        if m:
            ini = float(m.group(1))
        m = re.search(r"freeze_end: ([\d.]+)", ln)
        if m and ini is not None:
            avisos.append({"t": round(ini, 2), "tipo": "congelada", "dur": round(float(m.group(1)) - ini, 2)})
            ini = None
        m = re.search(r"black_start:([\d.]+) black_end:([\d.]+)", ln)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            avisos.append({"t": round(a, 2), "tipo": "preta", "dur": round(b - a, 2)})
    if ini is not None and total - ini > 1.2:
        avisos.append({"t": round(ini, 2), "tipo": "congelada", "dur": round(total - ini, 2)})
    # o fim do vídeo (meio segundo de folga depois da última fala) pode ficar parado
    avisos = [a for a in avisos if not (a["tipo"] == "congelada" and a["t"] > total - 1.0)]
    if avisos:
        print(f"conferência da imagem: {avisos}", flush=True)
    return avisos


def renderizar(projeto, banco, pasta_raw, voz_wav, pasta_trab, destino, pasta_fontes, progresso=None):
    definir_formato(projeto)
    MOLDURA["ativa"] = L["H"] > L["W"] and projeto.get("enquadramento", "cheio") == "moldura"
    pasta_trab = os.path.abspath(pasta_trab)
    seg_dir = os.path.join(pasta_trab, "seg")
    shutil.rmtree(seg_dir, ignore_errors=True)
    os.makedirs(seg_dir)
    orig = projeto
    corrido = e_corrido(projeto)
    if precisa_respiro(projeto):
        # fórmula dos tops: respiro antes de cada jogo; e pausas pedidas com o som do jogo (a voz ganha silêncio)
        projeto, cortes_r = com_respiro(projeto)
        voz_wav = voz_com_respiro(voz_wav, cortes_r, os.path.join(pasta_trab, "voz_respiro.wav"))
    faixas, total, tipos = linha_tempo(projeto)
    trans_cena = {} if corrido else transicoes(projeto, faixas)
    lista, k = [], 0
    usados_cena = []
    # pouco material (jogo novo com 1 trailer só): cortes um pouco mais longos em vez de repetir trecho.
    # Com material de sobra continua o ritmo normal (CUT).
    uso = {}
    tarefas = []
    distintos = max(len(banco), 1)
    corte = min(max(CUT, total / distintos * 0.85), CUT * 1.7)
    if corte > CUT + 0.05 and not corrido:
        print(f"pouco material ({distintos} mídias para {total:.0f}s): cortes de {corte:.1f}s", flush=True)
    diag = []
    if corrido:
        tarefas, lista, usados_cena = montar_corrido(projeto, banco, faixas, seg_dir, pasta_raw)
        tarefas = [(t[0], t[1], t[2], os.path.join(pasta_raw, t[2]["arquivo"]), *t[4:]) for t in tarefas]
    for fi, (c, a, b) in enumerate([] if corrido else faixas):
        dur = b - a
        pz = pausa_da_cena(projeto["cenas"][c]) if W > H else 0.0
        final_m = 0.0
        if W > H and fi == len(faixas) - 1:
            # encerramento do YouTube (depois da última fala): montagem rápida dos jogos do vídeo
            final_m = float(projeto.get("cauda", CAUDA_YT) or 0)
            if dur > final_m + 1.0:
                dur -= final_m
            else:
                final_m = 0.0
        if pz and dur > pz + 1.0:
            dur -= pz  # a pausa vira UM trecho contínuo do jogo no fim (com o som dele), sem picotar
        else:
            pz = 0.0
        n = max(1, round(dur / corte))
        if f"cartao_cap_{c}" in banco:
            n = max(n, math.ceil(dur / 2.6))  # a cartela de capítulo fica ~2,5 s; depois volta o jogo
        midias = [m for m in projeto["cenas"][c].get("midias", []) if m in banco] or list(banco)[:1]
        tr = trans_cena.get(c)
        usados_cena.append({"cena": c, "inicio": round(a, 2), "fim": round(b, 2), "cortes": [],
                            "entrada": tipos.get(c, "inicio"), "transicao": tr or "inicio"})
        # cartela de número: entra no corte em que o número é falado
        fixo = {}
        if f"cartao_cap_{c}" in banco:
            fixo[0] = f"cartao_cap_{c}"
        # foto real (Commons) do tópico: um corte só, quando o termo é falado (senão no 2º corte)
        for fid in (projeto["cenas"][c].get("fotos") or [])[:1]:
            if fid in banco and n >= 2:
                quando = cartoes.momento_do_nome(projeto, projeto["cenas"][c].get("imagem") or "", cena=c)
                jf = 1 if not quando else min(max(int((quando[1] - a) / (dur / n)), 0), n - 1)
                fixo.setdefault(jf, fid)
        # personagem citado (vídeo/arte oficial dele): entra no corte em que o nome é falado; um 2º trecho logo depois
        pers_c = projeto["cenas"][c].get("personagens") or []
        for pn in pers_c:
            meus = sorted((x for x, mm in banco.items() if mm.get("personagem") == pn),
                          key=lambda x: (banco[x]["tipo"] != "i", uso.get(x, 0)))
            if not meus:
                continue
            quando = cartoes.momento_do_nome(projeto, pn, cena=c)
            if not quando:  # o Whisper às vezes come metade do nome ("de Shiranui"): tenta a palavra mais marcante
                for tk in sorted(re.findall(r"[a-z0-9]+", norm_txt(pn)), key=len, reverse=True)[:2]:
                    if len(tk) >= 4:
                        quando = cartoes.momento_do_nome(projeto, tk, cena=c)
                        if quando:
                            break
            jp = 0 if not quando else min(max(int((quando[1] - a) / (dur / n)), 0), n - 1)
            while jp in fixo and jp + 1 < n:
                jp += 1
            fixo.setdefault(jp, meus[0])
            if jp + 1 < n:  # fica na tela mais um corte (outro material dele, ou a mesma arte com outro movimento)
                fixo.setdefault(jp + 1, meus[1] if len(meus) > 1 else meus[0])
        for cid, cm in banco.items():
            if cm.get("cartao") == "capa":
                # capa do jogo entra no corte em que o NOME dele é falado pela 1ª vez (fora da chamada do gancho)
                quando = cartoes.momento_do_nome(projeto, cm.get("nome", ""))
                if quando and quando[0] == c and quando[1] > (fim_titulo(projeto, faixas[1][1] if len(faixas) > 1 else total) or 0) + 0.1:
                    fixo[min(max(int((quando[1] - a) / (dur / n)), 0), n - 1)] = cid
                continue
            if cm.get("cartao") == "numero" and cm.get("cena") == c and n >= 2:
                t_num = cartoes.momento_do_numero(projeto, c, cm.get("valor"))
                jn = 1 if t_num is None else min(max(int((t_num - a) / (dur / n)), 0), n - 1)
                fixo[jn] = cid
        for j in range(n):
            if j == 0:
                mid = midias[0]
            else:
                # o trecho MENOS usado no vídeo até aqui (não repete enquanto houver inédito); cartão não volta
                giro = [x for x in midias if not (banco[x].get("cartao") and uso.get(x))
                        and (not banco[x].get("personagem") or banco[x]["personagem"] in pers_c)] or midias
                # todo material do mesmo jogo vale (não só os 6 sugeridos para o tópico) — regra do jogo certo mantida
                jogo_c = projeto["cenas"][c].get("jogo")
                extras = [x for x, mm in banco.items() if jogo_c and mm.get("jogo") == jogo_c and x not in giro
                          and not mm.get("cartao") and not mm.get("capa")
                          and (not mm.get("personagem") or mm["personagem"] in pers_c)]
                ultimo_mid = usados_cena[-1]["cortes"][-1] if usados_cena[-1]["cortes"] else None
                ordem = {x: i for i, x in enumerate(giro + extras)}
                mid = min(giro + extras, key=lambda x: (uso.get(x, 0), x == ultimo_mid, ordem[x]))
            mid = fixo.get(j) or mid
            uso[mid] = uso.get(mid, 0) + 1
            m = banco[mid]
            rep = uso[mid] - 1  # reaproveitado: mostra outra parte do trecho
            d = dur / n
            out = os.path.join(seg_dir, f"{k:04d}.mp4")
            fx = None
            if j == 0 and tr:
                fx = {"pan_leak": "leak"}.get(tr, tr) if tr in ("flash", "zoom", "glitch", "leak", "pan_leak") else None
            src = os.path.join(pasta_raw, m["arquivo"])
            usados_cena[-1]["cortes"].append(mid)
            # as decisões são sequenciais (uso de cada trecho); a codificação roda depois, 3 por vez
            tarefas.append((k, mid, m, src, d, out, fx, rep, midias))
            lista.append(out)
            k += 1
        if pz:
            jogo_c = projeto["cenas"][c].get("jogo")
            vids_c = [x for x, mm in banco.items() if mm["tipo"] == "v" and mm.get("jogo") == jogo_c and not mm.get("cartao")]
            escolhido = projeto["cenas"][c].get("pausa_midia")
            if escolhido in banco and banco[escolhido]["tipo"] == "v":
                vids_c = [escolhido]  # trecho escolhido a dedo (ex.: o que mostra a música/criatura citada)
            if vids_c:
                mid = max(vids_c, key=lambda x: (banco[x].get("dur", 0), -uso.get(x, 0)))
                m = dict(banco[mid], som=True)
                if escolhido == mid and projeto["cenas"][c].get("pausa_t") is not None:
                    m["t"] = float(projeto["cenas"][c]["pausa_t"])  # instante exato do trailer (ex.: a música)
                out = os.path.join(seg_dir, f"{k:04d}.mp4")
                tarefas.append((k, mid, m, os.path.join(pasta_raw, m["arquivo"]), pz, out, None, 0, [mid]))
                usados_cena[-1]["cortes"].append(mid)
                lista.append(out)
                k += 1
            else:  # sem vídeo do jogo: o último trecho se estende
                t_ult = tarefas[-1]
                tarefas[-1] = (*t_ult[:4], t_ult[4] + pz, *t_ult[5:])
        if final_m:
            todos = [x for x, mm in banco.items() if mm["tipo"] == "v" and not mm.get("cartao") and not mm.get("personagem")
                     and mm.get("jogo") not in ("materia", "commons")]
            por_jogo = {}
            for x in sorted(todos, key=lambda x: (-banco[x].get("mov", 0), uso.get(x, 0))):
                por_jogo.setdefault(banco[x]["jogo"], []).append(x)
            fila_f = []
            while any(por_jogo.values()):  # intercala os jogos: um plano de cada
                for jg in list(por_jogo):
                    if por_jogo[jg]:
                        fila_f.append(por_jogo[jg].pop(0))
            t_acum, g2 = 0.0, 0
            while fila_f and t_acum < final_m - 0.05:
                d = min(CORTE_FINAL, final_m - t_acum)
                if final_m - t_acum - d < 0.8:
                    d = final_m - t_acum
                mid = fila_f[g2 % len(fila_f)]
                g2 += 1
                out = os.path.join(seg_dir, f"{k:04d}.mp4")
                tarefas.append((k, mid, banco[mid], os.path.join(pasta_raw, banco[mid]["arquivo"]), d, out, None,
                                uso.get(mid, 0), [mid]))
                uso[mid] = uso.get(mid, 0) + 1
                usados_cena[-1]["cortes"].append(mid)
                lista.append(out)
                k += 1
                t_acum += d
            if not fila_f:  # sem vídeo nenhum: o último trecho se estende
                t_ult = tarefas[-1]
                tarefas[-1] = (*t_ult[:4], t_ult[4] + final_m, *t_ult[5:])
        if progresso:
            progresso(fi + 1, len(faixas) * 2)

    # durações no grid de quadros (acumulado): cada trecho sai com o número exato de quadros e a soma bate com o
    # total — a junção sem reencodar funciona (antes sobrava ~0,6 s em 10 min: reencodava o vídeo inteiro de novo)
    acum, novas = 0.0, []
    for tf in tarefas:
        ini_f = round(acum * FPS)
        acum += tf[4]
        d_q = max(1, round(acum * FPS) - ini_f) / FPS
        novas.append((*tf[:4], d_q, *tf[5:]))
    tarefas = novas
    # mapa (início, fim, mídia) do vídeo final: a revisão automática confere se cada trecho é do jogo certo
    mapa, ac = [], 0.0
    for tf in tarefas:
        mapa.append((round(ac, 2), round(ac + tf[4], 2), tf[1], bool(tf[2].get("mudo"))))
        ac += tf[4]

    def codificar(tf):
        k, mid, m, src, d, out, fx, rep, midias = tf
        if m.get("corrido"):
            seg_corrido(src, m.get("t", 0), d, out, m.get("fi"), m.get("fo"), m.get("marca"), m.get("area"))
        elif corrido and m["tipo"] == "i":
            seg_imagem(src, d, out, k, None, encaixar=True)
        elif m.get("cartao"):
            seg_cartao(src, d, out, fx)
        elif m["tipo"] == "v" and m.get("proprio"):
            seg_video_proprio(src, m.get("t", 0) + rep * 2.5, d, out, fx)
        elif m["tipo"] == "v":
            seg_video(src, m["t"] + rep * 2.5, d, out, k % 2 == 0, fx)
        else:
            seg_imagem(src, d, out, k, fx, encaixar=bool(m.get("proprio") or m.get("encaixar")), pre=_recorte(m))
        return tf

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        for i, tf in enumerate(ex.map(codificar, tarefas), 1):
            if progresso and i % 10 == 0:
                progresso(len(faixas) + round(len(faixas) * i / len(tarefas)), len(faixas) * 2)
    for k, mid, m, src, d, out, fx, rep, midias in tarefas:
        if True:
            ds = duracao(out)
            if abs(ds - d) > 0.08:
                print(f"aviso: segmento {k} ({mid}) saiu com {ds:.2f}s em vez de {d:.2f}s", flush=True)
                diag.append({"seg": k, "midia": mid, "esperado": round(d, 2), "saiu": round(ds, 2),
                             "detalhe": _ultimo.pop("seg", None) if m["tipo"] == "v" else None})
                if m["tipo"] == "v":
                    # trecho de vídeo que não decodifica direito: usa um quadro dele como imagem (Ken Burns)
                    quadro = os.path.join(seg_dir, f"{k:04d}_q.jpg")
                    try:
                        ffmpeg("-ss", f"{m.get('t', 0) + 0.5:.2f}", "-i", src, "-frames:v", 1, "-q:v", 2, quadro)
                        seg_imagem(quadro, d, out, k, fx)
                    except Exception:
                        seg_imagem(os.path.join(pasta_raw, next(
                            (banco[x]["arquivo"] for x in midias if banco[x]["tipo"] == "i"), m["arquivo"])), d, out, k, fx)
                    diag[-1]["refeito"] = round(duracao(out), 2)
    lst = os.path.join(pasta_trab, "lista.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{p}'\n" for p in lista)
    visual = os.path.join(pasta_trab, "visual.mp4")
    ffmpeg("-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", visual)
    dv = duracao(visual)
    if dv < total - 0.2:
        # junção sem reencodar perdeu tempo (base de tempo diferente em algum segmento): junta reencodando
        print(f"aviso: junção rápida deu {dv:.2f}s para {total:.2f}s; juntando de novo reencodando", flush=True)
        try:
            curtos = [(os.path.basename(t[5]), t[1], round(t[4], 3), round(duracao(t[5]), 3)) for t in tarefas
                      if abs(duracao(t[5]) - t[4]) > 0.02]
            print(f"trechos com duração diferente: {len(curtos)} {curtos[:8]}", flush=True)
        except Exception:
            pass
        diag.append({"juncao_rapida": round(dv, 2), "total": round(total, 2)})
        refeito = os.path.join(pasta_trab, "visual_reencodado.mp4")
        ffmpeg("-f", "concat", "-safe", 0, "-i", lst, "-vf", f"fps={FPS},setpts=N/({FPS}*TB)", "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", refeito)
        visual = refeito
        dv = duracao(visual)
    if dv < total - 0.2:
        # rede de segurança: nunca deixar a imagem acabar antes da voz
        print(f"aviso: imagem com {dv:.2f}s para {total:.2f}s de voz; completando com o último quadro", flush=True)
        completo = os.path.join(pasta_trab, "visual_completo.mp4")
        ffmpeg("-i", visual, "-vf", f"tpad=stop_mode=clone:stop_duration={total - dv + 0.5:.3f}", "-t", f"{total:.3f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", completo)
        visual = completo

    tem_som = (corrido or any(t[2].get("som") for t in tarefas)) and projeto.get("som_original", True) is not False
    som_jogo = audio_corrido(tarefas, pasta_raw, pasta_trab) if tem_som else None
    corte_gancho = faixas[1][1] if len(faixas) > 1 else total
    blocos = blocos_com_destaque(projeto)
    cortes = {c: a for c, a, b in faixas[1:]}

    entradas = ["-i", visual, "-i", voz_wav]
    grafo, v = [], "[0:v]"

    def nova_entrada(arquivo):
        entradas.extend(["-i", arquivo])
        return entradas.count("-i") - 1

    # pans (whip pan) — vários cortes possíveis, encadeados com xfade
    pans = sorted((cortes[c], "slideleft" if t in ("pan_dir", "pan_leak") else "slideright")
                  for c, t in trans_cena.items() if t in ("pan_dir", "pan_esq", "pan_leak"))
    if pans:
        meio = PAN_DUR / 2
        limites = [0.0] + [p for p, _ in pans] + [None]
        grafo.append(f"{v}split={len(pans) + 1}" + "".join(f"[ps{i}]" for i in range(len(pans) + 1)))
        for i in range(len(pans) + 1):
            ini, fim = limites[i], limites[i + 1]
            trim = f"trim=start={ini:.3f}" + (f":end={fim:.3f}" if fim is not None else "")
            pad = ""
            if i > 0:
                pad += f",tpad=start_mode=clone:start_duration={meio:.3f}"
            if fim is not None:
                pad += f",tpad=stop_mode=clone:stop_duration={meio:.3f}"
            grafo.append(f"[ps{i}]{trim},setpts=PTS-STARTPTS{pad}[pp{i}]")
        acc = "[pp0]"
        for i, (p, direcao) in enumerate(pans):
            saida = f"[px{i}]"
            grafo.append(f"{acc}[pp{i + 1}]xfade=transition={direcao}:duration={PAN_DUR:.3f}:offset={p - meio:.3f}{saida}")
            acc = saida
        janelas = "+".join(f"between(t\\,{p - meio:.3f}\\,{p + meio:.3f})" for p, _ in pans)
        grafo.append(f"{acc}gblur=sigma=40:sigmaV=0.01:enable='{janelas}'[vp]")
        v = "[vp]"

    # light leak
    leaks = [cortes[c] for c, t in trans_cena.items() if t in ("leak", "pan_leak")]
    if leaks:
        arq_leak = efeitos.light_leak(os.path.join(pasta_trab, "leak.mp4"))
        for i, t in enumerate(leaks):
            idx = nova_entrada(arq_leak)
            grafo.append(f"[{idx}:v]tpad=start_duration={max(t - LEAK_ANTES, 0.0):.3f}:start_mode=add:color=black,"
                         f"format=gbrp[lk{i}]")
            grafo.append(f"{v}format=gbrp[vb{i}];[vb{i}][lk{i}]blend=all_mode=screen:shortest=0,format=yuv420p[vl{i}]")
            v = f"[vl{i}]"

    # ênfase (zoom + pop) quando aparece palavra destacada: espaçada, longe das trocas de tópico
    tempos_destaque = []
    # YouTube: sem zoom/pop nas palavras destacadas — no vídeo longo o "pop" parece anunciar algo novo na tela
    for b in ([] if W > H else blocos):
        if b.get("destaque") and all(abs(b["ini"] - t) > 0.5 for t in cortes.values()) and b["ini"] > 0.4:
            if not tempos_destaque or b["ini"] - tempos_destaque[-1] >= ESPACO_ENFASE:
                tempos_destaque.append(b["ini"])
    if projeto.get("zoom_enfase", True):
        z = expr_zoom_enfase(tempos_destaque)
        if z:
            grafo.append(f"{v}scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame,crop={W}:{H}[vz]")
            v = "[vz]"

    # chamada do gancho: fundo desfocado e escuro enquanto ela está na tela (o texto salta aos olhos)
    fim_tit = fim_titulo(projeto, corte_gancho)
    if fim_tit and projeto.get("titulo_estilo", "manchete") != "simples":
        grafo.append(f"{v}boxblur=14:2:enable='lt(t,{fim_tit - 0.18:.2f})',"
                     f"boxblur=5:1:enable='between(t,{fim_tit - 0.18:.2f},{fim_tit:.2f})',"
                     f"eq=brightness=-0.16:saturation=0.75:enable='lt(t,{fim_tit:.2f})'[vt]")
        v = "[vt]"

    grafo.append(f"{v}null[vlimpo]")

    # áudio: voz + trilha de efeitos
    a = "[1:a]apad[a]"
    if projeto.get("sfx", True):
        eventos = [(0.0, "impacto", 0.55)]
        som_trans = {"pan_dir": ("woosh", 0.5), "pan_esq": ("woosh", 0.5), "pan_leak": ("woosh", 0.5),
                     "zoom": ("zoom", 0.45), "glitch": ("glitch", 0.35), "flash": ("woosh", 0.3), "leak": ("woosh", 0.25)}
        for c, t in trans_cena.items():
            if t in som_trans:
                nome, ganho = som_trans[t]
                eventos.append((cortes[c], nome, ganho))
        for t in tempos_destaque:
            eventos.append((t, "pop", 0.28))
        if corrido:
            for t in tempos_ficha(projeto, usados_cena):
                eventos.append((t, "woosh", 0.3))
        if W > H and projeto.get("graficos", True):
            import grafico
            for t, _ in grafico.momentos(projeto, usados_cena, total):
                eventos.append((t, "woosh", 0.35))
                eventos.append((t + 0.15 + grafico.CONTA, "pop", 0.4))
        for c, t in cortes.items():
            if f"cartao_cap_{c}" in banco:
                eventos.append((t + 0.05, "impacto", 0.4))
        if W > H and projeto.get("logo_anim", True):
            import cta
            for t0, _ in cta.momentos_canal(projeto):
                eventos.append((t0 + 0.05, "woosh", 0.45))
                eventos.append((t0 + 0.32, "impacto", 0.3))
        if projeto.get("cta_anim", L["W"] > L["H"]):
            import cta
            for t0 in cta.momentos_todos(projeto):
                for dt, nome in [(0.55, "clique"), (1.35, "clique"), (2.15, "clique"), (2.2, "sino")]:
                    eventos.append((t0 + dt, nome, 0.55 if nome == "clique" else 0.35))
        if fim_tit and projeto.get("titulo_estilo", "manchete") != "simples":
            n_linhas = len(_quebra_titulo(str(projeto["titulo_tela"]).upper(), 12))
            for i in range(1, n_linhas):  # cada linha da chamada "bate" com um som
                eventos.append((0.02 + 0.11 * i + 0.12, "pop", 0.32))
        sfx = efeitos.trilha_efeitos(eventos, total, os.path.join(pasta_trab, "efeitos.wav"))
        idx = nova_entrada(sfx)
        a = (f"[1:a]apad[vz0];[{idx}:a]apad[fx0];[vz0][fx0]amix=inputs=2:duration=first:normalize=0,"
             f"alimiter=limit=0.95:level=disabled[a]")
    # som do jogo: some quando a voz fala (a cama fica por baixo) e aparece nos respiros e pausas sem narração
    if som_jogo:
        idx = nova_entrada(som_jogo)
        vol = 10 ** (float(projeto.get("jogo_db", 0)) / 20)
        base = a.rsplit("[a]", 1)[0]
        a = (f"{base}[jm0];[jm0]asplit[jma][jmsc];"
             f"[{idx}:a]aresample=48000,{'loudnorm=I=-20:TP=-2:LRA=11,aresample=48000,' if corrido else ''}volume={vol:.3f}[jg];"
             f"[jg][jmsc]sidechaincompress=threshold=0.008:ratio=20:attack=15:release=450:makeup=1[jgd];"
             f"[jma][jgd]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=disabled[a]")
    # cama sonora (YouTube): música gerada por código, sem direito autoral. Toca o vídeo inteiro, baixinha por
    # baixo da voz (~16 dB abaixo, só clima), e sobe quando o resto silencia (pausas, imagem parada, trecho mudo
    # do trailer). Com trilha da biblioteca, a trilha ocupa esse lugar.
    trilha_ok = bool((projeto.get("trilha") or {}).get("arquivo")) and projeto.get("musica", True) and \
        os.path.exists(os.path.join(pasta_raw, (projeto.get("trilha") or {}).get("arquivo", "")))
    if W > H and projeto.get("cama", True) and not trilha_ok:
        clima = projeto.get("cama_clima") or efeitos.clima_do_video(projeto.get("titulo"), projeto.get("cenas", []))
        arq_cama = efeitos.cama_sonora(clima, os.path.join(pasta_trab, f"cama_{clima}.wav"))
        print(f"cama sonora: clima {clima}", flush=True)
        entradas.extend(["-stream_loop", "-1", "-i", arq_cama])
        ic = entradas.count("-i") - 1
        vol = 10 ** (float(projeto.get("cama_db", -11)) / 20)
        base = a.rsplit("[a]", 1)[0]
        a = (f"{base}[cm0];[cm0]asplit[cma][cmsc];"
             f"[{ic}:a]aresample=48000,volume={vol:.3f},afade=t=in:d=1.5,"
             f"afade=t=out:st={max(total - 2.5, 0):.2f}:d=2.4,atrim=0:{total:.2f}[cam];"
             f"[cam][cmsc]sidechaincompress=threshold=0.03:ratio=2:attack=60:release=900:makeup=1[camd];"
             f"[cma][camd]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=disabled[a]")
    # trilha de fundo (da biblioteca, jogo "Trilha"): baixa sozinha quando a voz fala (sidechain), sobe nas pausas
    trilha = projeto.get("trilha") or {}
    arq_tri = os.path.join(pasta_raw, trilha["arquivo"]) if trilha.get("arquivo") else ""
    if arq_tri and os.path.exists(arq_tri) and projeto.get("musica", True):
        entradas.extend(["-stream_loop", "-1", "-i", arq_tri])
        it = entradas.count("-i") - 1
        vol = 10 ** (float(projeto.get("musica_db", -20)) / 20)
        base = a.rsplit("[a]", 1)[0]  # mesma mistura de antes, sem o rótulo final
        a = (f"{base}[mix0];[mix0]asplit[mixa][mixsc];"
             f"[{it}:a]aresample=48000,volume={vol:.3f},afade=t=in:d=0.6,"
             f"afade=t=out:st={max(total - 1.6, 0):.2f}:d=1.5,atrim=0:{total:.2f}[mus];"
             f"[mus][mixsc]sidechaincompress=threshold=0.02:ratio=8:attack=15:release=350:makeup=1[musd];"
             f"[mixa][musd]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=disabled[a]")
    grafo.append(a)

    # 1) vídeo "limpo": cenas, transições, efeitos e áudio — sem legenda/chamada/emojis.
    #    Fica guardado: ajuste que só mexe na legenda reaproveita ele (render rápido, sem refazer cenas).
    limpo = os.path.join(os.path.dirname(os.path.abspath(destino)), "limpo.mp4")
    if W > H and projeto.get("legenda_estilo") == "nenhuma":
        # YouTube sem legenda: uma passada só (o limpo só servia para ajuste rápido de legenda, que aqui não há)
        if os.path.exists(limpo):
            os.remove(limpo)
        projeto["timeline"] = usados_cena
        legendar(projeto, None, pasta_trab, destino, pasta_fontes, corte_gancho, total,
                 base=(entradas, grafo, "[vlimpo]", "[a]"))
        try:
            projeto["avisos_video"] = conferir_video(destino, total)
        except Exception as e:
            print(f"aviso: conferência da imagem falhou: {e}", flush=True)
        _revisao(projeto, destino, banco, mapa, usados_cena, total)
        return _fim_render(projeto, orig, diag, usados_cena, total)
    ffmpeg(*entradas, "-filter_complex", ";".join(grafo),
           "-map", "[vlimpo]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", 17, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", 48000, "-movflags", "+faststart", limpo)
    # conferência da imagem (antes da legenda, que mexe a cada palavra e esconderia o congelamento)
    try:
        projeto["avisos_video"] = conferir_video(limpo, total)
    except Exception as e:
        print(f"aviso: conferência da imagem falhou: {e}", flush=True)
    # 2) legenda, chamada e emojis por cima
    projeto["timeline"] = usados_cena
    legendar(projeto, limpo, pasta_trab, destino, pasta_fontes, corte_gancho, total)
    _revisao(projeto, destino, banco, mapa, usados_cena, total)
    return _fim_render(projeto, orig, diag, usados_cena, total)


def _revisao(projeto, destino, banco, mapa, timeline, total):
    try:
        import qa
        cauda = float(projeto.get("cauda", CAUDA_YT) or 0) if W > H else 0.0
        projeto["qa"] = qa.revisar(projeto, destino, banco, mapa, timeline, total, cauda)
    except Exception as e:
        print(f"aviso: revisão automática falhou: {e}", flush=True)


def _fim_render(projeto, orig, diag, usados_cena, total):
    if diag:
        projeto["diagnostico_render"] = diag
    else:
        projeto.pop("diagnostico_render", None)
    if projeto is not orig:  # modo corrido trabalhou numa cópia (com respiro): devolve os avisos ao projeto
        for chave in ("avisos_video", "diagnostico_render", "qa"):
            if chave in projeto:
                orig[chave] = projeto[chave]
            else:
                orig.pop(chave, None)
    return usados_cena, total
