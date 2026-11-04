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
VERMELHO = (235, 32, 38)
DUR_MIN = 3.0      # cartão encurta quando o próximo número vem logo atrás (nunca menos que isto)
DUR_FONTE = 3.2

# escada (barrinhas subindo/descendo) só quando a frase fala de movimento
TENDE = re.compile(r"\b(sub(iu|iram|indo|ir)|cresc\w*|aument\w*|salt(ou|aram|o)|dispar\w*|dobr(ou|aram)|"
                   r"tripl\w*|avanç\w*|alta de|(?<!a )ca(iu|íram|indo|ir)|despenc\w*|diminu\w*|recu(ou|aram)|"
                   r"queda|encolh\w*|reduz\w*|redução|baix(ou|aram|a de)|desab\w*|evolu\w*|saltou|pulou|"
                   r"multiplic\w*|explod\w*)\b")
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


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


def gerar(numero, pasta, fontes, largura=1920, dur=None):
    """numero = {"valor", "rotulo", "comparar"?: {"valor", "rotulo"}, "_frase"?}. Devolve (padrão, duração, (w, h)).
    Gráfico: anel (%), duas barras (comparação), escada só quando a frase fala de tendência (subiu, caiu,
    cresceu…); nos outros casos o número entra sozinho, em destaque (sem gráfico inventado)."""
    from PIL import Image, ImageDraw
    if numero.get("tipo") == "data":
        return gerar_data(numero, pasta, fontes, largura, dur)
    if numero.get("tipo") == "fonte":
        return gerar_fonte(numero, pasta, fontes, largura, dur)
    dur = float(dur or DUR)
    lido = ler_valor(numero.get("valor"))
    if not lido:
        return None
    pre, alvo, casas, suf = lido
    comp = numero.get("comparar") if isinstance(numero.get("comparar"), dict) else None
    lido_c = ler_valor(comp.get("valor")) if comp else None
    pct = "%" in suf and 0 < alvo <= 100
    frase = f"{numero.get('_frase') or ''} {numero.get('rotulo') or ''}".lower()
    tendencia = TENDE.search(frase)
    so_numero = not (pct or lido_c or tendencia)
    e = largura / 1920
    W, H = int(820 * e), int(300 * e if not lido_c else 370 * e)
    os.makedirs(pasta, exist_ok=True)
    # número cabe ao lado do gráfico (comparação: a largura toda)
    final = f"{pre}{fmt(alvo, casas)}{suf}"
    livre = (W - 250 * e) if lido_c else (W - 120 * e) if so_numero else (W - 330 * e)
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
    if so_numero:  # cartão justo no texto: número + rótulo + filete que corre por baixo
        larg_txt = max(caixa[2], medir.textlength(rotulo, font=f_rot))
        W = int(max(460 * e, larg_txt + 44 * e + 60 * e))
        H = int(y_rot - 30 + (tam_r * 1.25) + 42 * e)
    # sentido do gráfico: número de perda (demissão, corte, queda, prejuízo) desce em vermelho
    negativo = bool(re.search(r"demiss|cort|queda|perd|prejuí|preju|fech|cancel|demit|redu|caiu|despenc|diminu|recu|"
                              r"encolh|baix|-", f"{rotulo} {numero.get('valor')}".lower()))
    if tendencia:  # o verbo da frase manda no sentido da escada
        negativo = bool(re.search(r"cai|caiu|despenc|diminu|recu|queda|encolh|baix|reduz|perd|desab", tendencia.group(0)))
    x_txt = int(30 + 44 * e)
    graf_w = int(230 * e)  # área do gráfico à direita
    n = int(dur * FPS)
    for i in range(n):
        t = i / FPS
        img = _cartao_base(W, H)
        d = ImageDraw.Draw(img)
        u = _suave((t - 0.15) / CONTA)
        v = alvo * u
        txt = f"{pre}{fmt(v, casas)}{suf}"
        if so_numero:  # "soco" quando o número chega no valor
            b = (t - 0.15 - CONTA) / 0.32
            s = 1 + 0.09 * math.sin(math.pi * b) if 0 <= b <= 1 else 1
            fn = _fonte(fontes, "Anton-Regular.ttf", tam * s) if s != 1 else f_num
            d.text((x_txt, y_num - (tam * (s - 1)) * 0.5), txt, font=fn, fill=AMARELO, stroke_width=int(3 * e),
                   stroke_fill=(0, 0, 0))
        else:
            d.text((x_txt, y_num), txt, font=f_num, fill=AMARELO, stroke_width=int(3 * e), stroke_fill=(0, 0, 0))
        if not lido_c:
            d.text((x_txt + 2, y_rot), rotulo, font=f_rot, fill=BRANCO)
        gx0, gx1 = int(30 + W - graf_w - 26 * e), int(30 + W - 30 * e)
        gy0, gy1 = int(30 + 40 * e), int(30 + 250 * e)
        if so_numero:
            prog = _suave((t - 0.35) / 0.9)
            yl = int(y_rot + tam_r * 1.25 + 10 * e)
            if prog > 0.01:
                d.rounded_rectangle((x_txt, yl, x_txt + max(int((W - 88 * e) * prog), 6), yl + int(7 * e)),
                                    int(4 * e), fill=AMARELO)
        elif pct:  # anel enchendo
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
                cabe = bw - medir.textlength(f"{valtxt}  {rot}", font=f_peq) - 24 * e
                comp_b = int(max(40 * e, min(bw * 0.62, cabe)) * (val / maior) * prog)
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
        elif t > dur - SAI:
            p = (dur - t) / SAI
            dx, alfa = int((1 - p) * img.width * 0.15), max(p, 0.0)
        quadro = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if alfa < 1:
            img.putalpha(img.getchannel("A").point(lambda q: int(q * alfa)))
        quadro.alpha_composite(img, (dx, 0) if dx < img.width else (img.width - 1, 0))
        quadro.save(os.path.join(pasta, f"{i:03d}.png"))
    return os.path.join(pasta, "%03d.png"), dur, (W + 60, H + 60)


