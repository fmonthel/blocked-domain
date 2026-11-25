// Vidéothèque - application monopage (aucune dépendance, aucune compilation).
// Catalogue : library.json (régénéré chaque nuit par vt-sync.py). Progression : localStorage de l'appareil.
// Routes : #/  #/c/<chaîne>  #/v/<vidéo>  #/s/<recherche>  #/history
'use strict';

const app = document.getElementById('app');
const qInput = document.getElementById('q');
let LIB = null, BY_ID = new Map(), CH = new Map(), BY_CH = new Map();
const PAGE = 48;

// ---------- stockage local (progression, historique, préférences) ----------
const store = {
  get(k, d) { try { const v = localStorage.getItem('vt.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('vt.' + k, JSON.stringify(v)); } catch { /* stockage indisponible */ } },
};
// progress = { id: { t: seconds, d: duration, at: timestamp, done: bool } }
const progress = () => store.get('progress', {});
function saveProgress(id, t, d) {
  const p = progress();
  const done = d > 0 && (t / d > 0.92 || d - t < 20);
  p[id] = { t: done ? 0 : Math.floor(t), d: Math.floor(d), at: Date.now(), done: done || (p[id] && p[id].done) || false };
  const ids = Object.keys(p);
  if (ids.length > 3000) ids.sort((a, b) => p[a].at - p[b].at).slice(0, ids.length - 3000).forEach(k => delete p[k]);
  store.set('progress', p);
}

// ---------- outils ----------
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
function fmtDur(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${x}` : `${m}:${x}`;
}
const toDate = d => d && d.length === 8 ? new Date(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8)) : null;
function ago(d) {
  const dt = toDate(d); if (!dt) return '';
  const days = Math.floor((Date.now() - dt) / 86400000);
  const plural = (n, w) => `${n} ${w}${n > 1 ? 's' : ''} ago`;
  if (days < 1) return 'today';
  if (days < 7) return plural(days, 'day');
  if (days < 31) return plural(Math.floor(days / 7), 'week');
  if (days < 365) return plural(Math.floor(days / 30), 'month');
  return plural(Math.floor(days / 365), 'year');
}
const longDate = d => { const dt = toDate(d); return dt ? dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }) : ''; };
const avatar = c => c && c.avatar ? `<img src="${esc(c.avatar)}" alt="" loading="lazy">` : '';
const plural = (n, w) => `${n.toLocaleString('en-US')} ${w}${n === 1 ? '' : 's'}`;

// ---------- cartes ----------
// vidéo d'une chaîne « à la demande » pas encore copiée : téléchargée au premier visionnage
const onDemand = v => !v.f ? '<span class="od" title="Downloaded the first time you play it (about a minute)">On demand</span>' : '';
const join = (...parts) => parts.filter(Boolean).join(' • ');
function card(v, opts = {}) {
  const c = CH.get(v.c), p = progress()[v.id];
  const pct = p && !p.done && p.d ? Math.min(100, Math.round(p.t / p.d * 100)) : 0;
  return `<a class="card" href="#/v/${esc(v.id)}">
    <div class="thumb">${v.th ? `<img src="${esc(v.th)}" alt="" loading="lazy">` : ''}
      ${p && p.done ? '<span class="seen">Watched</span>' : ''}${onDemand(v)}
      <span class="dur">${fmtDur(v.l)}</span>${pct ? `<span class="prog"><i style="width:${pct}%"></i></span>` : ''}</div>
    <div class="meta">${opts.noAvatar ? '' : `<div class="av">${avatar(c)}</div>`}
      <div><h3 title="${esc(v.t)}">${esc(v.t)}</h3><p>${join(opts.noChannel ? '' : esc(c ? c.name : ''), ago(v.d))}</p></div></div>
  </a>`;
}
function mini(v, current) {
  const p = progress()[v.id];
  const pct = p && !p.done && p.d ? Math.min(100, Math.round(p.t / p.d * 100)) : 0;
  return `<a class="mini${current ? ' current' : ''}" href="#/v/${esc(v.id)}">
    <div class="thumb">${v.th ? `<img src="${esc(v.th)}" alt="" loading="lazy">` : ''}<span class="dur">${fmtDur(v.l)}</span>
      ${pct ? `<span class="prog"><i style="width:${pct}%"></i></span>` : ''}${p && p.done ? '<span class="seen">Watched</span>' : ''}${onDemand(v)}</div>
    <div><h3>${esc(v.t)}</h3><p>${esc((CH.get(v.c) || {}).name || '')}${v.d ? '<br>' + ago(v.d) : ''}</p></div></a>`;
}

// grille avec « Show more » (et chargement auto en bas de page)
function pagedGrid(list, opts = {}) {
  let shown = 0;
  const wrap = document.createElement('div');
  wrap.innerHTML = '<div class="grid"></div><button class="loadmore" type="button">Show more</button>';
  const grid = wrap.firstChild, btn = wrap.lastChild;
  const more = () => {
    grid.insertAdjacentHTML('beforeend', list.slice(shown, shown + PAGE).map(v => card(v, opts)).join(''));
    shown += PAGE;
    btn.hidden = shown >= list.length;
  };
  btn.onclick = more;
  if ('IntersectionObserver' in window) new IntersectionObserver(e => { if (e[0].isIntersecting && !btn.hidden) more(); }, { rootMargin: '600px' }).observe(btn);
  if (!list.length) wrap.innerHTML = `<div class="empty">${opts.empty || 'No videos.'}</div>`; else more();
  return wrap;
}

// ---------- pages ----------
function home() {
  const p = progress();
  const cont = Object.entries(p).filter(([id, x]) => !x.done && x.t > 10 && BY_ID.has(id))
    .sort((a, b) => b[1].at - a[1].at).slice(0, 12).map(([id]) => BY_ID.get(id));
  let html = '';
  if (cont.length) html += section('Continue watching', cont, '#/history');
  const chans = LIB.channels.filter(c => (BY_CH.get(c.id) || []).length);
  html += `<section class="section"><div class="section-head"><h2>Channels</h2></div>
    <div class="channels">${chans.map(channelCard).join('')}</div></section>`;
  if (!chans.length) html = '<div class="empty">No videos yet: the library is filled every night.</div>';
  app.innerHTML = html;
}
// carte de chaîne de l'accueil : bannière, avatar, nom, nombre de vidéos, pastille « nouveau » (< 7 jours)
function channelCard(c) {
  const vids = BY_CH.get(c.id) || [], p = progress();
  const weekAgo = Date.now() - 7 * 86400000;
  const fresh = vids.filter(v => { const d = toDate(v.d); return d && d >= weekAgo && !(p[v.id] && p[v.id].done); }).length;
  const seen = vids.filter(v => p[v.id] && p[v.id].done).length;
  const last = vids[0];
  return `<a class="chan-card" href="#/c/${encodeURIComponent(c.id)}">
    <div class="chan-banner"${c.banner ? ` style="background-image:url('${esc(c.banner)}')"` : ''}>
      ${fresh ? `<span class="badge">${fresh} new</span>` : ''}</div>
    <div class="chan-body">${avatar(c)}<div>
      <h3>${esc(c.name)}</h3>
      <p>${join(plural(vids.length, 'video'), seen ? `${seen} watched` : '', c.mode === 'demand' ? 'on demand' : '')}</p>
      ${last && last.d ? `<p>Latest: ${ago(last.d)}</p>` : ''}</div></div></a>`;
}
function section(title, vids, moreHref, c) {
  return `<section class="section"><div class="section-head">${c ? `<a href="${moreHref}">${avatar(c)}</a>` : ''}
    <h2>${c ? `<a href="${moreHref}">${title}</a>` : title}</h2>${moreHref ? `<a class="more" href="${moreHref}">${c ? 'All videos' : 'See all'}</a>` : ''}</div>
    <div class="row">${vids.map(v => card(v, c ? { noChannel: true, noAvatar: true } : {})).join('')}</div></section>`;
}

function channel(id) {
  const c = CH.get(id);
  if (!c) return notFound();
  const all = BY_CH.get(id) || [];
  const sort = store.get('sort.' + id, 'new');
  app.innerHTML = `${c.banner ? `<div class="banner" style="background-image:url('${esc(c.banner)}')"></div>` : ''}
    <div class="chan-head">${avatar(c)}<div><h1>${esc(c.name)}</h1>
      <p>${join(esc(c.handle), plural(all.length, 'video'),
        c.mode === 'demand' ? 'videos marked "On demand" are prepared the first time you play them' : (c.complete ? '' : 'copy in progress'))}</p></div></div>
    <div class="toolbar">
      <button class="chip${sort === 'new' ? ' on' : ''}" data-s="new" type="button">Newest</button>
      <button class="chip${sort === 'old' ? ' on' : ''}" data-s="old" type="button">Oldest</button>
      <button class="chip${sort === 'unseen' ? ' on' : ''}" data-s="unseen" type="button">Not watched</button>
      <input type="search" placeholder="Search this channel" aria-label="Search this channel">
    </div><div id="list"></div>`;
  const input = app.querySelector('.toolbar input');
  const render = () => {
    const s = store.get('sort.' + id, 'new'), words = norm(input.value).split(/\s+/).filter(Boolean), p = progress();
    let list = all.filter(v => words.every(w => v._n.includes(w)));
    if (s === 'old') list = list.slice().reverse();
    if (s === 'unseen') list = list.filter(v => !(p[v.id] && p[v.id].done));
    const box = app.querySelector('#list'); box.innerHTML = '';
    box.appendChild(pagedGrid(list, { noChannel: true, noAvatar: true, empty: 'No matching videos.' }));
  };
  app.querySelectorAll('.chip').forEach(b => b.onclick = () => {
    store.set('sort.' + id, b.dataset.s);
    app.querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x === b));
    render();
  });
  let t; input.oninput = () => { clearTimeout(t); t = setTimeout(render, 200); };
  render();
}

function watch(id) {
  const v = BY_ID.get(id);
  if (!v) return notFound();
  const c = CH.get(v.c) || {}, list = BY_CH.get(v.c) || [];
  const i = list.indexOf(v);
  // « Up next » : les vidéos suivantes de la chaîne (plus anciennes = suite du cours), puis les précédentes
  const next = list.slice(i + 1, i + 21).concat(list.slice(Math.max(0, i - 5), i)).slice(0, 20);
  const auto = store.get('autoplay', false);
  app.innerHTML = `<div class="watch"><div>
      <div class="player"><video controls playsinline preload="metadata" ${v.th ? `poster="${esc(v.th)}"` : ''}${v.f ? ` src="${esc(v.f)}"` : ''}></video>
        <div class="prep" id="prep" hidden><div class="prep-box"><div class="spinner"></div>
          <b id="prep-title">Preparing your video…</b><span id="prep-text">It's being copied from YouTube, this takes about a minute.</span>
          <div class="prep-bar"><i id="prep-bar"></i></div><button type="button" id="prep-retry" hidden>Try again</button></div></div></div>
      <h1>${esc(v.t)}</h1>
      <div class="byline"><a href="#/c/${encodeURIComponent(v.c)}">${avatar(c)}</a>
        <div><a href="#/c/${encodeURIComponent(v.c)}"><b>${esc(c.name || '')}</b></a><small>${join(longDate(v.d), fmtDur(v.l))}</small></div>
        <span class="spacer"></span>
        <label class="toggle"><input type="checkbox" id="auto" ${auto ? 'checked' : ''}> Autoplay next</label></div>
      ${v.ch && v.ch.length > 1 ? `<div class="chapters">${v.ch.map(x => `<button type="button" data-t="${x.t}"><b>${fmtDur(x.t)}</b>${esc(x.title)}</button>`).join('')}</div>` : ''}
      ${v.s ? `<div class="desc short" id="desc">${linkTimes(esc(v.s))}</div><button class="desc-more" id="more" type="button">Show more</button>` : ''}
    </div>
    <aside class="side"><h2>Up next</h2>${next.map(x => mini(x)).join('') || '<div class="notice">No other videos in this channel yet.</div>'}</aside></div>`;
  window.scrollTo(0, 0);
  document.title = v.t + ' – Video Library';

  const video = app.querySelector('video');
  const saved = progress()[id];
  video.addEventListener('loadedmetadata', () => {
    if (saved && !saved.done && saved.t > 10 && saved.t < video.duration - 15) video.currentTime = saved.t;
  }, { once: true });
  let last = 0;
  const save = () => { if (video.duration) saveProgress(id, video.currentTime, video.duration); };
  video.addEventListener('timeupdate', () => { if (Date.now() - last > 5000) { last = Date.now(); save(); } });
  video.addEventListener('pause', save);
  video.addEventListener('ended', () => {
    saveProgress(id, video.duration, video.duration);
    if (app.querySelector('#auto').checked && next[0]) location.hash = '#/v/' + next[0].id;
  });
  if (v.f) video.play().catch(() => { /* lecture auto bloquée par le navigateur : on attend un clic */ });
  else prepare(v, video);
  window.addEventListener('hashchange', save, { once: true });

  const seek = t => { video.currentTime = +t; video.play().catch(() => {}); window.scrollTo({ top: 0, behavior: 'smooth' }); };
  app.querySelectorAll('[data-t]').forEach(b => b.onclick = () => seek(b.dataset.t));
  app.querySelector('#auto').onchange = e => store.set('autoplay', e.target.checked);
  const desc = app.querySelector('#desc'), more = app.querySelector('#more');
  if (desc) {
    if (desc.scrollHeight <= desc.clientHeight + 4) more.hidden = true;
    more.onclick = () => { const s = desc.classList.toggle('short'); more.textContent = s ? 'Show more' : 'Show less'; };
  }
}
// chaîne « à la demande » : demande au serveur de copier la vidéo, affiche l'avancement, puis la lance
function prepare(v, video) {
  const box = app.querySelector('#prep'), bar = app.querySelector('#prep-bar'), text = app.querySelector('#prep-text'),
        title = app.querySelector('#prep-title'), retry = app.querySelector('#prep-retry');
  const here = location.hash;
  box.hidden = false; video.controls = false;
  const show = (t, msg, pct) => { title.textContent = t; text.textContent = msg; bar.style.width = (pct || 0) + '%'; };
  const step = (api) => fetch(`api/${api}?id=${encodeURIComponent(v.id)}`, { cache: 'no-store' })
    .then(r => r.json()).then(s => {
      if (location.hash !== here) return;                       // l'enfant a changé de page
      if (s.state === 'ready' && s.f) {
        v.f = s.f; box.hidden = true; video.controls = true; video.src = s.f;
        video.play().catch(() => {});
        return;
      }
      if (s.state === 'error' || s.state === 'unknown') {
        show("This video can't be prepared right now", s.error || 'Please try again in a few minutes.', 0);
        retry.hidden = false;
        return;
      }
      if (s.state === 'queued') show('Waiting for another video…', `Your video is number ${s.position || 1} in line.`, 2);
      else show('Preparing your video…', s.phase === 'merge' ? 'Almost ready!' : "It's being copied from YouTube, this takes about a minute.", s.pct);
      setTimeout(() => step('status'), 2000);
    })
    .catch(() => { if (location.hash === here) setTimeout(() => step('status'), 4000); });
  retry.onclick = () => { retry.hidden = true; show('Preparing your video…', 'Trying again…', 0); step('prepare'); };
  step('prepare');
}

// horodatages « 12:34 » de la description -> boutons qui avancent la vidéo ; les liens restent du texte
function linkTimes(html) {
  return html.replace(/\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b/g, (m, h, mi, s) =>
    `<span class="ts" data-t="${(+h || 0) * 3600 + +mi * 60 + +s}">${m}</span>`);
}

function search(q) {
  qInput.value = q;
  const words = norm(q).split(/\s+/).filter(Boolean);
  const list = words.length ? LIB.videos.filter(v => words.every(w => v._n.includes(w))) : [];
  app.innerHTML = `<h1 class="page-title">${plural(list.length, 'result')} for “${esc(q)}”</h1>`;
  app.appendChild(pagedGrid(list, { empty: 'Nothing found. Try other words.' }));
}

function history() {
  const p = progress();
  const list = Object.entries(p).filter(([id]) => BY_ID.has(id)).sort((a, b) => b[1].at - a[1].at).map(([id]) => BY_ID.get(id));
  app.innerHTML = `<div class="section-head"><h1 class="page-title" style="margin:0">History</h1>
    ${list.length ? '<button class="more" id="clear" type="button" style="border:0;cursor:pointer">Clear history</button>' : ''}</div><br>`;
  app.appendChild(pagedGrid(list, { empty: 'Videos you watch on this device will appear here.' }));
  const clear = app.querySelector('#clear');
  if (clear) clear.onclick = () => { if (confirm('Clear the history on this device?')) { store.set('progress', {}); history(); } };
}

function notFound() { app.innerHTML = '<div class="empty">This video or channel is not available. <a href="#/"><b>Back to home</b></a></div>'; }

// ---------- routeur ----------
function route() {
  if (!LIB) return;
  const [, page, arg] = (location.hash || '#/').match(/^#\/?([^/]*)\/?(.*)$/) || [];
  const a = decodeURIComponent(arg || '');
  document.title = 'Video Library';
  if (page !== 's') qInput.value = '';
  if (page === 'c') channel(a);
  else if (page === 'v') watch(a);
  else if (page === 's') search(a);
  else if (page === 'history') history();
  else home();
  if (page !== 'v') window.scrollTo(0, 0);
}
document.getElementById('search').onsubmit = e => {
  e.preventDefault();
  const q = qInput.value.trim();
  if (q) location.hash = '#/s/' + encodeURIComponent(q);
};
window.addEventListener('hashchange', route);

fetch('library.json', { cache: 'no-cache' })
  .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then(lib => {
    LIB = lib;
    for (const c of lib.channels) { CH.set(c.id, c); BY_CH.set(c.id, []); }
    lib.videos = lib.videos.filter(v => CH.has(v.c));
    for (const v of lib.videos) { v._n = norm(v.t + ' ' + v.s); BY_ID.set(v.id, v); BY_CH.get(v.c).push(v); }
    route();
  })
  .catch(() => { app.innerHTML = '<div class="empty">The library is not available right now. Try again in a few minutes.</div>'; });
