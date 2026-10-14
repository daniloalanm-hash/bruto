"""Diagnóstico: de onde dá para baixar vídeo de jogo a partir do servidor de render (GitHub Actions)."""
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def ytdlp(alvo, pasta, nome, extra=()):
    t0 = time.time()
    out = os.path.join(pasta, nome + ".%(ext)s")
    r = subprocess.run(["yt-dlp", alvo, "--no-playlist", "--quiet", "--no-warnings", "--playlist-items", "1",
                        "-f", "bv*[height<=1080]/b", "--download-sections", "*0-20", "-o", out,
                        "--print", "after_move:%(title)s|%(height)s|%(webpage_url)s", *extra],
                       capture_output=True, text=True, timeout=180)
    arq = next((os.path.join(pasta, x) for x in os.listdir(pasta) if x.startswith(nome + ".")), None)
    return {"ok": r.returncode == 0 and bool(arq), "info": r.stdout.strip()[:200], "erro": r.stderr.strip()[-300:],
            "bytes": os.path.getsize(arq) if arq else 0, "s": round(time.time() - t0, 1)}


def testar(nomes, pasta):
    os.makedirs(pasta, exist_ok=True)
    rel = {}
    for nome in nomes:
        r = rel[nome] = {}
        q = f"{nome} gameplay trailer"
        for cli in ("default", "tv", "web_safari", "mweb", "web_embedded", "ios", "android_vr"):
            extra = () if cli == "default" else ("--extractor-args", f"youtube:player_client={cli}")
            try:
                r[f"youtube_{cli}"] = ytdlp(f"ytsearch1:{q}", pasta, f"yt_{cli}_{len(rel)}", extra)
            except Exception as e:
                r[f"youtube_{cli}"] = {"ok": False, "erro": str(e)}
        # Dailymotion: busca pública + yt-dlp
        try:
            d = json.loads(_get("https://api.dailymotion.com/videos?" + urllib.parse.urlencode(
                {"search": q, "fields": "id,title,duration", "limit": 5, "sort": "relevance"})))
            lista = d.get("list", [])
            r["dailymotion_busca"] = [f"{v['title']} ({v['duration']}s)" for v in lista]
            if lista:
                r["dailymotion"] = ytdlp(f"https://www.dailymotion.com/video/{lista[0]['id']}", pasta, f"dm_{len(rel)}")
        except Exception as e:
            r["dailymotion"] = {"ok": False, "erro": str(e)}
        # Internet Archive
        try:
            d = json.loads(_get("https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(
                {"q": f"({nome}) AND mediatype:movies", "fl[]": "identifier,title", "rows": 5, "output": "json"})))
            docs = d.get("response", {}).get("docs", [])
            r["archive_busca"] = [x.get("title") for x in docs]
            if docs:
                m = json.loads(_get(f"https://archive.org/metadata/{docs[0]['identifier']}"))
                mp4 = [f["name"] for f in m.get("files", []) if f.get("name", "").endswith(".mp4")]
                r["archive_mp4"] = mp4[:3]
        except Exception as e:
            r["archive"] = {"ok": False, "erro": str(e)}
        # Vimeo via yt-dlp não tem busca; pula
    return rel
