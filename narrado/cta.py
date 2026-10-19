"""Animação de "deixa o like, se inscreve e ativa o sininho" (estilo YouTube) com clique e sino.

Gera uma sequência PNG com transparência (cursor clicando no like, no botão INSCREVER-SE → INSCRITO e no
sino, que balança) e os momentos dos sons. O render sobrepõe no vídeo no instante em que a narração pede.
"""
import math
import os
import re

FPS = 30
DUR = 3.2
FONTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fontes")
EMOJI_FONTE = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"

GATILHO = re.compile(r"^(inscrev|inscrit|sininho|sino$|like|curt[aei]|curtir|joinha)")


def momentos(projeto, intervalo=45.0, maximo=3):
    """Instantes em que a narração pede like/inscrição/sininho (o 1º de cada pedido, espaçados)."""
    ws = sorted((w for w in projeto.get("palavras", []) if w.get("manter") and "ns" in w), key=lambda w: w["ns"])
    tempos = []
    for w in ws:
        t = re.sub(r"[^a-zà-ÿ]", "", str(w.get("legenda") or w["w"]).lower())
        if GATILHO.match(t) and (not tempos or w["ns"] - tempos[-1] >= intervalo):
            tempos.append(max(w["ns"] - 0.3, 0.0))
            if len(tempos) >= maximo:
                break
    return tempos


def _emoji(emo, tam):
    from PIL import Image, ImageDraw, ImageFont
    if not os.path.exists(EMOJI_FONTE):
        return None
    f = ImageFont.truetype(EMOJI_FONTE, 109)
    img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((8, 8), emo, font=f, embedded_color=True)
    cx = img.getbbox()
    if not cx:
        return None
    img = img.crop(cx)
    return img.resize((tam, int(tam * img.height / img.width)), Image.LANCZOS)


def _cursor(escala=1.0):
    from PIL import Image, ImageDraw
    s = int(54 * escala)
    img = Image.new("RGBA", (s, int(s * 1.5)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(0, 0), (0, s * 1.15), (s * 0.3, s * 0.88), (s * 0.5, s * 1.38), (s * 0.68, s * 1.3), (s * 0.48, s * 0.8),
           (s * 0.86, s * 0.8)]
    d.polygon(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=max(2, s // 14))
    return img


def gerar(pasta, largura=1920):
    """Gera os quadros em pasta/%03d.png (caixa de 900x260 px). Devolve (padrão, dur, sons[(t, nome)])."""
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(pasta, exist_ok=True)
    W, H = 900, 260
    esc = largura / 1920
    f_bt = ImageFont.truetype(os.path.join(FONTES, "Montserrat-Black.ttf"), 46)
    like = _emoji("👍", 92)
    sino = _emoji("🔔", 88)
    cur = _cursor()
    # posições (centro) dos 3 alvos dentro da caixa
    p_like, p_bt, p_sino = (90, 130), (440, 130), (800, 130)
    alvos = [(0.55, p_like), (1.35, p_bt), (2.15, p_sino)]  # (instante do clique, ponto)
    n = int(DUR * FPS)
    for i in range(n):
        t = i / FPS
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # entrada (0-0,25 s: sobe e cresce) e saída (últimos 0,35 s)
        ent = min(t / 0.25, 1.0)
        sai = max(0.0, min((DUR - t) / 0.35, 1.0))
        alfa = ent * sai
        # like: círculo que fica azul depois do clique
        clicou_like = t >= alvos[0][0]
        pul = 1 + 0.25 * max(0.0, 1 - abs(t - alvos[0][0]) / 0.15) if clicou_like else 1
        r = int(70 * pul)
        cor = (6, 95, 212, 255) if clicou_like else (40, 40, 40, 235)
        d.ellipse((p_like[0] - r, p_like[1] - r, p_like[0] + r, p_like[1] + r), fill=cor, outline=(255, 255, 255, 255), width=4)
        if like:
            lk = like.resize((int(like.width * pul * 0.9), int(like.height * pul * 0.9)))
            img.alpha_composite(lk, (p_like[0] - lk.width // 2, p_like[1] - lk.height // 2))
        # botão INSCREVER-SE -> INSCRITO
        inscrito = t >= alvos[1][0]
        txt = "INSCRITO" if inscrito else "INSCREVER-SE"
        bw = 470
        pul_b = 1 + 0.08 * max(0.0, 1 - abs(t - alvos[1][0]) / 0.15) if inscrito else 1
        hw, hh = int(bw * pul_b / 2), int(64 * pul_b)
        d.rounded_rectangle((p_bt[0] - hw, p_bt[1] - hh, p_bt[0] + hw, p_bt[1] + hh), 18,
                            fill=(90, 90, 90, 255) if inscrito else (220, 25, 30, 255))
        d.text(p_bt, txt, font=f_bt, fill=(255, 255, 255, 255), anchor="mm")
        # sino: balança depois do clique
        if sino:
            ang = 0.0
            if t >= alvos[2][0]:
                k = t - alvos[2][0]
                ang = 22 * math.sin(k * 28) * math.exp(-k * 3.2)
            sn = sino.rotate(ang, resample=Image.BICUBIC, expand=True)
            r2 = 70
            d.ellipse((p_sino[0] - r2, p_sino[1] - r2, p_sino[0] + r2, p_sino[1] + r2),
                      fill=(255, 196, 0, 255) if t >= alvos[2][0] else (40, 40, 40, 235), outline=(255, 255, 255, 255), width=4)
            img.alpha_composite(sn, (p_sino[0] - sn.width // 2, p_sino[1] - sn.height // 2))
        # cursor: vai de um alvo ao outro (chega 0,25 s antes do clique), afunda no clique
        trajeto = [((-0.2), (W + 40, H + 60))] + [(c - 0.25, p) for c, p in alvos]
        pos = trajeto[-1][1]
        for (t0, a), (t1, b) in zip(trajeto, trajeto[1:]):
            if t <= t1:
                u = min(max((t - t0) / max(t1 - t0, 1e-3), 0), 1)
                u = u * u * (3 - 2 * u)
                pos = (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
                break
        aperto = any(abs(t - c) < 0.08 for c, _ in alvos)
        cc = cur.resize((int(cur.width * (0.85 if aperto else 1)), int(cur.height * (0.85 if aperto else 1))))
        img.alpha_composite(cc, (int(pos[0] + 12), int(pos[1] + 18)))
        if alfa < 1:
            a = img.getchannel("A").point(lambda v: int(v * alfa))
            img.putalpha(a)
        if esc != 1:
            img = img.resize((int(W * esc), int(H * esc)), Image.LANCZOS)
        img.save(os.path.join(pasta, f"{i:03d}.png"))
    sons = [(alvos[0][0], "clique"), (alvos[1][0], "clique"), (alvos[2][0], "clique"), (alvos[2][0] + 0.05, "sino")]
    return os.path.join(pasta, "%03d.png"), DUR, sons
