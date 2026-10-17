// A throw may curve sideways. Landing on the table takes priority over direction.
export function gestureAction(dx, dy, touch = false, overTable = false) {
  const lift = touch ? 28 : 24;
  if (Math.hypot(dx, dy) < lift) return null;
  if (overTable || dy <= -lift && -dy >= Math.abs(dx) * .25) return 'TRUMP';
  if (dx >= (touch ? 56 : 48) && Math.abs(dy) < dx * .55) return 'DISCARD';
  return null;
}
