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


ROMANOS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"}
GENERICAS = {"the", "of", "and", "game", "jogo", "de", "do", "da", "edition", "remastered", "remake", "definitive"}


def _marcas(nome):
    """Números/sequência e palavras que identificam o jogo ("underground", "2")."""
    ws = re.findall(r"[a-z0-9]+", re.sub(r"[^\w\s]", " ", nome.lower()))
    nums = {ROMANOS.get(w, w) for w in ws if w.isdigit() or w in ROMANOS}
    curtas = {"of", "de", "do", "da", "a", "e", "o", "s", "to", "in", "on", "no", "na"}
    chave = {w for w in ws if not w.isdigit() and w not in ROMANOS and w not in GENERICAS and
             (len(w) >= 4 or (len(w) <= 2 and w.isalpha() and w not in curtas))}
    return nums, chave


def mesmo_jogo(pedido, achado, minimo=0.5):
    """O resultado da loja é o MESMO jogo? Parecença do nome + número da sequência igual + as palavras
    que identificam o jogo presentes ("Need for Speed Underground 2" não vira "Need for Speed No Limits")."""
    if SequenceMatcher(None, norm(pedido), norm(achado)).ratio() < minimo:
        return False
    n1, k1 = _marcas(pedido)
    n2, k2 = _marcas(achado)
    if n1 != (n2 & n1) or (n2 - n1 and n1):
        return False
    return len(k1 & k2) >= max(1, round(len(k1) * 0.75))


def sigla(nome):
    """"Car Parking Multiplayer 2" -> "cpm 2" (jogo que na Steam usa só a sigla: "CPM 2")."""
    ws = re.findall(r"[a-z0-9]+", str(nome).lower())
    letras = [w for w in ws if not w.isdigit() and w not in ROMANOS]
    nums = [ROMANOS.get(w, w) for w in ws if w.isdigit() or w in ROMANOS]
    if len(letras) < 2:
        return ""
    return ("".join(w[0] for w in letras) + (" " + " ".join(nums) if nums else "")).strip()


def e_sigla(pedido, achado):
    s = sigla(pedido)
    ach = "".join(ROMANOS.get(w, w) for w in re.findall(r"[a-z0-9]+", str(achado).lower()))
    return bool(s) and ach == s.replace(" ", "")


def achar_jogo(nome, dicas=()):
    """Procura o jogo na loja da Steam e devolve (appid, nome oficial) do resultado mais parecido.
    dicas: appids de links da Steam que estavam na matéria (o jogo pode ter outro nome na loja)."""
    for appid in dicas or ():
        try:
            d = json.loads(_get(f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic"))[str(appid)]
            if d.get("success"):
                oficial = d["data"].get("name", "")
                if mesmo_jogo(nome, oficial) or e_sigla(nome, oficial) or len(dicas) == 1:
                    print(f"Steam pelo link da matéria: {nome} = {oficial} ({appid})", flush=True)
                    return int(appid), oficial
        except Exception as e:
            print(f"aviso: appid {appid} da matéria: {e}", flush=True)
    q = urllib.parse.quote(nome)
    dados = json.loads(_get(f"https://store.steampowered.com/api/storesearch/?term={q}&cc=br&l=portuguese"))
    itens = [i for i in dados.get("items", []) if i.get("type") == "app"]
    if not itens and sigla(nome):
        # nome popular x nome na loja: "Car Parking Multiplayer 2" está na Steam como "CPM 2"
        q2 = urllib.parse.quote(sigla(nome))
        d2 = json.loads(_get(f"https://store.steampowered.com/api/storesearch/?term={q2}&cc=br&l=portuguese"))
        ach = [i for i in d2.get("items", []) if i.get("type") == "app" and e_sigla(nome, i["name"])]
        if ach:
            print(f"Steam pela sigla: {nome} = {ach[0]['name']} ({ach[0]['id']})", flush=True)
            return ach[0]["id"], ach[0]["name"]
    if not itens:
        return None
    alvo = norm(nome)
    # prefere o nome mais parecido; penaliza DLCs/trilhas/edições ("soundtrack", "dlc", "pack")
    def nota(i):
        n = norm(i["name"])
        extra = 0.25 if re.search(r"soundtrack|dlc|pack|expansion|season pass|demo", i["name"], re.I) else 0
        return SequenceMatcher(None, alvo, n).ratio() - extra
    itens = [i for i in itens if mesmo_jogo(nome, i["name"])]
    if not itens:
        return None  # nada que seja este jogo na Steam (ex.: jogo de console antigo/celular): outras fontes
    melhor = max(itens, key=nota)
    if nota(melhor) < 0.5:
        return None
    return melhor["id"], melhor["name"]


def _decodifica(p):
    """O arquivo decodifica do começo ao fim sem erro? (cópia direta de stream às vezes sai corrompida:
    "Invalid NAL unit size" — aí os trechos do trailer saem vazios no render)."""
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-an", "-vf", "scale=160:-2", "-f", "null", "-"],
                       capture_output=True, text=True)
    erros = [l for l in r.stderr.splitlines() if l.strip()]
    return r.returncode == 0 and len(erros) <= 2


def tem_audio(p):
    try:
        r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", p])
        return bool(r.stdout.strip())
    except Exception:
        return False


def _baixar_trailer(src, p):
    """Baixa os primeiros TRECHO_MAX s de um trailer (cópia direta; se sair corrompida ou o contêiner não aceitar,
    reencoda). Arquivo já baixado é conferido uma vez (marca .ok ao lado)."""
    ok = p + ".ok2"  # .ok2: baixado COM o som (o YouTube longo usa o áudio do trailer); .ok antigo era sem som
    if os.path.exists(p) and os.path.exists(ok):
        return True
    if os.path.exists(p) and _decodifica(p) and tem_audio(p):
        open(ok, "w").close()
        return True
    if os.path.exists(p) and not tem_audio(p):
        os.remove(p)  # cópia antiga, sem som: baixa de novo
    if os.path.exists(p):
        print(f"aviso: {os.path.basename(p)} estava corrompido; baixando de novo reencodando", flush=True)
        os.remove(p)
        ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:1080",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
        open(ok, "w").close()
        return os.path.exists(p)
    try:
        ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-movflags", "+faststart", p)
        if duracao(p) < 3 or not _decodifica(p):
            raise RuntimeError("curto ou corrompido")
        open(ok, "w").close()
        return True
    except Exception:
        if os.path.exists(p):
            os.remove(p)
        ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:1080",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
        open(ok, "w").close()
    return os.path.exists(p)


def _steam(nome, pasta, max_trailers=MAX_TRAILERS, dicas=()):
    achado = achar_jogo(nome, dicas)
    if not achado:
        return None
    appid, oficial = achado
    if not mesmo_jogo(nome, oficial):
        oficial = nome  # achado pela sigla/link: segue com o nome do roteiro (é o que os tópicos usam)
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
    for i, m in enumerate(d.get("movies", [])[:max_trailers]):
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
    try:
        _personagens_steam(d, appid, info, pasta, oficial)
    except Exception as e:
        print(f"aviso: personagens de {oficial}: {e}", flush=True)
    return info


# ---------------------------------------------------------------- personagens (luta, heróis, DLC)

TEXTO_ROTEIRO = ""  # o pipeline põe aqui o texto do roteiro: só baixa vídeo/imagem de personagem que é citado
_FILLER = {"trailer", "traler", "en", "jp", "pt", "br", "animated", "story", "newcos", "new", "cos", "game", "official",
           "launch", "gameplay", "character", "costume", "dlc", "classic", "teaser", "reveal", "the", "pack", "edition",
           "deluxe", "bundle", "pre", "order", "bonus", "outfit", "skin", "set"}


def _palavras(t):
    import unicodedata
    t = unicodedata.normalize("NFD", str(t).lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t))


