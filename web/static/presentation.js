// Presentation never waits for an animation or changes the authoritative table.
const motion = matchMedia('(prefers-reduced-motion: reduce)');
const soundKey = `${new URL('./', import.meta.url).pathname}noir-sound`;
const $ = (id) => document.getElementById(id);
const animations = new Set(), voices = new Set(), labels = new Set();
const canvas = $('particles'), context = canvas.getContext('2d');
let audio, noise, sound = false, particles = [], frame = null, lastFrame = 0;
let warning = { key: null, seconds: null, fired: new Set() };
try { sound = localStorage.getItem(soundKey) === 'on'; } catch { /* Preferences are optional. */ }

function soundButton() {
  $('sound-toggle').textContent = sound ? '音效：开' : '音效：关';
  $('sound-toggle').setAttribute('aria-pressed', String(sound));
  $('sound-toggle').title = '轻声纸牌与回合提示 · 切到后台自动静音';
}
soundButton();

function unlockAudio() {
  if (!sound || document.hidden) return;
  try {
    audio ||= new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === 'suspended' || audio.state === 'interrupted') audio.resume().catch(() => {});
  } catch { sound = false; soundButton(); }
}
for (const event of ['pointerdown', 'keydown']) document.addEventListener(event, (e) => {
  if (e.isTrusted) unlockAudio();
}, { capture: true });

function muteVoices() {
  for (const source of voices) { try { source.stop(); } catch { /* Already ended. */ } }
  voices.clear();
}

export function toggleSound() {
  sound = !sound;
  try { localStorage.setItem(soundKey, sound ? 'on' : 'off'); } catch { /* Preferences are optional. */ }
  soundButton();
  if (!sound) { muteVoices(); return; }
  unlockAudio();
  // The first opt-in may still be unlocking; don't queue later game sounds.
  audio?.resume().then(() => tone('press')).catch(() => {});
}

function sourceVoice(source, gain, filter, duration, start = audio.currentTime) {
  source.connect(filter || gain);
  if (filter) filter.connect(gain);
  gain.connect(audio.destination);
  voices.add(source);
  source.onended = () => { voices.delete(source); source.disconnect(); gain.disconnect(); filter?.disconnect(); };
  source.start(start); source.stop(start + duration);
}

function note(frequency, volume, duration, offset = 0, type = 'sine', end = frequency) {
  const start = audio.currentTime + offset;
  const source = audio.createOscillator(), gain = audio.createGain();
  source.type = type;
  source.frequency.setValueAtTime(frequency, start);
  source.frequency.exponentialRampToValueAtTime(end, start + duration);
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(volume, start + .004);
  gain.gain.exponentialRampToValueAtTime(.0001, start + duration);
  sourceVoice(source, gain, null, duration + .01, start);
}

function paper(volume, duration, frequency = 1500) {
  if (!noise) {
    noise = audio.createBuffer(1, Math.ceil(audio.sampleRate * .16), audio.sampleRate);
    const data = noise.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  }
  const source = audio.createBufferSource(), gain = audio.createGain(), filter = audio.createBiquadFilter();
  source.buffer = noise;
  filter.type = 'bandpass'; filter.Q.value = .65;
  filter.frequency.setValueAtTime(frequency, audio.currentTime);
  filter.frequency.exponentialRampToValueAtTime(frequency * .55, audio.currentTime + duration);
  gain.gain.setValueAtTime(volume, audio.currentTime);
  gain.gain.exponentialRampToValueAtTime(.0001, audio.currentTime + duration);
  sourceVoice(source, gain, filter, duration);
}

function tone(kind) {
  if (!sound || !audio || audio.state !== 'running' || document.hidden) return;
  // Rapid actions never build an audio backlog or an unbounded mixer.
  if (voices.size > 8) muteVoices();
  switch (kind) {
    case 'press': paper(.025, .025, 1900); break;
    case 'card': paper(.10, .085); note(160, .025, .07, 0, 'triangle', 90); break;
    case 'discard': paper(.07, .09, 2300); break;
    case 'stay': note(190, .035, .08, 0, 'triangle', 115); break;
    case 'trump': paper(.07, .12, 1000); note(247, .025, .16); note(370, .018, .18, .045); break;
    case 'damage': note(100, .055, .15, 0, 'triangle', 45); paper(.045, .075, 450); break;
    case 'turn': note(660, .017, .14); note(880, .012, .14, .045); break;
    case 'warning': note(440, .020, .10); break;
    case 'win': [262, 330, 392].forEach((f, i) => note(f, .030, .22, i * .06)); break;
    case 'loss': [196, 165, 131].forEach((f, i) => note(f, .026, .20, i * .05)); break;
    case 'draw': note(262, .025, .18); note(392, .018, .18, .04); break;
  }
}

