// POST /api/job — cria o vídeo, recebe o áudio em partes e dispara o render.
//   { acao: "criar", job }                       -> { tag }
//   { acao: "parte", tag, n, base64 }            -> sobe voz.part<n> (cada parte até ~3 MB)
//   { acao: "iniciar", tag, fase }               -> dispara o GitHub Actions (fase: preparar | render)
//   { acao: "projeto", tag, projeto, fase }      -> salva o projeto ajustado e dispara o re-render
//                                                 (fase "legenda": só a legenda mudou → render rápido)
//   { acao: "midia", tag, nome, n, base64 }      -> sobe parte de uma imagem/clipe próprio (pm-<id>.<ext>.part<n>)
const { gh, REPO, releasePorTag, subirAsset, disparar, checarSenha, responder, lerCorpo, httpError } = require('./_gh');

module.exports = (req, res) => responder(res, async () => {
  if (req.method !== 'POST') throw httpError(405, 'Use POST.');
  checarSenha(req);
  const corpo = await lerCorpo(req);
  const { acao } = corpo;

  if (acao === 'criar') {
    const job = corpo.job || {};
    if (!Array.isArray(job.cenas) || !job.cenas.length) throw httpError(400, 'O roteiro está vazio.');
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const tag = `narrado-${id}`;
    job.id = tag;
    const titulo = String(job.titulo || 'Vídeo narrado').slice(0, 80);
    const rel = await gh(`/repos/${REPO}/releases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tag_name: tag, target_commitish: 'main', name: `Narrado · ${titulo}`, prerelease: true,
        body: `Vídeo do Modo Narrado do Bruto.\n\nFonte: ${job.fonte || '—'}\n\nArquivos gerados automaticamente; pode apagar quando não precisar mais.`,
      }),
    });
    await subirAsset(rel, 'job.json', JSON.stringify(job, null, 1), 'application/json');
    return { tag };
  }

  const tag = String(corpo.tag || '');
  if (!/^narrado-[a-z0-9]+$/.test(tag)) throw httpError(400, 'Vídeo inválido.');

  if (acao === 'parte') {
    const n = Number(corpo.n);
    if (!Number.isInteger(n) || n < 0 || n > 60) throw httpError(400, 'Parte inválida.');
    const buf = Buffer.from(String(corpo.base64 || ''), 'base64');
    if (!buf.length) throw httpError(400, 'Parte vazia.');
    const rel = await releasePorTag(tag);
    await subirAsset(rel, `voz.part${String(n).padStart(3, '0')}`, buf);
    return { ok: true };
  }

  if (acao === 'midia') {
    const nome = String(corpo.nome || '').toLowerCase();
    if (!/^pm-[a-z0-9]{4,20}\.(jpg|jpeg|png|webp|mp4|mov|webm|m4v)$/.test(nome)) throw httpError(400, 'Arquivo inválido.');
    const n = Number(corpo.n);
    if (!Number.isInteger(n) || n < 0 || n > 80) throw httpError(400, 'Parte inválida (arquivo grande demais?).');
    const buf = Buffer.from(String(corpo.base64 || ''), 'base64');
    if (!buf.length) throw httpError(400, 'Parte vazia.');
    const rel = await releasePorTag(tag);
    await subirAsset(rel, `${nome}.part${String(n).padStart(3, '0')}`, buf);
    return { ok: true };
  }

  if (acao === 'projeto') {
    if (!corpo.projeto || !Array.isArray(corpo.projeto.palavras)) throw httpError(400, 'Projeto inválido.');
    const rel = await releasePorTag(tag);
    await subirAsset(rel, 'projeto.json', JSON.stringify(corpo.projeto), 'application/json');
    const fase = corpo.fase === 'legenda' && rel.assets.some((a) => a.name === 'limpo.mp4') ? 'legenda' : 'render';
    await subirAsset(rel, 'status.json', JSON.stringify({
      estado: 'processando', fase, etapa: 'Na fila do servidor de render', progresso: 1,
      atualizado: Math.floor(Date.now() / 1000),
    }), 'application/json');
    await disparar(tag, fase);
    return { ok: true, fase };
  }

  if (acao === 'iniciar') {
    const fase = corpo.fase === 'render' ? 'render' : 'preparar';
    const rel = await releasePorTag(tag);
    await subirAsset(rel, 'status.json', JSON.stringify({
      estado: 'processando', fase, etapa: 'Na fila do servidor de render', progresso: 1,
      atualizado: Math.floor(Date.now() / 1000),
    }), 'application/json');
    await disparar(tag, fase);
    return { ok: true };
  }

  throw httpError(400, 'Ação desconhecida.');
});
