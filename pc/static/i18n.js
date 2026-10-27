// Lingue del pannello, con chiavi identificative.
// - Testi: i18n_it.js è il riferimento (chiave → italiano); i18n_<lingua>.js ha le stesse chiavi tradotte.
// - index.html: data-i18n="chiave" per il testo di un elemento, data-i18n-title / data-i18n-placeholder /
//   data-i18n-aria per gli attributi. Il testo italiano resta scritto nell'HTML: senza dizionario si legge lo stesso.
// - app.js: t('chiave', valore0, valore1…) per il testo semplice, th(…) dentro l'HTML (il testo tradotto viene
//   protetto, i valori no: sono già HTML o testo protetto da chi chiama).
// - Testi che arrivano dal programma sul PC in italiano (righe [Pannello] della console, versioni, messaggi):
//   non hanno una chiave nel codice del pannello, si riconoscono dal testo italiano di i18n_it.js.
// - Cambio di lingua: si salva la scelta e si ricarica il pannello, che si costruisce direttamente nella lingua nuova.
// Per aggiungere una lingua: il suo i18n_<codice>.js (stesse chiavi di i18n_it.js) e una riga in LANGS, col nome
// scritto nella lingua stessa. I test controllano che tutte le lingue abbiano le stesse chiavi e gli stessi segnaposto.
(function () {
  const LANG_KEY = 'voxarium_lang';
  const LANGS = { it: 'Italiano', en: 'English', es: 'Español', fr: 'Français', de: 'Deutsch', pt: 'Português' };

  let lang = 'it';
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved && LANGS[saved]) lang = saved;
    else {
      // Prima volta: la lingua del dispositivo se c'è, altrimenti l'inglese
      const device = (navigator.language || 'it').slice(0, 2).toLowerCase();
      lang = LANGS[device] ? device : 'en';
    }
  } catch (e) { /* senza memoria del browser resta l'italiano */ }

  // Il dizionario della lingua scelta si carica adesso, prima di app.js: così app.js scrive già nella lingua giusta.
  // (i18n_it.js è caricato da index.html; app.py serve i dizionari senza cache)
  if (lang !== 'it' && !(window.I18N_DICTS || {})[lang]) {
    document.write(`<script src="i18n_${lang}.js"><\/script>`);
  }

  const dicts = () => window.I18N_DICTS || {};
  // Dizionario non arrivato (connessione caduta): si usa l'italiano
  const active = () => (lang !== 'it' && dicts()[lang]) ? lang : 'it';

  function lookup(key) {
    const d = dicts();
    const cur = d[active()];
    if (cur && Object.prototype.hasOwnProperty.call(cur, key)) return cur[key];
    if (d.it && Object.prototype.hasOwnProperty.call(d.it, key)) return d.it[key];
    return undefined;
  }

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = s => String(s).replace(/[&<>"']/g, c => ESC[c]);

  function fill(template, args) {
    return template.replace(/\{(\d+)\}/g, (m, n) => (n < args.length ? String(fromPc(args[n])) : m));
  }

  // Testo con chiave. Se non è una chiave è una frase italiana (testi dal PC, valori proposti nei comandi):
  // la si traduce riconoscendola dal dizionario.
  function t(key, ...args) {
    const s = lookup(key);
    if (s === undefined) return fromPc(key);
    return fill(s, args);
  }

  // Come t, per l'HTML: il testo tradotto viene protetto (& < > "), i valori restano come sono
  function th(key, ...args) {
    const s = lookup(key);
    if (s === undefined) return esc(fromPc(key));
    return fill(esc(s), args);
  }

  // ---------- testi italiani che arrivano dal PC: riconosciuti dal testo di i18n_it.js ----------
  // Frasi fisse in una tabella, frasi con {0}, {1}… trasformate in espressioni regolari raggruppate per inizio
  let compiled = null;
  function compile() {
    const it = dicts().it || {}, cur = dicts()[active()] || {};
    const exact = new Map(), byStart = new Map(), loose = [];
    for (const key of Object.keys(it)) {
      const src = it[key], out = cur[key];
      if (out === undefined) continue;
      if (!/\{\d+\}/.test(src)) { exact.set(src, out); continue; }
      const re = new RegExp('^' + src.split(/(\{\d+\})/).map((p, i) =>
        i % 2 ? '([\\s\\S]*?)' : p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('') + '$');
      const order = (src.match(/\{\d+\}/g) || []).map(p => +p.slice(1, -1));
      const tpl = { re, order, out, fixed: src.replace(/\{\d+\}/g, '').length };
      const start = src.match(/^[^{]{3}/);
      if (start) {
        if (!byStart.has(start[0])) byStart.set(start[0], []);
        byStart.get(start[0]).push(tpl);
      } else loose.push(tpl);
    }
    // Prima i modelli con più testo fisso: "Informazioni su questa {0}" vince su "Informazioni su {0}"
    const bySpecific = (a, b) => b.fixed - a.fixed;
    byStart.forEach(list => list.sort(bySpecific));
    loose.sort(bySpecific);
    return { exact, byStart, loose };
  }
  const cache = new Map();
  function applyTemplate(tpl, text) {
    const m = text.match(tpl.re);
    if (!m) return undefined;
    const parts = {};
    tpl.order.forEach((n, i) => { parts[n] = m[i + 1]; });
    return tpl.out.replace(/\{(\d+)\}/g, (x, n) => (n in parts ? fromPc(parts[n]) : x));
  }
  function fromPc(text) {
    if (active() === 'it' || typeof text !== 'string') return text;
    if (!compiled) compiled = compile();
    const norm = text.replace(/[ \t\r]+/g, ' ').replace(/ *\n */g, '\n').trim();
    if (!norm) return text;
    let out = compiled.exact.get(norm);
    if (out === undefined) {
      if (cache.has(norm)) out = cache.get(norm);
      else {
        for (const tpl of (compiled.byStart.get(norm.slice(0, 3)) || []).concat(compiled.loose)) {
          out = applyTemplate(tpl, norm);
          if (out !== undefined) break;
        }
        // "Etichetta:" e "Suggerimento...": frasi del dizionario con ":" o "..." aggiunti
        if (out === undefined) {
          const m = norm.match(/^(\/?)([\s\S]*?)(:|\.\.\.)?$/);
          if (m && (m[1] || m[3]) && m[2]) {
            const core = fromPc(m[2]);
            if (core !== m[2]) out = m[1] + core + (m[3] || '');
          }
        }
        if (cache.size > 5000) cache.clear();
        cache.set(norm, out);
      }
    }
    if (out === undefined) return text;
    const lead = text.match(/^\s*/)[0], trail = text.match(/\s*$/)[0];
    return lead + out + trail;
  }

  // ---------- index.html: elementi con data-i18n ----------
  const STATIC_ATTRS = { 'data-i18n-title': 'title', 'data-i18n-placeholder': 'placeholder', 'data-i18n-aria': 'aria-label' };
  function applyStatic(root) {
    if (active() === 'it') return;   // l'HTML è già scritto in italiano
    (root || document).querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
    for (const [dattr, attr] of Object.entries(STATIC_ATTRS)) {
      (root || document).querySelectorAll(`[${dattr}]`).forEach(el => el.setAttribute(attr, t(el.getAttribute(dattr))));
    }
  }

  // ---------- testi italiani rimasti nella pagina (in genere arrivati dal PC): tradotti appena compaiono ----------
  // Zone con testo che non va mai tradotto (console, chat dei giocatori, campi da compilare, codice)
  // Nella console si traducono solo le righe del programma ("[Pannello] ..."), mai quelle di Minecraft o i comandi
  const SKIP_SELECTOR = 'script, style, code, pre, textarea, input, [data-lang-select], [translate="no"], .chat-bubble, .chat-sender-name, .chat-time';
  const PANEL_LINE = /^\[Pannello[\]/]/;
  const ATTRS = ['title', 'placeholder', 'aria-label'];
  const done = new WeakMap();   // nodo di testo → testo già tradotto da qui

  function skipped(el) {
    if (!el || !el.closest) return true;
    if (el.closest(SKIP_SELECTOR)) return true;
    const line = el.closest('.log-line');
    return !!line && !PANEL_LINE.test(line.textContent);
  }
  function translateTextNode(node) {
    if (skipped(node.parentElement) || done.get(node) === node.data || !node.data.trim()) return;
    const out = fromPc(node.data);
    if (out !== node.data) node.data = out;
    done.set(node, node.data);
  }
  function translateAttrs(el) {
    if (skipped(el.parentElement)) return;
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v) continue;
      const out = fromPc(v);
      if (out !== v) el.setAttribute(a, out);
    }
  }
  function translateTree(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) { translateTextNode(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    translateAttrs(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let n;
    while ((n = walker.nextNode())) {
      if (n.nodeType === Node.TEXT_NODE) translateTextNode(n);
      else translateAttrs(n);
    }
  }
  const observer = new MutationObserver(records => {
    for (const r of records) {
      // Le righe della console e della chat arrivano a raffica: si scartano subito
      const host = r.target.nodeType === Node.ELEMENT_NODE ? r.target : r.target.parentElement;
      if (r.type !== 'attributes' && skipped(host)) continue;
      if (r.type === 'characterData') translateTextNode(r.target);
      else if (r.type === 'attributes') translateAttrs(r.target);
      else r.addedNodes.forEach(translateTree);
    }
  });

  // ---------- selettore della lingua (contenitori con data-lang-select) ----------
  // Fino a 3 lingue: pulsanti affiancati (IT | EN | ES). Da 4 in su: un pulsante col codice della lingua attiva che
  // apre l'elenco dei nomi, ognuno scritto nella propria lingua, con lo stile dei menu del pannello.
  const GLOBE = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>';
  const codes = Object.keys(LANGS);

  function renderSelector(box) {
    if (codes.length <= 3) {
      box.className = 'lang-switch';
      box.setAttribute('role', 'group');
      box.innerHTML = codes.map(c =>
        `<button type="button" data-lang="${c}" aria-label="${LANGS[c]}" title="${LANGS[c]}">${c.toUpperCase()}</button>`).join('');
      box.querySelectorAll('[data-lang]').forEach(b => b.addEventListener('click', () => setLanguage(b.dataset.lang)));
      return;
    }
    box.className = 'lang-picker';
    box.removeAttribute('role');
    box.innerHTML =
      `<button type="button" class="lang-picker-btn" aria-haspopup="menu" aria-expanded="false">${GLOBE}` +
      `<span class="lang-picker-code"></span><span class="lang-picker-caret" aria-hidden="true">▾</span></button>` +
      `<div class="dropdown-popover lang-picker-list" role="menu" style="display: none;">` +
      codes.map(c => `<button type="button" class="dropdown-item" role="menuitemradio" data-lang="${c}" lang="${c}">` +
        `<span class="lang-picker-name">${LANGS[c]}</span><span class="lang-picker-item-code">${c.toUpperCase()}</span></button>`).join('') +
      `</div>`;
    const btn = box.querySelector('.lang-picker-btn');
    const list = box.querySelector('.lang-picker-list');
    const items = [...list.querySelectorAll('[data-lang]')];
    const close = (focusBtn) => {
      if (list.style.display === 'none') return;
      list.style.display = 'none';
      btn.setAttribute('aria-expanded', 'false');
      if (focusBtn) btn.focus();
    };
    const open = () => {
      if (typeof closeAllDropdowns === 'function') closeAllDropdowns();
      list.style.display = 'flex';
      btn.setAttribute('aria-expanded', 'true');
      // L'elenco si apre verso il lato dove c'è spazio (sul telefono il pulsante è a sinistra, sul PC a destra)
      list.style.left = '0'; list.style.right = 'auto';
      if (list.getBoundingClientRect().right > window.innerWidth - 8) { list.style.left = 'auto'; list.style.right = '0'; }
      (items.find(i => i.dataset.lang === lang) || items[0]).focus();
    };
    btn.addEventListener('click', e => { e.stopPropagation(); list.style.display === 'none' ? open() : close(false); });
    items.forEach((item, i) => {
      item.addEventListener('click', () => { close(true); setLanguage(item.dataset.lang); });
      item.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault();
          items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
        }
      });
    });
    box.addEventListener('keydown', e => { if (e.key === 'Escape') close(true); });
    document.addEventListener('click', e => { if (!box.contains(e.target)) close(false); });
  }

  function syncSelectors() {
    const label = `${t('header.lingua_pannello')}: ${LANGS[lang]}`;
    document.querySelectorAll('[data-lang-select]').forEach(box => {
      box.querySelectorAll('[data-lang]').forEach(b => {
        b.setAttribute(b.getAttribute('role') === 'menuitemradio' ? 'aria-checked' : 'aria-pressed', String(b.dataset.lang === lang));
      });
      const btn = box.querySelector('.lang-picker-btn');
      if (btn) {
        btn.querySelector('.lang-picker-code').textContent = lang.toUpperCase();
        btn.title = label;
        btn.setAttribute('aria-label', label);
      } else {
        box.title = label;
        box.setAttribute('aria-label', label);
      }
    });
    document.documentElement.lang = active();
  }

  // Le scritte create da app.js sono già nella lingua scelta: per cambiarla si ricarica il pannello
  function setLanguage(next) {
    if (!LANGS[next] || next === lang) return;
    try { localStorage.setItem(LANG_KEY, next); } catch (e) { /* senza memoria la scelta non resterebbe: si ricarica comunque */ }
    lang = next;
    location.reload();
  }

  // Finestre del browser (avvisi, conferme e domande): app.js passa testi già tradotti, qui restano solo quelli dal PC
  const nativeAlert = window.alert.bind(window);
  const nativeConfirm = window.confirm.bind(window);
  const nativePrompt = window.prompt.bind(window);
  window.alert = msg => nativeAlert(fromPc(msg));
  window.confirm = msg => nativeConfirm(fromPc(msg));
  window.prompt = (msg, value) => nativePrompt(fromPc(msg), value);

  window.t = t;
  window.th = th;
  window.i18n = { t, th, setLanguage, getLanguage: () => active(), languages: LANGS };

  function start() {
    applyStatic(document);
    document.querySelectorAll('[data-lang-select]').forEach(renderSelector);
    syncSelectors();
    if (active() !== 'it') {
      translateTree(document.body);
      observer.observe(document.body, {
        subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS
      });
    }
    // Avviso per app.js: lingua pronta (per i valori proposti nei campi dei comandi)
    document.dispatchEvent(new CustomEvent('i18n:ready', { detail: { lang: active() } }));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
