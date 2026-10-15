/* Bruto — Modo Narrado
   Pauta (link da notícia) → roteiro (Gemini, chave salva no navegador) → voz (gravada aqui, arquivo ou IA)
   → vídeo montado no GitHub Actions → revisão (cortes de fala, legenda, cenas) → re-render.
*/
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'value') el.value = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function toast(msg, kind = 'info', ms = 4200) {
  const t = h('div', { class: `toast ${kind}` }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}
const normal = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[^a-z0-9]/g, '');
const fmtT = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

/* ---------------- armazenamento local ---------------- */
const K = {
  gemini: 'bruto:gemini:key', modelo: 'bruto:gemini:model', senha: 'bruto:narrado:senha', rasc: 'bruto:narrado:rascunho',
  estilo: 'bruto:narrado:estilo', lote: 'bruto:narrado:lote', post: 'bruto:narrado:post:', fila: 'bruto:narrado:fila',
  duracao: 'bruto:narrado:duracao', ritmo: 'bruto:narrado:ritmo',
};
// aparência escolhida na aba Cenas vira o padrão dos próximos vídeos
const CHAVES_ESTILO = ['jl_quadros', 'transicao_gancho', 'titulo_estilo', 'legenda_estilo', 'legenda_cor', 'legenda_altura', 'sfx', 'zoom_enfase', 'emoji'];
function estiloSalvo() { try { return JSON.parse(ls.get(K.estilo) || '{}'); } catch { return {}; } }
function salvarEstilo(p) {
  const e = {};
  for (const k of CHAVES_ESTILO) if (p[k] !== undefined) e[k] = p[k];
  ls.set(K.estilo, JSON.stringify(e));
}
const ls = {
  get(k) { try { return localStorage.getItem(k) || ''; } catch { return ''; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* navegador sem armazenamento: segue sem salvar */ } },
};
const limpaToken = (v) => String(v || '').replace(/[^\x21-\x7E]/g, '').replace(/^["']+|["']+$/g, '');

const state = {
  passo: 'pauta',
  materia: null,           // { titulo, resumo, texto, imagem, url }
  roteiro: { titulo: '', jogos: [], cenas: [] },
  voz: { modo: 'gravar', blob: null, nome: '' },
  tag: '',
  projeto: null,
  sujo: false,             // ajustes não aplicados
  polling: 0,
};

function salvarRascunho() {
  ls.set(K.rasc, JSON.stringify({ passo: state.passo, materia: state.materia, roteiro: state.roteiro, tag: state.tag }));
}

/* ---------------- API do Bruto ---------------- */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', 'x-narrado-senha': ls.get(K.senha), ...(opts.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) {
    abrirConfig('Informe a senha do Modo Narrado para continuar.');
    throw new Error(body.erro || 'Senha necessária.');
  }
  if (!res.ok) throw new Error(body.erro || `Erro ${res.status}`);
  return body;
}

/* ---------------- Gemini (mesma chave do editor do Bruto) ---------------- */
const MODELOS = ['gemini-flash-latest', 'gemini-3.6-flash', 'gemini-flash-lite-latest'];
async function gemini(prompt, onStatus = () => {}) {
  const key = limpaToken(ls.get(K.gemini));
  if (!key) { abrirConfig('Cadastre a chave gratuita do Gemini para escrever o roteiro.'); throw new Error('Falta a chave do Gemini.'); }
  const preferido = limpaToken(ls.get(K.modelo));
  const modelos = [...new Set([preferido, ...MODELOS].filter(Boolean))];
  const payload = JSON.stringify({ contents: [{ role: 'user', parts: [{ text: prompt }] }], generationConfig: { responseMimeType: 'application/json', temperature: 0.8 } });
  let ultimo = '';
  for (const m of modelos) {
    for (let tentativa = 0; tentativa < 3; tentativa++) {
      if (tentativa) { onStatus(`IA ocupada, tentando de novo (${m})…`); await sleep(tentativa * 4000); }
      const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(m)}:generateContent`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key }, body: payload,
      });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        const txt = (body.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('');
        const a = txt.indexOf('{'), b = txt.lastIndexOf('}');
        if (a >= 0 && b > a) return JSON.parse(txt.slice(a, b + 1));
        ultimo = 'A IA não devolveu um roteiro válido.';
        continue;
      }
      ultimo = body?.error?.message || `erro ${res.status}`;
      if (res.status === 400 && /api key/i.test(ultimo)) throw new Error('A chave do Gemini é inválida. Gere outra em aistudio.google.com/apikey.');
      if (res.status === 404 || res.status === 429) break; // modelo inexistente ou sem cota: próximo
      if (res.status < 500) throw new Error(`Gemini: ${ultimo}`);
    }
  }
  throw new Error(`Gemini indisponível agora (${ultimo}). Tente em alguns minutos.`);
}

// ritmo do vídeo pronto (palavras/s depois dos cortes): começa em 3,1 (média medida nos seus vídeos)
// e se ajusta sozinho com a média dos últimos vídeos que você abriu
const MONETIZA = 60; // TikTok: só monetiza vídeo com mais de 1 minuto
function ritmo() {
  try { const r = JSON.parse(ls.get(K.ritmo) || '[]'); if (r.length) return r.reduce((a, b) => a + b, 0) / r.length; } catch { /* padrão */ }
  return 3.1;
}
function registrarRitmo(p) {
  if (!p?.duracao || p.voz?.modo !== 'gravada') return;
  const n = (p.palavras || []).filter((w) => w.manter).length;
  const r = n / p.duracao;
  if (!(r > 1.5 && r < 5)) return;
  let l = [];
  try { l = JSON.parse(ls.get(K.ritmo) || '[]'); } catch { l = []; }
  if (l.at(-1) === +r.toFixed(3)) return;
  ls.set(K.ritmo, JSON.stringify([...l, +r.toFixed(3)].slice(-6)));
}
const contarPalavras = (cenas) => cenas.reduce((a, c) => a + String(c.texto || '').split(/\s+/).filter(Boolean).length, 0);
// meta de palavras: para 1 min+ mira ~10% acima (a edição corta pausas e o vídeo encolhe)
function metaPalavras(segundos) {
  const alvo = segundos >= MONETIZA ? Math.max(segundos, 68) : segundos;
  return { alvo: Math.round(alvo * ritmo()), minimo: Math.round((segundos >= MONETIZA ? 64 : segundos * 0.85) * ritmo()) };
}

function promptRoteiro(materia, segundos) {
  const { alvo: palavras, minimo } = metaPalavras(segundos);
  const longo = segundos >= MONETIZA;
  return `Você é roteirista de vídeos curtos virais de games (TikTok/Reels/Shorts) no estilo "narração sem rosto": uma voz conta a história enquanto aparecem cenas dos jogos com legenda grande.

Escreva o roteiro a partir da matéria abaixo.

REGRAS
- Português do Brasil, falado e natural, frases curtas, como quem conta uma novidade para um amigo gamer.
- ${longo ? `TAMANHO É OBRIGATÓRIO: entre ${palavras} e ${palavras + 25} palavras no total, NUNCA menos de ${minimo} (o vídeo precisa passar de 1 minuto para monetizar).` : `Cerca de ${palavras} palavras no total (~${segundos} segundos de fala).`}
- A PRIMEIRA frase é um gancho forte (curiosidade, contraste ou número surpreendente). Nada de "neste vídeo".
- Divida em ${longo ? '9 a 13' : '6 a 10'} tópicos. Cada tópico = uma ideia; trocar de tópico = trocar de assunto/jogo.${longo ? `
- Para chegar ao tamanho SEM enrolar (o ritmo tem que continuar rápido, frases curtas):
  - logo depois do gancho, abra uma curiosidade que só se resolve no fim ("e o último detalhe é o mais curioso");
  - aproveite TODOS os dados da matéria: números, datas, preço, plataformas, nomes, comparações, o que muda para o jogador;
  - dê contexto que o público gamer precisa para entender (o que é o jogo, de qual franquia/estúdio, o que aconteceu antes), desde que seja informação pública e conhecida;
  - um tópico de "por que isso importa" ou "o que isso significa para quem joga";
  - mini-ganchos entre tópicos ("mas não para por aí", "agora vem a parte boa").
- Nada de repetir a mesma informação com outras palavras só para encher.` : ''}
- FECHAMENTO (é o que gera salvamento e compartilhamento, que fazem o vídeo ser entregue para mais gente):
  - o penúltimo tópico entrega a "recompensa" prometida no começo (o detalhe mais curioso/surpreendente fica para o fim);
  - o último tópico junta, em no máximo 2 frases curtas: uma pergunta para comentar + UM pedido de ação ESPECÍFICO, escolhendo o que combina com o assunto:
    - compartilhar com alguém concreto: "manda pra aquele amigo que [joga X / está esperando Y / vive reclamando de Z]";
    - salvar quando o vídeo tem informação útil (datas, lista de jogos, preço, requisitos): "salva pra não esquecer a data" / "salva pra consultar depois";
  - nada de "curte, comenta, compartilha e segue" genérico.
- Se o vídeo for uma lista (vários jogos, várias datas), diga no começo quantos itens são ("são 5 jogos, e o último...") para a pessoa ficar até o fim.
- Números em algarismos (2026, 30, 11). Sem reticências, sem emojis, sem hashtags, sem aspas.
- Use os nomes dos jogos exatamente como aparecem na loja da Steam.
- Não invente fatos que não estão na matéria.

Para cada tópico informe:
- "texto": o que será falado.
- "destaques": 2 a 4 palavras ou expressões curtas do próprio texto (copiadas exatamente) que merecem destaque colorido na legenda.
- "assunto": nome do jogo de que esse trecho está falando (para mostrar imagens desse jogo). Use null se for um comentário geral — aí aparece o jogo principal. NUNCA ponha um jogo que não está sendo citado naquele trecho.
- "emoji": UM emoji que combine com a emoção do tópico (ex.: 💀 fim/morte, 💰 dinheiro, 😱 surpresa, 🔥 hype, 🏆 recorde, ⏳ tempo). Use em no máximo metade dos tópicos; nos outros, null.

Também:
- "titulo_tela": texto curto e chamativo (3 a 6 palavras) que aparece grande na tela nos 2 primeiros segundos, reforçando o gancho. Ex.: "O FIM DO RAINBOW SIX MOBILE".
- "post": legenda para publicar o vídeo no TikTok/Reels: {"legenda": "1 a 2 frases que instigam + pergunta + o mesmo pedido de salvar/compartilhar do fim do vídeo", "hashtags": ["#5 a 8 hashtags relevantes, em português quando fizer sentido"]}.

Responda SOMENTE com JSON:
{"titulo": "...", "titulo_tela": "...", "jogos": ["nome do jogo principal", "outros jogos citados"], "cenas": [{"texto": "...", "destaques": ["..."], "assunto": "...", "emoji": null}], "post": {"legenda": "...", "hashtags": ["#..."]}}

MATÉRIA
Título: ${materia.titulo || ''}
${materia.resumo || ''}
${materia.texto || ''}`;
}

/* ---------------- navegação entre passos ---------------- */
const PASSOS = ['pauta', 'roteiro', 'voz', 'video'];
function irPara(passo) {
  state.passo = passo;
  PASSOS.forEach((p, i) => {
    $(`#passo-${p}`).hidden = p !== passo;
    const li = $(`.trilha li[data-passo="${p}"]`);
    li.classList.toggle('ativo', p === passo);
    li.classList.toggle('feito', i < PASSOS.indexOf(passo));
  });
  if (passo === 'roteiro') renderRoteiro();
  if (passo === 'voz') renderTeleprompter();
  atualizarBotaoProximo();
  salvarRascunho();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
$$('[data-ir]').forEach((b) => b.addEventListener('click', () => irPara(b.dataset.ir)));

