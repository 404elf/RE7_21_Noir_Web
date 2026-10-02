// The socket is ordered. A missing baseline always requires a fresh projection.
export function receive(previous, message) {
  let value;
  if (message.type === 'state') value = message;
  else {
    if (!previous) return null;
    if (message.type === 'patch') {
      if (message.base_revision !== previous.revision) return null;
      value = { ...previous, ...message.changes, revision: message.revision,
        players: previous.players.map((p) => ({ ...p, ...message.players.find((v) => v.id === p.id) })),
        events: (message.events_reset ? message.events : [...(previous.events || []), ...message.events]).slice(-80) };
    } else if (message.type === 'clock' && message.revision === previous.revision) value = { ...previous };
    else return null;
  }
  const timing = message.timing || (message.type === 'clock' ? message : null);
  if (timing) {
    value = { ...value, timing, receivedAt: performance.now(),
      paused: timing.paused, clock_active: timing.clock_active,
      preparation_active: timing.preparation_active, reconnect_seconds: timing.reconnect_seconds,
      players: value.players.map((p) => ({ ...p, ...timing.players.find((v) => v.id === p.id) })) };
  }
  return value;
}
