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
    return melhor["id"], melhor["name"]


def baixar_jogo(nome, pasta):
    """Baixa screenshots e trailers de um jogo. Devolve dict com o que foi obtido."""
    achado = achar_jogo(nome)
    if not achado:
        print(f"aviso: '{nome}' não encontrado na Steam")
        return None
    appid, oficial = achado
    d = json.loads(_get(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=portuguese"))[str(appid)]
    if not d.get("success"):
        return None
    d = d["data"]
    chave = slug(oficial)
    info = {"chave": chave, "nome": oficial, "appid": appid, "screens": [], "trailers": []}
    os.makedirs(pasta, exist_ok=True)
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
        if not os.path.exists(p):
            try:  # cópia direta é rápida; se o contêiner não aceitar, reencoda
                ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-an", "-c:v", "copy", "-movflags", "+faststart", p)
                if duracao(p) < 3:
                    raise RuntimeError("curto")
            except Exception:
                ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-an", "-vf", "scale=-2:1080",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", p)
        info["trailers"].append({"arquivo": p, "nome": m.get("name", "")})
    print(f"{oficial}: {len(info['screens'])} imagens, {len(info['trailers'])} trailers")
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
        candidatos.append({"t": round(a2, 2), "dur": round(b2 - a2, 2), "y": y, "s": s})
    # prioriza planos mais longos e coloridos, mantendo a ordem do trailer
    candidatos.sort(key=lambda c: -(min(c["dur"], 4) + c["s"] / 40))
    escolhidos = sorted(candidatos[:MAX_SHOTS_POR_TRAILER], key=lambda c: c["t"])
    return escolhidos


def montar_banco(jogos_info, pasta_thumbs):
    """Gera o banco de mídias {id: {...}} e as miniaturas verticais de cada uma."""
    os.makedirs(pasta_thumbs, exist_ok=True)
    banco = {}
    for j in jogos_info:
        for i, p in enumerate(j["screens"]):
            if tem_texto(p, limite=4):
                continue  # screenshot de mapa/menu/legenda
            mid = f"{j['chave']}_s{i}"
            banco[mid] = {"tipo": "i", "jogo": j["chave"], "arquivo": os.path.basename(p)}
        for ti, t in enumerate(j["trailers"]):
            for c in trechos_bons(t["arquivo"]):
                mid = f"{j['chave']}_t{ti}_{int(c['t'] * 10)}"
                banco[mid] = {"tipo": "v", "jogo": j["chave"], "arquivo": os.path.basename(t["arquivo"]),
                              "t": c["t"], "dur": c["dur"]}
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
        vf = f"crop=ih*9/16:ih,scale={tw}:{th}"
        if m["tipo"] == "v":
            ffmpeg("-ss", m["t"] + min(m["dur"] / 2, 1.0), "-i", src, "-frames:v", 1, "-vf", vf, "-q:v", 4, out)
        else:
            ffmpeg("-i", src, "-frames:v", 1, "-vf", vf, "-q:v", 4, out)
        with Image.open(out) as im:
            folha.paste(im.convert("RGB").resize((tw, th)), ((k % col) * tw, (k // col) * th))
        m["sprite"] = k
    folha.save(destino, quality=80)
    return {"colunas": col, "largura": tw, "altura": th}
