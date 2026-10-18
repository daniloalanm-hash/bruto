"""Cartões gerados para notícia sem trailer: "print" da manchete e cartela de número.

Servem para qualquer notícia (mercado, console, esports, lei): mostram exatamente o que está sendo falado
quando não existe vídeo do assunto. São PNGs 1080x1920 que entram no banco como imagens comuns
(tipo "i", com "cartao"), refeitos a cada render a partir do projeto — não dependem de cache.

Tudo fica na metade de cima da tela: a legenda mora em ~1180 px.
"""
import os
import re
import unicodedata
from urllib.parse import urlparse

from comum import ffmpeg

W, H = 1080, 1920
AMARELO = (255, 212, 0)
TOPO_LEGENDA = 1060     # nada importante do cartão abaixo disso (a legenda fica em ~1180 px)
MAX_NUMEROS = 4

SITES = {
    "ign.com": "IGN", "br.ign.com": "IGN Brasil", "theverge.com": "The Verge", "eurogamer.net": "Eurogamer",
    "dotesports.com": "Dot Esports", "vlr.gg": "VLR.gg", "liquipedia.net": "Liquipedia", "kotaku.com": "Kotaku",
    "polygon.com": "Polygon", "gamespot.com": "GameSpot", "pcgamer.com": "PC Gamer", "vgc.com": "VGC",
    "videogameschronicle.com": "VGC", "theenemy.com.br": "The Enemy", "tecmundo.com.br": "TecMundo",
    "voxel.com.br": "Voxel", "uol.com.br": "UOL", "g1.globo.com": "g1", "ge.globo.com": "ge",
    "bloomberg.com": "Bloomberg", "reuters.com": "Reuters", "xbox.com": "Xbox Wire", "news.xbox.com": "Xbox Wire",
    "blog.playstation.com": "PlayStation Blog", "nintendo.com": "Nintendo", "steampowered.com": "Steam",
    "rockstargames.com": "Rockstar Games", "lojadosgifts.com.br": "Loja dos Gifts", "gamesindustry.biz": "GamesIndustry.biz",
    "insider-gaming.com": "Insider Gaming", "dexerto.com": "Dexerto", "vavel.com": "VAVEL", "draft5.gg": "DRAFT5",
    "maisesports.com.br": "Mais Esports", "hltv.org": "HLTV",
}


def _norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")


def nome_do_site(url):
    host = (urlparse(url or "").hostname or "").lower().removeprefix("www.")
    if not host:
        return ""
    for dom, nome in SITES.items():
        if host == dom or host.endswith("." + dom):
            return nome
    base = host.split(".")[0]
    return base.upper() if len(base) <= 4 else base.capitalize()


def _fonte(pasta, nome, tam):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(pasta, nome), tam)


def _fundo(src):
    """Imagem de fundo coberta, desfocada e escurecida (ou um degradê escuro se não houver imagem)."""
    from PIL import Image, ImageDraw, ImageFilter
    if src and os.path.exists(src):
        with Image.open(src) as im:
            im = im.convert("RGB")
            esc = max(W / im.width, H / im.height)
            im = im.resize((int(im.width * esc) + 1, int(im.height * esc) + 1))
            x, y = (im.width - W) // 2, (im.height - H) // 2
            bg = im.crop((x, y, x + W, y + H)).filter(ImageFilter.GaussianBlur(28))
        return Image.blend(bg, Image.new("RGB", (W, H), (8, 10, 14)), 0.55)
    bg = Image.new("RGB", (W, H), (12, 14, 20))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(18 + 20 * k), int(20 + 8 * k), int(32 + 30 * (1 - k))))
    return bg


def _quebrar(draw, palavras, fonte, largura):
    linhas, atual = [], []
    for p in palavras:
        teste = " ".join(atual + [p])
        if atual and draw.textlength(teste, font=fonte) > largura:
            linhas.append(atual)
            atual = [p]
        else:
            atual.append(p)
    if atual:
        linhas.append(atual)
    return linhas


