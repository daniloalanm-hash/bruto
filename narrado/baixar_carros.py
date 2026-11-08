"""Baixa no SEU computador os vídeos HD dos carros de um roteiro (Radar de Garagem).

Por quê: o Dailymotion só entrega 216–288p para o servidor de render (bloqueio pelo IP do GitHub Actions);
da sua internet de casa ele entrega 720p/1080p. Este script usa a MESMA escolha de vídeos do servidor
(geração certa pelos anos da ficha, sem crash test/brinquedo/jogo, prioridade para HD) e grava os
trechos já sem som e MANDA SOZINHO para a Biblioteca do app (pede a senha do app uma vez e guarda
neste computador). Os vídeos ficam guardados e entram sozinhos em todo vídeo daquele carro.

Uso (precisa só do Python 3.10+ instalado; o resto o script instala na primeira vez):
    python baixar_carros.py                       -> todos os roteiros de carro com carro ainda sem vídeo HD
    python baixar_carros.py suvs-injusticados-fipe
    python baixar_carros.py "Peugeot 3008:2016-2023" "Hyundai Tucson:2015-2020"
"""
import glob
import importlib
import json
import os
import re
import shutil
import site
import subprocess
import sys
import sysconfig
import urllib.error
import urllib.request

RAW = "https://raw.githubusercontent.com/daniloalanm-hash/bruto/main/narrado"
APP = "https://bruto-nu.vercel.app"
ROTEIROS_API = "https://api.github.com/repos/daniloalanm-hash/bruto/contents/narrado/roteiros"
AQUI = os.path.dirname(os.path.abspath(__file__))


def instalar():
    """yt-dlp (com curl-cffi) e um ffmpeg empacotado — sem precisar instalar ffmpeg à parte."""
    falta = []
    for mod, pacote in (("yt_dlp", "yt-dlp[default,curl-cffi]"), ("imageio_ffmpeg", "imageio-ffmpeg")):
        try:
            importlib.import_module(mod)
        except ImportError:
            falta.append(pacote)
    if falta:
        print("Instalando (só na primeira vez):", ", ".join(falta), flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade", *falta], check=True)
    else:  # o Dailymotion muda sempre: yt-dlp desatualizado quebra
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade", "yt-dlp[default,curl-cffi]"])


def preparar_ferramentas():
    """Põe 'ffmpeg' e 'yt-dlp' no PATH deste processo (o código do servidor chama os dois pelo nome)."""
    import imageio_ffmpeg
    pasta = os.path.join(AQUI, ".ferramentas")
    os.makedirs(pasta, exist_ok=True)
    exe = ".exe" if os.name == "nt" else ""
    alvo = os.path.join(pasta, "ffmpeg" + exe)
    if not os.path.exists(alvo):
        shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), alvo)
        os.chmod(alvo, 0o755)
    caminhos = [pasta, sysconfig.get_path("scripts"), os.path.dirname(sys.executable)]
    try:
        caminhos.append(sysconfig.get_path("scripts", f"{os.name}_user"))
    except KeyError:
        pass
    caminhos.append(os.path.join(site.USER_BASE or "", "Scripts" if os.name == "nt" else "bin"))
    os.environ["PATH"] = os.pathsep.join([c for c in caminhos if c] + [os.environ.get("PATH", "")])
    if not shutil.which("yt-dlp"):  # último recurso: um atalho que chama o módulo
        sh = os.path.join(pasta, "yt-dlp.cmd" if os.name == "nt" else "yt-dlp")
        with open(sh, "w") as f:
            f.write(f'@"{sys.executable}" -m yt_dlp %*\n' if os.name == "nt"
                    else f'#!/bin/sh\nexec "{sys.executable}" -m yt_dlp "$@"\n')
        os.chmod(sh, 0o755)
    return alvo


def codigo_do_servidor():
    """Usa a escolha de vídeos do servidor: assets.py/comum.py daqui do lado ou baixados do GitHub."""
    if os.path.exists(os.path.join(AQUI, "assets.py")) and os.path.exists(os.path.join(AQUI, "comum.py")):
        sys.path.insert(0, AQUI)
    else:
        pasta = os.path.join(AQUI, ".codigo")
        os.makedirs(pasta, exist_ok=True)
        for nome in ("assets.py", "comum.py"):
            with open(os.path.join(pasta, nome), "wb") as f:
                f.write(urllib.request.urlopen(f"{RAW}/{nome}", timeout=60).read())
        sys.path.insert(0, pasta)
    import assets
    return assets


