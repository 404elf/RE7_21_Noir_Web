import { receive } from './transport.js';
import { deal, feedback, toggleSound } from './presentation.js';
import { SettingsEditor, settingsSummary } from './settings.js';
import { gestureAction } from './gestures.js';
const appRoot = new URL('./', import.meta.url);
const storagePrefix = appRoot.pathname === '/' ? '' : appRoot.pathname;
const seatKey = `${storagePrefix}noir-seat`, nameKey = `${storagePrefix}noir-name`;
function inviteURL(room) {
  const url = new URL(appRoot);
  url.searchParams.set('room', room);
  return url;
}
const $ = (id) => document.getElementById(id);
const categories = { attack: '进攻', guard: '防御', draw: '抽牌', control: '干扰', resource: '资源', target: '目标' };
let catalog = {}, state = null, seat = null, socket = null, selected = null;
let pending = null, rendered = -1, reconnectTimer, heartbeat, toastTimer;
let closing = false, retry = 0, online = false;
let inspectedEffect = null, detailAnchor = null;
let syncing = false;
let handPage = 0, fieldPage = 0, lastReceived = 0;
let networkRTT = null, commandSequence = 0, decisionStarted = 0;
const settingsEditor = new SettingsEditor(appRoot);
let roomDraft = null;

function storageRead(key) { try { return sessionStorage.getItem(key); } catch { return null; } }
function saveSeat(value) {
  seat = value;
  try { if (value) sessionStorage.setItem(seatKey, JSON.stringify(value)); else sessionStorage.removeItem(seatKey); }
  catch { toast('浏览器未允许会话存储，关闭页面后将无法重连。'); }
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}
function connection(text) {
  $('connection').textContent = text === '已连接 · 私人牌桌' && networkRTT !== null ? `已连接 · ${networkRTT}ms` : text;
  $('connection').title = networkRTT === null ? '' : `网络往返约 ${networkRTT} 毫秒；计时补偿以服务器测量和额度为准`;
}
function cardInfo(name) { return catalog[name] || { title: name, description: '', category: 'control', english: name }; }
function myPlayer() { return state?.players.find((p) => p.id === state.pid); }
function playerName(pid) { return pid === state?.pid ? '你' : state?.players.find((p) => p.id === pid)?.name || '对手'; }

async function enter(path, settings = null) {
  $('home-error').textContent = '';
  $('create-room').disabled = $('join-room').disabled = $('solo-start').disabled = $('solo-open').disabled = true;
  try {
    const response = await fetch(new URL(path, appRoot), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: $('nickname').value.trim(), ...(settings || {}) }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法进入房间。');
    saveSeat(data);
    try { sessionStorage.setItem(nameKey, $('nickname').value.trim()); } catch { /* Storage is optional. */ }
    history.replaceState(null, '', inviteURL(data.room));
    state = null; rendered = -1; closing = false; retry = 0;
    connect();
  } catch (error) { $('home-error').textContent = error.message; }
  finally { $('create-room').disabled = $('solo-open').disabled = !roomDraft; $('join-room').disabled = $('solo-start').disabled = false; }
}