def nome_de_personagem(bruto, jogo, dlc=False):
    """'BlueMary_trailer_en' -> 'Blue Mary'; 'FATAL FURY CotW - DLC Costume "Classic Mai Shiranui"' -> 'Mai Shiranui'."""
    s = str(bruto or "")
    if dlc:
        q = re.findall(r'["“”]([^"“”]+)["“”]', s)
        s = q[-1] if q else (s.split(" - ", 1)[1] if " - " in s else s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s.replace("_", " "))
    jogo_t = set(_palavras(jogo)) | {"cotw"}
    toks = [t for t in re.findall(r"[A-Za-z0-9\-\.]+", s)]
    bons = [t for t in toks if t.lower().strip(".") not in _FILLER and t.lower().strip(".") not in jogo_t]
    nome = " ".join(bons).strip(" -.")
    return nome if len(nome.replace(" ", "")) >= 2 else ""


def citado(nome, texto):
    """O personagem é citado no texto? Todas as palavras do nome (2+ letras) aparecem; "Season Pass 3" exige o número."""
    tn = " " + " ".join(_palavras(texto)) + " "
    toks = [t for t in _palavras(nome) if len(t) >= 2 or t.isdigit()]
    if not toks:
        return False
    if not re.search(r"(season|pass|vol|temporada)", " ".join(toks)):
        toks = [t for t in toks if not t.isdigit()]
    return bool(toks) and all(f" {t} " in tn for t in toks)