def momentos(projeto, timeline, total=None):
    """[(t, item, dur)]: números falados (o do roteiro + os que aparecem na fala), datas e fontes citadas.
    Cartões do canto direito nunca se sobrepõem (o de agora encurta se o próximo vem logo); nada na abertura nem
    na tela final; data e fonte só na 1ª vez que aparecem."""
    import cartoes
    inicio = {x["cena"]: x["inicio"] for x in (timeline or [])}
    total = float(total or projeto.get("duracao") or 0) or 1e9
    cands, vistos = [], set()
    for k, c in enumerate(projeto.get("cenas") or []):
        if k not in inicio:
            continue
        frase = str(c.get("texto") or "")
        itens = []
        num = c.get("numero")
        if isinstance(num, dict) and num.get("valor") and ler_valor(num["valor"]) and \
                not re.fullmatch(r"\s*(19|20)\d{2}\s*", str(num["valor"])):
            itens.append((0, dict(num)))
        dig_roteiro = cartoes._digitos(num.get("valor"))[:3] if isinstance(num, dict) else ""
        if projeto.get("graficos_auto", True):
            for a in numeros_da_fala(frase):
                if dig_roteiro and cartoes._digitos(a["valor"])[:3] == dig_roteiro:
                    continue
                itens.append((1, a))
            for dt in datas_da_fala(frase):
                chave = ("data", dt["dia"], dt["mes"])
                if chave not in vistos:
                    vistos.add(chave)
                    itens.append((2, dt))
            for fo in fontes_da_fala(frase):
                chave = ("fonte", fo["nome"].lower())
                if chave not in vistos:
                    vistos.add(chave)
                    itens.append((3, fo))
        for prio, it in itens:
            it["_frase"] = frase
            if it.get("tipo") == "fonte":
                t = _momento_palavra(projeto, k, it["nome"].split()[0])
            else:
                t = cartoes.momento_do_numero(projeto, k, it["valor"])
            t = t if t is not None else inicio[k] + 0.4
            if t < 3 or t > total - 25:
                continue
            cands.append((round(t, 2), prio, it))
    cands.sort(key=lambda x: x[0])
    # fontes: faixa própria (canto esquerdo), só não encostam umas nas outras
    fontes_ok = []
    for t, prio, it in cands:
        if it.get("tipo") == "fonte" and (not fontes_ok or t - fontes_ok[-1][0] >= DUR_FONTE + 1):
            fontes_ok.append((t, it, DUR_FONTE))
    # canto direito: um por vez, prioridade para o número do roteiro
    escolhidos = []
    for t, prio, it in cands:
        if it.get("tipo") == "fonte":
            continue
        if escolhidos and t - escolhidos[-1][0] < DUR_MIN + 0.4:
            if prio < escolhidos[-1][1]:
                escolhidos[-1] = (t, prio, it)
            continue
        escolhidos.append((t, prio, it))
    saida = []
    for i, (t, prio, it) in enumerate(escolhidos):
        prox = escolhidos[i + 1][0] if i + 1 < len(escolhidos) else 1e9
        dur = max(DUR_MIN, min(DUR, prox - t - 0.4))
        saida.append((t, it, round(dur, 2)))
    return sorted(saida + fontes_ok, key=lambda x: x[0])


