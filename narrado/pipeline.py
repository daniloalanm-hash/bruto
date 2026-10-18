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
import cartoes  # noqa: E402
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
    # visual de referência (TikTok de notícia de games): janela 4:5 com fundo desfocado, legenda palavra por
    # palavra, whip pan só na troca de jogo, capa do jogo na 1ª aparição, trilha de fundo
    "enquadramento": "moldura", "transicoes_modo": "por_jogo", "musica": True, "capas": True,
    "marca_dagua": "@jogandosemplano",
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
        por_imagem = 4 if len(vs) >= 3 * max(len(im), 1) else 3 if len(vs) >= 2 * max(len(im), 1) else 2
        while vs or im:
            for _ in range(por_imagem):  # vídeo prende mais que imagem parada
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


def garantir_assets(nomes, permitir_vazio=False, trailers=None, steam_ids=()):
    """Baixa o material de cada jogo em paralelo (rede, não CPU). trailers: {nome: máximo de trailers}."""
    from concurrent.futures import ThreadPoolExecutor
    trailers = trailers or {}

    def um(nome):
        try:
            # links da Steam da matéria: valem para o jogo principal (o 1º) ou quando há um só
            dicas = list(steam_ids) if (steam_ids and (nome == nomes[0] or len(steam_ids) == 1)) else []
            return assets.baixar_jogo(nome, RAW, trailers.get(nome, assets.MAX_TRAILERS), dicas)
        except Exception as e:
            print(f"aviso: falhou ao buscar '{nome}': {e}", flush=True)
            return None
    with ThreadPoolExecutor(max_workers=4) as ex:
        jogos = [j for j in ex.map(um, nomes) if j]
    if not jogos and not permitir_vazio:
        raise RuntimeError("Não encontrei material (Steam, App Store ou YouTube) para nenhum dos jogos do roteiro.")
    return jogos


BIB_TAG = "narrado-biblioteca"


def _gh_baixar(padrao, destino):
    """Baixa arquivos da release da biblioteca (gh CLI do Actions). Falha em silêncio (biblioteca é opcional)."""
    os.makedirs(destino, exist_ok=True)
    r = subprocess.run(["gh", "release", "download", BIB_TAG, "-p", padrao, "-D", destino, "--clobber"],
                       capture_output=True, text=True)
    return r.returncode == 0


