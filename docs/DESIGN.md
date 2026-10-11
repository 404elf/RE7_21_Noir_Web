# NOIR interface direction

The visual review follows Anthropic's public `frontend-design` skill:
https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md

## Brief and design plan

A two-person survival card game, seen across a worn table in a dim basement.
The established table geometry, card sizes, quick actions and responsive placement
are the source of truth. The headline and physical cards carry the character;
controls and state labels stay quiet and precise.

- Basement black `#10110f`: background.
- Damp felt `#1a1c18`: panels and the table.
- Old paper `#e6ddc9`: text and number cards.
- Worn brass `#c7ad78`: primary actions, target and current turn.
- Dust `#b7ad9a`: secondary text, with sufficient separation from dark surfaces.
- Dried red `#bc725c`: life loss and danger.

Use Georgia / Songti / SimSun for printed cards and the headline; Segoe UI /
Microsoft YaHei for controls and explanatory text. Number values use tabular
figures. Do not introduce remote font or UI dependencies.

Desktop keeps the table on the left and controls on the right. Mobile keeps
status above the table and actions above the hand. Existing alignment and hit
areas stay intact. No extra panel or fake sequence number is needed.

## Review and implementation

The first review found too much club-like sepia, decorative English labels,
a fake `01 / 21` index, repeated survival slogans and continuous card floating.
These competed with play. Replace them with grey-brown materials, a single
unaccented headline, Chinese functional labels and one brief card arrival.
Keep atmosphere in the headline, cards and results; keep errors and actions
literal. The game is still, until a person acts.

Preserve short deal/discard/health/result feedback, visible card values from the
first frame, stable card DOM and keyboard focus. Reduced motion remains
supported. Waiting controls remain readable. Paused, disconnected or syncing
tables must not claim an actionable turn.

Before release, inspect home, table and result screenshots at desktop, narrow
phone and landscape sizes; check keyboard focus, reduced motion, hand gestures,
actual play and action latency. Use the existing regression and deployment gates.