/* ---------------- 1. pauta ---------------- */
function atualizaBotaoRoteiro() {
  $('#btnRoteiro').disabled = !(state.materia?.texto || $('#textoMateria').value.trim().length > 20);
}
$('#textoMateria').addEventListener('input', atualizaBotaoRoteiro);
// Sites WordPress (como o blog da Loja dos Gifts) entregam a matéria pela API REST deles, e o
// navegador consegue ler direto. Isso evita a proteção anti-robô (Cloudflare) que bloqueia servidores.
function textoDeHtml(html) {
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  doc.querySelectorAll('script,style,figure,.sharedaddy,.jp-relatedposts').forEach((n) => n.remove());
  return $$('p, h2, h3, li, blockquote', doc).map((n) => {
    const t = n.textContent.replace(/\s+/g, ' ').trim();
    return /^H/.test(n.tagName) ? `## ${t}` : t;
  }).filter((t) => t.length > 25 && !/compartilhe|leia também|newsletter/i.test(t)).join('\n');
}
// Imagens da matéria (são as que mostram de fato a novidade): destaque + imagens do corpo, sem ícones/logos
function imagensDeHtml(html, base) {
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  const urls = [];
  for (const img of doc.querySelectorAll('img')) {
    let src = img.getAttribute('data-orig-file') || img.getAttribute('data-src') || img.getAttribute('src') || '';
    const srcset = img.getAttribute('srcset') || img.getAttribute('data-srcset');
    if (srcset) { // maior versão do srcset
      const maior = srcset.split(',').map((x) => x.trim().split(/\s+/)).sort((a, b) => parseInt(b[1] || 0, 10) - parseInt(a[1] || 0, 10))[0];
      if (maior?.[0]) src = maior[0];
    }
    const w = parseInt(img.getAttribute('width') || '0', 10);
    if (w && w < 300) continue;
    urls.push(src);
  }
  return limparImagens(urls, base);
}
function limparImagens(urls, base) {
  const vistos = new Set();
  const saida = [];
  for (let u of urls) {
    try { u = new URL(u, base).href; } catch { continue; }
    if (!/^https?:/.test(u) || /\.(svg|gif)(\?|$)/i.test(u) || /logo|avatar|icon|emoji|gravatar|pixel|banner-ad|placeholder/i.test(u)) continue;
    const chave = u.split('?')[0].replace(/-\d+x\d+(?=\.\w+$)/, '').replace(/^https?:\/\/i\d\.wp\.com\//, '');
    if (vistos.has(chave)) continue;
    vistos.add(chave);
    saida.push(u);
  }
  return saida.slice(0, 8);
}

async function materiaWordPress(url) {
  const u = new URL(url);
  const partes = u.pathname.split('/').filter(Boolean);
  const slug = partes[partes.length - 1];
  if (!slug) return null;
  const bases = [];
  for (let i = 0; i < partes.length - 1 && i < 3; i++) {
    if (/^\d{4}$/.test(partes[i])) break; // /2026/10/11/slug: a base é o que vem antes da data
    bases.push('/' + partes.slice(0, i + 1).join('/'));
  }
  bases.reverse().push('');
  for (const base of [...new Set(bases)]) {
    try {
      const r = await fetch(`${u.origin}${base}/wp-json/wp/v2/posts?slug=${encodeURIComponent(slug)}&_fields=title,content,excerpt,link,jetpack_featured_media_url`);
      if (!r.ok) continue;
      const [post] = await r.json();
      if (!post) continue;
      const imagens = limparImagens([post.jetpack_featured_media_url, ...imagensDeHtml(post.content?.rendered, post.link || url)].filter(Boolean), post.link || url);
      return {
        imagens,
        titulo: new DOMParser().parseFromString(post.title?.rendered || '', 'text/html').body.textContent.trim(),
        resumo: new DOMParser().parseFromString(post.excerpt?.rendered || '', 'text/html').body.textContent.trim().slice(0, 300),
        texto: textoDeHtml(post.content?.rendered).slice(0, 14000),
        imagem: post.jetpack_featured_media_url || '',
        url: post.link || url,
      };
    } catch { /* não é WordPress, ou bloqueado: tenta o próximo caminho */ }
  }
  return null;
}

$('#btnBuscar').addEventListener('click', async () => {
  const url = $('#urlMateria').value.trim();
  if (!url) { toast('Cole o link da matéria.', 'warn'); return; }
  const btn = $('#btnBuscar');
  btn.classList.add('busy');
  try {
    let m = await materiaWordPress(url).catch(() => null);
    if (!m || m.texto.length < 200) m = await api(`/api/artigo?url=${encodeURIComponent(url)}`);
    state.materia = m;
    const p = $('#previaMateria');
    p.replaceChildren(
      m.imagem ? h('img', { src: m.imagem, alt: '' }) : h('div'),
      h('div', {}, h('b', {}, m.titulo), h('span', { class: 'muted small' }, `${m.texto.split(/\s+/).length} palavras lidas · ${m.resumo || ''}`),
        (m.imagens || []).length ? h('div', { class: 'fotos-materia' },
          h('span', { class: 'muted small' }, `${m.imagens.length} imagens da matéria entram nas cenas:`),
          ...m.imagens.map((u) => h('img', { src: u, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' }))) : null),
    );
    p.hidden = false;
    $('#textoMateria').value = m.texto;
    atualizaBotaoRoteiro();
  } catch (e) {
    toast(e.message, 'warn', 7000);
    $('#boxTexto').open = true;
  } finally { btn.classList.remove('busy'); }
});

function roteiroDaIA(r, materia) {
  return {
    titulo: r.titulo || materia.titulo || '',
    titulo_tela: String(r.titulo_tela || '').trim(),
    jogos: (r.jogos || []).filter(Boolean),
    cenas: (r.cenas || []).filter((c) => c.texto).map((c) => ({
      texto: c.texto, destaques: c.destaques || [], assunto: c.assunto || null, emoji: c.emoji || null,
    })),
    post: r.post && typeof r.post === 'object' ? { legenda: r.post.legenda || '', hashtags: r.post.hashtags || [] } : null,
  };
}

async function roteiroComTamanho(materia, segundos, onStatus = () => {}) {
  let r = roteiroDaIA(await gemini(promptRoteiro(materia, segundos), onStatus), materia);
  const { alvo, minimo } = metaPalavras(segundos);
  for (let tentativa = 0; tentativa < 2 && r.cenas.length && contarPalavras(r.cenas) < minimo; tentativa++) {
    onStatus(`Roteiro curto (${contarPalavras(r.cenas)} palavras): pedindo para a IA completar até ~${alvo}…`);
    const extra = await gemini(`${promptRoteiro(materia, segundos)}

ROTEIRO ATUAL (curto demais: ${contarPalavras(r.cenas)} palavras; precisa de ${alvo} a ${alvo + 25}):
${JSON.stringify({ titulo: r.titulo, titulo_tela: r.titulo_tela, jogos: r.jogos, cenas: r.cenas, post: r.post })}

Reescreva e AUMENTE este roteiro até o tamanho pedido: mantenha o gancho e o final, acrescente tópicos com dados e contexto da matéria (sem inventar e sem repetir). Responda no mesmo formato JSON.`, onStatus);
    const novo = roteiroDaIA(extra, materia);
    if (contarPalavras(novo.cenas) > contarPalavras(r.cenas)) r = novo;
  }
  return r;
}

async function escreverRoteiro() {
  const texto = $('#textoMateria').value.trim();
  const materia = { ...(state.materia || {}), texto: texto || state.materia?.texto || '' };
  if (!materia.titulo && texto) materia.titulo = texto.split('\n')[0].slice(0, 100);
  const st = $('#statusRoteiro');
  st.textContent = 'A IA está escrevendo o roteiro…';
  $('#btnRoteiro').classList.add('busy');
  try {
    state.roteiro = await roteiroComTamanho(materia, +$('#duracao').value, (m) => { st.textContent = m; });
    if (!state.roteiro.cenas.length) throw new Error('A IA devolveu um roteiro vazio. Tente de novo.');
    state.materia = materia;
    st.textContent = '';
    irPara('roteiro');
  } catch (e) {
    st.textContent = `Erro: ${e.message}`;
  } finally { $('#btnRoteiro').classList.remove('busy'); }
}
$('#btnRoteiro').addEventListener('click', escreverRoteiro);
$('#btnRefazerRoteiro').addEventListener('click', () => {
  if (!confirm('Reescrever o roteiro inteiro com a IA? As suas edições serão perdidas.')) return;
  irPara('pauta'); escreverRoteiro();
});
$('#btnRoteiroVazio').addEventListener('click', () => {
  state.roteiro = { titulo: state.materia?.titulo || '', jogos: [], cenas: [{ texto: '', destaques: [], assunto: null }] };
  irPara('roteiro');
});

/* ---------------- 1b. roteiros em lote ---------------- */
function lote() { try { return JSON.parse(ls.get(K.lote) || '[]'); } catch { return []; } }
function salvarLote(l) { ls.set(K.lote, JSON.stringify(l.slice(0, 20))); }
function renderLote() {
  const l = lote();
  $('#listaLote').replaceChildren(...l.map((d) => h('li', {},
    h('div', {}, h('b', {}, d.roteiro.titulo || d.materia?.titulo || 'Sem título'),
      h('span', { class: 'muted small' }, ` · ${d.roteiro.cenas.length} tópicos · ${new Date(d.criado).toLocaleDateString('pt-BR')}`)),
    h('div', { class: 'row' },
      h('button', { class: 'btn small primary', onclick: () => abrirDoLote(d.id) }, 'Abrir e gravar'),
      h('button', { class: 'btn small icon danger', title: 'Excluir', onclick: () => { salvarLote(lote().filter((x) => x.id !== d.id)); renderLote(); } }, '✕')))));
}
function abrirDoLote(id, passo = 'roteiro') {
  const d = lote().find((x) => x.id === id);
  if (!d) return;
  state.materia = d.materia;
  state.roteiro = d.roteiro;
  state.loteId = d.id;
  if (d.materia) { $('#urlMateria').value = d.materia.url || ''; $('#textoMateria').value = d.materia.texto || ''; }
  atualizaBotaoRoteiro();
  irPara(passo);
}
function proximoDoLote() { return lote().find((x) => x.id !== state.loteId) || null; }
async function buscarMateria(url) {
  let m = await materiaWordPress(url).catch(() => null);
  if (!m || m.texto.length < 200) m = await api(`/api/artigo?url=${encodeURIComponent(url)}`);
  return m;
}
$('#btnLote').addEventListener('click', async () => {
  const links = [...new Set($('#linksLote').value.split(/\s+/).map((x) => x.trim()).filter((x) => /^https?:\/\//.test(x)))].slice(0, 6);
  if (!links.length) { toast('Cole pelo menos um link (um por linha).', 'warn'); return; }
  const st = $('#statusLote'), btn = $('#btnLote');
  btn.classList.add('busy');
  let ok = 0;
  for (const [i, url] of links.entries()) {
    try {
      st.textContent = `(${i + 1}/${links.length}) Lendo a matéria…`;
      const m = await buscarMateria(url);
      st.textContent = `(${i + 1}/${links.length}) Escrevendo o roteiro: ${m.titulo}`;
      const r = await roteiroComTamanho(m, +$('#duracao').value, (x) => { st.textContent = `(${i + 1}/${links.length}) ${x}`; });
      if (!r.cenas.length) throw new Error('roteiro vazio');
      salvarLote([{ id: Math.random().toString(36).slice(2, 10), criado: Date.now(), materia: m, roteiro: r }, ...lote()]);
      ok++;
      renderLote();
    } catch (e) { toast(`Não consegui fazer o roteiro de ${url}: ${e.message}`, 'warn', 8000); }
  }
  st.textContent = `${ok} de ${links.length} roteiros prontos.`;
  if (ok) $('#linksLote').value = '';
  btn.classList.remove('busy');
});

/* ---------------- 2. roteiro ---------------- */
function nomesJogos() { return $('#jogos').value.split('\n').map((s) => s.trim()).filter(Boolean); }
// "https://…" ou "Nome do jogo: https://…" -> [{ url, jogo }]
function linksVideo() {
  return $('#linksVideo').value.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const i = l.search(/https?:\/\//);
    if (i < 0) return null;
    const jogo = l.slice(0, i).replace(/[:\-–|]\s*$/, '').trim();
    return { url: l.slice(i).split(/\s+/)[0], jogo: jogo || null };
  }).filter(Boolean);
}
function estimativa() {
  const n = contarPalavras(state.roteiro.cenas);
  const seg = Math.round(n / ritmo());
  const el = $('#estimativa');
  el.textContent = seg >= 60 ? `${fmtT(seg)} min` : `${seg} s`;
  const curto = seg < 64;
  el.classList.toggle('curto', curto);
  const falta = Math.max(0, Math.ceil((64 - seg) * ritmo()));
  $('#estimativaNota').textContent = curto ? `abaixo de 1 min · faltam ~${falta} palavras` : `${n} palavras · passa de 1 min ✓`;
}
function renderRoteiro() {
  const r = state.roteiro;
  $('#titulo').value = r.titulo;
  $('#tituloTela').value = r.titulo_tela || '';
  $('#jogos').value = r.jogos.join('\n');
  $('#linksVideo').value = (r.links || []).map((x) => (x.jogo ? `${x.jogo}: ${x.url}` : x.url)).join('\n');
  const box = $('#cenas');
  box.replaceChildren();
  r.cenas.forEach((c, i) => {
    const sel = h('select', { 'aria-label': 'Jogo na tela' },
      h('option', { value: '' }, 'Jogo principal (geral)'),
      ...nomesJogos().map((n) => h('option', { value: n, selected: n === c.assunto }, n)));
    if (c.assunto && !nomesJogos().includes(c.assunto)) sel.append(h('option', { value: c.assunto, selected: true }, c.assunto));
    sel.addEventListener('change', () => { c.assunto = sel.value || null; salvarRascunho(); });
    const txt = h('textarea', { rows: 2, value: c.texto, placeholder: 'O que será falado neste tópico…' });
    txt.addEventListener('input', () => { c.texto = txt.value; estimativa(); salvarRascunho(); });
    const dest = h('input', { type: 'text', value: (c.destaques || []).join(', '), placeholder: 'palavras em destaque, separadas por vírgula' });
    dest.addEventListener('input', () => { c.destaques = dest.value.split(',').map((s) => s.trim()).filter(Boolean); salvarRascunho(); });
    const emo = h('input', { type: 'text', value: c.emoji || '', placeholder: '—', maxlength: 4, class: 'emoji-in', title: 'Emoji que aparece sobre a legenda deste tópico (vazio = automático)' });
    emo.addEventListener('input', () => { c.emoji = emo.value.trim() || null; salvarRascunho(); });
    const mover = (d) => { const j = i + d; if (j < 0 || j >= r.cenas.length) return; [r.cenas[i], r.cenas[j]] = [r.cenas[j], r.cenas[i]]; renderRoteiro(); };
    box.append(h('div', { class: 'cena' },
      h('div', { class: 'campos' }, txt,
        h('div', { class: 'linha2' },
          h('label', { class: 'field' }, h('span', {}, 'Destaques na legenda'), dest),
          h('label', { class: 'field' }, h('span', {}, 'Jogo na tela'), sel),
          h('label', { class: 'field curto' }, h('span', {}, 'Emoji'), emo),
          h('div', { class: 'acoes' },
            h('button', { class: 'btn small icon', title: 'Subir', onclick: () => mover(-1) }, '↑'),
            h('button', { class: 'btn small icon', title: 'Descer', onclick: () => mover(1) }, '↓'),
            h('button', { class: 'btn small icon danger', title: 'Remover', onclick: () => { r.cenas.splice(i, 1); renderRoteiro(); } }, '✕'))))));
  });
  estimativa();
  salvarRascunho();
}
$('#titulo').addEventListener('input', (e) => { state.roteiro.titulo = e.target.value; salvarRascunho(); });
$('#tituloTela').addEventListener('input', (e) => { state.roteiro.titulo_tela = e.target.value; salvarRascunho(); });
$('#jogos').addEventListener('change', () => { state.roteiro.jogos = nomesJogos(); renderRoteiro(); });
$('#linksVideo').addEventListener('change', () => { state.roteiro.links = linksVideo(); salvarRascunho(); });
$('#btnAddCena').addEventListener('click', () => { state.roteiro.cenas.push({ texto: '', destaques: [], assunto: null }); renderRoteiro(); });
$('#btnCopiar').addEventListener('click', async () => {
  const t = state.roteiro.cenas.map((c) => c.texto).join('\n\n');
  try { await navigator.clipboard.writeText(t); toast('Roteiro copiado.'); } catch { toast('Não consegui copiar; selecione o texto manualmente.', 'warn'); }
});
$('#btnIrVoz').addEventListener('click', () => {
  state.roteiro.jogos = nomesJogos();
  state.roteiro.links = linksVideo();
  if (!state.roteiro.cenas.some((c) => c.texto.trim())) { toast('Escreva pelo menos um tópico.', 'warn'); return; }
  if (!state.roteiro.jogos.length) { toast('Informe pelo menos um jogo (é de lá que vêm as imagens).', 'warn'); $('#jogos').focus(); return; }
  irPara('voz');
});

/* ---------------- 3. voz ---------------- */
// Teleprompter: o marcador avança no ritmo da fala, só enquanto o microfone capta voz; a frase atual fica em destaque.
const tp = { palavras: [], frases: [], pos: 0, falando: 0, ultimo: 0 };
function renderTeleprompter() {
  tp.palavras = []; tp.frases = []; tp.pos = 0;
  const paragrafos = state.roteiro.cenas.filter((c) => c.texto.trim()).map((c) => {
    const p = h('p');
    for (const frase of c.texto.trim().split(/(?<=[.!?…])\s+/)) {
      const fi = tp.frases.length;
      const el = h('span', { class: 'tp-frase' });
      tp.frases.push({ el, ini: tp.palavras.length });
      for (const pal of frase.split(/\s+/).filter(Boolean)) {
        const k = tp.palavras.length;
        const w = h('span', { class: 'tp-w', onclick: () => { tp.pos = k; atualizarTp(true); } }, pal);
        tp.palavras.push({ el: w, frase: fi });
        el.append(w, ' ');
      }
      p.append(el, ' ');
    }
    return p;
  });
  $('#teleprompter').replaceChildren(...paragrafos);
  atualizarTp(true);
}
function atualizarTp(rolar) {
  const k = Math.min(Math.floor(tp.pos), tp.palavras.length - 1);
  if (k < 0) return;
  const fAtual = tp.palavras[k].frase;
  tp.palavras.forEach((w, i) => { w.el.classList.toggle('lida', i < k); w.el.classList.toggle('agora', i === k); });
  tp.frases.forEach((f, i) => f.el.classList.toggle('atual', i === fAtual));
  if (rolar) {
    const box = $('#teleprompter'), alvo = tp.frases[fAtual].el;
    const y = alvo.offsetTop - box.clientHeight * 0.35; // .teleprompter é position: relative
    if (Math.abs(box.scrollTop - y) > 8) box.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
  }
}
// chamado pelo medidor do microfone a cada quadro durante a gravação
function avancarTp(pico) {
  const agora = performance.now();
  const dt = tp.ultimo ? Math.min((agora - tp.ultimo) / 1000, 0.1) : 0;
  tp.ultimo = agora;
  if (!$('#tpAuto').checked || !tp.palavras.length) return;
  if (pico > 0.045) tp.falando = agora;
  if (agora - tp.falando < 350) { // voz agora (ou há menos de 0,35 s): avança no ritmo escolhido
    const antes = Math.floor(tp.pos);
    tp.pos = Math.min(tp.pos + dt * Number($('#tpVel').value), tp.palavras.length - 1);
    if (Math.floor(tp.pos) !== antes) atualizarTp(true);
  }
}
function setVozModo(m) {
  state.voz.modo = m;
  $$('[data-voz]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.voz === m)));
  $$('[data-painel]').forEach((p) => { p.hidden = p.dataset.painel !== m; });
}
$$('[data-voz]').forEach((b) => b.addEventListener('click', () => setVozModo(b.dataset.voz)));

const grav = { rec: null, stream: null, chunks: [], t0: 0, timer: 0, ctx: null, raf: 0 };
async function iniciarGravacao() {
  try {
    grav.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
  } catch {
    toast('Não consegui acessar o microfone. Libere o acesso no navegador.', 'warn', 6000);
    return;
  }
  const tipo = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  grav.rec = new MediaRecorder(grav.stream, tipo ? { mimeType: tipo, audioBitsPerSecond: 96000 } : {});
  grav.chunks = [];
  grav.rec.ondataavailable = (e) => e.data.size && grav.chunks.push(e.data);
  grav.rec.onstop = () => {
    const mime = grav.rec.mimeType || 'audio/webm';
    const ext = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
    state.voz.blob = new Blob(grav.chunks, { type: mime });
    state.voz.nome = `voz.${ext}`;
    const a = $('#ouvirGrav');
    a.src = URL.createObjectURL(state.voz.blob); a.hidden = false;
    $('#statusGrav').textContent = 'Gravação pronta. Ouça antes de gerar, ou grave de novo.';
  };
  grav.rec.start(1000);
  grav.t0 = Date.now();
  tp.pos = 0; tp.ultimo = 0; atualizarTp(true);
  $('#btnGravar').classList.add('on');
  $('#statusGrav').textContent = 'Gravando… clique de novo para parar.';
  grav.timer = setInterval(() => { $('#tempoGrav').textContent = fmtT((Date.now() - grav.t0) / 1000); }, 250);
  grav.ctx = new AudioContext();
  const an = grav.ctx.createAnalyser();
  an.fftSize = 1024;
  grav.ctx.createMediaStreamSource(grav.stream).connect(an);
  const buf = new Float32Array(an.fftSize);
  const loop = () => {
    an.getFloatTimeDomainData(buf);
    let pico = 0;
    for (const v of buf) pico = Math.max(pico, Math.abs(v));
    $('#nivel').style.width = `${Math.min(100, pico * 140)}%`;
    avancarTp(pico);
    $('#nivel').style.background = pico > 0.95 ? 'var(--warn)' : 'var(--in)';
    grav.raf = requestAnimationFrame(loop);
  };
  loop();
}
function pararGravacao() {
  grav.rec?.state === 'recording' && grav.rec.stop();
  grav.stream?.getTracks().forEach((t) => t.stop());
  clearInterval(grav.timer); cancelAnimationFrame(grav.raf);
  grav.ctx?.close();
  $('#nivel').style.width = '0';
  $('#btnGravar').classList.remove('on');
}
$('#btnGravar').addEventListener('click', () => (grav.rec?.state === 'recording' ? pararGravacao() : iniciarGravacao()));

function usarArquivo(f) {
  if (!f) return;
  if (f.size > 150e6) { toast('Arquivo grande demais (máx. 150 MB).', 'warn'); return; }
  state.voz.blob = f;
  const ext = (f.name.split('.').pop() || 'm4a').toLowerCase().replace(/[^a-z0-9]/g, '');
  state.voz.nome = `voz.${ext || 'm4a'}`;
  const a = $('#ouvirArq');
  a.src = URL.createObjectURL(f); a.hidden = false;
  $('#soltarAudio span').textContent = `${f.name} · ${(f.size / 1e6).toFixed(1)} MB`;
}
$('#arqAudio').addEventListener('change', (e) => usarArquivo(e.target.files[0]));
const zona = $('#soltarAudio');
zona.addEventListener('dragover', (e) => { e.preventDefault(); zona.classList.add('sobre'); });
zona.addEventListener('dragleave', () => zona.classList.remove('sobre'));
zona.addEventListener('drop', (e) => { e.preventDefault(); zona.classList.remove('sobre'); usarArquivo(e.dataTransfer.files[0]); });

function paraBase64(blob) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(',')[1]);
    r.onerror = rej;
    r.readAsDataURL(blob);
  });
}

function montarJob() {
  const modo = state.voz.modo === 'ia' ? 'ia' : 'gravada';
  const r = state.roteiro;
  return {
    ...estiloSalvo(),
    titulo: r.titulo, titulo_tela: r.titulo_tela || '', fonte: state.materia?.url || '',
    jogos: r.jogos,
    cenas: r.cenas.filter((c) => c.texto.trim()),
    post: r.post || null,
    imagens_materia: state.materia?.imagens || [],
    links_video: r.links || [],
    voz: modo === 'ia' ? { modo, voz_ia: $('#vozIa').value } : { modo, arquivo: state.voz.nome },
  };
}
function podeGerar() {
  if (state.voz.modo !== 'ia' && !state.voz.blob) { toast('Grave ou envie o áudio primeiro.', 'warn'); return false; }
  if (grav.rec?.state === 'recording') { toast('Pare a gravação antes de gerar.', 'warn'); return false; }
  return true;
}

/* ---------------- fila: vários vídeos sendo gerados ao mesmo tempo ----------------
   Cada vídeo é enviado e montado no servidor por conta própria; a tela fica livre para gravar o próximo. */
const fila = (() => { try { return JSON.parse(ls.get(K.fila) || '[]'); } catch { return []; } })();
function salvarFila() {
  // guarda só o que dá para retomar depois de recarregar (os que já estão no servidor)
  ls.set(K.fila, JSON.stringify(fila.filter((x) => x.tag && x.estado !== 'enviando').slice(0, 12)));
}
function renderFila() {
  const box = $('#fila');
  const itens = fila.filter((x) => !x.dispensado);
  box.hidden = !itens.length;
  if (!itens.length) return;
  const ativos = itens.filter((x) => x.estado === 'enviando' || x.estado === 'processando').length;
  box.replaceChildren(
    h('div', { class: 'fila-topo' },
      h('button', { class: 'fila-abre', title: 'Mostrar/esconder', onclick: () => box.classList.toggle('aberta') },
        ativos ? `Gerando ${ativos} vídeo${ativos > 1 ? 's' : ''}` : 'Vídeos prontos', h('span', { class: 'muted small' }, ` · ${itens.length}`)),
      h('button', { class: 'link small', onclick: () => { fila.forEach((x) => { if (x.estado === 'pronto' || x.estado === 'erro') x.dispensado = true; }); salvarFila(); renderFila(); } }, 'limpar prontos')),
    h('ul', {}, ...itens.map((x) => h('li', { class: `fila-${x.estado}` },
      h('button', {
        class: 'fila-item', disabled: !x.tag, title: x.tag ? 'Abrir este vídeo' : 'Ainda enviando',
        onclick: () => { if (x.tag) abrirVideo(x.tag); },
      },
      h('span', { class: 'fila-tit' }, x.titulo || 'Sem título'),
      h('span', { class: 'fila-etapa muted small' },
        x.estado === 'pronto' ? '✅ Pronto — abrir' : x.estado === 'erro' ? `⚠️ ${x.etapa || 'Erro'}` : (x.etapa || 'Na fila…')),
      h('i', { class: 'fila-barra' }, h('i', { style: { width: `${x.estado === 'pronto' ? 100 : Math.max(3, x.progresso || 0)}%` } })))))));
}
let vigiandoFila = false;
async function vigiarFila() {
  if (vigiandoFila) return;
  vigiandoFila = true;
  try {
    while (fila.some((x) => x.estado === 'processando' || x.estado === 'enviando')) {
      for (const x of fila.filter((i) => i.estado === 'processando' && i.tag)) {
        try {
          const s = await api(`/api/status?tag=${encodeURIComponent(x.tag)}`);
          const st = s.status || {};
          if (st.estado === 'pronto' && s.video) {
            x.estado = 'pronto';
            notificar('Vídeo pronto', x.titulo || 'Modo Narrado', true, x.tag);
          } else if (st.estado === 'erro') {
            x.estado = 'erro'; x.etapa = st.erro || 'falha no render';
            notificar('Um vídeo deu erro', x.titulo || '', false, x.tag);
          } else {
            x.etapa = st.etapa || 'Processando…'; x.progresso = st.progresso || x.progresso || 0;
          }
        } catch { /* tenta de novo na próxima volta */ }
      }
      salvarFila(); renderFila();
      await sleep(8000);
    }
  } finally { vigiandoFila = false; }
}
async function enviarParaFila(job, voz) {
  const item = { id: Math.random().toString(36).slice(2, 9), titulo: job.titulo, estado: 'enviando', etapa: 'Criando…', progresso: 1 };
  fila.unshift(item);
  renderFila();
  pedirPermissaoAviso();
  try {
    const { tag } = await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'criar', job }) });
    item.tag = tag;
    if (job.post) ls.set(K.post + tag, JSON.stringify(job.post));
    if (voz?.blob) {
      const PARTE = 3 * 1024 * 1024; // limite de upload das funções do Vercel
      const total = Math.ceil(voz.blob.size / PARTE);
      for (let n = 0; n < total; n++) {
        item.etapa = `Enviando o áudio (${n + 1}/${total})…`; renderFila();
        const base64 = await paraBase64(voz.blob.slice(n * PARTE, (n + 1) * PARTE));
        await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'parte', tag, n, base64 }) });
      }
    }
    item.etapa = 'Mandando para o servidor de render…'; renderFila();
    await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'iniciar', tag, fase: 'preparar' }) });
    item.estado = 'processando'; item.etapa = 'Na fila do servidor…'; item.progresso = 2;
  } catch (e) {
    item.estado = 'erro'; item.etapa = `Não enviou: ${e.message}`;
    toast(`O envio de "${job.titulo}" falhou: ${e.message}`, 'warn', 8000);
  }
  salvarFila(); renderFila();
  vigiarFila();
  return item;
}
function tirarDoLote() {
  if (state.loteId) { salvarLote(lote().filter((x) => x.id !== state.loteId)); state.loteId = null; renderLote(); }
}

