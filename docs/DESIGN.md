# NOIR interface direction

The visual review follows Anthropic's public `frontend-design` skill:
https://github.com/anthropics/skills/blob/main/skills/frontend-design/SKILL.md

## Brief and design plan

A two-person survival card game, seen across a worn table in a dim basement.
The desktop table and quick actions are the baseline. Mobile geometry gives
priority to playable cards, with status kept compact. The headline and physical cards carry the character;
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

Desktop keeps the table on the left and controls on the right. Portrait is the
default mobile layout: opponents above, your cards below, decisions near your
thumb and three trumps per hand page. Landscape places the two players beside
each other and shows six trumps. Never require an orientation lock.

Mobile has one 40px status strip (36px in landscape), replacing the old 124px
target/clock/stakes stack. Untimed games omit clocks. Targets, deck count and
both stakes remain visible; timed games retain both live clocks and their state.
Infrequent draw/surrender controls move into the “更多” dialog. Empty field cards
use a short strip; occupied fields retain readable, paginated effect controls.
Short phones put identities beside cards to give card faces the full row height.
Portrait rows grow with the number of cards. Desktop spacing is preserved.

The mobile review follows two primary references:
- Apple's MARVEL SNAP design interview and portrait gameplay image:
  https://developer.apple.com/news/?id=sosm2p7q
- Blizzard's mobile interface announcement, which describes a dedicated small
  screen interface rather than a scaled desktop layout:
  https://hearthstone.blizzard.com/en-us/news/18648148/hearthstone%C2%AE-now-available-on-mobile-4-14-2015

Borrow hierarchy and touch placement, not their visual identity. NOIR retains
worn paper, damp felt and restrained brass. Avoid additional decorative panels.

Fullscreen uses a user-initiated browser request, follows fullscreenchange, and
keeps the browser exit path available. Unsupported/rejected requests show a
clear home-screen installation alternative. A relative-scope standalone manifest
supports both root and subpath hosting; it does not claim offline support.
Viewport safe areas protect controls in fullscreen and installed mode.
https://developer.mozilla.org/en-US/docs/Web/API/Element/requestFullscreen

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
