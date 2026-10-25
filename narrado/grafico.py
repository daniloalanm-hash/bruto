"""Motion de números (vídeo longo do YouTube): quando a narração fala um número de impacto, entra um cartão no
canto com o número CONTANDO até o valor + um gráfico simples — anel de porcentagem, barras comparando dois valores
(preço cheio × promoção, custo × faturamento) ou barrinhas subindo. Quadros PNG transparentes, como a logo/CTA."""
import math
import os
import re

from comum import FPS

DUR = 4.6          # tempo na tela
ENTRA, SAI = 0.35, 0.35
CONTA = 1.3        # quanto tempo o número leva para chegar ao valor
AMARELO = (255, 214, 10)
BRANCO = (255, 255, 255)
CINZA = (120, 126, 140)
FUNDO = (12, 14, 20)


def _fonte(fontes, nome, tam):
    from PIL import ImageFont
    return ImageFont.truetype(os.path.join(fontes, nome), int(tam))


def ler_valor(texto):
    """'R$ 24,99' -> ('R$ ', 24.99, 2, ''); '523 km²' -> ('', 523, 0, ' km²'); '40%' -> ('', 40, 0, '%')."""
    t = str(texto or "").strip()
    m = re.search(r"\d[\d.,]*", t)
    if not m:
        return None
    pre, num, suf = t[:m.start()], m.group(0).rstrip(".,"), t[m.start() + len(m.group(0).rstrip(".,")):]
    if "," in num:  # PT-BR: 1.234,56
        inteiro, dec = num.rsplit(",", 1)
        casas = len(dec)
        v = float(inteiro.replace(".", "") + "." + dec)
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", num):  # 1.500 = mil e quinhentos
        casas, v = 0, float(num.replace(".", ""))
    elif "." in num:  # 2.5 (vindo em inglês)
        casas = len(num.split(".")[1])
        v = float(num)
    else:
        casas, v = 0, float(num)
    return pre, v, casas, suf


def fmt(v, casas):
    s = f"{v:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _suave(u):
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