$('#btnGerar').addEventListener('click', async () => {
  if (!podeGerar()) return;
  const st = $('#statusEnvio'), btn = $('#btnGerar');
  btn.classList.add('busy');
  st.textContent = 'Enviando…';
  const job = montarJob();
  tirarDoLote();
  const item = await enviarParaFila(job, state.voz.modo === 'ia' ? null : { blob: state.voz.blob });
  btn.classList.remove('busy');
  st.textContent = '';
  if (item.tag && item.estado !== 'erro') abrirVideo(item.tag);
  else st.textContent = `Erro: ${item.etapa}`;
});

// gravar em sequência: manda este para a fila (o envio continua sozinho) e já abre o próximo roteiro na voz
$('#btnGerarProximo').addEventListener('click', () => {
  if (!podeGerar()) return;
  const prox = proximoDoLote();
  if (!prox) return;
  const job = montarJob();
  const voz = state.voz.modo === 'ia' ? null : { blob: state.voz.blob };
  tirarDoLote();
  enviarParaFila(job, voz);
  toast(`"${job.titulo}" foi para a fila. Pode gravar o próximo.`);
  state.voz.blob = null; state.voz.nome = '';
  $('#ouvirGrav').hidden = true; $('#ouvirGrav').removeAttribute('src');
  $('#ouvirArq').hidden = true; $('#ouvirArq').removeAttribute('src');
  $('#soltarAudio span').textContent = 'Clique ou arraste o áudio (m4a, mp3, wav…)';
  $('#arqAudio').value = '';
  $('#tempoGrav').textContent = '0:00';
  $('#statusGrav').textContent = 'Pronto para gravar';
  abrirDoLote(prox.id, 'voz');
});
function atualizarBotaoProximo() {
  const prox = state.passo === 'voz' ? proximoDoLote() : null;
  const b = $('#btnGerarProximo');
  b.hidden = !prox;
  if (prox) {
    const resto = lote().filter((x) => x.id !== state.loteId).length;
    b.textContent = `Gerar e gravar o próximo (${resto} na lista)`;
    b.title = `Próximo: ${prox.roteiro.titulo || 'sem título'}`;
  }
}

