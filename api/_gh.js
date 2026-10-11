// Acesso ao GitHub para o Modo Narrado. Cada vídeo é uma release "narrado-<id>" no repositório do Bruto:
// a tela sobe o pedido (job.json + áudio) e o GitHub Actions devolve o vídeo na mesma release.
//
// Variáveis de ambiente no Vercel:
//   GH_TOKEN       token "fine-grained" com Contents (read/write) e Actions (read/write) só neste repositório
//   NARRADO_SENHA  senha simples que a tela pede uma vez (evita que qualquer um dispare renders)
//   GH_REPO        opcional, padrão "daniloalanm-hash/bruto"

const REPO = process.env.GH_REPO || 'daniloalanm-hash/bruto';
const API = 'https://api.github.com';

function headers(extra = {}) {
  const token = (process.env.GH_TOKEN || '').trim();
  if (!token) throw httpError(500, 'Falta configurar o GH_TOKEN no Vercel (veja o README do Modo Narrado).');
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'bruto-narrado',
    ...extra,
  };
}

function httpError(status, message) {
  const e = new Error(message);
  e.status = status;
  return e;
}

async function gh(path, opts = {}) {
  const res = await fetch(path.startsWith('http') ? path : `${API}${path}`, {
    ...opts,
    headers: headers(opts.headers),
  });
  if (res.status === 204) return null;
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const msg = (body && body.message) || text || `erro ${res.status}`;
    throw httpError(res.status === 404 ? 404 : 502, `GitHub: ${msg}`);
  }
  return body;
}

async function releasePorTag(tag) {
  return gh(`/repos/${REPO}/releases/tags/${encodeURIComponent(tag)}`);
}

// Sobe (ou substitui) um arquivo na release.
async function subirAsset(release, nome, conteudo, tipo = 'application/octet-stream') {
  const existente = (release.assets || []).find((a) => a.name === nome);
  if (existente) await gh(`/repos/${REPO}/releases/assets/${existente.id}`, { method: 'DELETE' });
  const url = release.upload_url.replace(/\{.*\}$/, '') + `?name=${encodeURIComponent(nome)}`;
  return gh(url, { method: 'POST', body: conteudo, headers: { 'Content-Type': tipo } });
}

// Lê o conteúdo de um arquivo da release (pela API: sempre a versão mais nova, sem cache de CDN).
async function lerAsset(asset) {
  const res = await fetch(`${API}/repos/${REPO}/releases/assets/${asset.id}`, {
    headers: headers({ Accept: 'application/octet-stream' }),
    redirect: 'follow',
  });
  if (!res.ok) throw httpError(502, `Não consegui ler ${asset.name} (${res.status}).`);
  return res.text();
}

async function disparar(tag, fase) {
  await gh(`/repos/${REPO}/actions/workflows/narrado.yml/dispatches`, {
    method: 'POST',
    body: JSON.stringify({ ref: 'main', inputs: { tag, fase } }),
    headers: { 'Content-Type': 'application/json' },
  });
}

function checarSenha(req) {
  const esperada = (process.env.NARRADO_SENHA || '').trim();
  if (!esperada) return; // sem senha configurada, a tela fica aberta
  const enviada = String(req.headers['x-narrado-senha'] || '').trim();
  if (enviada !== esperada) throw httpError(401, 'Senha do Modo Narrado incorreta.');
}

function responder(res, fn) {
  return Promise.resolve()
    .then(fn)
    .then((dados) => {
      res.setHeader('Cache-Control', 'no-store');
      res.status(200).json(dados);
    })
    .catch((e) => {
      res.setHeader('Cache-Control', 'no-store');
      res.status(e.status || 500).json({ erro: e.message || 'Erro inesperado.' });
    });
}

async function lerCorpo(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  if (typeof req.body === 'string') return JSON.parse(req.body || '{}');
  const partes = [];
  for await (const p of req) partes.push(p);
  return JSON.parse(Buffer.concat(partes).toString('utf8') || '{}');
}

module.exports = { REPO, gh, releasePorTag, subirAsset, lerAsset, disparar, checarSenha, responder, lerCorpo, httpError };
