"""Thumbnails do YouTube: 3 versões por vídeo longo, para o "Testar e comparar" do YouTube Studio.

Os 3 estilos que mais aparecem nos vídeos de games com muita view no YouTube BR (pesquisa de out/2026):
  1. manchete  — 2 a 4 palavras gigantes (branco + 1 palavra em amarelo), fonte pesada inclinada, contorno preto,
                 a arte do jogo atrás com o lado do texto escurecido ("BIOWARE ACABOU DE VEZ");
  2. trio      — 2 ou 3 jogos lado a lado em fatias inclinadas + frase curta embaixo ("OS MELHORES JOGOS PS5");
  3. épica     — a imagem mais bonita do jogo, tela cheia e tratada, só um selo pequeno (estilo dos tops).
As imagens são o material oficial já baixado (screenshots da Steam, quadros dos trailers); a escolha prioriza
imagem colorida, nítida, com contraste e SEM texto na tela.
"""
import os
import re

W, H = 1280, 720
AMARELO = (255, 214, 0)
BRANCO = (255, 255, 255)


def _fonte(fontes, nome, tam):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(fontes, nome), tam)


# ---------------------------------------------------------------- escolha das imagens

def _nota(img):
    """Colorida + contraste + nitidez, sem ser escura demais (nota maior = thumb melhor)."""
    import numpy as np
    pequeno = img.convert("RGB").resize((320, 180))
    a = np.asarray(pequeno).astype(float)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    cor = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    contraste = lum.std()
    brilho = lum.mean()
    lap = np.abs(4 * lum[1:-1, 1:-1] - lum[:-2, 1:-1] - lum[2:, 1:-1] - lum[1:-1, :-2] - lum[1:-1, 2:]).mean()
    pena_escuro = max(0.0, 55 - brilho) * 1.2 + max(0.0, brilho - 200) * 1.0
    return cor * 1.0 + contraste * 0.8 + lap * 2.0 - pena_escuro


def _tem_texto(caminho):
    try:
        import assets
        return assets._fracao_texto_east(caminho) >= 0.004
    except Exception:
        return False


def _tem_rosto(caminho):
    """Rosto/personagem grande na imagem (Haar do OpenCV): thumb com personagem chama mais."""
    try:
        import cv2
        img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False
        img = cv2.resize(img, (640, int(640 * img.shape[0] / img.shape[1])))
        det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        rostos = det.detectMultiScale(img, 1.1, 6, minSize=(60, 60))
        return len(rostos) > 0
    except Exception:
        return False


def candidatas(projeto, raw, pasta, por_jogo=8):
    """{chave_do_jogo: [(nota, caminho)]} — screenshots + quadros dos trechos de vídeo de cada jogo."""
    from PIL import Image
    from comum import ffmpeg
    os.makedirs(pasta, exist_ok=True)
    banco = projeto.get("banco") or {}
    saida = {}
    for j in projeto.get("jogos") or []:
        ch = j["chave"]
        cands = []
        for mid, m in banco.items():
            if m.get("jogo") != ch or m.get("cartao"):
                continue
            src = os.path.join(raw, m.get("arquivo", ""))
            if not os.path.exists(src):
                continue
            if m["tipo"] == "i":
                cands.append(src)
            elif m["tipo"] == "v" and len([c for c in cands if c.startswith(pasta)]) < por_jogo:
                q = os.path.join(pasta, f"{mid}.jpg")
                if not os.path.exists(q):
                    try:
                        ffmpeg("-ss", f"{float(m.get('t', 0)) + float(m.get('dur', 2)) / 2:.2f}", "-i", src,
                               "-frames:v", 1, "-q:v", 2, "-vf", "scale=1920:-2", q)
                    except Exception:
                        continue
                if os.path.exists(q):
                    cands.append(q)
        notas, com_texto = [], []
        for c in cands[:40]:
            try:
                img = Image.open(c)
                if img.width < 600:
                    continue
                n = _nota(img) + (35 if _tem_rosto(c) else 0)  # personagem na tela segura o clique
                (com_texto if _tem_texto(c) else notas).append((n, c))
            except Exception:
                continue
        notas = notas or [(n - 100, c) for n, c in com_texto]  # imagem com texto só se não houver outra
        if notas:
            saida[ch] = sorted(notas, reverse=True)
    return saida


# ---------------------------------------------------------------- desenho

def _cobrir(img, w, h):
    from PIL import Image
    img = img.convert("RGB")
    esc = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * esc) + 1, int(img.height * esc) + 1), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def _tratar(img, forte=1.0):
    from PIL import ImageEnhance
    img = ImageEnhance.Contrast(img).enhance(1.0 + 0.18 * forte)
    img = ImageEnhance.Color(img).enhance(1.0 + 0.30 * forte)
    return ImageEnhance.Sharpness(img).enhance(1.3)


