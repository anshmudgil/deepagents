/* Meridian — Investment Research Agent (vanilla SPA, no build step).
   API: docs/CONTRACT.md. All Markdown is sanitized with DOMPurify; all other
   untrusted text is written with textContent. */
(() => {
  'use strict';

  // ---------------------------------------------------------------- constants
  const AGENTS = {
    'orchestrator':           { mono: 'OR', label: 'Orchestrator',         desc: 'Plans the work, delegates, writes the answer' },
    'fundamental-analyst':    { mono: 'FA', label: 'Fundamental analyst',  desc: 'Financials, valuation, SEC filings' },
    'quant-analyst':          { mono: 'QA', label: 'Quant analyst',        desc: 'Returns, volatility, drawdown, correlation' },
    'news-sentiment-analyst': { mono: 'NS', label: 'News & sentiment',     desc: 'Recent news, catalysts and tone' },
    'risk-manager':           { mono: 'RM', label: 'Risk manager',         desc: 'Key risks, scenarios, concentration' },
    'portfolio-strategist':   { mono: 'PS', label: 'Portfolio strategist', desc: 'Allocation, diversification, rebalancing' },
  };
  const DEFAULT_WATCH = ['NVDA', 'MSFT', 'AAPL', 'VTI'];
  const K_THEME = 'meridian.theme';
  const K_WATCH = 'meridian.watchlist';
  const K_THREAD = 'meridian.thread';
  const TICKER_RE = /^[A-Z0-9][A-Z0-9.\-^=]{0,11}$/;
  const MINUS = '−';

  // ---------------------------------------------------------------- helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  function storage(kind) {
    try { return kind === 'session' ? window.sessionStorage : window.localStorage; } catch (_) { return null; }
  }
  const store = {
    get(key, kind) { try { const s = storage(kind); return s ? s.getItem(key) : null; } catch (_) { return null; } },
    set(key, val, kind) { try { const s = storage(kind); if (s) s.setItem(key, val); } catch (_) { /* ignore */ } },
    del(key, kind) { try { const s = storage(kind); if (s) s.removeItem(key); } catch (_) { /* ignore */ } },
  };

  /** Tiny DOM builder. Text always goes through textContent. */
  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v == null || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'dataset') Object.assign(node.dataset, v);
        else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v === true ? '' : v);
      }
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }
  const SVGNS = 'http://www.w3.org/2000/svg';
  function svg(markup, cls) {
    // Only ever called with static, trusted markup defined in this file.
    const t = document.createElementNS(SVGNS, 'svg');
    t.setAttribute('viewBox', '0 0 16 16');
    t.setAttribute('aria-hidden', 'true');
    t.setAttribute('focusable', 'false');
    if (cls) t.setAttribute('class', cls);
    t.innerHTML = markup;
    return t;
  }
  const ICON = {
    check: '<path d="M3.5 8.5 6.5 11.5 12.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    warn: '<path d="M8 2.2 14.5 13.5h-13z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M8 6.5v3.3M8 11.6v.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    chev: '<path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    doc: '<path d="M4 1.8h5.2L12.5 5v9.2H4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 1.8V5h3.5M6 8h4.5M6 10.5h4.5" fill="none" stroke="currentColor" stroke-width="1.2"/>',
    x: '<path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    todoPending: '<circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" stroke-width="1.4"/>',
    todoProgress: '<circle cx="8" cy="8" r="5.8" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 2.2a5.8 5.8 0 0 1 0 11.6z" fill="currentColor"/>',
    todoDone: '<circle cx="8" cy="8" r="6.5" fill="currentColor" opacity=".16"/><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M5 8.2 7.1 10.3 11 6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    stop: '<rect x="4.5" y="4.5" width="7" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.4"/>',
  };

  const agentInfo = (a) => AGENTS[a] || { mono: String(a || '??').replace(/[^a-z]/gi, '').slice(0, 2).toUpperCase() || '??', label: a || 'Agent', desc: '' };
  const agentKey = (a) => (AGENTS[a] ? a : 'other');
  function agentBadge(agent) {
    const info = agentInfo(agent);
    return el('span', { class: 'agent-badge', dataset: { agent: agentKey(agent) }, 'aria-hidden': 'true', title: info.label }, info.mono);
  }

  function fmtBytes(n) {
    if (n < 1024) return `${n} B`;
    return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  }
  function fmtDuration(ms) {
    if (ms < 1000) return `${Math.max(1, Math.round(ms / 100) * 100)}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
    const m = Math.floor(ms / 60000); const s = Math.round((ms % 60000) / 1000);
    return `${m}m ${String(s).padStart(2, '0')}s`;
  }
  function fmtPrice(v, currency) {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || 'USD', currencyDisplay: 'narrowSymbol', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
    } catch (_) { return v.toFixed(2); }
  }
  function direction(v) { return typeof v !== 'number' || !isFinite(v) || Math.abs(v) < 0.005 ? 'flat' : v > 0 ? 'up' : 'down'; }
  function fmtPct(v) {
    if (typeof v !== 'number' || !isFinite(v)) return '—';
    const d = direction(v);
    const glyph = d === 'up' ? '▲' : d === 'down' ? '▼' : '■';
    const sign = d === 'up' ? '+' : d === 'down' ? MINUS : '';
    return `${glyph} ${sign}${Math.abs(v).toFixed(2)}%`;
  }
  function pctWords(v) {
    const d = direction(v);
    if (d === 'flat') return 'unchanged';
    return `${d === 'up' ? 'up' : 'down'} ${Math.abs(v).toFixed(2)} percent`;
  }
  function truncate(s, n) { s = String(s); return s.length > n ? `${s.slice(0, n - 1)}…` : s; }
  function pretty(v) {
    if (typeof v === 'string') {
      const t = v.trim();
      if ((t.startsWith('{') || t.startsWith('[')) && t.length < 200000) {
        try { return JSON.stringify(JSON.parse(t), null, 2); } catch (_) { /* not JSON */ }
      }
      return v;
    }
    try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
  }
  function basename(p) { return String(p).split('/').filter(Boolean).pop() || 'report.md'; }

  // ---------------------------------------------------------------- markdown
  let purifyHooked = false;
  function mdReady() { return !!(window.marked && typeof window.marked.parse === 'function' && window.DOMPurify && typeof window.DOMPurify.sanitize === 'function'); }
  function renderMarkdown(target, text) {
    if (mdReady()) {
      if (!purifyHooked) {
        window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
          if (node.tagName === 'A' && node.getAttribute('href')) {
            node.setAttribute('target', '_blank');
            node.setAttribute('rel', 'noopener noreferrer');
          }
        });
        purifyHooked = true;
      }
      let html;
      try { html = window.marked.parse(text || '', { gfm: true, breaks: false, async: false }); }
      catch (_) { target.classList.add('md-plain'); target.textContent = text || ''; return; }
      target.classList.remove('md-plain');
      target.innerHTML = window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select'], FORBID_ATTR: ['style'] });
      // Wide tables are scroll containers: make them keyboard reachable.
      $$('table', target).forEach((t) => { t.setAttribute('tabindex', '0'); });
    } else {
      target.classList.add('md-plain');
      target.textContent = text || '';
    }
  }

  // ---------------------------------------------------------------- DOM refs
  const dom = {
    body: document.body,
    modelBadge: $('#model-badge'),
    modelLabel: $('#model-badge .model-label'),
    newBtn: $('#new-research'),
    themeBtns: $$('[data-theme-choice]'),
    watchForm: $('#watch-add'),
    watchInput: $('#watch-input'),
    watchError: $('#watch-error'),
    watchList: $('#watch-list'),
    watchUpdated: $('#watch-updated'),
    conversation: $('#conversation'),
    empty: $('#empty-state'),
    roster: $('#roster'),
    turns: $('#turns'),
    status: $('#run-status'),
    timer: $('#run-timer'),
    composer: $('#composer'),
    input: $('#composer-input'),
    sendBtn: $('#send-btn'),
    stopBtn: $('#stop-btn'),
    sideTabs: $('#side-tabs'),
    mobileTabs: $('#mobile-tabs'),
    todoList: $('#todo-list'),
    planCount: $('#plan-count'),
    planProgress: $('#plan-progress'),
    planEmpty: $('#plan-empty'),
    activity: $('#activity'),
    activityEmpty: $('#activity-empty'),
    fileList: $('#file-list'),
    reportToolbar: $('#report-toolbar'),
    reportPath: $('#report-path'),
    reportView: $('#report-view'),
    reportsEmpty: $('#reports-empty'),
    copyBtn: $('#copy-report'),
    downloadBtn: $('#download-report'),
    toast: $('#toast'),
  };

  // ---------------------------------------------------------------- state
  const state = {
    threadId: store.get(K_THREAD, 'session'),
    run: null,              // active run
    todos: [],
    groups: new Map(),      // agent -> group record
    tools: new Map(),       // tool id -> tool record
    filePaths: [],
    files: {},              // path -> content
    selectedFile: null,
    watch: [],
    quotes: new Map(),
  };

  // ---------------------------------------------------------------- API
  async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = '';
      try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : ''; } catch (_) { /* ignore */ }
      const err = new Error(detail || `Request failed (HTTP ${res.status})`);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }
  const threadPath = (id, rest) => `/api/threads/${encodeURIComponent(id)}${rest}`;

  async function ensureThread() {
    if (state.threadId) return state.threadId;
    const j = await api('/api/threads', { method: 'POST' });
    if (!j || !j.thread_id) throw new Error('Server did not return a thread id');
    state.threadId = j.thread_id;
    store.set(K_THREAD, state.threadId, 'session');
    return state.threadId;
  }

  // ---------------------------------------------------------------- theme
  function applyTheme(choice) {
    const root = document.documentElement;
    if (choice === 'light' || choice === 'dark') { root.setAttribute('data-theme', choice); store.set(K_THEME, choice); }
    else { root.removeAttribute('data-theme'); store.del(K_THEME); choice = 'system'; }
    dom.themeBtns.forEach((b) => {
      const on = b.dataset.themeChoice === choice;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
    });
  }
  function initTheme() {
    const saved = store.get(K_THEME);
    applyTheme(saved === 'light' || saved === 'dark' ? saved : 'system');
    dom.themeBtns.forEach((b, i) => {
      b.addEventListener('click', () => applyTheme(b.dataset.themeChoice));
      b.addEventListener('keydown', (e) => {
        const dir = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (!dir) return;
        e.preventDefault();
        const next = dom.themeBtns[(i + dir + dom.themeBtns.length) % dom.themeBtns.length];
        applyTheme(next.dataset.themeChoice);
        next.focus();
      });
    });
  }

  // ---------------------------------------------------------------- tabs (ARIA APG)
  function wireTablist(list, onSelect) {
    const tabs = $$('[role="tab"]', list);
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => onSelect(tab));
      tab.addEventListener('keydown', (e) => {
        let j = null;
        if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
        else if (e.key === 'Home') j = 0;
        else if (e.key === 'End') j = tabs.length - 1;
        if (j === null) return;
        e.preventDefault();
        tabs[j].focus();
        onSelect(tabs[j]);
      });
    });
  }
  function markSelected(list, match) {
    $$('[role="tab"]', list).forEach((t) => {
      const on = match(t);
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
    });
  }
  function selectSideTab(name) {
    markSelected(dom.sideTabs, (t) => t.dataset.tab === name);
    ['plan', 'activity', 'reports'].forEach((n) => { $(`#panel-${n}`).hidden = n !== name; });
    if (dom.body.dataset.mview !== 'chat' && dom.body.dataset.mview !== 'watch') setMobileView(name);
  }
  function setMobileView(view) {
    dom.body.dataset.mview = view;
    markSelected(dom.mobileTabs, (t) => t.dataset.mview === view);
    if (view === 'chat' && stick) requestAnimationFrame(scrollToBottom);
    if (view === 'plan' || view === 'activity' || view === 'reports') {
      markSelected(dom.sideTabs, (t) => t.dataset.tab === view);
      ['plan', 'activity', 'reports'].forEach((n) => { $(`#panel-${n}`).hidden = n !== view; });
    }
  }
  const isMobile = () => window.matchMedia('(max-width: 900px)').matches;
  function showPanel(name) {
    if (isMobile()) setMobileView(name); else selectSideTab(name);
  }

  function setCount(name, text, live) {
    $$(`[data-count="${name}"]`).forEach((c) => {
      c.hidden = text === '' || text == null;
      c.textContent = text == null ? '' : String(text);
      c.classList.toggle('is-live', !!live);
    });
  }

  // ---------------------------------------------------------------- status
  let statusKey = '';
  function setStatus(parts, opts = {}) {
    // parts: array of Node|string. Avoid re-announcing identical status.
    const key = parts.map((p) => (p instanceof Node ? p.textContent : p)).join('|') + (opts.error ? '!' : '');
    if (key === statusKey) return;
    statusKey = key;
    dom.status.replaceChildren(...parts.map((p) => (p instanceof Node ? p : el('span', { class: 's-text', text: p }))));
    dom.status.classList.toggle('is-error', !!opts.error);
  }
  function clearStatus() { statusKey = ''; dom.status.replaceChildren(); dom.status.classList.remove('is-error'); dom.timer.textContent = ''; }

  // ---------------------------------------------------------------- conversation
  function hideEmpty() { dom.empty.hidden = true; }
  function nearBottom() {
    const c = dom.conversation;
    return c.scrollHeight - c.scrollTop - c.clientHeight < 120;
  }
  let stick = true;
  function scrollToBottom() { dom.conversation.scrollTop = dom.conversation.scrollHeight; }

  function brandMark() {
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 32 32'); s.setAttribute('class', 'brand-mark'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('focusable', 'false');
    s.innerHTML = '<circle cx="16" cy="16" r="12.5" fill="none" stroke="currentColor" stroke-width="2.5"/><path d="M16 3.5v25" stroke="currentColor" stroke-width="2.5"/><path d="M5 16h22" stroke="currentColor" stroke-width="1.25" opacity=".45"/>';
    return s;
  }
  function addUserTurn(text) {
    const node = el('div', { class: 'turn-user' }, el('div', { class: 'bubble' }, el('span', { class: 'sr-only', text: 'You asked: ' }), text));
    dom.turns.append(node);
    return node;
  }
  function addAssistantTurn(opts = {}) {
    const time = el('time', { datetime: new Date().toISOString(), text: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) });
    const md = el('div', { class: 'md answer' });
    const turn = el('article', { class: 'turn turn-assistant', 'aria-label': 'Meridian answer' },
      el('div', { class: 'meta' }, brandMark(), el('span', { class: 'who', text: 'Meridian' }), opts.restored ? null : el('span', { 'aria-hidden': 'true', text: '·' }), opts.restored ? null : time),
      opts.restored ? null : el('ol', { class: 'trail', 'aria-label': 'Analysts working on this question' }),
      md);
    dom.turns.append(turn);
    return { turn, md, trail: turn.querySelector('.trail') };
  }

  // ---------------------------------------------------------------- plan
  function renderTodos(todos) {
    state.todos = Array.isArray(todos) ? todos : [];
    const total = state.todos.length;
    const done = state.todos.filter((t) => t.status === 'completed').length;
    const active = state.todos.find((t) => t.status === 'in_progress');
    dom.planEmpty.hidden = total > 0;
    dom.planCount.textContent = total ? `${done} of ${total} steps complete` : 'No plan yet';
    dom.planProgress.style.width = total ? `${(done / total) * 100}%` : '0';
    setCount('plan', total ? `${done}/${total}` : '', !!active);
    const items = state.todos.map((t) => {
      const st = t.status === 'completed' || t.status === 'in_progress' ? t.status : 'pending';
      const icon = svg(st === 'completed' ? ICON.todoDone : st === 'in_progress' ? ICON.todoProgress : ICON.todoPending, 'ico');
      const label = st === 'completed' ? 'Done' : st === 'in_progress' ? 'In progress' : 'Pending';
      return el('li', { class: 'todo', dataset: { status: st } },
        icon,
        el('div', null,
          el('span', { class: 't-text', text: String(t.content || '') }),
          el('span', { class: st === 'in_progress' ? 't-state' : 'sr-only', text: label })));
    });
    dom.todoList.replaceChildren(...items);
    const run = state.run;
    if (run && run.step) {
      const idx = active ? state.todos.indexOf(active) : -1;
      run.step.hidden = idx === -1;
      if (idx !== -1) run.step.textContent = `Step ${idx + 1} of ${total}: ${String(active.content || '')}`;
    }
  }

  // ---------------------------------------------------------------- activity
  function ensureGroup(agent) {
    const key = agent || 'orchestrator';
    let g = state.groups.get(key);
    if (g) return g;
    const info = agentInfo(key);
    const count = el('span', { class: 'a-count' });
    const live = el('span', { class: 'a-live', hidden: true }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'working');
    const list = el('ul', { class: 'tool-list' });
    const notesP = el('p');
    const notes = el('details', { class: 'notes', hidden: true }, el('summary', { text: 'Working notes' }), notesP);
    const head = el('div', { class: 'agent-head' }, agentBadge(key), el('h2', { class: 'a-name', text: info.label }), live, count);
    const section = el('section', { class: 'agent-group', dataset: { agent: agentKey(key) }, 'aria-label': `${info.label} activity` }, head, notes, list);
    dom.activity.append(section);
    dom.activityEmpty.hidden = true;
    g = { agent: key, section, list, count, live, notes, notesP, notesText: '', n: 0, running: 0, notesDirty: false };
    state.groups.set(key, g);
    return g;
  }
  function refreshGroup(g) {
    g.count.textContent = g.n === 1 ? '1 call' : `${g.n} calls`;
    g.live.hidden = g.running === 0;
  }
  function refreshActivityCount() {
    const running = Array.from(state.tools.values()).filter((t) => t.status === 'running').length;
    setCount('activity', state.tools.size ? state.tools.size : '', running > 0);
  }
  function summarizeArgs(name, args) {
    if (!args || typeof args !== 'object') return typeof args === 'string' ? truncate(args, 90) : '';
    if (name === 'task' && args.subagent_type) {
      return `→ ${agentInfo(args.subagent_type).label}${args.description ? `: ${args.description}` : ''}`;
    }
    const parts = [];
    for (const [k, v] of Object.entries(args)) {
      if (v == null) continue;
      if (typeof v === 'object') { parts.push(`${k}: ${Array.isArray(v) ? `[${v.length}]` : '{…}'}`); continue; }
      parts.push(`${k}: ${v}`);
      if (parts.length >= 3) break;
    }
    return truncate(parts.join(' · '), 90);
  }
  function statusIcon(status) {
    if (status === 'running') return [el('span', { class: 'spinner', 'aria-hidden': 'true' }), el('span', { class: 'sr-only', text: 'Running' })];
    if (status === 'done') return [svg(ICON.check), el('span', { class: 'sr-only', text: 'Done' })];
    if (status === 'cancelled') return [svg(ICON.stop), el('span', { class: 'sr-only', text: 'Cancelled' })];
    return [svg(ICON.warn), el('span', { class: 'sr-only', text: 'Error' })];
  }
  function toolStart(d) {
    const id = String(d.id || `t${state.tools.size + 1}`);
    if (state.tools.has(id)) return;
    const agent = d.agent || 'orchestrator';
    const g = ensureGroup(agent);
    const st = el('span', { class: 'st' }, ...statusIcon('running'));
    const meta = el('span', { class: 't-meta' }, el('span', { class: 't-dur', text: '' }), svg(ICON.chev, 'chev'));
    const out = el('pre', { tabindex: '0', 'aria-label': 'Tool output', text: 'Waiting for output…' });
    const details = el('details', { class: 'tool', dataset: { status: 'running' } },
      el('summary', null, st,
        el('span', { class: 't-main' },
          el('span', { class: 't-name', text: String(d.name || 'tool') }),
          el('span', { class: 't-sub', text: summarizeArgs(d.name, d.args) })),
        meta),
      el('div', { class: 'tool-body' },
        el('h3', { text: 'Input' }), el('pre', { tabindex: '0', 'aria-label': 'Tool input', text: pretty(d.args ?? {}) }),
        el('h3', { text: 'Output' }), out));
    const li = el('li', null, details);
    g.list.append(li);
    revealInActivity(li);
    g.n += 1; g.running += 1;
    refreshGroup(g);
    const rec = { id, agent, name: d.name, args: d.args, status: 'running', t0: performance.now(), details, st, out, dur: meta.firstChild, group: g };
    state.tools.set(id, rec);
    if (d.name === 'task' && d.args && d.args.subagent_type) ensureGroup(d.args.subagent_type);
    refreshActivityCount();
    return rec;
  }
  let activityUserScroll = 0;
  function revealInActivity(node) {
    const panel = $('#panel-activity');
    if (panel.hidden || Date.now() - activityUserScroll < 4000) return;
    const pr = panel.getBoundingClientRect(); const nr = node.getBoundingClientRect();
    if (nr.bottom > pr.bottom) panel.scrollTop += nr.bottom - pr.bottom + 12;
    else if (nr.top < pr.top) panel.scrollTop -= pr.top - nr.top + 12;
  }
  function setToolStatus(rec, status) {
    if (rec.status === 'running') { rec.group.running = Math.max(0, rec.group.running - 1); }
    rec.status = status;
    rec.details.dataset.status = status;
    rec.st.replaceChildren(...statusIcon(status));
    rec.dur.textContent = status === 'cancelled' ? 'stopped' : fmtDuration(performance.now() - rec.t0);
    refreshGroup(rec.group);
  }
  function toolEnd(d) {
    let rec = state.tools.get(String(d.id));
    if (!rec) rec = toolStart({ id: d.id, name: d.name, args: {}, agent: d.agent });
    if (!rec || rec.status !== 'running') return;
    const output = typeof d.output === 'string' ? d.output : pretty(d.output);
    rec.out.textContent = output ? pretty(output) : '(no output)';
    const isErr = /^\s*(error|exception|traceback)\b/i.test(output || '');
    setToolStatus(rec, isErr ? 'error' : 'done');
    refreshActivityCount();
  }
  let notesRaf = 0;
  function appendNotes(agent, text) {
    const g = ensureGroup(agent);
    g.notesText = (g.notesText + text).slice(-1200);
    g.notesDirty = true;
    if (!notesRaf) {
      notesRaf = requestAnimationFrame(() => {
        notesRaf = 0;
        state.groups.forEach((gr) => {
          if (!gr.notesDirty) return;
          gr.notesDirty = false;
          gr.notes.hidden = false;
          gr.notesP.textContent = gr.notesText.length >= 1200 ? `…${gr.notesText}` : gr.notesText;
        });
      });
    }
  }
  function resetActivity() {
    state.groups.clear(); state.tools.clear();
    dom.activity.replaceChildren();
    dom.activityEmpty.hidden = false;
    setCount('activity', '');
  }

  // ---------------------------------------------------------------- files / reports
  function isMarkdown(p) { return /\.(md|markdown|txt)$/i.test(p) || !/\.[a-z0-9]+$/i.test(p); }
  function renderFileList() {
    const paths = state.filePaths;
    dom.reportsEmpty.hidden = paths.length > 0;
    setCount('reports', paths.length || '');
    const items = paths.map((p) => {
      const content = state.files[p];
      const size = typeof content === 'string' ? fmtBytes(new Blob([content]).size) : '…';
      const btn = el('button', { type: 'button', class: 'file-item', 'aria-current': String(p === state.selectedFile), title: p, onclick: () => selectFile(p) },
        svg(ICON.doc), el('span', { class: 'f-path', text: p }), el('span', { class: 'f-size', text: size }));
      return el('li', null, btn);
    });
    dom.fileList.replaceChildren(...items);
  }
  function renderSelectedFile() {
    const p = state.selectedFile;
    if (!p) { dom.reportToolbar.hidden = true; dom.reportView.replaceChildren(); return; }
    dom.reportToolbar.hidden = false;
    dom.reportPath.textContent = p;
    const content = state.files[p];
    if (typeof content !== 'string') {
      dom.reportView.replaceChildren(el('p', { class: 'answer-pending' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Loading file…'));
      return;
    }
    if (isMarkdown(p)) renderMarkdown(dom.reportView, content);
    else dom.reportView.replaceChildren(el('pre', { text: content }));
  }
  function selectFile(p) {
    state.selectedFile = p;
    renderFileList();
    renderSelectedFile();
  }
  function preferredFile() {
    const reports = state.filePaths.filter((p) => p.startsWith('/reports/'));
    return reports[reports.length - 1] || state.filePaths[0] || null;
  }
  function updatePaths(paths) {
    const list = Array.isArray(paths) ? paths.map(String) : [];
    state.filePaths = Array.from(new Set(list)).sort((a, b) => {
      const ra = a.startsWith('/reports/') ? 0 : 1; const rb = b.startsWith('/reports/') ? 0 : 1;
      return ra - rb || a.localeCompare(b);
    });
    if (state.selectedFile && !state.filePaths.includes(state.selectedFile)) state.selectedFile = null;
    if (!state.selectedFile) state.selectedFile = preferredFile();
    renderFileList();
    renderSelectedFile();
  }
  let filesTimer = 0; let filesSeq = 0;
  function scheduleFilesFetch(delay = 600) {
    clearTimeout(filesTimer);
    filesTimer = setTimeout(fetchFiles, delay);
  }
  async function fetchFiles() {
    if (!state.threadId) return;
    const seq = ++filesSeq; const tid = state.threadId;
    try {
      const j = await api(threadPath(tid, '/files'));
      if (seq !== filesSeq || tid !== state.threadId) return;
      const files = j && j.files && typeof j.files === 'object' ? j.files : {};
      state.files = {};
      for (const [k, v] of Object.entries(files)) state.files[k] = typeof v === 'string' ? v : pretty(v);
      updatePaths(Array.from(new Set([...state.filePaths, ...Object.keys(state.files)])));
    } catch (e) {
      if (e.status === 404) return;
      toast('Couldn’t load reports. They’ll refresh after the next update.');
    }
  }
  function resetFiles() {
    state.filePaths = []; state.files = {}; state.selectedFile = null;
    renderFileList(); renderSelectedFile();
  }
  async function copyReport() {
    const p = state.selectedFile; const text = p && state.files[p];
    if (typeof text !== 'string') return;
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {
      const ta = el('textarea', { class: 'sr-only', 'aria-hidden': 'true' }); ta.value = text;
      document.body.append(ta); ta.select();
      try { ok = document.execCommand('copy'); } catch (__) { ok = false; }
      ta.remove();
    }
    toast(ok ? 'Report copied as Markdown' : 'Copy failed — use Download instead');
  }
  function downloadReport() {
    const p = state.selectedFile; const text = p && state.files[p];
    if (typeof text !== 'string') return;
    let name = basename(p); if (!/\.[a-z0-9]+$/i.test(name)) name += '.md';
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }));
    const a = el('a', { href: url, download: name });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  let toastTimer = 0;
  function toast(msg) {
    dom.toast.textContent = msg; dom.toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { dom.toast.hidden = true; }, 2600);
  }

  // ---------------------------------------------------------------- SSE over POST
  /** Parse a text/event-stream body from fetch(). Handles chunk boundaries,
   *  CRLF/CR line endings (including a CR split across chunks), multi-line
   *  data fields and ":" comment lines. */
  async function readSSE(body, onEvent) {
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buf = ''; let carryCR = false;
    const feed = (text) => {
      if (carryCR) { text = `\r${text}`; carryCR = false; }
      if (text.endsWith('\r')) { carryCR = true; text = text.slice(0, -1); }
      buf += text.replace(/\r\n?/g, '\n');
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
        dispatchBlock(block, onEvent);
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      feed(decoder.decode(value, { stream: true }));
    }
    feed(decoder.decode());
    if (carryCR) { buf += '\n'; carryCR = false; }
    if (buf.trim()) dispatchBlock(buf, onEvent);
  }
  function dispatchBlock(block, onEvent) {
    let type = 'message'; const data = [];
    for (const line of block.split('\n')) {
      if (!line || line.startsWith(':')) continue;
      const i = line.indexOf(':');
      const field = i === -1 ? line : line.slice(0, i);
      let val = i === -1 ? '' : line.slice(i + 1);
      if (val.startsWith(' ')) val = val.slice(1);
      if (field === 'event') type = val.trim() || 'message';
      else if (field === 'data') data.push(val);
    }
    if (!data.length) return;
    const raw = data.join('\n');
    let payload;
    try { payload = JSON.parse(raw); } catch (_) { payload = { text: raw }; }
    onEvent(type, payload || {});
  }

  // ---------------------------------------------------------------- runs
  function setStreamingUI(on) {
    const hadFocus = document.activeElement;
    dom.sendBtn.hidden = on;
    dom.stopBtn.hidden = !on;
    dom.composer.setAttribute('aria-busy', String(on));
    if (on && hadFocus === dom.sendBtn) dom.stopBtn.focus();
    if (!on && hadFocus === dom.stopBtn) dom.input.focus();
  }
  let timerInt = 0;
  function startTimer(run) {
    clearInterval(timerInt);
    const tick = () => { dom.timer.textContent = fmtDuration(Date.now() - run.startedAt).replace(/\.\ds$/, 's'); };
    tick(); timerInt = setInterval(tick, 1000);
  }
  function stopTimer() { clearInterval(timerInt); timerInt = 0; }

  function scheduleAnswerRender(run) {
    if (run.raf) return;
    run.raf = requestAnimationFrame(() => {
      run.raf = 0;
      if (run.pending) { run.pending.remove(); run.pending = null; }
      renderMarkdown(run.md, run.answer);
      placeCaret(run.md);
      if (stick) scrollToBottom();
    });
  }

  /** Put the streaming caret at the end of the deepest last text block. */
  function placeCaret(root) {
    let node = root;
    while (node.lastElementChild && !/^(PRE|TABLE|HR|BR|IMG)$/.test(node.lastElementChild.tagName)) node = node.lastElementChild;
    if (/^(TABLE|THEAD|TBODY|TR)$/.test(node.tagName)) node = root;
    node.append(el('span', { class: 'caret', 'aria-hidden': 'true' }));
  }

  function handleEvent(run, type, d) {
    if (run !== state.run) return; // stale stream
    switch (type) {
      case 'token': {
        const text = typeof d.text === 'string' ? d.text : '';
        if (!text) return;
        const agent = d.agent || 'orchestrator';
        if (agent === 'orchestrator') {
          run.answer += text;
          scheduleAnswerRender(run);
          if (!run.writing) { run.writing = true; setStatus([agentBadge('orchestrator'), 'Writing the answer…']); }
        } else {
          appendNotes(agent, text);
        }
        break;
      }
      case 'tool_start': {
        toolStart(d);
        run.writing = false;
        const agent = d.agent || 'orchestrator';
        if (d.name === 'task' && d.args && d.args.subagent_type) {
          addTrail(run, String(d.id), d.args.subagent_type);
          setStatus([agentBadge(d.args.subagent_type), `Delegating to ${agentInfo(d.args.subagent_type).label.toLowerCase()}`]);
        } else {
          setStatus([agentBadge(agent), `${agentInfo(agent).label} · running ${d.name || 'a tool'}`]);
        }
        break;
      }
      case 'tool_end':
        toolEnd(d);
        setTrail(run, String(d.id), 'done');
        break;
      case 'todos':
        renderTodos(d.todos);
        break;
      case 'files':
        updatePaths(Array.from(new Set([...(state.filePaths || []), ...((d && d.paths) || [])])));
        scheduleFilesFetch();
        break;
      case 'done':
        run.done = true;
        break;
      case 'error':
        run.error = (d && typeof d.message === 'string' && d.message) || 'The agent reported an error.';
        break;
      default:
        break; // forward compatible: ignore unknown events
    }
  }

  function addTrail(run, id, agent) {
    if (!run.trail || run.trailItems.has(id)) return;
    const st = el('span', { class: 'tr-st' }, ...statusIcon('running'));
    const btn = el('button', { type: 'button', dataset: { status: 'running' }, title: 'Show in Activity', onclick: () => showPanel('activity') },
      agentBadge(agent), el('span', { text: agentInfo(agent).label }), st);
    run.trail.append(el('li', null, btn));
    run.trailItems.set(id, { btn, st });
  }
  function setTrail(run, id, status) {
    const it = run.trailItems && run.trailItems.get(id);
    if (!it || it.btn.dataset.status !== 'running') return;
    it.btn.dataset.status = status;
    it.st.replaceChildren(...statusIcon(status));
  }

  async function send(text) {
    text = (text || '').trim();
    if (!text || state.run) return;
    hideEmpty();
    addUserTurn(text);
    const { turn, md, trail } = addAssistantTurn();
    const pending = el('p', { class: 'answer-pending' }, el('span', { class: 'spinner', 'aria-hidden': 'true' }), 'Planning the research…');
    turn.append(pending);
    md.classList.add('streaming');
    stick = true;
    scrollToBottom();
    dom.input.value = ''; autosize();

    resetActivity();
    const step = el('p', { class: 'turn-step', hidden: true });
    trail.after(step);
    const run = { text, turn, md, trail, step, trailItems: new Map(), pending, answer: '', raf: 0, done: false, error: null, writing: false, startedAt: Date.now(), controller: new AbortController() };
    state.run = run;
    setStreamingUI(true);
    startTimer(run);
    setStatus([agentBadge('orchestrator'), 'Planning the research…']);

    let outcome = 'done';
    try {
      await ensureThread();
      let res = await postStream(run);
      if (res.status === 404) { // thread expired server-side: start a fresh one once
        state.threadId = null; store.del(K_THREAD, 'session');
        await ensureThread();
        res = await postStream(run);
      }
      if (!res.ok || !res.body) {
        let detail = '';
        try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : ''; } catch (_) { /* ignore */ }
        throw new Error(detail || `The server returned HTTP ${res.status}.`);
      }
      await readSSE(res.body, (type, d) => handleEvent(run, type, d));
      if (run.error) outcome = 'error';
      else if (!run.done) { outcome = 'error'; run.error = 'The connection closed before the agent finished.'; }
    } catch (e) {
      if (e && e.name === 'AbortError') outcome = 'stopped';
      else { outcome = 'error'; run.error = (e && e.message) || 'Network error.'; }
    }
    finishRun(run, outcome);
  }
  function postStream(run) {
    return fetch(threadPath(state.threadId, '/runs/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ message: run.text }),
      signal: run.controller.signal,
    });
  }
  function finishRun(run, outcome) {
    if (state.run !== run) return;
    state.run = null;
    if (run.raf) { cancelAnimationFrame(run.raf); run.raf = 0; }
    if (run.pending) { run.pending.remove(); run.pending = null; }
    run.md.classList.remove('streaming');
    if (run.step) run.step.remove();
    if (run.answer) renderMarkdown(run.md, run.answer);
    if (stick) scrollToBottom();
    stopTimer();
    const elapsed = fmtDuration(Date.now() - run.startedAt).replace(/\.\ds$/, 's');
    state.tools.forEach((t) => { if (t.status === 'running') setToolStatus(t, outcome === 'done' ? 'done' : 'cancelled'); });
    run.trailItems.forEach((_, id) => setTrail(run, id, outcome === 'done' ? 'done' : 'cancelled'));
    refreshActivityCount();
    setStreamingUI(false);

    if (outcome === 'done') {
      if (!run.answer.trim()) {
        run.turn.append(el('p', { class: 'turn-note' }, 'The agent finished without a written reply. Check the Reports tab for its deliverables.'));
      }
      setStatus([svg(ICON.check), `Research complete · ${elapsed}`]);
    } else if (outcome === 'stopped') {
      run.turn.append(el('p', { class: 'turn-note' }, el('span', { class: 'note-title', text: 'Stopped.' }), 'Partial results are kept above and in the side panel.'));
      setStatus([svg(ICON.stop), `Stopped after ${elapsed}`]);
    } else {
      const retry = el('button', { type: 'button', class: 'btn btn-secondary btn-sm', onclick: (ev) => { ev.currentTarget.disabled = true; send(run.text); } }, 'Try again');
      run.turn.append(el('div', { class: 'turn-note is-error', role: 'alert' }, el('span', { class: 'note-title', text: 'Something went wrong.' }), run.error, retry));
      setStatus([svg(ICON.warn), 'Run failed. Partial results kept.'], { error: true });
    }
    dom.timer.textContent = '';
    // Final file sync, then link reports from the turn.
    fetchFiles().then(() => { attachFilePills(run); if (stick) scrollToBottom(); });
  }
  function attachFilePills(run) {
    const reports = state.filePaths.filter((p) => p.startsWith('/reports/'));
    if (!reports.length || run.turn.querySelector('.turn-files')) return;
    const wrap = el('div', { class: 'turn-files', role: 'group', 'aria-label': 'Reports written in this run' },
      ...reports.map((p) => el('button', { type: 'button', class: 'file-pill', onclick: () => { selectFile(p); showPanel('reports'); } }, svg(ICON.doc), el('span', { text: basename(p) }), el('span', { class: 'sr-only', text: ' — open in Reports' }))));
    run.turn.append(wrap);
  }
  function stopRun() {
    if (state.run) state.run.controller.abort();
  }

  async function newResearch() {
    if (state.run) { const r = state.run; r.controller.abort(); finishRun(r, 'stopped'); }
    state.threadId = null; store.del(K_THREAD, 'session');
    dom.turns.replaceChildren();
    dom.empty.hidden = false;
    renderTodos([]);
    resetActivity();
    resetFiles();
    clearStatus();
    setStatus(['New research session started.']);
    dom.conversation.scrollTop = 0;
    if (isMobile()) setMobileView('chat');
    dom.input.focus();
    try { await ensureThread(); } catch (_) { /* created lazily on send */ }
  }

  async function restoreThread() {
    if (!state.threadId) return;
    try {
      const j = await api(threadPath(state.threadId, '/messages'));
      const msgs = (j && Array.isArray(j.messages)) ? j.messages : [];
      if (!msgs.length) return;
      hideEmpty();
      for (const m of msgs) {
        if (m.role === 'user') addUserTurn(String(m.content || ''));
        else if (m.role === 'assistant' && m.content) { const { md } = addAssistantTurn({ restored: true }); renderMarkdown(md, String(m.content)); }
      }
      scrollToBottom();
      await fetchFiles();
    } catch (e) {
      if (e.status === 404) { state.threadId = null; store.del(K_THREAD, 'session'); }
    }
  }

  // ---------------------------------------------------------------- composer
  function autosize() {
    const ta = dom.input;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }
  function initComposer() {
    dom.input.addEventListener('input', autosize);
    dom.conversation.addEventListener('scroll', () => { stick = nearBottom(); }, { passive: true });
    const panel = $('#panel-activity');
    ['wheel', 'touchmove', 'keydown'].forEach((ev) => panel.addEventListener(ev, () => { activityUserScroll = Date.now(); }, { passive: true }));
    dom.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        send(dom.input.value);
      } else if (e.key === 'Escape' && state.run) {
        e.preventDefault(); stopRun();
      }
    });
    dom.composer.addEventListener('submit', (e) => { e.preventDefault(); send(dom.input.value); });
    dom.stopBtn.addEventListener('click', stopRun);
    $$('.chip').forEach((c) => c.addEventListener('click', () => send(c.dataset.prompt)));
    dom.newBtn.addEventListener('click', newResearch);
    dom.copyBtn.addEventListener('click', copyReport);
    dom.downloadBtn.addEventListener('click', downloadReport);
  }
  function renderRoster() {
    dom.roster.replaceChildren(...Object.entries(AGENTS).map(([k, a]) =>
      el('li', null, agentBadge(k), el('div', null, el('span', { class: 'r-name', text: a.label }), el('span', { class: 'r-desc', text: a.desc })))));
  }

  // ---------------------------------------------------------------- watchlist
  function loadWatch() {
    const raw = store.get(K_WATCH);
    if (raw) {
      try { const arr = JSON.parse(raw); if (Array.isArray(arr)) return arr.filter((t) => typeof t === 'string' && TICKER_RE.test(t)).slice(0, 40); } catch (_) { /* fall through */ }
    }
    return DEFAULT_WATCH.slice();
  }
  const saveWatch = () => store.set(K_WATCH, JSON.stringify(state.watch));

  function sparkline(values, dir) {
    const pts = (values || []).filter((v) => typeof v === 'number' && isFinite(v));
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 64 26'); s.setAttribute('preserveAspectRatio', 'none');
    s.setAttribute('role', 'img');
    if (pts.length < 2) { s.setAttribute('aria-label', 'No price history'); return s; }
    const w = 64; const h = 26; const pad = 3;
    const min = Math.min(...pts); const max = Math.max(...pts); const span = max - min || 1;
    const x = (i) => (i / (pts.length - 1)) * (w - pad) + 1;
    const y = (v) => h - pad - ((v - min) / span) * (h - pad * 2);
    const first = pts[0]; const last = pts[pts.length - 1];
    const chg = ((last - first) / first) * 100;
    s.setAttribute('aria-label', `${pts.length}-day closes, from ${first.toFixed(2)} to ${last.toFixed(2)}, ${pctWords(chg)}`);
    const base = document.createElementNS(SVGNS, 'line');
    base.setAttribute('x1', '0'); base.setAttribute('x2', String(w)); base.setAttribute('y1', y(first).toFixed(2)); base.setAttribute('y2', y(first).toFixed(2));
    base.setAttribute('stroke', 'var(--border-strong)'); base.setAttribute('stroke-width', '1'); base.setAttribute('stroke-dasharray', '2 2'); base.setAttribute('vector-effect', 'non-scaling-stroke');
    const line = document.createElementNS(SVGNS, 'polyline');
    line.setAttribute('points', pts.map((v, i) => `${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' '));
    line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'currentColor'); line.setAttribute('stroke-width', '1.5');
    line.setAttribute('stroke-linejoin', 'round'); line.setAttribute('stroke-linecap', 'round'); line.setAttribute('vector-effect', 'non-scaling-stroke');
    const dot = document.createElementNS(SVGNS, 'circle');
    dot.setAttribute('cx', x(pts.length - 1).toFixed(2)); dot.setAttribute('cy', y(last).toFixed(2)); dot.setAttribute('r', '2'); dot.setAttribute('fill', 'currentColor');
    s.append(base, line, dot);
    void dir;
    return s;
  }

  function watchRow(ticker) {
    const tk = el('span', { class: 'w-ticker', text: ticker });
    const nm = el('span', { class: 'w-name' }, el('span', { class: 'skeleton', style: 'width:72px' }));
    const px = el('span', { class: 'w-price num' }, el('span', { class: 'skeleton', style: 'width:48px' }));
    const chg = el('span', { class: 'w-chg num', text: '' });
    const spark = el('span', { class: 'w-spark', 'aria-hidden': 'false' });
    const main = el('button', { type: 'button', class: 'watch-main', 'aria-label': `${ticker}, loading quote. Draft a research question.`, onclick: () => draftFor(ticker) }, tk, nm, spark, px, chg);
    const rm = el('button', { type: 'button', class: 'watch-remove', 'aria-label': `Remove ${ticker} from watchlist`, title: `Remove ${ticker}`, onclick: () => removeTicker(ticker) }, svg(ICON.x));
    const li = el('li', { class: 'watch-row', dataset: { state: 'loading', ticker } }, main, rm);
    return { li, nm, px, chg, spark, main };
  }
  function paintQuote(row, ticker, q, err) {
    if (err) {
      row.li.dataset.state = 'error';
      row.nm.textContent = err.status === 404 ? 'Ticker not found' : 'Quote unavailable';
      row.px.textContent = '—'; row.chg.textContent = ''; row.spark.replaceChildren();
      row.main.setAttribute('aria-label', `${ticker}, ${row.nm.textContent}. Draft a research question.`);
      return;
    }
    row.li.dataset.state = 'ok';
    const dir = direction(q.change_pct);
    row.nm.textContent = q.name || '';
    row.nm.title = q.name || '';
    row.px.textContent = fmtPrice(q.price, q.currency);
    row.chg.textContent = fmtPct(q.change_pct);
    row.chg.className = `w-chg num dir-${dir}`;
    row.spark.replaceChildren(sparkline(q.sparkline, dir));
    row.main.setAttribute('aria-label', `${ticker}${q.name ? `, ${q.name}` : ''}, ${fmtPrice(q.price, q.currency)}, ${pctWords(q.change_pct)} today. Draft a research question.`);
  }
  const rows = new Map();
  async function loadQuote(ticker) {
    const row = rows.get(ticker); if (!row) return;
    try {
      const q = await api(`/api/quote/${encodeURIComponent(ticker)}`);
      state.quotes.set(ticker, q);
      if (rows.get(ticker) === row) paintQuote(row, ticker, q);
    } catch (e) {
      if (rows.get(ticker) === row) paintQuote(row, ticker, null, e);
    }
  }
  function renderWatch() {
    const items = state.watch.map((t) => {
      let r = rows.get(t);
      if (!r) { r = watchRow(t); rows.set(t, r); loadQuote(t); }
      return r.li;
    });
    for (const t of Array.from(rows.keys())) if (!state.watch.includes(t)) rows.delete(t);
    dom.watchList.replaceChildren(...items);
    if (!state.watch.length) dom.watchList.replaceChildren(el('li', { class: 'panel-empty', text: 'Your watchlist is empty. Add a ticker above.' }));
  }
  function stampUpdated() {
    dom.watchUpdated.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  }
  function refreshQuotes() {
    if (document.hidden) return;
    state.watch.forEach(loadQuote);
    stampUpdated();
  }
  async function addTicker(e) {
    e.preventDefault();
    const t = dom.watchInput.value.trim().toUpperCase();
    dom.watchError.textContent = '';
    dom.watchInput.removeAttribute('aria-invalid');
    const fail = (msg) => { dom.watchError.textContent = msg; dom.watchInput.setAttribute('aria-invalid', 'true'); dom.watchInput.focus(); };
    if (!t) return fail('Enter a ticker symbol.');
    if (!TICKER_RE.test(t)) return fail('Use letters, digits, “.” or “-” (max 12).');
    if (state.watch.includes(t)) return fail(`${t} is already on your watchlist.`);
    try {
      await api(`/api/quote/${encodeURIComponent(t)}`);
    } catch (err) {
      if (err.status === 404) return fail(`Couldn’t find ticker ${t}.`);
      // Network/other errors: add anyway; row will show "Quote unavailable".
    }
    state.watch.push(t); saveWatch(); renderWatch();
    dom.watchInput.value = '';
    dom.watchInput.focus();
  }
  function removeTicker(t) {
    const idx = state.watch.indexOf(t);
    if (idx === -1) return;
    const btns = $$('.watch-remove', dom.watchList);
    state.watch.splice(idx, 1); saveWatch(); renderWatch();
    const next = $$('.watch-remove', dom.watchList)[Math.min(idx, state.watch.length - 1)];
    (next || dom.watchInput).focus();
    void btns;
    toast(`Removed ${t} from watchlist`);
  }
  function draftFor(t) {
    dom.input.value = `Full research report on ${t}`;
    autosize();
    if (isMobile()) setMobileView('chat');
    dom.input.focus();
    dom.input.setSelectionRange(dom.input.value.length, dom.input.value.length);
  }

  // ---------------------------------------------------------------- health
  async function loadHealth() {
    try {
      const j = await api('/api/health');
      dom.modelLabel.textContent = j.model || 'model unknown';
      dom.modelBadge.dataset.state = j.status === 'ok' ? 'ok' : 'down';
      dom.modelBadge.title = `Model: ${j.model || 'unknown'} · status ${j.status || 'unknown'}`;
    } catch (_) {
      dom.modelLabel.textContent = 'Server offline';
      dom.modelBadge.dataset.state = 'down';
    }
  }

  // ---------------------------------------------------------------- init
  function init() {
    initTheme();
    wireTablist(dom.sideTabs, (t) => selectSideTab(t.dataset.tab));
    wireTablist(dom.mobileTabs, (t) => setMobileView(t.dataset.mview));
    initComposer();
    renderRoster();
    renderTodos([]);
    resetFiles();
    state.watch = loadWatch();
    dom.watchForm.addEventListener('submit', addTicker);
    dom.watchInput.addEventListener('input', () => { if (dom.watchError.textContent) { dom.watchError.textContent = ''; dom.watchInput.removeAttribute('aria-invalid'); } });
    renderWatch(); stampUpdated();
    setInterval(refreshQuotes, 60000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshQuotes(); });
    loadHealth();
    restoreThread();
    autosize();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
