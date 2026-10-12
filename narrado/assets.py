"""Busca material oficial dos jogos na Steam (screenshots + trailers) e monta o banco de cenas.

Cada jogo vira um grupo de "mídias": screenshots (imagem com movimento de câmera) e trechos de
trailer já filtrados (sem logos de abertura/encerramento, sem telas escuras ou cartelas de texto).
"""
import json
import os
import re
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

from comum import UA, ffmpeg, duracao, norm, run, slug

MAX_TRAILERS = 3
MAX_SHOTS_POR_TRAILER = 16
MAX_SCREENSHOTS = 10
TRECHO_MAX = 150  # segundos baixados de cada trailer


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def achar_jogo(nome):
    """Procura o jogo na loja da Steam e devolve (appid, nome oficial) do resultado mais parecido."""
    q = urllib.parse.quote(nome)
    dados = json.loads(_get(f"https://store.steampowered.com/api/storesearch/?term={q}&cc=br&l=portuguese"))
    itens = [i for i in dados.get("items", []) if i.get("type") == "app"]
    if not itens:
        return None
    alvo = norm(nome)
    # prefere o nome mais parecido; penaliza DLCs/trilhas/edições ("soundtrack", "dlc", "pack")
    def nota(i):
        n = norm(i["name"])
        extra = 0.25 if re.search(r"soundtrack|dlc|pack|expansion|season pass|demo", i["name"], re.I) else 0
        return SequenceMatcher(None, alvo, n).ratio() - extra
    melhor = max(itens, key=nota)
    if nota(melhor) < 0.5:  # nada parecido na Steam (ex.: jogo só de celular): deixa as outras fontes tentarem
        return None
    return melhor["id"], melhor["name"]


def _baixar_trailer(src, p):
    """Baixa os primeiros TRECHO_MAX s de um trailer (cópia direta; se o contêiner não aceitar, reencoda)."""
    if os.path.exists(p):
        return True
    try:
        ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-an", "-c:v", "copy", "-movflags", "+faststart", p)
        if duracao(p) < 3:
            raise RuntimeError("curto")
    except Exception:
        ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-an", "-vf", "scale=-2:1080",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
    return os.path.exists(p)


