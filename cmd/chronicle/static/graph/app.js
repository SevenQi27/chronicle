'use strict';

const $ = id => document.getElementById(id);

const COLORS = {
  understanding: '#c59eff',
  experience: '#4ef0b8',
  entity: '#ff8e7b',
  source: '#6ec4ff',
  state: '#ffd370'
};

const NAMES = {
  understanding: '认识',
  experience: '经历',
  entity: '人物与主题',
  source: '来源证据',
  state: '事实与计划'
};

const RELATIONS = {
  EVIDENCED_BY: '原始来源',
  INVOLVES: '涉及实体',
  SUPPORTED_BY: '认识依据',
  REVISES: '补充版本',
  CORRECTS: '更正版本',
  RELATED_TO: '相关',
  CONTINUES: '延续',
  CONTRADICTS: '相互矛盾',
  CAUSED_BY: '因果依据'
};

const PROV_GROUPS = [
  { key: 'version', title: '版本演进', kinds: new Set(['REVISES', 'CORRECTS']) },
  { key: 'support', title: '认识依据', kinds: new Set(['SUPPORTED_BY', 'CAUSED_BY', 'CONTRADICTS']) },
  { key: 'evidence', title: '原始来源证据', kinds: new Set(['EVIDENCED_BY']) },
  { key: 'entity', title: '涉及人物与主题', kinds: new Set(['INVOLVES']) },
  { key: 'other', title: '其他关联', kinds: null }
];

const RECORD_KINDS = new Set(['experience', 'understanding', 'state']);
const BATCH_LIST = 80;
const SVG_NS = 'http://www.w3.org/2000/svg';

const canvas = $('graph');
const ctx = canvas.getContext('2d');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const compactScreen = matchMedia('(max-width: 1100px)');

let graph = { nodes: [], edges: [] };
let nodesById = new Map();
let adjacency = new Map();
let visible = [];
let visibleEdges = [];
let matches = new Set();
let sortedCandidates = [];

let selected = null;
let hovered = null;
let kind = 'all';
let query = '';
let mode = 'orbit'; // 'orbit' | 'timeline' | 'flow'
let egoOnly = false;
let neuralPulseOn = true;

let yaw = -0.34;
let pitch = -0.36;
let zoom = 1.08;
let velYaw = 0;
let velPitch = 0;
let targetYaw = null;
let targetPitch = null;
let rotating = !reducedMotion.matches;
let width = 1;
let height = 1;
let lastFrame = 0;

let minTime = 0;
let maxTime = 0;
let cutoff = Infinity;
let pending = false;
let playing = false;
let playLastTick = 0;

let projection = new Map();
let pointer = null;
let selectionPulse = null;
let layoutMotion = null;
let detailAnimations = [];
let numberTimers = new Map();

let listLimit = BATCH_LIST;
let listButtons = new Map();
let histogramBins = [];

// Kinetic ambient effects
const stars = Array.from({ length: 135 }, () => ({
  x: Math.random(),
  y: Math.random(),
  r: 0.4 + Math.random() * 1.35,
  phase: Math.random() * Math.PI * 2,
  speed: 0.0006 + Math.random() * 0.0018,
  depth: 0.3 + Math.random() * 0.7
}));
let meteors = [];
let ambientPulses = [];
let birthRipples = [];
let prevVisibleIds = new Set();

// Flow view state
let flowSelectedId = null;
let flowCardEls = { col1: new Map(), col2: new Map(), col3: new Map() };
let flowLinks = { c1to2: [], c2to3: [] };
let flowBeamRaf = 0;

function formatDate(value, full = false) {
  if (!value) return '未明确';
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: graph.timezone || 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(full ? { hour: '2-digit', minute: '2-digit', hour12: false } : {})
  }).format(d);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  }
  return h >>> 0;
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

/* Smooth Number Ticker */
function animateNumber(id, target) {
  const targetEl = $(id);
  if (!targetEl) return;
  const end = Number(target) || 0;
  if (reducedMotion.matches) {
    targetEl.textContent = end.toLocaleString('zh-CN');
    return;
  }
  const prevRaf = numberTimers.get(id);
  if (prevRaf) cancelAnimationFrame(prevRaf);
  const current = parseInt(String(targetEl.textContent).replace(/[^\d]/g, ''), 10) || 0;
  if (current === end) {
    targetEl.textContent = end.toLocaleString('zh-CN');
    return;
  }
  const start = performance.now();
  const duration = 460;
  function step(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    targetEl.textContent = Math.round(current + (end - current) * eased).toLocaleString('zh-CN');
    if (t < 1) numberTimers.set(id, requestAnimationFrame(step));
    else numberTimers.delete(id);
  }
  numberTimers.set(id, requestAnimationFrame(step));
}

/* Cursor Spotlight */
document.addEventListener('pointermove', e => {
  const card = e.target.closest?.('.memory-item, .prov-card, .flow-card');
  if (!card) return;
  const rect = card.getBoundingClientRect();
  card.style.setProperty('--mx', `${e.clientX - rect.left}px`);
  card.style.setProperty('--my', `${e.clientY - rect.top}px`);
}, { passive: true });

/* Segmented Mode Switch */
function syncModeThumb() {
  const activeBtn = document.querySelector(`.mode-btn[data-mode="${mode}"]`);
  const thumb = $('mode-thumb');
  if (!activeBtn || !thumb) return;
  thumb.style.width = `${activeBtn.offsetWidth}px`;
  thumb.style.transform = `translateX(${activeBtn.offsetLeft - 3}px)`;
}

function setMode(next) {
  if (next === mode) return;
  const prevGraphLayout = mode === 'timeline' ? 'timeline' : 'orbit';
  const nextGraphLayout = next === 'timeline' ? 'timeline' : 'orbit';
  if (prevGraphLayout !== nextGraphLayout && !reducedMotion.matches) {
    layoutMotion = {
      startedAt: performance.now(),
      from: new Map(graph.nodes.map(n => [n.id, [...(n.position || n.orbit)]]))
    };
  }
  mode = next;
  document.body.dataset.mode = mode;
  document.querySelectorAll('.mode-btn').forEach(btn => {
    const isActive = btn.dataset.mode === mode;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-selected', String(isActive));
  });
  syncModeThumb();

  const isFlow = mode === 'flow';
  $('flow-view').classList.toggle('hidden', !isFlow);
  if (isFlow) {
    renderFlowView();
    requestAnimationFrame(drawFlowBeams);
  }
}

/* Drawer Controls */
function setLeftOpen(open) {
  if (open && compactScreen.matches) setRightOpen(false);
  $('left-drawer').classList.toggle('collapsed', !open);
  document.body.classList.toggle('left-closed', !open);
  $('toggle-left').setAttribute('aria-pressed', String(open));
}

function setRightOpen(open) {
  if (open && compactScreen.matches) setLeftOpen(false);
  $('right-drawer').classList.toggle('collapsed', !open);
  document.body.classList.toggle('right-closed', !open);
  $('toggle-right').setAttribute('aria-pressed', String(open));
}