function connect() {
  if (!seat || closing) return;
  clearTimeout(reconnectTimer);
  $('home').hidden = true; $('room').hidden = false;
  $('room-label').textContent = seat.room;
  $('notice').textContent = retry ? '连接中断，正在恢复席位…' : '正在连接牌桌…';
  connection('连接中');
  const url = new URL(`ws/${encodeURIComponent(seat.room)}`, appRoot);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(url);
  let measuredConnection = false;
  socket = ws;
  networkRTT = null;
  ws.onopen = () => {
    if (socket !== ws) return;
    lastReceived = performance.now();
    ws.send(JSON.stringify({ type: 'auth', token: seat.token }));
    clearInterval(heartbeat);
    heartbeat = setInterval(() => { if (online && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping', measure: true })); }, 10000);
  };
  ws.onmessage = (event) => {
    if (socket !== ws) return;
    const data = JSON.parse(event.data);
    lastReceived = performance.now();
    if (['state', 'patch', 'clock'].includes(data.type)) {
      if (state && data.revision < state.revision) return;
      const next = receive(state, data);
      if (!next) {
        if (!syncing) ws.send(JSON.stringify({ type: 'sync' }));
        syncing = true; updateControls(); return;
      }
      online = true; retry = 0;
      if (!measuredConnection) {
        measuredConnection = true;
        ws.send(JSON.stringify({ type: 'ping', measure: true }));
      }
      const acknowledged = pending && data.action_result?.command_id === pending.command_id;
      if (acknowledged) pending = null;
      if (JSON.stringify(myPlayer()?.trumps) !== JSON.stringify(next.players.find((p) => p.id === next.pid)?.trumps)) selected = null;
      const previous = state;
      state = next;
      if (data.type === 'state') { syncing = false; pending = null; rendered = -1; }
      if (next.phase === 'ACTION' && next.turn === next.pid &&
          (acknowledged || data.type === 'state' || previous?.turn !== next.turn || previous?.phase !== next.phase ||
           previous?.round !== next.round || previous?.match_id !== next.match_id)) decisionStarted = performance.now();
      connection('已连接 · 私人牌桌');
      render();
      if (data.type === 'patch') feedback(previous, state);
    } else if (data.type === 'pong') {
      if (data.probe) ws.send(JSON.stringify({ type: 'probe_ack', probe: data.probe }));
    } else if (data.type === 'network') {
      networkRTT = Math.max(0, Math.round(data.rtt_ms));
      if (state) {
        state.timing = { ...state.timing, lag_allowance_ms: data.lag_allowance_ms };
        if (online) connection('已连接 · 私人牌桌');
      }
    } else if (data.type === 'error') {
      pending = null; toast(data.message); updateControls();
    } else if (data.type === 'retry') {
      online = false; pending = null;
      connection('重连中'); $('notice').textContent = data.message; updateControls();
    } else if (data.type === 'fatal' || data.type === 'closed') {
      closing = true; online = false; pending = null;
      $('notice').textContent = data.message;
      connection('连接已结束'); updateControls();
      toast(data.message);
      // Keep the last table visible; Exit is always available.
      saveSeat(null);
      ws.close();
    }
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    clearInterval(heartbeat);
    online = false; pending = null; updateControls();
    if (!closing && seat) {
      connection('重连中');
      $('notice').textContent = '连接中断，正在重连。对局会暂停，60 秒内恢复可继续。';
      reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** retry++, 5000));
    }
  };
  ws.onerror = () => { connection('网络不可用'); };
}

function send(type, action, index) {
  if (!online || !state || pending !== null || socket?.readyState !== WebSocket.OPEN) return;
  const started = performance.now();
  const command_id = ++commandSequence;
  pending = { revision: state.revision, action: action || type, index, command_id, started, syncRequested: false };
  const message = { type, revision: state.revision, command_id };
  if (['HIT', 'STAY', 'TRUMP', 'DISCARD'].includes(action)) message.think_ms = Math.min(86400000, Math.round(Math.max(0, started-decisionStarted)));
  if (action) message.action = action;
  if (index !== undefined) message.index = index;
  socket.send(JSON.stringify(message));
  updateControls();
}

function reconnect() {
  if (!seat || closing) return;
  online = false; pending = null; syncing = false;
  connection('重连中'); updateControls();
  if (socket && socket.readyState !== WebSocket.CLOSED) socket.close(4000, 'Reconnect');
  else connect();
}

function checkResponse() {
  if (!seat || closing || socket?.readyState !== WebSocket.OPEN) return;
  const now = performance.now();
  if (pending && now - pending.started > 1000) updateControls();
  // Request a current view, never replay a possibly already accepted move.
  if (pending && now - pending.started > 1500 && !pending.syncRequested) {
    pending.syncRequested = true;
    socket.send(JSON.stringify({ type: 'sync' })); updateControls();
  }
  if (now - lastReceived > 25000 || pending && now - pending.started > 12000) reconnect();
}

function render() {
  settingsEditor.refreshRoom({ config: state.rules, timer: state.timer });
  $('home').hidden = true; $('room').hidden = false;
  $('lobby').hidden = state.phase !== 'LOBBY'; $('game').hidden = state.phase === 'LOBBY';
  document.body.classList.toggle('playing', state.phase !== 'LOBBY');
  if (state.paused) $('notice').textContent = `对手连接中断，牌局与计时已暂停。剩余重连时间 ${state.reconnect_seconds} 秒。`;
  else if (state.phase === 'LOBBY') $('notice').textContent = state.ai ? 'AI 已入座，确认规则并准备即可开始。' : '把房间码或邀请链接发给朋友，双方准备后开局。';
  else $('notice').textContent = '';
  $('copy-invite').hidden = !!state.ai;
  $('room-label').textContent = state.ai ? `人机 · ${settingsEditor.options.ai.difficulties[state.ai.difficulty][0]}` : state.room;
  if (rendered !== state.revision) {
    rendered = state.revision;
    if (state.phase === 'LOBBY') renderLobby();
    else renderGame();
  }
  updateClocks(); updateControls();
}

function renderLobby() {
  document.querySelector('#lobby h2').textContent = state.ai ? 'AI 已就位，轮到你了。' : '等你，也等一位对手。';
  $('lobby-players').replaceChildren(...state.players.map((p) => {
    const node = element('div', null, 'lobby-seat');
    node.append(element('strong', p.name), element('span', p.ready ? '已准备' : p.connected ? '已入座 · 等待准备' : '等待连接'));
    return node;
  }));
  $('lobby-settings').textContent = settingsSummary({ config: state.rules, timer: state.timer });
  $('lobby-target').textContent = state.rules.game_settings.target_score;
  $('ready').textContent = myPlayer().ready ? '已确认 · 等待对手' : '确认规则并准备';
  $('edit-room-settings').hidden = state.pid !== 1;
}

function renderPlayer(p, mine) {
  const root = $(mine ? 'my-player' : 'opponent');
  const heading = element('div', null, 'player-heading');
  const label = element('div');
  const name = element('span', mine ? '你' : state.ai ? 'AI 对手' : '对手', 'player-name'); name.title = p.name;
  label.append(element('span', p.name.slice(0, 1), 'avatar'), name, element('span', mine ? 'YOU' : 'OPPONENT', 'player-tag'));
  const stats = element('div');
  stats.append(element('span', `♥ ${p.hp} / ${state.max_hp}`, 'health'));
  const vitality = element('progress', null, 'vitality'); vitality.max = state.max_hp; vitality.value = p.hp; vitality.setAttribute('aria-label', `${p.name} 生命 ${p.hp}`); stats.append(vitality);
  heading.append(label, stats, element('small', `王牌 ${p.trump_count}`, 'trump-inventory'));
  let cards = root.querySelector('.number-cards');
  if (!cards) { cards = element('div', null, 'number-cards'); root.replaceChildren(heading, cards); }
  else root.querySelector('.player-heading').replaceWith(heading);
  const oldCards = new Map([...cards.querySelectorAll('.number-card')].map((c) => [c.dataset.key, c]));
  cards.style.setProperty('--hand-count', p.hand.length);
  const columns = Math.max(1, Math.min(4, p.hand.length));
  cards.style.setProperty('--hand-columns', columns);
  cards.style.setProperty('--hand-rows', Math.ceil(p.hand.length / columns));
  const added = [];
  p.hand.forEach((number, index) => {
    const key = index === 0 ? 'first' : `number-${number}`;
    const existing = oldCards.get(key);
    const card = existing || element('div');
    card.dataset.key = key; oldCards.delete(key);
    card.className = `number-card ${number === null ? 'hidden-card' : index === 0 && mine ? 'private-card' : ''}`;
    card.setAttribute('aria-label', number === null ? '对手暗牌' : `${index === 0 ? '底牌' : '明牌'} ${number}`);
    if (!existing) {
      const inner = element('div', null, 'number-inner');
      const front = element('div', null, 'card-face card-front');
      front.append(element('span', '', 'card-rank'), element('strong', '', 'card-number'), element('span', '', 'card-rank bottom'));
      const back = element('div', null, 'card-face card-back'); back.append(element('strong', '◆'), element('small', 'NOIR / 21'));
      inner.append(front, back); card.append(inner); added.push(card);
    }
    card.querySelectorAll('.card-rank, .card-number').forEach((n) => { n.textContent = number === null ? '' : String(number); });
    const tag = card.querySelector('.card-private');
    if (index === 0 && mine && !tag) card.querySelector('.card-front').append(element('small', 'PRIVATE', 'card-private'));
    cards.append(card);
  });
  const total = element('div', null, `total ${p.total > state.target ? 'bust' : ''}`);
  total.append(element('strong', p.total === null ? '? + ' + p.hand.slice(1).reduce((a, b) => a + b, 0) : String(p.total)), element('small', p.total === null ? '明牌点数' : p.total > state.target ? '已爆牌' : '当前点数'));
  oldCards.forEach((card) => card.remove()); cards.querySelector('.total')?.remove();
  root.querySelector(':scope > .total')?.remove(); root.append(total);
  added.forEach((card) => deal(card));
}

function renderGame() {
  $('round-label').textContent = `ROUND ${String(state.round).padStart(2, '0')}`;
  $('deck-label').textContent = `牌堆剩余 ${state.deck_count} 张`;
  $('target').textContent = state.target;
  state.players.forEach((p) => renderPlayer(p, p.id === state.pid));
  renderMatchStatus();
  renderFieldTrumps();
  document.querySelector('.field-effects').hidden = false;
  if (inspectedEffect && !state.active_trumps.some((t) => t.name === inspectedEffect)) inspectedEffect = null;
  const cards = myPlayer().trumps;
  const pageSize = innerWidth <= 760 ? 3 : 6;
  const pageCount = Math.max(1, Math.ceil(cards.length / pageSize));
  handPage = Math.min(handPage, pageCount - 1);
  $('hand-page').textContent = `${handPage + 1}/${pageCount}`;
  $('hand-prev').disabled = handPage === 0;
  $('hand-next').disabled = handPage === pageCount - 1;
  document.querySelector('.hand-pages').hidden = pageCount === 1;
  if (selected !== null && selected >= cards.length) selected = null;
  $('trump-count').textContent = `${cards.length} / ${state.rules.game_settings.max_trumps_hand_size}`;
  $('trump-hand').replaceChildren(...cards.slice(handPage * pageSize, (handPage + 1) * pageSize).map(([name], offset) => {
    const index = handPage * pageSize + offset;
    const info = cardInfo(name);
    const button = element('button', null, `trump-card ${info.category} ${selected === index ? 'selected' : ''}`);
    button.dataset.index = index;
    button.setAttribute('aria-pressed', String(selected === index));
    button.setAttribute('aria-label', `${info.title}：${info.description}`);
    button.append(element('span', categories[info.category], 'category'), element('strong', info.title), element('small', info.english));
    button.onclick = () => { if (suppressClick) return; selectTrump(index); };
    return button;
  }));
  $('trump-hand').hidden = !cards.length;
  $('gesture-hint').hidden = !cards.length;
  updateTrumpDetail();
  renderResult();
  const latest = [...state.events].reverse().find((e) => e.round === state.round && ['hit', 'stay', 'trump', 'discard', 'timeout'].includes(e.event));
  $('latest-action').textContent = latest ? `最近：${eventText(latest)}` : '第一张牌仅你可见';
  $('events').replaceChildren(...state.events.slice(-16).reverse().map((event) => {
    const node = element('li'); node.append(element('small', `R${event.round}`), document.createTextNode(eventText(event))); return node;
  }));
  $('draw-request').hidden = state.draw_offer !== 3 - state.pid || state.phase !== 'ACTION';
  $('draw-request-text').textContent = `${playerName(3 - state.pid)} 提议平局结束本场。`;
}

function renderFieldTrumps() {
  const container = $('field-trumps');
  const cardWidth = innerWidth <= 760 || innerHeight <= 520 ? 84 : 112;
  const perPage = Math.max(1, Math.floor((container.clientWidth + 6) / (cardWidth + 6)));
  const pages = Math.max(1, Math.ceil(state.active_trumps.length / perPage));
  fieldPage = Math.max(0, Math.min(fieldPage, pages - 1));
  container.replaceChildren(...state.active_trumps.slice(fieldPage * perPage, (fieldPage + 1) * perPage).map((t) => {
    const info = cardInfo(t.name), mine = t.owner === state.pid;
    const node = element('button', null, `effect ${info.category} ${mine ? 'owned' : 'opposing'}`);
    node.append(element('small', mine ? '你的' : '对手'), element('strong', info.title + (t.counter === undefined ? '' : ` ${t.counter}/${t.val}`)));
    node.title = info.description; node.dataset.name = t.name;
    node.onclick = () => { selectTrump(null); inspectedEffect = t.name; detailAnchor = node; updateTrumpDetail(); };
    return node;
  }));
  if (!state.active_trumps.length) container.append(element('span', '暂无场牌', 'empty-effects'));
  $('field-pages').hidden = pages === 1;
  $('field-page').textContent = `${fieldPage + 1}/${pages}`;
  $('field-prev').disabled = fieldPage === 0; $('field-next').disabled = fieldPage === pages - 1;
}

function renderMatchStatus() {
  const players = [...state.players].sort((a, b) => Number(a.id === state.pid) - Number(b.id === state.pid));
  const timer = state.timer;
  $('clock-mode').textContent = !timer.enabled ? '不限时' : timer.mode === 'fischer'
    ? `${timer.initial_minutes ?? '∞'}+${timer.increment_seconds} · 整场`
    : timer.mode === 'round' ? '每人每局' : '每次行动';
  $('match-clocks').replaceChildren(...players.map((p) => {
    const row = element('div', null, 'player-clock'); row.id = `clock-row-${p.id}`;
    const value = element('strong', '', 'clock'); value.id = `clock-${p.id}`;
    const status = element('small'); status.id = `clock-status-${p.id}`;
    row.append(element('span', p.id === state.pid ? '你' : state.ai ? 'AI 对手' : '对手'), value, status); return row;
  }));
  $('stake-label').replaceChildren(...players.map((p) => {
    const row = element('div'); row.append(element('span', p.id === state.pid ? '你' : '对手'), element('strong', String(p.stake ?? '?'))); return row;
  }));
}

function selectTrump(index) {
  selected = index;
  inspectedEffect = null;
  detailAnchor = index === null ? null : $('trump-hand').querySelector(`[data-index="${index}"]`);
  $('trump-hand').querySelectorAll('.trump-card').forEach((card) => {
    const active = Number(card.dataset.index) === index;
    card.classList.toggle('selected', active); card.setAttribute('aria-pressed', String(active));
  });
  updateTrumpDetail(); updateControls();
}

function updateTrumpDetail() {
  const name = selected === null ? inspectedEffect : myPlayer()?.trumps[selected]?.[0];
  const panel = document.querySelector('.trump-detail');
  panel.hidden = !name;
  if (!name) return;
  const info = cardInfo(name);
  $('selected-title').textContent = info.title;
  $('selected-description').textContent = info.description;
  if (innerWidth > 760 && innerHeight > 520) { panel.style.left = panel.style.top = ''; return; }
  detailAnchor = selected === null ? document.querySelector(`.effect[data-name="${name}"]`) : $('trump-hand').querySelector(`[data-index="${selected}"]`);
  if (!detailAnchor) { panel.hidden = true; return; }
  const anchor = detailAnchor.getBoundingClientRect();
  const box = panel.getBoundingClientRect();
  const left = Math.max(8, Math.min(innerWidth - box.width - 8, anchor.left));
  let top = Math.max(8, Math.min(innerHeight - box.height - 8, anchor.top - box.height - 8));
  const controls = document.querySelector(state.phase === 'ACTION' ? '.turn-actions' : '#result-panel').getBoundingClientRect();
  if (left < controls.right && left + box.width > controls.left && top + box.height > controls.top && top < controls.bottom) top = Math.max(8, controls.top - box.height - 8);
  panel.style.left = `${left}px`; panel.style.top = `${top}px`;
}

function eventText(event) {
  const who = playerName(event.pid);
  switch (event.event) {
    case 'round_start': return `新一局开始 · 目标 ${event.target}`;
    case 'hit': return `${who} 抽牌 ${event.drawn?.join('、') || '（牌堆已空）'}`;
    case 'stay': return `${who} ${event.automatic ? '超时自动停牌' : '停牌'}`;
    case 'trump': return `${who} 使用 ${cardInfo(event.card).title}`;
    case 'discard': return `${who} 丢弃一张王牌`;
    case 'result': return `${event.winner ? playerName(event.winner) + ' 赢下本局' : '平局'} · ${event.totals.join(' : ')} · 伤害 ${event.damage}`;
    case 'gameover': return event.winner ? `${playerName(event.winner)} 赢得本场` : '本场平局';
    case 'draw_offer': return `${who} 提出求和`;
    case 'draw_decline': return `${who} 拒绝求和`;
    case 'rematch': return `${who} 请求再战`;
    case 'timeout': return `${who} 行动超时`;
    case 'preparation_timeout': return `${who} 准备超时`;
    default: return event.event;
  }
}

function renderResult() {
  const current = ['RESULT', 'GAMEOVER'].includes(state.phase);
  const result = current ? state : state.last_result;
  $('result-panel').hidden = !current || !result;
  document.querySelector('.turn-actions').hidden = current;
  if (!result) return;
  const over = state.phase === 'GAMEOVER';
  $('result-title').textContent = result.winner === 0 ? '平局。' : result.winner === state.pid ? (over ? '你赢得了本场。' : '这一手，你赢了。') : (over ? '本场落败。' : '这一手，对手胜。');
  const reasons = { surrender: '玩家投降', agreement: '双方同意平局', timeout: '整场计时耗尽', preparation_timeout: '首次准备超时', disconnect: '断线超过重连宽限期' };
  if (over && state.ai?.difficulty === 'nightmare' && result.winner) $('result-title').textContent = result.winner === state.pid ? '极难挑战 · 你活下来了。' : '极难挑战 · 本场落败。';
  $('result-text').textContent = (over && reasons[state.end_reason]) || (result.escape ? '逃脱成功，平局结束整场。' : `${current ? '本局' : `上一局（第 ${result.round} 局）`}伤害 ${result.damage}。${over ? '双方可选择再战。' : '下一局继续争夺。'}`);
  $('rematch').hidden = !over;
  $('rematch').textContent = myPlayer().rematch ? '已请求 · 等待对手' : '再来一场';
}

function updateClocks() {
  if (!state || state.phase === 'LOBBY') return;
  for (const p of state.players) {
    const node = $(`clock-${p.id}`);
    if (!node) continue;
    const prep = state.preparation_active === p.id;
    let elapsed = online && !state.paused && state.phase === 'ACTION' ? (performance.now() - state.receivedAt) / 1000 : 0;
    const active = prep || state.clock_active === p.id && !state.preparation_active;
    const stored = prep ? p.preparation : p.clock;
    const confirming = p.id === state.pid && active && pending && ['HIT', 'STAY', 'TRUMP', 'DISCARD'].includes(pending.action);
    if (confirming) elapsed = Math.max(0, elapsed - Math.min((performance.now()-pending.started)/1000, (state.timing?.lag_allowance_ms || 0)/1000));
    const left = stored === null ? null : Math.max(0, stored - (active ? elapsed : 0));
    const seconds = left === null ? null : Math.ceil(left);
    node.textContent = !state.timer.enabled ? '∞' : seconds === null ? '∞' : `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    $(`clock-status-${p.id}`).textContent = !state.timer.enabled ? '' : state.paused ? '暂停' : confirming ? '确认中' : prep ? '准备' : active ? '计时中' : '等待';
    $(`clock-row-${p.id}`).classList.toggle('active', Boolean(state.timer.enabled && active && !state.paused));
    node.classList.toggle('urgent', left !== null && left <= 10 && active);
  }
  if (state.paused) {
    const left = Math.max(0, Math.ceil((state.reconnect_seconds || 0) - (performance.now() - state.receivedAt) / 1000));
    $('notice').textContent = `对手连接中断，牌局与计时已暂停。剩余重连时间 ${left} 秒。`;
  }
}

function updateControls() {
  const ready = online && !syncing && state && pending === null;
  $('retry-connection').hidden = !seat || closing || online && !syncing && !(pending?.syncRequested);
  $('ready').disabled = !ready || state?.phase !== 'LOBBY' || myPlayer()?.ready;
  $('edit-room-settings').disabled = !ready || state?.settings_locked || state?.pid !== 1;
  $('room-settings').disabled = !state || !settingsEditor.options;
  const action = ready && !state.paused && state.phase === 'ACTION';
  const turn = action && state.turn === state.pid;
  const lockedDraw = state?.active_trumps?.some((t) => t.owner !== state.pid && ['GAMBLE', 'SILENCE'].includes(t.type));
  $('hit').disabled = !turn || myPlayer()?.total > state.target || state.deck_count === 0 || lockedDraw;
  $('stay').disabled = !turn;
  $('offer-draw').disabled = !action || Boolean(state.draw_offer);
  $('surrender').disabled = !action;
  $('accept-draw').disabled = $('decline-draw').disabled = !action;
  $('rematch').disabled = !ready || state?.players.some((p) => !p.connected) || myPlayer()?.rematch;
  const labels = { HIT: '抽牌', STAY: '停牌', TRUMP: '出牌', DISCARD: '弃牌', ready: '准备', REMATCH: '再战' };
  const slowResponse = pending && performance.now() - pending.started > 1000;
  if (state && state.phase !== 'LOBBY') $('turn-hint').textContent = !online ? '连接中断 · 正在重连' : syncing ? '正在同步牌桌…' : slowResponse ? `正在确认${labels[pending.action] || '操作'}${pending.syncRequested ? ' · 正在核对状态' : '…'}` : state.paused ? '暂停 · 等待对手重连' : state.phase === 'GAMEOVER' ? '本场结束 · 可选择再战' : state.phase === 'RESULT' ? '双方亮牌 · 正在结算' : state.turn === state.pid ? (myPlayer().stopped ? '轮到你 · 已停牌，仍可使用王牌' : lockedDraw ? '轮到你 · 抽牌被封锁' : '轮到你 · 你的行动') : '对手的行动';
  for (const [id, move] of Object.entries({ hit: 'HIT', stay: 'STAY' })) $(id).setAttribute('aria-busy', String(pending?.action === move));
  $('trump-hand').querySelectorAll('.trump-card').forEach((card) => {
    const submitting = pending?.index === Number(card.dataset.index);
    card.classList.toggle('pending', submitting);
    card.setAttribute('aria-busy', String(submitting));
    card.setAttribute('aria-keyshortcuts', 'ArrowUp ArrowRight');
    if (submitting) card.dataset.pendingAction = pending.action;
    else delete card.dataset.pendingAction;
  });
  $('my-player').dataset.pendingAction = pending?.action || '';
  document.querySelector('.table').classList.toggle('my-turn', Boolean(turn));
  $('board-turn').textContent = state?.phase === 'ACTION' ? (state.turn === state.pid ? '你的行动' : '对手的行动') : '';
}

function renderCatalog() {
  const query = $('catalog-search').value.trim().toLowerCase();
  $('catalog').replaceChildren(...Object.values(catalog).filter((c) => `${c.name} ${c.title} ${c.description}`.toLowerCase().includes(query)).map((c) => {
    const disabled = state?.enabled_cards?.[c.name] === false;
    const item = element('article', null, disabled ? 'disabled-card' : '');
    item.append(element('h3', c.title), element('small', `${c.english} · ${disabled ? '当前牌池未启用' : categories[c.category]}`), element('p', c.description)); return item;
  }));
}

$('create-room').onclick = () => enter('api/rooms', roomDraft);
$('solo-open').onclick = () => { $('solo-settings').textContent = settingsSummary(roomDraft); $('solo-dialog').showModal(); };
$('solo-close').onclick = () => $('solo-dialog').close();
$('solo-start').onclick = () => { $('solo-dialog').close(); enter('api/rooms', { ...roomDraft, ai: { difficulty: $('ai-difficulty').value, style: $('ai-style').value } }); };
const editDraft = (tab = 'game') => settingsEditor.open(roomDraft, { tab, save(value) {
  roomDraft = value; $('new-settings-summary').textContent = settingsSummary(value);
} });
$('customize-room').onclick = () => editDraft();
$('timer-open').onclick = () => editDraft('timer');
$('room-settings').onclick = () => settingsEditor.open({ config: state.rules, timer: state.timer }, { readonly: true, room: true });
$('edit-room-settings').onclick = () => {
  const code = state.room, revision = state.revision;
  settingsEditor.open({ config: state.rules, timer: state.timer }, { room: true, async save(value) {
    const response = await fetch(new URL(`api/rooms/${encodeURIComponent(code)}/settings`, appRoot), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...value, token: seat.token, revision }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '无法修改规则。');
    toast('规则已更新，双方请重新确认并准备。');
  } });
};
$('join-form').onsubmit = (event) => { event.preventDefault(); const code = $('room-code').value.trim().toUpperCase(); if (!/^[A-Z2-9]{8}$/.test(code)) { $('home-error').textContent = '请输入完整的 8 位房间码。'; return; } enter(`api/rooms/${encodeURIComponent(code)}/join`); };
$('ready').onclick = () => send('ready');
for (const [id, action] of Object.entries({ hit: 'HIT', stay: 'STAY', rematch: 'REMATCH', 'offer-draw': 'DRAW_OFFER', 'accept-draw': 'DRAW_ACCEPT', 'decline-draw': 'DRAW_DECLINE' })) $(id).onclick = () => send('action', action);
document.addEventListener('pointerdown', (event) => {
  if (!event.target.closest('.trump-card, .trump-detail, .effect')) selectTrump(null);
  if (!event.target.closest('#match-menu')) $('match-menu').open = false;
});
$('surrender').onclick = async () => { $('match-menu').open = false; if (await confirmMove('就此结束？', '投降将结束本场对局，对手获胜。', '确认投降')) send('action', 'SURRENDER'); };
$('copy-invite').onclick = async () => {
  const link = inviteURL(state?.room || seat?.room).href;
  try { await navigator.clipboard.writeText(link); toast('邀请链接已复制'); }
  catch { prompt('复制下方邀请链接：', link); }
};
$('leave-room').onclick = async () => {
  if (state?.phase === 'ACTION' && !await confirmMove('离开牌桌？', '离席后无法回到这个座位，未结束的对局将按断线规则判负。', '确认离席')) return;
  closing = true; clearTimeout(reconnectTimer); clearInterval(heartbeat); socket?.close();
  saveSeat(null); state = null; rendered = -1; pending = null; online = false; selected = null;
  $('home').hidden = false; $('room').hidden = true; history.replaceState(null, '', appRoot); connection('双人在线牌局');
  document.body.classList.remove('playing');
};
$('retry-connection').onclick = reconnect;
$('journal-open').onclick = () => $('journal-dialog').showModal();
$('journal-close').onclick = () => $('journal-dialog').close();
for (const [id, delta] of [['hand-prev', -1], ['hand-next', 1]]) $(id).onclick = () => { handPage += delta; renderGame(); updateClocks(); updateControls(); };
for (const [id, delta] of [['field-prev', -1], ['field-next', 1]]) $(id).onclick = () => { fieldPage += delta; renderFieldTrumps(); };
addEventListener('resize', () => { if (state && state.phase !== 'LOBBY') { endDrag(); renderGame(); updateClocks(); updateControls(); } });
$('rules-open').onclick = () => { renderCatalog(); $('rules-dialog').showModal(); };
$('rules-close').onclick = () => $('rules-dialog').close();
$('catalog-search').oninput = renderCatalog;
$('sound-toggle').onclick = toggleSound;

function confirmMove(title, copy, label) {
  const dialog = $('confirm-dialog');
  $('confirm-title').textContent = title; $('confirm-copy').textContent = copy;
  $('confirm-ok').textContent = label; dialog.returnValue = '';
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'yes'), { once: true });
    $('confirm-ok').onclick = () => dialog.close('yes');
    $('confirm-cancel').onclick = () => dialog.close('no');
    dialog.showModal(); $('confirm-cancel').focus();
  });
}

let drag = null, suppressClick = false;
function canUseTrump(action) {
  if (!online || syncing || pending || !state || state.paused || state.phase !== 'ACTION' || state.turn !== state.pid || selected === null) return false;
  return action === 'DISCARD' || action === 'TRUMP' && !state.active_trumps.some((t) => t.owner !== state.pid && t.type === 'DESTROY_BLOCK');
}
$('trump-hand').addEventListener('pointerdown', (event) => {
  const card = event.target.closest('.trump-card');
  if (!card || !event.isPrimary || event.button !== 0) return;
  event.preventDefault(); card.focus({ preventScroll: true });
  drag = { index: Number(card.dataset.index), revision: state.revision, pointer: event.pointerId, touch: event.pointerType !== 'mouse', x: event.clientX, y: event.clientY, card, ghost: null };
  selectTrump(drag.index);
});
function endDrag() {
  if (drag && $('trump-hand').hasPointerCapture(drag.pointer)) $('trump-hand').releasePointerCapture(drag.pointer);
  drag?.ghost?.remove(); drag = null; document.body.classList.remove('dragging');
  $('gesture-hint').textContent = '上拉出牌 · 右拉弃牌 · 点选查看';
  document.querySelectorAll('.drop-hover').forEach((n) => n.classList.remove('drop-hover'));
}
document.addEventListener('pointermove', (event) => {
  if (!drag || event.pointerId !== drag.pointer) return;
  if (!drag.ghost && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 8) return;
  if (!drag.ghost) {
    $('trump-hand').setPointerCapture(drag.pointer);
    drag.ghost = drag.card.cloneNode(true); drag.ghost.classList.add('drag-ghost');
    drag.ghost.setAttribute('aria-hidden', 'true'); drag.ghost.tabIndex = -1;
    document.body.append(drag.ghost); document.body.classList.add('dragging');
    suppressClick = true;
  }
  drag.ghost.style.left = `${event.clientX - 55}px`; drag.ghost.style.top = `${event.clientY - 65}px`;
  const action = gestureAction(event.clientX - drag.x, event.clientY - drag.y, drag.touch);
  $('gesture-hint').textContent = action === 'TRUMP' ? '松手出牌 ↑' : action === 'DISCARD' ? '松手弃牌 →' : '向上出牌 · 向右弃牌';
  event.preventDefault();
});
document.addEventListener('pointerup', (event) => {
  if (!drag || event.pointerId !== drag.pointer) return;
  if (drag.ghost) {
    const action = gestureAction(event.clientX - drag.x, event.clientY - drag.y, drag.touch);
    if (drag.revision === state.revision && action && canUseTrump(action)) send('action', action, selected);
    else if (action) toast($('turn-hint').textContent || '当前不能出牌。');
  }
  endDrag(); setTimeout(() => { suppressClick = false; }, 0);
});
document.addEventListener('pointercancel', () => { endDrag(); suppressClick = false; });
addEventListener('blur', () => { endDrag(); suppressClick = false; });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { endDrag(); suppressClick = false; return; }
  if (!seat || closing) return;
  if (socket?.readyState === WebSocket.CLOSED) connect();
  else checkResponse();
});
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { endDrag(); suppressClick = false; selectTrump(null); } });
document.addEventListener('keydown', (event) => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('dialog[open]') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
  const card = document.activeElement.closest('.trump-card');
  const trumpAction = event.key === 'ArrowUp' ? 'TRUMP' : event.key === 'ArrowRight' ? 'DISCARD' : null;
  if (card && trumpAction) {
    event.preventDefault();
    selectTrump(Number(card.dataset.index));
    if (canUseTrump(trumpAction)) send('action', trumpAction, selected);
    return;
  }
  const button = event.key.toLowerCase() === 'h' ? $('hit') : event.key.toLowerCase() === 's' ? $('stay') : null;
  if (button && !button.disabled) { event.preventDefault(); button.click(); }
});

async function init() {
  const code = new URLSearchParams(location.search).get('room');
  if (code && /^[A-Z2-9]{8}$/i.test(code)) $('room-code').value = code.toUpperCase();
  $('nickname').value = storageRead(nameKey) || '无名旅人';
  try { const response = await fetch(new URL('api/catalog', appRoot)); if (!response.ok) throw new Error(); catalog = await response.json(); }
  catch { toast('图鉴加载失败，请检查网络后刷新。'); }
  try {
    if (!Object.keys(catalog).length) throw new Error('设置加载失败，请刷新后重试。');
    roomDraft = await settingsEditor.init(catalog);
    for (const [kind, options] of Object.entries({ difficulty: settingsEditor.options.ai.difficulties, style: settingsEditor.options.ai.styles })) {
      const select = $(`ai-${kind}`);
      select.replaceChildren(...Object.entries(options).map(([key, row]) => { const option = element('option', row[0]); option.value = key; return option; }));
      select.value = kind === 'difficulty' ? 'normal' : 'swing';
      $(`ai-${kind}-choices`).replaceChildren(...Object.entries(options).map(([key, row]) => {
        const button = element('button', null, 'secondary ai-choice'); button.type = 'button'; button.dataset.value = key;
        button.append(element('strong', row[0]), element('span', row[2]));
        button.onclick = () => { select.value = key; select.onchange(); }; return button;
      }));
    }
    const refreshAI = () => {
      $('ai-style').disabled = $('ai-difficulty').value === 'nightmare';
      $('ai-style-note').textContent = $('ai-style').disabled ? '极难模式不区分打法，与原版一致。' : '';
      for (const kind of ['difficulty', 'style']) {
        const select = $(`ai-${kind}`);
        for (const button of $(`ai-${kind}-choices`).children) {
          button.setAttribute('aria-pressed', String(select.value === button.dataset.value)); button.disabled = select.disabled;
        }
      }
    };
    $('ai-difficulty').onchange = $('ai-style').onchange = refreshAI;
    refreshAI();
    $('new-settings-summary').textContent = settingsSummary(roomDraft);
    $('timer-open').disabled = $('customize-room').disabled = $('create-room').disabled = $('solo-open').disabled = false;
  } catch (error) { $('home-error').textContent = error.message; }
  try { const saved = JSON.parse(storageRead(seatKey)); if (saved && typeof saved.token === 'string' && /^[A-Z2-9]{8}$/.test(saved.room)) { seat = saved; connect(); } }
  catch { saveSeat(null); }
}
init();
setInterval(updateClocks, 100);
setInterval(checkResponse, 250);
