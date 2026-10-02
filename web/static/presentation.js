const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
let audio, sound = false, particles = [], frame = null, damageTimer;
const canvas = document.getElementById('particles');
const context = canvas.getContext('2d');

export function toggleSound() {
  sound = !sound;
  if (sound) {
    try { audio ||= new (window.AudioContext || window.webkitAudioContext)(); audio.resume().catch(() => {}); }
    catch { sound = false; }
  }
  const button = document.getElementById('sound-toggle');
  button.textContent = sound ? '音效：开' : '音效：关';
  button.setAttribute('aria-pressed', String(sound));
  tone('card');
}

function tone(kind) {
  if (!sound || !audio || audio.state !== 'running') return;
  const notes = { card: [220, 330], trump: [165, 247, 330], damage: [90, 55], win: [220, 277, 330, 440], loss: [147, 110, 73] }[kind] || [220];
  notes.forEach((frequency, index) => {
    const oscillator = audio.createOscillator(), gain = audio.createGain();
    const start = audio.currentTime + index * .085;
    oscillator.type = kind === 'damage' ? 'triangle' : 'sine';
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(0, start);
    gain.gain.linearRampToValueAtTime(.055, start + .015);
    gain.gain.exponentialRampToValueAtTime(.001, start + .24);
    oscillator.connect(gain); gain.connect(audio.destination);
    oscillator.start(start); oscillator.stop(start + .25);
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
  });
}

export function deal(node, index = 0) {
  if (reduced()) return;
  node.animate([{ opacity: 0, transform: 'translate(65px,-28px) rotate(13deg) scale(.92)' }, { opacity: 1, transform: 'none' }],
    { duration: 460, delay: Math.min(index, 5) * 55, easing: 'cubic-bezier(.16,.7,.3,1)' });
}

function burst(color, x, y) {
  if (reduced() || !context) return;
  for (let i = 0; i < 35; i++) particles.push({ x, y, vx: (Math.random() - .5) * 4, vy: -Math.random() * 4 - 1,
    size: Math.random() * 3 + 1, life: 1, color });
  if (frame === null) frame = requestAnimationFrame(paint);
}

function paint() {
  const dpr = Math.min(devicePixelRatio || 1, 1.5);
  if (canvas.width !== Math.round(innerWidth * dpr) || canvas.height !== Math.round(innerHeight * dpr)) {
    canvas.width = Math.round(innerWidth * dpr); canvas.height = Math.round(innerHeight * dpr);
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0); context.clearRect(0, 0, innerWidth, innerHeight);
  particles = particles.filter((p) => p.life > 0);
  for (const p of particles) {
    p.x += p.vx; p.y += p.vy; p.vy += .07; p.life -= .014;
    context.globalAlpha = Math.max(0, p.life); context.fillStyle = p.color;
    context.beginPath(); context.arc(p.x, p.y, p.size, 0, Math.PI * 2); context.fill();
  }
  context.globalAlpha = 1;
  frame = particles.length ? requestAnimationFrame(paint) : null;
}

export function feedback(previous, next) {
  if (!previous || previous.match_id !== next.match_id || !next.match_id) return;
  const last = previous.events?.at(-1)?.id || 0;
  const fresh = next.events?.filter((event) => event.id > last) || [];
  const mine = next.players.find((p) => p.id === next.pid);
  const old = previous.players.find((p) => p.id === next.pid);
  if (mine.hp < old.hp) {
    if (!reduced()) {
      document.body.classList.remove('damage-flash'); void document.body.offsetWidth;
      document.body.classList.add('damage-flash'); clearTimeout(damageTimer);
      damageTimer = setTimeout(() => document.body.classList.remove('damage-flash'), 750);
      const rect = document.getElementById('my-player').getBoundingClientRect();
      burst('#b54b36', rect.left + rect.width * .6, rect.top + 55);
    }
    tone('damage');
  }
  if (fresh.some((e) => e.event === 'trump')) {
    tone('trump');
    const table = document.querySelector('.table');
    if (!reduced()) { table.classList.remove('event-flash'); void table.offsetWidth; table.classList.add('event-flash'); }
    const rect = document.querySelector('.target-seal').getBoundingClientRect();
    burst('#d5b278', rect.left + rect.width / 2, rect.top + rect.height / 2);
  } else if (fresh.some((e) => ['hit', 'discard'].includes(e.event))) tone('card');
  if (next.phase === 'GAMEOVER' && previous.phase !== 'GAMEOVER') {
    const won = next.winner === next.pid;
    tone(won ? 'win' : 'loss');
    if (won) burst('#e3bc74', innerWidth * .5, innerHeight * .4);
  }
}
