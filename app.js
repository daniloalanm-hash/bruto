// Bruto — edição de vídeo bruto no navegador.
// Tudo roda localmente: os vídeos nunca saem do computador de quem edita.
import { FFmpeg } from './vendor/ffmpeg/index.js';

const CORE_BASE = 'https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/esm';
const STORE_KEY = 'bruto:projeto:v1';
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const DEFAULT_SETTINGS = { aspect: '16:9', fit: 'contain', res: 1080, fps: 30, audio: true, quality: 'final' };

/* ---------------- utilidades ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (typeof v === 'boolean' || k === 'value') e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const k of kids.flat(Infinity)) if (k != null && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
}
const uid = () => Math.random().toString(36).slice(2, 10);
const pad = (n, l = 2) => String(n).padStart(l, '0');
function fmt(t) {
  if (!isFinite(t) || t < 0) t = 0;
  const tenths = Math.round(t * 10);
  return `${pad(Math.floor(tenths / 600))}:${(tenths % 600 / 10).toFixed(1).padStart(4, '0')}`;
}
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const slug = (s) => (s || 'bruto').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^\w]+/g, '-').replace(/^-|-$/g, '').toLowerCase() || 'bruto';
function toast(msg, kind = 'info', ms = 3800) {
  const t = h('div', { class: `toast ${kind}` }, msg);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}
function download(name, data, type = 'application/octet-stream') {
  const isUrl = typeof data === 'string' && (data.startsWith('data:') || data.startsWith('blob:'));
  const url = isUrl ? data : URL.createObjectURL(data instanceof Blob ? data : new Blob([data], { type }));
  const a = h('a', { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  if (!isUrl) setTimeout(() => URL.revokeObjectURL(url), 60000);
}
function waitEvent(el, ev, timeout = 8000) {
  return new Promise((res, rej) => {
    let t;
    const clear = () => { clearTimeout(t); el.removeEventListener(ev, ok); el.removeEventListener('error', bad); };
    const ok = () => { clear(); res(); };
    const bad = () => { clear(); rej(new Error('Não foi possível ler o vídeo.')); };
    el.addEventListener(ev, ok, { once: true });
    el.addEventListener('error', bad, { once: true });
    t = setTimeout(() => { clear(); res(); }, timeout);
  });
}
async function seek(v, t) {
  const target = clamp(t, 0, Math.max(0, (v.duration || t) - 0.04));
  if (Math.abs(v.currentTime - target) < 0.001 && v.readyState >= 2) return;
  const p = waitEvent(v, 'seeked', 4000);
  v.currentTime = target;
  await p;
}

/* ---------------- estado ---------------- */
const state = {
  name: 'Projeto sem nome',
  sources: [],   // {id, name, size, file, url, duration, width, height, analyzed, unsupported, hasAudio, analyzing}
  scenes: [],    // {id, srcId, start, end, bright, sharp, motion, flags, thumbs, thumb}
  clips: [],     // {id, srcId, in, out, label, speed, mute, note}
  settings: { ...DEFAULT_SETTINGS },
  template: 'imovel', briefing: '', targetDur: 60,
  selectedClip: null, activeSrc: null, mode: 'source',
  markIn: null, markOut: null,
  sensitivity: 5, zoom: 14, sceneSeq: 0, srcSeq: 0,
};
const srcById = (id) => state.sources.find((s) => s.id === id);
const sceneById = (id) => state.scenes.find((s) => s.id === id);
const clipDur = (c) => (c.out - c.in) / c.speed;
const seqDur = () => state.clips.reduce((a, c) => a + clipDur(c), 0);
const srcIndex = (id) => state.sources.findIndex((s) => s.id === id);

/* ---------------- histórico (desfazer) ---------------- */
const hist = { past: [], future: [] };
const snap = () => JSON.stringify({ clips: state.clips, settings: state.settings });
function checkpoint() {
  hist.past.push(snap());
  if (hist.past.length > 150) hist.past.shift();
  hist.future = [];
  updateUndoButtons();
}
function restoreSnap(s) {
  const o = JSON.parse(s);
  state.clips = o.clips; state.settings = o.settings;
  if (!state.clips.some((c) => c.id === state.selectedClip)) state.selectedClip = null;
  renderAll(); persist();
}
function undo() { if (!hist.past.length) return; hist.future.push(snap()); restoreSnap(hist.past.pop()); updateUndoButtons(); }
function redo() { if (!hist.future.length) return; hist.past.push(snap()); restoreSnap(hist.future.pop()); updateUndoButtons(); }
function updateUndoButtons() { $('#btnUndo').disabled = !hist.past.length; $('#btnRedo').disabled = !hist.future.length; }

/* ---------------- persistência ---------------- */
function projectData() {
  return {
    format: 'bruto-projeto', version: 1, name: state.name, savedAt: new Date().toISOString(),
    settings: state.settings, template: state.template, briefing: state.briefing, targetDur: state.targetDur,
    sources: state.sources.map(({ id, name, size, duration, width, height, analyzed, hasAudio }) => ({ id, name, size, duration, width, height, analyzed, hasAudio })),
    scenes: state.scenes.map(({ thumbs, thumb, samples, ...rest }) => rest),
    clips: state.clips, sceneSeq: state.sceneSeq, srcSeq: state.srcSeq,
  };
}
let persistTimer;
function persist() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(projectData())); } catch (e) { console.warn('Falha ao salvar localmente', e); }
  }, 400);
}
function loadProject(p) {
  if (p?.format !== 'bruto-projeto') throw new Error('Este arquivo não é um projeto do Bruto.');
  stopSeq();
  const old = state.sources;
  state.name = p.name || 'Projeto sem nome';
  state.settings = { ...DEFAULT_SETTINGS, ...(p.settings || {}) };
  state.template = p.template || 'imovel'; state.briefing = p.briefing || ''; state.targetDur = p.targetDur || 60;
  state.sources = (p.sources || []).map((s) => {
    const m = old.find((o) => o.file && o.name === s.name && o.size === s.size);
    return { ...s, file: m?.file || null, url: m?.url || null };
  });
  for (const o of old) if (o.url && !state.sources.some((s) => s.url === o.url)) URL.revokeObjectURL(o.url);
  state.scenes = p.scenes || [];
  state.clips = (p.clips || []).map((c) => ({ label: '', speed: 1, mute: false, note: '', ...c }));
  state.sceneSeq = p.sceneSeq || state.scenes.length;
  state.srcSeq = p.srcSeq || state.sources.length;
  state.selectedClip = null; state.markIn = state.markOut = null;
  state.activeSrc = state.sources.find((s) => s.file)?.id || state.sources[0]?.id || null;
  hist.past = []; hist.future = [];
  playerSrc = null; player.removeAttribute('src'); player.load();
  renderAll(); persist();
  if (state.activeSrc && srcById(state.activeSrc)?.file) openSource(state.activeSrc);
}
function newProject() {
  stopSeq();
  for (const s of state.sources) if (s.url) URL.revokeObjectURL(s.url);
  Object.assign(state, {
    name: 'Projeto sem nome', sources: [], scenes: [], clips: [], settings: { ...DEFAULT_SETTINGS },
    template: 'imovel', briefing: '', targetDur: 60, selectedClip: null, activeSrc: null,
    markIn: null, markOut: null, sceneSeq: 0, srcSeq: 0,
  });
  hist.past = []; hist.future = [];
  thumbCache.clear();
  playerSrc = null; player.removeAttribute('src'); player.load();
  setMode('source');
  renderAll(); persist();
}

/* ---------------- arquivos ---------------- */
function readMeta(url) {
  return new Promise((res, rej) => {
    const v = document.createElement('video');
    v.preload = 'metadata'; v.muted = true;
    const t = setTimeout(() => rej(new Error('timeout')), 15000);
    v.onloadedmetadata = () => { clearTimeout(t); res({ duration: v.duration, width: v.videoWidth, height: v.videoHeight }); v.removeAttribute('src'); v.load(); };
    v.onerror = () => { clearTimeout(t); rej(new Error('meta')); };
    v.src = url;
  });
}
async function addFiles(fileList) {
  const files = [...fileList].filter((f) => f.type.startsWith('video/') || /\.(mp4|mov|m4v|webm|mkv)$/i.test(f.name));
  if (!files.length) { toast('Nenhum arquivo de vídeo reconhecido.', 'warn'); return; }
  const fresh = [];
  let relinked = 0;
  for (const f of files) {
    let s = state.sources.find((x) => !x.file && x.name === f.name && x.size === f.size);
    if (s) { s.file = f; s.url = URL.createObjectURL(f); relinked++; }
    else {
      if (state.sources.some((x) => x.file && x.name === f.name && x.size === f.size)) continue;
      s = { id: 'V' + (++state.srcSeq), name: f.name, size: f.size, file: f, url: URL.createObjectURL(f), duration: 0, width: 0, height: 0, analyzed: false };
      state.sources.push(s); fresh.push(s);
    }
    try {
      const m = await readMeta(s.url);
      Object.assign(s, m);
      s.unsupported = !m.width || !isFinite(m.duration);
    } catch { s.unsupported = true; }
    if (s.unsupported) toast(`${s.name}: o navegador não abriu este vídeo (provavelmente HEVC/H.265). Converta para H.264 ou grave em "Mais compatível".`, 'warn', 8000);
  }
  if (!srcById(state.activeSrc)?.file) state.activeSrc = state.sources.find((s) => s.file && !s.unsupported)?.id || null;
  renderAll(); persist();
  if (state.activeSrc && state.mode === 'source' && !playerSrc) openSource(state.activeSrc);
  if (relinked) toast(`${relinked} arquivo(s) reconectado(s) ao projeto.`);
  for (const s of fresh) if (!s.unsupported) await analyzeSource(s);
}

/* ---------------- captura de quadros ---------------- */
const grab = { v: Object.assign(document.createElement('video'), { muted: true, preload: 'auto', playsInline: true }), srcId: null, q: Promise.resolve() };
function withGrab(fn) { const p = grab.q.then(fn); grab.q = p.catch(() => {}); return p; }
async function grabLoad(src) {
  if (grab.srcId === src.id) return;
  grab.srcId = null;
  grab.v.src = src.url;
  await waitEvent(grab.v, 'loadeddata', 15000);
  grab.srcId = src.id;
}
function drawFit(ctx, v, W, H) {
  ctx.fillStyle = '#000'; ctx.fillRect(0, 0, W, H);
  if (!v.videoWidth) return;
  const r = Math.min(W / v.videoWidth, H / v.videoHeight);
  const w = v.videoWidth * r, hh = v.videoHeight * r;
  ctx.drawImage(v, (W - w) / 2, (H - hh) / 2, w, hh);
}
function snapJPEG(v, W = 240, H = 135, q = 0.72) {
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  drawFit(c.getContext('2d'), v, W, H);
  return c.toDataURL('image/jpeg', q);
}
const thumbCache = new Map();
function getThumb(srcId, t) {
  const key = `${srcId}@${t.toFixed(1)}`;
  if (thumbCache.has(key)) return thumbCache.get(key);
  const src = srcById(srcId);
  if (!src?.file || src.unsupported) return Promise.resolve(null);
  const p = withGrab(async () => { await grabLoad(src); await seek(grab.v, t); return snapJPEG(grab.v, 160, 90, 0.7); }).catch(() => null);
  thumbCache.set(key, p);
  return p;
}
async function ensureSceneThumbs(sc) {
  if (sc.thumbs?.length) return;
  const src = srcById(sc.srcId);
  if (!src?.file || src.unsupported) return;
  await withGrab(async () => {
    await grabLoad(src);
    sc.thumbs = [];
    for (const f of [0.15, 0.5, 0.85]) { await seek(grab.v, sc.start + (sc.end - sc.start) * f); sc.thumbs.push(snapJPEG(grab.v)); }
    sc.thumb = sc.thumbs[1];
  });
}