/* ---------------- aviso quando o vídeo fica pronto ---------------- */
function pedirPermissaoAviso() {
  try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {}); } catch { /* sem suporte */ }
}
function pedirAviso() {
  state.esperando = true;
  pedirPermissaoAviso();
}
const tituloPagina = document.title;
function avisar(titulo, corpo, ok = true) {
  if (!state.esperando) return;
  state.esperando = false;
  notificar(titulo, corpo, ok);
}
function notificar(titulo, corpo, ok = true, tag = '') {
  try {
    if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
      const n = new Notification(titulo, { body: corpo, icon: "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎙️</text></svg>" });
      n.onclick = () => { window.focus(); n.close(); if (tag) abrirVideo(tag); };
    }
  } catch { /* sem suporte */ }
  if (document.hidden) {
    document.title = `${ok ? '✅' : '⚠️'} ${titulo}`;
    const volta = () => { document.title = tituloPagina; document.removeEventListener('visibilitychange', volta); };
    document.addEventListener('visibilitychange', volta);
  }
  try { // dois bipes curtos
    const c = new AudioContext();
    [0, 0.16].forEach((dt, i) => {
      const o = c.createOscillator(), g = c.createGain();
      o.frequency.value = ok ? (i ? 1046 : 784) : 330;
      g.gain.setValueAtTime(0.0001, c.currentTime + dt);
      g.gain.exponentialRampToValueAtTime(0.18, c.currentTime + dt + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + dt + 0.14);
      o.connect(g).connect(c.destination);
      o.start(c.currentTime + dt); o.stop(c.currentTime + dt + 0.15);
    });
    setTimeout(() => c.close(), 600);
  } catch { /* sem áudio */ }
}

/* ---------------- 4. vídeo: acompanhar ---------------- */
function abrirVideo(tag) {
  pararPrevia();
  state.tag = tag;
  state.projeto = null;
  state.sujo = false;
  $('#ajustes').hidden = true;
  $('#player').hidden = true;
  $('#player').removeAttribute('src');
  $('#btnBaixar').hidden = true;
  atualizarBotaoAplicar();
  $('#aguarde').hidden = false;
  $('#aguarde').classList.remove('erro');
  $('#etapa').textContent = 'Na fila…';
  $('#progresso').style.width = '1%';
  irPara('video');
  acompanhar();
}

async function acompanhar() {
  const meu = ++state.polling;
  let falhas = 0;
  while (meu === state.polling && state.tag) {
    try {
      const s = await api(`/api/status?tag=${encodeURIComponent(state.tag)}`);
      falhas = 0;
      $('#infoVideo').textContent = s.titulo;
      const st = s.status || {};
      if (st.estado === 'erro') {
        avisar('O vídeo deu erro', st.erro || 'Falha no render', false);
        $('#aguarde').classList.add('erro');
        $('#etapa').textContent = `Deu erro: ${st.erro || 'falha no render'}`;
        $('#aguarde').hidden = false;
        if (s.video) mostrarVideo(s); // ainda dá para ver a versão anterior
        return;
      }
      if (st.estado === 'pronto' && s.video) {
        avisar('Vídeo pronto', s.titulo || 'Modo Narrado');
        mostrarVideo(s);
        await carregarProjeto();
        return;
      }
      $('#etapa').textContent = st.etapa || 'Processando…';
      $('#progresso').style.width = `${Math.max(2, st.progresso || 0)}%`;
    } catch (e) {
      if (++falhas > 5) { $('#etapa').textContent = `Sem resposta do servidor: ${e.message}`; }
    }
    await sleep(5000);
  }
}

function mostrarVideo(s) {
  const v = $('#player');
  v.src = s.video;
  v.hidden = false;
  $('#aguarde').hidden = true;
  const a = $('#btnBaixar');
  a.href = s.video;
  a.setAttribute('download', `${(s.titulo || 'video').replace(/[^\w\- ]+/g, '').trim() || 'video'}.mp4`);
  a.hidden = false;
  state.cenasImg = s.cenas;
  state.temPrevia = !!s.temPrevia;
  state.temLimpo = !!s.temLimpo;
}