def _vinheta(img, forca=0.55):
    from PIL import Image, ImageDraw, ImageFilter
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse((-img.width * 0.15, -img.height * 0.2, img.width * 1.15, img.height * 1.2), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(img.width // 8))
    preto = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(img, Image.blend(img, preto, forca), m)


def _degrade_lateral(img, ate=0.62, alfa=225, lado="esq"):
    from PIL import Image
    import numpy as np
    w, h = img.size
    x = np.linspace(0, 1, w)
    if lado == "dir":
        x = x[::-1]
    a = np.clip((ate - x) / ate, 0, 1) ** 1.2 * alfa
    mascara = Image.fromarray(np.tile(a, (h, 1)).astype("uint8"))
    preto = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(preto, img, mascara)


def _quebrar(palavras, fonte, largura, desenho):
    linhas, atual = [], []
    for p in palavras:
        teste = atual + [p]
        if atual and desenho.textlength(" ".join(teste), font=fonte) > largura:
            linhas.append(atual)
            atual = [p]
        else:
            atual = teste
    if atual:
        linhas.append(atual)
    return linhas


def _texto_bloco(texto, destaque, fontes, largura, max_tam=170, min_tam=80, max_linhas=3, alinhar="esq",
                 fonte="Anton-Regular.ttf"):
    """Camada RGBA com o texto grande (contorno preto + sombra), a palavra de destaque em amarelo, inclinado."""
    from PIL import Image, ImageDraw, ImageFilter
    palavras = str(texto).upper().split()
    dest = {d for d in re.findall(r"\w+", str(destaque or "").upper())}
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tam = max_tam
    while True:
        f = _fonte(fontes, fonte, tam)
        linhas = _quebrar(palavras, f, largura, tmp)
        if (len(linhas) <= max_linhas and all(tmp.textlength(" ".join(l), font=f) <= largura for l in linhas)) \
                or tam <= min_tam:
            break
        tam -= 6
    alt = int(tam * 1.02)
    contorno = max(6, tam // 16)
    larg_txt = int(max(tmp.textlength(" ".join(l), font=f) for l in linhas)) + 4 * contorno
    camada = Image.new("RGBA", (larg_txt + 80, alt * len(linhas) + 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    y = 30
    for ln in linhas:
        lw = d.textlength(" ".join(ln), font=f)
        x = 40 + (0 if alinhar == "esq" else (larg_txt - lw) / 2)
        for k, p in enumerate(ln):
            cor = AMARELO if re.sub(r"\W", "", p) in dest else BRANCO
            d.text((x, y), p, font=f, fill=cor, stroke_width=contorno, stroke_fill=(0, 0, 0))
            x += d.textlength(p + " ", font=f)
        y += alt
    sombra = Image.new("RGBA", camada.size, (0, 0, 0, 0))
    sombra.putalpha(camada.getchannel("A").point(lambda v: int(v * 0.7)))
    sombra = sombra.filter(ImageFilter.GaussianBlur(10))
    base = Image.new("RGBA", camada.size, (0, 0, 0, 0))
    base.alpha_composite(sombra, (8, 10))
    base.alpha_composite(camada)
    # inclinação (itálico) de ~8 graus
    k = 0.14
    base = base.transform((base.width + int(base.height * k), base.height), Image.AFFINE,
                          (1, k, -base.height * k, 0, 1, 0), resample=Image.BICUBIC)
    return base


def _selo(texto, fontes, tam=46, cor_fundo=AMARELO, cor_txt=(10, 10, 10)):
    from PIL import Image, ImageDraw
    f = _fonte(fontes, "Anton-Regular.ttf", tam)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    w = int(tmp.textlength(texto, font=f)) + 40
    img = Image.new("RGBA", (w, int(tam * 1.45)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, img.height - 1), 12, fill=cor_fundo + (255,))
    d.text((w // 2, img.height // 2), texto, font=f, fill=cor_txt, anchor="mm")
    return img


def manchete(fundo, texto, destaque, fontes, destino):
    from PIL import Image
    img = _tratar(_cobrir(Image.open(fundo), W, H), 1.0)
    img = _degrade_lateral(_vinheta(img, 0.35), ate=0.66, alfa=215)
    txt = _texto_bloco(texto, destaque, fontes, largura=640, max_tam=175)
    base = img.convert("RGBA")
    y = max(10, (H - txt.height) // 2)
    base.alpha_composite(txt, (18, y))
    base.convert("RGB").save(destino, quality=92)
    return destino


def trio(fundos, texto, destaque, fontes, destino, selo=None):
    from PIL import Image, ImageDraw
    fundos = fundos[:3]
    n = len(fundos)
    base = Image.new("RGB", (W, H), (0, 0, 0))
    incl = 70
    fatia = W / n
    for i, fz in enumerate(fundos):
        img = _tratar(_cobrir(Image.open(fz), int(fatia + incl * 2), H), 0.9)
        m = Image.new("L", (W, H), 0)
        x0, x1 = i * fatia, (i + 1) * fatia
        pts = [(x0 + (incl if i else -incl), 0), (x1 + (incl if i < n - 1 else incl * 2), 0),
               (x1 - (incl if i < n - 1 else -incl * 2), H), (x0 - (incl if i else incl * 2), H)]
        ImageDraw.Draw(m).polygon(pts, fill=255)
        camada = Image.new("RGB", (W, H))
        camada.paste(img, (int(x0 - incl), 0))
        base = Image.composite(camada, base, m)
    d = ImageDraw.Draw(base)
    for i in range(1, n):  # divisórias brancas
        x = i * fatia
        d.line([(x + incl, 0), (x - incl, H)], fill=(255, 255, 255), width=7)
    base = _vinheta(base, 0.4)
    # faixa escura embaixo para o texto
    import numpy as np
    a = np.clip((np.linspace(0, 1, H) - 0.45) / 0.55, 0, 1) ** 1.3 * 230
    mascara = Image.fromarray(np.tile(a[:, None], (1, W)).astype("uint8"))
    base = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), base, mascara).convert("RGBA")
    txt = _texto_bloco(texto, destaque, fontes, largura=1100, max_tam=135, max_linhas=2, alinhar="centro")
    base.alpha_composite(txt, ((W - txt.width) // 2, H - txt.height + 18))
    if selo:
        s = _selo(selo, fontes, 56)
        base.alpha_composite(s, (W - s.width - 26, 24))
    base.convert("RGB").save(destino, quality=92)
    return destino


def epica(fundo, fontes, destino, selo=None):
    from PIL import Image
    img = _vinheta(_tratar(_cobrir(Image.open(fundo), W, H), 1.25), 0.45).convert("RGBA")
    if selo:
        s = _selo(selo, fontes, 52)
        img.alpha_composite(s, (W - s.width - 26, 24))
    img.convert("RGB").save(destino, quality=92)
    return destino


# ---------------------------------------------------------------- textos

def _selo_do_titulo(titulo):
    m = re.search(r"\b(?:top\s*)?(\d{1,3})\s+(?:melhores|piores|jogos|games|maiores|coisas|segredos|curiosidades)",
                  str(titulo), re.I) or re.search(r"\btop\s*(\d{1,3})\b", str(titulo), re.I)
    return f"TOP {m.group(1)}" if m else None


def textos(projeto):
    """3 frases curtas (2 a 4 palavras) para as thumbs: as que a IA escreveu ou, sem elas, tiradas do título."""
    ts = [t for t in (projeto.get("thumbs") or []) if isinstance(t, dict) and t.get("texto")]
    if len(ts) >= 3:
        return ts[:3]
    base = str(projeto.get("titulo_tela") or projeto.get("titulo") or "").strip()
    pal = re.findall(r"[\wÀ-ÿ]+", base)
    curto = " ".join(pal[:4]) or "VOCÊ PRECISA VER"
    extra = [{"texto": curto, "destaque": pal[-1] if pal else ""},
             {"texto": " ".join(pal[:3]) or curto, "destaque": pal[1] if len(pal) > 1 else ""},
             {"texto": curto, "destaque": ""}]
    return (ts + extra)[:3]


def gerar(projeto, raw, fontes, pasta):
    """Gera thumb_1/2/3.jpg em pasta. Devolve a lista de arquivos."""
    import collections
    trab = os.path.join(pasta, "trab", "thumbs")
    cands = candidatas(projeto, raw, trab)
    if not cands:
        return []
    tempo = collections.Counter(c.get("jogo") for c in projeto.get("cenas", []) if c.get("jogo"))
    ordem = sorted(cands, key=lambda ch: -tempo.get(ch, 0))
    principal = ordem[0]
    tx = textos(projeto)
    selo = _selo_do_titulo(projeto.get("titulo") or (projeto.get("post") or {}).get("titulo_yt") or "")
    saidas = []
    melhor = cands[principal][0][1]
    saidas.append(manchete(melhor, tx[0]["texto"], tx[0].get("destaque"), fontes, os.path.join(pasta, "thumb_1.jpg")))
    outros = [cands[ch][0][1] for ch in ordem[:3]]
    if len(outros) < 2:
        outros = [c for _, c in cands[principal][:3]]
    saidas.append(trio(outros, tx[1]["texto"], tx[1].get("destaque"), fontes, os.path.join(pasta, "thumb_2.jpg"), selo))
    # épica: top lista -> a imagem mais forte do vídeo todo; senão a 2ª melhor do jogo principal
    if selo:
        todas = sorted((n, c) for ch in cands for n, c in cands[ch][:3])
        epi = next((c for n, c in reversed(todas) if c != melhor), melhor)
    else:
        epi = next((c for n, c in cands[principal][1:]), melhor)
    saidas.append(epica(epi, fontes, os.path.join(pasta, "thumb_3.jpg"), selo or None))
    return saidas