def _cobrir(im, w, h):
    esc = max(w / im.width, h / im.height)
    im = im.resize((int(im.width * esc) + 1, int(im.height * esc) + 1))
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def _arredondar(im, raio):
    from PIL import Image, ImageDraw
    m = Image.new("L", im.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, im.width - 1, im.height - 1), raio, fill=255)
    out = Image.new("RGBA", im.size)
    out.paste(im, (0, 0), m)
    return out


def _marcadas(destaques):
    marcar = set()
    for ds in destaques or []:
        marcar |= {_norm(p).strip(".,:;!?\"'()") for p in str(ds).split() if len(p) > 2}
    return marcar


def _escrever_manchete(d, linhas, x0, y, f_tit, tam, alt_linha, marcar):
    espaco = d.textlength(" ", font=f_tit)
    for linha in linhas:
        x = x0
        marc = [_norm(p).strip(".,:;!?\"'()") in marcar for p in linha]
        for i, p in enumerate(linha):
            wp = d.textlength(p, font=f_tit)
            if marc[i]:
                fim = x + wp + (espaco if i + 1 < len(linha) and marc[i + 1] else 6)
                d.rectangle((x - (0 if i and marc[i - 1] else 6), y + int(tam * 0.18), fim, y + int(tam * 1.12)), fill=AMARELO)
            d.text((x, y), p, font=f_tit, fill=(17, 17, 17))
            x += wp + espaco
        y += alt_linha
    return y