def _steam(nome, pasta):
    achado = achar_jogo(nome)
    if not achado:
        return None
    appid, oficial = achado
    d = json.loads(_get(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=portuguese"))[str(appid)]
    if not d.get("success"):
        return None
    d = d["data"]
    chave = slug(oficial)
    info = {"chave": chave, "nome": oficial, "appid": appid, "fonte": "Steam", "screens": [], "trailers": []}
    for i, s in enumerate(d.get("screenshots", [])[:MAX_SCREENSHOTS]):
        p = os.path.join(pasta, f"{chave}_s{i}.jpg")
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(_get(s["path_full"]))
        info["screens"].append(p)
    for i, m in enumerate(d.get("movies", [])[:MAX_TRAILERS]):
        src = None
        for k in ("mp4", "webm"):
            if m.get(k):
                src = m[k].get("max") or m[k].get("480")
                break
        src = src or m.get("hls_h264") or m.get("dash_h264")
        if not src:
            continue
        p = os.path.join(pasta, f"{chave}_t{i}.mp4")
        try:
            if _baixar_trailer(src, p):
                info["trailers"].append({"arquivo": p, "nome": m.get("name", "")})
        except Exception as e:
            print(f"aviso: trailer {i} de {oficial} falhou: {e}")
    return info


def _app_store(nome, pasta):
    """Jogos de celular: screenshots da App Store (API pública do iTunes, sem chave)."""
    q = urllib.parse.quote(nome)
    dados = json.loads(_get(f"https://itunes.apple.com/search?term={q}&entity=software&country=br&limit=10"))
    itens = [r for r in dados.get("results", []) if "Games" in (r.get("genres") or []) or
             r.get("primaryGenreName") == "Games"]
    if not itens:
        return None
    alvo = norm(nome)
    melhor = max(itens, key=lambda r: SequenceMatcher(None, alvo, norm(r.get("trackName", ""))).ratio())
    if SequenceMatcher(None, alvo, norm(melhor.get("trackName", ""))).ratio() < 0.45:
        return None
    oficial = melhor["trackName"]
    chave = slug(oficial)
    info = {"chave": chave, "nome": oficial, "appid": f"ios-{melhor.get('trackId')}", "fonte": "App Store",
            "screens": [], "trailers": []}
    urls = (melhor.get("screenshotUrls") or []) + (melhor.get("ipadScreenshotUrls") or [])
    for i, u in enumerate(urls[:MAX_SCREENSHOTS]):
        p = os.path.join(pasta, f"{chave}_a{i}.jpg")
        try:
            if not os.path.exists(p):
                with open(p, "wb") as f:
                    f.write(_get(u))
            info["screens"].append(p)
        except Exception as e:
            print(f"aviso: screenshot da App Store falhou: {e}")
    return info if info["screens"] else None


def _youtube(nome, chave, pasta, n=2):
    """Trailer oficial no YouTube (yt-dlp). Melhor esforço: o YouTube às vezes bloqueia servidores."""
    import shutil
    import subprocess
    if not shutil.which("yt-dlp"):
        return []
    saida = []
    for i in range(n):
        p = os.path.join(pasta, f"{chave}_y{i}.mp4")
        if os.path.exists(p):
            saida.append({"arquivo": p, "nome": "YouTube"})
            continue
        tmp = os.path.join(pasta, f"{chave}_y{i}.tmp")
        try:
            r = subprocess.run(
                ["yt-dlp", f"ytsearch{n}:{nome} trailer oficial", "--playlist-items", str(i + 1),
                 "--match-filter", "duration < 900", "-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]",
                 "--download-sections", f"*0-{TRECHO_MAX}", "--no-playlist", "--quiet", "--no-warnings",
                 "-o", tmp + ".%(ext)s"], capture_output=True, text=True, timeout=240)
            baixado = next((os.path.join(pasta, x) for x in os.listdir(pasta) if x.startswith(os.path.basename(tmp))), None)
            if r.returncode != 0 or not baixado:
                print(f"aviso: YouTube não liberou o trailer de {nome}: {(r.stderr or '').strip()[-200:]}")
                break
            ffmpeg("-i", baixado, "-t", TRECHO_MAX, "-an", "-vf", "scale=-2:'min(1080,ih)'",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
            os.remove(baixado)
            saida.append({"arquivo": p, "nome": "YouTube"})
        except Exception as e:
            print(f"aviso: YouTube falhou para {nome}: {e}")
            break
    return saida


def baixar_imagens_materia(urls, pasta):
    """Imagens da própria matéria (mostram a novidade de verdade). Converte tudo para JPG e descarta
    miniaturas. Devolve entradas de banco {id: {...}}."""
    import hashlib
    os.makedirs(pasta, exist_ok=True)
    banco = {}
    for u in (urls or [])[:8]:
        h = hashlib.sha1(u.encode()).hexdigest()[:10]
        destino = os.path.join(pasta, f"materia_{h}.jpg")
        try:
            if not os.path.exists(destino):
                bruto = destino + ".orig"
                with open(bruto, "wb") as f:
                    f.write(_get(u, timeout=40))
                ffmpeg("-i", bruto, "-frames:v", 1, "-q:v", 2, destino)
                os.remove(bruto)
            from PIL import Image
            with Image.open(destino) as im:
                w, hh = im.size
            if w < 480 or hh < 270:
                continue
            # com texto (arte de capa com título) ou formato estranho: mostra inteira sobre fundo desfocado
            inteira = tem_texto(destino, limite=3) or not (1.2 <= w / hh <= 2.0)
            banco[f"materia_{h}"] = {"tipo": "i", "jogo": "materia", "arquivo": os.path.basename(destino),
                                     "encaixar": bool(inteira), "url": u}
        except Exception as e:
            print(f"aviso: imagem da matéria não baixou ({u[:80]}): {e}")
    print(f"matéria: {len(banco)} imagens usáveis")
    return banco


def baixar_jogo(nome, pasta):
    """Material oficial do jogo: Steam; se não tiver (ou não tiver trailer), App Store e YouTube."""
    os.makedirs(pasta, exist_ok=True)
    info = None
    try:
        info = _steam(nome, pasta)
    except Exception as e:
        print(f"aviso: Steam falhou para '{nome}': {e}")
    if not info:
        try:
            info = _app_store(nome, pasta)
        except Exception as e:
            print(f"aviso: App Store falhou para '{nome}': {e}")
    if not info:
        info = {"chave": slug(nome), "nome": nome, "appid": None, "fonte": "YouTube", "screens": [], "trailers": []}
    if not info["trailers"]:
        info["trailers"] = _youtube(info["nome"], info["chave"], pasta)
    if not info["screens"] and not info["trailers"]:
        print(f"aviso: nenhum material encontrado para '{nome}'")
        return None
    print(f"{info['nome']} ({info['fonte']}): {len(info['screens'])} imagens, {len(info['trailers'])} trailers")
    return info


def cortes_de_cena(video):
    """Instantes de troca de plano no trailer (detecção barata em baixa resolução)."""
    r = run(["ffmpeg", "-hide_banner", "-i", video, "-vf", "scale=320:-2,select='gt(scene,0.32)',showinfo",
             "-an", "-f", "null", "-"])
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def medir_quadro(video, t):
    """Brilho e saturação médios de um quadro (para descartar telas escuras e cartelas de texto)."""
    r = run(["ffmpeg", "-hide_banner", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf",
             # mede só a faixa central: tarjas pretas de trailer cinematográfico não contam como "escuro"
             "scale=320:-2,crop=iw:ih*0.6,signalstats,metadata=print", "-f", "null", "-"])
    y = re.search(r"YAVG=([\d.]+)", r.stderr)
    s = re.search(r"SATAVG=([\d.]+)", r.stderr)
    return float(y.group(1)) if y else 0, float(s.group(1)) if s else 0


EAST_URL = "https://raw.githubusercontent.com/oyyd/frozen_east_text_detection.pb/master/frozen_east_text_detection.pb"
_east = []


def _rede_east():
    """Detector de texto EAST (OpenCV). Acha cartelas estilizadas que o Tesseract não consegue ler."""
    if _east:
        return _east[0]
    rede = None
    try:
        import cv2
        caminho = os.path.expanduser("~/.cache/narrado/east.pb")
        if not os.path.exists(caminho) or os.path.getsize(caminho) < 1_000_000:
            os.makedirs(os.path.dirname(caminho), exist_ok=True)
            with open(caminho + ".tmp", "wb") as f:
                f.write(_get(EAST_URL, timeout=180))
            os.replace(caminho + ".tmp", caminho)
        rede = cv2.dnn.readNet(caminho)
    except Exception as e:  # sem OpenCV/modelo: fica só o Tesseract
        print(f"aviso: detector EAST indisponível ({e})")
    _east.append(rede)
    return rede


def _fracao_texto_east(png):
    """Fração da imagem marcada como texto pelo EAST (0 a 1)."""
    rede = _rede_east()
    if rede is None:
        return 0.0
    import cv2
    img = cv2.imread(png)
    if img is None:
        return 0.0
    h, w = img.shape[:2]
    nw = 384
    nh = max(32, int(round(h * nw / w / 32)) * 32)
    blob = cv2.dnn.blobFromImage(cv2.resize(img, (nw, nh)), 1.0, (nw, nh), (123.68, 116.78, 103.94), True, False)
    rede.setInput(blob)
    s = rede.forward("feature_fusion/Conv_7/Sigmoid")[0, 0]
    return float((s >= 0.8).mean())


def faixa_sem_texto(png, min_altura=0.45):
    """Maior faixa vertical da imagem sem texto (EAST), em frações (y0, y1). Usada nos screenshots de loja,
    que têm chamadas de marketing em cima/embaixo. None se não sobrar faixa útil."""
    rede = _rede_east()
    if rede is None:
        return None
    import cv2
    import numpy as np
    img = cv2.imread(png)
    if img is None:
        return None
    h, w = img.shape[:2]
    nw = 384
    nh = max(32, int(round(h * nw / w / 32)) * 32)
    blob = cv2.dnn.blobFromImage(cv2.resize(img, (nw, nh)), 1.0, (nw, nh), (123.68, 116.78, 103.94), True, False)
    rede.setInput(blob)
    sc = rede.forward("feature_fusion/Conv_7/Sigmoid")[0, 0]
    linhas = (sc >= 0.8).sum(axis=1) >= 2           # linha (de 4 px) com texto
    linhas = np.convolve(linhas.astype(float), np.ones(15), mode="same") > 0  # margem (~3%) em volta do texto
    melhor, ini = (0, 0), None
    for i, t in enumerate(list(linhas) + [True]):
        if not t and ini is None:
            ini = i
        elif t and ini is not None:
            if i - ini > melhor[1] - melhor[0]:
                melhor = (ini, i)
            ini = None
    y0, y1 = melhor[0] / len(linhas), melhor[1] / len(linhas)
    return (round(y0, 3), round(y1, 3)) if y1 - y0 >= min_altura else None


def _palavras_fortes(tsv):
    """Palavras que o Tesseract leu com segurança (descarta o "ruído" de folhagem, fagulhas etc.)."""
    fortes, grandes = 0, 0
    for linha in tsv.splitlines()[1:]:
        cols = linha.split("\t")
        if len(cols) < 12 or not cols[11].strip():
            continue
        conf = float(cols[10] or -1)
        nucleo = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", cols[11].strip())
        if len(nucleo) >= 3 and nucleo.isalpha() and conf >= 70 and (nucleo.isupper() or nucleo.istitle()):
            fortes += 1
            if len(nucleo) >= 4 and nucleo.isupper() and conf >= 88:
                grandes += 1
    return fortes, grandes


def movimento(video, t, dur=1.0):
    """Quanto a imagem muda entre quadros (média do YDIF): mede a "energia" do plano para o gancho."""
    r = run(["ffmpeg", "-hide_banner", "-ss", f"{max(t, 0):.2f}", "-i", video, "-t", f"{dur:.2f}", "-vf",
             "scale=320:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF", "-f", "null", "-"])
    v = [float(x) for x in re.findall(r"YDIF=([\d.]+)", r.stderr)]
    return round(sum(v) / len(v), 2) if v else 0.0


def tem_texto(origem, t=None, limite=2):
    """True se a parte que aparece no vídeo (centro 9:16) tiver texto: cartela de trailer, legenda, menu.

    Combina o detector EAST (acha a região de texto, mesmo estilizado) com uma leitura do Tesseract.
    """
    import shutil
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cor = os.path.join(tmp, "q.png")
        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        if t is not None:
            args += ["-ss", f"{t:.2f}"]
        # mesmo recorte do render: centro vertical 9:16
        args += ["-i", origem, "-frames:v", "1", "-vf", "crop='min(iw,ih*9/16)':ih,scale=720:-2", cor]
        if subprocess.run(args).returncode != 0 or not os.path.exists(cor):
            return False
        if _fracao_texto_east(cor) >= 0.0018 * limite / 2:
            return True
        if not shutil.which("tesseract"):
            return False
        # legenda/letreiro comum: uma leitura do Tesseract, contando só palavras lidas com segurança
        cinza = os.path.join(tmp, "g.png")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", cor, "-vf", "format=gray", cinza])
        r = subprocess.run(["tesseract", cinza, "-", "--psm", "11", "-l", "eng", "tsv"], capture_output=True, text=True)
        fortes, grandes = _palavras_fortes(r.stdout)
        if fortes >= limite or grandes >= 1:
            return True
    return False


def trechos_bons(video):
    """Escolhe trechos de trailer que funcionam como cena: planos com 1,6s+ fora de abertura/encerramento."""
    dur = duracao(video)
    marcas = [0.0] + cortes_de_cena(video) + [dur]
    ini, fim = dur * 0.07, dur * 0.86  # abertura (classificação etária/logos) e encerramento (logo/data)
    candidatos = []
    for a, b in zip(marcas, marcas[1:]):
        a2, b2 = max(a, ini) + 0.15, min(b, fim) - 0.1
        if b2 - a2 < 1.6:
            continue
        meio = (a2 + b2) / 2
        y, s = medir_quadro(video, meio)
        if y < 38:            # escuro demais para celular
            continue
        if s < 10 and y < 70:  # cartela de texto (fundo escuro sem cor)
            continue
        # cartela ("EXPANDED PHOTO MODE"), legenda ou interface: confere começo, meio e fim do plano
        if any(tem_texto(video, x) for x in (a2 + 0.3, meio, b2 - 0.3)):
            continue
        candidatos.append({"t": round(a2, 2), "dur": round(b2 - a2, 2), "y": y, "s": s,
                           "mov": movimento(video, meio - 0.5)})
    # prioriza planos mais longos e coloridos, mantendo a ordem do trailer
    candidatos.sort(key=lambda c: -(min(c["dur"], 4) + c["s"] / 40))
    escolhidos = sorted(candidatos[:MAX_SHOTS_POR_TRAILER], key=lambda c: c["t"])
    return escolhidos


def montar_banco(jogos_info, pasta_thumbs):
    """Gera o banco de mídias {id: {...}} e as miniaturas verticais de cada uma."""
    os.makedirs(pasta_thumbs, exist_ok=True)
    banco = {}
    for j in jogos_info:
        antes = len(banco)
        loja = j.get("fonte") == "App Store"
        for i, p in enumerate(j["screens"]):
            if loja:
                # screenshot de loja: usa só a faixa sem a chamada de marketing
                faixa = faixa_sem_texto(p)
                if faixa:
                    banco[f"{j['chave']}_s{i}"] = {"tipo": "i", "jogo": j["chave"], "arquivo": os.path.basename(p),
                                                   "recorte": list(faixa)}
                continue
            if tem_texto(p, limite=4):
                continue  # screenshot de mapa/menu/legenda
            mid = f"{j['chave']}_s{i}"
            banco[mid] = {"tipo": "i", "jogo": j["chave"], "arquivo": os.path.basename(p)}
        for ti, t in enumerate(j["trailers"]):
            for c in trechos_bons(t["arquivo"]):
                mid = f"{j['chave']}_t{ti}_{int(c['t'] * 10)}"
                banco[mid] = {"tipo": "v", "jogo": j["chave"], "arquivo": os.path.basename(t["arquivo"]),
                              "t": c["t"], "dur": c["dur"], "mov": c.get("mov", 0), "sat": round(c["s"], 1)}
        if len(banco) == antes:  # tudo tinha texto (ex.: screenshots de loja com chamada): melhor isso que nada
            for i, p in enumerate(j["screens"][:6]):
                banco[f"{j['chave']}_s{i}"] = {"tipo": "i", "jogo": j["chave"], "arquivo": os.path.basename(p)}
    return banco


def sprite(banco, pasta_raw, destino, col=10, tw=108, th=192):
    """Uma imagem única com a miniatura vertical de cada mídia (a tela usa como sprite CSS)."""
    from tempfile import mkdtemp
    from PIL import Image
    tmp = mkdtemp()
    ids = list(banco)
    linhas = max(1, (len(ids) + col - 1) // col)
    folha = Image.new("RGB", (col * tw, linhas * th), (13, 16, 19))
    for k, mid in enumerate(ids):
        m = banco[mid]
        src = os.path.join(pasta_raw, m["arquivo"])
        out = os.path.join(tmp, f"{k:04d}.jpg")
        vf = f"crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale={tw}:{th}"
        if m["tipo"] == "v":
            ffmpeg("-ss", m["t"] + min(m["dur"] / 2, 1.0), "-i", src, "-frames:v", 1, "-vf", vf, "-q:v", 4, out)
        else:
            rc = m.get("recorte")
            pre = f"crop=iw:ih*{rc[1] - rc[0]:.4f}:0:ih*{rc[0]:.4f}," if rc else ""
            ffmpeg("-i", src, "-frames:v", 1, "-vf", pre + vf, "-q:v", 4, out)
        with Image.open(out) as im:
            folha.paste(im.convert("RGB").resize((tw, th)), ((k % col) * tw, (k // col) * th))
        m["sprite"] = k
    folha.save(destino, quality=80)
    return {"colunas": col, "largura": tw, "altura": th}