/* 3D Spatial Layout */
function positionNodes() {
  const understandings = graph.nodes.filter(n => n.kind === 'understanding');
  const records = graph.nodes.filter(n => RECORD_KINDS.has(n.kind) && n.kind !== 'understanding');
  const auxiliary = graph.nodes.filter(n => !RECORD_KINDS.has(n.kind));

  understandings.forEach((n, i) => {
    const angle = (i / Math.max(1, understandings.length)) * Math.PI * 2 + 0.4;
    const radius = understandings.length === 1 ? 0 : 74 + (i % 3) * 28;
    const y = Math.sin(angle * 2.1) * 44;
    n.orbit = [Math.cos(angle) * radius, y, Math.sin(angle) * radius];
    const tFrac = maxTime > minTime ? (new Date(n.recorded_at).getTime() - minTime) / (maxTime - minTime) : 0.5;
    n.timeline = [tFrac * 520 - 260, -78 + (i % 3) * 24, (i % 4 - 1.5) * 28];
  });

  records.forEach((n, i) => {
    const a = i * 2.3999632;
    const norm = records.length <= 1 ? 0 : i / (records.length - 1);
    const radius = 120 + Math.sqrt(norm) * 235;
    const y = Math.sin(a * 1.35) * (26 + norm * 65);
    n.orbit = [Math.cos(a) * radius, y, Math.sin(a) * radius];
    const tFrac = maxTime > minTime ? (new Date(n.recorded_at).getTime() - minTime) / (maxTime - minTime) : 0.5;
    n.timeline = [tFrac * 520 - 260, ((i % 5) - 2) * 28, ((i % 7) - 3) * 22];
  });

  const parents = new Map();
  graph.edges.forEach(e => {
    const from = nodesById.get(e.from);
    const to = nodesById.get(e.to);
    if (from && to && RECORD_KINDS.has(from.kind) && !RECORD_KINDS.has(to.kind)) {
      if (!parents.has(to.id)) parents.set(to.id, []);
      parents.get(to.id).push(from);
    }
  });

  auxiliary.forEach(n => {
    const seed = hash(n.id);
    const a = ((seed % 10000) / 10000) * Math.PI * 2;
    const linked = parents.get(n.id) || [];
    const origin3d = linked.length
      ? [0, 1, 2].map(axis => linked.reduce((s, item) => s + item.orbit[axis], 0) / linked.length)
      : [0, 0, 0];
    const elev = (((seed >>> 14) % 1000) / 500) - 1;
    const dist = n.kind === 'entity' ? 34 + ((seed >>> 5) % 48) : 24 + ((seed >>> 5) % 52);
    const horiz = Math.sqrt(Math.max(0, 1 - elev * elev));
    n.orbit = [
      origin3d[0] + Math.cos(a) * horiz * dist,
      origin3d[1] + elev * dist,
      origin3d[2] + Math.sin(a) * horiz * dist
    ];
    const parentTimeline = linked[0]?.timeline || [0, 0, 0];
    n.timeline = [
      parentTimeline[0] + Math.cos(a) * 44,
      parentTimeline[1] + (n.kind === 'source' ? 58 + (seed % 40) : -38 - (seed % 30)),
      parentTimeline[2] + Math.sin(a) * 44
    ];
  });

  const activeLayout = mode === 'timeline' ? 'timeline' : 'orbit';
  graph.nodes.forEach(n => {
    n.position = [...n[activeLayout]];
  });
  layoutMotion = null;
}

function buildAdjacency() {
  adjacency = new Map();
  for (const e of graph.edges) {
    if (!adjacency.has(e.from)) adjacency.set(e.from, []);
    if (!adjacency.has(e.to)) adjacency.set(e.to, []);
    adjacency.get(e.from).push(e);
    adjacency.get(e.to).push(e);
  }
}

/* Filtering */
function filterGraph() {
  const knownRecords = new Set(
    graph.nodes
      .filter(n => RECORD_KINDS.has(n.kind) && (!n.recorded_at || new Date(n.recorded_at).getTime() <= cutoff))
      .map(n => n.id)
  );
  const timeNodes = new Set(knownRecords);
  for (const e of graph.edges) {
    if (knownRecords.has(e.from) && !RECORD_KINDS.has(nodesById.get(e.to)?.kind)) {
      timeNodes.add(e.to);
    }
  }

  matches = new Set(
    graph.nodes
      .filter(n => {
        if (!timeNodes.has(n.id)) return false;
        if (kind !== 'all' && n.kind !== kind) return false;
        if (!query) return true;
        const hay = `${n.title || ''} ${n.text || ''} ${(n.payload?.aliases || []).join(' ')}`.toLocaleLowerCase();
        return hay.includes(query);
      })
      .map(n => n.id)
  );

  let included = new Set(matches);
  for (const e of graph.edges) {
    if (timeNodes.has(e.from) && timeNodes.has(e.to) && (matches.has(e.from) || matches.has(e.to))) {
      included.add(e.from);
      included.add(e.to);
    }
  }

  sortedCandidates = graph.nodes
    .filter(n => matches.has(n.id))
    .sort((a, b) => {
      if (kind === 'all' && !query) {
        const rank = n => (n.kind === 'understanding' ? 0 : n.kind === 'state' ? 1 : n.kind === 'experience' ? 2 : n.kind === 'entity' ? 3 : 4);
        const dr = rank(a) - rank(b);
        if (dr !== 0) return dr;
      }
      const ta = a.recorded_at ? new Date(a.recorded_at).getTime() : 0;
      const tb = b.recorded_at ? new Date(b.recorded_at).getTime() : 0;
      return tb - ta;
    });

  if (!selected || !included.has(selected)) {
    selected = sortedCandidates[0]?.id || null;
  }

  if (egoOnly && selected && included.has(selected)) {
    const egoSet = new Set([selected]);
    for (const e of adjacency.get(selected) || []) {
      if (included.has(e.from)) egoSet.add(e.from);
      if (included.has(e.to)) egoSet.add(e.to);
    }
    included = egoSet;
  }

  // Detect newly born nodes during Time-Lapse playback for birth-bloom ripples
  if (playing && !reducedMotion.matches) {
    const now = performance.now();
    let spawned = 0;
    for (const id of included) {
      if (!prevVisibleIds.has(id) && spawned < 6) {
        const n = nodesById.get(id);
        if (n && RECORD_KINDS.has(n.kind)) {
          birthRipples.push({ id, startedAt: now, color: COLORS[n.kind] || '#f5b971' });
          spawned++;
        }
      }
    }
  }
  prevVisibleIds = new Set(included);

  visible = graph.nodes.filter(n => included.has(n.id));
  visibleEdges = graph.edges.filter(
    e => included.has(e.from) && included.has(e.to) && (!e.recorded_at || new Date(e.recorded_at).getTime() <= cutoff)
  );

  animateNumber('graph-count', visible.length);
  animateNumber('stat-edges', visibleEdges.length);
  $('result-count').textContent = sortedCandidates.length.toLocaleString('zh-CN');
  $('graph-caption').textContent = egoOnly && selected
    ? `聚焦子图 · ${visible.length} 个关联节点 · ${visibleEdges.length} 条关系`
    : query || kind !== 'all'
      ? `${matches.size.toLocaleString('zh-CN')} 个匹配 · ${visibleEdges.length.toLocaleString('zh-CN')} 条关联`
      : `${knownRecords.size.toLocaleString('zh-CN')} 条记忆版本 · ${visibleEdges.length.toLocaleString('zh-CN')} 条关联`;

  const isEmpty = visible.length === 0;
  $('empty').classList.toggle('hidden', !isEmpty);
  if (isEmpty) {
    $('empty-title').textContent = graph.nodes.length ? '没有匹配的节点' : '还没有记忆';
    $('empty-desc').textContent = graph.nodes.length
      ? '试试清空搜索词、切换节点类型，或重置时间视角。'
      : '让 Agent 使用 Chronicle 记录一段经历，然后刷新观测台。';
    $('empty-refresh').textContent = graph.nodes.length ? '重置筛选并刷新' : '刷新记忆';
  }

  listLimit = BATCH_LIST;
  renderList();
  renderDetail();
  if (mode === 'flow') {
    renderFlowView();
    requestAnimationFrame(drawFlowBeams);
  }
}

