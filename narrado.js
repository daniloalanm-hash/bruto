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
const K = { gemini: 'bruto:gemini:key', modelo: 'bruto:gemini:model', senha: 'bruto:narrado:senha', rasc: 'bruto:narrado:rascunho' };
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

function promptRoteiro(materia, segundos) {
  const palavras = Math.round(segundos * 2.6);
  return `Você é roteirista de vídeos curtos virais de games (TikTok/Reels/Shorts) no estilo "narração sem rosto": uma voz conta a história enquanto aparecem cenas dos jogos com legenda grande.

Escreva o roteiro a partir da matéria abaixo.

REGRAS
- Português do Brasil, falado e natural, frases curtas, como quem conta uma novidade para um amigo gamer.
- Cerca de ${palavras} palavras no total (~${segundos} segundos de fala).
- A PRIMEIRA frase é um gancho forte (curiosidade, contraste ou número surpreendente). Nada de "neste vídeo".
- Divida em 6 a 10 tópicos. Cada tópico = uma ideia; trocar de tópico = trocar de assunto/jogo.
- O último tópico é uma pergunta para gerar comentários + "comenta aí".
- Números em algarismos (2026, 30, 11). Sem reticências, sem emojis, sem hashtags, sem aspas.
- Use os nomes dos jogos exatamente como aparecem na loja da Steam.
- Não invente fatos que não estão na matéria.

Para cada tópico informe:
- "texto": o que será falado.
- "destaques": 2 a 4 palavras do próprio texto (copiadas exatamente) que merecem destaque amarelo na legenda.
- "assunto": nome do jogo de que esse trecho está falando (para mostrar imagens desse jogo). Use null se for um comentário geral — aí aparece o jogo principal. NUNCA ponha um jogo que não está sendo citado naquele trecho.

Responda SOMENTE com JSON:
{"titulo": "...", "jogos": ["nome do jogo principal", "outros jogos citados"], "cenas": [{"texto": "...", "destaques": ["..."], "assunto": "..." }]}

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
      return {
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
      h('div', {}, h('b', {}, m.titulo), h('span', { class: 'muted small' }, `${m.texto.split(/\s+/).length} palavras lidas · ${m.resumo || ''}`)),
    );
    p.hidden = false;
    $('#textoMateria').value = m.texto;
    atualizaBotaoRoteiro();
  } catch (e) {
    toast(e.message, 'warn', 7000);
    $('#boxTexto').open = true;
  } finally { btn.classList.remove('busy'); }
});

async function escreverRoteiro() {
  const texto = $('#textoMateria').value.trim();
  const materia = { ...(state.materia || {}), texto: texto || state.materia?.texto || '' };
  if (!materia.titulo && texto) materia.titulo = texto.split('\n')[0].slice(0, 100);
  const st = $('#statusRoteiro');
  st.textContent = 'A IA está escrevendo o roteiro…';
  $('#btnRoteiro').classList.add('busy');
  try {
    const r = await gemini(promptRoteiro(materia, +$('#duracao').value), (m) => { st.textContent = m; });
    state.roteiro = {
      titulo: r.titulo || materia.titulo || '',
      jogos: (r.jogos || []).filter(Boolean),
      cenas: (r.cenas || []).filter((c) => c.texto).map((c) => ({ texto: c.texto, destaques: c.destaques || [], assunto: c.assunto || null })),
    };
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

/* ---------------- 2. roteiro ---------------- */
function nomesJogos() { return $('#jogos').value.split('\n').map((s) => s.trim()).filter(Boolean); }
function estimativa() {
  const n = state.roteiro.cenas.reduce((a, c) => a + c.texto.split(/\s+/).filter(Boolean).length, 0);
  $('#estimativa').textContent = `${Math.round(n / 2.6)} s`;
}
function renderRoteiro() {
  const r = state.roteiro;
  $('#titulo').value = r.titulo;
  $('#jogos').value = r.jogos.join('\n');
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
    const mover = (d) => { const j = i + d; if (j < 0 || j >= r.cenas.length) return; [r.cenas[i], r.cenas[j]] = [r.cenas[j], r.cenas[i]]; renderRoteiro(); };
    box.append(h('div', { class: 'cena' },
      h('div', { class: 'campos' }, txt,
        h('div', { class: 'linha2' },
          h('label', { class: 'field' }, h('span', {}, 'Destaques na legenda'), dest),
          h('label', { class: 'field' }, h('span', {}, 'Jogo na tela'), sel),
          h('div', { class: 'acoes' },
            h('button', { class: 'btn small icon', title: 'Subir', onclick: () => mover(-1) }, '↑'),
            h('button', { class: 'btn small icon', title: 'Descer', onclick: () => mover(1) }, '↓'),
            h('button', { class: 'btn small icon danger', title: 'Remover', onclick: () => { r.cenas.splice(i, 1); renderRoteiro(); } }, '✕'))))));
  });
  estimativa();
  salvarRascunho();
}
$('#titulo').addEventListener('input', (e) => { state.roteiro.titulo = e.target.value; salvarRascunho(); });
$('#jogos').addEventListener('change', () => { state.roteiro.jogos = nomesJogos(); renderRoteiro(); });
$('#btnAddCena').addEventListener('click', () => { state.roteiro.cenas.push({ texto: '', destaques: [], assunto: null }); renderRoteiro(); });
$('#btnCopiar').addEventListener('click', async () => {
  const t = state.roteiro.cenas.map((c) => c.texto).join('\n\n');
  try { await navigator.clipboard.writeText(t); toast('Roteiro copiado.'); } catch { toast('Não consegui copiar; selecione o texto manualmente.', 'warn'); }
});
$('#btnIrVoz').addEventListener('click', () => {
  state.roteiro.jogos = nomesJogos();
  if (!state.roteiro.cenas.some((c) => c.texto.trim())) { toast('Escreva pelo menos um tópico.', 'warn'); return; }
  if (!state.roteiro.jogos.length) { toast('Informe pelo menos um jogo (é de lá que vêm as imagens).', 'warn'); $('#jogos').focus(); return; }
  irPara('voz');
});

/* ---------------- 3. voz ---------------- */
function renderTeleprompter() {
  $('#teleprompter').replaceChildren(...state.roteiro.cenas.filter((c) => c.texto.trim()).map((c) => h('p', {}, c.texto)));
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

$('#btnGerar').addEventListener('click', async () => {
  const modo = state.voz.modo === 'ia' ? 'ia' : 'gravada';
  if (modo === 'gravada' && !state.voz.blob) { toast('Grave ou envie o áudio primeiro.', 'warn'); return; }
  if (grav.rec?.state === 'recording') { toast('Pare a gravação antes de gerar.', 'warn'); return; }
  const st = $('#statusEnvio'), btn = $('#btnGerar');
  btn.classList.add('busy');
  try {
    const r = state.roteiro;
    const job = {
      titulo: r.titulo, fonte: state.materia?.url || '',
      jogos: r.jogos,
      cenas: r.cenas.filter((c) => c.texto.trim()),
      voz: modo === 'ia' ? { modo, voz_ia: $('#vozIa').value } : { modo, arquivo: state.voz.nome },
    };
    st.textContent = 'Criando o vídeo…';
    const { tag } = await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'criar', job }) });
    if (modo === 'gravada') {
      const PARTE = 3 * 1024 * 1024; // limite de upload das funções do Vercel
      const total = Math.ceil(state.voz.blob.size / PARTE);
      for (let n = 0; n < total; n++) {
        st.textContent = `Enviando o áudio (${n + 1}/${total})…`;
        const base64 = await paraBase64(state.voz.blob.slice(n * PARTE, (n + 1) * PARTE));
        await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'parte', tag, n, base64 }) });
      }
    }
    st.textContent = 'Mandando para o servidor de render…';
    await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'iniciar', tag, fase: 'preparar' }) });
    st.textContent = '';
    abrirVideo(tag);
  } catch (e) {
    st.textContent = `Erro: ${e.message}`;
  } finally { btn.classList.remove('busy'); }
});

/* ---------------- 4. vídeo: acompanhar ---------------- */
function abrirVideo(tag) {
  state.tag = tag;
  state.projeto = null;
  state.sujo = false;
  $('#ajustes').hidden = true;
  $('#player').hidden = true;
  $('#player').removeAttribute('src');
  $('#btnBaixar').hidden = true;
  $('#btnAplicar').disabled = true;
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
        $('#aguarde').classList.add('erro');
        $('#etapa').textContent = `Deu erro: ${st.erro || 'falha no render'}`;
        $('#aguarde').hidden = false;
        if (s.video) mostrarVideo(s); // ainda dá para ver a versão anterior
        return;
      }
      if (st.estado === 'pronto' && s.video) {
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
}

async function carregarProjeto() {
  try {
    state.projeto = await api(`/api/status?tag=${encodeURIComponent(state.tag)}&projeto=1`);
  } catch (e) { toast(`Não consegui abrir o projeto para ajustes: ${e.message}`, 'warn', 7000); return; }
  const p = state.projeto;
  const info = [`${fmtT(p.duracao || 0)} min`, `versão ${p.versao}`];
  if (p.voz?.modo === 'gravada') info.push(p.voz.reducao_aplicada ? `redução de ruído ligada (fundo ${p.voz.ruido_db} dB)` : 'gravação limpa, sem redução de ruído');
  $('#infoVideo').textContent = `${p.titulo} · ${info.join(' · ')}`;
  $('#ruido').value = p.voz?.reducao_ruido || 'auto';
  $('#ajustes').hidden = false;
  state.sujo = false;
  $('#btnAplicar').disabled = true;
  renderTomadas();
  renderCenas();
}

function marcarSujo() { state.sujo = true; $('#btnAplicar').disabled = false; }
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
        w.manter = !w.manter;
        marcarSujo(); renderTomadas();
      });
      span.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        if (!w.manter) return;
        const inp = h('input', { type: 'text', value: w.legenda || '', size: Math.max(4, (w.legenda || '').length + 2) });
        const fim = () => { w.legenda = inp.value.trim(); marcarSujo(); renderTomadas(); };
        inp.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') inp.blur(); if (ev.key === 'Escape') renderTomadas(); });
        inp.addEventListener('blur', fim);
        span.replaceChildren(inp); inp.focus(); inp.select();
      });
      linha.append(span, ' ');
    }
    if (!mantida) linha.addEventListener('click', () => trazerTomada(g));
    box.append(linha);
  }
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
const nomeJogo = (chave) => state.projeto?.jogos?.find((j) => j.chave === chave)?.nome || chave;
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
function renderCenas() {
  const p = state.projeto;
  const box = $('#listaCenas');
  box.replaceChildren();
  const usados = new Map((p.timeline || []).map((t) => [t.cena, t.cortes.length]));
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
    box.append(h('div', { class: 'lc-cena' },
      h('b', {}, `Tópico ${ci + 1} · ${nomeJogo(c.jogo)}`),
      h('p', {}, `${n ? `${n} cortes · ` : ''}${c.texto}`),
      tira));
  });
}

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
  $('#filtroJogos').replaceChildren(...p.jogos.map((j) => h('button', {
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
        if (p.banco[id].jogo !== c.jogo) c.jogo = p.banco[id].jogo;
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
  const btn = $('#btnAplicar');
  btn.classList.add('busy');
  try {
    const p = { ...state.projeto };
    delete p.timeline;
    await api('/api/job', { method: 'POST', body: JSON.stringify({ acao: 'projeto', tag: state.tag, projeto: p }) });
    state.sujo = false;
    btn.disabled = true;
    $('#aguarde').hidden = false;
    $('#aguarde').classList.remove('erro');
    $('#etapa').textContent = 'Na fila…';
    $('#progresso').style.width = '1%';
    $('#player').hidden = true;
    $('#ajustes').hidden = true;
    acompanhar();
  } catch (e) {
    toast(e.message, 'warn', 7000);
  } finally { btn.classList.remove('busy'); }
});

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
  if (state.sujo || grav.rec?.state === 'recording') { e.preventDefault(); e.returnValue = ''; }
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
  atualizaBotaoRoteiro();
  if (tagUrl) { abrirVideo(tagUrl); return; }
  if (r?.passo === 'video' && r.tag) { abrirVideo(r.tag); return; }
  // a gravação não sobrevive a um recarregamento: volta no máximo para a voz
  const passo = r?.passo === 'video' ? 'voz' : (r?.passo || 'pauta');
  irPara(passo === 'roteiro' || passo === 'voz' ? (state.roteiro.cenas.length ? passo : 'pauta') : 'pauta');
})();