def da_biblioteca(alvos):
    """Itens da biblioteca (mandados uma vez pela tela) dos jogos deste vídeo. Devolve entradas no formato
    dos links: {"jogo", "url"} para link e {"jogo", "arquivo"} para vídeo/imagem já pronto no RAW."""
    tmp = os.path.join(RAW, "_bib")
    itens = []
    fixa = os.path.join(os.path.dirname(os.path.abspath(__file__)), "biblioteca_fixa.json")
    if os.path.exists(fixa):
        itens += ler(fixa) or []
    if _gh_baixar("biblioteca.json", tmp):
        try:
            itens += ler(os.path.join(tmp, "biblioteca.json")) or []
        except Exception:
            pass
    saida = []
    for it in itens:
        jogo = it.get("jogo") or ""
        if norm(jogo) in NOMES_TRILHA:
            continue  # música de fundo: é tratada em garantir_trilha
        if not any(norm(jogo) == norm(a) or assets.mesmo_jogo(jogo, a, 0.4) or assets.mesmo_jogo(a, jogo, 0.4)
                   for a in alvos if a):
            continue
        if it.get("tipo") == "link":
            saida.append({"jogo": jogo, "url": it["url"], "bib": it["id"]})
            continue
        nome = it.get("nome") or ""
        ext = nome.rsplit(".", 1)[-1]
        final = os.path.join(RAW, f"lib_{it['id']}." + ("mp4" if ext in ("mp4", "mov", "webm", "m4v") else "jpg"))
        if not os.path.exists(final):
            pasta_p = os.path.join(tmp, it["id"])
            if not _gh_baixar(f"{nome}.part*", pasta_p):
                print(f"aviso: não baixei {nome} da biblioteca", flush=True)
                continue
            partes = sorted(os.listdir(pasta_p))
            bruto = os.path.join(pasta_p, nome)
            with open(bruto, "wb") as f:
                for parte in partes:
                    with open(os.path.join(pasta_p, parte), "rb") as g:
                        f.write(g.read())
            try:
                if final.endswith(".mp4"):
                    ffmpeg("-i", bruto, "-t", assets.TRECHO_MAX, "-an", "-vf", "scale=-2:'min(1080,ih)'",
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", 20, final)
                else:
                    ffmpeg("-i", bruto, "-frames:v", 1, "-q:v", 2, final)
            except Exception as e:
                print(f"aviso: arquivo da biblioteca {it.get('original') or nome} não abriu: {e}", flush=True)
                continue
        saida.append({"jogo": jogo, "arquivo": final, "bib": it["id"]})
        print(f"biblioteca: {jogo} ← {it.get('original') or nome}", flush=True)
    return saida


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
        if ln.get("arquivo"):  # item da biblioteca já baixado
            if ln["arquivo"].endswith(".jpg"):
                info["screens"].insert(0, ln["arquivo"])
                continue
            t = {"arquivo": ln["arquivo"], "nome": "biblioteca"}
        else:
            t = None
            if "rockstargames.com" in ln["url"]:
                t = assets.baixar_rockstar(ln["url"], info["chave"], RAW) or None
            if not t:
                t = assets.baixar_link_video(ln["url"], info["chave"], RAW, i)
            if not t:  # não é player de vídeo: tenta os clipes que a própria página carrega (site oficial)
                t = assets.baixar_videos_site(ln["url"], info["chave"], RAW) or None
        if t:
            # com vídeo oficial indicado por você, sai o material achado por busca (Dailymotion/YouTube)
            info["trailers"] = [x for x in info["trailers"] if not re.search(r"_[dy]\d+(_\w+)?\.mp4$", x["arquivo"])]
            for x in reversed(t if isinstance(t, list) else [t]):
                if all(x["arquivo"] != y["arquivo"] for y in info["trailers"]):
                    info["trailers"].insert(0, x)
    return [j for j in jogos if j["screens"] or j["trailers"]]


NOMES_TRILHA = {"trilha", "trilhas", "musica", "musicas", "trilha sonora"}


def garantir_trilha(projeto):
    """Música de fundo: arquivos de áudio da biblioteca com jogo "Trilha". Cada vídeo pega uma (em rodízio
    pelo id do vídeo); re-render reaproveita a mesma. Sem trilha na biblioteca, o vídeo segue sem música."""
    if "materia" not in projeto or projeto.get("musica") is False:
        return
    tri = projeto.get("trilha")
    if tri and os.path.exists(os.path.join(RAW, tri.get("arquivo", ""))):
        return
    tmp = os.path.join(RAW, "_bib")
    if not _gh_baixar("biblioteca.json", tmp):
        return
    try:
        itens = [it for it in (ler(os.path.join(tmp, "biblioteca.json")) or [])
                 if norm(it.get("jogo") or "") in NOMES_TRILHA and it.get("tipo") == "arquivo"]
    except Exception:
        return
    if not itens:
        return
    import zlib
    it = next((x for x in itens if tri and x.get("id") == tri.get("bib")), None) or \
        itens[zlib.crc32(str(projeto.get("id") or projeto.get("titulo")).encode()) % len(itens)]
    final = os.path.join(RAW, f"trilha_{it['id']}.m4a")
    if not os.path.exists(final):
        nome = it.get("nome") or ""
        pasta_p = os.path.join(tmp, it["id"])
        if not _gh_baixar(f"{nome}.part*", pasta_p):
            print(f"aviso: trilha {nome} não baixou", flush=True)
            return
        bruto = os.path.join(pasta_p, nome)
        with open(bruto, "wb") as f:
            for parte in sorted(os.listdir(pasta_p)):
                if parte.startswith(nome + ".part"):
                    with open(os.path.join(pasta_p, parte), "rb") as g:
                        f.write(g.read())
        try:
            ffmpeg("-i", bruto, "-vn", "-ac", 2, "-ar", 48000, "-af", "loudnorm=I=-16:TP=-2", "-c:a", "aac", "-b:a", "160k", final)
        except Exception as e:
            print(f"aviso: trilha {it.get('original')} não abriu: {e}", flush=True)
            return
    projeto["trilha"] = {"arquivo": os.path.basename(final), "nome": it.get("original") or nome, "bib": it["id"]}
    print(f"trilha de fundo: {projeto['trilha']['nome']}", flush=True)


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
    longo = job.get("formato") == "horizontal"  # YouTube: 5-10 min pedem muito mais material
    for n in nomes:
        q = assuntos.count(n)
        if longo:
            # cada trailer custa ~1 min de análise: top lista com 5 jogos não pode buscar 25 trailers
            trailers[n] = 5 if n == principal else (3 if q >= 3 else 2)
        else:
            # vídeo de 1 min+ tem ~40 cortes: o jogo principal precisa de material variado (sem repetir trecho)
            trailers[n] = 5 if n == principal else (2 if q >= 2 else 1)
    pulados = [n for n in lista if n not in nomes]
    if pulados:
        print(f"jogos citados sem tópico próprio (não busco material): {pulados}", flush=True)
    return nomes, trailers


def preparar(pasta, st):
    job = ler(os.path.join(pasta, "job.json"))
    cenas = [{"texto": c["texto"].strip(), "destaques": c.get("destaques", []), "assunto": c.get("assunto"),
              **({"emoji": c["emoji"]} if c.get("emoji") else {}),
              # "numero" presente (mesmo null) = a IA já decidiu; ausente (roteiro antigo) = detecta no texto
              **({"numero": c["numero"] if isinstance(c.get("numero"), dict) and c["numero"].get("valor") else None}
                 if "numero" in c else {}),
              **({"imagem": str(c["imagem"]).strip()} if c.get("imagem") else {}),
              **({"capitulo": str(c["capitulo"]).strip()[:80]} if c.get("capitulo") else {})}
             for c in job["cenas"] if c.get("texto", "").strip()]
    nomes, n_trailers = jogos_necessarios(job, cenas)

    st.etapa("Buscando trailers e imagens oficiais", 5)
    links = [x for x in (job.get("links_video") or []) if x.get("url")]
    bib = da_biblioteca(list(dict.fromkeys(nomes + [c["assunto"] for c in cenas if c.get("assunto")])))
    links += [b for b in bib if not any(b.get("url") and b["url"] == x["url"] for x in links)]
    steam_ids = [int(x) for x in ((job.get("materia") or {}).get("steam") or []) if str(x).isdigit()][:4]
    jogos = garantir_assets(nomes, permitir_vazio=bool(job.get("imagens_materia") or links or (job.get("materia") or {}).get("titulo")),
                            trailers=n_trailers, steam_ids=steam_ids)
    if links:
        st.etapa("Baixando os vídeos dos links", 12)
        jogos = juntar_links(jogos, links, nomes)

    if False and job.get("formato") == "horizontal" and nomes:
        # (desligado: a gameplay longa agora entra só quando faltar trecho — ver o complemento depois do banco)
        st.etapa("Buscando gameplay para o vídeo longo", 14)
        assuntos = [c.get("assunto") for c in cenas if c.get("assunto")]
        top = sorted(dict.fromkeys(nomes), key=lambda n: -assuntos.count(n))[:2]
        for n in top:
            j = next((x for x in jogos if assets.mesmo_jogo(n, x["nome"], 0.4) or norm(n) == norm(x["nome"])), None)
            if not j:
                continue
            g = assets.gameplay_longo(j["nome"], j["chave"], RAW)
            if g:
                j["trailers"].append(g)
    st.etapa("Escolhendo os melhores trechos dos trailers", 18)

    def prog(k, n):
        st.etapa(f"Escolhendo os melhores trechos dos trailers ({k}/{n})", 18 + int(10 * k / n))
    banco = assets.montar_banco(jogos, os.path.join(pasta, "thumbs"), prog)
    banco = assets.segunda_rodada(jogos, banco, RAW, [c["assunto"] for c in cenas if c.get("assunto")] + nomes[:1])
    # pouco trecho de vídeo para o tamanho do roteiro = o mesmo trecho voltando várias vezes (fica chato).
    # Completa com gameplay longa (janelas espalhadas) dos jogos com mais tempo de tela.
    palavras_rot = sum(len(c["texto"].split()) for c in cenas)
    cortes_previstos = palavras_rot / 3.1 / (3.2 if job.get("formato") == "horizontal" else 1.7)
    assuntos_l = [c.get("assunto") for c in cenas if c.get("assunto")] or nomes[:1]
    for j in sorted(jogos, key=lambda x: -sum(1 for a in assuntos_l if assets.mesmo_jogo(a, x["nome"], 0.4) or norm(a) == norm(x["nome"]))):
        fatia = sum(1 for a in assuntos_l if assets.mesmo_jogo(a, j["nome"], 0.4) or norm(a) == norm(j["nome"])) / max(len(assuntos_l), 1)
        precisa = cortes_previstos * max(fatia, 0.3) * 0.8
        tem = sum(1 for m in banco.values() if m["jogo"] == j["chave"] and m["tipo"] == "v")
        if fatia < 0.2 or tem >= precisa or any("_g_" in t["arquivo"] for t in j["trailers"]):
            continue
        st.etapa(f"Buscando mais cenas de {j['nome']} (tinha {tem} trechos)", 26)
        g = assets.gameplay_longo(j["nome"], j["chave"], RAW)
        if g:
            extra = dict(j, trailers=[g], screens=[])
            banco.update(assets.montar_banco([extra], os.path.join(pasta, "thumbs"), ti0=len(j["trailers"])))
            j["trailers"].append(g)
            print(f"{j['nome']}: {tem} → {sum(1 for m in banco.values() if m['jogo'] == j['chave'] and m['tipo'] == 'v')} trechos de vídeo", flush=True)
    banco.update(assets.baixar_imagens_materia(job.get("imagens_materia"), RAW))
    # fotos reais (Wikimedia Commons, licença livre) do que o tópico mostra quando não é jogo:
    # console, controle, pessoa, empresa, evento
    fotos_termo = {}
    for termo in dict.fromkeys(c["imagem"] for c in cenas if c.get("imagem")):
        try:
            achadas = assets.imagens_commons(termo, RAW, n=3)
        except Exception as e:
            print(f"aviso: Commons '{termo}': {e}", flush=True)
            achadas = []
        fotos_termo[termo] = []
        for ent in achadas:
            mid = os.path.splitext(ent["arquivo"])[0]
            banco[mid] = ent
            fotos_termo[termo].append(mid)
    sprite = assets.sprite(banco, RAW, os.path.join(pasta, "cenas.jpg"))
    assuntos = {c["assunto"] for c in cenas if c.get("assunto")} | set(nomes[:1])
    sem_video = sorted(a for a in assuntos if not any(
        m["tipo"] == "v" and m["jogo"] == (chave_do_jogo(a, jogos) or "") for m in banco.values()))

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
    for c in cenas:
        fotos = fotos_termo.get(c.get("imagem")) or []
        if fotos:  # a foto do que está sendo falado vem antes do material do jogo
            c["midias"] = fotos + [m for m in c.get("midias", []) if m not in fotos]
    projeto = {
        "versao": 1, "id": job.get("id"), "titulo": job.get("titulo", ""), "fonte": job.get("fonte", ""),
        "materia": job.get("materia") or {},
        "formato": "horizontal" if job.get("formato") == "horizontal" else "vertical",
        "voz": {"modo": modo, "arquivo": os.path.basename(audio), "reducao_ruido": "auto"},
        "jogos": [{"chave": j["chave"], "nome": j["nome"], "appid": j["appid"]} for j in jogos],
        "nomes_busca": nomes, "trailers_busca": n_trailers, "links_video": links, "sem_video": sem_video, "cenas": cenas, "palavras": palavras, "faltando": faltando,
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
        if ln.get("chave") and ln.get("url"):
            assets.baixar_link_video(ln["url"], ln["chave"], RAW, i)  # já baixado: não faz nada
    if any(m["arquivo"].startswith("lib_") for m in falta):
        da_biblioteca(projeto.get("nomes_busca") or [j["nome"] for j in projeto["jogos"]])
    for m in falta:
        if m.get("jogo") == "commons":
            try:
                assets.baixar_commons(m, RAW)
            except Exception as e:
                print(f"aviso: foto do Commons não baixou de novo: {e}", flush=True)
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
    render.definir_formato(projeto)  # cartões e render no tamanho certo (9:16 ou 16:9)
    try:
        garantir_trilha(projeto)
    except Exception as e:
        print(f"aviso: trilha de fundo: {e}", flush=True)
    try:
        if cartoes.aplicar(projeto, RAW, FONTES, os.path.join(pasta, "trab")):
            projeto["sprite"] = assets.sprite(projeto["banco"], RAW, os.path.join(pasta, "cenas.jpg"))
    except Exception as e:
        print(f"aviso: cartões não gerados: {e}", flush=True)
    st.etapa("Tratando a voz", p0)
    escolha = projeto["voz"].get("reducao_ruido", "auto")
    forcar = None if escolha == "auto" else escolha == "sim"
    wav = os.path.join(pasta, "voz_final.wav")
    info = voz.montar(audio_limpo(audio), projeto["palavras"], wav, MODELO_RNN, projeto["voz"]["modo"], forcar)
    projeto["duracao_voz"] = info["duracao"]
    projeto["voz"].update({"ruido_db": info["ruido_db"], "reducao_aplicada": info["reducao_ruido"]})
    projeto["avisos_voz"] = info.get("avisos") or []

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
    model = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    pub = publicador()
    for modo in ("lote", "isolado"):
        t0 = _t.time()
        pal = fala.transcrever(audio, MODELO_WHISPER, dicas, modo=modo, model=model)
        destino = os.path.join(pasta, f"teste_{modo}.json")
        gravar(destino, {"segundos": round(_t.time() - t0, 1), "palavras": pal})
        print(f"teste {modo}: {_t.time() - t0:.0f}s", flush=True)
        if pub:
            pub(destino)


TESTE_LINKS = ["https://www.rockstargames.com/videos/rkoCtr1r", "https://www.rockstargames.com/videos/video/rkoCtr1r"]


def teste_fontes(pasta, st):
    """Diagnóstico: o que o Dailymotion tem do jogo (gameplay longa e vídeos curtos)."""
    import io
    import contextlib
    import urllib.parse
    rel, buf = {}, io.StringIO()
    for termo in ("Car Parking Multiplayer 2", "Car Parking Multiplayer 2 gameplay", "CPM 2 gameplay", "Car Parking Multiplayer 2 PC"):
        try:
            d = json.loads(assets._get("https://api.dailymotion.com/videos?" + urllib.parse.urlencode(
                {"search": termo, "fields": "id,title,duration", "limit": 15})))
            rel[termo] = [f"{v['title']} ({v['duration']}s)" for v in d.get("list", [])]
        except Exception as e:
            rel[termo] = str(e)
    with contextlib.redirect_stdout(buf):
        rel["gameplay_longo"] = assets.gameplay_longo("Car Parking Multiplayer 2", "teste_cpm", RAW)
        rel["curtos"] = assets._dailymotion("Car Parking Multiplayer 2", "teste_cpm", RAW, n=3)
    rel["log"] = buf.getvalue()[-3000:]
    destino = os.path.join(pasta, "teste_fontes.json")
    gravar(destino, rel)
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
