"""Thumbnails do YouTube: 3 versões por vídeo longo, para o "Testar e comparar" do YouTube Studio.

Os 3 estilos que mais aparecem nos vídeos de games com muita view no YouTube BR (pesquisa de out/2026):
  1. manchete  — 2 a 4 palavras gigantes (branco + 1 palavra em amarelo), fonte pesada inclinada, contorno preto,
                 a arte do jogo atrás com o lado do texto escurecido ("BIOWARE ACABOU DE VEZ");
  2. trio      — 2 ou 3 jogos lado a lado em fatias inclinadas + frase curta embaixo ("OS MELHORES JOGOS PS5");
  3. épica     — a imagem mais bonita do jogo, tela cheia e tratada, só um selo pequeno (estilo dos tops).
Regras completas em GUIA_THUMBS.md. As imagens são o material oficial já baixado (screenshots da Steam, quadros dos trailers); a escolha prioriza
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
    pena_escuro = max(0.0, 85 - brilho) * 1.6 + max(0.0, brilho - 200) * 1.0
    return cor * 1.0 + contraste * 0.8 + lap * 2.0 - pena_escuro


def _tem_texto(caminho):
    try:
        import assets
        return assets._fracao_texto_east(caminho) >= 0.004
    except Exception:
        return False


_ROSTOS = {}


YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
_yunet = []


def _detector():
    """YuNet (rede do OpenCV): acha rosto de personagem 3D bem melhor que Haar, que nem existe no OpenCV 5."""
    if _yunet:
        return _yunet[0]
    det = None
    try:
        import cv2
        import assets
        caminho = os.path.expanduser("~/.cache/narrado/yunet.onnx")
        if not os.path.exists(caminho) or os.path.getsize(caminho) < 100_000:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            tmp = f"{caminho}.{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                f.write(assets._get(YUNET_URL, timeout=120))
            os.replace(tmp, caminho)
        det = cv2.FaceDetectorYN.create(caminho, "", (640, 360), 0.6)
    except Exception as e:
        print(f"aviso: detector de rosto indisponível ({e})")
    _yunet.append(det)
    return det


def _rosto(caminho):
    """Centro (x, y em fração) do maior rosto da imagem, ou None."""
    if caminho in _ROSTOS:
        return _ROSTOS[caminho]
    r = None
    try:
        import cv2
        det = _detector()
        img = cv2.imread(caminho)
        if det is not None and img is not None:
            h0, w0 = img.shape[:2]
            img = cv2.resize(img, (640, int(640 * h0 / w0)))
            det.setInputSize((img.shape[1], img.shape[0]))
            rostos = det.detect(img)[1]
            if rostos is not None and len(rostos):
                x, y, w, h = max(rostos, key=lambda q: q[2] * q[3])[:4]
                r = ((x + w / 2) / img.shape[1], (y + h / 2) / img.shape[0])
                _AREA[caminho] = float(w * h) / (img.shape[0] * img.shape[1])
    except Exception:
        r = None
    _ROSTOS[caminho] = r
    return r


_AREA = {}


def _area_rosto(caminho):
    """Fração da imagem ocupada pelo maior rosto (0 se não houver)."""
    return _AREA.get(caminho, 0.0) if _rosto(caminho) else 0.0


def _tem_rosto(caminho):
    """Rosto/personagem grande na imagem: thumb com personagem chama mais."""
    return _rosto(caminho) is not None


def arte_oficial(appid, pasta):
    """Key art da Steam (library_hero: arte grande, nítida, sem logo) — fundo de thumb com cara profissional."""
    if not str(appid or "").isdigit():
        return None
    destino = os.path.join(pasta, f"hero_{appid}.jpg")
    if os.path.exists(destino) and os.path.getsize(destino) > 20_000:
        return destino
    import assets
    for base in ("https://shared.fastly.steamstatic.com/store_item_assets/steam/apps",
                 "https://cdn.cloudflare.steamstatic.com/steam/apps"):
        try:
            dados = assets._get(f"{base}/{appid}/library_hero.jpg", timeout=30)
            if dados and len(dados) > 20_000:
                with open(destino, "wb") as f:
                    f.write(dados)
                return destino
        except Exception:
            continue
    return None


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
        try:
            hero = arte_oficial(j.get("appid"), pasta)
        except Exception as e:
            print(f"aviso: key art de {ch}: {e}", flush=True)
            hero = None
        print(f"thumbs: key art de {ch}: {'ok' if hero else 'não achada'}", flush=True)
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
                        pre = ""
                        if m.get("marca"):  # logo de outro canal no canto: borrada como no vídeo
                            import render
                            pre = render._delogo(src, m["marca"])
                        ffmpeg("-ss", f"{float(m.get('t', 0)) + float(m.get('dur', 2)) / 2:.2f}", "-i", src,
                               "-frames:v", 1, "-q:v", 2, "-vf", pre + "scale=1920:-2", q)
                    except Exception:
                        continue
                if os.path.exists(q):
                    cands.append(q)
        # arte guardada no repositório (narrado/arte/<chave>/: key art oficial que o Danilo mandou) vence tudo
        pasta_arte = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arte", ch)
        artes = [os.path.join(pasta_arte, a) for a in sorted(os.listdir(pasta_arte))] if os.path.isdir(pasta_arte) else []
        # imagens que o Danilo subiu no app para o vídeo também entram, com vantagem
        enviadas = [os.path.join(raw, m.get("arquivo", "")) for m in banco.values()
                    if m.get("tipo") == "i" and m.get("jogo") == "proprio" and ch == (projeto.get("jogos") or [{}])[0].get("chave")
                    and os.path.exists(os.path.join(raw, m.get("arquivo", "")))]
        notas, com_texto = [], []
        for a in artes + enviadas:
            try:
                n = _nota(Image.open(a)) + 60 + (35 if _tem_rosto(a) else 0)
                notas.append((n, a))
            except Exception:
                continue
        if hero:
            try:
                n = _nota(Image.open(hero)) + 25 + (35 if _tem_rosto(hero) else 0)  # arte oficial: vantagem
                (com_texto if _tem_texto(hero) else notas).append((n, hero))
            except Exception:
                pass
        for c in cands[:40]:
            try:
                img = Image.open(c)
                if img.width < 600:
                    continue
                n = _nota(img) + (35 if _tem_rosto(c) else 0)  # personagem na tela segura o clique
                if projeto.get("nicho") == "carros" and os.path.basename(c).startswith("commons_"):
                    n -= 40  # foto do Commons pode ser de outra geração do carro: quadro do vídeo oficial vem antes
                (com_texto if _tem_texto(c) else notas).append((n, c))
            except Exception:
                continue
        notas = notas or [(n - 100, c) for n, c in com_texto]  # imagem com texto só se não houver outra
        if notas:
            saida[ch] = sorted(notas, reverse=True)
    return saida


# ---------------------------------------------------------------- desenho

def _cobrir(img, w, h, foco=None, alvo_x=0.5, zoom=1.0):
    """Recorta cobrindo w x h; com foco (rosto), põe o rosto em alvo_x da largura (manchete: à direita do texto)."""
    from PIL import Image
    img = img.convert("RGB")
    esc = max(w / img.width, h / img.height) * zoom
    img = img.resize((int(img.width * esc) + 1, int(img.height * esc) + 1), Image.LANCZOS)
    if foco:
        x = int(foco[0] * img.width - alvo_x * w)
        y = int(foco[1] * img.height - 0.42 * h)
    else:
        x, y = (img.width - w) // 2, (img.height - h) // 2
    x, y = min(max(x, 0), img.width - w), min(max(y, 0), img.height - h)
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
    foco = _rosto(fundo)
    img = _tratar(_cobrir(Image.open(fundo), W, H, foco, alvo_x=0.72, zoom=1.15 if foco else 1.0), 1.0)
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
        img = _tratar(_cobrir(Image.open(fz), int(fatia + incl * 2), H, _rosto(fz)), 0.9)
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
    img = _vinheta(_tratar(_cobrir(Image.open(fundo), W, H, _rosto(fundo), alvo_x=0.5), 1.25), 0.45).convert("RGBA")
    if selo:
        s = _selo(selo, fontes, 52)
        img.alpha_composite(s, (W - s.width - 26, 24))
    img.convert("RGB").save(destino, quality=92)
    return destino


VERMELHO = (235, 32, 38)


def _seta_queda(w, h):
    """Linha de gráfico despencando (vermelha, com brilho e seta) — o símbolo de 'queda' das thumbs que mais rodam."""
    from PIL import Image, ImageDraw, ImageFilter
    pts = [(0.02, 0.56), (0.14, 0.64), (0.24, 0.59), (0.36, 0.75), (0.46, 0.70), (0.62, 0.86), (0.84, 0.90)]
    pts = [(x * w, y * h) for x, y in pts]
    camada = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    k = max(0.45, min(1.0, w / 1280))
    d.line(pts, fill=VERMELHO + (255,), width=int(26 * k), joint="curve")
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    ponta = (x2 + 60 * k * math.cos(ang), y2 + 60 * k * math.sin(ang))
    lados = [(x2 + 52 * k * math.cos(ang + s), y2 + 52 * k * math.sin(ang + s)) for s in (math.pi / 2, -math.pi / 2)]
    d.polygon([ponta, lados[0], lados[1]], fill=VERMELHO + (255,))
    contorno = camada.getchannel("A").filter(ImageFilter.MaxFilter(13))
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    preto = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    base.paste(preto, (0, 0), contorno)
    brilho = Image.new("RGBA", (w, h), VERMELHO + (0,))
    brilho.putalpha(camada.getchannel("A").filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * 0.8)))
    base.alpha_composite(brilho)
    base.alpha_composite(camada)
    return base


def _palavra(texto, fontes, tam, cor):
    from PIL import Image, ImageDraw, ImageFilter
    f = _fonte(fontes, "Anton-Regular.ttf", tam)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    cont = max(8, tam // 14)
    w = int(tmp.textlength(texto, font=f)) + 4 * cont + 40
    img = Image.new("RGBA", (w, int(tam * 1.25) + 40), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20 + 2 * cont, 20), texto, font=f, fill=cor, stroke_width=cont, stroke_fill=(0, 0, 0))
    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sombra.putalpha(img.getchannel("A").point(lambda v: int(v * 0.75)).filter(ImageFilter.GaussianBlur(12)))
    base = Image.new("RGBA", img.size, (0, 0, 0, 0))
    base.alpha_composite(sombra, (8, 12))
    base.alpha_composite(img)
    return base


NEGATIVO = re.compile(r"FIM|ACAB|QUEDA|CAIU|CAI|FECH|DEMIT|DEMISS|CORT|CANCEL|MORR|FALI|PERD|PREJU|ADEUS|"
                      r"FRACASS|FLOP|ERRO|PIOR|CRISE|BOMB")
POSITIVO = re.compile(r"RECORD|HIST[OÓ]RIC|BILH|MILH|ESGOT|SUCESS|EXPLOD|DOMIN|GIGANT|MONSTR|ABSURD|LIDER|TOP|"
                      r"CRESC|SUBI|DISPAR|INSANO|MAIOR")


def _seta(w, h, sobe):
    """Gráfico da thumb: despencando em vermelho (fim/queda) ou disparando em verde (recorde/sucesso)."""
    from PIL import Image
    s = _seta_queda(w, h)
    if not sobe:
        return s
    import numpy as np
    a = np.asarray(s).copy()
    verm = (a[..., 0] > 150) & (a[..., 1] < 90)
    a[verm, 0], a[verm, 1], a[verm, 2] = 40, 230, 90
    brilho = (a[..., 0] > 100) & (a[..., 1] < 60) & ~verm  # halo vermelho
    a[brilho, 0], a[brilho, 1], a[brilho, 2] = 40, 230, 90
    return Image.fromarray(a).transpose(Image.FLIP_TOP_BOTTOM)


def impacto(fundo, palavra, fontes, destino, selo=None):
    """Close do personagem + UMA palavra gigante inteira (nunca partida) embaixo + gráfico só quando a palavra
    tem sentido de queda (vermelho, descendo) ou de recorde (verde, subindo)."""
    from PIL import Image, ImageEnhance
    foco = _rosto(fundo)
    pal = re.sub(r"\s+", " ", str(palavra or "").upper()).strip()[:40] or "FIM."  # nunca corta no meio da palavra
    neg, pos = bool(NEGATIVO.search(pal)), bool(POSITIVO.search(pal))
    img = _cobrir(Image.open(fundo), W, H, foco, alvo_x=0.5, zoom=1.15 if foco else 1.0)
    img = _tratar(img, 1.1 if not neg else 0.9)
    if neg:
        img = ImageEnhance.Color(img).enhance(0.75)  # tom mais sério
    img = _vinheta(img, 0.5 if neg else 0.35)
    import numpy as np
    a = np.clip((np.linspace(0, 1, H) - 0.5) / 0.5, 0, 1) ** 1.4 * 200  # chão escuro para a palavra
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img,
                          Image.fromarray(np.tile(a[:, None], (1, W)).astype("uint8"))).convert("RGBA")
    if neg or pos:  # gráfico num quadro do lado oposto ao rosto (nunca por cima do personagem)
        bw, bh = int(W * 0.40), int(H * 0.46)
        cx = (foco[0] if foco else 0.5)
        x0 = int(W * 0.03) if cx >= 0.5 else W - bw - int(W * 0.03)
        img.alpha_composite(_seta(bw, bh, sobe=pos and not neg), (x0, int(H * 0.06)))
    cor = AMARELO if pos and not neg else BRANCO
    t = _palavra(pal, fontes, 300, cor)
    if t.width > W * 0.94 * 1.6 and " " in pal:
        # texto longo ("SUVS INJUSTIÇADOS"): duas linhas equilibradas em vez de encolher demais ou cortar
        ps = pal.split()
        k = min(range(1, len(ps)), key=lambda i: abs(len(" ".join(ps[:i])) - len(" ".join(ps[i:]))))
        l1, l2 = _palavra(" ".join(ps[:k]), fontes, 260, cor), _palavra(" ".join(ps[k:]), fontes, 260, cor)
        passo = int(260 * 1.02)
        t = Image.new("RGBA", (max(l1.width, l2.width), passo + l2.height), (0, 0, 0, 0))
        t.alpha_composite(l1, ((t.width - l1.width) // 2, 0))
        t.alpha_composite(l2, ((t.width - l2.width) // 2, passo))
    if t.width > W * 0.94:
        esc = W * 0.94 / t.width
        t = t.resize((int(t.width * esc), int(t.height * esc)), Image.LANCZOS)
    if t.height > H * 0.62:
        esc = H * 0.62 / t.height
        t = t.resize((int(t.width * esc), int(t.height * esc)), Image.LANCZOS)
    img.alpha_composite(t, ((W - t.width) // 2, H - t.height + 14))
    if selo:
        s = _selo(selo, fontes, 56)
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
    extra = [{"texto": " ".join(pal[:3]) or curto, "destaque": pal[-1] if pal else ""},
             {"texto": " ".join(pal[:3]) or curto, "destaque": pal[1] if len(pal) > 1 else ""},
             {"texto": "FIM.", "destaque": ""}]
    ts = (ts + extra[len(ts):])[:3]
    # a 3ª é UMA palavra (estilo impacto): se a IA mandou frase, fica a palavra de destaque
    t3 = ts[2]
    if len(str(t3["texto"]).split()) > 1:
        ts[2] = {"texto": t3.get("destaque") or str(t3["texto"]).split()[-1], "destaque": ""}
    return ts


def gerar(projeto, raw, fontes, pasta, so=None, evitar=(), rodada=0, fixar=()):
    """Gera thumb_1/2/3.jpg em pasta. Devolve a lista de arquivos.
    so=1|2|3 refaz só aquela (botão "gerar outra" do app): as imagens de `evitar` (nomes de arquivo já usados
    nela) vão para o fim da fila e a escolha varia entre as boas (sorteio pela `rodada`).
    fixar = nomes das imagens a manter (só o texto mudou). Grava em projeto["thumbs_imgs"] as imagens de cada uma."""
    import collections
    import random
    trab = os.path.join(pasta, "trab", "thumbs")
    cands = candidatas(projeto, raw, trab)
    if not cands:
        return []
    corte = max(n for ch in cands for n, _ in cands[ch][:1]) * 0.6
    evitar = set(evitar or ())
    todos = {os.path.basename(c): c for ch in cands for _, c in cands[ch]}
    fix = [todos[b] for b in (fixar or ()) if b in todos] if so else []
    if so:
        rng = random.Random(rodada)
        for ch in cands:  # variação: sorteio entre as boas, as já usadas vão para o fim
            cands[ch] = sorted(((n + rng.uniform(0, 30) - (500 if so != 2 and os.path.basename(c) in evitar else 0), c)
                                for n, c in cands[ch]), reverse=True)
    tempo = collections.Counter(c.get("jogo") for c in projeto.get("cenas", []) if c.get("jogo"))
    ordem = sorted(cands, key=lambda ch: -tempo.get(ch, 0))
    principal = ordem[0]
    tx = textos(projeto)
    selo = _selo_do_titulo(projeto.get("titulo") or (projeto.get("post") or {}).get("titulo_yt") or "")
    saidas = []
    # manchete: de preferência com personagem (rosto) do jogo principal
    # o rosto MAIOR vence (falso positivo do detector costuma ser pequeno, num quadro caótico)
    def novo(c):
        return os.path.basename(c) not in evitar
    com_rosto = [(_area_rosto(c), c) for n, c in cands[principal][:8] if _area_rosto(c) >= 0.012]
    if so == 1 and any(novo(c) for _, c in com_rosto):
        com_rosto = [(a, c) for a, c in com_rosto if novo(c)]
        com_rosto = [rng.choice(sorted(com_rosto, reverse=True)[:3])]
    melhor = max(com_rosto)[1] if com_rosto else cands[principal][0][1]
    if so == 1 and fix:
        melhor = fix[0]
    usadas_por = {1: [melhor]}
    if so in (None, 1):
        saidas.append(manchete(melhor, tx[0]["texto"], tx[0].get("destaque"), fontes, os.path.join(pasta, "thumb_1.jpg")))
    # épica (sem texto): close de personagem, de preferência de OUTRO jogo do vídeo (variedade no teste A/B);
    # só jogos com tempo de tela relevante — nunca um jogo citado de passagem
    usadas = {melhor}
    relevantes = [ch for ch in ordem if tempo.get(ch, 0) >= max(2, tempo.get(principal, 0) * 0.15)][:4] or [principal]
    epi = None
    for grupo in ([ch for ch in relevantes if ch != principal], [principal]):
        rostos = [(_area_rosto(c), c) for ch in grupo for n, c in cands.get(ch, [])[:8]
                  if c not in usadas and _area_rosto(c) >= 0.012]
        if so == 3 and any(novo(c) for _, c in rostos):
            rostos = [rng.choice(sorted((x for x in rostos if novo(x[1])), reverse=True)[:3])]
        if rostos:
            epi = max(rostos)[1]
            break
    if epi is None:
        resto = [(n, c) for ch in relevantes for n, c in cands[ch][:3] if c not in usadas]
        epi = max(resto)[1] if resto else melhor
    usadas.add(epi)
    if so == 3 and not fix and os.path.basename(epi) in evitar:  # sem outro close inédito: outra imagem boa qualquer
        alt = [c for ch in relevantes for n, c in cands.get(ch, []) if novo(c) and n >= corte - 30]
        alt = [c for c in alt if c not in usadas] or alt
        if alt:
            epi = rng.choice(alt[:3])
    if so == 3 and fix:
        epi = fix[0]
    usadas_por[3] = [epi]
    if so == 2:  # refazendo o trio: as imagens das outras thumbs podem voltar
        usadas = set()
    # trio: a melhor imagem de cada jogo que ainda não foi usada
    # fatia só com imagem boa: nota perto da melhor do vídeo (quadro escuro de gameplay fica de fora)
    outros = []
    for ch in relevantes[:3]:
        c = next((c for n, c in cands[ch] if c not in usadas and n >= corte), None)
        if c:
            outros.append(c)
            usadas.add(c)
    if len(outros) < 3:  # vídeo de um jogo só: as melhores imagens dele, sem repetir
        for n, c in cands[principal]:
            if len(outros) >= 3:
                break
            if c not in usadas and c not in outros and n >= corte:
                outros.append(c)
                usadas.add(c)
    if len(outros) < 3:  # faltou imagem boa inédita: repete a melhor arte em outro recorte, mas nunca escura
        boas = [c for ch in cands for n, c in cands[ch] if n >= corte and c not in outros]
        outros += boas[:3 - len(outros)]
    if len(outros) < 2:
        outros = [c for _, c in cands[principal][:3]]
    if so == 2 and fix:
        outros = fix
    elif so == 2:
        rng.shuffle(outros)
    usadas_por[2] = outros
    if so in (None, 2):
        saidas.append(trio(outros, tx[1]["texto"], tx[1].get("destaque"), fontes, os.path.join(pasta, "thumb_2.jpg"), selo))
    if so in (None, 3):
        saidas.append(impacto(epi, tx[2]["texto"], fontes, os.path.join(pasta, "thumb_3.jpg"), selo or None))
    imgs = projeto.setdefault("thumbs_imgs", {})
    for k, v in usadas_por.items():
        if so in (None, k):
            imgs[str(k)] = [os.path.basename(c) for c in v]
    return saidas
