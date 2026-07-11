# Batch D Design — Profile & Social Layer

**Date:** 2026-07-11  
**Scope:** Stats expansion, gift P2P trading, `/profile` bot command, profile enhancements, social layer, and priority bug fixes.  
**Implementation order:** Priority Fixes → Stats/Leaderboard → Gift P2P Trading → `/profile` bot command → Profile Enhancements → Social Layer

---

## 0. Priority Bug Fixes

These ship first — they're blocking user experience on existing features.

### 0a. Slots / Coinflip — Duplicate Play-Again Button

**Bug:** Using the main Spin / Flip button after a result appends another "Spin Again" / "Flip Again" button each time instead of reusing the existing one.

**Fix:** Before injecting a play-again button, call `document.querySelectorAll('.slots-again-btn')` (and `.coinflip-again-btn`) and remove any existing matches. Same pattern as the C2 T0 fix already applied to other games. Apply to both `initSlotsUI()` and `initCoinflipUI()`.

### 0b. Cases — WRK$ Payout Buff

**Bug:** Non-gift rolls pay 100–10,000 WRK$ on a 75,000 WRK$ case — effectively nothing. Players feel ripped off unless they hit a gift.

**Fix:** Buff `_CASE_LOOT` WRK$ ranges so non-gift outcomes are meaningful:

```python
_CASE_LOOT = [
    (55,  "common",    15_000,  40_000,  None),   # was 100–400
    (80,  "uncommon",  40_000,  80_000,  None),   # was 500–2,000
    (92,  "rare",      80_000, 200_000,  None),   # was 2,000–10,000
    (98,  "epic",           0,       0,  "mid"),
    (100, "legendary",      0,       0,  "high"),
]
```

Expected WRK$ return from non-gift rolls ≈ 46,900 WRK$ vs 75,000 cost. Gifts cover the upside.

### 0c. Plinko — Physics Fix + RTP Rebalance

**Bug:** Current multipliers are center-high (edges lose, center wins), which is backwards from real Plinko physics AND results in heavily player-favorable RTP: low=103%, medium=163%, high=373%. Plinko should pay the edges, not the center.

**Fix:** Replace `_PLINKO_MULTS` with edge-high, center-low values targeting ~95% RTP:

```python
_PLINKO_MULTS = {
    "low":    [2.2, 1.5, 1.2, 0.9, 0.65, 0.9, 1.2, 1.5, 2.2],   # 94.5% RTP
    "medium": [7.0, 2.5, 1.4, 0.7, 0.50, 0.7, 1.4, 2.5, 7.0],   # 96.0% RTP
    "high":   [17,  3.5, 1.5, 0.5, 0.20, 0.5, 1.5, 3.5, 17 ],   # 95.3% RTP
}
```

RTP verified: Σ P(k) * m(k) for binomial(n=8, p=0.5). No frontend changes needed — the existing ball path animation already maps slot index correctly.

### 0d. Live Game Buy-In Refunds (Solo Player)

**Bug:** If a player buys into Live Blackjack or Poker and no other players join, the game eventually resolves solo (auto-stand / fold) and the player loses their buy-in with no real game having occurred.

