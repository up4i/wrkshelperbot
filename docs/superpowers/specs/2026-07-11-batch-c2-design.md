# Batch C2 — Live Multiplayer Games Design Spec

**Date:** 2026-07-11  
**Scope:** Live Crash upgrade, tab restructure, Duck Racing, Marbles, Texas Hold'Em Poker, Live Blackjack

---

## Architecture

All multiplayer games follow Crash's existing pattern exactly:
- `_GameState` dataclass holding phase, connections, bets, and game-specific state
- `asyncio` loop launched at startup via `@app.on_event("startup")`
- `/ws/game` WebSocket endpoint for client connections
- `_game_broadcast(msg)` helper sending to all connected sockets

No new infrastructure. Crash's pattern is proven and already production-ready.

**Key paths:**
- Backend: `miniapp/server.py`
- Frontend: `miniapp/static/index.html`

---

## Feature 1: Live Crash — Players Panel

**What's changing:** Crash already has full WebSocket infrastructure (loop, endpoint, frontend). The only missing piece is visibility into other players.

**Backend changes:**
- Add `names: dict[int, str]` to `_CrashState` — populated when a bet is placed (query `economy.name` for the user_id)
- Update `_crash_snapshot()` to include a `players` list: `[{name, bet, cashed_out, mult}]` where `mult` is the cashout multiplier (null if still in, crash point if lost)
- On cashout: store the cashout multiplier in `_crash.bets[uid]["mult"]`

**Frontend changes:**
- Add a players panel below the crash chart
- Each row: colored dot + name + bet amount + status badge (🟡 In · ✅ 2.45× · 💀 Lost)
- Panel updates on every `state` broadcast
- Scrollable if many players

---

## Feature 2: Games Tab Restructure

The games section gains three tabs: **Live | Solo | Rob & Hack**

**Live tab:** Crash, Duck Racing, Marbles, Texas Hold'Em, Live Blackjack  
**Solo tab:** Slots, Coinflip, Blackjack, Craps, Roulette, Slider, Plinko, Wheel, Case Opening  
**Rob & Hack tab:** existing rob/hack cards, unchanged

Tab state is stored in a JS variable, swapping which set of game cards is visible. Active tab highlighted with primary color. Default tab on open: Live.

---

## Feature 3: Duck Racing

**Mechanic:** 4 ducks, random pre-race multipliers, players pick a duck + bet, winner pays `bet × multiplier`. House-edge baked into multiplier pool (~94% RTP).

### Phases
| Phase | Duration | Description |
|-------|----------|-------------|
| waiting | 15s | Odds revealed, bets accepted |
| racing | ~8s | Ducks animate, winner pre-determined |
| finished | 5s | Winner announced, payouts, reset |

### Multiplier Pool
Each round, 4 multipliers are randomly assigned to ducks. Winner probability is `P(duck_i) = (1/mult_i) / sum(1/mult_j)`. The multipliers are chosen so that `sum(P(duck_i) × mult_i) ≈ 0.94` (94% RTP).

Formula: RTP = `N / sum(1/mult_j)` where N=4. Target: `sum(1/mult_j) = 4/0.94 ≈ 4.255`.

Preset pools (each sums to ~4.255 in inverse): `[1.4, 2.2, 3.8, 7.5]`, `[1.5, 2.0, 3.5, 9.0]`, `[1.6, 2.4, 3.2, 6.5]`, `[1.3, 2.8, 4.0, 8.0]`.

Winner probability: `P(duck_i) = (1/mult_i) / sum(1/mult_j for all j)`

### Backend: `_DuckState`
```
phase: str                          # waiting | racing | finished
ducks: list[dict]                   # [{emoji, name, mult}] × 4
bets: dict[int, {duck_idx, bet, name}]
winner_idx: int | None
countdown: float
connections: set[WebSocket]
```

### Backend: `_duck_loop()`
- Waiting: assign random multiplier pool, broadcast state every 0.5s with countdown
- Racing: pick winner via weighted random, broadcast `racing` phase, sleep 8s for animation
- Finished: process payouts (`UPDATE economy SET balance = balance + ? WHERE user_id = ?` for winners), broadcast results, sleep 5s