def _cartao_base(w, h, raio=28):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (w + 60, h + 60), (0, 0, 0, 0))
    sombra = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((30, 38, 30 + w, 38 + h), raio, fill=(0, 0, 0, 150))
    img.alpha_composite(sombra.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((30, 30, 30 + w, 30 + h), raio, fill=FUNDO + (228,))
    d.rounded_rectangle((30, 30, 30 + 12, 30 + h), 6, fill=AMARELO + (255,))  # filete de destaque
    return img


def gerar(numero, pasta, fontes, largura=1920):
    """numero = {"valor", "rotulo", "comparar"?: {"valor", "rotulo"}}. Devolve (padrão, duração, (w, h)) ou None."""
    from PIL import Image, ImageDraw
    lido = ler_valor(numero.get("valor"))
    if not lido:
        return None
    pre, alvo, casas, suf = lido
    comp = numero.get("comparar") if isinstance(numero.get("comparar"), dict) else None
    lido_c = ler_valor(comp.get("valor")) if comp else None
    pct = "%" in suf and 0 < alvo <= 100
    e = largura / 1920
    W, H = int(820 * e), int(300 * e if not lido_c else 370 * e)
    os.makedirs(pasta, exist_ok=True)
    # número cabe ao lado do gráfico (comparação: a largura toda)
    final = f"{pre}{fmt(alvo, casas)}{suf}"
    livre = (W - 250 * e) if lido_c else (W - 330 * e)
    tam = 118 * e
    while tam > 60 * e and ImageDraw.Draw(Image.new("RGBA", (4, 4))).textlength(
            final, font=_fonte(fontes, "Anton-Regular.ttf", tam)) > livre:
        tam -= 4 * e
    f_num = _fonte(fontes, "Anton-Regular.ttf", tam)
    f_peq = _fonte(fontes, "Poppins-Medium.ttf", 26 * e)
    rotulo = str(numero.get("rotulo") or "").upper()[:40]
    # rótulo cabe na mesma largura do número (nunca por baixo do gráfico)
    medir = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    tam_r = 34 * e
    while tam_r > 22 * e and medir.textlength(rotulo, font=_fonte(fontes, "Poppins-Bold.ttf", tam_r)) > livre:
        tam_r -= 2 * e
    f_rot = _fonte(fontes, "Poppins-Bold.ttf", tam_r)
    while rotulo and medir.textlength(rotulo, font=f_rot) > livre:
        rotulo = rotulo[:-2].rstrip() + "…"
    # rótulo logo abaixo do número (número encolhido não deixa buraco)
    caixa = medir.textbbox((0, 0), final, font=f_num)
    y_num = int(30 + 22 * e + (118 * e - tam) * 0.55)
    y_rot = y_num + caixa[3] + int(14 * e)
    # sentido do gráfico: número de perda (demissão, corte, queda, prejuízo) desce em vermelho
    negativo = bool(re.search(r"demiss|cort|queda|perd|prejuí|preju|fech|cancel|demit|redu|caiu|despenc|-",
                              f"{rotulo} {numero.get('valor')}".lower()))
    x_txt = int(30 + 44 * e)
    graf_w = int(230 * e)  # área do gráfico à direita
    n = int(DUR * FPS)
    for i in range(n):
        t = i / FPS
        img = _cartao_base(W, H)
        d = ImageDraw.Draw(img)
        u = _suave((t - 0.15) / CONTA)
        v = alvo * u
        txt = f"{pre}{fmt(v, casas)}{suf}"
        d.text((x_txt, y_num), txt, font=f_num, fill=AMARELO, stroke_width=int(3 * e), stroke_fill=(0, 0, 0))
        if not lido_c:
            d.text((x_txt + 2, y_rot), rotulo, font=f_rot, fill=BRANCO)
        gx0, gx1 = int(30 + W - graf_w - 26 * e), int(30 + W - 30 * e)
        gy0, gy1 = int(30 + 40 * e), int(30 + 250 * e)
        if pct:  # anel enchendo
            cx, cy, r = (gx0 + gx1) // 2, (gy0 + gy1) // 2, int(min(gx1 - gx0, gy1 - gy0) / 2) - 4
            larg = int(24 * e)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(60, 64, 76), width=larg)
            fim = -90 + 360 * (alvo / 100) * u
            if u > 0.01:
                d.arc((cx - r, cy - r, cx + r, cy + r), -90, fim, fill=AMARELO, width=larg)
        elif lido_c:  # duas barras: o número falado × o de comparação
            pre_c, alvo_c, casas_c, suf_c = lido_c
            maior = max(alvo, alvo_c) or 1
            by = int(30 + 236 * e)
            bw = W - 2 * int(44 * e)
            for k, (val, cor, rot, valtxt) in enumerate((
                    (alvo_c, CINZA, str(comp.get("rotulo") or "").upper()[:22], f"{pre_c}{fmt(alvo_c, casas_c)}{suf_c}"),
                    (alvo, AMARELO, rotulo[:22], txt))):
                y = by + k * int(46 * e)
                prog = _suave((t - 0.15 - 0.25 * k) / CONTA)
                comp_b = int(bw * 0.62 * (val / maior) * prog)
                d.rounded_rectangle((x_txt, y, x_txt + max(comp_b, 6), y + int(30 * e)), int(8 * e), fill=cor)
                d.text((x_txt + comp_b + int(14 * e), y - int(2 * e)), f"{valtxt}  {rot}", font=f_peq, fill=BRANCO)
            # selo só quando é desconto/queda de verdade (não em "versão flex × versão híbrida")
            antes = re.search(r"cheio|original|normal|antes|anterior|antigo|era|de \d|\b(19|20)\d\d\b",
                              str(comp.get("rotulo") or "").lower())
            if alvo_c and alvo < alvo_c and pre == pre_c and suf == suf_c and u > 0.6 and (negativo or antes):
                queda = round((1 - alvo / alvo_c) * 100)
                if 1 <= queda <= 99:
                    s = f"-{queda}%"
                    bx = gx1 - int(150 * e)
                    d.rounded_rectangle((bx, gy0, gx1, gy0 + int(70 * e)), int(14 * e), fill=(235, 32, 38))
                    d.text(((bx + gx1) // 2, gy0 + int(35 * e)), s, font=_fonte(fontes, "Anton-Regular.ttf", 52 * e),
                           fill=BRANCO, anchor="mm")
        else:  # barrinhas subindo (ou descendo, em vermelho, se o número é de perda) + seta
            nb = 5
            bw = (gx1 - gx0) / (nb * 1.6)
            destaque = (235, 32, 38) if negativo else AMARELO
            for k in range(nb):
                deg = (nb - k) if negativo else (k + 1)
                alt = (0.28 + 0.72 * deg / nb) * (gy1 - gy0) * _suave((t - 0.1 - 0.12 * k) / 0.7)
                x = gx0 + k * bw * 1.6
                cor = destaque if k == nb - 1 else (70, 76, 92)
                if alt > 2:
                    d.rounded_rectangle((x, gy1 - alt, x + bw, gy1), int(6 * e), fill=cor)
            if u > 0.5:
                a0 = (gx0, gy0 + 24 * e) if negativo else (gx0, gy1 - 30 * e)
                a1 = (gx1 - 18 * e, gy1 - 30 * e) if negativo else (gx1 - 18 * e, gy0 + 24 * e)
                d.line([a0, a1], fill=BRANCO, width=int(6 * e))
                a = math.atan2(a1[1] - a0[1], a1[0] - a0[0])
                px, py = a1
                d.polygon([(px + 26 * e * math.cos(a), py + 26 * e * math.sin(a)),
                           (px + 22 * e * math.cos(a + 2.4), py + 22 * e * math.sin(a + 2.4)),
                           (px + 22 * e * math.cos(a - 2.4), py + 22 * e * math.sin(a - 2.4))], fill=BRANCO)
        # entrada (desliza da direita) e saída (sobe e some)
        alfa, dx = 1.0, 0
        if t < ENTRA:
            p = _suave(t / ENTRA)
            dx, alfa = int((1 - p) * img.width * 0.6), p
        elif t > DUR - SAI:
            p = (DUR - t) / SAI
            dx, alfa = int((1 - p) * img.width * 0.15), max(p, 0.0)
        quadro = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if alfa < 1:
            img.putalpha(img.getchannel("A").point(lambda q: int(q * alfa)))
        quadro.alpha_composite(img, (dx, 0) if dx < img.width else (img.width - 1, 0))
        quadro.save(os.path.join(pasta, f"{i:03d}.png"))
    return os.path.join(pasta, "%03d.png"), DUR, (W + 60, H + 60)


def momentos(projeto, timeline, total=None):
    """[(t, numero)] dos números falados no vídeo (um a cada 6 s no máximo; nada na abertura nem na tela final)."""
    import cartoes
    inicio = {x["cena"]: x["inicio"] for x in (timeline or [])}
    total = float(total or projeto.get("duracao") or 0) or 1e9
    saida = []
    for k, c in enumerate(projeto.get("cenas") or []):
        num = c.get("numero")
        if not (isinstance(num, dict) and num.get("valor") and ler_valor(num["valor"])) or k not in inicio:
            continue
        if re.fullmatch(r"\s*(19|20)\d{2}\s*", str(num["valor"])):
            continue  # ano não é número de impacto (contar até 2017 fica estranho)
        t = cartoes.momento_do_numero(projeto, k, num["valor"])
        t = t if t is not None else inicio[k] + 0.4
        if t < 3 or t > total - 25:
            continue
        if saida and t - saida[-1][0] < DUR + 1.5:
            continue
        saida.append((round(t, 2), num))
    return saida