/* ---------------- análise (decupagem automática) ---------------- */
async function analyzeSource(src) {
  if (!src.file || src.unsupported || src.analyzing != null) return;
  src.analyzing = 0; renderBin();
  try {
    await withGrab(async () => {
      await grabLoad(src);
      const v = grab.v, dur = src.duration;
      const step = dur <= 60 ? 0.25 : dur <= 180 ? 0.5 : dur <= 2400 ? 1 : 2;
      const W = 128, H = 72;
      const c = document.createElement('canvas'); c.width = W; c.height = H;
      const ctx = c.getContext('2d', { willReadFrequently: true });
      const samples = [];
      let prev = null, lastUI = 0;
      for (let t = 0; t < dur; t += step) {
        await seek(v, t);
        drawFit(ctx, v, W, H);
        const d = ctx.getImageData(0, 0, W, H).data;
        const g = new Float32Array(W * H);
        let sum = 0;
        for (let i = 0, j = 0; i < g.length; i++, j += 4) { const y = (0.299 * d[j] + 0.587 * d[j + 1] + 0.114 * d[j + 2]) / 255; g[i] = y; sum += y; }
        let diff = 0;
        if (prev) { for (let i = 0; i < g.length; i++) diff += Math.abs(g[i] - prev[i]); diff /= g.length; }
        let ls = 0, lq = 0, n = 0;
        for (let y = 1; y < H - 1; y++) for (let x = 1; x < W - 1; x++) {
          const i = y * W + x;
          const l = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - W] - g[i + W];
          ls += l; lq += l * l; n++;
        }
        const sig = new Float32Array(27), cnt = new Float32Array(9);
        for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
          const cell = Math.min(2, Math.floor((y * 3) / H)) * 3 + Math.min(2, Math.floor((x * 3) / W));
          const j = (y * W + x) * 4;
          sig[cell * 3] += d[j]; sig[cell * 3 + 1] += d[j + 1]; sig[cell * 3 + 2] += d[j + 2]; cnt[cell]++;
        }
        for (let k = 0; k < 27; k++) sig[k] /= cnt[Math.floor(k / 3)] * 255;
        samples.push({ t, diff, bright: sum / g.length, sharp: lq / n - (ls / n) ** 2, sig });
        prev = g;
        if (performance.now() - lastUI > 150) { src.analyzing = t / dur; updateAnalyzeLabel(src); lastUI = performance.now(); }
      }
      // cortes: salto grande de imagem em relação ao movimento recente
      const T = 0.3 - (state.sensitivity - 1) * 0.025;
      const MIN = 1.0;
      const bounds = [0];
      for (let i = 1; i < samples.length; i++) {
        const s = samples[i];
        const recent = samples.slice(Math.max(1, i - 5), i);
        const avg = recent.length ? recent.reduce((a, b) => a + b.diff, 0) / recent.length : 0;
        const cutT = (samples[i - 1].t + s.t) / 2;
        const prevD = samples[i - 1].diff, nextD = samples[i + 1]?.diff ?? 0;
        const isolated = prevD < s.diff * 0.35 && nextD < s.diff * 0.35;
        if (isolated && s.diff > T && s.diff > 2.5 * avg + 0.02 && cutT - bounds[bounds.length - 1] >= MIN && dur - cutT >= MIN * 0.5) {
          bounds.push(cutT); s.isCut = true;
        }
      }
      bounds.push(dur);
      // gravações contínuas: divide quando o ambiente muda (cores e composição mudam de forma consistente)
      const SOFT_MIN = clamp(dur / 130, 5, 12);
      const D = 0.16 - (state.sensitivity - 1) * 0.012;
      const soft = [];
      for (let h = 0; h < bounds.length - 1; h++) {
        const a = bounds[h], b = bounds[h + 1];
        let start = a, mean = null, n = 0;
        for (const smp of samples) {
          if (smp.t < a || smp.t >= b) continue;
          if (!mean) { mean = Float32Array.from(smp.sig); n = 1; continue; }
          let dist = 0;
          for (let k = 0; k < 27; k++) dist += Math.abs(smp.sig[k] - mean[k]);
          dist /= 27;
          if (dist > D && smp.t - start >= SOFT_MIN && b - smp.t >= SOFT_MIN * 0.5) {
            soft.push(smp.t); start = smp.t; mean = Float32Array.from(smp.sig); n = 1;
          } else {
            n = Math.min(n + 1, 8);
            for (let k = 0; k < 27; k++) mean[k] += (smp.sig[k] - mean[k]) / n;
          }
        }
      }
      bounds.push(...soft);
      bounds.sort((p, q) => p - q);
      const made = [];
      for (let i = 0; i < bounds.length - 1; i++) {
        const start = +bounds[i].toFixed(2), end = +bounds[i + 1].toFixed(2);
        const inside = samples.filter((s) => s.t >= start && s.t < end);
        const mean = (arr, k) => (arr.length ? arr.reduce((a, b) => a + b[k], 0) / arr.length : 0);
        const moving = inside.slice(1).filter((s) => !s.isCut);
        made.push({
          id: '', srcId: src.id, start, end,
          bright: mean(inside, 'bright'), sharp: mean(inside, 'sharp'),
          motion: mean(moving, 'diff') * (0.5 / step), flags: [],
          samples: inside.map((x) => ({ t: x.t, d: x.diff * (0.5 / step), sh: x.sharp, b: x.bright, cut: !!x.isCut })),
        });
      }
      for (const [mi, sc] of made.entries()) {
        const lbl = document.getElementById(`ap-${src.id}`);
        if (lbl) lbl.textContent = `Miniaturas ${mi + 1}/${made.length}`;
        sc.thumbs = [];
        for (const f of [0.15, 0.5, 0.85]) { await seek(v, sc.start + (sc.end - sc.start) * f); sc.thumbs.push(snapJPEG(v)); }
        sc.thumb = sc.thumbs[1];
      }
      state.scenes = state.scenes.filter((x) => x.srcId !== src.id).concat(made);
      state.scenes.sort((a, b) => srcIndex(a.srcId) - srcIndex(b.srcId) || a.start - b.start);
      renumberScenes();
      src.analyzed = true;
    });
  } catch (e) {
    toast(`Falha ao analisar ${src.name}: ${e.message}`, 'warn');
  }
  src.analyzing = null;
  computeFlags();
  renderAll(); persist();
}
function renumberScenes() {
  state.scenes.forEach((s, i) => { s.id = 'C' + pad(i + 1, 3); });
  state.sceneSeq = state.scenes.length;
}
function computeFlags() {
  const brs = state.scenes.map((s) => s.bright).sort((a, b) => a - b);
  const brightMed = brs[Math.floor(brs.length / 2)] || 0.5;
  const sharps = state.scenes.map((s) => s.sharp).filter((x) => x > 0).sort((a, b) => a - b);
  const med = sharps[Math.floor(sharps.length / 2)] || 0;
  for (const s of state.scenes) {
    const f = [];
    if (s.bright < Math.min(0.18, brightMed * 0.6)) f.push('escuro');
    if (s.bright > 0.85) f.push('estourado');
    if (med && sharps.length > 2 && s.sharp < med * 0.35) f.push('desfocado');
    if (s.motion > 0.06) f.push('muito movimento');
    if (s.end - s.start < 1.2) f.push('curta');
    s.flags = f;
  }
}
async function analyzeAll(force) {
  for (const s of state.sources) if (s.file && !s.unsupported && (force || !s.analyzed)) await analyzeSource(s);
}
function updateAnalyzeLabel(src) {
  const el = document.getElementById(`ap-${src.id}`);
  if (el) el.textContent = `Analisando ${Math.round(src.analyzing * 100)}%`;
}

/* ---------------- painel de material ---------------- */
function renderBin() {
  const list = $('#binList');
  list.replaceChildren();
  $('#binCount').textContent = state.sources.length ? `${state.sources.length} arquivo(s), ${state.scenes.length} cena(s)` : '';
  if (!state.sources.length) {
    list.append(h('div', { class: 'empty' },
      h('p', {}, 'Arraste os arquivos brutos para esta janela ou use “Adicionar vídeos”.'),
      h('p', { class: 'muted' }, 'Os vídeos não são enviados para nenhum servidor: a análise e o render acontecem no seu navegador.')));
    return;
  }
  for (const s of state.sources) {
    const scenes = state.scenes.filter((x) => x.srcId === s.id);
    const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
    let status;
    if (!s.file) status = h('button', { class: 'link warn', onclick: stop(() => $('#fileVideos').click()) }, 'Arquivo ausente. Localizar');
    else if (s.unsupported) status = h('span', { class: 'warn' }, 'Formato não suportado');
    else if (s.analyzing != null) status = h('span', { id: `ap-${s.id}` }, `Analisando ${Math.round(s.analyzing * 100)}%`);
    else status = h('button', { class: 'link', onclick: stop(() => analyzeSource(s)) }, s.analyzed ? 'Reanalisar' : 'Analisar');
    const head = h('div', { class: 'src-head' + (state.activeSrc === s.id ? ' active' : ''), onclick: () => s.file && openSource(s.id) },
      h('div', { class: 'src-name', title: s.name }, s.name),
      h('div', { class: 'src-meta' }, `${fmt(s.duration)}   ${s.width || '?'}×${s.height || '?'}`),
      h('div', { class: 'src-status' }, status));
    list.append(h('section', { class: 'src' }, head, scenes.length ? h('div', { class: 'scene-grid' }, scenes.map(sceneCard)) : null));
  }
}
function sceneCard(sc) {
  const used = state.clips.some((c) => c.srcId === sc.srcId && c.in < sc.end && c.out > sc.start);
  const thumb = h('div', { class: 'thumb', style: { backgroundImage: sc.thumb ? `url(${sc.thumb})` : 'none' } },
    h('button', { class: 'add', title: 'Adicionar à timeline', 'aria-label': `Adicionar ${sc.id} à timeline`, onclick: (e) => { e.stopPropagation(); addClipFromScene(sc); } }, '+'));
  if (!sc.thumb && srcById(sc.srcId)?.file) {
    getThumb(sc.srcId, (sc.start + sc.end) / 2).then((u) => { if (u) { sc.thumb = u; thumb.style.backgroundImage = `url(${u})`; } });
  }
  return h('div', {
    class: 'scene' + (used ? ' used' : ''),
    title: 'Clique para ver no player. Duplo clique adiciona à timeline.',
    onclick: () => previewScene(sc), ondblclick: () => addClipFromScene(sc),
  },
  thumb,
  h('div', { class: 'scene-info' }, h('b', {}, sc.id), h('span', {}, `${fmt(sc.start)}–${fmt(sc.end)}`)),
  sc.flags?.length ? h('div', { class: 'flags' }, sc.flags.map((f) => h('span', { class: 'flag' }, f))) : null);
}
async function previewScene(sc) {
  if (state.mode !== 'source') setMode('source');
  await openSource(sc.srcId);
  await seek(player, sc.start);
  state.markIn = sc.start; state.markOut = sc.end;
  renderScrub();
}