def _manchete_horizontal(titulo, site, img, destaques, fontes, destino):
    """16:9 (YouTube): cartão largo com a manchete à esquerda e a foto da matéria à direita."""
    from PIL import Image, ImageDraw, ImageFilter
    bg = _fundo(img).convert("RGBA")
    larg, pad = 1520, 56
    x0, topo = (W - larg) // 2, 130
    foto = None
    if img and os.path.exists(img):
        with Image.open(img) as im:
            foto = im.convert("RGB")
    fw, fh = (640, 360) if foto is not None else (0, 0)
    interna = larg - 2 * pad - (fw + pad if fw else 0)
    rasc = ImageDraw.Draw(bg)
    for tam in (72, 66, 60, 54, 48):
        f_tit = _fonte(fontes, "Montserrat-Black.ttf", tam)
        linhas = _quebrar(rasc, str(titulo or "").split(), f_tit, interna)
        if len(linhas) <= 5:
            break
    linhas = linhas[:5]
    alt_linha = int(tam * 1.22)
    alt_txt = 56 + 34 + alt_linha * len(linhas)
    alt = 2 * pad + max(alt_txt, fh)
    topo = max(50, int(H * 0.42 - alt / 2))  # centrado acima da legenda
    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((x0, topo + 16, x0 + larg, topo + alt + 16), 44, fill=(0, 0, 0, 150))
    bg = Image.alpha_composite(bg, sombra.filter(ImageFilter.GaussianBlur(24)))
    d = ImageDraw.Draw(bg)
    d.rounded_rectangle((x0, topo, x0 + larg, topo + alt), 40, fill=(255, 255, 255, 255))
    f_selo = _fonte(fontes, "Montserrat-Black.ttf", 32)
    selo = (site or "NOTÍCIA").upper()
    ws = d.textlength(selo, font=f_selo)
    y = topo + pad + max(0, (alt - 2 * pad - alt_txt) // 2)
    d.rounded_rectangle((x0 + pad, y, x0 + pad + ws + 38, y + 56), 12, fill=(229, 57, 53))
    d.text((x0 + pad + 19, y + 28), selo, font=f_selo, fill=(255, 255, 255), anchor="lm")
    d.text((x0 + pad + ws + 60, y + 28), "NOTÍCIA", font=f_selo, fill=(150, 150, 150), anchor="lm")
    _escrever_manchete(d, linhas, x0 + pad, y + 56 + 34, f_tit, tam, alt_linha, _marcadas(destaques))
    if fw:
        bg.alpha_composite(_arredondar(_cobrir(foto, fw, fh), 24), (x0 + larg - pad - fw, topo + (alt - fh) // 2))
    bg.convert("RGB").save(destino, quality=92)
    return destino


def manchete(titulo, site, img, destaques, fontes, destino):
    """Cartão no formato de notícia: site, manchete com marca-texto nas palavras-chave e a foto da matéria."""
    if W > H:
        return _manchete_horizontal(titulo, site, img, destaques, fontes, destino)
    from PIL import Image, ImageDraw, ImageFilter
    bg = _fundo(img).convert("RGBA")
    x0, larg, pad = 70, 940, 48
    interna = larg - 2 * pad
    rasc = ImageDraw.Draw(bg)
    palavras = str(titulo or "").split()
    foto = None
    if img and os.path.exists(img):
        with Image.open(img) as im:
            foto = im.convert("RGB")
    max_linhas = 4 if foto is not None else 5
    for tam in ((60, 56, 52, 48, 44) if foto is not None else (68, 62, 56, 50, 46)):
        f_tit = _fonte(fontes, "Montserrat-Black.ttf", tam)
        linhas = _quebrar(rasc, palavras, f_tit, interna)
        if len(linhas) <= max_linhas:
            break
    linhas = linhas[:max_linhas]
    alt_linha = int(tam * 1.22)
    topo = 170
    alt_tit = alt_linha * len(linhas)
    alt_foto = 0
    if foto is not None:
        sobra = TOPO_LEGENDA - topo - (pad + 52 + 30 + alt_tit + 34 + pad)
        alt_foto = max(0, min(int(interna * 9 / 16), sobra))
        if alt_foto < 220:
            alt_foto = 0
    alt = pad + 52 + 30 + alt_tit + (34 + alt_foto if alt_foto else 0) + pad

    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((x0, topo + 14, x0 + larg, topo + alt + 14), 40, fill=(0, 0, 0, 150))
    bg = Image.alpha_composite(bg, sombra.filter(ImageFilter.GaussianBlur(22)))
    d = ImageDraw.Draw(bg)
    d.rounded_rectangle((x0, topo, x0 + larg, topo + alt), 36, fill=(255, 255, 255, 255))

    # selo do site
    f_selo = _fonte(fontes, "Montserrat-Black.ttf", 30)
    selo = (site or "NOTÍCIA").upper()
    ws = d.textlength(selo, font=f_selo)
    y = topo + pad
    d.rounded_rectangle((x0 + pad, y, x0 + pad + ws + 36, y + 52), 12, fill=(229, 57, 53))
    d.text((x0 + pad + 18, y + 26), selo, font=f_selo, fill=(255, 255, 255), anchor="lm")
    d.text((x0 + pad + ws + 56, y + 26), "NOTÍCIA", font=f_selo, fill=(150, 150, 150), anchor="lm")

    # manchete com marca-texto
    marcar = set()
    for ds in destaques or []:
        marcar |= {_norm(p).strip(".,:;!?\"'()") for p in str(ds).split() if len(p) > 2}
    y += 52 + 30
    espaco = d.textlength(" ", font=f_tit)
    for linha in linhas:
        x = x0 + pad
        marc = [_norm(p).strip(".,:;!?\"'()") in marcar for p in linha]
        for i, p in enumerate(linha):
            wp = d.textlength(p, font=f_tit)
            if marc[i]:  # palavras marcadas seguidas viram uma faixa só
                fim = x + wp + (espaco if i + 1 < len(linha) and marc[i + 1] else 6)
                d.rectangle((x - (0 if i and marc[i - 1] else 6), y + int(tam * 0.18), fim, y + int(tam * 1.12)), fill=AMARELO)
            d.text((x, y), p, font=f_tit, fill=(17, 17, 17))
            x += wp + espaco
        y += alt_linha
    if alt_foto:
        y += 34
        bg.alpha_composite(_arredondar(_cobrir(foto, interna, alt_foto), 22), (x0 + pad, y))
    bg.convert("RGB").save(destino, quality=92)
    return destino


def numero(valor, rotulo, img, fontes, destino):
    """Cartela de número: o valor bem grande (amarelo) e o que ele significa embaixo."""
    from PIL import ImageDraw
    bg = _fundo(img)
    d = ImageDraw.Draw(bg)
    valor = str(valor).strip().upper()
    tam = 330
    while tam > 110:
        f_val = _fonte(fontes, "Anton-Regular.ttf", tam)
        if d.textlength(valor, font=f_val) <= min(W * 0.89, 1500):
            break
        tam -= 10
    cy = int(H * 0.29) if H > W else int(H * 0.36)
    d.text((W // 2, cy), valor, font=f_val, fill=AMARELO, anchor="mm", stroke_width=10, stroke_fill=(0, 0, 0))
    if rotulo:
        f_rot = _fonte(fontes, "Montserrat-Black.ttf", 58)
        linhas = _quebrar(d, str(rotulo).upper().split(), f_rot, min(W * 0.83, 1400))[:3 if H > W else 2]
        y = d.textbbox((W // 2, cy), valor, font=f_val, anchor="mm", stroke_width=10)[3] + 95
        d.rectangle((W // 2 - 70, y - 40, W // 2 + 70, y - 30), fill=AMARELO)
        for ln in linhas:
            d.text((W // 2, y + 34), " ".join(ln), font=f_rot, fill=(255, 255, 255), anchor="mm",
                   stroke_width=4, stroke_fill=(0, 0, 0))
            y += 74
    bg.save(destino, quality=92)
    return destino


# ---------------------------------------------------------------- números do roteiro

_ESCALA = r"(?:milh(?:ão|ões|oes|ao)|bilh(?:ão|ões|oes|ao)|trilh(?:ão|ões|oes|ao)|mil)\b"
_PADROES = [
    rf"(?:R\$|US\$|U\$|€|£)\s?\d[\d.,]*(?:\s+{_ESCALA})?",
    r"\d[\d.,]*\s?%",
    rf"\d[\d.,]*\s+{_ESCALA}(?:\s+de\s+(?:d[oó]lares|reais|euros|jogadores|c[oó]pias|unidades|pessoas|funcion[aá]rios|horas))?",
    r"\b\d{1,2}\s?[-x×]\s?\d{1,2}\b",
    r"\b\d{1,3}(?:\.\d{3})+\b",
]


def achar_numero(texto):
    """Primeiro número "de impacto" do texto (dinheiro, %, mil/milhões, placar, número grande) e o que vem depois."""
    texto = str(texto or "")
    melhor = None
    for p in _PADROES:
        m = re.search(p, texto, re.I)
        if m and (melhor is None or m.start() < melhor.start()):
            melhor = m
    if not melhor:
        return None
    valor = melhor.group(0).strip().rstrip(".,")
    resto = re.split(r"[.!?;:,]", texto[melhor.end():], maxsplit=1)[0].strip(" ,")
    resto = re.split(r"\s(?:e|que|para|mas|porque|quando|com)\s", " " + resto + " ", maxsplit=1)[0].strip()
    rotulo = " ".join(resto.split()[:5])
    return {"valor": valor, "rotulo": rotulo}


def _digitos(s):
    return re.sub(r"\D", "", str(s or ""))


def momento_do_numero(projeto, cena, valor):
    """Quando o número é falado (timeline final): a palavra mantida do tópico que tem os mesmos dígitos."""
    alvo = _digitos(valor)[:3]
    ws = [w for w in projeto["palavras"] if w.get("manter") and w.get("cena") == cena and "ns" in w]
    if not ws:
        return None
    for w in ws:
        dg = _digitos(w.get("legenda") or w.get("w"))
        if alvo and dg and (dg.startswith(alvo) or (len(dg) >= 2 and alvo.startswith(dg))):
            return w["ns"]
    return None


# ---------------------------------------------------------------- esports: logos (Liquipedia) e placar

LIQUI_UA = "BrutoNarrado/1.0 (https://bruto-nu.vercel.app; github.com/daniloalanm-hash)"
WIKIS = [("valorant", "valorant"), ("counter-strike", "counterstrike"), ("cs2", "counterstrike"), ("cs:go", "counterstrike"),
         ("league of legends", "leagueoflegends"), ("lol", "leagueoflegends"), ("dota", "dota2"),
         ("rainbow six", "rainbowsix"), ("r6", "rainbowsix"), ("rocket league", "rocketleague"),
         ("overwatch", "overwatch"), ("apex", "apexlegends"), ("fortnite", "fortnite"), ("free fire", "freefire"),
         ("pubg mobile", "pubgmobile"), ("pubg", "pubg"), ("mobile legends", "mobilelegends"),
         ("street fighter", "fighters"), ("tekken", "fighters"), ("call of duty", "callofduty"),
         ("ea sports fc", "easportsfc"), ("fifa", "easportsfc")]
_ultimo_liqui = [0.0]


def wikis_do_projeto(projeto, cena=None):
    textos = [cena.get("assunto") or "", cena.get("texto") or ""] if cena else []
    textos += [j.get("nome", "") for j in projeto.get("jogos", [])] + [str(projeto.get("titulo") or "")]
    achados = []
    for t in textos:
        tn = _norm(t)
        for chave, wiki in WIKIS:
            if re.search(rf"(?<![a-z]){re.escape(chave)}(?![a-z])", tn) and wiki not in achados:
                achados.append(wiki)
    return achados or ["valorant", "counterstrike", "leagueoflegends"]


def _liqui(wiki, params):
    """API do Liquipedia (regra deles: User-Agent próprio, gzip e no máximo 1 consulta a cada 2 s)."""
    import gzip
    import json
    import time
    import urllib.parse
    import urllib.request
    espera = 2.1 - (time.time() - _ultimo_liqui[0])
    if espera > 0:
        time.sleep(espera)
    q = urllib.parse.urlencode({**params, "format": "json"})
    req = urllib.request.Request(f"https://liquipedia.net/{wiki}/api.php?{q}",
                                 headers={"User-Agent": LIQUI_UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            b = r.read()
            return json.loads(gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b)
    finally:
        _ultimo_liqui[0] = time.time()


def logo_do_time(nome, wikis, raw):
    """PNG do logo do time (versão para fundo escuro), baixado do Liquipedia e guardado em cache."""
    import urllib.request
    chave = re.sub(r"\W+", "_", _norm(nome)).strip("_")
    destino = os.path.join(raw, f"logo_{chave}.png")
    if os.path.exists(destino):
        return destino
    alvo = re.sub(r"[^a-z0-9]", "", _norm(nome))
    for wiki in wikis[:3]:
        try:
            d = _liqui(wiki, {"action": "query", "titles": nome, "redirects": 1, "prop": "images", "imlimit": 500})
            pags = list((d.get("query") or {}).get("pages", {}).values())
            if not pags or "missing" in pags[0]:
                continue
            titulo = re.sub(r"[^a-z0-9]", "", _norm(pags[0].get("title", nome)))
            cand = []
            for im in pags[0].get("images", []):
                arq = im["title"].split(":", 1)[-1]
                base = re.sub(r"[^a-z0-9]", "", _norm(re.sub(r"(\d{4})?\s*(allmode|darkmode|lightmode).*$", "", arq, flags=re.I)))
                if not re.search(r"allmode|darkmode", arq, re.I) or len(base) < 2:
                    continue
                exato = base in (alvo, titulo)
                # "FURIA" ↔ "FURIA Esports": prefixo vale, mas o nome exato ganha
                if not exato and not any(len(x) >= 3 and (base.startswith(x) or x.startswith(base)) for x in (alvo, titulo)):
                    continue
                ano = max([int(x) for x in re.findall(r"(?:19|20)\d\d", arq)] or [0])
                cand.append((exato, ano, "darkmode" in arq.lower(), im["title"]))
            if not cand:
                print(f"aviso: {nome} ({wiki}) sem logo reconhecível entre "
                      f"{[i['title'] for i in pags[0].get('images', []) if 'mode' in i['title']][:12]}", flush=True)
                continue
            escolhido = max(cand)[3]
            info = _liqui(wiki, {"action": "query", "titles": escolhido, "prop": "imageinfo", "iiprop": "url"})
            url = list(info["query"]["pages"].values())[0]["imageinfo"][0]["url"]
            req = urllib.request.Request(url, headers={"User-Agent": LIQUI_UA})
            with urllib.request.urlopen(req, timeout=30) as r, open(destino, "wb") as f:
                f.write(r.read())
            print(f"logo de {nome}: {escolhido} ({wiki})", flush=True)
            return destino
        except Exception as e:
            print(f"aviso: logo de {nome} no Liquipedia ({wiki}): {e}", flush=True)
    return None


def placar(valor, times, logos, rotulo, img, fontes, destino):
    """Placar: logo e nome de cada time, o resultado no meio e o contexto embaixo."""
    from PIL import Image, ImageDraw
    bg = _fundo(img).convert("RGBA")
    d = ImageDraw.Draw(bg)
    cy = 520 if H > W else int(H * 0.38)
    f_val = _fonte(fontes, "Anton-Regular.ttf", 190 if H > W else 220)
    f_nome = _fonte(fontes, "Montserrat-Black.ttf", 44)
    pts = re.split(r"\s*[-x×a]\s*", str(valor).strip(), maxsplit=1)
    centro = f"{pts[0]} - {pts[1]}" if len(pts) == 2 else str(valor)
    d.text((W // 2, cy), centro, font=f_val, fill=AMARELO, anchor="mm", stroke_width=9, stroke_fill=(0, 0, 0))
    for lado, (nome, logo) in enumerate(zip(times[:2], (list(logos) + [None, None])[:2])):
        cx = (175 if H > W else int(W * 0.24)) if lado == 0 else (W - 175 if H > W else int(W * 0.76))
        if logo and os.path.exists(logo):
            with Image.open(logo) as im:
                im = im.convert("RGBA")
                esc = min(250 / im.width, 230 / im.height)
                im = im.resize((max(1, int(im.width * esc)), max(1, int(im.height * esc))))
                bg.alpha_composite(im, (cx - im.width // 2, cy - 40 - im.height // 2))
        else:  # sem logo: as iniciais do time num anel
            d.ellipse((cx - 105, cy - 145, cx + 105, cy + 65), outline=AMARELO, width=8)
            ini = "".join(p[0] for p in re.findall(r"[A-Za-z0-9]+", str(nome))[:3]).upper() or "?"
            d.text((cx, cy - 40), ini, font=_fonte(fontes, "Anton-Regular.ttf", 110 if len(ini) < 3 else 84),
                   fill=(255, 255, 255), anchor="mm")
        nome_txt = str(nome).upper()
        while d.textlength(nome_txt, font=f_nome) > 360 and f_nome.size > 26:
            f_nome = _fonte(fontes, "Montserrat-Black.ttf", f_nome.size - 4)
        d.text((cx, cy + 150), nome_txt, font=f_nome, fill=(255, 255, 255), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
    if rotulo:
        f_rot = _fonte(fontes, "Montserrat-Black.ttf", 52)
        y = cy + (260 if H > W else 250)
        d.rectangle((W // 2 - 70, y - 40, W // 2 + 70, y - 30), fill=AMARELO)
        for ln in _quebrar(d, str(rotulo).upper().split(), f_rot, min(W * 0.83, 1400))[:2]:
            d.text((W // 2, y + 30), " ".join(ln), font=f_rot, fill=(255, 255, 255), anchor="mm", stroke_width=4, stroke_fill=(0, 0, 0))
            y += 68
    bg.convert("RGB").save(destino, quality=92)
    return destino


# ---------------------------------------------------------------- integração com o projeto

def _quadro_da_cena(projeto, cena, raw, pasta_tmp):
    """Imagem para o fundo da cartela: a 1ª mídia do tópico (quadro do trailer ou a própria imagem)."""
    banco = projeto["banco"]
    for mid in projeto["cenas"][cena].get("midias", []):
        m = banco.get(mid)
        if not m or m.get("cartao"):
            continue
        src = os.path.join(raw, m["arquivo"])
        if not os.path.exists(src):
            continue
        if m["tipo"] == "i":
            return src
        out = os.path.join(pasta_tmp, f"quadro_{cena}.jpg")
        try:
            ffmpeg("-ss", f"{m.get('t', 0) + 0.8:.2f}", "-i", src, "-frames:v", 1, "-q:v", 3, out)
            return out
        except Exception:
            continue
    return None


def _baixar(url, destino):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        dados = r.read()
    if len(dados) < 15000:
        raise RuntimeError("imagem pequena demais")
    with open(destino, "wb") as f:
        f.write(dados)
    return destino


def capa_do_jogo(appid, raw, destino):
    """Capa do jogo para o momento em que o nome é falado: o pôster vertical da Steam (quando existe) ou a
    arte principal da loja (header) grande, sobre o wallpaper do jogo desfocado. Sempre 1080x1920."""
    import json
    import urllib.request
    from PIL import Image, ImageDraw, ImageFilter
    poster = os.path.join(raw, f"steam_{appid}_poster.jpg")
    arte = os.path.join(raw, f"steam_{appid}_header.jpg")
    fundo = os.path.join(raw, f"steam_{appid}_fundo.jpg")
    if not os.path.exists(poster):
        for url in (f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg",
                    f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900_2x.jpg",
                    f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg"):
            try:
                _baixar(url, poster)
                break
            except Exception:
                continue
    if not (os.path.exists(arte) and os.path.exists(fundo)):
        # jogo novo: a Steam guarda as artes num caminho com hash — só a API da loja sabe o endereço
        req = urllib.request.Request(f"https://store.steampowered.com/api/appdetails?appids={appid}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = (json.loads(r.read()).get(str(appid)) or {}).get("data") or {}
        for url, alvo in ((d.get("header_image"), arte), (d.get("background_raw") or d.get("background"), fundo)):
            if url and not os.path.exists(alvo):
                try:
                    _baixar(url.replace("header.jpg", "header_2x.jpg") if alvo == arte else url, alvo)
                except Exception:
                    try:
                        _baixar(url, alvo)
                    except Exception:
                        pass
    if not os.path.exists(poster) and not os.path.exists(arte):
        raise RuntimeError("sem arte na Steam")
    bg = _fundo(fundo if os.path.exists(fundo) else (arte if os.path.exists(arte) else poster)).convert("RGBA")
    with Image.open(poster if os.path.exists(poster) else arte) as im:
        im = im.convert("RGB")
        if os.path.exists(poster):
            esc = min(820 / im.width, 1230 / im.height)
        else:
            esc = min(980 / im.width, 700 / im.height)
        im = im.resize((int(im.width * esc), int(im.height * esc)))
    x, y = (W - im.width) // 2, int(H * 0.42 - im.height / 2)
    sombra = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((x, y + 18, x + im.width, y + im.height + 18), 30, fill=(0, 0, 0, 170))
    bg = Image.alpha_composite(bg, sombra.filter(ImageFilter.GaussianBlur(26)))
    bg.alpha_composite(_arredondar(im, 26), (x, y))
    bg.convert("RGB").save(destino, quality=92)
    return destino


def momento_do_nome(projeto, nome, cena_min=0):
    """(cena, instante) da 1ª vez que o nome do jogo é falado (1ª palavra significativa do nome)."""
    toks = [t for t in re.findall(r"[a-z0-9]+", _norm(nome)) if len(t) >= 3 and t not in ("the", "and")]
    if not toks:
        return None
    ws = sorted((w for w in projeto["palavras"] if w.get("manter") and "ns" in w and w.get("cena") is not None),
                key=lambda w: w["ns"])
    lim = lambda w: re.sub(r"[^a-z0-9]", "", _norm(w.get("legenda") or w["w"]))
    alvo = toks[:2]  # palavra exata (não "carros" para "Car") e, se houver, a seguinte também
    for i, w in enumerate(ws):
        if w["cena"] < cena_min or lim(w) != alvo[0]:
            continue
        if len(alvo) > 1 and (i + 1 >= len(ws) or lim(ws[i + 1]) != alvo[1]):
            continue
        return w["cena"], w["ns"]
    return None


def aplicar(projeto, raw, fontes, pasta_trab):
    """Gera os cartões do projeto e (na 1ª vez) coloca cada um no tópico certo. Devolve True se o banco mudou."""
    if projeto.get("cartoes") is False or "materia" not in projeto:
        return False  # vídeo feito antes dos cartões: ajuste não muda o que já foi aprovado
    os.makedirs(raw, exist_ok=True)
    os.makedirs(pasta_trab, exist_ok=True)
    banco, cenas = projeto["banco"], projeto["cenas"]
    tag = re.sub(r"\W", "", str(projeto.get("id") or "x"))[:16]
    novo = False
    primeira = not projeto.get("cartoes_aplicados")

    # 1) manchete
    mat = projeto.get("materia") or {}
    titulo = (mat.get("titulo") or "").strip()
    url = mat.get("url") or projeto.get("fonte") or ""
    if titulo and len(cenas) > 1:
        img = next((os.path.join(raw, m["arquivo"]) for m in banco.values()
                    if m.get("jogo") == "materia" and os.path.exists(os.path.join(raw, m["arquivo"]))), None)
        img = img or _quadro_da_cena(projeto, 1, raw, pasta_trab) or _quadro_da_cena(projeto, 0, raw, pasta_trab)
        arq = f"cartao_{tag}_manchete.jpg"
        try:
            manchete(titulo, mat.get("site") or nome_do_site(url), img,
                     cenas[1].get("destaques", []) + cenas[0].get("destaques", []), fontes, os.path.join(raw, arq))
            if "cartao_manchete" not in banco:
                novo = True
            banco["cartao_manchete"] = {"tipo": "i", "jogo": "cartao", "arquivo": arq, "cartao": "manchete"}
            if primeira:
                cenas[1].setdefault("midias", []).insert(0, "cartao_manchete")
                for c in cenas:  # tópico sem material nenhum: a manchete é o que tem a ver com o assunto
                    if not c.get("midias"):
                        c["midias"] = ["cartao_manchete"]
        except Exception as e:
            print(f"aviso: cartão de manchete não gerado: {e}", flush=True)

    # 2) capa do jogo (pôster ou arte da Steam sobre o wallpaper) NO MOMENTO em que o nome é falado
    if projeto.get("capas", True) and H > W:
        for j in projeto.get("jogos", []):
            appid = str(j.get("appid") or "")
            if not appid.isdigit():
                continue
            mid = f"capa_{j['chave']}"
            arq = f"cartao_{tag}_capa_{appid}.jpg"
            try:
                capa_do_jogo(appid, raw, os.path.join(raw, arq))
                if mid not in banco:
                    novo = True
                banco[mid] = {"tipo": "i", "jogo": "cartao", "arquivo": arq, "cartao": "capa", "capa": True,
                              "nome": j["nome"], "chave_jogo": j["chave"]}
            except Exception as e:
                print(f"aviso: capa de {j.get('nome')} não gerada: {e}", flush=True)

    # 3) números
    feitos = 0
    for k, c in enumerate(cenas):
        if k == 0 or feitos >= MAX_NUMEROS:
            continue
        num = c.get("numero") if isinstance(c.get("numero"), dict) and c["numero"].get("valor") else None
        if num is None and primeira and "numero" not in c:
            num = achar_numero(c.get("texto"))
            c["numero"] = num  # fica salvo (a tela pode apagar/editar)
        if not num:
            continue
        mid = f"cartao_num_{k}"
        arq = f"cartao_{tag}_num{k}.jpg"
        try:
            fundo = _quadro_da_cena(projeto, k, raw, pasta_trab)
            times = [t for t in (num.get("times") or []) if t][:2]
            if len(times) == 2 and re.fullmatch(r"\s*\d{1,3}\s*[-x×a]\s*\d{1,3}\s*", str(num["valor"])):
                wk = wikis_do_projeto(projeto, c)
                logos = [logo_do_time(t, wk, raw) for t in times]
                placar(num["valor"], times, logos, num.get("rotulo"), fundo, fontes, os.path.join(raw, arq))
            else:
                numero(num["valor"], num.get("rotulo"), fundo, fontes, os.path.join(raw, arq))
            if mid not in banco:
                novo = True
            banco[mid] = {"tipo": "i", "jogo": "cartao", "arquivo": arq, "cartao": "numero", "cena": k,
                          "valor": num["valor"]}
            feitos += 1
        except Exception as e:
            print(f"aviso: cartela de número do tópico {k} não gerada: {e}", flush=True)
    projeto["cartoes_aplicados"] = True
    return novo