### Frontend
- SVG race track, 4 lanes, duck emoji per lane
- Odds board showing each duck's multiplier (color-coded: green low, yellow mid, red high)
- Tap duck to select → enter bet → confirm
- Selected duck highlighted during race
- Winner lane flashes gold on finish

### Payout
Winners: `balance += int(bet * mult)`  
Losers: balance already deducted on bet placement (same as Crash)

---

## Feature 4: Marbles

**Mechanic:** Players stake WRK$ or gifts. Each player owns a proportional slice of the board. A marble is launched and bounces until settling in someone's zone. Winner takes the entire pot (WRK$ + all staked gifts).

### Phases
| Phase | Duration | Description |
|-------|----------|-------------|
| open | 20s (+10s ext) | Bets accepted, board reflows live |
| launching | ~6s | Marble animation |
| finished | 5s | Winner announced, pot distributed |

**Minimum players:** 2. If only 1 player has bet when the window closes, extend 10s. If still only 1 after extension, refund that player and reset.

### Gift Bets
- Player can stake a gift from their inventory instead of WRK$
- Gift is removed from `gift_instances` (owner_id set to NULL, marked as staked) on bet placement
- Zone size determined by gift's current market value from `gift_prices` table
- If player wins: they receive all WRK$ in pot + all staked gifts (gift `owner_id` set to winner)
- If player loses: their staked gift transfers to the winner

### Backend: `_MarbleState`
```
phase: str                          # open | launching | finished
bets: dict[int, {
    name: str,
    wrk: int,                       # WRK$ bet (0 if gift bet)
    gift_id: int | None,            # gift instance ID if gift bet
    gift_value: int,                # market value used for zone sizing
    color: str,                     # hex, auto-assigned from palette
    total_value: int                # wrk + gift_value
}]
pot_wrk: int                        # total WRK$ in pot
pot_gifts: list[int]                # gift instance IDs in pot
winner_id: int | None
countdown: float
connections: set[WebSocket]
```

### Winner Determination
```python
total = sum(b["total_value"] for b in bets.values())
roll = random.randint(0, total - 1)
cumulative = 0
for uid, b in bets.items():
    cumulative += b["total_value"]
    if roll < cumulative:
        winner = uid; break
```

### Frontend
- Rectangular SVG board, zones colored per player, reflowing live during open phase
- Each zone labeled with player name + bet amount
- Marble SVG circle animates with bouncing bezier path before settling
- Players panel: color swatch · name · bet · % of board
- On win: winner's zone pulses gold, result banner

### Payouts
Winner receives:
- All WRK$ from the pot: `UPDATE economy SET balance = balance + pot_wrk WHERE user_id = winner`
- All staked gifts: `UPDATE gift_instances SET owner_id = winner WHERE id IN (pot_gifts)`

---

## Feature 5: Texas Hold'Em Poker

**Mechanic:** Standard Texas Hold'Em, global table, max 6 players, fixed 10,000 WRK$ buy-in.

### Buy-in & Chips
- 10,000 WRK$ deducted on join, converted to 10,000 chips (1:1)
- On elimination (0 chips) or leave: remaining chips converted back to WRK$ and credited

### Blinds
- Small blind: 500 chips · Big blind: 1,000 chips
- Blind positions rotate each hand

### Phases
| Phase | Description |
|-------|-------------|
| lobby | Players join, 2+ needed to start |
| pre_flop | 2 hole cards dealt, betting round |
| flop | 3 community cards, betting round |
| turn | 1 community card, betting round |
| river | 1 community card, betting round |
| showdown | Reveal hands, evaluate, pay pot |
| finished | 5s pause, next hand auto-deals |

### Betting Actions
`fold` · `check` · `call` · `raise [amount]`  
30s action timer per player — auto-fold on timeout.

### Hand Evaluator
Python function: takes 7 cards (2 hole + 5 community), returns best 5-card hand rank.