/* ---------------- edição de clipes ---------------- */
function insertClip(partial) {
  const src = srcById(partial.srcId);
  const a = clamp(partial.in, 0, src.duration), b = clamp(partial.out, 0, src.duration);
  if (b - a < 0.2) { toast('Trecho curto demais para virar clipe.', 'warn'); return; }
  checkpoint();
  const clip = { id: uid(), label: '', speed: 1, mute: false, note: '', ...partial, in: +a.toFixed(2), out: +b.toFixed(2) };
  const sel = state.clips.findIndex((c) => c.id === state.selectedClip);
  state.clips.splice(sel >= 0 ? sel + 1 : state.clips.length, 0, clip);
  state.selectedClip = clip.id;
  afterEdit();
  requestAnimationFrame(() => $('.clip.selected')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
}
function addClipFromScene(sc) { insertClip({ srcId: sc.srcId, in: sc.start, out: sc.end, label: sc.id }); }
function setClip(c, patch) {
  const s = srcById(c.srcId);
  const n = { ...c, ...patch };
  if ('in' in patch || 'out' in patch) {
    n.in = +clamp(+n.in || 0, 0, s?.duration || Infinity).toFixed(2);
    n.out = +clamp(+n.out || 0, 0, s?.duration || Infinity).toFixed(2);
    if (n.out - n.in < 0.1) { toast('A saída precisa ficar depois da entrada.', 'warn'); renderInspector(); return; }
  }
  if ('speed' in patch) n.speed = clamp(+n.speed || 1, 0.5, 4);
  checkpoint();
  Object.assign(c, n);
  afterEdit();
}
function afterEdit() { renderTimeline(); renderInspector(); renderScrub(); renderBin(); persist(); }
function selectClip(id, jump) {
  state.selectedClip = id;
  renderTimeline(); renderInspector();
  const idx = state.clips.findIndex((c) => c.id === id);
  const c = state.clips[idx];
  if (!jump || !c) return;
  if (state.mode === 'sequence') seqGoto(idx, 0, false);
  else if (srcById(c.srcId)?.file) {
    state.activeSrc = c.srcId; renderBin();
    playerLoad(c.srcId).then(() => seek(player, c.in)).then(renderScrub).catch(() => {});
  }
}
function moveClip(from, to) {
  if (from == null || from === to || from + 1 === to) return;
  checkpoint();
  const [c] = state.clips.splice(from, 1);
  if (to > from) to--;
  state.clips.splice(to, 0, c);
  afterEdit();
}
function removeClip(c) {
  const i = state.clips.indexOf(c);
  if (i < 0) return;
  checkpoint();
  state.clips.splice(i, 1);
  state.selectedClip = state.clips[Math.min(i, state.clips.length - 1)]?.id || null;
  afterEdit();
}
function duplicateClip(c) {
  checkpoint();
  const i = state.clips.indexOf(c);
  const copy = { ...c, id: uid() };
  state.clips.splice(i + 1, 0, copy);
  state.selectedClip = copy.id;
  afterEdit();
}
function splitAtPlayhead() {
  const c = state.mode === 'sequence' ? state.clips[seq.idx] : state.clips.find((x) => x.id === state.selectedClip);
  if (!c) return toast('Selecione um clipe para dividir.', 'warn');
  const t = player.currentTime;
  if (playerSrc !== c.srcId || t <= c.in + 0.1 || t >= c.out - 0.1) return toast('Posicione o player dentro do clipe para dividir.', 'warn');
  checkpoint();
  const i = state.clips.indexOf(c);
  const second = { ...c, id: uid(), in: +t.toFixed(2) };
  c.out = +t.toFixed(2);
  state.clips.splice(i + 1, 0, second);
  state.selectedClip = second.id;
  afterEdit();
}
async function showClipInSource(c) {
  setMode('source');
  await openSource(c.srcId);
  await seek(player, c.in);
  state.markIn = c.in; state.markOut = c.out;
  renderScrub();
}
function setFromPlayhead(c, key) {
  if (playerSrc !== c.srcId) return toast('Abra o arquivo deste clipe no player (botão “Ver no fonte”) para usar a posição atual.', 'warn', 5000);
  setClip(c, { [key]: player.currentTime });
}
function buildDraft() {
  const good = state.scenes.filter((s) => !s.flags?.some((f) => ['desfocado', 'escuro', 'curta'].includes(f)) && srcById(s.srcId));
  if (!good.length) return toast('Nenhuma cena analisada sem alertas. Analise os vídeos primeiro.', 'warn');
  if (state.clips.length && !confirm('Substituir a timeline atual por um rascunho com as cenas sem alertas?')) return;
  checkpoint();
  state.clips = good.map((s) => ({ id: uid(), srcId: s.srcId, in: s.start, out: s.end, label: s.id, speed: 1, mute: false, note: '' }));
  state.selectedClip = null;
  afterEdit();
  toast(`Rascunho com ${good.length} clipes. Agora é só ajustar.`);
}

/* ---------------- inspetor ---------------- */
function renderInspector() {
  const box = $('#inspector');
  box.replaceChildren();
  const c = state.clips.find((x) => x.id === state.selectedClip);
  if (!c) {
    box.append(
      h('h2', {}, 'Clipe'),
      h('p', { class: 'muted' }, 'Selecione um clipe na timeline para ajustar entrada, saída, velocidade e áudio.'),
      h('div', { class: 'shortcuts' },
        h('kbd', {}, 'Espaço'), 'Reproduzir ou pausar',
        h('kbd', {}, 'I'), 'Marcar entrada',
        h('kbd', {}, 'O'), 'Marcar saída',
        h('kbd', {}, 'A'), 'Adicionar trecho à timeline',
        h('kbd', {}, 'S'), 'Dividir clipe no player',
        h('kbd', {}, 'Del'), 'Remover clipe selecionado',
        h('kbd', {}, '← →'), 'Avançar um quadro (Shift: 1 s)',
        h('kbd', {}, 'Ctrl Z'), 'Desfazer'));
    return;
  }
  const s = srcById(c.srcId);
  const idx = state.clips.indexOf(c);
  const speeds = [...new Set([...SPEEDS, c.speed])].sort((a, b) => a - b);
  const numField = (label, key) => h('label', { class: 'field' }, h('span', {}, label),
    h('div', { class: 'row' },
      h('input', { type: 'number', step: '0.1', min: '0', value: c[key].toFixed(2), onchange: (e) => setClip(c, { [key]: +e.target.value }) }),
      h('button', { class: 'btn small', title: 'Usar a posição atual do player', onclick: () => setFromPlayhead(c, key) }, 'Posição atual')));
  box.append(
    h('h2', {}, `Clipe ${idx + 1} de ${state.clips.length}`),
    h('label', { class: 'field' }, h('span', {}, 'Nome'),
      h('input', { type: 'text', value: c.label || '', placeholder: 'Ex.: Fachada', onchange: (e) => setClip(c, { label: e.target.value }) })),
    h('p', { class: 'muted small' }, `Arquivo: ${s?.name || 'desconhecido'}`),
    numField('Entrada (s)', 'in'),
    numField('Saída (s)', 'out'),
    h('label', { class: 'field' }, h('span', {}, 'Velocidade'),
      h('select', { onchange: (e) => setClip(c, { speed: +e.target.value }) },
        speeds.map((v) => h('option', { value: String(v), selected: v === c.speed }, `${String(v).replace('.', ',')}x`)))),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: !!c.mute, onchange: (e) => setClip(c, { mute: e.target.checked }) }), 'Sem áudio neste clipe'),
    h('p', { class: 'muted small' }, `Duração na edição: ${fmt(clipDur(c))}`),
    c.note ? h('div', { class: 'note' }, h('span', {}, 'Nota do Claude'), c.note) : null,
    h('div', { class: 'btn-grid' },
      h('button', { class: 'btn small', disabled: idx === 0, onclick: () => moveClip(idx, idx - 1) }, 'Mover para trás'),
      h('button', { class: 'btn small', disabled: idx === state.clips.length - 1, onclick: () => moveClip(idx, idx + 2) }, 'Mover para frente'),
      h('button', { class: 'btn small', onclick: () => showClipInSource(c) }, 'Ver no fonte'),
      h('button', { class: 'btn small', onclick: splitAtPlayhead }, 'Dividir no player'),
      h('button', { class: 'btn small', onclick: () => duplicateClip(c) }, 'Duplicar'),
      h('button', { class: 'btn small danger', onclick: () => removeClip(c) }, 'Remover')));
}

/* ---------------- timeline ---------------- */
let dragIdx = null;
function renderTimeline() {
  const tr = $('#track');
  tr.replaceChildren();
  $('#seqSummary').textContent = state.clips.length ? `${state.clips.length} clipe(s), ${fmt(seqDur())}` : 'vazia';
  if (!state.clips.length) {
    tr.append(h('div', { class: 'empty-track' }, 'Monte a sequência com duplo clique numa cena, com o botão + ou marcando entrada e saída no player. Também dá para pedir a montagem ao Claude.'));
    return;
  }
  state.clips.forEach((c, i) => {
    const w = Math.max(70, clipDur(c) * state.zoom);
    const thumb = h('div', { class: 'clip-thumb' });
    const el = h('div', {
      class: 'clip' + (c.id === state.selectedClip ? ' selected' : ''),
      style: { width: `${w}px` }, draggable: true, tabindex: '0',
      title: `${c.label || `Clipe ${i + 1}`}: ${fmt(c.in)} a ${fmt(c.out)} de ${srcById(c.srcId)?.name || '?'}`,
      onclick: () => selectClip(c.id, true),
      onkeydown: (e) => { if (e.key === 'Enter') selectClip(c.id, true); },
      ondragstart: (e) => { dragIdx = i; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(i)); el.classList.add('dragging'); },
      ondragend: () => { el.classList.remove('dragging'); dragIdx = null; },
      ondragover: (e) => {
        if (dragIdx == null) return;
        e.preventDefault();
        const r = el.getBoundingClientRect();
        const before = e.clientX < r.left + r.width / 2;
        el.classList.toggle('drop-before', before); el.classList.toggle('drop-after', !before);
      },
      ondragleave: () => el.classList.remove('drop-before', 'drop-after'),
      ondrop: (e) => {
        if (dragIdx == null) return;
        e.preventDefault();
        const r = el.getBoundingClientRect();
        const before = e.clientX < r.left + r.width / 2;
        el.classList.remove('drop-before', 'drop-after');
        moveClip(dragIdx, before ? i : i + 1);
      },
    },
    thumb,
    h('div', { class: 'clip-body' },
      h('div', { class: 'clip-label' }, c.label || `Clipe ${i + 1}`),
      h('div', { class: 'clip-meta' }, fmt(clipDur(c)),
        c.speed !== 1 ? h('span', { class: 'badge' }, `${String(c.speed).replace('.', ',')}x`) : null,
        c.mute ? h('span', { class: 'badge' }, 'mudo') : null)));
    tr.append(el);
    getThumb(c.srcId, Math.min(c.in + 0.1, c.out)).then((u) => { if (u) thumb.style.backgroundImage = `url(${u})`; });
  });
  tr.append(h('div', { class: 'tl-playhead', id: 'tlHead' }));
}

