// Biblioteca de mídias por jogo: vídeos/imagens/links que você manda UMA vez e entram sozinhos em todo vídeo
// que falar daquele jogo. Fica numa release própria ("narrado-biblioteca") com um índice biblioteca.json.
//   GET  /api/biblioteca                                  -> { itens: [...] }
//   POST { acao: "parte", nome, n, base64 }               -> sobe parte de um arquivo (lib-<id>.<ext>.part<n>)
//   POST { acao: "salvar", item: { jogo, tipo, nome?, url?, original? } } -> registra no índice
//   POST { acao: "remover", id }                          -> tira do índice (e apaga as partes)
const { gh, REPO, subirAsset, lerAsset, checarSenha, responder, lerCorpo, httpError } = require('./_gh');

const TAG = 'narrado-biblioteca';

async function release(criar = false) {
  try {
    return await gh(`/repos/${REPO}/releases/tags/${TAG}`);
  } catch (e) {
    if (e.status !== 404 || !criar) throw e;
    return gh(`/repos/${REPO}/releases`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tag_name: TAG, target_commitish: 'main', name: 'Narrado · Biblioteca de mídias', prerelease: true,
        body: 'Vídeos, imagens e links por jogo, usados automaticamente pelo Modo Narrado. Não apague.',
      }),
    });
  }
}

async function indice(rel) {
  const a = (rel.assets || []).find((x) => x.name === 'biblioteca.json');
  if (!a) return [];
  try { return JSON.parse(await lerAsset(a)); } catch { return []; }
}

module.exports = (req, res) => responder(res, async () => {
  checarSenha(req);
  if (req.method === 'GET') {
    let rel;
    try { rel = await release(false); } catch (e) { if (e.status === 404) return { itens: [] }; throw e; }
    return { itens: await indice(rel) };
  }
  if (req.method !== 'POST') throw httpError(405, 'Use GET ou POST.');
  const corpo = await lerCorpo(req);

  if (corpo.acao === 'parte') {
    const nome = String(corpo.nome || '').toLowerCase();
    if (!/^lib-[a-z0-9]{4,20}\.(jpg|jpeg|png|webp|mp4|mov|webm|m4v)$/.test(nome)) throw httpError(400, 'Arquivo inválido.');
    const n = Number(corpo.n);
    if (!Number.isInteger(n) || n < 0 || n > 120) throw httpError(400, 'Parte inválida (arquivo grande demais?).');
    const buf = Buffer.from(String(corpo.base64 || ''), 'base64');
    if (!buf.length) throw httpError(400, 'Parte vazia.');
    const rel = await release(true);
    await subirAsset(rel, `${nome}.part${String(n).padStart(3, '0')}`, buf);
    return { ok: true };
  }

  if (corpo.acao === 'salvar') {
    const it = corpo.item || {};
    const jogo = String(it.jogo || '').trim().slice(0, 120);
    if (!jogo) throw httpError(400, 'Diga de qual jogo é.');
    const item = { id: Math.random().toString(36).slice(2, 10), jogo, criado: new Date().toISOString() };
    if (it.tipo === 'link') {
      if (!/^https?:\/\//.test(String(it.url || ''))) throw httpError(400, 'Link inválido.');
      Object.assign(item, { tipo: 'link', url: String(it.url).slice(0, 500) });
    } else {
      if (!/^lib-[a-z0-9]{4,20}\.(jpg|jpeg|png|webp|mp4|mov|webm|m4v)$/.test(String(it.nome || ''))) throw httpError(400, 'Arquivo inválido.');
      Object.assign(item, { tipo: 'arquivo', nome: it.nome, original: String(it.original || '').slice(0, 120) });
    }
    const rel = await release(true);
    const itens = await indice(rel);
    itens.push(item);
    await subirAsset(rel, 'biblioteca.json', JSON.stringify(itens, null, 1), 'application/json');
    return { ok: true, item, itens };
  }

  if (corpo.acao === 'remover') {
    const rel = await release(false);
    const itens = await indice(rel);
    const fora = itens.find((x) => x.id === corpo.id);
    const resto = itens.filter((x) => x.id !== corpo.id);
    await subirAsset(rel, 'biblioteca.json', JSON.stringify(resto, null, 1), 'application/json');
    if (fora && fora.nome) {
      for (const a of rel.assets || []) {
        if (a.name.startsWith(`${fora.nome}.part`)) await gh(`/repos/${REPO}/releases/assets/${a.id}`, { method: 'DELETE' });
      }
    }
    return { ok: true, itens: resto };
  }
  throw httpError(400, 'Ação desconhecida.');
});