/* Fast Batched Index List */
function renderList() {
  const list = $('memory-list');
  list.replaceChildren();
  listButtons.clear();

  if (!sortedCandidates.length) {
    list.append(el('p', 'list-empty', '没有符合条件的节点'));
    $('list-more-wrap').classList.add('hidden');
    return;
  }

  const slice = sortedCandidates.slice(0, listLimit);
  const frag = document.createDocumentFragment();
  for (const n of slice) {
    const btn = el('button', `memory-item${n.id === selected ? ' selected' : ''}`);
    btn.type = 'button';
    btn.setAttribute('role', 'option');
    btn.setAttribute('aria-selected', String(n.id === selected));
    btn.append(el('span', `dot item-dot ${n.kind}`));

    const body = el('div', 'item-body');
    body.append(el('div', 'item-title', n.title || '未命名节点'));
    body.append(el('div', 'item-meta', `${NAMES[n.kind] || n.kind}${n.recorded_at ? ' · ' + formatDate(n.recorded_at) : ''}`));
    btn.append(body);

    btn.addEventListener('click', () => select(n.id, true));
    frag.append(btn);
    listButtons.set(n.id, btn);
  }
  list.append(frag);

  const remaining = sortedCandidates.length - slice.length;
  $('list-more-wrap').classList.toggle('hidden', remaining <= 0);
  if (remaining > 0) {
    $('list-more').textContent = `显示更多（剩余 ${remaining.toLocaleString('zh-CN')} 条）`;
  }
}

/* Three-Column Luminous Provenance Flow View */
function buildFlowCard(n, extraClass, relTag) {
  const card = el('button', `flow-card ${extraClass || ''}`);
  card.type = 'button';

  const top = el('div', 'flow-card-top');
  const badge = el('span', `kind-badge ${n.kind}`);
  badge.append(el('span', `dot ${n.kind}`), document.createTextNode(NAMES[n.kind] || n.kind));
  top.append(badge, el('span', 'flow-card-time', n.recorded_at ? formatDate(n.recorded_at, true) : '—'));

  const title = el('div', 'flow-card-title', n.title || '未命名节点');
  const text = el('div', 'flow-card-text', n.text || '（无正文内容）');

  const foot = el('div', 'flow-card-foot');
  const leftLabel = relTag || (n.payload?.basis === 'explicit' ? '明确表达' : n.payload?.basis === 'inferred' ? '模型归纳' : n.payload?.role || '可追溯');
  const edgeCount = (adjacency.get(n.id) || []).length;
  foot.append(el('span', '', leftLabel), el('span', '', `${edgeCount} 条关联`));

  card.append(top, title, text, foot);
  return card;
}

function renderFlowView() {
  const col1El = $('flow-col-1');
  const col2El = $('flow-col-2');
  const col3El = $('flow-col-3');
  col1El.replaceChildren();
  col2El.replaceChildren();
  col3El.replaceChildren();
  flowCardEls = { col1: new Map(), col2: new Map(), col3: new Map() };
  flowLinks = { c1to2: [], c2to3: [] };

  // Column 1: Prioritize understandings, then states, then recent experiences
  const col1Nodes = sortedCandidates.filter(n => n.kind === 'understanding' || n.kind === 'state');
  const primaryPool = col1Nodes.length ? col1Nodes : sortedCandidates.filter(n => n.kind === 'experience').slice(0, 40);
  $('flow-col1-count').textContent = String(primaryPool.length);

  if (!primaryPool.length) {
    col1El.append(el('div', 'flow-empty', '当前筛选条件下没有可溯源的核心记录'));
    $('flow-col2-count').textContent = '0';
    $('flow-col3-count').textContent = '0';
    drawFlowBeams();
    return;
  }

  if (!flowSelectedId || !primaryPool.some(n => n.id === flowSelectedId)) {
    flowSelectedId = primaryPool.some(n => n.id === selected) ? selected : primaryPool[0].id;
  }

  primaryPool.slice(0, 45).forEach((n, idx) => {
    const isActive = n.id === flowSelectedId;
    const card = buildFlowCard(n, isActive ? 'active' : '', null);
    card.addEventListener('click', () => {
      flowSelectedId = n.id;
      selected = n.id;
      renderFlowView();
      requestAnimationFrame(drawFlowBeams);
    });
    if (!reducedMotion.matches && idx < 12) {
      card.animate(
        [{ opacity: 0, transform: 'translateX(-12px)' }, { opacity: 1, transform: 'translateX(0)' }],
        { duration: 340, delay: idx * 24, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'backwards' }
      );
    }
    col1El.append(card);
    flowCardEls.col1.set(n.id, card);
  });

  // Column 2: Linked experiences / revisions / entities of flowSelectedId
  const activeNode = nodesById.get(flowSelectedId);
  const col2Map = new Map();
  const directSources = new Map();

  for (const e of adjacency.get(flowSelectedId) || []) {
    const targetId = e.from === flowSelectedId ? e.to : e.from;
    const target = nodesById.get(targetId);
    if (!target) continue;
    if (target.kind === 'source') {
      directSources.set(target.id, { node: target, rel: RELATIONS[e.kind] || e.kind, via: flowSelectedId });
    } else {
      col2Map.set(target.id, { node: target, rel: RELATIONS[e.kind] || e.kind });
    }
  }

  const col2Items = [...col2Map.values()];
  $('flow-col2-count').textContent = String(col2Items.length);

  if (!col2Items.length) {
    col2El.append(el('div', 'flow-empty', '该记录直接关联原始证据，无中间支撑经历'));
  } else {
    col2Items.forEach((item, idx) => {
      const card = buildFlowCard(item.node, 'linked-exp', `← ${item.rel}`);
      card.addEventListener('click', () => {
        selected = item.node.id;
        renderDetail();
      });
      if (!reducedMotion.matches) {
        card.animate(
          [{ opacity: 0, transform: 'translateY(14px) scale(0.97)' }, { opacity: 1, transform: 'translateY(0) scale(1)' }],
          { duration: 380, delay: idx * 45, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'backwards' }
        );
      }
      col2El.append(card);
      flowCardEls.col2.set(item.node.id, card);
      flowLinks.c1to2.push({ from: flowSelectedId, to: item.node.id });
    });
  }

  // Column 3: Raw evidence sources from Column 2 items + direct sources of Column 1
  const col3Map = new Map();
  for (const item of col2Items) {
    for (const e of adjacency.get(item.node.id) || []) {
      const targetId = e.from === item.node.id ? e.to : e.from;
      const target = nodesById.get(targetId);
      if (target && target.kind === 'source') {
        if (!col3Map.has(target.id)) {
          col3Map.set(target.id, { node: target, rel: RELATIONS[e.kind] || e.kind });
        }
        flowLinks.c2to3.push({ from: item.node.id, to: target.id, col1Direct: false });
      }
    }
  }
  for (const [sid, info] of directSources.entries()) {
    if (!col3Map.has(sid)) {
      col3Map.set(sid, { node: info.node, rel: info.rel });
    }
    if (!col2Items.length) {
      flowLinks.c2to3.push({ from: flowSelectedId, to: sid, col1Direct: true });
    }
  }

  const col3Items = [...col3Map.values()];
  $('flow-col3-count').textContent = String(col3Items.length);
  if (!col3Items.length) {
    col3El.append(el('div', 'flow-empty', '未关联到原始来源证据'));
  } else {
    col3Items.forEach((item, idx) => {
      const card = buildFlowCard(item.node, 'linked-src', item.node.payload?.role ? `来源 · ${item.node.payload.role}` : '原始来源');
      card.addEventListener('click', () => {
        selected = item.node.id;
        renderDetail();
      });
      if (!reducedMotion.matches) {
        card.animate(
          [{ opacity: 0, transform: 'translateX(14px)' }, { opacity: 1, transform: 'translateX(0)' }],
          { duration: 400, delay: 70 + idx * 45, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'backwards' }
        );
      }
      col3El.append(card);
      flowCardEls.col3.set(item.node.id, card);
    });
  }
}