def _momento_palavra(projeto, cena, palavra):
    alvo = re.sub(r"\W", "", palavra.lower())
    for w in projeto.get("palavras") or []:
        if w.get("manter") and w.get("cena") == cena and "ns" in w and \
                re.sub(r"\W", "", str(w.get("legenda") or w.get("w")).lower()) == alvo:
            return w["ns"]
    return None


# ---------------------------------------------------------------- números, datas e fontes tirados da fala

_UNID = r"(?:mil(?:hões|hão)?|bilh(?:ões|ão)|trilh(?:ões|ão))"
_NUM = re.compile(r"(?P<pre>(?:US|R)\$\s?|€\s?)?(?P<n>\d[\d.,]*\d|\d)(?P<pct>\s?%|\s+por\s+cento)?"
                  r"(?:\s+(?P<un>" + _UNID + r"))?(?:\s+de\s+(?P<moeda>dólares|reais|euros))?", re.I)
_PARA = {"que", "e", "mas", "logo", "nos", "nas", "no", "na", "pela", "pelo", "com", "entre", "até", "só", "já",
         "apenas", "segundo", "porque", "quando", "se", "ou", "enquanto", "antes", "depois", "para", "pra"}
_PLATAF = [("ps5", "no PS5"), ("playstation", "no PlayStation"), ("xbox", "no Xbox"), ("steam", "na Steam"),
           ("switch", "no Switch"), ("pc", "no PC")]


def _rotulo_depois(resto):
    """'de cópias em pré-venda globalmente.' -> 'cópias em pré-venda'."""
    palavras = []
    for w in re.split(r"\s+", resto.strip()):
        limpo = w.strip(",.;:!?()\"'“”")
        if not limpo or (palavras and limpo.lower() in _PARA) or limpo.lower().endswith("mente"):
            break
        palavras.append(limpo)
        if re.search(r"[,.;:!?]$", w) or len(palavras) >= 5:
            break
    while palavras and palavras[0].lower() in ("de", "em", "a", "o", "cerca"):
        palavras = palavras[1:]
    while palavras and palavras[-1].lower() in _PARA | {"de", "do", "da", "dos", "das", "em", "a", "o"}:
        palavras = palavras[:-1]
    return " ".join(palavras)


def _rotulo_contexto(frase):
    f = frase.lower()
    base = ("arrecadados" if "arrecad" in f else "em receita" if "receita" in f else "faturados" if "fatur" in f
            else "em vendas" if "vend" in f else "em reservas" if "reserv" in f else "")
    if base:
        for chave, txt in _PLATAF:
            if re.search(rf"\b{chave}\b", f):
                return f"{base} {txt}"
    return base


