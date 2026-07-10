# User Feedback — Implementation Batches

Source: feedback from Nookie (2026-07-10). Anonymous numbers deferred to separate update. NFT background accuracy confirmed correct — removed from scope.

---

## Batch A — Polish & Fixes ✅ SPEC + PLAN WRITTEN
> Spec: `docs/superpowers/specs/2026-07-10-batch-a-design.md`
> Plan: `docs/superpowers/plans/2026-07-10-batch-a-implementation.md`

- Blackjack split: require matching ranks (7+7, A+A) not just equal values
- Blackjack dealer card reveal: animated one-by-one with flip on hole card
- Gift card "box" fix: apply `gi.background` color to each card so thumbnail blends in
- Endless profile gift scroll: infinite scroll replacing LIMIT 20
- Gift drag-and-drop reorder: default newest-first, Sortable.js, saved per-user via `sort_index`

---

## Batch B — Visual Game Overhauls
- Blackjack: full casino table interface redesign + Perfect Pair side bet
- Crash: replace rocket emoji with rocket-on-chart/scale animation (more engaging)
- Coin flip: improved animation/visual

---

## Batch C — Four New Games
- Duck Racing
- Plinko
- CS Case Opening
- Wheel of Fortune

---

## Batch D — Profile & Social Layer
- Friends tab: view profiles, send WRK$, trade gifts
- Net worth display per player (balance + gift value combined)
- Profile tags: #1 Pepe holder, #1 Scared Cats holder, #1 net worth, etc.
- Animated gifts while scrolling the profile

---

## Batch E — Multiplayer (Separate Project)
- Poker table: real-time player-vs-player, WebSocket rooms, per-table game state
- Note: significantly larger than all other batches combined — own spec/plan/implementation cycle

---

## Already Completed This Session (2026-07-10)
- Crash WebSocket: `ws://` → `wss://` fix (was showing "Connecting…")
- Rob target tap: fixed onclick escaping bug with data attributes
- Hack reward: now scales 0.5–1.5% of player balance (min 5k–15k, max 500k)
- Roulette wheel animation: SVG wheel, RAF-based spin, correct landing angle
- Roulette lag: replaced CSS animation→transition switch with RAF tracking
- Craps dice: roll animation, dice stay visible 2s after win/lose, ⚀–⚅ faces
- Roulette wrong color: fixed inverted rotation math (360-target not target)
- Rob DM: reveals robber's @username to victim
- Slots rebalance: two-match → push (92.6% RTP, was +29.6% player edge)
- Rob bail penalty: confirmed working as intended (5–15% of robber's balance)
