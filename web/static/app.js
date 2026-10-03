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
let pending = null, rendered = -1, reconnectTimer, heartbeat, toastTimer, cooldownTimer;
let closing = false, retry = 0, online = false, cooldown = 0;
let syncing = false;
let handPage = 0, lastReceived = 0;
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
function connection(text) { $('connection').textContent = text; }
function cardInfo(name) { return catalog[name] || { title: name, description: '', category: 'control', english: name }; }
function myPlayer() { return state?.players.find((p) => p.id === state.pid); }
function playerName(pid) { return pid === state?.pid ? '你' : state?.players.find((p) => p.id === pid)?.name || '对手'; }

async function enter(path, settings = null) {
  $('home-error').textContent = '';
  $('create-room').disabled = $('join-room').disabled = true;
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
  finally { $('create-room').disabled = !roomDraft; $('join-room').disabled = false; }
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
  socket = ws;
  ws.onopen = () => {
    if (socket !== ws) return;
    lastReceived = performance.now();
    ws.send(JSON.stringify({ type: 'auth', token: seat.token }));
    clearInterval(heartbeat);
    heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' })); }, 10000);
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
      if (pending !== null && data.revision > pending.revision) pending = null;
      if (JSON.stringify(myPlayer()?.trumps) !== JSON.stringify(next.players.find((p) => p.id === next.pid)?.trumps)) selected = null;
      const previous = state;
      state = next;
      if (data.type === 'state') { syncing = false; pending = null; rendered = -1; }
      connection('已连接 · 私人牌桌');
      render();
      if (data.type === 'patch') feedback(previous, state);
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
  pending = { revision: state.revision, action: action || type, index, started: performance.now(), syncRequested: false };
  const message = { type, revision: state.revision };
  if (action) message.action = action;
  if (index !== undefined) message.index = index;
  socket.send(JSON.stringify(message));
  if (['HIT', 'STAY', 'TRUMP', 'DISCARD'].includes(action)) {
    cooldown = Date.now() + 550;
    clearTimeout(cooldownTimer);
    cooldownTimer = setTimeout(updateControls, 560);
  }
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
  else if (state.phase === 'LOBBY') $('notice').textContent = '把房间码或邀请链接发给朋友，双方准备后开局。';
  else if (state.phase === 'GAMEOVER') $('notice').textContent = '本场结束。双方同意再战后，将开启全新一场。';
  else $('notice').textContent = state.phase === 'RESULT' ? '双方亮牌，正在结算。本局结果会保留在记录中。' : '暗牌只对你可见。每次抽牌或停牌后交接行动。';
  if (rendered !== state.revision) {
    rendered = state.revision;
    if (state.phase === 'LOBBY') renderLobby();
    else renderGame();
  }
  updateClocks(); updateControls();
}

function renderLobby() {
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
  label.append(element('span', p.name.slice(0, 1), 'avatar'), element('span', p.name, 'player-name'), element('span', mine ? 'YOU' : 'OPPONENT', 'player-tag'));
  const stats = element('div');
  stats.append(element('span', `♥ ${p.hp} / ${state.max_hp}`, 'health'));
  const vitality = element('progress', null, 'vitality'); vitality.max = state.max_hp; vitality.value = p.hp; vitality.setAttribute('aria-label', `${p.name} 生命 ${p.hp}`); stats.append(vitality);
  const clock = element('span', '', 'clock'); clock.id = `clock-${p.id}`;
  stats.append(clock); heading.append(label, stats);
  let cards = root.querySelector('.number-cards');
  if (!cards) { cards = element('div', null, 'number-cards'); root.replaceChildren(heading, cards); }
  else root.querySelector('.player-heading').replaceWith(heading);
  const oldCards = new Map([...cards.querySelectorAll('.number-card')].map((c) => [c.dataset.key, c]));
  cards.style.setProperty('--hand-count', p.hand.length);
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
  total.append(element('strong', p.total === null ? '? + ' + p.hand.slice(1).reduce((a, b) => a + b, 0) : String(p.total)), element('small', p.total === null ? `明牌点数 · 王牌 ${p.trump_count}` : p.total > state.target ? '已爆牌 · 可用王牌自救' : `总点数 · 王牌 ${p.trump_count}`));
  oldCards.forEach((card) => card.remove()); cards.querySelector('.total')?.remove();
  cards.append(total); added.forEach((card, i) => deal(card, i));
}

function renderGame() {
  $('round-label').textContent = `ROUND ${String(state.round).padStart(2, '0')}`;
  $('deck-label').textContent = `牌堆剩余 ${state.deck_count} 张`;
  $('target').textContent = state.target;
  $('stake-label').textContent = `对手 / 你的赌注 ${state.players.find((p) => p.id !== state.pid).stake ?? '?'} / ${myPlayer().stake ?? '?'}`;
  state.players.forEach((p) => renderPlayer(p, p.id === state.pid));
  for (const mine of [true, false]) {
    const effects = state.active_trumps.filter((t) => (t.owner === state.pid) === mine).map((t) => {
      const info = cardInfo(t.name);
      const node = element('button', info.title + (t.counter === undefined ? '' : ` ${t.counter}/${t.val}`), `effect ${info.category}`);
      node.title = info.description;
      node.onclick = () => { selectTrump(null); $('selected-title').textContent = info.title; $('selected-description').textContent = info.description; };
      return node;
    });
    $(mine ? 'my-effects' : 'opponent-effects').replaceChildren(...effects);
  }
  const cards = myPlayer().trumps;
  const pageSize = innerWidth <= 760 ? 3 : 6;
  const pageCount = Math.max(1, Math.ceil(cards.length / pageSize));
  handPage = Math.min(handPage, pageCount - 1);
  $('hand-page').textContent = `${handPage + 1}/${pageCount}`;
  $('hand-prev').disabled = handPage === 0;
  $('hand-next').disabled = handPage === pageCount - 1;
  if (selected !== null && selected >= cards.length) selected = null;
  $('trump-count').textContent = `${cards.length} / ${state.rules.game_settings.max_trumps_hand_size}`;
  $('trump-hand').replaceChildren(...cards.slice(handPage * pageSize, (handPage + 1) * pageSize).map(([name], offset) => {
    const index = handPage * pageSize + offset;
    const info = cardInfo(name);
    const button = element('button', null, `trump-card ${info.category} ${selected === index ? 'selected' : ''}`);
    button.dataset.index = index;
    button.setAttribute('aria-pressed', String(selected === index));
    button.setAttribute('aria-label', `${info.title}：${info.description}`);
    const symbols = { attack: '†', guard: '◇', draw: '↗', control: '✣', resource: '∞', target: '◎' };
    button.append(element('span', categories[info.category], 'category'), element('span', symbols[info.category] || '◆', 'trump-art'), element('strong', info.title), element('small', info.english));
    button.onclick = () => { if (suppressClick) return; selectTrump(index); };
    return button;
  }));
  if (!cards.length) $('trump-hand').append(element('p', '暂无王牌。每局开始会按配置补充。', 'muted'));
  updateTrumpDetail();
  renderResult();
  $('events').replaceChildren(...state.events.slice(-16).reverse().map((event) => {
    const node = element('li'); node.append(element('small', `R${event.round}`), document.createTextNode(eventText(event))); return node;
  }));
  $('draw-request').hidden = state.draw_offer !== 3 - state.pid || state.phase !== 'ACTION';
  $('draw-request-text').textContent = `${playerName(3 - state.pid)} 提议平局结束本场。`;
}

function selectTrump(index) {
  selected = index;
  $('trump-hand').querySelectorAll('.trump-card').forEach((card) => {
    const active = Number(card.dataset.index) === index;
    card.classList.toggle('selected', active); card.setAttribute('aria-pressed', String(active));
  });
  updateTrumpDetail(); updateControls();
}

function updateTrumpDetail() {
  const info = selected === null ? null : cardInfo(myPlayer().trumps[selected]?.[0]);
  $('selected-title').textContent = info?.title || '选择一张王牌';
  $('selected-description').textContent = info?.description || '上拉出牌、右拉弃牌，也可点选后使用下方按钮。王牌不结束行动。';
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
  $('table-verdict').hidden = !current;
  if (!result) return;
  const over = state.phase === 'GAMEOVER';
  $('verdict-label').textContent = over ? 'THE FINAL VERDICT' : `ROUND ${String(state.round).padStart(2, '0')} / REVEAL`;
  $('verdict-title').textContent = state.winner === 0 ? 'DRAW' : state.winner === state.pid ? (over ? 'YOU SURVIVED' : 'YOU WIN') : (over ? 'GAME OVER' : 'YOU LOSE');
  $('table-verdict').className = `table-verdict ${state.winner === state.pid ? 'victory' : state.winner ? 'loss' : ''}`;
  $('result-title').textContent = result.winner === 0 ? '平局。' : result.winner === state.pid ? (over ? '你赢得了本场。' : '这一手，你赢了。') : (over ? '本场落败。' : '这一手，对手胜。');
  const reasons = { surrender: '玩家投降', agreement: '双方同意平局', timeout: '整场计时耗尽', preparation_timeout: '首次准备超时', disconnect: '断线超过重连宽限期' };
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
    const elapsed = online && !state.paused && state.phase === 'ACTION' ? (performance.now() - state.receivedAt) / 1000 : 0;
    const active = prep || state.clock_active === p.id && !state.preparation_active;
    const stored = prep ? p.preparation : p.clock;
    const left = stored === null ? null : Math.max(0, stored - (active ? elapsed : 0));
    node.textContent = state.timer.enabled ? `${prep ? '准备 ' : ''}${left === null ? '∞' : Math.ceil(left) + 's'}${state.paused ? ' · 暂停' : ''}` : '';
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
  const turn = action && state.turn === state.pid && Date.now() >= cooldown;
  const lockedDraw = state?.active_trumps?.some((t) => t.owner !== state.pid && ['GAMBLE', 'SILENCE'].includes(t.type));
  const lockedTrump = state?.active_trumps?.some((t) => t.owner !== state.pid && t.type === 'DESTROY_BLOCK');
  $('hit').disabled = !turn || myPlayer()?.total > state.target || state.deck_count === 0 || lockedDraw;
  $('stay').disabled = !turn;
  $('use-trump').disabled = !turn || selected === null || lockedTrump;
  $('discard-trump').disabled = !turn || selected === null;
  $('offer-draw').disabled = !action || Boolean(state.draw_offer);
  $('surrender').disabled = !action;
  $('accept-draw').disabled = $('decline-draw').disabled = !action;
  $('rematch').disabled = !ready || state?.players.some((p) => !p.connected) || myPlayer()?.rematch;
  const labels = { HIT: '抽牌', STAY: '停牌', TRUMP: '出牌', DISCARD: '弃牌', ready: '准备', REMATCH: '再战' };
  if (state && state.phase !== 'LOBBY') $('turn-hint').textContent = !online ? '连接中断 · 正在重连' : syncing ? '正在同步牌桌…' : pending ? `正在确认${labels[pending.action] || '操作'}${pending.syncRequested ? ' · 正在核对状态' : '…'}` : state.paused ? '暂停 · 等待对手重连' : state.phase === 'GAMEOVER' ? '本场结束 · 可选择再战' : state.phase === 'RESULT' ? '双方亮牌 · 正在结算' : state.turn === state.pid ? (Date.now() < cooldown ? '轮到你 · 操作已确认' : myPlayer().stopped ? '轮到你 · 已停牌，仍可使用王牌' : lockedDraw ? '轮到你 · 抽牌被封锁' : '轮到你 · 你的行动') : '对手正在思考…';
  for (const id of ['hit', 'stay', 'use-trump', 'discard-trump']) $(id).setAttribute('aria-busy', String(Boolean(pending)));
  $('trump-hand').querySelectorAll('.trump-card').forEach((card) => card.classList.toggle('pending', pending?.index === Number(card.dataset.index)));
  document.querySelector('.table').classList.toggle('my-turn', Boolean(turn));
  $('table-status').textContent = state?.phase === 'ACTION' ? state.paused ? 'TABLE PAUSED' : state.turn === state.pid ? 'YOUR MOVE' : 'OPPONENT’S MOVE' : 'THE VERDICT';
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
$('customize-room').onclick = () => settingsEditor.open(roomDraft, { save(value) {
  roomDraft = value; $('new-settings-summary').textContent = settingsSummary(value);
} });
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
$('use-trump').onclick = () => send('action', 'TRUMP', selected);
$('discard-trump').onclick = () => send('action', 'DISCARD', selected);
$('surrender').onclick = async () => { if (await confirmMove('就此结束？', '投降将结束本场对局，对手获胜。', '确认投降')) send('action', 'SURRENDER'); };
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
$('trump-hand').addEventListener('pointerdown', (event) => {
  const card = event.target.closest('.trump-card');
  if (!card || !event.isPrimary || event.button !== 0) return;
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
    const button = action === 'TRUMP' ? $('use-trump') : action === 'DISCARD' ? $('discard-trump') : null;
    if (drag.revision === state.revision && button && !button.disabled) button.click();
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
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { endDrag(); suppressClick = false; } });
document.addEventListener('keydown', (event) => {
  if (event.repeat || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('dialog[open]') || ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
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
    $('new-settings-summary').textContent = settingsSummary(roomDraft);
    $('customize-room').disabled = $('create-room').disabled = false;
  } catch (error) { $('home-error').textContent = error.message; }
  try { const saved = JSON.parse(storageRead(seatKey)); if (saved && typeof saved.token === 'string' && /^[A-Z2-9]{8}$/.test(saved.room)) { seat = saved; connect(); } }
  catch { saveSeat(null); }
}
init();
setInterval(updateClocks, 100);
setInterval(checkResponse, 250);
