"""Orquestra o Modo Narrado.

    python narrado/pipeline.py preparar <pasta_do_job>   # 1ª versão do vídeo a partir do job.json + voz
    python narrado/pipeline.py render   <pasta_do_job>   # re-render a partir do projeto.json editado na tela

Na pasta do job: job.json (vem da tela), o áudio enviado e, no re-render, o projeto.json.
Saídas na mesma pasta: final.mp4, projeto.json, cenas.jpg, status.json.
"""
import json
import os
import re
import subprocess
import sys
import traceback
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import assets  # noqa: E402
import fala  # noqa: E402
import render  # noqa: E402
import voz  # noqa: E402
from comum import Status, duracao, ffmpeg, gravar, ler, norm, slug  # noqa: E402

RAIZ = os.path.dirname(os.path.abspath(__file__))
RAW = os.environ.get("NARRADO_RAW", os.path.join(RAIZ, "..", ".narrado-cache", "raw"))
FONTES = os.environ.get("NARRADO_FONTES", os.path.join(RAIZ, "fontes"))
MODELO_RNN = os.environ.get("NARRADO_RNN", os.path.join(RAIZ, "modelos", "sh.rnnn"))
MODELO_WHISPER = os.environ.get("NARRADO_WHISPER", "medium")


def publicador():
    """Publica arquivos na release do job (no GitHub Actions); localmente não faz nada."""
    tag = os.environ.get("NARRADO_TAG")
    if not tag:
        return None

    def pub(*paths):
        subprocess.run(["gh", "release", "upload", tag, *paths, "--clobber"], check=True, capture_output=True)
    return pub


def chave_do_jogo(nome, jogos):
    if not nome:
        return None
    melhor = max(jogos, key=lambda j: SequenceMatcher(None, norm(nome), norm(j["nome"])).ratio(), default=None)
    if melhor and SequenceMatcher(None, norm(nome), norm(melhor["nome"])).ratio() >= 0.5:
        return melhor["chave"]
    for j in jogos:  # "Witcher" dentro de "The Witcher 3: Wild Hunt"
        if norm(nome) in norm(j["nome"]) or norm(j["nome"]) in norm(nome):
            return j["chave"]
    return None


# aparência padrão de um vídeo novo (a tela pode mudar cada uma; o job pode trazer as preferências salvas)
ESTILO_PADRAO = {
    "jl_quadros": 8, "transicao_gancho": "pan", "titulo_estilo": "manchete", "legenda_estilo": "classico", "legenda_cor": "amarelo",
    "legenda_altura": 0.615, "sfx": True, "zoom_enfase": True, "emoji": True,
}


def completar_cenas(palavras):
    """Toda palavra mantida precisa de tópico (cena): herda da vizinha mais próxima."""
    ult = None
    for w in palavras:
        if w.get("manter"):
            if w.get("cena") is None:
                w["cena"] = ult
            ult = w["cena"] if w.get("cena") is not None else ult
    prox = None
    for w in reversed(palavras):
        if w.get("manter"):
            if w.get("cena") is None:
                w["cena"] = prox if prox is not None else 0
            prox = w["cena"]
    for w in palavras:
        if w.get("manter") and not str(w.get("legenda") or "").strip():
            w["legenda"] = w["w"]


def impacto_plano(banco):
    """Nota de impacto de um trecho: movimento entre quadros, cor e duração suficiente para o gancho."""
    def nota(mid):
        m = banco[mid]
        return (m.get("mov", 0) + 1) * (1 + m.get("sat", 30) / 60) * (1 if m.get("dur", 0) >= 2 else 0.6)
    return nota


_GENERICAS = {"tom", "clancys", "the", "of", "edition", "remastered", "remaster", "mobile", "online", "game",
              "games", "definitive", "deluxe", "complete", "goty", "2", "3", "4", "ii", "iii", "iv"}


def mesma_franquia(a, b):
    """Dois nomes da mesma franquia: dividem pelo menos 2 palavras relevantes (ou 1 bem longa)."""
    pa = {w for w in re.findall(r"[a-z0-9]+", norm_txt(a)) if w not in _GENERICAS}
    pb = {w for w in re.findall(r"[a-z0-9]+", norm_txt(b)) if w not in _GENERICAS}
    comuns = pa & pb
    return len(comuns) >= 2 or any(len(w) >= 7 for w in comuns)