def duracao_ffmpeg(ffmpeg_exe):
    """O ffmpeg empacotado não traz o ffprobe: lê a duração da saída do próprio ffmpeg."""
    def duracao(path):
        r = subprocess.run([ffmpeg_exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    return duracao


def ler_roteiro(rid):
    local = os.path.join(AQUI, "roteiros", f"{rid}.json")
    if os.path.exists(local):
        with open(local, encoding="utf-8") as f:
            return json.load(f)
    return json.loads(urllib.request.urlopen(f"{RAW}/roteiros/{rid}.json", timeout=60).read())


def carros_do_roteiro(dados, assets):
    carros = {}
    r = dados.get("roteiro") or {}
    if r.get("nicho") != "carros":
        return {}
    for c in r.get("cenas", []):
        fi = c.get("ficha") or {}
        if c.get("assunto"):
            faixa = assets.anos_da_ficha(fi.get("geracao") or fi.get("ano")) if fi else None
            if faixa or c["assunto"] not in carros:
                carros[c["assunto"]] = faixa or carros.get(c["assunto"])
    return carros


def carros_do_pedido(args, assets):
    """{nome: (ano_min, ano_max) ou None} — de ids de roteiro, de "Nome:2016-2023" ou (sem nada) de todos."""
    carros = {}
    if not args:
        lista = json.loads(urllib.request.urlopen(ROTEIROS_API, timeout=60).read())
        args = [x["name"][:-5] for x in lista if x["name"].endswith(".json")]
    for a in args:
        if ":" in a or not re.fullmatch(r"[a-z0-9-]+", a):
            nome, _, anos = a.partition(":")
            carros[nome.strip()] = assets.anos_da_ficha(anos)
        else:
            for nome, faixa in carros_do_roteiro(ler_roteiro(a), assets).items():
                carros[nome] = faixa or carros.get(nome)
    return carros


# ------------------------------------------------------------------ envio para a Biblioteca do app
def senha_app():
    arq = os.path.join(AQUI, ".senha_app")
    if os.path.exists(arq):
        return open(arq, encoding="utf-8").read().strip()
    if not sys.stdin or not sys.stdin.isatty():
        sys.exit("Sem senha do app guardada: rode o instalador (ou o script no Terminal) uma vez para digitá-la.")
    for tentativa in range(3):
        s = input("Senha do app Bruto (cole com Cmd+V e aperte Enter): ")
        # o Terminal do Mac embrulha o que é colado em códigos invisíveis (ESC[200~ … ESC[201~): tira
        s = re.sub(r"\x1b\[[0-9;]*[~A-Za-z]|\[?20[01]~", "", s)
        s = "".join(c for c in s if c.isprintable()).strip()
        if s:
            print(f"  conferindo {s[:3]}…{s[-3:]} ({len(s)} caracteres)…", flush=True)
        if not s:
            print("Veio vazia. Cole a senha ANTES de apertar Enter.")
            continue
        try:
            _chamar(s)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("Essa senha não confere. Tente de novo.")
                continue
            raise
        with open(arq, "w", encoding="utf-8") as f:
            f.write(s)
        return s
    sys.exit("Senha não conferiu. Rode de novo quando tiver a senha (⚙ do app).")


def _chamar(senha, corpo=None):
    req = urllib.request.Request(f"{APP}/api/biblioteca", method="POST" if corpo else "GET",
                                 data=json.dumps(corpo).encode() if corpo else None,
                                 headers={"Content-Type": "application/json", "x-narrado-senha": senha,
                                          "User-Agent": "Mozilla/5.0 (Macintosh) RadarDeGaragem/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def api_bib(senha, corpo=None):
    try:
        return _chamar(senha, corpo)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            try:
                os.remove(os.path.join(AQUI, ".senha_app"))
            except OSError:
                pass
            sys.exit("A senha do app mudou. Rode de novo no Terminal e digite a nova.")
        raise


def norm(t):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if c.isalnum())


def enviar(senha, nome, arquivos, ja):
    import base64
    import random
    import string
    PARTE = 3 * 1024 * 1024
    enviados = 0
    for k, arq in enumerate(arquivos, 1):
        original = os.path.basename(arq)
        if (norm(nome), original) in ja:
            try:
                os.remove(arq)
            except OSError:
                pass
            continue
        lib = "lib-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8)) + ".mp4"
        with open(arq, "rb") as f:
            dados = f.read()
        total = (len(dados) + PARTE - 1) // PARTE
        for n in range(total):
            print(f"\r  enviando {k}/{len(arquivos)} ({original}) parte {n + 1}/{total}   ", end="", flush=True)
            for tentativa in range(3):
                try:
                    api_bib(senha, {"acao": "parte", "nome": lib, "n": n,
                                    "base64": base64.b64encode(dados[n * PARTE:(n + 1) * PARTE]).decode()})
                    break
                except Exception:
                    if tentativa == 2:
                        raise
        api_bib(senha, {"acao": "salvar", "item": {"jogo": nome, "tipo": "arquivo", "nome": lib, "original": original}})
        enviados += 1
        try:
            os.remove(arq)  # já está guardado na biblioteca: não enche o disco
        except OSError:
            pass
    print()
    return enviados


def trava():
    """Rodando de hora em hora: se a rodada anterior ainda está baixando, esta sai sem fazer nada."""
    try:
        import fcntl
    except ImportError:
        return True
    global _TRAVA
    _TRAVA = open(os.path.join(AQUI, ".rodando"), "w")
    try:
        fcntl.flock(_TRAVA, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    if "--configurar" in sys.argv:  # só guarda e confere a senha (usado pelo instalador)
        n = len(api_bib(senha_app()).get("itens", []))
        print(f"Senha conferida. Biblioteca com {n} item(ns).")
        return
    if not trava():
        print("Outra rodada ainda está baixando; essa fica para a próxima.")
        return
    instalar()
    ffmpeg_exe = preparar_ferramentas()
    assets = codigo_do_servidor()
    assets.duracao = duracao_ffmpeg(ffmpeg_exe)
    senha = senha_app()
    itens = api_bib(senha).get("itens", [])
    ja = {(norm(x.get("jogo")), x.get("original")) for x in itens}
    com_hd = {norm(x.get("jogo")) for x in itens if str(x.get("original", "")).endswith(".mp4") and "_c" in str(x.get("original"))}
    carros = carros_do_pedido(sys.argv[1:], assets)
    arq_tent = os.path.join(AQUI, ".sem_hd.json")
    try:
        sem_hd = json.load(open(arq_tent, encoding="utf-8"))
    except Exception:
        sem_hd = {}
    if not sys.argv[1:]:  # modo automático: só carro sem vídeo HD na biblioteca (e sem tentativa vazia nas últimas 24 h)
        import time as _t
        carros = {n: f for n, f in carros.items()
                  if norm(n) not in com_hd and _t.time() - sem_hd.get(n, 0) > 86400}
    if not carros:
        print("Todos os carros dos roteiros já têm vídeo HD na biblioteca. Nada a fazer.")
        return
    print("Carros:", ", ".join(carros), flush=True)
    raiz = os.path.join(AQUI, "clipes_carros")
    resumo = []
    for nome, faixa in carros.items():
        if faixa:
            assets.ANOS_CARRO[nome] = faixa
        pasta = os.path.join(raiz, nome)
        os.makedirs(pasta, exist_ok=True)
        print(f"\n=== {nome} (geração {faixa[0]}–{faixa[1]})" if faixa else f"\n=== {nome}", flush=True)
        chave = assets.slug(nome)
        clipes = assets._dailymotion_carro(nome, chave, pasta, n=4)
        total = sum(assets.duracao(c["arquivo"]) for c in clipes)
        if total < 300:  # pouco vídeo: 2ª rodada com outros termos, como o servidor faz
            extra = assets._dailymotion_carro(nome, chave, pasta, n=2, extra=True)
            clipes += extra
            total += sum(assets.duracao(c["arquivo"]) for c in extra)
        for x in glob.glob(os.path.join(pasta, "*.tmp*")):
            os.remove(x)
        arquivos = sorted(glob.glob(os.path.join(pasta, "*.mp4")))
        n_env = enviar(senha, nome, arquivos, ja) if arquivos else 0
        resumo.append((nome, len(arquivos), total, n_env))
        if not arquivos:
            import time as _t
            sem_hd[nome] = _t.time()
            with open(arq_tent, "w", encoding="utf-8") as f:
                json.dump(sem_hd, f, ensure_ascii=False)
    print("\n================ PRONTO ================")
    for nome, n, total, n_env in resumo:
        print(f"{nome}: {n} vídeo(s) HD, {total / 60:.1f} min, {n_env} enviado(s) agora para a biblioteca"
              + ("" if n else "  <- nada em HD achado; esse carro vai com fotos"))
    print("\nPode gravar o áudio e gerar o vídeo no app normalmente.")


if __name__ == "__main__":
    main()
