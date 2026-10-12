"""Render vertical 1080x1920: cortes dinâmicos por assunto, transições, legenda, título do gancho, emojis e efeitos.

Ordem da montagem:
  1. cada corte vira um segmento (movimento de câmera + efeito de entrada do tópico: flash, zoom, glitch);
  2. segmentos concatenados; por cima: pans (xfade), light leak, zoom de ênfase nas palavras destacadas;
  3. legenda + título do gancho (ASS) e emojis (PNG) queimados por último, sempre nítidos;
  4. voz + trilha de efeitos (woosh, pop, impacto, glitch) mixados.
"""
import os
import re
import shutil

import efeitos
from comum import FPS, W, H, ffmpeg, norm, run

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


def seg_video(src, t0, dur, out, zoom_in, trans):
    z = f"1+0.07*t/{dur:.3f}" if zoom_in else f"1.07-0.07*t/{dur:.3f}"
    if trans in PUNCH:
        z = f"({z})+{PUNCH[trans]}*max(0\\,1-t/{TRANS})"
    c = area_util(src, t0)
    pre = f"crop={c[0]}:{c[1]}:{c[2]}:{c[3]}," if c and int(c[1]) >= 300 else ""
    vf = (f"{pre}crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale={W}:{H},"
          f"scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame,"
          f"crop={W}:{H},setsar=1,fps={FPS},eq=contrast=1.06:saturation=1.12:gamma=1.1" + _fx_transicao(trans))
    ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur:.3f}", "-vf", vf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", out)


