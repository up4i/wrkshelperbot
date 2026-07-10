# User Feedback — Implementation Batches

Source: feedback from Nookie + Bryce (2026-07-10 → 2026-07-11).

---

## Batch A — Polish & Fixes ✅ IMPLEMENTED

- Blackjack split: require matching ranks (7+7, A+A) not just equal values ✅
- Blackjack dealer card reveal: animated one-by-one with flip on hole card ✅
- Gift card "box" fix: apply `gi.background` color to each card so thumbnail blends in ✅
- Endless profile gift scroll: infinite scroll replacing LIMIT 20 ✅
- Gift drag-and-drop reorder: default newest-first, Sortable.js, saved per-user via `sort_index` ✅
- Gift default order: newest-first (acquired_at DESC as fallback when no sort_index) ✅

---

## Priority Fixes (ship before Batch B)

Quick bugs and one-liners — no spec/plan needed, implement directly.

- **Hack HTTP 600**: some users see HTTP 600 error — investigate server error handling in `/api/hack/*` endpoints
- **Commas on numbers**: all WRK$ amounts should use comma formatting (1,000,000 not 1000000) — check `fmt()` usage and any places it's skipped
- **Leaderboard profile embed**: remove the profile preview/embed that appears on the leaderboard
- **Coinflip same side**: both sides of the coin flip animation show the same face — make heads/tails visually distinct
- **Bet button spacing**: in the new games (roulette, craps, hack, rob, high-low), the preset bet buttons are too close to the confirm/bet button — add spacing
- **Infinite scroll on other user's profile loads wrong gifts**: when viewing another user's profile and scrolling to load more gifts, the scroll loads the wrong user's gifts (likely own user's gifts). `_giftsProfileId` / `window._profileIsOwn` may not be resetting correctly when navigating between profiles while staying on the profile tab
- **Gift reorder — drag whole card**: currently dragging requires hitting the small ⠿ handle in the corner; make the entire gift card the drag target in reorder mode (remove the handle overlay, use the whole card)
- **Gift reorder — multi-move stability**: moving more than one gift in a session bugs out; Sortable state likely not resetting cleanly between moves

---

## Batch B — Game Polish & Visual Overhauls

- **Blackjack**: full casino table interface redesign + Perfect Pair side bet
- **Crash**: replace rocket emoji with a proper rocket-on-chart animation (more engaging)
- **Coinflip**: improved animation (distinct heads/tails, better flip feel)
- **Roulette**:
  - Fix wheel layout — 2 red segments are adjacent (incorrect); correct the color sequence
  - Add numbers to wheel segments
  - Add betting variants: odd/even, red/black, sectors/columns
- **Street Craps**:
  - Smoother dice animation (more frames, better feel)
  - Auto-continue option: play again without re-selecting a bet after each roll
  - Refund if player fails to hit come-out point after N rolls (avoid endless loop)
- **High-Low**: Bryce mentioned a "slider" variant of high-low — clarify if this is a separate game concept (Price is Right style) vs the existing card-chain game. Resolve before implementing.
- **All games**: "Play again with same bet" quick-continue option after each round ends
- **All games**: Spacing fix — preset bet chips/buttons need more padding from the main bet/confirm button

---

## Batch C — Four New Games

- Duck Racing
- Plinko
- CS Case Opening
- Wheel of Fortune
- **Portals / Marble game**: players wager WRK$ or gifts; board tiles proportional to total value wagered; marble launched from random position; whoever's tile it lands on wins the pot. NFT/gift wagering makes it the first game where gifts are at stake. Complexity: high — needs gift valuation, tile layout math, marble physics/animation, and payout logic.

---

## Batch D — Profile & Social Layer

- **Friends tab**: quick access to contacts — send WRK$, trade gifts faster than searching by username
- **Profile action buttons**: send money + initiate gift trade as direct buttons on any player's profile page (not just via friends tab)
- **Net worth**: show balance + total gift value combined on profile
- **Online presence**: display who's currently active in the mini-app
- **In-game join notifications**: when a user starts Crash (or other social games), online members get a mini-app notification — "Nic has started Crash — join now"
- **Profile tags**: #1 Pepe holder, #1 Scared Cats holder, #1 net worth, etc.
- **Customizable stat highlights**: user can pin a preferred gamble stat to their profile (e.g. "largest crash mult" swappable with win streak, total wagered, etc.)
- **Animated gifts**: gifts animate while scrolling the profile gift grid

---

## Batch E — Multiplayer (Separate Project)

- Poker table: real-time player-vs-player, WebSocket rooms, per-table game state
- Note: significantly larger than all other batches combined — own spec/plan/implementation cycle

---

## Already Completed (2026-07-10 → 2026-07-11)

- Crash WebSocket: `ws://` → `wss://` fix
- Rob target tap: fixed onclick escaping bug with data attributes
- Hack reward: now scales 0.5–1.5% of player balance (min 5k–15k, max 500k)
- Roulette wheel animation: SVG wheel, RAF-based spin, correct landing angle
- Roulette lag: replaced CSS animation→transition switch with RAF tracking
- Craps dice: roll animation, dice stay visible 2s after win/lose, ⚀–⚅ faces
- Roulette wrong color: fixed inverted rotation math
- Rob DM: reveals robber's @username to victim
- Slots rebalance: two-match → push (92.6% RTP)
- Rob bail penalty: confirmed working as intended
- BJ split same-rank fix
- BJ dealer card reveal animation
- Gift card background color fix
- Infinite gift scroll
- Gift drag-and-drop reorder
- Infinite scroll observer race fix (profileId captured at setup time)
- Gift reorder guard fix (disconnect observer after last page)
