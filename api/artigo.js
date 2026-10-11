// GET /api/artigo?url=...  -> { titulo, resumo, texto, imagem } da matéria (o navegador não lê outros sites por CORS)
const { checarSenha, responder, httpError } = require('./_gh');

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36';

const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', '#39': "'", hellip: '…', ndash: '–', mdash: '—', laquo: '«', raquo: '»' };
function limpar(html) {
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&(#x?[0-9a-f]+|\w+);/gi, (m, e) => {
      if (e[0] === '#') {
        const n = e[1].toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        return Number.isFinite(n) ? String.fromCodePoint(n) : ' ';
      }
      return ENT[e.toLowerCase()] ?? m;
    })
    .replace(/\s+/g, ' ')
    .trim();
}
function meta(html, prop) {
  const re = new RegExp(`<meta[^>]+(?:property|name)=["']${prop}["'][^>]*content=["']([^"']*)["']`, 'i');
  const re2 = new RegExp(`<meta[^>]+content=["']([^"']*)["'][^>]*(?:property|name)=["']${prop}["']`, 'i');
  const m = html.match(re) || html.match(re2);
  return m ? limpar(m[1]) : '';
}

module.exports = (req, res) => responder(res, async () => {
  checarSenha(req);
  const url = String((req.query || {}).url || '');
  if (!/^https?:\/\//i.test(url)) throw httpError(400, 'Cole o link completo da matéria (começando com https://).');
  const r = await fetch(url, { headers: { 'User-Agent': UA, 'Accept-Language': 'pt-BR,pt;q=0.9' }, redirect: 'follow' });
  if (r.status === 403 || r.status === 503) {
    throw httpError(502, 'O site bloqueou a leitura automática (proteção anti-robô). Cole o texto da matéria no campo abaixo.');
  }
  if (!r.ok) throw httpError(502, `O site respondeu com erro ${r.status}.`);
  let html = await r.text();
  const titulo = meta(html, 'og:title') || limpar((html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i) || [])[1] || '') ||
    limpar((html.match(/<title>([\s\S]*?)<\/title>/i) || [])[1] || '');
  const resumo = meta(html, 'og:description') || meta(html, 'description');
  const imagem = meta(html, 'og:image');

  html = html.replace(/<(script|style|noscript|svg|nav|footer|header|aside|form)[\s\S]*?<\/\1>/gi, ' ');
  // prioriza o corpo da matéria (WordPress e afins); senão, a página toda
  const corpo = (html.match(/<article[\s\S]*?<\/article>/i) ||
    html.match(/<div[^>]+class=["'][^"']*(entry-content|post-content|article-content|content-body)[^"']*["'][\s\S]*?<\/div>\s*<\/div>/i) ||
    [html])[0];
  const blocos = [];
  corpo.replace(/<(p|h2|h3|li|blockquote)[^>]*>([\s\S]*?)<\/\1>/gi, (m, tag, inner) => {
    const t = limpar(inner);
    if (t.length > 25 && !/compartilhe|cookies|newsletter|todos os direitos|leia também/i.test(t)) {
      blocos.push(/^h/i.test(tag) ? `## ${t}` : t);
    }
    return m;
  });
  const texto = [...new Set(blocos)].join('\n').slice(0, 14000);
  if (texto.length < 200) throw httpError(422, 'Não consegui ler o texto dessa página. Cole o texto da matéria no campo abaixo.');
  return { titulo, resumo, texto, imagem, url };
});