function animate(node, frames, duration = 120) {
  if (!node || motion.matches || document.hidden) return;
  const effect = node.animate(frames, { duration, easing: 'cubic-bezier(.2,.7,.3,1)' });
  animations.add(effect);
  effect.finished.then(() => animations.delete(effect), () => animations.delete(effect));
  return effect;
}

export function deal(node) {
  // Values are fully visible from the first frame, including under high RTT.
  animate(node, [
    { transform: 'translate(16px,-8px) rotate(3deg)' },
    { transform: 'none' },
  ]);
}

export function submitted(action, card) {
  if (!['HIT', 'STAY', 'TRUMP', 'DISCARD'].includes(action)) return;
  tone('press');
  if (card) return; // The existing upward/rightward gesture already shows submission.
  const node = $(action === 'HIT' ? 'hit' : 'stay');
  animate(node, [{ boxShadow: 'inset 0 0 0 2px #edd7ac', filter: 'brightness(1.12)' },
    { boxShadow: 'inset 0 0 0 0 transparent', filter: 'none' }]);
}

function accent(node, color = '#d5b278', duration = 220) {
  animate(node, [{ color, textShadow: `0 0 14px ${color}80` },
    { textShadow: '0 0 0 transparent' }], duration);
}

function healthDelta(root, difference) {
  if (motion.matches || document.hidden) return;
  const anchor = root?.querySelector('.player-heading');
  if (!anchor) return;
  const label = document.createElement('span');
  label.className = `health-delta${difference > 0 ? ' gain' : ''}`;
  label.textContent = `${difference > 0 ? '+' : '−'}${Math.abs(difference)}`;
  label.setAttribute('aria-hidden', 'true'); // The updated health already has a label.
  anchor.append(label); labels.add(label);
  const effect = animate(label, [
    { opacity: 1, transform: 'translateY(6px)' },
    { opacity: 1, transform: 'translateY(-5px)', offset: .65 },
    { opacity: 0, transform: 'translateY(-12px)' },
  ], 460);
  const remove = () => { label.remove(); labels.delete(label); };
  effect.finished.then(remove, remove);
}

function burst(node, color, count = 8) {
  if (!node || motion.matches || document.hidden || !context) return;
  const rect = node.getBoundingClientRect();
  const now = performance.now();
  for (let i = 0; i < count; i++) particles.push({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2,
    vx: (Math.random() - .5) * .055, vy: -Math.random() * .04, born: now, life: 340, color });
  particles = particles.slice(-32);
  if (frame === null) { lastFrame = now; frame = requestAnimationFrame(paint); }
}

function paint(now) {
  const dpr = Math.min(devicePixelRatio || 1, 1.5);
  if (canvas.width !== Math.round(innerWidth * dpr) || canvas.height !== Math.round(innerHeight * dpr)) {
    canvas.width = Math.round(innerWidth * dpr); canvas.height = Math.round(innerHeight * dpr);
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, innerWidth, innerHeight);
  const elapsed = Math.min(32, now - lastFrame); lastFrame = now;
  particles = particles.filter((p) => now - p.born < p.life);
  for (const p of particles) {
    p.x += p.vx * elapsed; p.y += p.vy * elapsed; p.vy += .00012 * elapsed;
    context.globalAlpha = .55 * (1 - (now - p.born) / p.life); context.fillStyle = p.color;
    context.fillRect(p.x, p.y, 2, 2);
  }
  context.globalAlpha = 1;
  frame = particles.length ? requestAnimationFrame(paint) : null;
}