/* ---------------- player ---------------- */
const player = $('#player');
let playerSrc = null;
async function playerLoad(srcId) {
  if (playerSrc === srcId && player.getAttribute('src')) return;
  const s = srcById(srcId);
  if (!s?.url) throw new Error('Arquivo não carregado.');
  playerSrc = srcId;
  player.src = s.url;
  $('#stageEmpty').hidden = true;
  await waitEvent(player, 'loadedmetadata');
}
async function openSource(srcId) {
  state.activeSrc = srcId;
  if (state.mode !== 'source') setMode('source');
  stopSeq();
  state.markIn = state.markOut = null;
  renderBin();
  try { await playerLoad(srcId); } catch (e) { toast(e.message, 'warn'); }
  renderScrub(); updateStage();
}
function setMode(m) {
  state.mode = m;
  stopSeq();
  $$('.mode-tab').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === m)));
  $('#scrubWrap').hidden = m !== 'source';
  $('#markControls').hidden = m !== 'source';
  updateStage();
  if (m === 'sequence') { if (state.clips.length) seqGoto(Math.min(seq.idx, state.clips.length - 1), 0, false); }
  else if (state.activeSrc && srcById(state.activeSrc)?.file) playerLoad(state.activeSrc).then(renderScrub).catch(() => {});
}
function updateStage() {
  const seqMode = state.mode === 'sequence';
  const ar = seqMode && state.settings.aspect === '9:16' ? 9 / 16 : 16 / 9;
  $('#stage').style.setProperty('--ar', ar);
  player.style.objectFit = seqMode && state.settings.fit === 'cover' ? 'cover' : 'contain';
  const s = srcById(state.activeSrc);
  $('#viewerTitle').textContent = seqMode
    ? `Prévia da saída ${state.settings.aspect}, ${state.settings.fit === 'cover' ? 'preenchendo o quadro' : 'com barras'}`
    : (s ? s.name : '');
  $('#stageEmpty').hidden = !!player.getAttribute('src');
}
function togglePlay() {
  if (state.mode === 'source') {
    if (!player.getAttribute('src')) return;
    if (player.paused) player.play().catch(() => {}); else player.pause();
    return;
  }
  if (!state.clips.length) return;
  if (seq.playing) { seq.playing = false; player.pause(); return; }
  const c = state.clips[seq.idx];
  const inside = c && playerSrc === c.srcId && player.currentTime >= c.in - 0.05 && player.currentTime < c.out - 0.05;
  if (inside) {
    seq.playing = true;
    player.playbackRate = c.speed; player.muted = !state.settings.audio || c.mute;
    player.play().catch(() => {});
  } else {
    const last = seq.idx >= state.clips.length - 1;
    seqGoto(last ? 0 : seq.idx + (c && playerSrc === c.srcId && player.currentTime >= c.out - 0.05 ? 1 : 0), 0, true);
  }
}
function markIn() {
  if (state.mode !== 'source' || !player.getAttribute('src')) return;
  state.markIn = player.currentTime;
  if (state.markOut != null && state.markOut <= state.markIn) state.markOut = null;
  renderScrub();
}
function markOut() {
  if (state.mode !== 'source' || !player.getAttribute('src')) return;
  state.markOut = player.currentTime;
  if (state.markIn != null && state.markIn >= state.markOut) state.markIn = null;
  renderScrub();
}
function addMarked() {
  if (state.mode !== 'source') return;
  const s = srcById(state.activeSrc);
  if (!s?.file) return;
  let a = state.markIn, b = state.markOut;
  if (a == null && b == null) {
    const sc = state.scenes.find((x) => x.srcId === s.id && player.currentTime >= x.start && player.currentTime < x.end);
    if (!sc) return toast('Marque a entrada (I) e a saída (O) antes de adicionar.', 'warn');
    a = sc.start; b = sc.end;
  }
  insertClip({ srcId: s.id, in: a ?? 0, out: b ?? s.duration });
  state.markIn = state.markOut = null;
  renderScrub();
}
function stepFrame(dir, big) {
  if (!player.getAttribute('src')) return;
  seq.playing = false; player.pause();
  player.currentTime = clamp(player.currentTime + dir * (big ? 1 : 1 / state.settings.fps), 0, player.duration || 0);
}

/* barra do arquivo (modo fonte) */
function renderScrub() {
  const sc = $('#scrub');
  sc.replaceChildren();
  const s = srcById(state.activeSrc);
  if (!s?.duration) return;
  const D = s.duration;
  const pct = (t) => `${(t / D * 100).toFixed(3)}%`;
  for (const scn of state.scenes.filter((x) => x.srcId === s.id)) {
    sc.append(h('div', { class: 'seg' + (scn.flags?.length ? ' flagged' : ''), style: { left: pct(scn.start), width: pct(scn.end - scn.start) }, title: `${scn.id}: ${fmt(scn.start)} a ${fmt(scn.end)}${scn.flags?.length ? ` (${scn.flags.join(', ')})` : ''}` }, h('span', {}, scn.id)));
  }
  for (const c of state.clips.filter((x) => x.srcId === s.id)) sc.append(h('div', { class: 'used-bar', style: { left: pct(c.in), width: pct(c.out - c.in) } }));
  if (state.markIn != null || state.markOut != null) {
    const a = state.markIn ?? 0, b = state.markOut ?? D;
    sc.append(h('div', { class: 'range', style: { left: pct(Math.min(a, b)), width: pct(Math.abs(b - a)) } }));
  }
  sc.append(h('div', { class: 'playhead', id: 'scrubHead' }));
}
function scrubTo(e) {
  const s = srcById(state.activeSrc);
  if (!s?.duration || playerSrc !== s.id) return;
  const r = $('#scrub').getBoundingClientRect();
  player.currentTime = clamp((e.clientX - r.left) / r.width, 0, 1) * s.duration;
}

/* reprodução da sequência */
const seq = { idx: 0, playing: false, token: 0, switching: false };
function seqTimeAt(idx, srcTime) {
  let t = 0;
  for (let i = 0; i < idx && i < state.clips.length; i++) t += clipDur(state.clips[i]);
  const c = state.clips[idx];
  if (c && playerSrc === c.srcId) t += clamp(srcTime - c.in, 0, c.out - c.in) / c.speed;
  return t;
}
async function seqGoto(idx, offset = 0, play = false) {
  const tok = ++seq.token;
  const c = state.clips[idx];
  if (!c) { stopSeq(); return; }
  const s = srcById(c.srcId);
  if (!s?.url) { toast(`Arquivo ausente: ${s?.name || c.srcId}`, 'warn'); stopSeq(); return; }
  seq.switching = true; seq.idx = idx;
  try {
    await playerLoad(c.srcId); if (tok !== seq.token) return;
    player.playbackRate = c.speed;
    player.muted = !state.settings.audio || c.mute;
    await seek(player, c.in + offset); if (tok !== seq.token) return;
  } catch (e) { toast(e.message, 'warn'); stopSeq(); return; }
  seq.switching = false;
  if (play) { seq.playing = true; player.play().catch(() => {}); }
}
function stopSeq() {
  seq.playing = false; seq.token++; seq.switching = false;
  player.pause(); player.playbackRate = 1; player.muted = false;
}

/* laço de atualização visual */
let lastTime = '', lastPlay = null;
function tick() {
  requestAnimationFrame(tick);
  const playing = state.mode === 'sequence' ? seq.playing : !player.paused;
  if (playing !== lastPlay) { $('#btnPlay').textContent = playing ? '❚❚' : '▶'; $('#btnPlay').setAttribute('aria-label', playing ? 'Pausar' : 'Reproduzir'); lastPlay = playing; }
  let txt;
  if (state.mode === 'source') {
    const s = srcById(state.activeSrc);
    txt = `${fmt(player.currentTime)} / ${fmt(s?.duration || 0)}`;
    const head = document.getElementById('scrubHead');
    if (head && s?.duration) head.style.left = `${(player.currentTime / s.duration) * 100}%`;
    const tl = document.getElementById('tlHead'); if (tl) tl.style.display = 'none';
  } else {
    const c = state.clips[seq.idx];
    if (seq.playing && !seq.switching && c) {
      if (player.currentTime >= c.out - 0.03 || player.ended) {
        if (seq.idx + 1 < state.clips.length) seqGoto(seq.idx + 1, 0, true);
        else { seq.playing = false; player.pause(); }
      }
    }
    txt = `${fmt(seqTimeAt(seq.idx, player.currentTime))} / ${fmt(seqDur())}`;
    const tl = document.getElementById('tlHead');
    const clipEl = $('#track').children[seq.idx];
    if (tl && clipEl?.classList.contains('clip') && c) {
      const frac = playerSrc === c.srcId ? clamp((player.currentTime - c.in) / (c.out - c.in), 0, 1) : 0;
      tl.style.display = 'block';
      tl.style.left = `${clipEl.offsetLeft + frac * clipEl.offsetWidth}px`;
    }
  }
  if (txt !== lastTime) { $('#timeDisp').textContent = txt; lastTime = txt; }
}