async function carregarProjeto() {
  try {
    state.projeto = await api(`/api/status?tag=${encodeURIComponent(state.tag)}&projeto=1`);
  } catch (e) { toast(`Não consegui abrir o projeto para ajustes: ${e.message}`, 'warn', 7000); return; }
  const p = state.projeto;
  registrarRitmo(p);
  const info = [`${fmtT(p.duracao || 0)} min${(p.duracao || 0) < MONETIZA ? ' ⚠️ abaixo de 1 min (não monetiza no TikTok)' : ''}`, `versão ${p.versao}`];
  if (p.voz?.modo === 'gravada') info.push(p.voz.reducao_aplicada ? `redução de ruído ligada (fundo ${p.voz.ruido_db} dB)` : 'gravação limpa, sem redução de ruído');
  $('#infoVideo').textContent = `${p.titulo} · ${info.join(' · ')}`;
  $('#ruido').value = p.voz?.reducao_ruido || 'auto';
  $('#ajustes').hidden = false;
  $('#btnPrevia').hidden = !state.temPrevia;
  state.baseEstrutura = assinaturaEstrutural(p);
  state.sujo = false;
  atualizarBotaoAplicar();
  renderTomadas();
  renderBlocosLegenda();
  renderCenas();
  renderPost();
  renderLinhaTempo();
}

/* legenda do post (vem do roteiro; dá para editar, copiar ou pedir outra à IA) */
function textoDoPost(post) {
  if (!post) return '';
  return [post.legenda || '', (post.hashtags || []).map((x) => (x.startsWith('#') ? x : `#${x}`)).join(' ')].filter(Boolean).join('\n\n');
}
function renderPost() {
  const p = state.projeto;
  let post = p?.post && (p.post.legenda || p.post.hashtags?.length) ? p.post : null;
  if (!post) { try { post = JSON.parse(ls.get(K.post + state.tag) || 'null'); } catch { post = null; } }
  $('#textoPost').value = textoDoPost(post);
  $('#boxPost').hidden = false;
}
$('#btnCopiarPost').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#textoPost').value); toast('Legenda copiada.'); } catch { toast('Não consegui copiar; selecione o texto.', 'warn'); }
});
$('#btnGerarPost').addEventListener('click', async () => {
  const p = state.projeto;
  if (!p) return;
  const btn = $('#btnGerarPost');
  btn.classList.add('busy');
  try {
    const texto = p.cenas.map((c) => c.texto).join(' ');
    const r = await gemini(`Escreva a legenda para publicar este vídeo curto de games no TikTok/Reels, em português do Brasil.
1 a 2 frases que instigam + uma pergunta para comentários. Depois 5 a 8 hashtags relevantes.
Responda SOMENTE com JSON: {"legenda": "...", "hashtags": ["#..."]}

ROTEIRO DO VÍDEO: ${texto}`);
    const post = { legenda: r.legenda || '', hashtags: r.hashtags || [] };
    p.post = post;
    ls.set(K.post + state.tag, JSON.stringify(post));
    $('#textoPost').value = textoDoPost(post);
  } catch (e) { toast(e.message, 'warn', 7000); } finally { btn.classList.remove('busy'); }
});

$$('[data-vista]').forEach((b) => b.addEventListener('click', () => {
  $$('[data-vista]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  $$('[data-vista-painel]').forEach((p) => { p.hidden = p.dataset.vistaPainel !== b.dataset.vista; });
  if (b.dataset.vista === 'legenda') renderBlocosLegenda();
}));

// O que muda o vídeo "por baixo" (cenas, cortes de fala, efeitos). O resto (texto e destaques da legenda,
// estilo/cor/altura, texto da chamada, emojis) só pede para queimar a legenda de novo: ajuste rápido.
function assinaturaEstrutural(p) {
  return JSON.stringify({
    palavras: p.palavras.map((w) => [w.manter ? 1 : 0, w.cena]),
    cenas: p.cenas.map((c) => [c.midias, c.entrada || 'auto', c.transicao || 'auto', c.jogo]),
    voz: p.voz?.reducao_ruido,
    outros: [p.jl_quadros, p.transicao_gancho, p.sfx, p.zoom_enfase, p.titulo_estilo, !!(p.titulo_gancho !== false && p.titulo_tela)],
    proprias: (p.proprias || []).map((x) => [x.id, x.cena]),
  });
}
function soLegenda() {
  return !!(state.projeto && state.temLimpo && state.baseEstrutura === assinaturaEstrutural(state.projeto));
}
function atualizarBotaoAplicar() {
  const b = $('#btnAplicar');
  b.disabled = !state.sujo;
  b.textContent = !state.sujo ? 'Aplicar ajustes' : soLegenda() ? 'Aplicar ajustes · rápido (só legenda)' : 'Aplicar ajustes · refaz o vídeo';
}

function marcarSujo() { state.sujo = true; atualizarBotaoAplicar(); }
$('#ruido').addEventListener('change', (e) => { if (state.projeto) { state.projeto.voz.reducao_ruido = e.target.value; marcarSujo(); } });

/* ---------------- 4b. ajustes: fala e legenda ---------------- */
$$('[data-aba]').forEach((b) => b.addEventListener('click', () => {
  $$('[data-aba]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
  $$('[data-aba-painel]').forEach((p) => { p.hidden = p.dataset.abaPainel !== b.dataset.aba; });
}));

function renderTomadas() {
  const p = state.projeto;
  const box = $('#tomadas');
  box.replaceChildren();
  const falta = (p.faltando || []).filter((w) => w.replace(/\W/g, '').length > 2);
  $('#avisoFalta').hidden = !falta.length;
  $('#avisoFalta').textContent = falta.length ? `Trechos do roteiro que não encontrei na gravação: “${falta.slice(0, 12).join(' ')}${falta.length > 12 ? '…' : ''}”. Se faltou falar algo, grave de novo; se você só disse com outras palavras, está tudo certo.` : '';
  // agrupa por tomada (trecho falado entre pausas)
  const grupos = [];
  for (const w of p.palavras) {
    const g = grupos[grupos.length - 1];
    if (g && g.c === w.c) g.ws.push(w); else grupos.push({ c: w.c, ws: [w] });
  }
  let cenaAtual = -1;
  for (const g of grupos) {
    const mantida = g.ws.some((w) => w.manter);
    const cena = g.ws.find((w) => w.manter)?.cena;
    if (mantida && cena != null && cena !== cenaAtual) {
      cenaAtual = cena;
      const c = p.cenas[cena];
      box.append(h('div', { class: 'cena-titulo' }, `Tópico ${cena + 1}${c?.jogo ? ' · ' + nomeJogo(c.jogo) : ''}`));
    }
    const linha = h('div', { class: `tomada${mantida ? '' : ' fora'}`, title: mantida ? '' : 'Tomada descartada — clique para usar esta' },
      h('span', { class: 't' }, fmtT(g.ws[0].s)));
    for (const w of g.ws) {
      const diferente = w.legenda && normal(w.legenda) !== normal(w.w);
      const span = h('span', { class: `w${w.manter ? '' : ' cortada'}${diferente ? ' editada' : ''}`, title: diferente ? `falado: ${w.w}` : '' },
        w.manter ? (w.legenda || '·') : w.w);
      span.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!mantida) { trazerTomada(g); return; }
        editarPalavra(w, span);
      });
      linha.append(span, ' ');
    }
    if (!mantida) linha.addEventListener('click', () => trazerTomada(g));
    box.append(linha);
  }
}

