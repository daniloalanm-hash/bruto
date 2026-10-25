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


# ---------------------------------------------------------------- logo do canal quando o nome é falado

CANAIS = {("jogando", "sem", "plano"): os.path.join(os.path.dirname(os.path.abspath(__file__)), "marca", "jogandosemplano.png")}
LOGO_DUR = 2.6


def momentos_canal(projeto):
    """[(instante, png)] em que a narração fala o nome do canal ("aqui no Jogando Sem Plano")."""
    ws = sorted((w for w in projeto.get("palavras", []) if w.get("manter") and "ns" in w), key=lambda w: w["ns"])
    lim = [re.sub(r"[^a-z0-9]", "", str(w.get("legenda") or w["w"]).lower()) for w in ws]
    saida = []
    for nome, png in CANAIS.items():
        if not os.path.exists(png):
            continue
        for i in range(len(ws) - len(nome) + 1):
            if tuple(lim[i:i + len(nome)]) == nome and (not saida or ws[i]["ns"] - saida[-1][0] > 60):
                saida.append((max(ws[i]["ns"] - 0.15, 0.0), png))
    return saida


def momentos_todos(projeto):
    """CTA (like/inscrever/sininho): quando a narração pede, e também logo depois da apresentação do canal
    (o nome do canal na abertura vira logo + pedido de inscrição), sem repetir em menos de 45 s."""
    tempos = list(momentos(projeto))
    for t, _ in momentos_canal(projeto):
        t2 = t + LOGO_DUR + 0.1
        if all(abs(t2 - x) > 45 for x in tempos):
            tempos.append(t2)
    return sorted(tempos)[:4]


def gerar_logo(png, pasta, largura=1920):
    """Quadros (PNG com transparência) da logo grande no centro: entra crescendo com um leve giro, respira,
    sai encolhendo e sumindo. Devolve (padrão, duração)."""
    from PIL import Image, ImageFilter
    os.makedirs(pasta, exist_ok=True)
    base = Image.open(png).convert("RGBA")
    alvo_w = int(largura * 0.30)
    alvo = base.resize((alvo_w, int(base.height * alvo_w / base.width)), Image.LANCZOS)
    alvo = alvo.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    W, H = int(alvo_w * 1.5), int(alvo.height * 1.5)
    n = int(LOGO_DUR * FPS)
    for i in range(n):
        t = i / FPS
        if t < 0.35:  # entrada: cresce com "overshoot"
            u = t / 0.35
            esc = 0.4 + 0.75 * (1 - (1 - u) ** 3) - 0.15 * max(0.0, u - 0.7) / 0.3
            alfa, ang = min(1.0, u * 1.6), -8 * (1 - u)
        elif t > LOGO_DUR - 0.35:  # saída: encolhe e some
            u = (LOGO_DUR - t) / 0.35
            esc, alfa, ang = 0.75 + 0.25 * u, u, 0.0
        else:
            esc, alfa, ang = 1.0 + 0.03 * math.sin((t - 0.35) * 3.2), 1.0, 0.0
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        lg = alvo.resize((max(1, int(alvo.width * esc)), max(1, int(alvo.height * esc))), Image.LANCZOS)
        if ang:
            lg = lg.rotate(ang, resample=Image.BICUBIC, expand=True)
        if alfa < 1:
            lg.putalpha(lg.getchannel("A").point(lambda v: int(v * alfa)))
        img.alpha_composite(lg, ((W - lg.width) // 2, (H - lg.height) // 2))
        img.save(os.path.join(pasta, f"{i:03d}.png"))
    return os.path.join(pasta, "%03d.png"), LOGO_DUR