/* ---------------- Claude ---------------- */
const TEMPLATES = {
  imovel: 'Abra com a fachada ou com o ambiente mais bonito. Siga um percurso lógico (entrada, sala, cozinha, quartos, banheiros, área externa). Planos de 2 a 5 s, com movimentos lentos e estáveis. Descarte tremidos, escuros e repetições do mesmo ambiente. Normalmente o áudio ambiente deve ficar mudo (mute: true), porque a trilha entra depois.',
  vlog: 'Preserve as falas inteiras e a ordem lógica do raciocínio. Corte começos falsos, repetições e pausas longas. Evite planos parados longos sem fala. Como você só vê imagens, prefira trechos completos em vez de cortes no meio da fala.',
  entrevista: 'Preserve respostas completas. Corte perguntas repetidas, pausas e trechos de preparação. Se fizer sentido, abra com o trecho mais forte.',
  gameplay: 'Priorize ação, viradas e reações. Corte carregamentos, menus e tempo morto. Deslocamentos longos podem ser acelerados (speed 1.5 a 2).',
  produto: 'Mostre o produto logo no início. Alterne detalhes em close e planos gerais, com planos curtos de 1,5 a 3 s. Termine com o produto inteiro em destaque.',
  livre: 'Siga o objetivo descrito.',
};
const TEMPLATE_NAMES = { imovel: 'Imóvel', vlog: 'Vlog ou fala para câmera', entrevista: 'Entrevista', gameplay: 'Gameplay', produto: 'Produto', livre: 'Livre' };
function buildPrompt() {
  const data = state.scenes.filter((s) => srcById(s.srcId)).map((s) => {
    const src = srcById(s.srcId);
    const good = src.file ? findWindows(src, Math.min(5, s.end - s.start), 2, [s.start, s.end]).map((w) => [+w.a.toFixed(2), +w.b.toFixed(2)]) : [];
    return {
      id: s.id, arquivo: src.name, inicio: s.start, fim: s.end,
      duracao: +(s.end - s.start).toFixed(2), ...(s.flags?.length ? { alertas: s.flags } : {}),
      ...(good.length ? { trechos_estaveis: good } : {}),
    };
  });
  return [
    'Você é o editor assistente do Bruto, uma ferramenta de edição de vídeo bruto. Escreva as notas em português.',
    'As folhas de contato anexadas mostram cada cena em uma linha: ID, arquivo, intervalo e 3 quadros (início, meio e fim).',
    'Analise as imagens e os dados abaixo e monte a edição.',
    '',
    `Tipo de vídeo: ${TEMPLATE_NAMES[state.template]}`,
    `Objetivo: ${state.briefing || '(não informado)'}`,
    `Duração alvo: cerca de ${state.targetDur} s`,
    `Formato de saída: ${state.settings.aspect}`,
    ...(state.sources.length <= 2 && state.scenes.length > 3 ? ['O material é uma gravação contínua: cada cena é um trecho do percurso, separado pela mudança de ambiente. Cenas vizinhas podem mostrar o mesmo ambiente; escolha só a melhor delas.'] : []),
    '',
    `Diretrizes para este tipo: ${TEMPLATES[state.template]}`,
    '',
    'Cenas (tempos em segundos dentro de cada arquivo):',
    JSON.stringify(data),
    '',
    'Responda SOMENTE com um JSON neste formato, sem texto fora dele:',
    JSON.stringify({
      format: 'bruto-edit', version: 1,
      settings: { aspect: state.settings.aspect, fit: 'contain' },
      clips: [{ scene: 'C004', in: 12.4, out: 16.0, label: 'Fachada', speed: 1, mute: true, note: 'motivo da escolha' }],
    }, null, 1),
    '',
    'Regras: "in" e "out" ficam dentro do intervalo da cena indicada em "scene"; a ordem de "clips" é a ordem do vídeo final; "speed" entre 0.5 e 2;',
    'quando houver "trechos_estaveis", use esses intervalos como "in" e "out" (pode encurtar, nunca estender): eles evitam cortes no meio de movimentos de câmera e trechos tremidos;',
    'cada arquivo costuma ser um ambiente: não repita o mesmo ambiente e não deixe ambientes de fora sem motivo; clipes com no mínimo 2,5 s;',
    'evite cenas com alertas (escuro, desfocado, muito movimento) a não ser que sejam a única opção; use "note" para explicar escolhas que o editor deve revisar;',
    '"fit" pode ser "contain" (barras) ou "cover" (preenche cortando).',
  ].join('\n');
}
function loadImg(src) { return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; }); }
async function renderSheets(onProgress = () => {}) {
  const scenes = state.scenes.filter((s) => srcById(s.srcId));
  for (const [i, sc] of scenes.entries()) { onProgress(`Preparando quadros ${i + 1}/${scenes.length}`); await ensureSceneThumbs(sc); }
  const per = 10, W = 1000, rowH = 150, top = 56;
  const pages = Math.ceil(scenes.length / per);
  const out = [];
  for (let p = 0; p < pages; p++) {
      const chunk = scenes.slice(p * per, p * per + per);
      const c = document.createElement('canvas');
      c.width = W; c.height = top + chunk.length * rowH + 10;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
      ctx.fillStyle = '#111'; ctx.font = '600 20px system-ui, sans-serif';
      ctx.fillText(`${state.name}: folha de contato ${p + 1} de ${pages}`, 16, 34);
      for (const [r, sc] of chunk.entries()) {
        const y = top + r * rowH;
        ctx.fillStyle = r % 2 ? '#f3f3f3' : '#fff'; ctx.fillRect(0, y, W, rowH);
        ctx.fillStyle = '#111'; ctx.font = '700 26px system-ui, sans-serif'; ctx.fillText(sc.id, 16, y + 38);
        ctx.font = '14px system-ui, sans-serif'; ctx.fillStyle = '#333';
        let name = srcById(sc.srcId).name;
        while (ctx.measureText(name).width > 220 && name.length > 4) name = name.slice(0, -2);
        ctx.fillText(name, 16, y + 62);
        ctx.fillText(`${sc.start.toFixed(1)}s a ${sc.end.toFixed(1)}s (${(sc.end - sc.start).toFixed(1)}s)`, 16, y + 84);
        if (sc.flags?.length) { ctx.fillStyle = '#c0392b'; ctx.fillText(sc.flags.join(', '), 16, y + 106); }
        for (const [k, src] of (sc.thumbs || []).entries()) {
          try { const img = await loadImg(src); ctx.drawImage(img, 250 + k * 250, y + 7, 240, 135); } catch { /* quadro ausente */ }
        }
      }
    out.push(c.toDataURL('image/jpeg', 0.85));
  }
  return out;
}
async function exportSheets() {
  if (!state.scenes.some((s) => srcById(s.srcId))) return toast('Analise os vídeos antes de gerar as folhas de contato.', 'warn');
  const btn = $('#btnSheets');
  btn.disabled = true;
  try {
    const sheets = await renderSheets((m) => { btn.textContent = m; });
    for (const [i, u] of sheets.entries()) { download(`${slug(state.name)}-folha-${i + 1}.jpg`, u); await sleep(400); }
    toast(`${sheets.length} folha(s) de contato baixada(s).`);
  } finally {
    btn.disabled = false; btn.textContent = 'Baixar folhas de contato';
  }
}
function parseLooseJSON(text) {
  let t = String(text).replace(/```(?:json)?/gi, '').trim();
  const a = t.indexOf('{'), b = t.lastIndexOf('}');
  if (a < 0 || b < a) throw new Error('Não encontrei um JSON no texto colado.');
  return JSON.parse(t.slice(a, b + 1));
}
function applyEdit(obj) {
  if (obj?.format === 'bruto-projeto') { loadProject(obj); toast('Projeto aberto.'); return true; }
  if (!Array.isArray(obj?.clips)) throw new Error('O JSON não tem a lista "clips".');
  let skipped = 0;
  const clips = [];
  for (const c of obj.clips) {
    const sc = c.scene ? sceneById(c.scene) : null;
    const src = sc ? srcById(sc.srcId) : state.sources.find((s) => s.name === c.file || s.name === c.arquivo || s.id === c.srcId);
    if (!src) { skipped++; continue; }
    let a = clamp(+(c.in ?? c.start ?? sc?.start ?? 0), 0, src.duration);
    let b = clamp(+(c.out ?? c.end ?? sc?.end ?? src.duration), 0, src.duration);
    if (!(b - a >= 0.2)) { skipped++; continue; }
    if (!['vlog', 'entrevista'].includes(state.template) && b - a < 2) {
      const m = (a + b) / 2, half = Math.min(1.25, src.duration / 2);
      a = clamp(m - half, 0, src.duration - 2 * half); b = a + 2 * half;
    }
    clips.push({
      id: uid(), srcId: src.id, in: +a.toFixed(2), out: +b.toFixed(2),
      label: String(c.label || c.scene || ''), speed: clamp(+c.speed || 1, 0.5, 4), mute: !!c.mute, note: String(c.note || ''),
    });
  }
  if (!clips.length) throw new Error('Nenhum clipe válido. Confira se as cenas citadas existem neste projeto.');
  checkpoint();
  state.clips = clips;
  if (obj.settings) {
    if (['16:9', '9:16'].includes(obj.settings.aspect)) state.settings.aspect = obj.settings.aspect;
    if (['contain', 'cover'].includes(obj.settings.fit)) state.settings.fit = obj.settings.fit;
  }
  state.selectedClip = null;
  afterEdit(); updateStage();
  toast(`${clips.length} clipe(s) aplicados${skipped ? `, ${skipped} ignorado(s)` : ''}. Use Ctrl+Z para voltar.`, 'info', 5000);
  return true;
}

/* ---------------- montagem automática ---------------- */
const MUTE_BY_DEFAULT = { imovel: true, produto: true };
function sharpMedian(list) {
  const v = list.map((s) => s.sharp).filter((x) => x > 0).sort((a, b) => a - b);
  return v[Math.floor(v.length / 2)] || 1;
}
function srcSamples(srcId) {
  return state.scenes.filter((x) => x.srcId === srcId).flatMap((x) => (x.samples || []).filter((y) => !y.cut)).sort((p, q) => p.t - q.t);
}
function globalSharpMed() {
  const v = state.scenes.flatMap((x) => (x.samples || []).map((y) => y.sh)).filter((y) => y > 0).sort((p, q) => p - q);
  return v[Math.floor(v.length / 2)] || sharpMedian(state.scenes);
}
// Nota de um trecho: nítido, estável (movimento constante, sem trancos), bem exposto,
// e com entrada/saída em momentos de câmera mais calma.
function globalBrightMed() {
  const v = state.scenes.flatMap((x) => (x.samples || []).map((y) => y.b)).filter((y) => y != null).sort((p, q) => p - q);
  return v[Math.floor(v.length / 2)] || 0.5;
}
function windowScore(w, med, bmed = 0.5) {
  const n = w.length;
  const avg = (k, def) => w.reduce((t, x) => t + (x[k] ?? def), 0) / n;
  let jitter = 0;
  for (let i = 1; i < n; i++) jitter += Math.abs(w[i].d - w[i - 1].d);
  jitter /= Math.max(1, n - 1);
  const motion = avg('d', 0), sharp = avg('sh', med) / med, bright = avg('b', 0.5);
  const edge = (w[0].d + w[n - 1].d) / 2;
  return Math.min(sharp, 1.5) - 8 * jitter - 2.5 * Math.max(0, motion - 0.05)
    - 3 * Math.max(0, Math.min(0.22, bmed * 0.7) - bright) - 3 * Math.max(0, bright - 0.85) - 1.5 * edge;
}
function findWindows(src, L, k = 1, range = null, gap = 0) {
  const lo = range ? range[0] : 0, hi = range ? range[1] : src.duration;
  const margin = Math.min(0.6, (hi - lo) * 0.1);
  const t0 = lo + margin, t1 = hi - margin;
  L = Math.max(0.8, Math.min(L, t1 - t0));
  const centered = () => { const m = (t0 + t1) / 2; return [{ a: m - L / 2, b: m + L / 2, score: -9 }]; };
  const smp = srcSamples(src.id).filter((x) => x.t >= lo && x.t <= hi);
  if (smp.length < 3) return centered();
  const med = globalSharpMed(), bmed = globalBrightMed();
  const cands = [];
  let i0 = 0;
  for (let a = t0; a + L <= t1 + 1e-6; a += 0.25) {
    while (i0 < smp.length && smp[i0].t < a - 1e-6) i0++;
    const w = [];
    for (let j = i0; j < smp.length && smp[j].t <= a + L + 1e-6; j++) w.push(smp[j]);
    if (w.length >= 2) cands.push({ a, b: a + L, score: windowScore(w, med, bmed) });
  }
  if (!cands.length) return centered();
  cands.sort((p, q) => q.score - p.score);
  const out = [];
  for (const c of cands) {
    if (out.every((o) => c.b + gap <= o.a || c.a >= o.b + gap)) out.push(c);
    if (out.length >= k) break;
  }
  return out.sort((p, q) => p.a - q.a);
}
const baseName = (n) => n.replace(/\.[^.]+$/, '');
function sceneAt(srcId, t) { return state.scenes.find((x) => x.srcId === srcId && t >= x.start && t < x.end); }
function autoBuild() {
  const usable = state.sources.filter((s) => s.file && !s.unsupported && s.analyzed);
  if (!usable.length) { toast('Adicione os vídeos e aguarde a análise antes de montar.', 'warn'); return false; }
  if (usable.some((s) => !srcSamples(s.id).length)) toast('Alguns vídeos foram analisados na versão anterior. Use "Reanalisar tudo" para cortes melhores.', 'warn', 6000);
  const tpl = state.template;
  const target = Math.max(5, +state.targetDur || 60);
  const mute = !!MUTE_BY_DEFAULT[tpl];
  const MIN = tpl === 'produto' ? 2 : 2.5, MAX = tpl === 'gameplay' ? 8 : 6;
  let picks = [];
  let dropped = 0;

  if (tpl === 'vlog' || tpl === 'entrevista') {
    // fala: mantém cada arquivo/cena inteiro, cortando só as pontas
    for (const s of usable) for (const sc of state.scenes.filter((x) => x.srcId === s.id)) picks.push({ src: s, a: sc.start + 0.2, b: sc.end - 0.2 });
    let total = picks.reduce((t, p) => t + p.b - p.a, 0);
    while (picks.length > 1 && total > target * 1.15) {
      const worst = picks.reduce((w, p) => (p.b - p.a < w.b - w.a ? p : w));
      picks = picks.filter((p) => p !== worst); total -= worst.b - worst.a; dropped++;
    }
  } else {
    // cada cena é um ambiente: um arquivo curto ou um trecho de uma gravação contínua
    const units = state.scenes.filter((sc) => usable.some((u) => u.id === sc.srcId)).map((sc) => {
      const src = srcById(sc.srcId), len = sc.end - sc.start;
      const w = findWindows(src, Math.min(MAX, len), 1, [sc.start, sc.end])[0];
      return { sc, src, len, w, rank: w.score + 0.15 * Math.log(1 + len) };
    }).filter((u) => u.len >= 1.2);
    let chosen = units;
    const fit = Math.max(1, Math.floor(target / MIN));
    if (chosen.length > fit) {
      const keep = new Set([chosen[0], ...chosen.slice(1).sort((p, q) => q.rank - p.rank).slice(0, fit - 1)]);
      dropped = chosen.length - keep.size;
      chosen = chosen.filter((u) => keep.has(u));
    }
    const L = clamp(target / chosen.length, MIN, MAX);
    // se sobrar tempo (poucos ambientes longos), tira mais de um trecho dos ambientes mais longos, sem colar trechos vizinhos
    let extra = Math.max(0, Math.round(target / L) - chosen.length);
    const extraOf = new Map();
    const byLen = [...chosen].sort((p, q) => q.len - p.len);
    for (let gave = true; extra > 0 && gave;) {
      gave = false;
      for (const u of byLen) {
        if (extra <= 0) break;
        const can = Math.max(0, Math.floor(u.len / (L * 2.5)) - 1);
        if ((extraOf.get(u) || 0) < can) { extraOf.set(u, (extraOf.get(u) || 0) + 1); extra--; gave = true; }
      }
    }
    for (const u of chosen) {
      const range = [u.sc.start, u.sc.end];
      const k = 1 + (extraOf.get(u) || 0);
      if (k > 1) {
        for (const w of findWindows(u.src, L, k, range, L)) picks.push({ src: u.src, a: w.a, b: w.b, sc: u.sc });
        continue;
      }
      const tries = [...new Set([L, L * 0.8, L * 0.65, MIN].map((x) => +Math.max(MIN, Math.min(x, u.len - 0.4)).toFixed(2)))];
      const opts = tries.map((len) => findWindows(u.src, len, 1, range)[0]);
      const top = Math.max(...opts.map((o) => o.score));
      const w = opts.find((o) => o.score >= top - 0.12) || opts[0];
      picks.push({ src: u.src, a: w.a, b: w.b, sc: u.sc });
    }
  }
  const clips = picks.filter((p) => p.b - p.a >= 0.8).map((p) => ({
    id: uid(), srcId: p.src.id, in: +Math.max(0, p.a).toFixed(2), out: +Math.min(p.src.duration, p.b).toFixed(2),
    label: usable.length >= 3 ? baseName(p.src.name) : (p.sc?.id || sceneAt(p.src.id, p.a)?.id || baseName(p.src.name)), speed: 1, mute, note: '',
  }));
  if (!clips.length) { toast('Não encontrei trechos aproveitáveis. Tente reanalisar.', 'warn'); return false; }
  checkpoint();
  state.clips = clips; state.selectedClip = null;
  afterEdit(); updateStage();
  toast(`Montagem com ${clips.length} clipes (${fmt(seqDur())}).${dropped ? ` ${dropped} trecho(s) ficaram de fora para caber na duração; aumente a duração alvo para incluir.` : ''} Ctrl+Z desfaz.`, 'info', 7000);
  return true;
}

/* ---------------- montagem com IA (Gemini) ---------------- */
const GEMINI_KEY = 'bruto:gemini:key';
const GEMINI_MODEL = 'bruto:gemini:model';
const DEFAULT_MODEL = 'gemini-flash-latest';
const FALLBACK_MODELS = ['gemini-flash-latest', 'gemini-3.6-flash', 'gemini-flash-lite-latest'];
const getKey = () => { try { return localStorage.getItem(GEMINI_KEY) || ''; } catch { return ''; } };
const getModel = () => { try { return localStorage.getItem(GEMINI_MODEL) || DEFAULT_MODEL; } catch { return DEFAULT_MODEL; } };
function updateKeyState() {
  const k = getKey();
  $('#keyState').textContent = k ? `Chave salva (termina em ${k.slice(-4)})` : 'Nenhuma chave salva';
  $('#geminiModel').value = getModel() === DEFAULT_MODEL ? '' : getModel();
}
function geminiError(status, body) {
  const msg = body?.error?.message || '';
  if (status === 400 && /api key/i.test(msg)) return 'A chave do Gemini é inválida. Gere outra em aistudio.google.com/apikey.';
  if (status === 403) return 'A chave não tem permissão para usar o Gemini. Confira a chave no AI Studio.';
  if (status === 404) return `Modelo "${getModel()}" não encontrado. Deixe o campo Modelo vazio para usar o padrão, ou informe outro nome.`;
  if (status === 429) return 'Limite gratuito do Gemini atingido. Espere alguns minutos ou use "Montar automaticamente".';
  if (status >= 500) return 'Os servidores do Gemini estão sobrecarregados agora (erro ' + status + '). Tente de novo em alguns minutos ou use "Montar automaticamente".';
  return `O Gemini respondeu com erro ${status}. ${msg}`.trim();
}
async function aiBuild() {
  const key = getKey();
  if (!key) { $('#aiConfig').open = true; $('#geminiKey').focus(); toast('Cadastre a chave gratuita do Gemini para usar a montagem com IA.', 'warn', 5000); return; }
  if (!state.scenes.some((s) => srcById(s.srcId)?.file)) { toast('Adicione os vídeos e aguarde a análise antes de montar.', 'warn'); return; }
  const btn = $('#btnAiBuild'), status = $('#autoStatus');
  btn.classList.add('busy'); $('#btnAutoBuild').disabled = true;
  try {
    const sheets = await renderSheets((m) => { status.textContent = m; });
    status.textContent = 'A IA está analisando as cenas…';
    const parts = [{ text: buildPrompt() }, ...sheets.map((u) => ({ inline_data: { mime_type: 'image/jpeg', data: u.split(',')[1] } }))];
    const payload = JSON.stringify({ contents: [{ role: 'user', parts }], generationConfig: { responseMimeType: 'application/json', temperature: 0.4 } });
    const models = [...new Set([getModel(), ...FALLBACK_MODELS])];
    let res = null, body = {}, lastStatus = 0;
    outer: for (const model of models) {
      for (let attempt = 0; attempt < 3; attempt++) {
        if (attempt) { status.textContent = `IA ocupada, tentando de novo (${model})…`; await sleep(attempt * 4000); }
        res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key }, body: payload,
        });
        body = await res.json().catch(() => ({}));
        lastStatus = res.status;
        if (res.ok) break outer;
        if (res.status === 404) break;                       // modelo inexistente: tenta o próximo
        if (res.status !== 503 && res.status !== 500 && res.status !== 429) throw new Error(geminiError(res.status, body));
        if (res.status === 429) break;                       // cota do modelo: tenta outro modelo
      }
      status.textContent = 'Trocando para outro modelo da IA…';
    }
    if (!res?.ok) throw new Error(geminiError(lastStatus, body));
    const text = (body.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('');
    if (!text) throw new Error('A IA não devolveu uma edição. Tente de novo.');
    applyEdit(parseLooseJSON(text));
    status.textContent = '';
    $('#dlgClaude').close();
  } catch (e) {
    status.textContent = `Erro: ${e.message || 'falha ao falar com o Gemini.'}`;
    toast(e.message || 'Falha ao falar com o Gemini.', 'warn', 8000);
  } finally {
    btn.classList.remove('busy'); $('#btnAutoBuild').disabled = false;
  }
}

/* ---------------- exportação ---------------- */
function outDims() {
  const r = +state.settings.res;
  const long = Math.round((r * 16) / 9 / 2) * 2;
  return state.settings.aspect === '9:16' ? { w: r, h: long } : { w: long, h: r };
}
function vfFor(c) {
  const { w, h: hh } = outDims();
  const fit = state.settings.fit === 'cover'
    ? `scale=${w}:${hh}:force_original_aspect_ratio=increase,crop=${w}:${hh}`
    : `scale=${w}:${hh}:force_original_aspect_ratio=decrease,pad=${w}:${hh}:(ow-iw)/2:(oh-ih)/2:color=black`;
  return `${fit},setsar=1,setpts=(PTS-STARTPTS)/${c.speed},fps=${state.settings.fps},format=yuv420p`;
}
function atempoChain(sp) {
  const parts = [];
  while (sp > 2.0001) { parts.push('atempo=2'); sp /= 2; }
  while (sp < 0.4999) { parts.push('atempo=0.5'); sp /= 0.5; }
  parts.push(`atempo=${+sp.toFixed(4)}`);
  return parts.join(',');
}
function clipArgs(c, inPath, outName, opt = {}) {
  const set = state.settings;
  const s = srcById(c.srcId);
  const len = c.out - c.in;
  const outLen = (len / c.speed).toFixed(3);
  const args = ['-ss', c.in.toFixed(3), '-t', len.toFixed(3), '-i', inPath];
  const audioOn = set.audio && !opt.noAudio;
  const silence = audioOn && s.hasAudio === false;
  if (silence) args.push('-f', 'lavfi', '-t', outLen, '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000');
  args.push('-vf', vfFor(c));
  if (audioOn) {
    if (silence) args.push('-map', '0:v:0', '-map', '1:a:0');
    else {
      const af = [];
      if (c.speed !== 1) af.push(atempoChain(c.speed));
      if (c.mute) af.push('volume=0');
      af.push('aresample=48000');
      args.push('-map', '0:v:0', '-map', '0:a:0', '-af', af.join(','));
    }
    args.push('-c:a', 'aac', '-b:a', '160k', '-ac', '2', '-ar', '48000');
  } else args.push('-an');
  const draft = set.quality === 'draft';
  const q = opt.local ? { preset: draft ? 'veryfast' : 'medium', crf: draft ? 26 : 20 } : { preset: draft ? 'ultrafast' : 'veryfast', crf: draft ? 28 : 21 };
  args.push('-c:v', 'libx264', '-preset', q.preset, '-crf', String(q.crf), '-t', outLen, outName);
  return args;
}

let ff = null, ffLoading = null, job = null;
async function toBlobURL(url, type, onProgress) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Falha ao baixar ${url}`);
  const total = +res.headers.get('content-length') || 0;
  const reader = res.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length;
    if (total && onProgress) onProgress(got / total);
  }
  return URL.createObjectURL(new Blob(chunks, { type }));
}
function getFF(onStatus) {
  if (ff) return Promise.resolve(ff);
  if (!ffLoading) {
    ffLoading = (async () => {
      const inst = new FFmpeg();
      onStatus('Baixando o motor de render (cerca de 32 MB, só na primeira vez)…');
      const coreURL = await toBlobURL(`${CORE_BASE}/ffmpeg-core.js`, 'text/javascript');
      const wasmURL = await toBlobURL(`${CORE_BASE}/ffmpeg-core.wasm`, 'application/wasm', (p) => onStatus(`Baixando o motor de render… ${Math.round(p * 100)}%`));
      onStatus('Iniciando o motor de render…');
      await inst.load({ coreURL, wasmURL });
      ff = inst;
      return inst;
    })().catch((e) => { ffLoading = null; throw e; });
  }
  return ffLoading;
}
const ui = {
  start() {
    $('#expProgress').hidden = false; $('#expResult').replaceChildren();
    $('#btnRender').disabled = true; $('#btnCancel').hidden = false;
    $('#expLog').textContent = ''; this.progress(0);
  },
  status(t) { $('#expStatus').textContent = t; },
  progress(p) { $('#expBar').style.width = `${clamp(p, 0, 1) * 100}%`; },
  log(line) {
    const el = $('#expLog');
    el.textContent = (el.textContent + '\n' + line).split('\n').slice(-14).join('\n');
    el.scrollTop = el.scrollHeight;
  },
  end() { $('#btnRender').disabled = false; $('#btnCancel').hidden = true; },
  done(url, name, size) {
    this.end(); this.progress(1); this.status('Pronto.');
    $('#expResult').replaceChildren(h('div', { class: 'result' },
      h('span', {}, `${name} (${(size / 1048576).toFixed(1)} MB)`),
      h('a', { class: 'btn primary', href: url, download: name }, 'Baixar MP4')));
  },
  error(msg) {
    this.end();
    $('#expResult').replaceChildren(h('div', { class: 'result error' }, msg));
  },
};
async function exportMP4() {
  if (!state.clips.length) return toast('A timeline está vazia.', 'warn');
  const usedIds = [...new Set(state.clips.map((c) => c.srcId))];
  const used = usedIds.map(srcById);
  const missing = used.filter((s) => !s?.file);
  if (missing.length) return toast(`Faltam arquivos: ${missing.map((s) => s?.name).join(', ')}. Adicione-os de novo.`, 'warn', 6000);
  if (+state.settings.res > 1080 && !confirm('4K no navegador é muito lento e pode estourar a memória. Prefira o script de render local. Continuar mesmo assim?')) return;
  stopSeq();
  ui.start();
  job = { cancelled: false };
  const logs = [];
  const onLog = ({ message }) => { logs.push(message); if (logs.length > 400) logs.shift(); ui.log(message); };
  let inst = null, mounted = false;
  const created = [];
  try {
    inst = await getFF((m) => ui.status(m));
    inst.on('log', onLog);
    const pathOf = {};
    const files = used.map((s, i) => {
      const name = `${i}_${s.name.replace(/[^\w.-]+/g, '_')}`;
      pathOf[s.id] = `/in/${name}`;
      return new File([s.file], name, { type: s.file.type });
    });
    try { await inst.createDir('/in'); } catch { /* já existe */ }
    try { mounted = await inst.mount('WORKERFS', { files }, '/in'); } catch { mounted = false; }
    if (!mounted) {
      ui.status('Copiando os arquivos para a memória do navegador…');
      for (const [i, f] of files.entries()) {
        pathOf[used[i].id] = f.name;
        await inst.writeFile(f.name, new Uint8Array(await f.arrayBuffer()));
        created.push(f.name);
      }
    }
    let noAudio = false;
    if (state.settings.audio) {
      ui.status('Verificando o áudio dos arquivos…');
      for (const s of used) {
        if (s.hasAudio !== undefined) continue;
        const before = logs.length;
        await inst.exec(['-hide_banner', '-i', pathOf[s.id]]);
        s.hasAudio = logs.slice(before).some((l) => /Stream #.*Audio:/.test(l));
      }
      if (used.some((s) => s.hasAudio === false)) {
        const ok = await inst.exec(['-f', 'lavfi', '-t', '0.2', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000', '-c:a', 'aac', 'teste.m4a']);
        if (ok !== 0) { noAudio = true; toast('Algum arquivo não tem áudio; o vídeo será exportado sem som.', 'warn', 6000); }
        else { try { await inst.deleteFile('teste.m4a'); } catch { /* ignora */ } }
      }
      persist();
    }
    const total = seqDur() || 1;
    let done = 0;
    const outs = [];
    for (const [i, c] of state.clips.entries()) {
      if (job.cancelled) throw new Error('cancelado');
      const name = `c${pad(i, 3)}.mp4`;
      ui.status(`Renderizando clipe ${i + 1} de ${state.clips.length}…`);
      const d = clipDur(c);
      const onProg = ({ progress }) => { if (progress >= 0 && progress <= 1) ui.progress((done + progress * d) / total * 0.97); };
      inst.on('progress', onProg);
      const ret = await inst.exec(clipArgs(c, pathOf[c.srcId], name, { noAudio }));
      inst.off('progress', onProg);
      if (ret !== 0) throw new Error(`Falha ao renderizar o clipe ${i + 1}.\n${logs.slice(-8).join('\n')}`);
      outs.push(name); created.push(name);
      done += d; ui.progress(done / total * 0.97);
    }
    ui.status('Juntando os clipes…');
    await inst.writeFile('lista.txt', outs.map((n) => `file '${n}'`).join('\n'));
    created.push('lista.txt');
    const ret = await inst.exec(['-f', 'concat', '-safe', '0', '-i', 'lista.txt', '-c', 'copy', '-movflags', '+faststart', 'final.mp4']);
    if (ret !== 0) throw new Error(`Falha ao juntar os clipes.\n${logs.slice(-8).join('\n')}`);
    created.push('final.mp4');
    const data = await inst.readFile('final.mp4');
    const blob = new Blob([data], { type: 'video/mp4' });
    ui.done(URL.createObjectURL(blob), `${slug(state.name)}.mp4`, blob.size);
  } catch (e) {
    if (job?.cancelled) { ui.end(); ui.status('Render cancelado.'); }
    else { ui.error(e?.message || String(e)); }
  } finally {
    if (ff && inst === ff) {
      inst.off('log', onLog);
      for (const n of created) { try { await inst.deleteFile(n); } catch { /* ignora */ } }
      if (mounted) { try { await inst.unmount('/in'); } catch { /* ignora */ } }
    }
    job = null;
  }
}
function cancelExport() {
  if (!job) return;
  job.cancelled = true;
  if (ff) { ff.terminate(); ff = null; ffLoading = null; }
}

function localScript(kind) {
  if (!state.clips.length) return toast('A timeline está vazia.', 'warn');
  const win = kind === 'bat';
  const sep = win ? '\\' : '/';
  const quote = (a) => (/^[\w.:+-]+$/.test(a) ? a : `"${a}"`);
  const L = [];
  if (win) L.push('@echo off', 'chcp 65001 >nul', 'REM Gerado pelo Bruto. Coloque este arquivo na mesma pasta dos videos e de dois cliques.', 'REM Requer o ffmpeg instalado e no PATH (https://ffmpeg.org).', 'if not exist bruto_tmp mkdir bruto_tmp', 'del /q bruto_tmp\\lista.txt 2>nul');
  else L.push('#!/usr/bin/env bash', '# Gerado pelo Bruto. Coloque na pasta dos videos e rode: bash render.sh', '# Requer o ffmpeg instalado (https://ffmpeg.org).', 'set -e', 'cd "$(dirname "$0")"', 'mkdir -p bruto_tmp', 'rm -f bruto_tmp/lista.txt');
  state.clips.forEach((c, i) => {
    const s = srcById(c.srcId);
    const name = `c${pad(i, 3)}.mp4`;
    const args = clipArgs(c, s.name, `bruto_tmp${sep}${name}`, { local: true });
    L.push(['ffmpeg -y -hide_banner -loglevel warning -stats', ...args.map(quote)].join(' '));
    if (win) L.push('if errorlevel 1 goto erro');
    L.push(win ? `echo file '${name}'>> bruto_tmp\\lista.txt` : `echo "file '${name}'" >> bruto_tmp/lista.txt`);
  });
  const out = `${slug(state.name)}.mp4`;
  L.push(`ffmpeg -y -hide_banner -loglevel warning -f concat -safe 0 -i bruto_tmp${sep}lista.txt -c copy -movflags +faststart ${quote(out)}`);
  if (win) L.push('if errorlevel 1 goto erro', `echo Pronto: ${out}`, 'pause', 'goto :eof', ':erro', 'echo Erro ao renderizar. Veja a mensagem acima.', 'pause');
  else L.push(`echo "Pronto: ${out}"`);
  download(`render-${slug(state.name)}.${kind}`, L.join(win ? '\r\n' : '\n') + (win ? '\r\n' : '\n'), 'text/plain');
}
function exportEDL() {
  if (!state.clips.length) return toast('A timeline está vazia.', 'warn');
  const fps = +state.settings.fps;
  const tc = (sec) => {
    const f = Math.round(sec * fps);
    return [Math.floor(f / fps / 3600), Math.floor(f / fps / 60) % 60, Math.floor(f / fps) % 60, f % fps].map((n) => pad(n)).join(':');
  };
  const L = [`TITLE: ${state.name}`, 'FCM: NON-DROP FRAME', ''];
  let rec = 3600;
  state.clips.forEach((c, i) => {
    const d = c.out - c.in;
    L.push(`${pad(i + 1, 3)}  AX       AA/V  C        ${tc(c.in)} ${tc(c.out)} ${tc(rec)} ${tc(rec + d)}`);
    L.push(`* FROM CLIP NAME: ${srcById(c.srcId)?.name || c.srcId}`);
    if (c.label) L.push(`* COMMENT: ${c.label}`);
    if (c.speed !== 1) L.push(`* COMMENT: aplicar velocidade ${c.speed}x no editor`);
    L.push('');
    rec += d;
  });
  download(`${slug(state.name)}.edl`, L.join('\r\n'), 'text/plain');
  toast(`EDL gerada a ${fps} fps. Use a mesma taxa na timeline do DaVinci ou Premiere.`, 'info', 5000);
}
function saveProjectFile() {
  const d = new Date();
  download(`${slug(state.name)}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}.json`, JSON.stringify(projectData(), null, 1), 'application/json');
}
function renderExportSettings() {
  for (const sel of $$('#dlgExport [data-setting]')) sel.value = String(state.settings[sel.dataset.setting]);
  const { w, h: hh } = outDims();
  $('#expEstimate').textContent = state.clips.length
    ? `Sequência de ${fmt(seqDur())} em ${w}×${hh}. No navegador, conte alguns minutos de render por minuto de vídeo; mantenha esta aba aberta durante o processo.`
    : 'A timeline está vazia.';
}

/* ---------------- ligações da interface ---------------- */
function renderAll() {
  $('#projName').value = state.name;
  $('#sens').value = state.sensitivity;
  $('#zoom').value = state.zoom;
  renderBin(); renderTimeline(); renderInspector(); renderScrub(); updateStage(); updateUndoButtons();
}
function bind() {
  $('#btnAdd').onclick = () => $('#fileVideos').click();
  $('#fileVideos').onchange = (e) => { addFiles(e.target.files); e.target.value = ''; };
  $('#projName').onchange = (e) => { state.name = e.target.value.trim() || 'Projeto sem nome'; persist(); };
  $('#btnUndo').onclick = undo;
  $('#btnRedo').onclick = redo;

  const closeMenu = () => $('#projMenu').removeAttribute('open');
  $('#btnSaveProj').onclick = () => { closeMenu(); saveProjectFile(); };
  $('#btnSaveProj2').onclick = saveProjectFile;
  $('#btnOpenProj').onclick = () => { closeMenu(); $('#fileProject').click(); };
  $('#btnLoadEditFile').onclick = () => $('#fileProject').click();
  $('#btnNewProj').onclick = () => { closeMenu(); if (confirm('Começar um projeto novo? A timeline atual será descartada (salve antes se precisar).')) newProject(); };
  document.addEventListener('click', (e) => { if (!e.target.closest('#projMenu')) closeMenu(); });
  $('#fileProject').onchange = async (e) => {
    const f = e.target.files[0]; e.target.value = '';
    if (!f) return;
    try { applyEdit(JSON.parse(await f.text())); $('#dlgClaude').close(); } catch (err) { toast(err.message, 'warn', 6000); }
  };

  $('#sens').oninput = (e) => { state.sensitivity = +e.target.value; };
  $('#btnReanalyze').onclick = () => (state.sources.length ? analyzeAll(true) : toast('Adicione vídeos primeiro.', 'warn'));
  $('#btnDraft').onclick = buildDraft;
  $('#zoom').oninput = (e) => { state.zoom = +e.target.value; renderTimeline(); };

  $$('.mode-tab').forEach((b) => { b.onclick = () => setMode(b.dataset.mode); });
  $('#btnPlay').onclick = togglePlay;
  $('#btnIn').onclick = markIn;
  $('#btnOut').onclick = markOut;
  $('#btnAddRange').onclick = addMarked;
  player.addEventListener('click', togglePlay);

  const scrub = $('#scrub');
  let scrubbing = false;
  scrub.addEventListener('pointerdown', (e) => { scrubbing = true; scrub.setPointerCapture(e.pointerId); player.pause(); scrubTo(e); });
  scrub.addEventListener('pointermove', (e) => { if (scrubbing) scrubTo(e); });
  scrub.addEventListener('pointerup', () => { scrubbing = false; });

  // Claude
  $('#btnClaude').onclick = () => {
    $('#tplSelect').value = state.template; $('#targetDur').value = state.targetDur; $('#briefing').value = state.briefing;
    $('#autoAspect').value = state.settings.aspect;
    $('#promptPreview').value = buildPrompt();
    updateKeyState();
    $('#dlgClaude').showModal();
  };
  $('#autoAspect').onchange = (e) => { state.settings.aspect = e.target.value; updateStage(); persist(); $('#promptPreview').value = buildPrompt(); };
  $('#btnAutoBuild').onclick = () => { syncBrief(); if (autoBuild()) $('#dlgClaude').close(); };
  $('#btnAiBuild').onclick = () => { syncBrief(); aiBuild(); };
  $('#btnSaveKey').onclick = () => {
    const k = $('#geminiKey').value.trim(), m = $('#geminiModel').value.trim();
    try {
      if (k) localStorage.setItem(GEMINI_KEY, k);
      if (m) localStorage.setItem(GEMINI_MODEL, m); else localStorage.removeItem(GEMINI_MODEL);
    } catch { toast('O navegador bloqueou o armazenamento local.', 'warn'); return; }
    $('#geminiKey').value = ''; updateKeyState(); toast('Configuração da IA salva neste navegador.');
  };
  $('#btnClearKey').onclick = () => { try { localStorage.removeItem(GEMINI_KEY); } catch { /* ignora */ } updateKeyState(); toast('Chave removida.'); };
  function syncBrief() {
    state.template = $('#tplSelect').value; state.targetDur = +$('#targetDur').value || 60; state.briefing = $('#briefing').value;
    $('#promptPreview').value = buildPrompt(); persist();
  }
  ['#tplSelect', '#targetDur', '#briefing'].forEach((s) => $(s).addEventListener('input', syncBrief));
  $('#btnSheets').onclick = exportSheets;
  $('#btnCopyPrompt').onclick = async () => {
    if (!state.scenes.length) return toast('Analise os vídeos antes de copiar o prompt.', 'warn');
    syncBrief();
    try { await navigator.clipboard.writeText($('#promptPreview').value); toast('Prompt copiado. Cole no Claude junto com as folhas de contato.'); }
    catch { $('.prompt-preview').open = true; $('#promptPreview').select(); toast('Copie o prompt manualmente (Ctrl+C).', 'warn'); }
  };
  $('#btnApplyEdit').onclick = () => {
    try { applyEdit(parseLooseJSON($('#editJson').value)); $('#editJson').value = ''; $('#dlgClaude').close(); }
    catch (err) { toast(err.message, 'warn', 6000); }
  };

  // Exportar
  $('#btnExport').onclick = () => { renderExportSettings(); $('#dlgExport').showModal(); };
  for (const sel of $$('#dlgExport [data-setting]')) {
    sel.onchange = () => {
      const k = sel.dataset.setting;
      let v = sel.value;
      if (k === 'res' || k === 'fps') v = +v;
      if (k === 'audio') v = v === 'true';
      state.settings[k] = v;
      renderExportSettings(); updateStage(); persist();
    };
  }
  $('#btnRender').onclick = exportMP4;
  $('#btnCancel').onclick = cancelExport;
  $('#btnBat').onclick = () => localScript('bat');
  $('#btnSh').onclick = () => localScript('sh');
  $('#btnEdl').onclick = exportEDL;
  $('#dlgExport').addEventListener('cancel', (e) => { if (job) e.preventDefault(); });

  // Atalhos
  document.addEventListener('keydown', (e) => {
    if (document.querySelector('dialog[open]')) return;
    const tag = e.target.tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || e.target.isContentEditable) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === 'y') { e.preventDefault(); redo(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (k === ' ' && tag === 'BUTTON') return;
    switch (k) {
      case ' ': e.preventDefault(); togglePlay(); break;
      case 'i': markIn(); break;
      case 'o': markOut(); break;
      case 'a': addMarked(); break;
      case 's': splitAtPlayhead(); break;
      case 'delete': case 'backspace': { const c = state.clips.find((x) => x.id === state.selectedClip); if (c) { e.preventDefault(); removeClip(c); } break; }
      case 'arrowleft': e.preventDefault(); stepFrame(-1, e.shiftKey); break;
      case 'arrowright': e.preventDefault(); stepFrame(1, e.shiftKey); break;
      default: break;
    }
  });

  // Arrastar arquivos para a janela
  const overlay = $('#dropOverlay');
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  window.addEventListener('dragenter', (e) => { if (hasFiles(e)) { e.preventDefault(); overlay.hidden = false; } });
  overlay.addEventListener('dragover', (e) => e.preventDefault());
  overlay.addEventListener('dragleave', (e) => { if (e.target === overlay) overlay.hidden = true; });
  overlay.addEventListener('drop', (e) => { e.preventDefault(); overlay.hidden = true; addFiles(e.dataTransfer.files); });

  window.addEventListener('beforeunload', (e) => { if (job) { e.preventDefault(); e.returnValue = ''; } });
}

function init() {
  bind();
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      if (p.clips?.length || p.sources?.length) {
        loadProject(p);
        toast('Projeto anterior restaurado. Adicione os mesmos vídeos para reconectar os arquivos.', 'info', 7000);
      }
    }
  } catch (e) { console.warn(e); }
  renderAll();
  setMode('source');
  tick();
}
init();