// Caixinha de edição de uma palavra: corrigir o texto da legenda ou cortar/restaurar a fala
function fecharPop() { $('#popPalavra')?.remove(); }
function editarPalavra(w, ancora) {
  fecharPop();
  const inp = h('input', { type: 'text', value: w.manter ? (w.legenda || w.w) : (w.legenda || w.w), 'aria-label': 'Texto da legenda' });
  const salvar = () => {
    const v = inp.value.trim();
    if (v !== (w.legenda || '')) { w.legenda = v; marcarSujo(); }
    fecharPop(); renderTomadas(); renderBlocosLegenda();
  };
  const pop = h('div', { class: 'pop-palavra', id: 'popPalavra', role: 'dialog' },
    h('div', { class: 'muted small' }, `Falado: “${w.w}”`),
    w.manter ? inp : null,
    h('div', { class: 'row' },
      w.manter ? h('button', { class: 'btn small primary', onclick: salvar }, 'Corrigir legenda') : null,
      h('button', { class: `btn small${w.manter ? ' danger' : ' primary'}`, onclick: () => { w.manter = !w.manter; marcarSujo(); fecharPop(); renderTomadas(); renderBlocosLegenda(); } },
        w.manter ? '✂ Cortar esta fala' : 'Restaurar fala'),
      h('button', { class: 'btn small', onclick: fecharPop }, 'Cancelar')),
    w.manter ? h('div', { class: 'muted small' }, 'Corrigir só muda o texto (render rápido). Cortar muda o áudio (refaz o vídeo).') : null);
  inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') salvar(); if (ev.key === 'Escape') fecharPop(); });
  document.body.append(pop);
  const r = ancora.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - pop.offsetWidth - 8, r.left + window.scrollX - 10))}px`;
  pop.style.top = `${r.bottom + window.scrollY + 6}px`;
  if (w.manter) { inp.focus(); inp.select(); }
}
document.addEventListener('mousedown', (e) => { const p = $('#popPalavra'); if (p && !p.contains(e.target) && !e.target.closest('.w')) fecharPop(); });

// Legenda como texto, bloco a bloco (como na timeline de um editor): edita a frase inteira
function blocosLegendaTela(p) {
  const mant = [...p.palavras].filter((w) => w.manter && w.cena != null).sort((a, b) => a.s - b.s);
  const blocos = []; let cur = [];
  const fecha = () => { if (cur.length) blocos.push(cur); cur = []; };
  for (const w of mant) {
    const ult = cur[cur.length - 1];
    if (ult && (w.cena !== ult.cena || w.s - ult.e > 0.9)) fecha();
    cur.push(w);
    const txt = cur.map((x) => x.legenda || x.w).join(' ');
    if (cur.length === 3 || /[.,:;?!…]$/.test(w.legenda || w.w) || (cur.length === 2 && txt.length > 11)) fecha();
  }
  fecha();
  return blocos;
}
function renderBlocosLegenda() {
  const box = $('#blocosLegenda');
  if (!box || !state.projeto) return;
  const blocos = blocosLegendaTela(state.projeto);
  box.replaceChildren(...blocos.map((b) => {
    const inp = h('input', { type: 'text', value: b.map((w) => w.legenda ?? w.w).filter(Boolean).join(' ') });
    inp.addEventListener('change', () => {
      // distribui o texto novo pelas palavras do bloco (o tempo de cada uma continua o da fala)
      const toks = inp.value.trim().split(/\s+/).filter(Boolean);
      b.forEach((w, i) => {
        if (i < b.length - 1) w.legenda = toks[i] || '';
        else w.legenda = toks.slice(i).join(' ');
      });
      marcarSujo(); renderTomadas();
    });
    return h('label', { class: 'bloco-leg' }, h('span', { class: 't' }, fmtT(b[0].s)), inp);
  }));
}

// Usar uma tomada descartada: ela entra no lugar das palavras do mesmo trecho do roteiro.
function trazerTomada(g) {
  const p = state.projeto;
  const idxs = new Set(g.ws.map((w) => w.idx).filter((x) => x != null));
  for (const w of p.palavras) if (w.manter && w.idx != null && idxs.has(w.idx)) w.manter = false;
  let cena = null;
  for (const w of g.ws) {
    w.manter = true;
    if (w.cena == null) w.cena = cena ?? p.palavras.find((o) => o.idx != null && o.idx === w.idx && o.cena != null)?.cena ?? 0;
    cena = w.cena;
  }
  g.ws.forEach((w, i) => { if (w.cena == null) w.cena = g.ws[i - 1]?.cena ?? 0; });
  marcarSujo(); renderTomadas();
  toast('Tomada trocada. Clique em “Aplicar ajustes” para gerar a nova versão.');
}

/* ---------------- 4c. ajustes: cenas ---------------- */
const nomeJogo = (chave) => ({ proprio: 'Suas mídias', materia: 'Imagens da matéria' }[chave] || state.projeto?.jogos?.find((j) => j.chave === chave)?.nome || chave);
function mini(mid, extra = {}) {
  const p = state.projeto, m = p.banco[mid], sp = p.sprite;
  const k = m?.sprite ?? 0;
  const col = k % sp.colunas, lin = Math.floor(k / sp.colunas);
  const linhas = Math.ceil(Object.keys(p.banco).length / sp.colunas);
  return h('button', {
    class: `mini ${extra.class || ''}`, title: `${nomeJogo(m?.jogo)} · ${m?.tipo === 'v' ? 'trecho de trailer' : 'imagem'}`,
    style: {
      backgroundImage: `url("${state.cenasImg}")`,
      backgroundSize: `${sp.colunas * 100}% ${linhas * 100}%`,
      backgroundPosition: `${sp.colunas > 1 ? (col / (sp.colunas - 1)) * 100 : 0}% ${linhas > 1 ? (lin / (linhas - 1)) * 100 : 0}%`,
    },
    ...extra.props,
  }, h('span', { class: 'tipo' }, m?.tipo === 'v' ? '▶' : '▣'));
}

let troca = null; // { cena, pos }
const NOMES_TRANS = {
  flash: 'Flash + desfoque', zoom: 'Zoom + woosh', glitch: 'Glitch', pan_dir: 'Pan para a direita',
  pan_esq: 'Pan para a esquerda', leak: 'Light leak', pan_leak: 'Pan + light leak', seco: 'Corte seco',
};

function campoSel(rotulo, valor, opcoes, aoMudar) {
  const sel = h('select', { onchange: (e) => aoMudar(e.target.value) },
    ...opcoes.map(([v, t]) => h('option', { value: String(v) }, t)));
  sel.value = String(valor);
  return h('label', { class: 'field curto' }, h('span', {}, rotulo), sel);
}
function campoChk(rotulo, valor, aoMudar) {
  const c = h('input', { type: 'checkbox', checked: !!valor, onchange: (e) => aoMudar(e.target.checked) });
  return h('label', { class: 'chk' }, c, h('span', {}, rotulo));
}

// Aparência do vídeo (vale para este vídeo e vira o padrão dos próximos)
function renderAparencia() {
  const p = state.projeto;
  const muda = (k, conv = (v) => v) => (v) => { p[k] = conv(v); salvarEstilo(p); marcarSujo(); };
  const altura = h('input', { type: 'range', min: 40, max: 80, step: 1, value: Math.round((p.legenda_altura ?? 0.615) * 100) });
  const alturaTxt = h('span', { class: 'muted small' }, `${altura.value}% da tela`);
  altura.addEventListener('input', () => { alturaTxt.textContent = `${altura.value}% da tela`; });
  altura.addEventListener('change', () => { p.legenda_altura = altura.value / 100; salvarEstilo(p); marcarSujo(); });
  const tit = h('input', { type: 'text', value: p.titulo_tela || '', placeholder: 'Texto grande nos 2 primeiros segundos' });
  tit.addEventListener('change', () => { p.titulo_tela = tit.value.trim(); if (p.titulo_tela) p.titulo_gancho = true; marcarSujo(); });
  $('#aparencia').replaceChildren(
    h('div', { class: 'grade-ap' },
      campoSel('Legenda', p.legenda_estilo || 'classico', [['classico', 'Clássica (Anton)'], ['caixa', 'Caixa no destaque'], ['moderna', 'Moderna (Montserrat)']], muda('legenda_estilo')),
      campoSel('Cor do destaque', p.legenda_cor || 'amarelo', [['amarelo', 'Amarelo'], ['verde', 'Verde'], ['vermelho', 'Vermelho'], ['ciano', 'Ciano'], ['rosa', 'Rosa']], muda('legenda_cor')),
      h('label', { class: 'field' }, h('span', {}, 'Altura da legenda'), altura, alturaTxt),
      campoSel('J-cut / L-cut', p.jl_quadros || 8, [4, 8, 12].map((q) => [q, `${q} quadros (~${(q / 30).toFixed(2).replace('.', ',')}s)`]), muda('jl_quadros', Number)),
      campoSel('Saída do gancho', p.transicao_gancho || 'pan', [['pan', 'Pan para a direita + woosh'], ['leak', 'Light leak'], ['pan_leak', 'Pan + woosh + light leak']], muda('transicao_gancho'))),
    h('div', { class: 'grade-ap' },
      h('label', { class: 'field', style: { gridColumn: 'span 2' } }, h('span', {}, 'Chamada na tela (gancho)'), tit),
      campoSel('Estilo da chamada', p.titulo_estilo || 'manchete', [['manchete', 'Manchete (linhas gigantes + adesivo)'], ['faixa', 'Faixa colorida'], ['simples', 'Simples (texto no topo)']], muda('titulo_estilo'))),
    h('div', { class: 'row wrap' },
      campoChk('Título no início', p.titulo_gancho !== false && !!p.titulo_tela, (v) => { p.titulo_gancho = v; marcarSujo(); }),
      campoChk('Efeitos sonoros (woosh, pop, impacto)', p.sfx !== false, muda('sfx')),
      campoChk('Zoom nas palavras destacadas', p.zoom_enfase !== false, muda('zoom_enfase')),
      campoChk('Emojis', p.emoji !== false, muda('emoji'))),
  );
}

function renderCenas() {
  const p = state.projeto;
  const box = $('#listaCenas');
  box.replaceChildren();
  const usados = new Map((p.timeline || []).map((t) => [t.cena, t.cortes.length]));
  renderAparencia();
  renderProprias();
  p.cenas.forEach((c, ci) => {
    if (!c.texto) return;
    const tira = h('div', { class: 'tiras' });
    (c.midias || []).forEach((mid, pos) => {
      const b = mini(mid, { props: { draggable: true, onclick: () => abrirTroca(ci, pos) } });
      b.addEventListener('dragstart', (e) => { e.dataTransfer.setData('text/plain', `${ci}:${pos}`); b.classList.add('arrastando'); });
      b.addEventListener('dragend', () => b.classList.remove('arrastando'));
      b.addEventListener('dragover', (e) => e.preventDefault());
      b.addEventListener('drop', (e) => {
        e.preventDefault();
        const [cOrig, pOrig] = e.dataTransfer.getData('text/plain').split(':').map(Number);
        if (cOrig !== ci) return;
        const [it] = c.midias.splice(pOrig, 1);
        c.midias.splice(pos, 0, it);
        marcarSujo(); renderCenas();
      });
      tira.append(b);
    });
    tira.append(h('button', { class: 'mini mais', title: 'Adicionar imagem', onclick: () => abrirTroca(ci, (c.midias || []).length) }, '+'));
    const n = usados.get(ci);
    // troca de tópico: J-cut (fala entra antes da imagem), L-cut (imagem entra antes da fala) ou corte seco
    let entrada = null;
    if (ci > (p.cenas.findIndex((x) => x.texto))) {
      const aplicado = (p.timeline || []).find((t) => t.cena === ci)?.entrada;
      const nomes = { J: 'J-cut', L: 'L-cut', seco: 'corte seco' };
      const sel = h('select', {
        class: 'entrada', title: 'Como a imagem entra na troca de tópico',
        onchange: (e) => { c.entrada = e.target.value; marcarSujo(); },
      },
      h('option', { value: 'auto' }, `Automático${aplicado && nomes[aplicado] ? ` (${nomes[aplicado]})` : ''}`),
      h('option', { value: 'J' }, 'J-cut · a fala entra antes da imagem'),
      h('option', { value: 'L' }, 'L-cut · a imagem entra antes da fala'),
      h('option', { value: 'seco' }, 'Corte seco'));
      sel.value = c.entrada || 'auto';
      const primeiro = p.cenas.findIndex((x) => x.texto);
      const segundo = p.cenas.findIndex((x, i) => i > primeiro && x.texto);
      let trans;
      if (ci === segundo) {
        trans = h('span', { class: 'muted small' }, 'Transição: a da “saída do gancho” (acima)');
      } else {
        const feita = (p.timeline || []).find((t) => t.cena === ci)?.transicao;
        const selT = h('select', { title: 'Efeito na troca para este tópico', onchange: (e) => { c.transicao = e.target.value; marcarSujo(); } },
          h('option', { value: 'auto' }, `Automática${feita && NOMES_TRANS[feita] ? ` (${NOMES_TRANS[feita]})` : ''}`),
          ...Object.entries(NOMES_TRANS).filter(([k]) => k !== 'pan_leak').map(([k, v]) => h('option', { value: k }, v)));
        selT.value = c.transicao || 'auto';
        trans = h('label', { class: 'entrada-lbl' }, 'Transição: ', selT);
      }
      entrada = h('div', { class: 'linha-trans' }, h('label', { class: 'entrada-lbl' }, 'Entrada: ', sel), trans);
    }
    box.append(h('div', { class: 'lc-cena' },
      h('b', {}, `Tópico ${ci + 1} · ${nomeJogo(c.jogo)}`),
      h('p', {}, `${n ? `${n} cortes · ` : ''}${c.texto}`),
      entrada, tira));
  });
}

/* mídias próprias (print, foto, clipe) */
function renderProprias() {
  const p = state.projeto;
  const sel = $('#propriaCena');
  sel.replaceChildren(...p.cenas.map((c, i) => (c.texto ? h('option', { value: String(i) }, `Tópico ${i + 1}`) : null)));
  const lista = (p.proprias || []).map((it) => h('li', {},
    `${it.tipo === 'v' ? '🎬' : '🖼️'} ${it.nome || it.arquivo} → Tópico ${(it.cena ?? 0) + 1} `,
    h('span', { class: 'muted small' }, it.aplicada ? '(no vídeo)' : '(entra no próximo “Aplicar ajustes”)')));
  $('#listaProprias').replaceChildren(...lista);
}
$('#arqPropria').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  e.target.value = '';
  if (!f || !state.projeto) return;
  const ext = (f.name.split('.').pop() || '').toLowerCase();
  const ok = { jpg: 'i', jpeg: 'i', png: 'i', webp: 'i', mp4: 'v', mov: 'v', webm: 'v', m4v: 'v' };
  if (!ok[ext]) { toast('Use JPG, PNG, WEBP, MP4, MOV ou WEBM. (Foto HEIC do iPhone: exporte como JPG.)', 'warn', 7000); return; }
  if (f.size > 200e6) { toast('Arquivo grande demais (máx. 200 MB).', 'warn'); return; }
  const id = Math.random().toString(36).slice(2, 10);
  const nomeArq = `pm-${id}.${ext}`;
  const st = $('#statusPropria');
  try {
    const PARTE = 3 * 1024 * 1024;
    const total = Math.ceil(f.size / PARTE);
    for (let n = 0; n < total; n++) {
      st.textContent = `Enviando ${f.name} (${n + 1}/${total})…`;
      const base64 = await paraBase64(f.slice(n * PARTE, (n + 1) * PARTE));
      await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'midia', tag: state.tag, nome: nomeArq, n, base64 }) });
    }
    const p = state.projeto;
    p.proprias = p.proprias || [];
    p.proprias.push({ id, arquivo: nomeArq, tipo: ok[ext], cena: Number($('#propriaCena').value), nome: f.name.slice(0, 60) });
    st.textContent = 'Enviado. Clique em “Aplicar ajustes” para gerar a versão com ele.';
    marcarSujo();
    renderProprias();
  } catch (err) { st.textContent = `Erro: ${err.message}`; }
});

let filtroJogo = null;
function abrirTroca(ci, pos) {
  troca = { cena: ci, pos };
  filtroJogo = state.projeto.cenas[ci].jogo;
  renderGrade();
  $('#btnRemoverMidia').hidden = pos >= (state.projeto.cenas[ci].midias || []).length;
  $('#dlgTroca').showModal();
}
function renderGrade() {
  const p = state.projeto;
  const grupos = [...p.jogos];
  if (Object.values(p.banco).some((m) => m.jogo === 'materia')) grupos.push({ chave: 'materia', nome: 'Imagens da matéria' });
  if (Object.values(p.banco).some((m) => m.jogo === 'proprio')) grupos.push({ chave: 'proprio', nome: 'Suas mídias' });
  $('#filtroJogos').replaceChildren(...grupos.map((j) => h('button', {
    class: `btn small${j.chave === filtroJogo ? ' accent' : ''}`, onclick: () => { filtroJogo = j.chave; renderGrade(); },
  }, j.nome)));
  const naCena = new Set(p.cenas[troca.cena].midias || []);
  const ids = Object.keys(p.banco).filter((id) => p.banco[id].jogo === filtroJogo);
  $('#gradeMidias').replaceChildren(...ids.map((id) => mini(id, {
    class: naCena.has(id) ? 'usada' : '',
    props: {
      onclick: () => {
        const c = p.cenas[troca.cena];
        c.midias = c.midias || [];
        if (troca.pos < c.midias.length) c.midias[troca.pos] = id; else c.midias.push(id);
        if (p.banco[id].jogo !== c.jogo && !['proprio', 'materia'].includes(p.banco[id].jogo)) c.jogo = p.banco[id].jogo;
        $('#dlgTroca').close(); marcarSujo(); renderCenas();
      },
    },
  })));
}
$('#btnRemoverMidia').addEventListener('click', () => {
  const c = state.projeto.cenas[troca.cena];
  if ((c.midias || []).length <= 1) { toast('Cada tópico precisa de pelo menos uma imagem.', 'warn'); return; }
  c.midias.splice(troca.pos, 1);
  $('#dlgTroca').close(); marcarSujo(); renderCenas();
});

$('#btnAplicar').addEventListener('click', async () => {
  if (!state.projeto) return;
  pararPrevia();
  const btn = $('#btnAplicar');
  btn.classList.add('busy');
  try {
    const p = { ...state.projeto };
    const rapido = soLegenda();
    const r = await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'projeto', tag: state.tag, projeto: p, fase: rapido ? 'legenda' : 'render' }) });
    if (r.fase === 'legenda') toast('Ajuste rápido: só a legenda vai ser refeita (cerca de 2 minutos).');
    state.sujo = false;
    btn.disabled = true;
    $('#aguarde').hidden = false;
    $('#aguarde').classList.remove('erro');
    $('#etapa').textContent = 'Na fila…';
    $('#progresso').style.width = '1%';
    $('#player').hidden = true;
    $('#ajustes').hidden = true;
    $('#linhaTempo').hidden = true;
    $('#btnPrevia').hidden = true;
    pedirAviso();
    acompanhar();
  } catch (e) {
    toast(e.message, 'warn', 7000);
  } finally { btn.classList.remove('busy'); }
});

/* ---------------- prévia rápida (no navegador, sem render) ---------------- */
const CORES_LEG = { amarelo: '#FFD400', verde: '#3AF05A', vermelho: '#FF3B3B', ciano: '#22E5FF', rosa: '#FF4FD8' };
const previa = { ctx: null, buf: null, tag: '', fontes: [], raf: 0, inicio: 0, desde: 0, plano: null, tocando: false };

// Mesmas regras do servidor (voz.py / render.py), de forma simplificada
function montarPlano(p) {
  const todas = [...p.palavras].sort((a, b) => a.s - b.s);
  const pos = new Map(todas.map((w, i) => [w, i]));
  const mant = todas.filter((w) => w.manter);
  const blocos = [];
  for (const w of mant) {
    const b = blocos[blocos.length - 1];
    const k = pos.get(w);
    if (b && b.ultimo === k - 1 && todas[k - 1].c === w.c && w.s - todas[k - 1].e < 0.30) {
      b.e = w.e; b.ultimo = k; b.pal.push(w);
    } else blocos.push({ s: w.s, e: w.e, primeiro: k, ultimo: k, pal: [w] });
  }
  let t = 0;
  const tempo = new Map();
  for (const b of blocos) {
    const ant = b.primeiro > 0 ? todas[b.primeiro - 1].e : 0;
    const prox = b.ultimo + 1 < todas.length ? todas[b.ultimo + 1].s : b.e + 1;
    b.cs = Math.max(ant + 0.02, b.s - 0.05, 0);
    b.ce = Math.min(prox - 0.02, b.e + 0.12);
    b.ns = t;
    for (const w of b.pal) tempo.set(w, { ns: t + w.s - b.cs, ne: t + w.e - b.cs });
    t += Math.max(b.ce - b.cs, 0.05) + 0.06;
  }
  const total = t + 0.4;
  // cenas com J/L
  const inicios = new Map();
  for (const w of mant) if (w.cena != null && !inicios.has(w.cena)) inicios.set(w.cena, tempo.get(w).ns);
  const cenas = [...inicios.keys()].sort((a, b) => a - b);
  const desloc = (p.jl_quadros || 8) / 30;
  const corte = new Map();
  cenas.forEach((c, k) => {
    if (!k) { corte.set(c, 0); return; }
    const ent = String(p.cenas[c]?.entrada || 'auto').toUpperCase();
    const tipo = ['J', 'L', 'SECO'].includes(ent) ? ent : (k % 2 ? 'J' : 'L');
    corte.set(c, inicios.get(c) + (tipo === 'J' ? desloc : tipo === 'L' ? -desloc : 0));
  });
  const cortes = [];
  cenas.forEach((c, k) => {
    const a = corte.get(c), b = k + 1 < cenas.length ? corte.get(cenas[k + 1]) : total;
    const midias = (p.cenas[c]?.midias || []).filter((m) => p.banco[m]);
    const n = Math.max(1, Math.round((b - a) / 1.7));
    for (let j = 0; j < n; j++) cortes.push({ cena: c, a: a + (b - a) * j / n, b: a + (b - a) * (j + 1) / n, mid: midias[j % Math.max(midias.length, 1)] });
  });
  // legenda em blocos + destaques (palavra ou expressão)
  const marcadas = new Set();
  const porCena = new Map();
  for (const w of mant) if (String(w.legenda || '').trim()) { if (!porCena.has(w.cena)) porCena.set(w.cena, []); porCena.get(w.cena).push(w); }
  for (const [c, seq] of porCena) {
    const toks = seq.map((w) => normal(w.legenda));
    for (const frase of p.cenas[c]?.destaques || []) {
      const alvo = String(frase).split(/\s+/).map(normal).filter(Boolean);
      for (let k = 0; alvo.length && k + alvo.length <= toks.length; k++) {
        if (alvo.every((x, i) => toks[k + i] === x)) for (let i = 0; i < alvo.length; i++) marcadas.add(seq[k + i]);
      }
    }
  }
  const legendas = [];
  let cur = [];
  const fecha = () => { if (cur.length) legendas.push(cur); cur = []; };
  for (const w of mant) {
    if (!String(w.legenda || '').trim()) continue;
    const tw = tempo.get(w);
    const ult = cur[cur.length - 1];
    if (ult && (w.cena !== ult.cena || tw.ns - tempo.get(ult).ne > 0.6)) fecha();
    cur.push(w);
    const txt = cur.map((x) => x.legenda).join(' ');
    if (cur.length === 3 || /[.,:;?!…]$/.test(w.legenda) || (cur.length === 2 && txt.length > 11)) fecha();
  }
  fecha();
  const legs = legendas.map((b, i) => {
    const ini = tempo.get(b[0]).ns;
    const prox = legendas[i + 1];
    const fimB = tempo.get(b[b.length - 1]).ne;
    const fim = prox && tempo.get(prox[0]).ns - fimB < 0.6 ? tempo.get(prox[0]).ns : fimB + 0.35;
    return { ini, fim, partes: b.map((w) => ({ t: w.legenda.replace(/[.,:;…]+$/, '').toUpperCase(), d: marcadas.has(w) })) };
  });
  return { blocos, cortes, legs, total, cenas: cenas.map((c) => ({ cena: c, inicio: corte.get(c) })) };
}

async function carregarAudioPrevia() {
  if (previa.buf && previa.tag === state.tag) return;
  const r = await fetch(`/api/status?tag=${encodeURIComponent(state.tag)}&previa=1`, { headers: { 'x-narrado-senha': ls.get(K.senha) } });
  if (!r.ok) { const b = await r.json().catch(() => ({})); throw new Error(b.erro || `Erro ${r.status}`); }
  previa.ctx = previa.ctx || new AudioContext();
  previa.buf = await previa.ctx.decodeAudioData(await r.arrayBuffer());
  previa.tag = state.tag;
}

let spriteImg = null;
function imagemSprite() {
  if (!spriteImg || spriteImg.dataset.src !== state.cenasImg) {
    spriteImg = new Image(); // sem crossOrigin: só desenhamos (não lemos pixels), então outra origem funciona
    spriteImg.dataset.src = state.cenasImg;
    spriteImg.src = state.cenasImg;
  }
  return spriteImg;
}

// chamada em até 3 linhas sem perder palavra (mesma regra do render.py)
const CURTAS_TIT = new Set(['A', 'O', 'E', 'DE', 'DA', 'DO', 'NA', 'NO', 'EM', 'UM', 'AS', 'OS', 'AO', 'À']);
function quebraTitulo(txt, max) {
  const pals = String(txt || '').toUpperCase().split(/\s+/).filter(Boolean);
  const quebra = (m) => { const out = []; let l = ''; for (const p of pals) { if (l && (l + ' ' + p).length > m) { out.push(l); l = p; } else l = (l + ' ' + p).trim(); } if (l) out.push(l); return out; };
  let m = max, ls = quebra(m);
  while (ls.length > 3) ls = quebra(++m);
  for (let i = 0; i < ls.length - 1; i++) {
    const ws = ls[i].split(' ');
    if (ws.length > 1 && CURTAS_TIT.has(ws[ws.length - 1])) { ls[i + 1] = ws.pop() + ' ' + ls[i + 1]; ls[i] = ws.join(' '); }
  }
  return ls;
}

function desenharPrevia(t) {
  const p = state.projeto, pl = previa.plano, cv = $('#telaPrevia'), g = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  g.fillStyle = '#0d1013'; g.fillRect(0, 0, W, H);
  const c = pl.cortes.find((x) => t >= x.a && t < x.b) || pl.cortes[pl.cortes.length - 1];
  const img = imagemSprite();
  if (c?.mid && img.complete && img.naturalWidth) {
    const sp = p.sprite, k = p.banco[c.mid]?.sprite ?? 0;
    const sx = (k % sp.colunas) * sp.largura, sy = Math.floor(k / sp.colunas) * sp.altura;
    const z = 1 + 0.06 * Math.min(1, (t - c.a) / Math.max(c.b - c.a, 0.1));
    const w = W * z, hh = H * z;
    g.drawImage(img, sx, sy, sp.largura, sp.altura, (W - w) / 2, (H - hh) / 2, w, hh);
  }
  // chamada do gancho (manchete/faixa: fundo escuro, linhas ocupando a largura, linha-chave em destaque)
  const fimTit = Math.min(2.4, Math.max((pl.cenas[1]?.inicio ?? 3) - 0.05, 1.3));
  if (p.titulo_gancho !== false && p.titulo_tela && t < fimTit) {
    const estilo = p.titulo_estilo || 'manchete';
    const cor = CORES_LEG[p.legenda_cor] || CORES_LEG.amarelo;
    const ls = quebraTitulo(p.titulo_tela, estilo === 'simples' ? 14 : 12);
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.lineJoin = 'round';
    if (estilo === 'simples') {
      g.font = '40px Anton'; g.lineWidth = 6; g.strokeStyle = '#000';
      ls.forEach((tx, i, arr) => {
        const y = H * 0.25 + (i - (arr.length - 1) / 2) * 46;
        g.fillStyle = i === arr.length - 1 && arr.length > 1 ? cor : '#fff';
        g.strokeText(tx, W / 2, y); g.fillText(tx, W / 2, y);
      });
    } else {
      g.fillStyle = 'rgba(0,0,0,.38)'; g.fillRect(0, 0, W, H);
      const tamanhos = ls.map((tx, i) => { g.font = '100px Anton'; const w = g.measureText(tx).width; return Math.min(Math.max(100 * (W * (i === ls.length - 1 ? 0.83 : 0.89)) / w, 40), 150); });
      const caps = tamanhos.map((s) => s * 0.72);
      let y = H * 0.42 - (caps.reduce((a, b) => a + b, 0) + 16 * (ls.length - 1) + 20) / 2;
      ls.forEach((tx, i) => {
        const chave = i === ls.length - 1;
        const entra = Math.min(1, Math.max(0, (t - 0.11 * i) / 0.15));
        if (entra <= 0) { y += caps[i] + 16; return; }
        const esc = 1 + 0.45 * (1 - entra);
        if (chave) y += 10;
        const yc = y + caps[i] / 2;
        g.save(); g.translate(W / 2, yc); g.scale(esc, esc); g.globalAlpha = entra;
        g.font = `${tamanhos[i]}px Anton`;
        const w = g.measureText(tx).width;
        if (chave && estilo === 'faixa') { g.fillStyle = cor; g.fillRect(-W, -caps[i] / 2 - 10, 2 * W, caps[i] + 20); g.fillStyle = '#000'; g.fillText(tx, 0, 2); }
        else if (chave) { g.rotate(-0.05); g.fillStyle = cor; g.fillRect(-w / 2 - 12, -caps[i] / 2 - 10, w + 24, caps[i] + 20); g.fillStyle = '#000'; g.fillText(tx, 0, 2); }
        else { g.lineWidth = 8; g.strokeStyle = '#000'; g.strokeText(tx, 0, 2); g.fillStyle = '#fff'; g.fillText(tx, 0, 2); }
        g.restore();
        y += caps[i] + 16;
      });
    }
  }
  const escondeLeg = p.titulo_gancho !== false && p.titulo_tela && (p.titulo_estilo || 'manchete') !== 'simples' && t < fimTit;
  // legenda
  const leg = escondeLeg ? null : pl.legs.find((x) => t >= x.ini && t < x.fim);
  if (leg) {
    const fonte = p.legenda_estilo === 'moderna' ? 'Montserrat' : 'Anton';
    const peso = p.legenda_estilo === 'moderna' ? '900 ' : '';
    let tam = p.legenda_estilo === 'moderna' ? 44 : 54;
    g.textAlign = 'left'; g.textBaseline = 'middle'; g.lineJoin = 'round';
    const medir = () => { g.font = `${peso}${tam}px ${fonte}`; return leg.partes.reduce((a, x) => a + g.measureText(x.t + ' ').width, 0); };
    let larg = medir();
    while (larg > W - 24 && tam > 20) { tam -= 2; larg = medir(); }
    const pop = Math.min(1, (t - leg.ini) / 0.09);
    const esc = 0.75 + 0.25 * pop;
    const y = H * (p.legenda_altura ?? 0.615);
    g.save(); g.translate(W / 2, y); g.scale(esc, esc);
    let x = -larg / 2;
    const cor = CORES_LEG[p.legenda_cor] || CORES_LEG.amarelo;
    for (const parte of leg.partes) {
      const w = g.measureText(parte.t).width;
      if (p.legenda_estilo === 'caixa' && parte.d) {
        g.fillStyle = cor; g.fillRect(x - 6, -tam * 0.62, w + 12, tam * 1.2);
        g.fillStyle = '#000'; g.fillText(parte.t, x, 0);
      } else {
        g.lineWidth = Math.max(4, tam * 0.13); g.strokeStyle = '#000'; g.strokeText(parte.t, x, 0);
        g.fillStyle = parte.d ? cor : '#fff'; g.fillText(parte.t, x, 0);
      }
      x += g.measureText(parte.t + ' ').width;
    }
    g.restore();
  }
  // barra de progresso
  g.fillStyle = 'rgba(255,255,255,.25)'; g.fillRect(0, H - 4, W, 4);
  g.fillStyle = '#fff'; g.fillRect(0, H - 4, W * Math.min(1, t / pl.total), 4);
}

function pararPrevia() {
  previa.tocando = false;
  cancelAnimationFrame(previa.raf);
  previa.fontes.forEach((s) => { try { s.stop(); } catch { /* já parou */ } });
  previa.fontes = [];
  $('#btnPrevia').textContent = '▶ Prévia rápida';
  $('#telaPrevia').hidden = true;
  $('#avisoPrevia').hidden = true;
  if ($('#player').getAttribute('src') && $('#aguarde').hidden) $('#player').hidden = false;
  renderLinhaTempo();
}

async function tocarPrevia(desde = 0) {
  const btn = $('#btnPrevia');
  btn.classList.add('busy');
  try {
    await carregarAudioPrevia();
    await Promise.all(['54px Anton', '900 44px Montserrat'].map((f) => document.fonts.load(f).catch(() => null)));
  } catch (e) { toast(`Prévia indisponível: ${e.message}`, 'warn', 7000); btn.classList.remove('busy'); return; }
  btn.classList.remove('busy');
  pararPrevia();
  $('#player').pause();
  previa.plano = montarPlano(state.projeto);
  const ctx = previa.ctx;
  if (ctx.state === 'suspended') await ctx.resume();
  const base = ctx.currentTime + 0.12;
  for (const b of previa.plano.blocos) {
    const dur = Math.max(b.ce - b.cs, 0.05);
    if (b.ns + dur <= desde) continue;
    const pulo = Math.max(0, desde - b.ns);
    const s = ctx.createBufferSource();
    s.buffer = previa.buf;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0, base + Math.max(b.ns - desde, 0));
    g.gain.linearRampToValueAtTime(1, base + Math.max(b.ns - desde, 0) + 0.012);
    s.connect(g).connect(ctx.destination);
    s.start(base + Math.max(b.ns - desde, 0), b.cs + pulo, dur - pulo);
    previa.fontes.push(s);
  }
  previa.inicio = base;
  previa.desde = desde;
  previa.tocando = true;
  $('#player').hidden = true;
  $('#telaPrevia').hidden = false;
  $('#avisoPrevia').hidden = false;
  btn.textContent = '■ Parar prévia';
  renderLinhaTempo();
  const loop = () => {
    if (!previa.tocando) return;
    const t = previa.desde + Math.max(0, ctx.currentTime - previa.inicio);
    desenharPrevia(t);
    moverCursor(t, previa.plano.total);
    if (t >= previa.plano.total) { pararPrevia(); return; }
    previa.raf = requestAnimationFrame(loop);
  };
  loop();
}
$('#btnPrevia').addEventListener('click', () => (previa.tocando ? pararPrevia() : tocarPrevia(0)));

/* ---------------- linha do tempo (vídeo final ou prévia) ---------------- */
function renderLinhaTempo() {
  const p = state.projeto, box = $('#linhaTempo');
  if (!p) { box.hidden = true; return; }
  const usarPrevia = previa.tocando && previa.plano;
  const total = usarPrevia ? previa.plano.total : (p.duracao || 0);
  const faixas = usarPrevia
    ? previa.plano.cenas.map((c, i, arr) => ({ cena: c.cena, inicio: c.inicio, fim: arr[i + 1]?.inicio ?? total, mid: p.cenas[c.cena]?.midias?.[0] }))
    : (p.timeline || []).map((t) => ({ cena: t.cena, inicio: t.inicio, fim: t.fim, mid: t.cortes?.[0] }));
  if (!total || !faixas.length) { box.hidden = true; return; }
  const sp = p.sprite;
  const linhas = Math.ceil(Object.keys(p.banco).length / sp.colunas);
  const blocos = faixas.map((f) => {
    const k = p.banco[f.mid]?.sprite ?? 0;
    const col = k % sp.colunas, lin = Math.floor(k / sp.colunas);
    return h('button', {
      class: 'lt-cena', title: `Tópico ${f.cena + 1} · ${fmtT(f.inicio)}`,
      style: {
        width: `${((f.fim - f.inicio) / total) * 100}%`,
        backgroundImage: state.cenasImg ? `url("${state.cenasImg}")` : 'none',
        backgroundSize: `${sp.colunas * 100}% ${linhas * 100}%`,
        backgroundPosition: `${sp.colunas > 1 ? (col / (sp.colunas - 1)) * 100 : 0}% ${linhas > 1 ? (lin / (linhas - 1)) * 100 : 0}%`,
      },
      onclick: (e) => {
        const r = box.getBoundingClientRect();
        pularPara(((e.clientX - r.left) / r.width) * total);
      },
    }, h('span', {}, `T${f.cena + 1}`));
  });
  box.replaceChildren(h('div', { class: 'lt-faixa' }, ...blocos), h('i', { class: 'lt-cursor', id: 'ltCursor' }));
  box.hidden = false;
}
function moverCursor(t, total) {
  const c = $('#ltCursor');
  if (c && total) c.style.left = `${Math.min(100, (t / total) * 100)}%`;
}
function pularPara(t) {
  if (previa.tocando) { tocarPrevia(Math.max(0, t)); return; }
  const v = $('#player');
  if (!v.getAttribute('src')) return;
  v.currentTime = Math.max(0, t);
  v.play().catch(() => {});
}
$('#player').addEventListener('timeupdate', () => { if (!previa.tocando && state.projeto) moverCursor($('#player').currentTime, state.projeto.duracao || 0); });

/* ---------------- meus vídeos / novo / configurações ---------------- */
$('#menuVideos').addEventListener('toggle', async (e) => {
  if (!e.target.open) return;
  const box = $('#listaVideos');
  box.replaceChildren(h('p', { class: 'muted small' }, 'Carregando…'));
  try {
    const { videos } = await api('/api/status?lista=1');
    box.replaceChildren(...(videos.length ? videos.map((v) => h('button', {
      onclick: () => { $('#menuVideos').open = false; abrirVideo(v.tag); },
    }, v.titulo, h('small', {}, `${new Date(v.criado).toLocaleString('pt-BR')} · ${v.pronto ? 'pronto' : 'em andamento'}`))) : [h('p', { class: 'muted small' }, 'Nenhum vídeo ainda.')]));
  } catch (err) { box.replaceChildren(h('p', { class: 'muted small' }, err.message)); }
});
$('#btnNovo').addEventListener('click', () => {
  if (state.sujo && !confirm('Há ajustes não aplicados neste vídeo. Começar outro mesmo assim?')) return;
  state.polling++;
  Object.assign(state, { materia: null, roteiro: { titulo: '', jogos: [], cenas: [] }, tag: '', projeto: null, sujo: false });
  state.voz = { modo: 'gravar', blob: null, nome: '' };
  $('#urlMateria').value = ''; $('#textoMateria').value = ''; $('#previaMateria').hidden = true;
  $('#ouvirGrav').hidden = true; $('#ouvirArq').hidden = true; $('#tempoGrav').textContent = '0:00';
  atualizaBotaoRoteiro();
  irPara('pauta');
});

function abrirConfig(msg) {
  $('#cfgGemini').value = ls.get(K.gemini);
  $('#cfgSenha').value = ls.get(K.senha);
  if (msg) toast(msg, 'warn', 6000);
  if (!$('#dlgConfig').open) $('#dlgConfig').showModal();
}
$('#btnConfig').addEventListener('click', () => abrirConfig());
$('#btnSalvarCfg').addEventListener('click', () => {
  ls.set(K.gemini, limpaToken($('#cfgGemini').value));
  ls.set(K.senha, $('#cfgSenha').value.trim());
  $('#dlgConfig').close();
  toast('Configurações salvas neste navegador.');
});
window.addEventListener('beforeunload', (e) => {
  if (state.sujo || grav.rec?.state === 'recording' || fila.some((x) => x.estado === 'enviando')) { e.preventDefault(); e.returnValue = ''; }
});

/* ---------------- início ---------------- */
(function iniciar() {
  let r = null;
  try { r = JSON.parse(ls.get(K.rasc) || 'null'); } catch { r = null; }
  const tagUrl = new URLSearchParams(location.search).get('video');
  if (r) {
    state.materia = r.materia;
    state.roteiro = r.roteiro || state.roteiro;
    if (r.materia) { $('#urlMateria').value = r.materia.url || ''; $('#textoMateria').value = r.materia.texto || ''; }
  }
  if (ls.get(K.duracao)) $('#duracao').value = ls.get(K.duracao);
  $('#duracao').addEventListener('change', () => ls.set(K.duracao, $('#duracao').value));
  atualizaBotaoRoteiro();
  renderLote();
  renderFila();
  vigiarFila();
  if (tagUrl) { abrirVideo(tagUrl); return; }
  if (r?.passo === 'video' && r.tag) { abrirVideo(r.tag); return; }
  // a gravação não sobrevive a um recarregamento: volta no máximo para a voz
  const passo = r?.passo === 'video' ? 'voz' : (r?.passo || 'pauta');
  irPara(passo === 'roteiro' || passo === 'voz' ? (state.roteiro.cenas.length ? passo : 'pauta') : 'pauta');
})();