def _personagens_steam(d, appid, info, pasta, oficial):
    """Vídeos e imagens de personagem/DLC citados no roteiro (o vídeo do Fatal Fury mostrava outro lutador quando
    o roteiro falava da Mai Shiranui). Marca cada arquivo com o nome em info["personagens"]."""
    texto = TEXTO_ROTEIRO
    if not texto:
        return
    chave = info["chave"]
    feitos = set()
    for i, m in enumerate(d.get("movies", [])):
        nome = nome_de_personagem(m.get("name"), oficial)
        if not nome or nome in feitos or not citado(nome, texto):
            continue
        src = None
        for k in ("mp4", "webm"):
            if m.get(k):
                src = m[k].get("max") or m[k].get("480")
                break
        src = src or m.get("hls_h264") or m.get("dash_h264")
        p = os.path.join(pasta, f"{chave}_p{i}.mp4")
        try:
            if src and _baixar_trailer(src, p):
                info["trailers"].append({"arquivo": p, "nome": m.get("name", ""), "personagem": nome})
                info.setdefault("personagens", {})[os.path.basename(p)] = nome
                feitos.add(nome)
                print(f"personagem citado: {nome} (vídeo {m.get('name')})", flush=True)
        except Exception as e:
            print(f"aviso: vídeo de {nome} falhou: {e}", flush=True)
    for dl in (d.get("dlc") or [])[:45]:
        try:
            x = json.loads(_get(f"https://store.steampowered.com/api/appdetails?appids={dl}&l=english"))[str(dl)]
        except Exception:
            continue
        if not x.get("success"):
            continue
        xd = x["data"]
        nome = nome_de_personagem(xd.get("name"), oficial, dlc=True)
        if not nome or not citado(nome, texto):
            continue
        for k, sc in enumerate(xd.get("screenshots", [])[:4]):
            p = os.path.join(pasta, f"{chave}_d{dl}_{k}.jpg")
            try:
                if not os.path.exists(p):
                    with open(p, "wb") as f:
                        f.write(_get(sc["path_full"]))
                info["screens"].append(p)
                info.setdefault("personagens", {})[os.path.basename(p)] = nome
            except Exception:
                pass
        print(f"personagem citado: {nome} (DLC {xd.get('name')}: {len(xd.get('screenshots', []))} imagens)", flush=True)


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
    if not mesmo_jogo(nome, melhor.get("trackName", ""), 0.45):
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
                 "--match-filter", "duration < 900", "-f", "bv*[height<=1080][ext=mp4]+ba/bv*[height<=1080]+ba/b[height<=1080]/bv*[height<=1080]",
                 "--download-sections", f"*0-{TRECHO_MAX}", "--no-playlist", "--quiet", "--no-warnings",
                 "-o", tmp + ".%(ext)s"], capture_output=True, text=True, timeout=240)
            baixado = next((os.path.join(pasta, x) for x in os.listdir(pasta) if x.startswith(os.path.basename(tmp))), None)
            if r.returncode != 0 or not baixado:
                print(f"aviso: YouTube não liberou o trailer de {nome}: {(r.stderr or '').strip()[-200:]}")
                break
            ffmpeg("-i", baixado, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:'min(1080,ih)'",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
            os.remove(baixado)
            saida.append({"arquivo": p, "nome": "YouTube"})
        except Exception as e:
            print(f"aviso: YouTube falhou para {nome}: {e}")
            break
    return saida


RUIM_DM = re.compile(r"review|an[aá]lise|reac|react|mod\b|speedrun|tutorial|dicas|tips|how to|unboxing|"
                     r"compara|vs\.?\b|parte \d|part \d|episod|live|stream|ost|soundtrack|music", re.I)


def titulo_do_jogo(nome, titulo):
    """O vídeo é deste jogo? Todas as palavras que identificam o jogo no título e o número da sequência."""
    n1, k1 = _marcas(nome)
    n2, k2 = _marcas(titulo)
    # número de sequência que o pedido não tem ("Silent Hill 2" quando se pediu "Silent Hill f"); ano não conta
    extra = {x for x in n2 - n1 if x.isdigit() and int(x) <= 20}
    return n1 <= n2 and k1 <= k2 and not extra


def _info_dm(vid):
    """(maior altura disponível, nome do canal) de um vídeo do Dailymotion, via yt-dlp sem baixar."""
    import subprocess
    try:
        r = subprocess.run(["yt-dlp", "-J", "--no-warnings", f"https://www.dailymotion.com/video/{vid}"],
                           capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
        alt = max([f.get("height") or 0 for f in d.get("formats", [])] + [d.get("height") or 0])
        return int(alt), d.get("uploader") or d.get("channel") or ""
    except Exception:
        return 0, ""


TENTADOS_DM = {}  # chave do jogo -> ids do Dailymotion já baixados (a 2ª rodada pega outros)


def _dailymotion(nome, chave, pasta, n=2, extra=False):
    """Trailer/gameplay no Dailymotion (busca pública + yt-dlp): funciona no servidor de render, onde o YouTube
    bloqueia. Bom para jogo de console e jogo antigo que não estão na Steam."""
    import shutil
    import subprocess
    if not shutil.which("yt-dlp"):
        return []
    tentados = TENTADOS_DM.setdefault(chave, set())
    vistos, cands = set(tentados), []
    termos = ((f"{nome} trailer", 2), (f"{nome} gameplay", 1))
    if extra:
        termos = ((f"{nome} official trailer", 3), (f"{nome} trailer 4K", 2), (f"{nome} trailer oficial", 2))
    for termo, peso in termos:
        try:
            d = json.loads(_get("https://api.dailymotion.com/videos?" + urllib.parse.urlencode(
                {"search": termo, "fields": "id,title,duration", "limit": 15, "sort": "relevance"})))
        except Exception as e:
            print(f"aviso: busca no Dailymotion falhou ({termo}): {e}")
            continue
        for k, v in enumerate(d.get("list", [])):
            t, dur = v.get("title", ""), v.get("duration") or 0
            if v["id"] in vistos or not titulo_do_jogo(nome, t) or RUIM_DM.search(t) or not 25 <= dur <= 900:
                continue
            vistos.add(v["id"])
            bonus = 2 if re.search(r"trailer", t, re.I) else (1 if re.search(r"gameplay", t, re.I) else 0)
            cands.append((-(peso + bonus) + k * 0.1, v["id"], t))
    cands.sort()
    # qualidade: consulta a resolução e o canal dos melhores candidatos; vídeo de reupload borrado (360p de
    # canal qualquer) só entra se não houver nada melhor
    avaliados = []
    for ordem, vid, titulo in cands[:10 if extra else 8]:
        alt, canal = _info_dm(vid)
        oficial = bool(re.search(r"official|oficial|rockstar|playstation|xbox|nintendo|ign|gamespot|ubisoft|"
                                 r"bandai|capcom|konami|sega|square enix|ea\b|bethesda|2k|warner", f"{canal} {titulo}", re.I))
        nota = (2 if alt >= 1080 else 1.5 if alt >= 720 else 0.5 if alt >= 480 else 0) + (1.5 if oficial else 0) - ordem * 0.2
        avaliados.append((-nota, alt, vid, titulo))
        print(f"Dailymotion candidato: {titulo[:60]} | {alt}p | {canal}", flush=True)
    avaliados.sort()
    if any(a >= 720 for _, a, _, _ in avaliados):
        avaliados = [x for x in avaliados if x[1] >= 720]
    saida = []
    for _, _alt, vid, titulo in avaliados[: n + 2]:
        if len(saida) >= n:
            break
        tentados.add(vid)
        i = len(saida)
        p = os.path.join(pasta, f"{chave}_d{i}_{vid}.mp4")  # o id no nome: cache não troca um vídeo por outro
        if os.path.exists(p):
            saida.append({"arquivo": p, "nome": titulo})
            continue
        tmp = os.path.join(pasta, f"{chave}_d{i}_{vid}.tmp")
        try:
            r = subprocess.run(["yt-dlp", f"https://www.dailymotion.com/video/{vid}", "--no-playlist", "--quiet",
                                "--no-warnings", "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*[height<=1080]/b",
                                "--download-sections", f"*0-{TRECHO_MAX}", "-o", tmp + ".%(ext)s"],
                               capture_output=True, text=True, timeout=240)
            baixado = next((os.path.join(pasta, x) for x in os.listdir(pasta) if x.startswith(os.path.basename(tmp))),
                           None)
            if r.returncode != 0 or not baixado:
                print(f"aviso: Dailymotion não baixou {titulo}: {(r.stderr or '').strip()[-160:]}")
                continue
            ffmpeg("-i", baixado, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:'min(1080,ih)'",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
            os.remove(baixado)
            print(f"Dailymotion: {titulo} ({int(duracao(p))}s baixados)", flush=True)
            saida.append({"arquivo": p, "nome": titulo})
        except Exception as e:
            print(f"aviso: Dailymotion falhou para {titulo}: {e}")
    return saida


def baixar_link_video(url, chave, pasta, i):
    """Vídeo de um link colado na tela (X/Twitter, Dailymotion, Vimeo, Reddit… tudo que o yt-dlp abre).
    Baixa os primeiros TRECHO_MAX s em até 1080p, sem áudio. Devolve o trailer ou None."""
    import hashlib
    import shutil
    import subprocess
    if not shutil.which("yt-dlp"):
        return None
    p = os.path.join(pasta, f"{chave}_l{hashlib.md5(url.encode()).hexdigest()[:8]}.mp4")
    if os.path.exists(p) and duracao(p) > 2:
        return {"arquivo": p, "nome": url}
    tmp = p[:-4] + ".tmp"
    try:
        r = subprocess.run(["yt-dlp", url, "--no-playlist", "--quiet", "--no-warnings",
                            "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*[height<=1080]/b", "--download-sections", f"*0-{TRECHO_MAX}",
                            "-o", tmp + ".%(ext)s", "--print", "after_move:%(title)s"],
                           capture_output=True, text=True, timeout=300)
        baixado = next((os.path.join(pasta, x) for x in os.listdir(pasta) if x.startswith(os.path.basename(tmp))), None)
        if r.returncode != 0 or not baixado:
            print(f"aviso: não consegui baixar o link {url}: {(r.stderr or '').strip()[-200:]}", flush=True)
            return None
        ffmpeg("-i", baixado, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:'min(1080,ih)'",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
        os.remove(baixado)
        print(f"link: {r.stdout.strip()[:80]} ({int(duracao(p))}s)", flush=True)
        return {"arquivo": p, "nome": r.stdout.strip()[:120] or url}
    except Exception as e:
        print(f"aviso: link {url} falhou: {e}", flush=True)
        return None


def ids_rockstar(url):
    """Ids dos vídeos do player da Rockstar citados numa página do site (trailers do GTA VI etc.).
    A página do trailer pedido vem primeiro."""
    try:
        html = _get(url).decode("utf-8", "replace").replace('\\"', '"')
    except Exception as e:
        print(f"aviso: não abri {url}: {e}", flush=True)
        return []
    vids = re.findall(r'\{"key":"([\w-]+)","id":\{[^}]*"all":"(\w+)"\},"player":"rockstar"', html)
    vids += [("", v) for v in re.findall(r'videoplayer\?id=(\w+)', html)]
    pedido = url.rstrip("/").rsplit("/", 1)[-1]
    vids.sort(key=lambda kv: kv[0] != pedido)
    return list(dict.fromkeys(v for _, v in vids))


def arquivo_rockstar(vid):
    """URL do maior arquivo (até 1080p) de um vídeo do player da Rockstar (JSON público do videos.rockstargames.com)."""
    for ctx in ("-context-site", "", "-context-embed"):
        try:
            d = json.loads(_get(f"https://videos.rockstargames.com/v4/{vid}/data/en_us{ctx}.json")).get("data") or {}
        except Exception:
            continue
        files = ((d.get("video") or {}).get("files")) or []
        def alt(f):
            m = re.search(r"(\d{3,4})", str(f.get("resolution") or ""))
            return int(m.group(1)) if m else 0
        bons = sorted((f for f in files if f.get("src")), key=lambda f: (alt(f) <= 1080, alt(f)), reverse=True)
        if bons:
            src = bons[0]["src"]
            return src if src.startswith("http") else "https://videos.rockstargames.com" + ("" if src.startswith("/") else "/") + src
    return None


def baixar_espalhado(src, p, janelas=18, cada=12.0):
    """Baixa um vídeo para virar banco de trechos. Vídeo curto: os primeiros TRECHO_MAX s. Vídeo longo (gameplay
    de 20+ min): janelas espalhadas pelo vídeo todo, juntas num arquivo — senão só sobra o comecinho."""
    total = 0.0
    try:
        r = run(["ffprobe", "-v", "error", "-user_agent", UA, "-show_entries", "format=duration", "-of", "csv=p=0", src])
        total = float(r.stdout.strip() or 0)
    except Exception:
        total = 0.0
    if total < TRECHO_MAX * 2:
        ffmpeg("-user_agent", UA, "-i", src, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:'min(1080,ih)'",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        partes = []
        ini, fim = total * 0.04, total * 0.96 - cada
        for k in range(janelas):
            t0 = ini + (fim - ini) * k / max(janelas - 1, 1)
            q = os.path.join(tmp, f"j{k:02d}.mp4")
            try:
                ffmpeg("-user_agent", UA, "-ss", f"{t0:.1f}", "-i", src, "-t", f"{cada:.1f}", "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-af", "apad", "-shortest",
                       "-vf", "scale=-2:'min(1080,ih)',fps=30", "-c:v", "libx264", "-preset", "veryfast", "-crf", 20,
                       "-video_track_timescale", "15360", q)
                if duracao(q) > 2:
                    partes.append(q)
            except Exception as e:
                print(f"aviso: janela {k} não baixou: {e}", flush=True)
        lista = os.path.join(tmp, "lista.txt")
        with open(lista, "w") as f:
            f.writelines(f"file '{x}'\n" for x in partes)
        ffmpeg("-f", "concat", "-safe", 0, "-i", lista, "-c", "copy", p)
    print(f"vídeo longo ({total / 60:.0f} min): {len(partes)} janelas de {cada:.0f}s espalhadas", flush=True)


def gameplay_longo(nome, chave, pasta, janelas=24, cada=10.0):
    """Vídeo longo (YouTube): uma gameplay comprida do Dailymotion (de preferência sem comentário), da qual saem
    janelas espalhadas — variedade de cenas que trailer nenhum dá para 5-10 min de vídeo."""
    import shutil
    import subprocess
    if not shutil.which("yt-dlp"):
        return None
    cands = []
    for termo in (f"{nome} gameplay no commentary", f"{nome} gameplay walkthrough", f"{nome} gameplay"):
        try:
            d = json.loads(_get("https://api.dailymotion.com/videos?" + urllib.parse.urlencode(
                {"search": termo, "fields": "id,title,duration", "limit": 15, "sort": "relevance"})))
        except Exception as e:
            print(f"aviso: busca de gameplay falhou ({termo}): {e}", flush=True)
            continue
        for k, v in enumerate(d.get("list", [])):
            t, dur = v.get("title", ""), v.get("duration") or 0
            if not titulo_do_jogo(nome, t) or RUIM_DM.search(t) or not 600 <= dur <= 4 * 3600:
                continue
            if any(c[1] == v["id"] for c in cands):
                continue
            bonus = (2 if re.search(r"no commentary|sem coment|nocommentary", t, re.I) else 0) + \
                (1 if re.search(r"4k|1080|pc|max settings|ultra", t, re.I) else 0) - \
                (2 if re.search(r"live|stream|react|facecam|let'?s play", t, re.I) else 0)
            cands.append((-bonus + k * 0.1, v["id"], t, dur))
    cands.sort()
    for _, vid, titulo, dur in cands[:5]:
        p = os.path.join(pasta, f"{chave}_g_{vid}.mp4")
        if os.path.exists(p):
            return {"arquivo": p, "nome": titulo}
        alt, _canal = _info_dm(vid)
        if alt and alt < 720:
            continue
        try:
            r = subprocess.run(["yt-dlp", "-g", "--no-warnings", "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*[height<=1080]/b",
                                f"https://www.dailymotion.com/video/{vid}"], capture_output=True, text=True, timeout=90)
            url = (r.stdout or "").strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
            if not url:
                continue
            baixar_espalhado(url, p, janelas=janelas, cada=cada)
            if os.path.exists(p) and duracao(p) > 60:
                print(f"gameplay longa: {titulo} ({dur // 60} min) → {int(duracao(p))}s em janelas", flush=True)
                return {"arquivo": p, "nome": titulo}
        except Exception as e:
            print(f"aviso: gameplay {titulo}: {e}", flush=True)
    return None


def baixar_rockstar(url, chave, pasta, limite=4):
    """Trailers oficiais do site da Rockstar (o player deles não é aceito pelo yt-dlp)."""
    saida = []
    for vid in ids_rockstar(url)[:limite]:
        p = os.path.join(pasta, f"{chave}_r{vid}_e.mp4")  # _e: versão com janelas espalhadas
        try:
            if not (os.path.exists(p) and duracao(p) > 2):
                src = arquivo_rockstar(vid)
                if not src:
                    print(f"aviso: vídeo {vid} da Rockstar sem arquivo público", flush=True)
                    continue
                baixar_espalhado(src, p)
            if duracao(p) > 2:
                saida.append({"arquivo": p, "nome": f"Rockstar {vid}"})
        except Exception as e:
            print(f"aviso: vídeo {vid} da Rockstar falhou: {e}", flush=True)
    print(f"Rockstar {url}: {len(saida)} trailers oficiais", flush=True)
    return saida


def videos_do_site(url, limite=14):
    """Site oficial do jogo que não é player de vídeo (ex.: rockstargames.com/VI): procura os clipes .mp4 que a
    própria página carrega (no HTML e nos scripts do mesmo site). Pega a versão até 1920 px de cada clipe."""
    from urllib.parse import urljoin, urlparse
    try:
        html = _get(url).decode("utf-8", "replace")
    except Exception as e:
        print(f"aviso: não abri {url}: {e}", flush=True)
        return []
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    textos = [html]
    for js in sorted(set(re.findall(r'(?:src|href)="([^"]+\.js)"', html)))[:80]:
        alvo = urljoin(url, js)
        if urlparse(alvo).netloc != urlparse(url).netloc:
            continue
        try:
            textos.append(_get(alvo).decode("utf-8", "replace"))
        except Exception:
            continue
    achados = []
    for t in textos:
        for m in re.findall(r'["\'(]((?:https?://[^"\'()\s]+|/[^"\'()\s]+)\.mp4)["\')]', t):
            if m not in achados:
                achados.append(m)
    def largura(u):
        n = re.match(r"(\d{3,4})", os.path.basename(u))
        return int(n.group(1)) if n else 0
    # cada clipe vem em várias larguras (450, 768, 1280, 1920, 2160…): fica a de 1920 (ou 1080/1440 no vertical);
    # AV1 fica de fora (decodificar é lento)
    bons = [u for u in achados if "_av1" not in u and largura(u) in (1920, 1080, 1440)]
    if not bons:
        bons = [u for u in achados if "_av1" not in u]
    if len(bons) > limite:  # espalha pela lista (clipes de partes diferentes do site)
        passo = len(bons) / limite
        bons = [bons[int(i * passo)] for i in range(limite)]
    return [urljoin(base, u) for u in bons]


def baixar_videos_site(url, chave, pasta, limite=14):
    """Baixa os clipes achados por videos_do_site como trailers (no máximo TRECHO_MAX s cada)."""
    import hashlib
    saida = []
    for u in videos_do_site(url, limite):
        p = os.path.join(pasta, f"{chave}_w{hashlib.md5(u.encode()).hexdigest()[:8]}.mp4")
        try:
            if not (os.path.exists(p) and duracao(p) > 1):
                ffmpeg("-user_agent", UA, "-i", u, "-t", TRECHO_MAX, "-c:a", "aac", "-b:a", "128k", "-ac", 2, "-ar", 48000, "-vf", "scale=-2:'min(1080,ih)'",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, p)
            if duracao(p) > 1:
                saida.append({"arquivo": p, "nome": u.rsplit("/", 1)[-1]})
        except Exception as e:
            print(f"aviso: clipe {u} não baixou: {e}", flush=True)
    print(f"site {url}: {len(saida)} clipes oficiais", flush=True)
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


def baixar_jogo(nome, pasta, max_trailers=MAX_TRAILERS, dicas=()):
    """Material oficial do jogo: Steam; se não tiver (ou não tiver trailer), App Store e YouTube.

    max_trailers: jogo principal leva 3; jogo citado de passagem leva menos (cada trailer custa ~1 min de análise).
    """
    os.makedirs(pasta, exist_ok=True)
    info = None
    try:
        info = _steam(nome, pasta, max_trailers, dicas)
    except Exception as e:
        print(f"aviso: Steam falhou para '{nome}': {e}")
    if not info:
        try:
            info = _app_store(nome, pasta)
        except Exception as e:
            print(f"aviso: App Store falhou para '{nome}': {e}")
    if not info:
        info = {"chave": slug(nome), "nome": nome, "appid": None, "fonte": "Dailymotion", "screens": [], "trailers": []}
    if not info["trailers"]:
        # sem trailer na Steam (console, jogo antigo, celular): Dailymotion; o YouTube costuma bloquear o servidor
        info["trailers"] = _dailymotion(nome, info["chave"], pasta, n=min(2, max_trailers))
    if not info["trailers"]:
        info["trailers"] = _youtube(info["nome"], info["chave"], pasta, n=min(2, max_trailers))
    if not info["screens"] and not info["trailers"]:
        print(f"aviso: nenhum material encontrado para '{nome}'")
        return None
    print(f"{info['nome']} ({info['fonte']}): {len(info['screens'])} imagens, {len(info['trailers'])} trailers")
    return info


class Quadros:
    """Quadros do recorte central (9:16, 720 px) a 4 por segundo, extraídos no MESMO passe da detecção de
    cortes. Evita abrir o trailer em 1080p de novo para cada checagem de texto (era o grosso do tempo)."""
    FPS = 4

    def __init__(self, video, pasta):
        import subprocess
        self.pasta = pasta
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-filter_complex",
                            "[0:v]split[a][b];[a]scale=320:-2,select='gt(scene,0.32)',showinfo,nullsink;"
                            f"[b]fps={self.FPS},crop='min(iw,ih*9/16)':ih,scale=720:-2[q]",
                            "-map", "[q]", "-q:v", "3", os.path.join(pasta, "q%05d.jpg")],
                           capture_output=True, text=True)
        self.cortes = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
        self.n = len([f for f in os.listdir(pasta) if f.startswith("q")])

    def em(self, t):
        """Caminho do quadro mais próximo de t (o quadro k cobre ~ k/FPS)."""
        if not self.n:
            return None
        k = min(self.n, max(1, int(round(t * self.FPS)) + 1))
        return os.path.join(self.pasta, f"q{k:05d}.jpg")


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


def _baixar_east():
    """Baixa o modelo EAST uma vez (arquivo temporário por processo: vários processos ao mesmo tempo não
    se atropelam — foi o que deixou trailers sem o filtro de texto)."""
    caminho = os.path.expanduser("~/.cache/narrado/east.pb")
    if not os.path.exists(caminho) or os.path.getsize(caminho) < 1_000_000:
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        tmp = f"{caminho}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            f.write(_get(EAST_URL, timeout=180))
        os.replace(tmp, caminho)
    return caminho


def _rede_east():
    """Detector de texto EAST (OpenCV). Acha cartelas estilizadas que o Tesseract não consegue ler."""
    if _east:
        return _east[0]
    rede = None
    try:
        import cv2
        rede = cv2.dnn.readNet(_baixar_east())
    except Exception as e:  # sem OpenCV/modelo: fica só o Tesseract
        print(f"aviso: detector EAST indisponível ({e})")
    _east.append(rede)
    return rede


def _mapa_east(png):
    """Mapa booleano (células de 4 px na imagem reduzida) do que o EAST marcou como texto."""
    rede = _rede_east()
    if rede is None:
        return None
    import cv2
    img = cv2.imread(png)
    if img is None:
        return None
    h, w = img.shape[:2]
    nw = 384
    nh = max(32, int(round(h * nw / w / 32)) * 32)
    blob = cv2.dnn.blobFromImage(cv2.resize(img, (nw, nh)), 1.0, (nw, nh), (123.68, 116.78, 103.94), True, False)
    rede.setInput(blob)
    return rede.forward("feature_fusion/Conv_7/Sigmoid")[0, 0] >= 0.8


def _quadro_central(origem, t, destino):
    """Extrai o quadro no recorte que aparece no vídeo (centro 9:16), 720 px de largura."""
    import subprocess
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if t is not None:
        args += ["-ss", f"{t:.2f}"]
    args += ["-i", origem, "-frames:v", "1", "-vf", "crop='min(iw,ih*9/16)':ih,scale=720:-2", destino]
    return subprocess.run(args).returncode == 0 and os.path.exists(destino)


def mascara_persistente(video, ini, fim, amostras=12, quadros=None):
    """Texto que fica no MESMO lugar o trailer todo (marca d'água "Captured on PS5", classificação, HUD fixo).
    Devolve o mapa dessas células (para serem ignoradas) ou None."""
    import tempfile
    import numpy as np
    mapas = []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(amostras):
            t = ini + (fim - ini) * (i + 0.5) / amostras
            png = quadros.em(t) if quadros else os.path.join(tmp, f"m{i}.png")
            if (png and os.path.exists(png)) if quadros else _quadro_central(video, t, png):
                m = _mapa_east(png)
                if m is not None:
                    mapas.append(m)
    if len(mapas) < 6 or len({m.shape for m in mapas}) != 1:
        return None
    freq = np.mean(mapas, axis=0)
    masc = freq >= 0.5
    if not masc.any():
        return None
    # margem de 2 células em volta (o texto treme um pouco com a compressão)
    from numpy.lib.stride_tricks import sliding_window_view
    pad = np.pad(masc, 2)
    masc = sliding_window_view(pad, (5, 5)).any(axis=(2, 3))
    return masc


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


def tem_texto(origem, t=None, limite=2, mascara=None, ocr=True, pronto=False):
    """True se a parte que aparece no vídeo (centro 9:16) tiver texto: cartela de trailer, legenda, menu.

    Combina o detector EAST (acha a região de texto, mesmo estilizado) com uma leitura do Tesseract.
    """
    import shutil
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cor = os.path.join(tmp, "q.png")
        if pronto:  # já é o quadro do recorte central (Quadros)
            cor = origem
        else:
            args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
            if t is not None:
                args += ["-ss", f"{t:.2f}"]
            # mesmo recorte do render: centro vertical 9:16
            args += ["-i", origem, "-frames:v", "1", "-vf", "crop='min(iw,ih*9/16)':ih,scale=720:-2", cor]
            if subprocess.run(args).returncode != 0 or not os.path.exists(cor):
                return False
        if mascara is not None:
            # trailer com marca d'água fixa: só conta texto FORA dela; o Tesseract leria a marca d'água
            m = _mapa_east(cor)
            if m is not None and m.shape == mascara.shape:
                return float((m & ~mascara).mean()) >= 0.0018 * limite / 2
        if _fracao_texto_east(cor) >= 0.0018 * limite / 2:
            return True
        if not ocr or not shutil.which("tesseract"):
            return False
        # legenda/letreiro comum: uma leitura do Tesseract, contando só palavras lidas com segurança
        cinza = os.path.join(tmp, "g.png")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", cor, "-vf", "format=gray", cinza])
        r = subprocess.run(["tesseract", cinza, "-", "--psm", "11", "-l", "eng", "tsv"], capture_output=True, text=True,
                           env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        fortes, grandes = _palavras_fortes(r.stdout)
        if fortes >= limite or grandes >= 1:
            return True
    return False


NITIDEZ_MIN = 18  # variância do Laplaciano no quadro de 720 px: reupload borrado fica em 2-6; trailer oficial >100


def nitidez(png):
    """Quão nítido é o quadro (variância do Laplaciano). Baixa = borrado/esticado de baixa resolução."""
    try:
        import cv2
        im = cv2.imread(png, 0)
        if im is None:
            return None
        h = im.shape[0]
        im = im[int(h * 0.05):int(h * 0.75)]  # sem as bordas (tarjas, marca d'água no rodapé)
        return float(cv2.Laplacian(im, cv2.CV_64F).var())
    except Exception:
        return None


def _avaliar(video, a2, b2, conta, checar_pontas=True, mascara=None, quadros=None):
    """Trecho [a2, b2] serve como cena? Devolve o candidato ou None (e conta o motivo)."""
    meio = (a2 + b2) / 2
    y, s = medir_quadro(video, meio)
    if y < 38:            # escuro demais para celular
        conta["escuro"] += 1
        return None
    if s < 10 and y < 50:  # cartela de texto (fundo escuro sem cor); cena noturna dessaturada passa
        conta["cartela"] += 1
        return None
    # EAST (rápido) no meio e nas pontas; a leitura do Tesseract (lenta) só no meio, por último
    if quadros is not None:
        q = quadros.em(meio)
        nit = nitidez(q) if q and os.path.exists(q) else None
        if nit is not None and nit < NITIDEZ_MIN:
            conta["borrado"] = conta.get("borrado", 0) + 1
            return None
    pontos = (meio, a2 + 0.3, b2 - 0.3) if checar_pontas else (meio,)

    def texto(x, ocr):
        q = quadros.em(x) if quadros else None
        if q and os.path.exists(q):
            return tem_texto(q, mascara=mascara, ocr=ocr, pronto=True)
        return tem_texto(video, x, mascara=mascara, ocr=ocr)
    if any(texto(x, False) for x in pontos) or (mascara is None and texto(meio, True)):
        conta["texto"] += 1
        return None
    return {"t": round(a2, 2), "dur": round(b2 - a2, 2), "y": y, "s": s, "mov": movimento(video, meio - 0.5)}


def trechos_bons(video):
    """Escolhe trechos de trailer que funcionam como cena, fora de abertura/encerramento.

    1º: planos inteiros (entre cortes) com 1,2 s ou mais. Trailer de edição muito rápida (corrida, ação)
    quase não tem plano tão longo: aí completa com janelas de 1,8 s ao longo do trailer — cortes rápidos
    dentro de um trecho ficam naturais no vídeo.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        return _trechos_bons(video, Quadros(video, tmp))


def _trechos_bons(video, quadros):
    dur = duracao(video)
    marcas = [0.0] + quadros.cortes + [dur]
    # abertura (classificação etária, logos de estúdio — "20th Century Fox" leva ~8 s) e encerramento (logo/data)
    ini, fim = (dur * 0.07 if dur < 60 else max(dur * 0.07, 9.0)), dur * 0.86
    conta = {"escuro": 0, "cartela": 0, "texto": 0}
    mascara = mascara_persistente(video, ini, fim, quadros=quadros) if fim - ini > 6 else None
    candidatos = []
    planos = [(max(a, ini) + 0.1, min(b, fim) - 0.08) for a, b in zip(marcas, marcas[1:])]
    # avalia os planos mais longos primeiro e para quando já tem o bastante (a avaliação com detector de
    # texto é a parte cara; os planos longos são justamente os preferidos no fim)
    planos = sorted((p for p in planos if p[1] - p[0] >= 1.2), key=lambda p: -(p[1] - p[0]))
    for a2, b2 in planos:
        if len(candidatos) >= int(MAX_SHOTS_POR_TRAILER * 1.5):
            break
        c = _avaliar(video, a2, b2, conta, mascara=mascara, quadros=quadros)
        if c:
            candidatos.append(c)
    n_planos = len(candidatos)
    if len(candidatos) < 6 and fim - ini > 4:
        t = ini
        while t + 1.8 <= fim and len(candidatos) < MAX_SHOTS_POR_TRAILER:
            if all(abs(t - c["t"]) > 1.8 for c in candidatos):
                c = _avaliar(video, t, t + 1.8, conta, checar_pontas=False, mascara=mascara, quadros=quadros)
                if c:
                    candidatos.append(c)
            t += 3.0
    print(f"trailer {os.path.basename(video)}: {dur:.0f}s, {len(marcas) - 1} planos, {n_planos} planos bons, "
          f"{len(candidatos) - n_planos} janelas; descartes {conta}; marca d'água fixa: {mascara is not None}",
          flush=True)
    # prioriza trechos mais longos e coloridos, mantendo a ordem do trailer
    candidatos.sort(key=lambda c: -(min(c["dur"], 4) + c["s"] / 40))
    return sorted(candidatos[:MAX_SHOTS_POR_TRAILER], key=lambda c: c["t"])


def _iniciar_processo(threads):
    try:
        import cv2
        cv2.setNumThreads(threads)
    except Exception:
        pass


def _analisar_trailer(arquivo):
    return arquivo, trechos_bons(arquivo)


def segunda_rodada(jogos_info, banco, pasta, assuntos):
    """Jogo que é assunto de algum tópico e ficou sem nenhum trecho de vídeo (os achados eram borrados):
    busca de novo no Dailymotion (outros termos, outros vídeos) e analisa só esses."""
    novos = []
    for j in jogos_info:
        if not any(norm(a) == norm(j["nome"]) or mesmo_jogo(a, j["nome"], 0.4) for a in assuntos):
            continue
        if any(m["tipo"] == "v" and m["jogo"] == j["chave"] for m in banco.values()):
            continue
        extra = _dailymotion(j["nome"], j["chave"], pasta, n=3, extra=True)
        if extra:
            print(f"{j['nome']}: sem vídeo bom; 2ª busca trouxe {len(extra)} vídeos", flush=True)
            novos.append({**j, "screens": [], "trailers": extra, "_base": len(j["trailers"])})
            j["trailers"] = j["trailers"] + extra
    if not novos:
        return banco
    for nj in novos:
        for ti, t in enumerate(nj["trailers"]):
            for c in trechos_bons(t["arquivo"]):
                mid = f"{nj['chave']}_t{nj['_base'] + ti}_{int(c['t'] * 10)}"
                banco[mid] = {"tipo": "v", "jogo": nj["chave"], "arquivo": os.path.basename(t["arquivo"]),
                              "t": c["t"], "dur": c["dur"], "mov": c.get("mov", 0), "sat": round(c["s"], 1)}
    return banco


def montar_banco(jogos_info, pasta_thumbs, progresso=None, ti0=0):
    """Gera o banco de mídias {id: {...}} e as miniaturas verticais de cada uma.

    Os trailers são analisados em paralelo (um processo por núcleo; cada um carrega a sua rede EAST).
    progresso(feitos, total) é chamado a cada trailer concluído.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed
    os.makedirs(pasta_thumbs, exist_ok=True)
    arquivos = [t["arquivo"] for j in jogos_info for t in j["trailers"]]
    analise = {}
    if arquivos:
        nucleos = os.cpu_count() or 2
        n = max(1, min(len(arquivos), nucleos))
        with ProcessPoolExecutor(max_workers=n, initializer=_iniciar_processo,
                                 initargs=(max(1, nucleos // n),)) as ex:
            futuros = [ex.submit(_analisar_trailer, a) for a in arquivos]
            for k, f in enumerate(as_completed(futuros), 1):
                try:
                    a, trechos = f.result()
                    analise[a] = trechos
                except Exception as e:
                    print(f"aviso: análise de trailer falhou: {e}", flush=True)
                if progresso:
                    progresso(k, len(arquivos))
    banco = {}
    for j in jogos_info:
        antes = len(banco)
        loja = j.get("fonte") == "App Store"
        pers = j.get("personagens") or {}
        for i, p in enumerate(j["screens"]):
            if os.path.basename(p) in pers:  # arte oficial do personagem/DLC citado: entra sempre
                banco[f"{j['chave']}_s{i}"] = {"tipo": "i", "jogo": j["chave"], "arquivo": os.path.basename(p),
                                               "personagem": pers[os.path.basename(p)], "encaixar": True}
                continue
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
            for c in analise.get(t["arquivo"], []):
                mid = f"{j['chave']}_t{ti + ti0}_{int(c['t'] * 10)}"
                banco[mid] = {"tipo": "v", "jogo": j["chave"], "arquivo": os.path.basename(t["arquivo"]),
                              "t": c["t"], "dur": c["dur"], "mov": c.get("mov", 0), "sat": round(c["s"], 1),
                              **({"personagem": t["personagem"]} if t.get("personagem") else {})}
        if len(banco) == antes:  # tudo tinha texto (ex.: screenshots de loja com chamada): melhor isso que nada
            for i, p in enumerate(j["screens"][:6]):
                banco[f"{j['chave']}_s{i}"] = {"tipo": "i", "jogo": j["chave"], "arquivo": os.path.basename(p)}
    return banco


def trechos_corridos(video):
    """Trailer para tocar CORRIDO (YouTube): todos os planos, na ordem, menos os que não podem ir ao ar —
    logos de estúdio/classificação do começo, cartelas e telas com texto (aviso de violência, menu, anúncio,
    datas), tela preta. O fim do trailer (logo do jogo) é usado à parte pelo render, no fecho do capítulo.

    Um passe só no trailer: detecção de cortes + quadros da tela inteira a 2 por segundo."""
    import subprocess
    import tempfile
    from PIL import Image
    dur = duracao(video)
    if dur < 6:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", video, "-filter_complex",
                            "[0:v]split[a][b];[a]scale=320:-2,select='gt(scene,0.30)',showinfo,nullsink;"
                            "[b]fps=2,scale=640:-2[q]", "-map", "[q]", "-q:v", "4", os.path.join(tmp, "q%05d.jpg")],
                           capture_output=True, text=True)
        cortes = [float(x) for x in re.findall(r"pts_time:([\d.]+)", r.stderr)]
        n = len([f for f in os.listdir(tmp) if f.startswith("q")])

        def quadro(t):
            k = min(max(int(t * 2) + 1, 1), max(n, 1))
            q = os.path.join(tmp, f"q{k:05d}.jpg")
            return q if os.path.exists(q) else None

        def ruim(t):
            q = quadro(t)
            if not q:
                return "sem quadro"
            im = Image.open(q).convert("L")
            h = im.height
            meio = im.crop((0, int(h * 0.15), im.width, int(h * 0.85)))
            # preto de verdade (transição, tela de carregamento): nem os pontos mais claros aparecem.
            # Jogo escuro (terror) com luz em parte da cena passa.
            hist = meio.histogram()
            tot, acum, p95 = sum(hist), 0, 0
            for v, c in enumerate(hist):
                acum += c
                if acum >= tot * 0.95:
                    p95 = v
                    break
            if p95 < 16:
                return "preto"
            if _rede_east() is None:  # sem o EAST: leitura do Tesseract (mais lenta, mas não deixa cartela passar)
                r = subprocess.run(["tesseract", q, "-", "--psm", "11", "-l", "eng", "tsv"], capture_output=True,
                                   text=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"})
                fortes, grandes = _palavras_fortes(r.stdout)
                return "texto" if fortes >= 2 or grandes >= 1 else None
            if _fracao_texto_east(q) >= 0.0022:
                return "texto"
            return None

        # logos de estúdio/distribuidora (20th Century Fox, SEGA…) costumam vir nos 10 primeiros segundos
        ini = dur * 0.06 if dur < 60 else max(dur * 0.12, 14.0)
        fim = dur * 0.92
        marcas = [0.0] + cortes + [dur]
        bons, conta, n_planos = [], {}, 0
        for a, b in zip(marcas, marcas[1:]):
            a, b = max(a, ini) + 0.05, min(b, fim) - 0.05
            if b - a < 0.6:
                continue
            n_planos += 1
            # o plano é avaliado a cada 0,5 s: só o pedaço ruim sai (cartela que aparece no meio, fade no preto)
            pts = [a + 0.25 + 0.5 * i for i in range(max(1, int((b - a) / 0.5)))]
            run_ini = None
            for t in pts + [None]:
                motivo = ruim(t) if t is not None else "fim"
                if motivo is None:
                    if run_ini is None:
                        run_ini = max(a, t - 0.25)
                    continue
                if t is not None:
                    conta[motivo] = conta.get(motivo, 0) + 1
                if run_ini is not None:
                    fim_run = min(b, (t - 0.25) if t is not None else b)
                    if fim_run - run_ini >= 1.0:
                        bons.append({"t": round(run_ini, 2), "dur": round(fim_run - run_ini, 2)})
                    run_ini = None
        planos = range(n_planos)
    print(f"trailer corrido {os.path.basename(video)}: {dur:.0f}s, {len(planos)} planos, "
          f"{sum(x['dur'] for x in bons):.0f}s aproveitáveis; descartes {conta}", flush=True)
    return bons


def _analisar_corrido(arquivo):
    return arquivo, trechos_corridos(arquivo)


def banco_corrido(jogos_info, pasta_thumbs, progresso=None, ti0=0):
    """Banco do vídeo longo "trailer corrido": os planos aproveitáveis de cada trailer, na ordem (o render toca
    em sequência, com o som). Screenshots passam pelo filtro de sempre."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    # a análise em processos vem ANTES de qualquer uso do OpenCV aqui no processo principal: processo filho
    # criado depois que a rede EAST já rodou no pai trava (foi o que segurou o teste por 5 h)
    arquivos = [t["arquivo"] for j in jogos_info for t in j["trailers"]]
    analise = {}
    if arquivos:
        try:
            _baixar_east()  # antes dos processos: todos usam o mesmo arquivo pronto
        except Exception as e:
            print(f"aviso: modelo EAST não baixou: {e}", flush=True)
        nucleos = os.cpu_count() or 2
        import multiprocessing
        # "spawn": processo novo e limpo (fork depois do OpenCV/ONNX carregado no pai pode travar para sempre)
        with ProcessPoolExecutor(max_workers=max(1, min(len(arquivos), nucleos)), initializer=_iniciar_processo,
                                 initargs=(1,), mp_context=multiprocessing.get_context("spawn")) as ex:
            futuros = [ex.submit(_analisar_corrido, a) for a in arquivos]
            try:
                for k, f in enumerate(as_completed(futuros, timeout=240 + 90 * len(arquivos)), 1):
                    try:
                        a, trechos = f.result()
                        analise[a] = trechos
                    except Exception as e:
                        print(f"aviso: análise de trailer falhou: {e}", flush=True)
                    if progresso:
                        progresso(k, len(arquivos))
            except Exception as e:  # nunca mais segurar o servidor: segue com o que já foi analisado
                print(f"aviso: análise dos trailers passou do tempo ({e}); seguindo com {len(analise)}", flush=True)
                for f in futuros:
                    f.cancel()
                for p in list(getattr(ex, "_processes", {}).values()):
                    p.kill()
    so_telas = [dict(j, trailers=[]) for j in jogos_info]
    banco = montar_banco(so_telas, pasta_thumbs)
    for j in jogos_info:
        for ti, t in enumerate(j["trailers"]):
            for c in analise.get(t["arquivo"], []):
                banco[f"{j['chave']}_t{ti + ti0}_{int(c['t'] * 10)}"] = {
                    "tipo": "v", "jogo": j["chave"], "arquivo": os.path.basename(t["arquivo"]),
                    "t": c["t"], "dur": c["dur"], "corrido": True,
                    **({"personagem": t["personagem"]} if t.get("personagem") else {})}
    return banco


def segundos_de_video(banco, chave):
    return sum(m.get("dur", 0) for m in banco.values() if m.get("jogo") == chave and m["tipo"] == "v")


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


# ---------------------------------------------------------------- Wikimedia Commons (licença livre)

COMMONS_UA = "BrutoNarrado/1.0 (https://bruto-nu.vercel.app; github.com/daniloalanm-hash)"
_RUIM_COMMONS = re.compile(r"logo|icon|map|flag|diagram|chart|screenshot|svg|signature|coat.of.arms|seal|"
                           r"wordmark|banner|button|symbol|motherboard|teardown|disassembl|pcb|inside|walk.of.fame|star.on|"
                           r"hollywood.star|grave|tomb|plaque|statue|autograph|wax|museum|sign\b|poster|"
                           r"\.pdf|\.tif", re.I)
_GENERICAS = {"console", "the", "de", "do", "da", "of", "photo", "foto", "headquarters", "sede", "office", "logo",
              "and", "e", "game", "games", "event", "evento"}


def _fundo_liso(arq):
    """Foto de produto (fundo branco/liso): mostra inteira sobre fundo desfocado em vez de recortar."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(arq) as im:
            a = np.asarray(im.convert("L").resize((200, 120)), dtype=float)
        borda = np.concatenate([a[:6].ravel(), a[-6:].ravel(), a[:, :6].ravel(), a[:, -6:].ravel()])
        return bool(borda.std() < 12 and (borda.mean() > 225 or borda.mean() < 25))
    except Exception:
        return False


def baixar_commons(entrada, pasta):
    """Re-render sem cache: baixa de novo a foto do Commons pela URL guardada no banco."""
    import urllib.request
    destino = os.path.join(pasta, entrada["arquivo"])
    if os.path.exists(destino) or not entrada.get("url"):
        return
    bruto = destino + ".orig"
    req = urllib.request.Request(entrada["url"], headers={"User-Agent": COMMONS_UA})
    with urllib.request.urlopen(req, timeout=40) as r, open(bruto, "wb") as f:
        f.write(r.read())
    ffmpeg("-i", bruto, "-frames:v", 1, "-q:v", 2, destino)
    os.remove(bruto)


# console falado de um jeito ("PS1", "Mega Drive") e buscado de outro: foto clássica de cada um (títulos do
# Commons conferidos em tempo de execução; se não existir mais, cai na busca normal)
CONSOLES = [
    (r"\b(ps ?1|ps ?one|psx|playstation ?1|playstation one|primeiro playstation)\b|^playstation$",
     ["File:PSX-Console-wController.jpg", "File:PlayStation-SCPH-1000-with-Controller.jpg"], "PlayStation 1"),
    (r"\b(ps ?2|playstation ?2)\b", ["File:PS2-Fat-Console-Set.jpg", "File:PS2-Versions.jpg"], "PlayStation 2"),
    (r"\b(ps ?3|playstation ?3)\b", ["File:Sony-PlayStation-3-2001A-wController-L.jpg"], "PlayStation 3"),
    (r"\b(ps ?4|playstation ?4)\b", ["File:PS4-Console-wDS4.jpg"], "PlayStation 4"),
    (r"\b(ps ?5|playstation ?5)\b", ["File:PlayStation 5 and DualSense with transparent background.png"], "PlayStation 5"),
    (r"\b(psp)\b", ["File:Psp-1000.jpg"], "PSP"),
    (r"\b(ps vita|playstation vita)\b", ["File:PlayStation-Vita-1101-FL.jpg"], "PS Vita"),
    (r"\b(mega ?drive|genesis)\b", ["File:Sega-Genesis-Mod1-Set.jpg", "File:Sega-Mega-Drive-JP-Mk1-Console-Set.jpg"], "Mega Drive"),
    (r"\b(dreamcast)\b", ["File:Dreamcast-Console-Set.jpg"], "Dreamcast"),
    (r"\b(saturn)\b", ["File:Sega-Saturn-Console-Set-Mk1.jpg"], "Sega Saturn"),
    (r"\b(super nintendo|snes)\b", ["File:SNES-Mod1-Console-Set.jpg"], "Super Nintendo"),
    (r"\b(nintendo 64|n64)\b", ["File:N64-Console-Set.jpg"], "Nintendo 64"),
    (r"\b(gamecube)\b", ["File:GameCube-Set.jpg"], "GameCube"),
    (r"\b(nes|nintendinho)\b", ["File:NES-Console-Set.jpg"], "NES"),
    (r"\bwii u\b", ["File:Wii U Console and Gamepad.png"], "Wii U"),
    (r"\bwii\b", ["File:Wii-console.jpg"], "Wii"),
    (r"\b(game boy)\b", ["File:Game-Boy-FL.jpg"], "Game Boy"),
    (r"\b(xbox 360)\b", ["File:Xbox-360-Pro-wController.jpg"], "Xbox 360"),
    (r"\b(xbox one)\b", ["File:Microsoft-Xbox-One-Console-wController-FL.jpg"], "Xbox One"),
    (r"^(xbox|xbox original|primeiro xbox)$", ["File:Xbox-console.jpg"], "Xbox"),
]


def console_do_termo(termo, texto=""):
    """(títulos, nome) da foto clássica do console citado no termo — ou no texto falado do tópico."""
    alvos = [str(termo or "").lower().strip()]
    if re.fullmatch(r"(sony )?playstation|xbox|microsoft xbox|nintendo|sega|console", alvos[0]):
        alvos.append(str(texto or "").lower())  # marca genérica: o texto falado diz qual console
    for alvo in alvos:
        for rx, titulos, nome in CONSOLES:
            if alvo and re.search(rx, alvo):
                return titulos, nome
    return None


def _commons_por_titulo(titulos, pasta, largura=1600):
    import json as _json
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode({"action": "query", "format": "json", "titles": "|".join(titulos),
                                "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime", "iiurlwidth": largura})
    req = urllib.request.Request(f"https://commons.wikimedia.org/w/api.php?{q}", headers={"User-Agent": COMMONS_UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = _json.loads(r.read())
    pags = {p.get("title"): p for p in (d.get("query") or {}).get("pages", {}).values() if p.get("imageinfo")}
    return [pags[t] for t in titulos if t in pags]


def imagens_commons(termo, pasta, n=3, largura=1600, texto=""):
    """Fotos com licença livre do Wikimedia Commons (console, pessoa, empresa, evento).
    Devolve entradas de banco com autor/licença para crédito."""
    import hashlib
    import json as _json
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrsearch": f"{termo} filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": largura})
    req = urllib.request.Request(f"https://commons.wikimedia.org/w/api.php?{q}", headers={"User-Agent": COMMONS_UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = _json.loads(r.read())
    console = console_do_termo(termo, texto)
    if console:
        try:
            fixas = _commons_por_titulo(console[0], pasta, largura)
        except Exception:
            fixas = []
        if fixas:  # foto clássica do console certo: dispensa a busca (que trazia PSP/câmera para "PS1")
            d = {"query": {"pages": {str(i): dict(p, index=i) for i, p in enumerate(fixas)}}}
            termo = " ".join(re.findall(r"[a-z0-9]+", fixas[0]["title"].lower().replace("file:", ""))[:3])
        else:
            termo = console[1]  # sem a foto clássica: busca pelo nome completo (nada de console errado)
    palavras = [w for w in re.findall(r"[a-z0-9]+", termo.lower()) if w not in _GENERICAS]

    def extras(p):  # arquivo cujo nome é só o que foi pedido ("Xbox Series X.png") vem primeiro
        tok = set(re.findall(r"[a-z0-9]+", p.get("title", "").lower().replace("_", " ")))
        return len(tok - set(palavras) - {"file", "jpg", "jpeg", "png", "webp"})
    pags = sorted((d.get("query") or {}).get("pages", {}).values(),
                  key=lambda p: (min(extras(p), 4), p.get("index", 99)))
    out = []
    for p in pags:
        ii = (p.get("imageinfo") or [{}])[0]
        titulo = p.get("title", "")
        if not ii or ii.get("mime") not in ("image/jpeg", "image/png", "image/webp") or _RUIM_COMMONS.search(titulo):
            continue
        if ii.get("width", 0) < 900 or ii.get("height", 0) < 600:
            continue
        tok = set(re.findall(r"[a-z0-9]+", titulo.lower().replace("_", " ")))
        # TODAS as palavras específicas no nome do arquivo ("Xbox Series X" não aceita foto do Xbox antigo)
        if not palavras or not all(w in tok or (len(w) > 3 and w in titulo.lower()) for w in palavras):
            continue
        meta = ii.get("extmetadata") or {}
        # o nome do arquivo engana ("Ridley Scott.jpg" é a estrela da Calçada da Fama): olha descrição e categorias
        sobre = " ".join(str((meta.get(k) or {}).get("value", "")) for k in ("ImageDescription", "Categories", "ObjectName"))
        if _RUIM_COMMONS.search(re.sub(r"<[^>]+>", " ", sobre)):
            continue
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if not re.search(r"CC|Public domain|PD", lic, re.I):
            continue
        url = ii.get("thumburl") or ii.get("url")
        h = hashlib.sha1(url.encode()).hexdigest()[:10]
        destino = os.path.join(pasta, f"commons_{h}.jpg")
        try:
            if not os.path.exists(destino):
                bruto = destino + ".orig"
                req = urllib.request.Request(url, headers={"User-Agent": COMMONS_UA})
                with urllib.request.urlopen(req, timeout=40) as r, open(bruto, "wb") as f:
                    f.write(r.read())
                ffmpeg("-i", bruto, "-frames:v", 1, "-q:v", 2, destino)
                os.remove(bruto)
        except Exception as e:
            print(f"aviso: commons {titulo}: {e}", flush=True)
            continue
        autor = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()[:80]
        out.append({"tipo": "i", "jogo": "commons", "arquivo": os.path.basename(destino), "url": url,
                    "encaixar": True,
                    "credito": f"{autor} / {lic} / Wikimedia Commons".strip(" /"), "titulo": titulo, "termo": termo})
        if len(out) >= n:
            break
    print(f"commons '{termo}': {[x['titulo'] for x in out]}", flush=True)
    return out