export function feedback(previous, next) {
  if (!previous || !next.match_id || document.hidden) return;
  const sameMatch = previous.match_id === next.match_id;
  const last = sameMatch ? previous.events?.at(-1)?.id || 0 : 0;
  const fresh = next.events?.filter((event) => event.id > last) || [];
  if (!fresh.length) return;
  for (const player of next.players) {
    const old = sameMatch && previous.players.find((p) => p.id === player.id);
    const root = $(player.id === next.pid ? 'my-player' : 'opponent');
    if (old && player.hp !== old.hp) healthDelta(root, player.hp - old.hp);
    if (old && player.hp < old.hp) {
      accent(root.querySelector('.health'), '#d76f52', 320);
      animate(root.querySelector('.vitality'), [{ filter: 'brightness(1.8)' }, { filter: 'none' }], 280);
    }
    if (old && player.stake !== old.stake) accent($('stake-label').children[player.id === next.pid ? 1 : 0]);
    if (old && player.total !== old.total) {
      accent(root.querySelector('.total strong'), player.total > next.target ? '#d76f52' : player.total === next.target ? '#efd496' : '#d5b278');
      animate(root.querySelector('.total'), [{ transform: 'translateY(3px) scale(1.04)' }, { transform: 'none' }], 160);
    }
  }
  if (sameMatch && next.target !== previous.target) accent($('target'));
  if (sameMatch) {
    const held = new Map();
    for (const [name] of previous.players.find((p) => p.id === next.pid).trumps || []) held.set(name, (held.get(name) || 0) + 1);
    next.players.find((p) => p.id === next.pid).trumps.forEach(([name], index) => {
      if (held.get(name)) { held.set(name, held.get(name) - 1); return; }
      const card = $('trump-hand').querySelector(`[data-index="${index}"]`);
      animate(card, [{ boxShadow: 'inset 0 0 0 1px #e8c88d' }, { boxShadow: 'inset 0 0 0 0 transparent' }], 180);
    });
  }
  const trump = fresh.findLast((e) => e.event === 'trump');
  if (trump) {
    const effect = [...document.querySelectorAll('.effect')].findLast((node) => node.dataset.name === trump.card &&
      node.classList.contains('owned') === (trump.pid === next.pid));
    animate(effect, [{ boxShadow: 'inset 0 0 0 1px #e8c88d', transform: 'translateY(3px)' },
      { boxShadow: 'inset 0 0 0 0 transparent', transform: 'none' }], 160);
  }
  const result = fresh.findLast((e) => ['result', 'gameover'].includes(e.event));
  const damage = sameMatch && next.players.some((p) => p.hp < previous.players.find((v) => v.id === p.id)?.hp);
  const myTurn = next.phase === 'ACTION' && next.turn === next.pid && !next.paused &&
    (!sameMatch || previous.phase !== 'ACTION' || previous.turn !== next.pid || previous.round !== next.round);
  if (result) {
    const winner = result.winner ?? next.winner;
    tone(winner === 0 ? 'draw' : winner === next.pid ? 'win' : 'loss');
    accent($('result-title'), winner === next.pid ? '#efd496' : winner === 0 ? '#d5b278' : '#d76f52', 320);
    animate($('result-panel'), [{ transform: 'translateY(7px)', opacity: .7 }, { transform: 'none', opacity: 1 }], 220);
    if (next.phase === 'GAMEOVER' && winner === next.pid) burst($('result-title'), '#e3bc74', 16);
  } else if (damage) tone('damage');
  else if (trump) tone('trump');
  else if (fresh.some((e) => e.event === 'hit' || e.event === 'round_start')) tone('card');
  else if (fresh.some((e) => e.event === 'discard')) tone('discard');
  else if (myTurn) tone('turn');
  else if (fresh.some((e) => e.event === 'stay')) tone('stay');
  if (myTurn) {
    accent($('turn-hint'));
    animate($('board-turn'), [{ transform: 'translateX(5px)', opacity: .6 }, { transform: 'none', opacity: 1 }], 180);
    animate($('my-player'), [{ boxShadow: 'inset 2px 0 #e3c087' }, { boxShadow: 'inset 0 0 transparent' }], 280);
  }
}

export function clockFeedback(key, seconds, running, node) {
  if (warning.key !== key) warning = { key, seconds, fired: new Set() };
  if (running && !document.hidden && seconds !== null && warning.seconds !== null) {
    const crossed = [10, 3].filter((limit) => !warning.fired.has(limit) && warning.seconds > limit && seconds <= limit);
    if (crossed.length) {
      crossed.forEach((limit) => warning.fired.add(limit));
      tone('warning'); accent(node, '#d76f52', 160);
    }
  }
  warning.seconds = document.hidden ? null : seconds;
}

export function clearFeedback() {
  // Resume from the current clock, without replaying time elapsed out of view.
  warning.seconds = null;
  for (const effect of animations) effect.cancel();
  animations.clear();
  for (const label of labels) label.remove();
  labels.clear();
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null; particles = [];
  context?.clearRect(0, 0, canvas.width, canvas.height);
  muteVoices();
}
motion.addEventListener('change', () => { if (motion.matches) clearFeedback(); });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { clearFeedback(); audio?.suspend().catch(() => {}); }
  // Only resume an engine already unlocked by trusted input. A saved preference
  // alone must not create audio or replay any sound when a page is restored.
  else if (audio) unlockAudio();
});
addEventListener('pagehide', clearFeedback);