def numeros_da_fala(texto):
    """Números de impacto ditos na frase: [{"valor", "rotulo"}]. Fora: anos, dias de data, número solto pequeno."""
    achados = []
    for m in _NUM.finditer(texto or ""):
        n, un, pre, moeda, pct = m.group("n"), m.group("un"), m.group("pre"), m.group("moeda"), m.group("pct")
        depois = texto[m.end():]
        if re.match(r"\s+de\s+(" + "|".join(MESES) + r")", depois, re.I):
            continue  # dia de data (vira cartão de data)
        lido = ler_valor(n)
        if not lido:
            continue
        v = lido[1]
        if not (un or pre or moeda or pct) and (re.fullmatch(r"(19|20)\d\d", n) or v < 1000):
            continue  # ano ou número pequeno sem unidade ("3 dias", "2013")
        if pct and not 0 < v <= 100:
            continue
        prefixo = (pre or "").strip()
        if moeda:
            prefixo = {"dólares": "US$", "reais": "R$", "euros": "€"}[moeda.lower()]
        valor = (f"{prefixo} " if prefixo else "") + n + ("%" if pct else "") + (f" {un.lower()}" if un else "")
        rot = _rotulo_depois(depois)
        while len(rot) > 30 and " " in rot:
            rot = rot.rsplit(" ", 1)[0]
            rot = re.sub(r"\s+(de|do|da|dos|das|em|e|a|o)$", "", rot)
        if moeda or not rot or len(rot) > 30:
            rot = _rotulo_contexto(texto) or ("" if moeda or prefixo else rot[:30])
        achados.append({"valor": valor, "rotulo": rot, "_auto": True})
    return achados


def datas_da_fala(texto):
    """'19 de novembro de 2026' -> [{"tipo": "data", "dia", "mes", "ano"}]."""
    saida = []
    for m in re.finditer(r"\b(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")(?:\s+de\s+(\d{4}))?", texto or "", re.I):
        saida.append({"tipo": "data", "dia": int(m.group(1)), "mes": m.group(2).lower(), "ano": m.group(3) or "",
                      "valor": m.group(1), "rotulo": _rotulo_data(texto)})
    return saida


def _rotulo_data(texto):
    f = (texto or "").lower()
    for chave, rot in (("lança", "LANÇAMENTO"), ("estreia", "ESTREIA"), ("chega", "CHEGADA"), ("libera", "LIBERAÇÃO"),
                       ("evento", "EVENTO"), ("anúncio", "ANÚNCIO"), ("prazo", "PRAZO"), ("encerr", "FIM")):
        if chave in f:
            return rot
    return "ANOTA NA AGENDA"


_FONTE = re.compile(r"(?:segundo (?:a |o |as |os )?|de acordo com (?:a |o )?|estimativas? d[aoe]s? |dados d[aoe]s? |"
                    r"levantamento d[aoe]s? |relatório d[aoe]s? |pesquisa d[aoe]s? |consultoria |analistas? d[aoe]s? )"
                    r"((?:[A-Z][\w&.-]*|[A-Z]{2,})(?:\s+(?:[A-Z][\w&.-]*|de|of))*)")


def fontes_da_fala(texto):
    saida = []
    for m in _FONTE.finditer(texto or ""):
        nome = re.sub(r"\s+(de|of)$", "", m.group(1)).strip(" .,")
        if len(nome) >= 3 and nome.lower() not in ("eles", "ela", "ele"):
            saida.append({"tipo": "fonte", "nome": nome, "valor": ""})
    return saida