Hand rankings (high → low):
1. Royal Flush
2. Straight Flush
3. Four of a Kind
4. Full House
5. Flush
6. Straight
7. Three of a Kind
8. Two Pair
9. One Pair
10. High Card

### Backend: `_PokerState`
```
phase: str
seats: list[{user_id, name, chips, hole_cards, status, current_bet}]
community: list[str]                # card strings e.g. "A♠"
pot: int
deck: list[str]                     # shuffled 52-card deck
current_seat: int                   # index of active player
min_raise: int
connections: set[WebSocket]
```

Each connected client receives their hole cards only in their own messages. Broadcast sends hole cards as `["?","?"]` for other players until showdown.

### Frontend
- Felt table layout (oval), seats arranged around perimeter
- Your hole cards shown face-up at bottom; others show `🂠 🂠` until showdown
- Community cards center-table
- Chip counts under each player name
- Action bar (your turn only): Fold · Check/Call · Raise input
- **Hand Legend** — collapsible `?` button opens a panel:
  ```
  👑 Royal Flush     — A K Q J 10 same suit
  🌊 Straight Flush  — 5 in sequence, same suit
  4️⃣ Four of a Kind  — four same rank
  🏠 Full House      — three + pair
  ♠  Flush           — 5 same suit
  📈 Straight        — 5 in sequence
  3️⃣ Three of a Kind — three same rank
  2️⃣ Two Pair        — two different pairs
  1️⃣ One Pair        — two same rank
  🃏 High Card       — none of the above
  ```

---

## Feature 6: Live Blackjack

**Mechanic:** Multiplayer Blackjack — all players vs dealer. Each player plays their own hand independently. Uses existing hand logic from solo BJ.

### Buy-in & Chips
- No buy-in — players bet per round from their WRK$ balance (same as solo BJ)
- Max 6 players at the table

### Phases
| Phase | Duration | Description |
|-------|----------|-------------|
| waiting | 10s | Betting window, countdown |
| dealing | instant | 2 cards to each player + dealer (1 up, 1 hole) |
| player_turns | 30s/player | Each player acts in seat order |
| dealer | auto | Dealer reveals hole card, hits to 17+ |
| results | 5s | Payouts, next round |

### Player Actions
`hit` · `stand` · `double` (doubles bet, one card, auto-stand)  
Auto-stand after 30s timeout.

### Backend: `_LiveBJState`
```
phase: str
seats: list[{user_id, name, bet, hand, status, doubled}]
dealer_hand: list[str]
dealer_hole_shown: bool
deck: list[str]
current_seat: int
connections: set[WebSocket]
```

### Payouts (same as solo BJ)
- Win: `+bet`
- Blackjack (natural): `+bet × 1.5`
- Push: `0`
- Lose: `-bet`
- Dealer bust: all non-bust players win

### Frontend
- Felt table layout matching Poker aesthetic
- Each seat shows player name, bet, and their cards
- Your seat highlighted, action buttons appear on your turn
- Dealer hand shown at top with hole card face-down during player turns
- Other players' hands visible (face-up) — you can see what others have
- Hand Legend (same style as Poker) — collapsible `?` for BJ hand values (Ace=1or11, face=10, etc.)

---

## Database Changes

| Table | Change |
|-------|--------|
| `economy` | No changes needed |
| `gift_instances` | Add `staked BOOLEAN DEFAULT 0` — set when gift is in a Marbles pot |
| `game_stats` | Add columns: `duck_won`, `duck_lost`, `marbles_won`, `marbles_lost`, `poker_won`, `poker_lost`, `live_bj_won`, `live_bj_lost` |

No new tables required.

---

## Implementation Order

1. Games tab restructure (frontend only, no backend)
2. Live Crash players panel (small backend + frontend change)
3. Duck Racing backend → Duck Racing frontend
4. Marbles backend → Marbles frontend
5. Live Blackjack backend → Live Blackjack frontend
6. Texas Hold'Em backend → Texas Hold'Em frontend (largest, last)
