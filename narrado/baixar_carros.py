"""Baixa no SEU computador os vídeos HD dos carros de um roteiro (Radar de Garagem).

Por quê: o Dailymotion só entrega 216–288p para o servidor de render (bloqueio pelo IP do GitHub Actions);
da sua internet de casa ele entrega 720p/1080p. Este script usa a MESMA escolha de vídeos do servidor
(geração certa pelos anos da ficha, sem crash test/brinquedo/jogo, prioridade para HD) e grava os
trechos já sem som numa pasta por carro. Depois é só mandar cada pasta pela Biblioteca do app
(campo "Jogo" = nome do carro): os vídeos ficam guardados e entram sozinhos em todo vídeo daquele carro.

Uso (precisa só do Python 3.10+ instalado; o resto o script instala na primeira vez):
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
import urllib.request

RAW = "https://raw.githubusercontent.com/daniloalanm-hash/bruto/main/narrado"
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


def carros_do_pedido(args, assets):
    """[(nome, (ano_min, ano_max) ou None)] — de um id de roteiro ou de "Nome:2016-2023"."""
    if len(args) == 1 and ":" not in args[0] and re.fullmatch(r"[a-z0-9-]+", args[0]):
        local = os.path.join(AQUI, "roteiros", f"{args[0]}.json")
        if os.path.exists(local):
            with open(local, encoding="utf-8") as f:
                dados = json.load(f)
        else:
            dados = json.loads(urllib.request.urlopen(f"{RAW}/roteiros/{args[0]}.json", timeout=60).read())
        carros = {}
        for c in dados["roteiro"]["cenas"]:
            fi = c.get("ficha") or {}
            if c.get("assunto"):
                faixa = assets.anos_da_ficha(fi.get("geracao") or fi.get("ano")) if fi else None
                if faixa or c["assunto"] not in carros:
                    carros[c["assunto"]] = faixa or carros.get(c["assunto"])
        return list(carros.items()), args[0]
    saida = []
    for a in args:
        nome, _, anos = a.partition(":")
        saida.append((nome.strip(), assets.anos_da_ficha(anos)))
    return saida, "carros"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    instalar()
    ffmpeg_exe = preparar_ferramentas()
    assets = codigo_do_servidor()
    assets.duracao = duracao_ffmpeg(ffmpeg_exe)
    carros, nome_pasta = carros_do_pedido(sys.argv[1:], assets)
    raiz = os.path.join(os.getcwd(), f"clipes_{nome_pasta}")
    resumo = []
    for nome, faixa in carros:
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
        resumo.append((nome, len(clipes), total, pasta))
    print("\n================ PRONTO ================")
    for nome, n, total, pasta in resumo:
        print(f"{nome}: {n} vídeo(s), {total / 60:.1f} min  ->  {pasta}")
    print("\nAgora, no app: Biblioteca de vídeos -> campo Jogo = nome do carro (exatamente como acima)\n"
          "-> Enviar vídeo… -> selecione TODOS os arquivos da pasta daquele carro.")


if __name__ == "__main__":
    main()