function drawFlowBeams() {
  const pathsGroup = $('flow-paths');
  if (!pathsGroup || mode !== 'flow') return;
  pathsGroup.replaceChildren();

  const svg = $('flow-svg');
  const containerRect = $('flow-view').getBoundingClientRect();
  if (!containerRect.width || !svg.getClientRects().length) return;

  const addCurve = (fromEl, toEl, gradId) => {
    if (!fromEl || !toEl) return;
    const r1 = fromEl.getBoundingClientRect();
    const r2 = toEl.getBoundingClientRect();
    const fromScroll = fromEl.parentElement.getBoundingClientRect();
    const toScroll = toEl.parentElement.getBoundingClientRect();
    if (r1.bottom <= fromScroll.top || r1.top >= fromScroll.bottom) return;
    if (r2.bottom <= toScroll.top || r2.top >= toScroll.bottom) return;

    const x1 = r1.right - containerRect.left;
    const y1 = (Math.max(r1.top, fromScroll.top) + Math.min(r1.bottom, fromScroll.bottom)) / 2 - containerRect.top;
    const x2 = r2.left - containerRect.left;
    const y2 = (Math.max(r2.top, toScroll.top) + Math.min(r2.bottom, toScroll.bottom)) / 2 - containerRect.top;
    const dx = Math.max(40, (x2 - x1) * 0.5);
    const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;

    const bg = document.createElementNS(SVG_NS, 'path');
    bg.setAttribute('d', d);
    bg.setAttribute('class', 'flow-beam-bg');
    bg.setAttribute('stroke', `url(#${gradId})`);

    const core = document.createElementNS(SVG_NS, 'path');
    core.setAttribute('d', d);
    core.setAttribute('class', 'flow-beam-core');
    core.setAttribute('stroke', `url(#${gradId})`);

    pathsGroup.append(bg, core);
  };

  for (const link of flowLinks.c1to2) {
    addCurve(flowCardEls.col1.get(link.from), flowCardEls.col2.get(link.to), 'beam-grad-1');
  }
  for (const link of flowLinks.c2to3) {
    const startEl = link.col1Direct ? flowCardEls.col1.get(link.from) : flowCardEls.col2.get(link.from);
    addCurve(startEl, flowCardEls.col3.get(link.to), 'beam-grad-2');
  }
}

['flow-col-1', 'flow-col-2', 'flow-col-3'].forEach(id => {
  $(id)?.addEventListener('scroll', () => {
    if (flowBeamRaf) cancelAnimationFrame(flowBeamRaf);
    flowBeamRaf = requestAnimationFrame(drawFlowBeams);
  }, { passive: true });
});

/* Camera Fly-To & Selection */
function flyToNode(node) {
  if (!node?.position || mode === 'flow') return;
  const [x, y, z] = node.position;
  const planar = Math.hypot(x, z);
  if (planar > 1) {
    targetYaw = Math.atan2(-x, z);
    targetPitch = Math.max(-0.85, Math.min(0.85, Math.atan2(y, planar) * 0.65));
    velYaw = 0;
    velPitch = 0;
  }
}

function select(id, shouldOpenRight = false) {
  const prevBtn = listButtons.get(selected);
  const nextBtn = listButtons.get(id);
  prevBtn?.classList.remove('selected');
  prevBtn?.setAttribute('aria-selected', 'false');
  nextBtn?.classList.add('selected');
  nextBtn?.setAttribute('aria-selected', 'true');

  selected = id;
  selectionPulse = reducedMotion.matches ? null : { id, startedAt: performance.now() };

  const node = nodesById.get(id);
  if (node) flyToNode(node);

  if (egoOnly) {
    filterGraph();
  } else {
    renderDetail();
  }

  if (shouldOpenRight && mode !== 'flow') setRightOpen(true);
  $('detail-content').scrollTop = 0;
  animateDetail();
  $('announcement').textContent = `已选择 ${node?.title || ''}`;
}

function cancelDetailAnimations() {
  detailAnimations.forEach(a => a.cancel());
  detailAnimations = [];
}

function animateDetail() {
  cancelDetailAnimations();
  if (reducedMotion.matches) return;
  detailAnimations = ['detail-title', 'detail-meta', 'detail-text', 'detail-links']
    .map(id => $(id))
    .filter(Boolean)
    .map((node, index) =>
      node.animate(
        [{ opacity: 0.12, transform: 'translateY(12px)' }, { opacity: 1, transform: 'translateY(0)' }],
        { duration: 360, delay: index * 48, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'backwards' }
      )
    );
}

/* Right Inspector */
function addMeta(label, value) {
  const row = el('div', 'meta-row');
  row.append(el('span', '', label), el('span', '', value));
  $('detail-meta').append(row);
}

function renderDetail() {
  const node = nodesById.get(selected);
  $('detail-empty').classList.toggle('hidden', !!node);
  $('detail-content').classList.toggle('hidden', !node);
  if (!node) {
    $('detail-version').textContent = '';
    return;
  }

  const p = node.payload || {};
  const col = COLORS[node.kind] || '#f5b971';
  $('inspector-accent').style.background = `linear-gradient(90deg, transparent, ${col}, transparent)`;
  $('inspector-accent').style.boxShadow = `0 0 16px ${col}`;

  const badge = $('detail-kind');
  badge.className = `kind-badge ${node.kind}`;
  badge.replaceChildren(el('span', `dot ${node.kind}`), document.createTextNode(NAMES[node.kind] || node.kind));

  const basisBadge = $('detail-basis');
  if (p.basis) {
    basisBadge.classList.remove('hidden');
    basisBadge.classList.toggle('inferred', p.basis !== 'explicit');
    basisBadge.textContent = p.basis === 'explicit' ? '明确表达' : '模型归纳 · 建议核验';
  } else {
    basisBadge.classList.add('hidden');
  }

  const hasLater = graph.nodes.some(
    n => n.logical_id && n.logical_id === node.logical_id && new Date(n.recorded_at) > new Date(node.recorded_at) && new Date(n.recorded_at).getTime() <= cutoff
  );
  $('detail-version').textContent = RECORD_KINDS.has(node.kind) ? (hasLater ? '历史版本' : '最新版本') : '证据节点';

  $('detail-title').textContent = node.title || '未命名节点';
  $('detail-text').textContent = node.text || '（无正文内容）';
  $('content-heading').textContent = node.kind === 'source' ? '原始对话 / 工具原文' : node.kind === 'entity' ? '实体名称' : '记忆正文';

  $('detail-meta').replaceChildren();
  if (node.recorded_at) addMeta('获知时间', formatDate(node.recorded_at, true));
  if (p.when) addMeta('发生时间', p.when.start ? formatDate(p.when.start, true) : p.when.description || '未明确');
  if (p.when?.precision) {
    const map = { exact: '精确时刻', day: '日期', range: '时间范围', approximate: '大致时间', unknown: '未知' };
    addMeta('时间精度', map[p.when.precision] || p.when.precision);
  }
  if (p.valid_from) addMeta('开始适用', formatDate(p.valid_from, true));
  if (p.valid_to) addMeta('适用截止', formatDate(p.valid_to, true));
  if (p.kind) addMeta('记录类别', ({ fact: '事实', rule: '规则', plan: '计划' })[p.kind] || p.kind);
  if (p.due_at) addMeta('计划时间', formatDate(p.due_at, true));
  if (p.category) addMeta('实体类别', p.category);
  if (p.aliases?.length) addMeta('别名', p.aliases.join(' · '));
  if (p.role) addMeta('来源角色', p.role);
  if (p.conversation_id) addMeta('所属会话', p.conversation_id);

  const links = visibleEdges.filter(e => e.from === selected || e.to === selected);
  $('detail-link-count').textContent = String(links.length);
  const container = $('detail-links');
  container.replaceChildren();

  if (!links.length) {
    container.append(el('p', 'list-empty', '当前时间视角下没有关联节点'));
  } else {
    for (const group of PROV_GROUPS) {
      const groupLinks = links.filter(e => {
        if (group.kinds) return group.kinds.has(e.kind);
        return !PROV_GROUPS.some(g => g.kinds && g.kinds.has(e.kind));
      });
      if (!groupLinks.length) continue;

      const section = el('div', 'prov-group');
      section.append(el('div', 'prov-group-title', `${group.title} (${groupLinks.length})`));
      const listWrap = el('div', 'prov-list');

      for (const e of groupLinks) {
        const targetId = e.from === selected ? e.to : e.from;
        const target = nodesById.get(targetId);
        if (!target) continue;

        const card = el('button', 'prov-card');
        card.type = 'button';

        const top = el('div', 'prov-top');
        top.append(
          el('span', `dot ${target.kind}`),
          el('strong', '', target.title || '未命名节点'),
          el('span', 'prov-rel', `${e.from === selected ? '→' : '←'} ${RELATIONS[e.kind] || e.kind}`)
        );
        card.append(top);
        if (target.text && target.text !== target.title) {
          card.append(el('div', 'prov-snippet', target.text.slice(0, 110)));
        }
        card.addEventListener('click', () => select(target.id, true));
        listWrap.append(card);
      }

      section.append(listWrap);
      container.append(section);
    }
  }

  $('detail-json').textContent = JSON.stringify({ record_id: node.record_id, logical_id: node.logical_id, ...p }, null, 2);
}

