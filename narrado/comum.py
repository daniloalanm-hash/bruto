"""Utilidades compartilhadas do Modo Narrado (rodam no GitHub Actions)."""
import json
import os
import re
import subprocess
import time
import unicodedata

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
FPS = 30
W, H = 1080, 1920


def norm(s):
    """Normaliza palavra para comparação: minúsculas, sem acento, só letras/números."""
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if c.isalnum())


def run(args, **kw):
    """Executa um comando e falha com a saída de erro legível."""
    r = subprocess.run(args, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"comando falhou: {' '.join(map(str, args[:6]))}…\n{r.stderr[-1500:]}")
    return r


def ffmpeg(*args):
    return run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *map(str, args)])


def duracao(path):
    """Duração em segundos (0 se o arquivo não tiver duração legível, ex.: segmento quebrado)."""
    for campo in ("format=duration", "stream=duration"):
        try:
            r = run(["ffprobe", "-v", "error", "-show_entries", campo, "-of", "csv=p=0", str(path)])
        except RuntimeError:
            return 0.0
        for linha in r.stdout.split():
            try:
                return float(linha.strip().rstrip(","))
            except ValueError:
                continue
    return 0.0


def ler(path, padrao=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return padrao


def gravar(path, dados):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


class Status:
    """Escreve status.json; o workflow publica esse arquivo na release a cada etapa."""

    def __init__(self, pasta, fase, publicar=None):
        self.path = os.path.join(pasta, "status.json")
        self.fase = fase
        self.publicar = publicar
        self.etapas = []

    def etapa(self, nome, progresso):
        self.etapas.append(nome)
        print(f"== [{progresso:3d}%] {nome}", flush=True)
        self._grava({"estado": "processando", "fase": self.fase, "etapa": nome, "progresso": progresso})

    def pronto(self, extra=None):
        self._grava({"estado": "pronto", "fase": self.fase, "etapa": "Pronto", "progresso": 100, **(extra or {})})

    def erro(self, msg):
        self._grava({"estado": "erro", "fase": self.fase, "etapa": self.etapas[-1] if self.etapas else "", "erro": msg})

    def _grava(self, d):
        d["atualizado"] = int(time.time())
        gravar(self.path, d)
        if self.publicar:
            try:
                self.publicar(self.path)
            except Exception as e:  # status é informativo; nunca derruba o render
                print("aviso: não publiquei o status:", e)


def slug(s):
    s = norm(s.replace(" ", "-")) if s else "x"
    return re.sub(r"[^a-z0-9]+", "", s)[:24] or "x"