**Fix:**
- **Live Blackjack**: if a seat is the only occupied seat when the round timer fires, refund the bet to that user and skip the round (don't deal cards). Log as "round skipped — no opponent".
- **Poker**: if only 1 player is seated when the pre-flop timer fires, refund the buy-in chips to their wallet and reset the table. Add a "Leave Table" button visible when seated but no hand is in progress — clicking it refunds the buy-in immediately.
- **Duck Racing**: already partially handled by Marbles solo pattern. If only 1 player has bet when the race fires, refund that bet and cancel the race rather than racing a single duck.
- **Crash**: single-player compatible, no refund needed.
- **Marbles**: solo refund already implemented — no change.

---

## 1. Stats & Leaderboard Expansion

### Goal
Seven games currently record no win/loss stats: roulette, plinko, wheel, slider, craps, highlow, cases. The leaderboard gamble/loss totals also miss the four C2 games (duck, marbles, livebj, poker).

### Schema changes
Add columns to `game_stats` via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the startup migration:

```
roulette_won    INTEGER NOT NULL DEFAULT 0
roulette_lost   INTEGER NOT NULL DEFAULT 0
plinko_won      INTEGER NOT NULL DEFAULT 0
plinko_lost     INTEGER NOT NULL DEFAULT 0
wheel_won       INTEGER NOT NULL DEFAULT 0
wheel_lost      INTEGER NOT NULL DEFAULT 0
slider_won      INTEGER NOT NULL DEFAULT 0
slider_lost     INTEGER NOT NULL DEFAULT 0
craps_won       INTEGER NOT NULL DEFAULT 0
craps_lost      INTEGER NOT NULL DEFAULT 0
highlow_won     INTEGER NOT NULL DEFAULT 0
highlow_lost    INTEGER NOT NULL DEFAULT 0
cases_won       INTEGER NOT NULL DEFAULT 0
cases_lost      INTEGER NOT NULL DEFAULT 0
```

`_record_stats()` in `server.py` gains matching keyword params and `ON CONFLICT DO UPDATE` clauses for each column.

### Stat recording per game
- **Roulette** (`POST /api/play/roulette`): fire on resolution — `roulette_won=delta` on win, `roulette_lost=bet` on loss.
- **Plinko** (`POST /api/play/plinko`): `plinko_won=payout-bet` on win, `plinko_lost=bet` on loss (payout < bet).
- **Wheel** (`POST /api/play/wheel`): `wheel_won=payout-bet` on win, `wheel_lost=bet` on loss.
- **Slider** (`POST /api/play/slider`): `slider_won=payout-bet` on win, `slider_lost=bet` on loss.
- **Craps** (`POST /api/play/craps/roll`): fire only on final resolution (win or loss), not on each roll.
- **High-Low** (`POST /api/play/highlow/cashout` and on wrong guess in `/highlow/guess`): `highlow_won=payout-bet` on cashout, `highlow_lost=bet` on wrong guess.
- **Cases** (`POST /api/play/case`): `cases_won=gift_base_price` if gift awarded, else `cases_won=wrk_reward`; `cases_lost=bet` always (treat as cost of opening).

### Leaderboard fix
Update `_STAT_COLS` in `server.py`:

```python
"gamble": (
    "gs.slots_won+gs.coinflip_won+gs.blackjack_won+gs.crash_won"
    "+gs.duck_won+gs.marbles_won+gs.livebj_won+gs.poker_won"
    "+gs.roulette_won+gs.plinko_won+gs.wheel_won+gs.slider_won"
    "+gs.craps_won+gs.highlow_won+gs.cases_won",
    "WRK$ won"
),
"loss": (
    "gs.slots_lost+gs.coinflip_lost+gs.blackjack_lost+gs.crash_lost"
    "+gs.duck_lost+gs.marbles_lost+gs.livebj_lost+gs.poker_lost"
    "+gs.roulette_lost+gs.plinko_lost+gs.wheel_lost+gs.slider_lost"
    "+gs.craps_lost+gs.highlow_lost+gs.cases_lost",
    "WRK$ lost"
),
```

No frontend changes needed — existing leaderboard tabs pick up the new totals automatically.

---

## 2. Gift P2P Trading

### Goal
Allow users to send gifts/WRK$ to others or propose two-sided trades (gift-for-gift, gift+WRK$-for-gift, etc.) from the mini-app.

### Schema changes
Migrate `gift_offers` table — rename columns and add request-side columns:

```sql
-- Rename existing columns via migration
-- offer_gift_id  (was: instance_id)
-- offer_wrk      (was: wrk_offered)
-- New columns:
request_gift_id  INTEGER  -- gift instance sender wants from recipient (nullable)
request_wrk      INTEGER NOT NULL DEFAULT 0  -- WRK$ sender wants from recipient
```

Migration strategy: `ALTER TABLE gift_offers ADD COLUMN request_gift_id INTEGER` and `ADD COLUMN request_wrk INTEGER NOT NULL DEFAULT 0`. Existing rows treat new columns as NULL/0 (pure gifts — valid).

### Offer types
| Type | offer_gift_id | offer_wrk | request_gift_id | request_wrk |
|---|---|---|---|---|
| Gift donation | set | 0 | NULL | 0 |
| WRK$ donation | NULL | >0 | NULL | 0 |
| Trade (gift for gift) | set | 0 | set | 0 |
| Trade (gift+WRK$ for gift) | set | >0 | set | 0 |
| Trade (gift for gift+WRK$) | set | 0 | set | >0 |
| Buy offer (WRK$ for gift) | NULL | >0 | set | 0 |

### Donation confirmation
If `request_gift_id` is NULL and `request_wrk` is 0, the frontend shows an extra confirmation step before submitting:
> "You're giving [item(s)] away and will receive nothing in return. Confirm?"

### API endpoints
- `GET /api/trades?user_id=` — returns `{incoming: [...], outgoing: [...]}` with offer details + both gift thumbnails
- `POST /api/trades` — create offer `{from_user_id, to_user_id, offer_gift_id?, offer_wrk, request_gift_id?, request_wrk}`
- `POST /api/trades/{id}/accept` — atomic: swap gift owners, transfer WRK$ both ways, reject other pending offers on same gift instances
- `POST /api/trades/{id}/reject` — set status=rejected, bot DM to sender
- `POST /api/trades/{id}/cancel` — sender cancels pending offer

### Accept atomicity
Single SQLite transaction:
1. Verify both gifts still owned by correct parties (re-check inside transaction)
2. Transfer `offer_gift_id` ownership to `to_user_id`
3. Transfer `request_gift_id` ownership to `from_user_id`
4. Deduct `offer_wrk` from `from_user_id`, credit to `to_user_id`
5. Deduct `request_wrk` from `to_user_id`, credit to `from_user_id`
6. Set offer status = accepted
7. Set all other pending offers involving either gift instance = rejected

### Trade offer modal (mini-app)
Opens from "Trade Gift" button on any user's profile.

- **Left panel — Your offer**: gift picker from own inventory (optional) + WRK$ input (optional)
- **Right panel — You want**: gift picker from their inventory (optional, browse their profile grid) + WRK$ input (optional)
- If right panel empty → donation confirmation step before submit

### Trades tab (own profile)
New tab alongside profile tabs.
- **Incoming**: each card shows both sides, Accept / Reject buttons. Bot DM sent to other party on action.
- **Outgoing**: Cancel button. Bot DM sent to recipient on cancel.

### Bot DM notifications
- Offer received: "X offered you [items] in exchange for [items / nothing]"
- Offer accepted: "X accepted your trade offer"
- Offer rejected: "X declined your trade offer"
- Offer cancelled: "X cancelled their trade offer"

---

## 3. `/profile` Bot Command

### Goal
Telegram slash command showing a user's full profile stats as a formatted bot reply.

### Usage
- `/profile` — own profile
- `/profile @username` — another user's profile
- Replying to a user's message and sending `/profile` — that user's profile

### Reply format (HTML, text-only — no photo)
```
👤 @username (Display Name)

💰 Balance: 1,234,567 WRK$
💎 Net Worth: 2,100,000 WRK$
🔥 Streak: 14 days

📊 Ranks
  #3 by Balance
  #1 by Net Worth
  #7 by Gifts Owned

🎁 Pinned Gift: [emoji] Gift Name

⭐ Highlight: 12.4× best crash mult
```

Net worth = balance + sum of `gift_prices.current_price` for all owned gifts (same calc as Section 4).  
Highlight = user's `pinned_stat` (Section 4) or crash best mult as default.  
Leaderboard ranks: simple `SELECT COUNT(*)+1 FROM economy WHERE balance > ?` style rank queries — no stored rank column.

### "View Profile" button
Inline keyboard below the reply:
```python
InlineKeyboardButton(
    text="View Profile in App",
    web_app=WebAppInfo(url=f"https://miniapp.wrk.money?profile={user_id}")
)
```
Mini-app reads `?profile=` on load and navigates to that user's profile page.

### Implementation
New handler in `handlers/economy.py`. DB queries go directly to `wrkshelperbot.db` — no HTTP round-trip to the mini-app server.

---

## 4. Profile Enhancements

### Net worth
Computed at profile load time: `balance + SUM(gift_prices.current_price) WHERE gift_instances.owner_id = user_id`. Shown on profile page hero section alongside balance. Used in leaderboard (new `networth` tab) and `/profile` bot reply.

New `networth` leaderboard tab added directly to the `leaderboard()` endpoint as a special case (not via `_STAT_COLS` — it requires a subquery joining `gift_prices`):

```sql
SELECT e.user_id,
       e.balance + COALESCE(SUM(gp.current_price), 0) AS net_worth,
       ...
FROM economy e
LEFT JOIN gift_instances gi ON gi.owner_id = e.user_id
LEFT JOIN gift_prices gp ON gp.collection = (
    SELECT gm.collection FROM gift_models gm WHERE gm.id = gi.model_id
) AND gp.background = gi.background
GROUP BY e.user_id
ORDER BY net_worth DESC
LIMIT ?
```

Exposed as a new tab in the frontend leaderboard tabs (`setLbTab('networth', this)`).

### Profile tags
Computed at profile load from rank queries. Max 3 tags shown. Examples:
- `#1 [Collection] Holder` — for each collection where user owns the most gifts
- `#1 Net Worth`
- `#1 Crash Mult`

Tags shown as small chips below the user's name on their profile page.

### Customizable stat highlight
New `pinned_stat TEXT` column on `economy` table (migration). Values: `crash_mult`, `gamble_won`, `gamble_lost`, `gifts_owned`, `streak`. Default: `crash_mult`.

Own profile page shows a picker (dropdown or segmented control) to change the pinned stat. All profiles (own and others) display the stat highlight value.

`PATCH /api/profile/stat` endpoint: `{user_id, pinned_stat}` — validates value is in allowed set, updates column.

### Animated gifts
Gift cards on the profile grid animate their Telegram lottie sticker as they scroll into view using `IntersectionObserver`. The existing `/emoji-anim/{emoji_id}` endpoint already serves lottie JSON and `emoji_anim_cache/` already caches the files.

Each gift card checks `gm.custom_emoji_id` — if set, loads the lottie JSON from `/emoji-anim/{emoji_id}` and plays it via **lottie-web** (CDN: `https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js`). Falls back to static emoji rendering if no animated version exists. Animation plays once on scroll-in, loops on hover.

### "View Profile in App" button
Already covered in Section 3 — the mini-app `?profile=` param navigates to the profile on load.

---

## 5. Social Layer

### 5a. Online Presence

New `online_sessions` table:
```sql
CREATE TABLE IF NOT EXISTS online_sessions (
    user_id   INTEGER PRIMARY KEY,
    last_ping INTEGER NOT NULL
)
```

Mini-app calls `POST /api/presence/ping {user_id}` every 30s (using `setInterval`). Endpoint upserts `last_ping = now()`.

"Online" threshold: `last_ping > now() - 60s`.

`GET /api/presence/online` returns list of online user IDs. Used to show green dots on friend avatars in the Social tab and in trade/send flows.

### 5b. Friends System

New `friendships` table:
```sql
CREATE TABLE IF NOT EXISTS friendships (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user_id INTEGER NOT NULL,
    to_user_id   INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | declined
    created_at   INTEGER NOT NULL,
    UNIQUE(from_user_id, to_user_id)
)
```

#### API endpoints
- `GET /api/friends?user_id=` — returns `{friends: [...], incoming: [...], outgoing: [...]}`
- `POST /api/friends/request {from_user_id, to_user_id}` — creates pending row, bot DM to recipient
- `POST /api/friends/{id}/accept` — sets status=accepted, bot DM to requester
- `POST /api/friends/{id}/decline` — sets status=declined
- `DELETE /api/friends/{id}` — remove friendship

#### Social tab (own profile)
Tab alongside Trades tab on own profile page. Two sub-sections:
- **Friends**: accepted friends list with green dot if online, quick "Send WRK$" and "Trade Gift" buttons per friend
- **Requests**: incoming (Accept / Decline) and outgoing (Cancel)

#### Add friend flow
"Add Friend" button on any other user's profile page → sends friend request → bot DM to recipient:
> "X sent you a friend request — open the app to accept"

#### Friends-first in trade/send flows
Trade Gift modal and Send WRK$ flow both show a **Friends** section at the top (with online dots) followed by a search input for non-friends (search by @username or display name via existing user lookup).

### 5c. Profile Action Buttons
On any other user's profile page (not own):
- **Send WRK$** button → opens amount input + confirm flow → atomic balance transfer → bot DM to recipient: "X sent you N WRK$"
- **Trade Gift** button → opens trade modal pre-filled with this user as recipient
- **Add Friend** / **Friend Request Sent** / **Friends** button (state-aware)

### 5d. In-Game Join Notifications

New `/ws/lobby` WebSocket channel. All connected mini-app clients subscribe on load.

When a user joins Crash, Duck Race, Marbles, Live BJ, or Poker, the server broadcasts:
```json
{"type": "join", "game": "crash", "user": "Nic", "user_id": 123}
```

Frontend shows a toast card sliding down from the top:
- Content: *"Nic joined Crash — tap to join"*
- Auto-dismisses after 2s
- Tap navigates to that game's tab/modal
- Only shown to other online users (not the user who joined)
- No bot message sent — mini-app UI only

Toast styling: fixed position, top of screen, z-index above all modals, CSS slide-in/out animation.

---

## Architecture Notes

- All new endpoints follow existing patterns in `miniapp/server.py` (sync SQLite via `db_conn()` context manager)
- All new DB columns added via `try/except ALTER TABLE` in `_startup()`
- New tables created via `CREATE TABLE IF NOT EXISTS` in `_startup()`
- Bot DMs use existing `bot.send_message(chat_id=user_id, ...)` pattern from rob/hack handlers
- Mini-app `?profile=` param: read via `new URLSearchParams(window.location.search).get('profile')` on page load, then call `goProfile(userId)` if set
- `/ws/lobby` reuses the existing WebSocket connection lifecycle pattern from `/ws/crash`

---

## Testing Notes

- Stats: verify each game endpoint increments the correct column after win and loss
- Gift P2P: test atomic accept with concurrent offers on same gift (second accept should fail gracefully)
- `/profile`: test with user who has no gifts, no pinned gift, no game stats
- Friendships: test duplicate request (UNIQUE constraint), self-friend attempt, accept-then-remove cycle
- Leaderboard gamble/loss tabs: confirm totals increase after playing any of the 11 tracked games
