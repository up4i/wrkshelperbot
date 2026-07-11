# User Feedback — Implementation Batches

Source: feedback from Nookie + Bryce (2026-07-10 → 2026-07-11).  
Last updated: 2026-07-11

---

## Batch A — Polish & Fixes ✅ IMPLEMENTED

- Blackjack split: require matching ranks (7+7, A+A) not just equal values ✅
- Blackjack dealer card reveal: animated one-by-one with flip on hole card ✅
- Gift card "box" fix: apply `gi.background` color to each card so thumbnail blends in ✅
- Endless profile gift scroll: infinite scroll replacing LIMIT 20 ✅
- Gift drag-and-drop reorder: default newest-first, Sortable.js, saved per-user via `sort_index` ✅
- Gift default order: newest-first (acquired_at DESC as fallback when no sort_index) ✅

---

## Priority Fixes (shipped before Batch B) ✅ IMPLEMENTED

- Hack HTTP 600 ✅
- Commas on numbers ✅
- Leaderboard profile embed removed ✅
- Coinflip same side fix ✅
- Bet button spacing ✅
- Infinite scroll on other user's profile wrong gifts fix ✅
- Gift reorder — drag whole card ✅
- Gift reorder — multi-move stability ✅

---

## Batch B — Game Polish & Visual Overhauls ✅ IMPLEMENTED

- Blackjack: casino felt redesign + chip-based betting UI + Perfect Pair side bet ✅
- Crash: SVG line chart with log-scale, rocket at tip, resets each round ✅
- Coinflip: 4-spin cubic-bezier animation, Flip Again button ✅
- Roulette: correct color sequence, numbers on wheel, betting variants (odd/even/dozen/col), Spin Again ✅
- Street Craps: smoother dice animation (14 frames), Roll Again, 25-roll refund ✅
- High-Low Slider: new standalone game — drag bar, arrow animation, scales payout with risk ✅
- All games: Play Again / Spin Again / Roll Again buttons ✅

---

## Batch C1 — New Solo Games ✅ IMPLEMENTED

- Plinko: 8-row peg board, 3 risk tiers, binomial path, ball animation ✅
- Wheel of Fortune: 12-segment wheel, 95.8% RTP, spin animation ✅
- CS Case Opening: reel animation, loot tiers, gift awards ✅

---

## Batch C2 — Live Multiplayer Games ✅ IMPLEMENTED

- Games tab restructure: Live / Solo / Rob & Hack tabs ✅
- Crash players panel: live bettors list with cashout status ✅
- Duck Racing: 4 ducks, inverse-weight odds, lane selection, race animation ✅
- Marbles: proportional zones, gift bets, winner-takes-pot, SVG board, marble animation ✅
- Live Blackjack: 6-seat table, hit/stand/double, auto-stand, hand legend ✅
- Texas Hold'Em Poker: hand evaluator, betting rounds, showdown, 6-seat, fold/check/call/raise ✅

---

## Batch D — Profile & Social Layer ⬅ NEXT

Items from original spec:
- **Friends tab**: quick access to contacts — send WRK$, trade gifts faster than searching
- **Profile action buttons**: send money + initiate gift trade directly from any player's profile
- **Net worth**: balance + total gift value combined, shown on profile
- **Online presence**: display who's currently active in the mini-app
- **In-game join notifications**: when a user starts Crash (etc.), active members get a mini-app notification — "Nic has started Crash — join now"
- **Profile tags**: #1 Pepe holder, #1 Scared Cats holder, #1 net worth, etc.
- **Customizable stat highlights**: user pins a preferred gamble stat to their profile (largest crash mult, win streak, total wagered, etc.)
- **Animated gifts**: gifts animate while scrolling the profile gift grid

Additional items (not in original spec, accumulated since):
- **Stats expansion**: add game_stats columns for roulette, plinko, wheel, slider, craps, highlow, cases — they currently record no stats
- **Leaderboard gamble totals**: currently only sum slots+coinflip+BJ+crash; expand to include duck/marbles/livebj/poker
- **Gift P2P trading**: `gift_offers` table already exists; needs mini-app UI — browse open offers, make/accept/reject trades
- **/profile bot command**: Telegram slash command showing avatar, balance, leaderboard ranks, pinned gift, streak — same data as mini-app profile but in a formatted bot reply

---

## Batch E — Multiplayer Core ✅ IMPLEMENTED (via C2)

Originally planned as a separate project. Shipped in Batch C2:
- Live Blackjack (6-seat WebSocket table) ✅
- Texas Hold'Em Poker (6-seat WebSocket table, full hand evaluation) ✅
- Duck Racing (4-duck WebSocket race) ✅
- Marbles (proportional-zone WebSocket room with gift wagering) ✅

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
- Spin Again duplicate button bug fix (Batch C2)
- XSS fix: escape player names in crash panel (Batch C2)
- Wheel straight A-2-3-4-5 ranking fix (Batch C2)
- Poker pot written back to wallet at showdown (Batch C2)
- BJ double-down balance check (Batch C2)
- Marble stats recording (Batch C2)
