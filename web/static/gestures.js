// Directional gestures work near the card; no distant drop target is required.
export function gestureAction(dx, dy, touch = false) {
  if (dy <= -(touch ? 36 : 32) && -dy > Math.abs(dx) * 1.2) return 'TRUMP';
  if (dx >= (touch ? 56 : 48) && Math.abs(dy) < dx * .55) return 'DISCARD';
  return null;
}
