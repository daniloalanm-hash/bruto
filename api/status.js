// GET /api/status?tag=narrado-...           -> andamento do render + links dos arquivos prontos
// GET /api/status?tag=narrado-...&projeto=1 -> conteúdo do projeto.json (para a tela de revisão)
// GET /api/status?lista=1                   -> vídeos já feitos (mais recentes primeiro)
// GET /api/status?tag=narrado-...&previa=1  -> MP3 leve da gravação (bytes), para a prévia instantânea na tela
const { gh, REPO, releasePorTag, lerAsset, checarSenha, responder, httpError } = require('./_gh');

module.exports = (req, res) => responder(res, async () => {
  checarSenha(req);
  const q = req.query || {};

  if (q.lista) {
    const rels = await gh(`/repos/${REPO}/releases?per_page=30`);
    return {
      videos: rels.filter((r) => r.tag_name.startsWith('narrado-')).map((r) => ({
        tag: r.tag_name,
        titulo: r.name.replace(/^Narrado · /, ''),
        criado: r.created_at,
        pronto: r.assets.some((a) => a.name === 'final.mp4'),
      })),
    };
  }

  const tag = String(q.tag || '');
  if (!/^narrado-[a-z0-9]+$/.test(tag)) throw httpError(400, 'Vídeo inválido.');
  const rel = await releasePorTag(tag);
  const asset = (nome) => rel.assets.find((a) => a.name === nome);

  if (q.previa) {
    const a = asset('voz_previa.mp3');
    if (!a) throw httpError(404, 'A prévia ainda não está disponível para este vídeo (gere uma nova versão).');
    const r = await fetch(`https://api.github.com/repos/${REPO}/releases/assets/${a.id}`, {
      headers: { Authorization: `Bearer ${process.env.GH_TOKEN}`, Accept: 'application/octet-stream', 'User-Agent': 'bruto' },
      redirect: 'follow',
    });
    if (!r.ok) throw httpError(502, `Não consegui ler a prévia (${r.status}).`);
    const buf = Buffer.from(await r.arrayBuffer());
    res.setHeader('Content-Type', 'audio/mpeg');
    res.setHeader('Cache-Control', 'private, max-age=300');
    res.end(buf);
    return undefined;
  }

  if (q.projeto) {
    const a = asset('projeto.json');
    if (!a) throw httpError(404, 'O projeto ainda não foi gerado.');
    return JSON.parse(await lerAsset(a));
  }

  let status = { estado: 'processando', etapa: 'Na fila do servidor de render', progresso: 0 };
  const st = asset('status.json');
  if (st) {
    try { status = JSON.parse(await lerAsset(st)); } catch { /* status em escrita: mantém o anterior */ }
  }
  // atualizado_em força a tela a não reaproveitar um vídeo antigo do cache do navegador
  const link = (nome) => {
    const a = asset(nome);
    return a ? `${a.browser_download_url}?v=${encodeURIComponent(a.updated_at)}` : null;
  };
  return {
    tag,
    titulo: rel.name.replace(/^Narrado · /, ''),
    status,
    video: link('final.mp4'),
    cenas: link('cenas.jpg'),
    temProjeto: !!asset('projeto.json'),
    temPrevia: !!asset('voz_previa.mp3'),
    temLimpo: !!asset('limpo.mp4'),
  };
});