/* Time Machine & Histogram */
function updateTimeFilter() {
  const range = $('time-range');
  const fraction = Number(range.value) / Number(range.max);
  cutoff = fraction >= 0.999 ? Infinity : minTime + (maxTime - minTime) * fraction;
  const label = fraction >= 0.999 ? '全部时间' : formatDate(cutoff, true);
  $('time-reset').textContent = label;
  $('scrubber-fill').style.width = `${fraction * 100}%`;
  range.setAttribute('aria-valuetext', label);

  const activeBinCount = Math.ceil(fraction * histogramBins.length);
  histogramBins.forEach((bar, i) => {
    bar.classList.toggle('active', i < activeBinCount);
    bar.classList.toggle('peak', i === activeBinCount - 1 && fraction < 0.999);
  });
}

function buildHistogram(times) {
  const wrap = $('timeline-ticks');
  wrap.replaceChildren();
  histogramBins = [];
  const binCount = 48;
  const counts = new Array(binCount).fill(0);
  if (times.length && maxTime > minTime) {
    for (const t of times) {
      const idx = Math.min(binCount - 1, Math.max(0, Math.floor(((t - minTime) / (maxTime - minTime)) * binCount)));
      counts[idx]++;
    }
  }
  const maxBin = Math.max(1, ...counts);
  for (let i = 0; i < binCount; i++) {
    const bar = document.createElement('i');
    bar.style.height = `${Math.max(12, Math.round((counts[i] / maxBin) * 100))}%`;
    bar.classList.add('active');
    wrap.append(bar);
    histogramBins.push(bar);
  }
}

function setPlaying(next) {
  playing = next;
  const btn = $('time-play');
  btn.setAttribute('aria-pressed', String(playing));
  btn.textContent = playing ? '❚❚' : '▶';
  if (playing) {
    const range = $('time-range');
    if (Number(range.value) >= 995) {
      prevVisibleIds.clear();
      range.value = '0';
      updateTimeFilter();
      filterGraph();
    }
    playLastTick = performance.now();
  }
}

/* Data Loading */
async function load() {
  if (pending) return;
  pending = true;
  const refreshBtn = $('refresh');
  refreshBtn.disabled = true;
  refreshBtn.classList.add('loading');
  $('connection-label').textContent = '同步中';

  try {
    const response = await fetch('/api/graph', { cache: 'no-store' });
    const body = await response.json();
    if (!response.ok || !body.ok) throw new Error(body.error?.message || '暂时无法读取记忆');

    graph = body.result;
    nodesById = new Map(graph.nodes.map(n => [n.id, n]));
    buildAdjacency();

    const times = graph.nodes
      .filter(n => RECORD_KINDS.has(n.kind) && n.recorded_at)
      .map(n => new Date(n.recorded_at).getTime())
      .filter(Number.isFinite);

    maxTime = times.length ? Math.max(...times) : Date.now();
    minTime = times.length ? Math.min(...times) : maxTime - 60000;
    $('time-start').textContent = times.length ? formatDate(minTime, true) : '—';
    $('time-end').textContent = times.length ? formatDate(maxTime, true) : '—';
    if (minTime === maxTime) minTime -= 60000;

    buildHistogram(times);
    $('time-range').value = '1000';
    updateTimeFilter();

    $('namespace').textContent = graph.namespace || 'default';
    const counts = { all: graph.nodes.length, understanding: 0, experience: 0, entity: 0, source: 0, state: 0 };
    for (const n of graph.nodes) {
      if (counts[n.kind] !== undefined) counts[n.kind]++;
    }
    Object.entries(counts).forEach(([k, v]) => {
      const elCount = $(`count-${k}`);
      if (elCount) elCount.textContent = v.toLocaleString('zh-CN');
    });
    animateNumber('stat-understanding', counts.understanding);
    animateNumber('stat-experience', counts.experience);
    animateNumber('stat-source', counts.source);

    $('connection-label').textContent = '实时只读';
    $('status-dot').classList.remove('offline');
    $('notice').classList.add('hidden');
    $('notice').textContent = '';

    positionNodes();
    filterGraph();
  } catch (error) {
    $('connection-label').textContent = '连接中断';
    $('status-dot').classList.add('offline');
    $('notice').textContent = `${error.message}。确认 SQLite 文件可读后，点击右上角刷新。`;
    $('notice').classList.remove('hidden');
  } finally {
    pending = false;
    refreshBtn.disabled = false;
    refreshBtn.classList.remove('loading');
  }
}

/* 3D Projection & Kinetic Sci-Fi Canvas */
function project(point) {
  const [x, y, z] = point;
  const cy = Math.cos(yaw), sy = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const rx = x * cy - z * sy;
  const rz = x * sy + z * cy;
  const ry = y * cp - rz * sp;
  const depth = y * sp + rz * cp;
  const perspective = 1150 / (1150 + depth);
  const scale = Math.min(width / 760, height / 520) * zoom;
  return {
    x: width * 0.5 + rx * perspective * scale,
    y: height * 0.48 + ry * perspective * scale,
    scale: perspective * scale,
    z: depth
  };
}

function drawSpaceBackdrop(time) {
  // Parallax twinkling stars
  for (const s of stars) {
    const px = ((s.x * width + yaw * 36 * s.depth) % width + width) % width;
    const py = ((s.y * height + pitch * 26 * s.depth) % height + height) % height;
    const twinkle = 0.25 + 0.65 * (0.5 + 0.5 * Math.sin(time * s.speed + s.phase));
    ctx.fillStyle = `rgba(210, 228, 255, ${twinkle * 0.48})`;
    ctx.beginPath();
    ctx.arc(px, py, s.r, 0, Math.PI * 2);
    ctx.fill();
  }

  // Occasional shooting meteors
  if (!reducedMotion.matches && mode !== 'flow' && meteors.length < 2 && Math.random() < 0.008) {
    meteors.push({
      x: Math.random() * width * 0.9,
      y: Math.random() * height * 0.35,
      vx: 6 + Math.random() * 5,
      vy: 3 + Math.random() * 3,
      life: 0,
      maxLife: 42 + Math.random() * 24
    });
  }
  for (let i = meteors.length - 1; i >= 0; i--) {
    const m = meteors[i];
    m.x += m.vx;
    m.y += m.vy;
    m.life++;
    const alpha = Math.sin((m.life / m.maxLife) * Math.PI) * 0.65;
    const grad = ctx.createLinearGradient(m.x, m.y, m.x - m.vx * 11, m.y - m.vy * 11);
    grad.addColorStop(0, `rgba(255, 240, 210, ${alpha})`);
    grad.addColorStop(1, 'rgba(197, 158, 255, 0)');
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(m.x, m.y);
    ctx.lineTo(m.x - m.vx * 11, m.y - m.vy * 11);
    ctx.stroke();
    if (m.life >= m.maxLife) meteors.splice(i, 1);
  }
}