def gerar_data(item, pasta, fontes, largura=1920, dur=None):
    """Folhinha de calendário: faixa vermelha com mês/ano, dia grande que 'cai' e um rótulo do lado."""
    from PIL import Image, ImageDraw
    dur = float(dur or DUR)
    e = largura / 1920
    mes = item["mes"][:3].upper()
    ano = str(item.get("ano") or "")
    rot = str(item.get("rotulo") or "").upper()[:22]
    f_dia = _fonte(fontes, "Anton-Regular.ttf", 120 * e)
    f_mes = _fonte(fontes, "Poppins-Bold.ttf", 30 * e)
    f_rot = _fonte(fontes, "Poppins-Bold.ttf", 34 * e)
    f_ano = _fonte(fontes, "Anton-Regular.ttf", 64 * e)
    medir = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    fw, fh = int(190 * e), int(210 * e)
    W = int(44 * e + fw + 36 * e + max(medir.textlength(rot, font=f_rot), medir.textlength(ano, font=f_ano)) + 44 * e)
    H = int(fh + 50 * e)
    os.makedirs(pasta, exist_ok=True)
    for i in range(int(dur * FPS)):
        t = i / FPS
        img = _cartao_base(W, H)
        d = ImageDraw.Draw(img)
        fx, fy = int(30 + 44 * e), int(30 + 25 * e)
        d.rounded_rectangle((fx, fy, fx + fw, fy + fh), int(18 * e), fill=(245, 245, 245))
        d.rounded_rectangle((fx, fy, fx + fw, fy + int(58 * e)), int(18 * e), fill=VERMELHO)
        d.rectangle((fx, fy + int(30 * e), fx + fw, fy + int(58 * e)), fill=VERMELHO)
        d.text((fx + fw // 2, fy + int(30 * e)), mes, font=f_mes, fill=BRANCO, anchor="mm")
        for k in (0.3, 0.7):  # argolas
            d.ellipse((fx + fw * k - 8 * e, fy - 10 * e, fx + fw * k + 8 * e, fy + 10 * e), fill=(60, 64, 76))
        # dia cai de cima com quique
        p = _suave((t - 0.2) / 0.55)
        dy = (1 - p) * -70 * e
        if t > 0.2:
            d.text((fx + fw // 2, fy + int(135 * e) + dy), str(item["dia"]), font=f_dia, fill=(20, 22, 28), anchor="mm")
        tx = fx + fw + int(36 * e)
        q = _suave((t - 0.55) / 0.5)
        if q > 0:
            cor = tuple(int(c * q) for c in BRANCO)
            d.text((tx, fy + int(40 * e)), rot, font=f_rot, fill=cor)
            if ano:
                d.text((tx, fy + int(88 * e)), ano, font=f_ano, fill=tuple(int(c * q) for c in AMARELO))
        _entra_sai(img, t, dur, os.path.join(pasta, f"{i:03d}.png"))
    return os.path.join(pasta, "%03d.png"), dur, (W + 60, H + 60)


def gerar_fonte(item, pasta, fontes, largura=1920, dur=None):
    """Selo pequeno 'FONTE · NOME' (canto superior esquerdo) quando a narração cita de onde vem o dado."""
    from PIL import Image, ImageDraw
    dur = float(dur or DUR_FONTE)
    e = largura / 1920
    f_a = _fonte(fontes, "Poppins-Medium.ttf", 24 * e)
    f_b = _fonte(fontes, "Poppins-Bold.ttf", 30 * e)
    nome = str(item["nome"]).upper()[:28]
    medir = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    W = int(40 * e + medir.textlength("FONTE", font=f_a) + 22 * e + medir.textlength(nome, font=f_b) + 34 * e)
    H = int(66 * e)
    os.makedirs(pasta, exist_ok=True)
    for i in range(int(dur * FPS)):
        t = i / FPS
        img = _cartao_base(W, H, raio=int(18 * e))
        d = ImageDraw.Draw(img)
        x = int(30 + 34 * e)
        d.text((x, 30 + H // 2), "FONTE", font=f_a, fill=AMARELO, anchor="lm")
        x2 = x + int(medir.textlength("FONTE", font=f_a) + 22 * e)
        n = int(len(nome) * min(1.0, max(0.0, (t - 0.25) / 0.5)))  # nome "digitado"
        d.text((x2, 30 + H // 2), nome[:n], font=f_b, fill=BRANCO, anchor="lm")
        _entra_sai(img, t, dur, os.path.join(pasta, f"{i:03d}.png"), lado=-1)
    return os.path.join(pasta, "%03d.png"), dur, (W + 60, H + 60)


def _entra_sai(img, t, dur, caminho, lado=1):
    """Entra deslizando (lado 1: da direita; -1: da esquerda) e sai sumindo."""
    from PIL import Image
    alfa, dx = 1.0, 0
    if t < ENTRA:
        p = _suave(t / ENTRA)
        dx, alfa = int((1 - p) * img.width * 0.6), p
    elif t > dur - SAI:
        p = (dur - t) / SAI
        dx, alfa = int((1 - p) * img.width * 0.15), max(p, 0.0)
    if alfa < 1:
        img.putalpha(img.getchannel("A").point(lambda q: int(q * alfa)))
    dx = min(dx, img.width - 1)
    quadro = Image.new("RGBA", img.size, (0, 0, 0, 0))
    if lado > 0:
        quadro.alpha_composite(img.crop((0, 0, img.width - dx, img.height)), (dx, 0))
    else:
        quadro.alpha_composite(img.crop((dx, 0, img.width, img.height)), (0, 0))
    quadro.save(caminho)