def norm_txt(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn").replace("'", "")


def planejar(cenas, banco, jogos):
    """Cada cena usa material do jogo de que ela fala; cenas gerais usam o jogo principal.

    Regra do Danilo: imagem de outro jogo só aparece quando aquele jogo está sendo falado.
    """
    chaves = [chave_do_jogo(c.get("assunto"), jogos) for c in cenas]
    contagem = {}
    for ch in chaves:
        if ch:
            contagem[ch] = contagem.get(ch, 0) + 1
    principal = max(contagem, key=contagem.get) if contagem else (jogos[0]["chave"] if jogos else None)
    fila = {}
    for j in jogos:  # intercala 2 trechos de trailer : 1 imagem, sem repetir até esgotar
        vs = [m for m, d in banco.items() if d["jogo"] == j["chave"] and d["tipo"] == "v"]
        im = [m for m, d in banco.items() if d["jogo"] == j["chave"] and d["tipo"] == "i"]
        seq = []
        while vs or im:
            for _ in range(2):
                if vs:
                    seq.append(vs.pop(0))
            if im:
                seq.append(im.pop(0))
        fila[j["chave"]] = {"seq": seq, "pos": 0}
    # jogo com pouco material (ex.: versão de celular só com 2 screenshots) é completado com o de um jogo
    # da mesma franquia que está no vídeo (Rainbow Six Mobile ← Rainbow Six Siege). Jogo de outra
    # franquia continua nunca aparecendo fora da hora.
    for j in jogos:
        proprio = fila[j["chave"]]["seq"]
        n_vid = sum(1 for m in proprio if banco[m]["tipo"] == "v")
        if n_vid >= 2 and len(proprio) >= 6:
            continue
        extra = []
        for o in jogos:
            if o["chave"] != j["chave"] and mesma_franquia(j["nome"], o["nome"]):
                extra += [m for m in fila[o["chave"]]["seq"] if banco[m]["tipo"] == "v"] or fila[o["chave"]]["seq"]
        if extra:
            mix, a, b = [], list(proprio), list(extra)
            while a or b:  # 1 do próprio jogo : 2 da franquia
                if a:
                    mix.append(a.pop(0))
                for _ in range(2):
                    if b:
                        mix.append(b.pop(0))
            fila[j["chave"]]["seq"] = mix
    materia = [m for m, d in banco.items() if d.get("jogo") == "materia"]
    for k, c in enumerate(cenas):
        ch = chaves[k] or principal
        c["jogo"] = ch
        f = fila.get(ch)
        if not f or not f["seq"]:
            c["midias"] = []
            continue
        escolha = []
        if k == 0:  # gancho: abre com o plano de trailer mais impactante (movimento + cor) do jogo
            vids = [m for m in f["seq"] if banco[m]["tipo"] == "v"]
            if vids:
                escolha.append(max(vids, key=impacto_plano(banco)))
            else:
                img = next((m for m in f["seq"] if banco[m]["tipo"] == "i"), None)
                if img:
                    escolha.append(img)
        inicio = f["pos"]
        while len(escolha) < 6:
            m = f["seq"][f["pos"] % len(f["seq"])]
            f["pos"] += 1
            if m not in escolha:
                escolha.append(m)
            if len(escolha) >= len(f["seq"]):
                break
        # o próximo tópico continua de onde este parou de verdade (≈ cortes que ele usa), e não 6 adiante:
        # com pouco material (ex.: jogo de celular com 6 imagens), cada tópico abre com uma imagem diferente
        cortes = max(1, round(len(c.get("texto", "").split()) / 2.6 / 1.7))
        f["pos"] = inicio + min(cortes, len(escolha))
        c["midias"] = escolha
    # imagens da matéria: são as que mostram a novidade. Entram como 1ª mídia dos tópicos sobre o jogo
    # principal (ou gerais), uma por tópico, sem repetir; no gancho, depois do plano de impacto.
    fila_m = list(materia)
    for k, c in enumerate(cenas):
        if not fila_m:
            break
        if (chaves[k] or principal) != principal or not c.get("midias"):
            continue
        mid = fila_m.pop(0)
        midias = c.setdefault("midias", [])
        tem_video = any(banco[m]["tipo"] == "v" for m in midias if m in banco)
        midias.insert(1 if (k == 0 and tem_video) else 0, mid)
    # tópico sem nada (jogo sem material): usa as imagens da matéria em rodízio
    if materia:
        for k, c in enumerate(cenas):
            if not c.get("midias"):
                c["midias"] = [materia[(k + i) % len(materia)] for i in range(min(3, len(materia)))]
    return cenas


def garantir_assets(nomes, permitir_vazio=False, trailers=None):
    """Baixa o material de cada jogo em paralelo (rede, não CPU). trailers: {nome: máximo de trailers}."""
    from concurrent.futures import ThreadPoolExecutor
    trailers = trailers or {}

    def um(nome):
        try:
            return assets.baixar_jogo(nome, RAW, trailers.get(nome, assets.MAX_TRAILERS))
        except Exception as e:
            print(f"aviso: falhou ao buscar '{nome}': {e}", flush=True)
            return None
    with ThreadPoolExecutor(max_workers=4) as ex:
        jogos = [j for j in ex.map(um, nomes) if j]
    if not jogos and not permitir_vazio:
        raise RuntimeError("Não encontrei material (Steam, App Store ou YouTube) para nenhum dos jogos do roteiro.")
    return jogos


def juntar_links(jogos, links, nomes):
    """Vídeos de links colados na tela entram como trailers (os primeiros) do jogo indicado — ou do principal."""
    principal = nomes[0] if nomes else (jogos[0]["nome"] if jogos else "Vídeo")
    for i, ln in enumerate(links):
        alvo_nome = (ln.get("jogo") or principal).strip()
        info = next((j for j in jogos if assets.mesmo_jogo(alvo_nome, j["nome"], 0.4) or
                     norm(alvo_nome) == norm(j["nome"])), None)
        if info is None:
            # jogo sem material nenhum (ou só com link): cria a entrada para ele
            info = {"chave": slug(alvo_nome), "nome": alvo_nome, "appid": None, "fonte": "Link",
                    "screens": [], "trailers": []}
            jogos.append(info)
        ln["chave"] = info["chave"]
        t = assets.baixar_link_video(ln["url"], info["chave"], RAW, i)
        if t:
            info["trailers"].insert(0, t)
    return [j for j in jogos if j["screens"] or j["trailers"]]


def jogos_necessarios(job, cenas):
    """Só busca jogo que é assunto de algum tópico (mais o principal). Jogo só citado na lista não entra.

    Devolve (nomes, {nome: máximo de trailers}): o principal leva 3 trailers; jogo com 2+ tópicos leva 2;
    jogo de um tópico só leva 1 (análise de trailer é a parte mais lenta da preparação).
    """
    lista = [n for n in (job.get("jogos") or []) if n]
    assuntos = [c["assunto"] for c in cenas if c.get("assunto")]
    principal = lista[0] if lista else (max(set(assuntos), key=assuntos.count) if assuntos else None)
    nomes = list(dict.fromkeys([n for n in [principal] + assuntos if n]))
    if not assuntos:  # roteiro sem assuntos marcados: comportamento antigo (todos da lista)
        nomes = list(dict.fromkeys(lista))
    trailers = {}
    for n in nomes:
        q = assuntos.count(n)
        trailers[n] = assets.MAX_TRAILERS if n == principal else (2 if q >= 2 else 1)
    pulados = [n for n in lista if n not in nomes]
    if pulados:
        print(f"jogos citados sem tópico próprio (não busco material): {pulados}", flush=True)
    return nomes, trailers


def preparar(pasta, st):
    job = ler(os.path.join(pasta, "job.json"))
    cenas = [{"texto": c["texto"].strip(), "destaques": c.get("destaques", []), "assunto": c.get("assunto"),
              **({"emoji": c["emoji"]} if c.get("emoji") else {})}
             for c in job["cenas"] if c.get("texto", "").strip()]
    nomes, n_trailers = jogos_necessarios(job, cenas)

    st.etapa("Buscando trailers e imagens oficiais", 5)
    links = [x for x in (job.get("links_video") or []) if x.get("url")]
    jogos = garantir_assets(nomes, permitir_vazio=bool(job.get("imagens_materia") or links), trailers=n_trailers)
    if links:
        st.etapa("Baixando os vídeos dos links", 12)
        jogos = juntar_links(jogos, links, nomes)

    st.etapa("Escolhendo os melhores trechos dos trailers", 18)

    def prog(k, n):
        st.etapa(f"Escolhendo os melhores trechos dos trailers ({k}/{n})", 18 + int(10 * k / n))
    banco = assets.montar_banco(jogos, os.path.join(pasta, "thumbs"), prog)
    banco.update(assets.baixar_imagens_materia(job.get("imagens_materia"), RAW))
    sprite = assets.sprite(banco, RAW, os.path.join(pasta, "cenas.jpg"))

    modo = job.get("voz", {}).get("modo", "gravada")
    if modo == "ia":
        st.etapa("Gerando a narração por IA", 30)
        texto = " ".join(c["texto"] for c in cenas)
        audio = os.path.join(pasta, "voz_ia.mp3")
        palavras = fala.voz_ia(texto, audio, job.get("voz", {}).get("voz_ia") or "pt-BR-AntonioNeural")
    else:
        audio = os.path.join(pasta, job["voz"]["arquivo"])
        st.etapa("Transcrevendo a sua gravação", 30)
        dicas = ", ".join(dict.fromkeys([j["nome"] for j in jogos] + (job.get("jogos") or [])))
        palavras = fala.transcrever(audio_limpo(audio), MODELO_WHISPER, dicas)
    st.etapa("Cortando erros e repetições", 55)
    palavras, faltando = fala.editar(palavras, cenas, [j["nome"] for j in jogos])
    if modo == "ia":
        # voz de IA lê exatamente o roteiro: tudo fica; palavra que o alinhamento deixou sem tópico
        # herda o tópico da anterior (ou da seguinte, no começo)
        for w in palavras:
            w["manter"] = True
        completar_cenas(palavras)

    cenas = planejar(cenas, banco, jogos)
    projeto = {
        "versao": 1, "id": job.get("id"), "titulo": job.get("titulo", ""), "fonte": job.get("fonte", ""),
        "voz": {"modo": modo, "arquivo": os.path.basename(audio), "reducao_ruido": "auto"},
        "jogos": [{"chave": j["chave"], "nome": j["nome"], "appid": j["appid"]} for j in jogos],
        "nomes_busca": nomes, "trailers_busca": n_trailers, "links_video": links, "cenas": cenas, "palavras": palavras, "faltando": faltando,
        "banco": banco, "sprite": sprite,
        # aparência (a tela permite mudar e re-renderizar)
        "titulo_tela": (job.get("titulo_tela") or "").strip(), "titulo_gancho": bool(job.get("titulo_tela")),
        "post": job.get("post") or {},
        **{k: job[k] for k in ESTILO_PADRAO if k in job},
    }
    for k, v in ESTILO_PADRAO.items():
        projeto.setdefault(k, v)
    finalizar(pasta, projeto, audio, st, 62)


def audio_limpo(audio):
    """Converte a gravação para WAV PCM antes de tudo.

    A gravação feita no navegador (WebM/Opus do MediaRecorder) não tem índice nem duração no cabeçalho;
    cortes e detecção de silêncio direto nela saem desalinhados (trechos sobrepostos, palavras duplicadas).
    """
    destino = os.path.splitext(audio)[0] + ".src.wav"
    if not os.path.exists(destino) or os.path.getmtime(destino) < os.path.getmtime(audio):
        ffmpeg("-i", audio, "-vn", "-ac", 1, "-ar", 48000, "-c:a", "pcm_s16le", destino)
    return destino


def so_legenda(pasta, st):
    """Ajuste que só mexe na legenda/chamada/emojis: reaproveita o vídeo limpo do último render."""
    projeto = ler(os.path.join(pasta, "projeto.json"))
    limpo = os.path.join(pasta, "limpo.mp4")
    if not os.path.exists(limpo):
        raise RuntimeError("Este vídeo ainda não tem a versão sem legenda guardada. Use “Aplicar ajustes” "
                           "completo uma vez (os próximos ajustes de legenda ficam rápidos).")
    completar_cenas(sorted(projeto["palavras"], key=lambda w: w["s"]))
    st.etapa("Atualizando só a legenda", 30)
    render.legendar(projeto, limpo, os.path.join(pasta, "trab"), os.path.join(pasta, "final.mp4"), FONTES)
    projeto["versao"] = int(projeto.get("versao", 1)) + 1
    gravar(os.path.join(pasta, "projeto.json"), projeto)
    pub = publicador()
    if pub:
        st.etapa("Publicando o vídeo", 90)
        pub(os.path.join(pasta, "final.mp4"), os.path.join(pasta, "projeto.json"))
    st.pronto({"versao": projeto["versao"], "duracao": projeto.get("duracao")})


def rerender(pasta, st):
    projeto = ler(os.path.join(pasta, "projeto.json"))
    st.etapa("Preparando o material", 5)
    falta = [m for m in projeto["banco"].values() if not os.path.exists(os.path.join(RAW, m["arquivo"]))]
    if any(m.get("jogo") not in ("materia", "proprio") for m in falta):
        garantir_assets(projeto.get("nomes_busca") or [j["nome"] for j in projeto["jogos"]], permitir_vazio=True,
                        trailers=projeto.get("trailers_busca"))
    for i, ln in enumerate(projeto.get("links_video") or []):
        if ln.get("chave"):
            assets.baixar_link_video(ln["url"], ln["chave"], RAW, i)  # já baixado: não faz nada
    urls = [m["url"] for m in falta if m.get("jogo") == "materia" and m.get("url")]
    if urls:
        assets.baixar_imagens_materia(urls, RAW)
    projeto["versao"] = int(projeto.get("versao", 1)) + 1
    finalizar(pasta, projeto, os.path.join(pasta, projeto["voz"]["arquivo"]), st, 20)


def integrar_proprias(pasta, projeto):
    """Imagens/clipes enviados pela tela (job/proprias/) entram no banco e na cena escolhida."""
    import shutil
    novas = False
    for item in projeto.get("proprias", []):
        mid = f"proprio_{item['id']}"
        orig = os.path.join(pasta, "proprias", item["arquivo"])
        destino = os.path.join(RAW, item["arquivo"])
        if not os.path.exists(destino):
            if not os.path.exists(orig):
                print(f"aviso: mídia enviada {item['arquivo']} não chegou")
                continue
            os.makedirs(RAW, exist_ok=True)
            shutil.copy(orig, destino)
        if mid not in projeto["banco"]:
            ent = {"tipo": item.get("tipo", "i"), "jogo": "proprio", "arquivo": item["arquivo"], "proprio": True,
                   "nome": item.get("nome", "")}
            if ent["tipo"] == "v":
                ent.update({"t": 0.0, "dur": round(duracao(destino), 2)})
            projeto["banco"][mid] = ent
            novas = True
        cena = item.get("cena")
        if isinstance(cena, int) and 0 <= cena < len(projeto["cenas"]) and not item.get("aplicada"):
            midias = projeto["cenas"][cena].setdefault("midias", [])
            if mid not in midias:
                midias.insert(0, mid)
            item["aplicada"] = True
    if novas or not os.path.exists(os.path.join(pasta, "cenas.jpg")):
        try:
            projeto["sprite"] = assets.sprite(projeto["banco"], RAW, os.path.join(pasta, "cenas.jpg"))
        except Exception as e:
            print(f"aviso: miniaturas não atualizadas: {e}")


def voz_previa(audio, pasta):
    """MP3 leve da gravação inteira: a tela usa para a prévia instantânea (corta as falas no navegador)."""
    destino = os.path.join(pasta, "voz_previa.mp3")
    ffmpeg("-i", audio, "-ac", 1, "-ar", 24000, "-c:a", "libmp3lame", "-b:a", "48k", destino)
    return destino


def finalizar(pasta, projeto, audio, st, p0):
    completar_cenas(sorted(projeto["palavras"], key=lambda w: w["s"]))
    integrar_proprias(pasta, projeto)
    st.etapa("Tratando a voz", p0)
    escolha = projeto["voz"].get("reducao_ruido", "auto")
    forcar = None if escolha == "auto" else escolha == "sim"
    wav = os.path.join(pasta, "voz_final.wav")
    info = voz.montar(audio_limpo(audio), projeto["palavras"], wav, MODELO_RNN, projeto["voz"]["modo"], forcar)
    projeto["duracao_voz"] = info["duracao"]
    projeto["voz"].update({"ruido_db": info["ruido_db"], "reducao_aplicada": info["reducao_ruido"]})

    st.etapa("Montando cenas, transições e legenda", p0 + 8)
    faixa = 100 - (p0 + 8) - 6

    def prog(i, n):
        st.etapa(f"Montando cenas ({i}/{n})", p0 + 8 + int(faixa * i / n))
    usados, total = render.renderizar(projeto, projeto["banco"], RAW, wav, pasta, os.path.join(pasta, "final.mp4"),
                                      FONTES, prog)
    projeto["timeline"] = usados
    projeto["duracao"] = round(total, 2)
    gravar(os.path.join(pasta, "projeto.json"), projeto)
    pub = publicador()
    if pub:
        st.etapa("Publicando o vídeo", 97)
        try:
            voz_previa(audio_limpo(audio), pasta)
        except Exception as e:
            print(f"aviso: prévia de voz não gerada: {e}")
        arquivos = [os.path.join(pasta, x) for x in ("final.mp4", "projeto.json", "cenas.jpg", "voz_ia.mp3",
                                                     "voz_previa.mp3", "limpo.mp4")
                    if os.path.exists(os.path.join(pasta, x))]
        pub(*arquivos)
    st.pronto({"versao": projeto["versao"], "duracao": projeto["duracao"]})


def teste_transcricao(pasta, st):
    """Diagnóstico: transcreve a gravação de 3 jeitos e publica os resultados (não mexe no vídeo)."""
    import time as _t
    from faster_whisper import WhisperModel
    job = ler(os.path.join(pasta, "job.json"))
    audio = audio_limpo(os.path.join(pasta, job["voz"]["arquivo"]))
    dicas = ", ".join(job.get("jogos") or [])
    model = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")
    pub = publicador()
    for modo in ("pacote", "curto", "isolado"):
        t0 = _t.time()
        pal = fala.transcrever(audio, MODELO_WHISPER, dicas, modo=modo, model=model)
        destino = os.path.join(pasta, f"teste_{modo}.json")
        gravar(destino, {"segundos": round(_t.time() - t0, 1), "palavras": pal})
        print(f"teste {modo}: {_t.time() - t0:.0f}s", flush=True)
        if pub:
            pub(destino)


TESTE_LINKS = ["https://x.com/RockstarGames/status/2110717537964953810"]


def teste_fontes(pasta, st):
    import teste_fontes as tf
    job = ler(os.path.join(pasta, "job.json"))
    pf = os.path.join(pasta, "fontes")
    os.makedirs(pf, exist_ok=True)
    rel = {"links": {}}
    for i, u in enumerate(TESTE_LINKS):
        jogos = juntar_links([], [{"url": u, "jogo": "Grand Theft Auto VI"}], ["Grand Theft Auto VI"])
        banco = assets.montar_banco(jogos, os.path.join(pasta, "thumbs"))
        rel["links"][u] = {"jogos": [(j["nome"], [t["arquivo"] for t in j["trailers"]]) for j in jogos],
                           "trechos": len(banco), "exemplo": list(banco.values())[:3]}
    destino = os.path.join(pasta, "teste_fontes.json")
    gravar(destino, rel)
    print(json.dumps(rel, ensure_ascii=False, indent=1), flush=True)
    pub = publicador()
    if pub:
        pub(destino)


def main():
    fase, pasta = sys.argv[1], os.path.abspath(sys.argv[2])
    pub = publicador()
    st = Status(pasta, fase, (lambda p: pub(p)) if pub and not fase.startswith("teste") else None)
    try:
        {"preparar": preparar, "legenda": so_legenda, "teste_transcricao": teste_transcricao,
         "teste_fontes": teste_fontes}.get(fase, rerender)(pasta, st)
    except Exception as e:
        traceback.print_exc()
        st.erro(str(e)[:400])
        sys.exit(1)


if __name__ == "__main__":
    main()