def seg_imagem(src, dur, out, k, trans, encaixar=False):
    n = max(1, int(round(dur * FPS)))
    if encaixar:
        # print/foto inteira (sem cortar as laterais) sobre fundo desfocado da própria imagem
        punch = f"+{PUNCH[trans]}*max(0\\,1-t/{TRANS})" if trans in PUNCH else ""
        z = f"(1.0+0.05*t/{dur:.3f}{punch})"
        vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,"
              f"eq=brightness=-0.12[bg];[b]scale={W - 90}:{H - 520}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2-120,setsar=1,fps={FPS},"
              f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H}" + _fx_transicao(trans))
        ffmpeg("-loop", 1, "-t", f"{dur:.3f}", "-i", src, "-filter_complex", vf, "-frames:v", n,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", out)
        return
    pans = ["x='iw/2-(iw/zoom/2)'", f"x='(iw-iw/zoom)*on/{n}'", f"x='(iw-iw/zoom)*(1-on/{n})'"]
    punch = f"+{PUNCH[trans]}*max(0\\,1-on/{int(TRANS * FPS)})" if trans in PUNCH else ""
    # cobre 1350x2400 (paisagem ou retrato, ex.: screenshot de celular) e faz o Ken Burns
    vf = (f"scale=1350:2400:force_original_aspect_ratio=increase,crop=1350:2400,"
          f"zoompan=z='1.05+0.10*on/{n}{punch}':{pans[k % 3]}:y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS},"
          f"setsar=1,eq=contrast=1.05:saturation=1.1" + _fx_transicao(trans))
    ffmpeg("-loop", 1, "-i", src, "-frames:v", n, "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", out)


def seg_video_proprio(src, t0, dur, out, trans):
    """Clipe enviado pelo usuário: vertical vira tela cheia; horizontal fica inteiro sobre fundo desfocado."""
    punch = f"+{PUNCH[trans]}*max(0\\,1-t/{TRANS})" if trans in PUNCH else ""
    z = f"(1.0+0.04*t/{dur:.3f}{punch})"
    vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:2,"
          f"eq=brightness=-0.12[bg];[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},"
          f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame,crop={W}:{H}" + _fx_transicao(trans))
    ffmpeg("-ss", f"{t0:.3f}", "-i", src, "-t", f"{dur:.3f}", "-filter_complex", vf, "-an",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-pix_fmt", "yuv420p", out)


# ---------------------------------------------------------------- linha do tempo

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
        t = fala
        if tipo == "J":
            t = fala + desloc
        elif tipo == "L":
            t = fala - desloc
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
    pal = [w for w in projeto["palavras"] if w.get("manter") and "ns" in w and str(w.get("legenda", "")).strip()]
    blocos, cur = [], []
    for w in pal:
        if cur and (w["cena"] != cur[-1]["cena"] or w["ns"] - cur[-1]["ne"] > 0.6):
            blocos.append(cur)
            cur = []
        cur.append(w)
        fim = bool(re.search(r"[.,:;?!…]$", w["legenda"]))
        txt = " ".join(x["legenda"] for x in cur)
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


def _quebra_titulo(txt, max_linha=14):
    palavras, linhas, cur = txt.split(), [], ""
    for p in palavras:
        if cur and len(cur) + 1 + len(p) > max_linha:
            linhas.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        linhas.append(cur)
    return linhas[:3]


def legenda_ass(projeto, destino, corte_gancho):
    """Legenda em blocos (estilo/cor/altura do projeto) + título grande do gancho. Devolve os blocos."""
    nome_estilo = projeto.get("legenda_estilo") if projeto.get("legenda_estilo") in ESTILOS_LEGENDA else "classico"
    fonte, tam, contorno, sombra, caixa = ESTILOS_LEGENDA[nome_estilo]
    cor = CORES.get(projeto.get("legenda_cor"), CORES["amarelo"])
    try:
        altura = min(max(float(projeto.get("legenda_altura", ALTURA_PADRAO)), 0.35), 0.82)
    except (TypeError, ValueError):
        altura = ALTURA_PADRAO
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
              "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    marcadas = {id(projeto["palavras"][i]) for i in marcar_destaques(projeto)}
    blocos = blocos_legenda(projeto)
    for b in blocos:
        partes = []
        for w in b["pal"]:
            t = re.sub(r"[.,:;…]+$", "", w["legenda"]).upper()
            partes.append((destaque if id(w) in marcadas else normal) + t)
        b["palavras_destaque"] = [norm(w["legenda"]) for w in b["pal"] if id(w) in marcadas]
        b["destaque"] = bool(b["palavras_destaque"])
        pop = rf"{{\an5\pos(540,{y})\fscx70\fscy70\t(0,90,\fscx108\fscy108)\t(90,160,\fscx100\fscy100)}}"
        linhas.append(f"Dialogue: 0,{_ts(b['ini'])},{_ts(b['fim'])},Cap,,0,0,0,,{pop}{' '.join(partes)}")
    # título grande do gancho nos primeiros ~2 s (acima da legenda)
    titulo = str(projeto.get("titulo_tela") or "").strip()
    if projeto.get("titulo_gancho", True) and titulo:
        fim_tit = min(2.3, max(corte_gancho - 0.1, 1.2))
        ls = _quebra_titulo(titulo.upper())
        y_tit = int(H * 0.25)
        texto = r"\N".join(ls)
        # destaque: a última linha na cor da legenda
        if len(ls) > 1:
            texto = r"\N".join(ls[:-1]) + rf"\N{{\c&H{cor}&}}" + ls[-1]
        anim = (rf"{{\an5\pos(540,{y_tit})\fscx60\fscy60\alpha&HFF&\t(0,120,\alpha&H00&\fscx106\fscy106)"
                rf"\t(120,220,\fscx100\fscy100)\fad(0,180)}}")
        linhas.append(f"Dialogue: 1,{_ts(0.05)},{_ts(fim_tit)},Tit,,0,0,0,,{anim}{texto}")
    with open(destino, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    return blocos, y


def escolher_emojis(projeto, blocos):
    """No máximo 1 emoji por tópico, só em bloco com palavra destacada (ou o emoji que o roteiro sugeriu)."""
    if not projeto.get("emoji", True) or not os.path.exists(EMOJI_FONTE):
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

def renderizar(projeto, banco, pasta_raw, voz_wav, pasta_trab, destino, pasta_fontes, progresso=None):
    pasta_trab = os.path.abspath(pasta_trab)
    seg_dir = os.path.join(pasta_trab, "seg")
    shutil.rmtree(seg_dir, ignore_errors=True)
    os.makedirs(seg_dir)
    faixas, total, tipos = linha_tempo(projeto)
    trans_cena = transicoes(projeto, faixas)
    lista, k = [], 0
    usados_cena = []
    for fi, (c, a, b) in enumerate(faixas):
        dur = b - a
        n = max(1, round(dur / CUT))
        midias = [m for m in projeto["cenas"][c].get("midias", []) if m in banco] or list(banco)[:1]
        tr = trans_cena.get(c)
        usados_cena.append({"cena": c, "inicio": round(a, 2), "fim": round(b, 2), "cortes": [],
                            "entrada": tipos.get(c, "inicio"), "transicao": tr or "inicio"})
        for j in range(n):
            mid = midias[j % len(midias)]
            m = banco[mid]
            rep = j // len(midias)
            d = dur / n
            out = os.path.join(seg_dir, f"{k:04d}.mp4")
            fx = None
            if j == 0 and tr:
                fx = {"pan_leak": "leak"}.get(tr, tr) if tr in ("flash", "zoom", "glitch", "leak", "pan_leak") else None
            src = os.path.join(pasta_raw, m["arquivo"])
            if m["tipo"] == "v" and m.get("proprio"):
                seg_video_proprio(src, m.get("t", 0) + rep * 2.5, d, out, fx)
            elif m["tipo"] == "v":
                seg_video(src, m["t"] + rep * 2.5, d, out, k % 2 == 0, fx)
            else:
                seg_imagem(src, d, out, k, fx, encaixar=bool(m.get("proprio")))
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

    corte_gancho = faixas[1][1] if len(faixas) > 1 else total
    ass = os.path.join(pasta_trab, "legenda.ass")
    blocos, y_leg = legenda_ass(projeto, ass, corte_gancho)
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
    for b in blocos:
        if b.get("destaque") and all(abs(b["ini"] - t) > 0.5 for t in cortes.values()) and b["ini"] > 0.4:
            if not tempos_destaque or b["ini"] - tempos_destaque[-1] >= ESPACO_ENFASE:
                tempos_destaque.append(b["ini"])
    if projeto.get("zoom_enfase", True):
        z = expr_zoom_enfase(tempos_destaque)
        if z:
            grafo.append(f"{v}scale=w='trunc({W}*({z})/2)*2':h='trunc({H}*({z})/2)*2':eval=frame,crop={W}:{H}[vz]")
            v = "[vz]"

    # legenda + título
    grafo.append(f"{v}subtitles={ass}:fontsdir={pasta_fontes}[vs]")
    v = "[vs]"

    # emojis acima da legenda, com pulinho de entrada
    for i, e in enumerate(escolher_emojis(projeto, blocos)):
        png = emoji_png(e["emoji"], os.path.join(pasta_trab, f"emoji{i}.png"))
        if not png:
            continue
        idx = nova_entrada(png)
        a0, a1 = e["ini"], e["fim"]
        y_emo = y_leg - 285
        grafo.append(f"{v}[{idx}:v]overlay=x=(W-w)/2:y='{y_emo}-36*max(0\\,1-(t-{a0:.3f})/0.16)':"
                     f"enable='between(t\\,{a0:.3f}\\,{a1:.3f})'[ve{i}]")
        v = f"[ve{i}]"
    grafo.append(f"{v}fade=t=in:st=0:d=0.15[v]")

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
        sfx = efeitos.trilha_efeitos(eventos, total, os.path.join(pasta_trab, "efeitos.wav"))
        idx = nova_entrada(sfx)
        a = (f"[1:a]apad[vz0];[{idx}:a]apad[fx0];[vz0][fx0]amix=inputs=2:duration=first:normalize=0,"
             f"alimiter=limit=0.95:level=disabled[a]")
    grafo.append(a)

    ffmpeg(*entradas, "-filter_complex", ";".join(grafo),
           "-map", "[v]", "-map", "[a]", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", 20,
           "-c:a", "aac", "-b:a", "192k", "-ar", 48000, "-movflags", "+faststart", destino)
    return usados_cena, total