function drawHolographicGuides(time) {
  if (!visible.length) return;
  ctx.save();
  if (mode === 'timeline') {
    const start = project([-280, 0, 0]);
    const end = project([280, 0, 0]);
    const grad = ctx.createLinearGradient(start.x, start.y, end.x, end.y);
    grad.addColorStop(0, 'rgba(197, 158, 255, 0.12)');
    grad.addColorStop(0.5, 'rgba(245, 185, 113, 0.55)');
    grad.addColorStop(1, 'rgba(78, 240, 184, 0.14)');
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
    ctx.lineWidth = 1;
    for (let x = -240; x <= 240; x += 60) {
      const p1 = project([x, -10, 0]);
      const p2 = project([x, 10, 0]);
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.lineTo(p2.x, p2.y);
      ctx.stroke();
    }
  } else {
    const spin = time * 0.00018;
    const rings = [
      { r: 96, color: 'rgba(197, 158, 255, 0.24)', dash: [4, 6], dir: 1 },
      { r: 192, color: 'rgba(110, 196, 255, 0.13)', dash: [2, 10], dir: -0.7 },
      { r: 300, color: 'rgba(245, 185, 113, 0.12)', dash: [], dir: 0.4 }
    ];
    for (const ring of rings) {
      ctx.strokeStyle = ring.color;
      ctx.lineWidth = ring.r === 96 ? 1.2 : 1;
      ctx.setLineDash(ring.dash);
      ctx.beginPath();
      for (let i = 0; i <= 72; i++) {
        const a = (i * Math.PI) / 36 + spin * ring.dir;
        const pt = project([Math.cos(a) * ring.r, 0, Math.sin(a) * ring.r]);
        if (i === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
      }
      ctx.closePath();
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  ctx.restore();
}

function drawEnergyPacket(a, b, cx, cy, color, t, size = 2.6) {
  const x = (1 - t) * (1 - t) * a.x + 2 * (1 - t) * t * cx + t * t * b.x;
  const y = (1 - t) * (1 - t) * a.y + 2 * (1 - t) * t * cy + t * t * b.y;
  const tPrev = Math.max(0, t - 0.08);
  const px = (1 - tPrev) * (1 - tPrev) * a.x + 2 * (1 - tPrev) * tPrev * cx + tPrev * tPrev * b.x;
  const py = (1 - tPrev) * (1 - tPrev) * a.y + 2 * (1 - tPrev) * tPrev * cy + tPrev * tPrev * b.y;

  const grad = ctx.createLinearGradient(x, y, px, py);
  grad.addColorStop(0, '#fff7e8');
  grad.addColorStop(0.5, color);
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.strokeStyle = grad;
  ctx.lineWidth = size;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.moveTo(px, py);
  ctx.lineTo(x, y);
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(x, y, size * 0.9, 0, Math.PI * 2);
  ctx.fillStyle = '#ffffff';
  ctx.shadowBlur = 12;
  ctx.shadowColor = color;
  ctx.fill();
  ctx.shadowBlur = 0;
}

function drawSelectionShockwave(point, elapsed) {
  for (const delay of [0, 140, 280]) {
    const progress = (elapsed - delay) / 640;
    if (progress < 0 || progress >= 1) continue;
    const radius = 10 + 58 * (1 - Math.pow(1 - progress, 2.5));
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(245, 185, 113, ${Math.pow(1 - progress, 2) * 0.92})`;
    ctx.lineWidth = 2.4 - progress * 1.6;
    ctx.stroke();
  }

  const spark = elapsed / 420;
  if (spark < 0 || spark >= 1) return;
  const dist = 12 + 46 * (1 - Math.pow(1 - spark, 2));
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const angle = (i * Math.PI * 2) / 10 + spark * 0.4;
    const dx = Math.cos(angle), dy = Math.sin(angle);
    ctx.moveTo(point.x + dx * dist, point.y + dy * dist);
    ctx.lineTo(point.x + dx * (dist + 10 * (1 - spark)), point.y + dy * (dist + 10 * (1 - spark)));
  }
  ctx.strokeStyle = `rgba(255, 238, 206, ${Math.pow(1 - spark, 2)})`;
  ctx.lineWidth = 1.6;
  ctx.stroke();
}

function drawDiamond(x, y, r) {
  ctx.beginPath();
  ctx.moveTo(x, y - r * 1.35);
  ctx.lineTo(x + r * 1.2, y);
  ctx.lineTo(x, y + r * 1.35);
  ctx.lineTo(x - r * 1.2, y);
  ctx.closePath();
}

function drawPillLabel(text, subtext, x, y, highlight) {
  ctx.save();
  ctx.font = `${highlight ? '650 12px' : '500 10.5px'} -apple-system, "PingFang SC", sans-serif`;
  const textW = ctx.measureText(text).width;
  const subW = subtext ? ctx.measureText(subtext).width * 0.9 : 0;
  const padX = highlight ? 11 : 8;
  const boxW = Math.max(textW, subW) + padX * 2;
  const boxH = subtext ? 40 : 24;
  const bx = Math.max(10, Math.min(width - boxW - 10, x - boxW / 2));
  const by = Math.max(80, Math.min(height - boxH - 12, y));

  ctx.fillStyle = highlight ? 'rgba(11, 16, 29, 0.94)' : 'rgba(9, 13, 22, 0.8)';
  ctx.strokeStyle = highlight ? 'rgba(245, 185, 113, 0.65)' : 'rgba(255, 255, 255, 0.16)';
  ctx.lineWidth = highlight ? 1.2 : 1;
  ctx.beginPath();
  ctx.roundRect(bx, by, boxW, boxH, 8);
  ctx.fill();
  ctx.stroke();

  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = highlight ? '#ffebd0' : '#dce5f5';
  ctx.fillText(text, bx + boxW / 2, by + (subtext ? 14 : 12));

  if (subtext) {
    ctx.font = '500 9.5px ui-monospace, "SF Mono", monospace';
    ctx.fillStyle = '#92a0bc';
    ctx.fillText(subtext, bx + boxW / 2, by + 28);
  }
  ctx.restore();
}

function draw(time) {
  const dt = Math.min(32, time - lastFrame || 16);
  lastFrame = time;

  if (playing && !document.hidden) {
    const step = (time - playLastTick) * 0.095;
    playLastTick = time;
    const range = $('time-range');
    const nextVal = Math.min(1000, Number(range.value) + step);
    range.value = String(Math.round(nextVal));
    updateTimeFilter();
    filterGraph();
    if (nextVal >= 1000) setPlaying(false);
  }

  // Smooth fly-to or inertial momentum damping
  if (targetYaw !== null && !pointer) {
    const dy = Math.atan2(Math.sin(targetYaw - yaw), Math.cos(targetYaw - yaw));
    const dp = targetPitch - pitch;
    yaw += dy * 0.09;
    pitch += dp * 0.09;
    if (Math.abs(dy) < 0.004 && Math.abs(dp) < 0.004) {
      targetYaw = null;
      targetPitch = null;
    }
  } else if (!pointer) {
    yaw += velYaw;
    pitch = Math.max(-1.05, Math.min(1.05, pitch + velPitch));
    velYaw *= 0.93;
    velPitch *= 0.93;
    if (Math.abs(velYaw) < 0.0001) velYaw = 0;
    if (Math.abs(velPitch) < 0.0001) velPitch = 0;
    if (rotating && !document.hidden && mode !== 'flow') {
      yaw += dt * 0.00009;
    }
  }

  const activeLayout = mode === 'timeline' ? 'timeline' : 'orbit';
  const morphProgress = layoutMotion ? Math.max(0, Math.min(1, (time - layoutMotion.startedAt) / 780)) : 1;
  const blend = morphProgress * morphProgress * (3 - 2 * morphProgress);
  if (morphProgress === 1) layoutMotion = null;

  const pulseAge = selectionPulse?.id === selected ? time - selectionPulse.startedAt : Infinity;
  if (pulseAge >= 920) selectionPulse = null;

  ctx.clearRect(0, 0, width, height);
  drawSpaceBackdrop(time);
  drawHolographicGuides(time);

  projection = new Map();
  for (const n of visible) {
    const target = n[activeLayout];
    const from = layoutMotion?.from.get(n.id) || target;
    n.position[0] = from[0] + (target[0] - from[0]) * blend;
    n.position[1] = from[1] + (target[1] - from[1]) * blend;
    n.position[2] = from[2] + (target[2] - from[2]) * blend;
    projection.set(n.id, project(n.position));
  }

  const focus = hovered || selected;
  const adjacent = new Set(focus ? [focus] : []);
  for (const e of visibleEdges) {
    if (e.from === focus) adjacent.add(e.to);
    if (e.to === focus) adjacent.add(e.from);
  }

  // Spawn autonomous ambient neural pulses along visible edges
  if (neuralPulseOn && !reducedMotion.matches && mode !== 'flow' && visibleEdges.length && ambientPulses.length < 18 && Math.random() < 0.22) {
    const edge = visibleEdges[Math.floor(Math.random() * visibleEdges.length)];
    ambientPulses.push({ edge, t: 0, speed: 0.007 + Math.random() * 0.009 });
  }

  const dense = visible.length > 160;
  for (const e of visibleEdges) {
    const a = projection.get(e.from);
    const b = projection.get(e.to);
    if (!a || !b) continue;

    const isRelated = focus && (e.from === focus || e.to === focus);
    if (dense && !isRelated && e.kind === 'EVIDENCED_BY') continue;

    const toNode = nodesById.get(e.to);
    const edgeColor = COLORS[toNode?.kind] || '#7eb4ff';
    const bend = 18 + Math.abs(a.x - b.x) * 0.055;
    const cx = (a.x + b.x) / 2;
    const cy = (a.y + b.y) / 2 - bend;

    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.quadraticCurveTo(cx, cy, b.x, b.y);

    if (isRelated) {
      // Double-layer neon beam on focused links
      ctx.strokeStyle = hexToRgba(edgeColor, 0.26);
      ctx.lineWidth = 4.5;
      ctx.stroke();
      ctx.strokeStyle = hexToRgba(edgeColor, 0.88);
      ctx.lineWidth = 1.4;
      ctx.stroke();

      if (!reducedMotion.matches) {
        const seed = (hash(e.from + e.to) % 1000) / 1000;
        const beamT = ((time * 0.00055) + seed) % 1;
        const [orig, dest] = e.from === focus ? [a, b] : [b, a];
        drawEnergyPacket(orig, dest, cx, cy, edgeColor, beamT, 2.8);
      }
    } else {
      ctx.strokeStyle = hexToRgba(edgeColor, dense ? 0.06 : 0.18);
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }
  }

  // Draw autonomous neural pulses
  for (let i = ambientPulses.length - 1; i >= 0; i--) {
    const ap = ambientPulses[i];
    ap.t += ap.speed;
    if (ap.t >= 1) {
      ambientPulses.splice(i, 1);
      continue;
    }
    const a = projection.get(ap.edge.from);
    const b = projection.get(ap.edge.to);
    if (!a || !b) continue;
    const bend = 18 + Math.abs(a.x - b.x) * 0.055;
    const cx = (a.x + b.x) / 2;
    const cy = (a.y + b.y) / 2 - bend;
    const col = COLORS[nodesById.get(ap.edge.to)?.kind] || '#c59eff';
    drawEnergyPacket(a, b, cx, cy, col, ap.t, 2.0);
  }

  // Depth-sorted nodes
  const sortedNodes = [...visible].sort((a, b) => projection.get(b.id).z - projection.get(a.id).z);
  const labelQueue = [];

  for (const n of sortedNodes) {
    const p = projection.get(n.id);
    const isSelected = n.id === selected;
    const isHovered = n.id === hovered;
    const isAdj = adjacent.has(n.id);
    const col = COLORS[n.kind] || '#f5b971';

    let baseR = n.kind === 'understanding' ? 5.6 : n.kind === 'experience' ? 2.8 : n.kind === 'state' ? 3.4 : n.kind === 'entity' ? 2.6 : 1.4;
    if (isSelected) baseR = 6.8;
    else if (isHovered) baseR = 5.8;
    else if (isAdj && focus) baseR = Math.max(baseR, 3.3);

    const breathe = n.kind === 'understanding' && !reducedMotion.matches ? 1 + 0.12 * Math.sin(time * 0.0028 + hash(n.id)) : 1;
    const r = Math.max(1.1, Math.min(9.5, baseR * breathe * Math.max(0.65, Math.min(1.5, p.scale))));
    p.radius = r;

    let alpha = matches.has(n.id) ? 0.96 : 0.34;
    if (n.kind === 'source' && !isAdj && dense) alpha *= 0.42;
    if (focus && !isAdj) alpha *= dense ? 0.25 : 0.4;
    if (isSelected || isHovered) alpha = 1;

    ctx.globalAlpha = alpha;

    if (n.kind === 'understanding' || isSelected || isHovered) {
      const haloR = isSelected ? r * 4.0 : r * 2.8;
      const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, haloR);
      grad.addColorStop(0, hexToRgba(isSelected ? '#ffe5b8' : col, isSelected ? 0.55 : 0.34));
      grad.addColorStop(1, hexToRgba(col, 0));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(p.x, p.y, haloR, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = isSelected ? '#fff4dd' : isHovered ? '#ffffff' : col;
    if (n.kind === 'understanding') {
      drawDiamond(p.x, p.y, r);
      ctx.fill();
    } else if (n.kind === 'entity') {
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.strokeStyle = isSelected ? '#fff4dd' : col;
      ctx.lineWidth = 1.7;
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();
    }

    if (isSelected || isHovered) {
      ctx.strokeStyle = isSelected ? '#f5b971' : hexToRgba(col, 0.9);
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + 6, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (isSelected || isHovered || (visible.length <= 28 && RECORD_KINDS.has(n.kind))) {
      labelQueue.push({ n, p, highlight: isSelected || isHovered });
    }
  }

  ctx.globalAlpha = 1;

  // Birth ripples during time-lapse
  for (let i = birthRipples.length - 1; i >= 0; i--) {
    const br = birthRipples[i];
    const age = (time - br.startedAt) / 620;
    if (age >= 1) {
      birthRipples.splice(i, 1);
      continue;
    }
    const pt = projection.get(br.id);
    if (!pt) continue;
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, 6 + 34 * (1 - Math.pow(1 - age, 2)), 0, Math.PI * 2);
    ctx.strokeStyle = hexToRgba(br.color, Math.pow(1 - age, 2) * 0.9);
    ctx.lineWidth = 2 - age;
    ctx.stroke();
  }

  if (pulseAge >= 0 && pulseAge < 920) {
    const pt = projection.get(selected);
    if (pt) drawSelectionShockwave(pt, pulseAge);
  }

  for (const item of labelQueue) {
    const title = (item.n.title || '未命名').length > 22 ? item.n.title.slice(0, 21) + '…' : (item.n.title || '未命名');
    const sub = item.highlight ? `${NAMES[item.n.kind] || item.n.kind}${item.n.recorded_at ? ' · ' + formatDate(item.n.recorded_at) : ''}` : null;
    drawPillLabel(title, sub, item.p.x, item.p.y + item.p.radius + 11, item.highlight);
  }

  requestAnimationFrame(draw);
}

function resize() {
  const rect = canvas.getBoundingClientRect();
  width = rect.width;
  height = rect.height;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  syncModeThumb();
  if (mode === 'flow') requestAnimationFrame(drawFlowBeams);
}

function syncControlInsets() {
  const top = document.querySelector('.topbar').getBoundingClientRect().bottom;
  const bottom = $('bottom-dock').getBoundingClientRect().top;
  document.body.style.setProperty('--hud-top', `${Math.ceil(top) + 10}px`);
  document.body.style.setProperty('--dock-inset', `${Math.ceil(window.innerHeight - bottom) + 10}px`);
}

function hitTest(event) {
  const bounds = canvas.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  let nearest = null;
  let best = Infinity;
  let depth = Infinity;
  for (const node of visible) {
    const p = projection.get(node.id);
    if (!p) continue;
    const dist = Math.hypot(p.x - x, p.y - y);
    const hitR = Math.max(12, (p.radius || 3) + 6);
    if (dist <= hitR && (dist < best || (dist === best && p.z < depth))) {
      nearest = node;
      best = dist;
      depth = p.z;
    }
  }
  return nearest;
}

function setRotation(value) {
  rotating = value;
  $('rotate').setAttribute('aria-pressed', String(value));
  $('rotate').textContent = value ? '公转中' : '已暂停';
}

/* Pointer & Inertial Drag */
canvas.addEventListener('pointerdown', e => {
  targetYaw = null;
  targetPitch = null;
  velYaw = 0;
  velPitch = 0;
  pointer = { x: e.clientX, y: e.clientY, startX: e.clientX, startY: e.clientY, moved: false };
  canvas.setPointerCapture(e.pointerId);
  $('tooltip').classList.add('hidden');
});

canvas.addEventListener('pointermove', e => {
  if (pointer) {
    const dx = e.clientX - pointer.x;
    const dy = e.clientY - pointer.y;
    pointer.moved ||= Math.hypot(e.clientX - pointer.startX, e.clientY - pointer.startY) > 4;
    velYaw = dx * 0.0055;
    velPitch = dy * 0.0045;
    yaw += velYaw;
    pitch = Math.max(-1.05, Math.min(1.05, pitch + velPitch));
    pointer.x = e.clientX;
    pointer.y = e.clientY;
    return;
  }
  const node = hitTest(e);
  hovered = node?.id || null;
  canvas.style.cursor = node ? 'pointer' : 'grab';
  const tip = $('tooltip');
  tip.classList.toggle('hidden', !node || node.id === selected);
  if (node && node.id !== selected) {
    tip.replaceChildren(
      el('strong', '', node.title || '未命名节点'),
      el('small', '', `${NAMES[node.kind] || node.kind}${node.recorded_at ? ' · ' + formatDate(node.recorded_at, true) : ''}`)
    );
    const r = canvas.getBoundingClientRect();
    const x = Math.max(r.left, Math.min(r.right - tip.offsetWidth, e.clientX + 16));
    const y = Math.max(r.top, e.clientY - tip.offsetHeight - 8);
    tip.style.left = `${x}px`;
    tip.style.top = `${y}px`;
  }
});

canvas.addEventListener('pointerup', e => {
  if (pointer && !pointer.moved) {
    const n = hitTest(e);
    if (n) select(n.id, true);
  }
  pointer = null;
});
canvas.addEventListener('pointercancel', () => { pointer = null; });
canvas.addEventListener('pointerleave', () => { hovered = null; $('tooltip').classList.add('hidden'); });

canvas.addEventListener('wheel', e => {
  e.preventDefault();
  zoom = Math.max(0.45, Math.min(3.4, zoom - e.deltaY * 0.001));
}, { passive: false });

canvas.addEventListener('keydown', e => {
  if (e.key === 'ArrowLeft') yaw -= 0.12;
  else if (e.key === 'ArrowRight') yaw += 0.12;
  else if (e.key === 'ArrowUp') pitch = Math.max(-1.05, pitch - 0.1);
  else if (e.key === 'ArrowDown') pitch = Math.min(1.05, pitch + 0.1);
  else if (e.key === '+' || e.key === '=') zoom = Math.min(3.4, zoom + 0.15);
  else if (e.key === '-') zoom = Math.max(0.45, zoom - 0.15);
  else return;
  targetYaw = null;
  e.preventDefault();
});

/* Control Bindings */
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => setMode(btn.dataset.mode));
});

document.querySelectorAll('.kind-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    kind = chip.dataset.kind;
    document.querySelectorAll('.kind-chip').forEach(x => {
      const active = x === chip;
      x.classList.toggle('active', active);
      x.setAttribute('aria-pressed', String(active));
    });
    filterGraph();
  });
});

$('search').addEventListener('input', e => {
  query = e.target.value.trim().toLocaleLowerCase();
  filterGraph();
});

$('ego-toggle').addEventListener('click', () => {
  egoOnly = !egoOnly;
  $('ego-toggle').setAttribute('aria-pressed', String(egoOnly));
  filterGraph();
});

$('pulse-toggle').addEventListener('click', () => {
  neuralPulseOn = !neuralPulseOn;
  $('pulse-toggle').setAttribute('aria-pressed', String(neuralPulseOn));
  if (!neuralPulseOn) ambientPulses = [];
});

$('toggle-left').addEventListener('click', () => setLeftOpen($('left-drawer').classList.contains('collapsed')));
$('toggle-right').addEventListener('click', () => setRightOpen($('right-drawer').classList.contains('collapsed')));
$('close-right').addEventListener('click', () => setRightOpen(false));

$('list-more').addEventListener('click', () => {
  listLimit += BATCH_LIST;
  renderList();
});

$('rotate').addEventListener('click', () => setRotation(!rotating));
$('reset').addEventListener('click', () => {
  targetYaw = -0.34;
  targetPitch = -0.36;
  zoom = 1.08;
});

$('refresh').addEventListener('click', load);
$('empty-refresh').addEventListener('click', () => {
  kind = 'all';
  query = '';
  egoOnly = false;
  $('search').value = '';
  $('ego-toggle').setAttribute('aria-pressed', 'false');
  document.querySelectorAll('.kind-chip').forEach(c => {
    const active = c.dataset.kind === 'all';
    c.classList.toggle('active', active);
    c.setAttribute('aria-pressed', String(active));
  });
  $('time-range').value = '1000';
  updateTimeFilter();
  load();
});

$('time-range').addEventListener('input', () => {
  if (playing) setPlaying(false);
  updateTimeFilter();
  filterGraph();
});

$('time-reset').addEventListener('click', () => {
  if (playing) setPlaying(false);
  $('time-range').value = '1000';
  updateTimeFilter();
  filterGraph();
});

$('time-play').addEventListener('click', () => setPlaying(!playing));

window.addEventListener('keydown', e => {
  const typing = ['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName);
  if (e.key === '/' && !typing) {
    e.preventDefault();
    setLeftOpen(true);
    $('search').focus();
  } else if (e.key === 'Escape' && $('search').value) {
    $('search').value = '';
    query = '';
    filterGraph();
  } else if (e.key === '[' && !typing) {
    setLeftOpen($('left-drawer').classList.contains('collapsed'));
  } else if (e.key === ']' && !typing) {
    setRightOpen($('right-drawer').classList.contains('collapsed'));
  }
});

reducedMotion.addEventListener('change', e => {
  if (!e.matches) return;
  selectionPulse = null;
  layoutMotion = null;
  ambientPulses = [];
  meteors = [];
  birthRipples = [];
  cancelDetailAnimations();
  setRotation(false);
});

/* Boot */
if (compactScreen.matches) {
  setLeftOpen(false);
  setRightOpen(false);
}
compactScreen.addEventListener('change', event => {
  if (event.matches && document.body.matches(':not(.left-closed):not(.right-closed)')) setRightOpen(false);
});
const controlObserver = new ResizeObserver(syncControlInsets);
controlObserver.observe(document.querySelector('.topbar'));
controlObserver.observe($('bottom-dock'));
new ResizeObserver(resize).observe(canvas);
setRotation(rotating);
syncControlInsets();
resize();
requestAnimationFrame(draw);
load();
