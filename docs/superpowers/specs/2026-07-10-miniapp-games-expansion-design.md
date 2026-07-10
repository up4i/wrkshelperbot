# Mini-App Games Expansion Design
**Date:** 2026-07-10  
**Scope:** Add Roulette, Street Craps, High-Low, Rob, and Hack to the wrkshelperbot mini-app

---

## Overview

Add 5 new game/crime features to the mini-app at `miniapp.wrk.money`. All features appear under the Games tab. Rob and Hack are migrated from pure bot commands to full mini-app UIs, with shared state between bot and mini-app via SQLite.

---

## 1. New Games

### Roulette
- **Bet types:** Red (18/38, 2× payout), Black (18/38, 2×), Green (2/38, 14×)
- **Single roll per bet.** Instant result.
- **UI:** Color picker buttons, spin animation, result display. Matches existing modal style.
- **Endpoint:** `POST /api/play/roulette {user_id, bet, color}`

### Street Craps (Pass Line)
- **Come-out roll:** 7 or 11 = win, 2/3/12 = lose, else sets the point
- **Point phase:** Keep rolling until point hit (win) or 7 rolled (lose)
- **Bet locked at start.** No additional input during point phase — just a Roll button.
- **Session stored** in `craps_sessions` table so page refreshes don't lose state.
- **UI:** Dice display, phase indicator (Come-Out / Point: N), Roll button
- **Endpoints:**
  - `POST /api/play/craps/start {user_id, bet}` — deducts bet, creates session
  - `POST /api/play/craps/roll {user_id}` — rolls dice, resolves or continues

### High-Low
- **Flow:** Place bet → card revealed → guess Higher or Lower → chain or cash out
- **Multiplier:** 1.5× per correct guess (1.5 → 2.25 → 3.38 → 5.06 → …)
- **Cards:** Rank 1–13 (Ace=1, King=13). Equal rank on next card = loss.
- **Wrong guess** = lose entire original bet. Cash out any time after a correct guess.
- **Session stored** in `highlow_sessions` table.
- **UI:** Card display, Higher/Lower buttons, multiplier indicator, Cash Out button
- **Endpoints:**
  - `POST /api/play/highlow/start {user_id, bet}` — deducts bet, deals first card
  - `POST /api/play/highlow/guess {user_id, direction}` — `higher` or `lower`
  - `POST /api/play/highlow/cashout {user_id}` — credits winnings, ends session

---

## 2. Rob (Mini-App)

- **Target selection:** Scrollable list of players sorted by balance (from `/api/leaderboard`), self excluded. Tap to select → confirm screen → attempt.
- **Outcome:** Shown in-app with same flavor text as bot command. Victim receives a **bot DM** via Telegram API (`sendMessage` to `victim_id`) with the outcome message. No group post.
- **Cooldown:** 15 minutes, enforced via `economy.last_rob` column (shared with bot — same cooldown whether robbed via bot or mini-app).
- **Endpoints:**
  - `GET /api/rob/targets` — returns leaderboard-style player list (excludes requester)
  - `POST /api/rob/attempt {user_id, target_id}` — executes rob, sends victim DM

---

## 3. Hack (Mini-App)

- **Flow:** Start hack → clue + letter blanks shown → type guesses → reveal letters on wrong guess → win or fail after 5 attempts
- **Session shared with bot:** Active hack in the bot shows in the mini-app and vice versa (same `hack_sessions` table).
- **Cooldown:** 1 hour, enforced via `economy.last_hack` column.
- **UI:** Clue text, blank display (`_ _ _ _ _` with revealed letters), attempt counter, reward amount, text input + Guess button
- **Endpoints:**
  - `GET /api/hack/status/{user_id}` — returns active session or cooldown state
  - `POST /api/hack/start {user_id}` — starts new session (checks cooldown)
  - `POST /api/hack/guess {user_id, word}` — submits guess, returns result

---

## 4. Database Changes

All migrations run in `_startup()` with `ALTER TABLE ... IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS` pattern.

```sql
-- Shared cooldown columns on economy table
ALTER TABLE economy ADD COLUMN last_rob  INTEGER DEFAULT 0;
ALTER TABLE economy ADD COLUMN last_hack INTEGER DEFAULT 0;

-- Active craps sessions
CREATE TABLE IF NOT EXISTS craps_sessions (
    user_id    INTEGER PRIMARY KEY,
    bet        INTEGER NOT NULL,
    point      INTEGER,           -- NULL = still on come-out roll
    started_at INTEGER NOT NULL
);

-- Active high-low sessions
CREATE TABLE IF NOT EXISTS highlow_sessions (
    user_id      INTEGER PRIMARY KEY,
    bet          INTEGER NOT NULL,
    current_card INTEGER NOT NULL,
    multiplier   REAL    NOT NULL DEFAULT 1.0,
    started_at   INTEGER NOT NULL
);

-- Active hack sessions (replaces in-memory _hack_games dict)
CREATE TABLE IF NOT EXISTS hack_sessions (
    user_id          INTEGER PRIMARY KEY,
    word             TEXT    NOT NULL,
    clue             TEXT    NOT NULL,
    reward           INTEGER NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 5,
    revealed_indices TEXT    NOT NULL DEFAULT '0',  -- comma-separated
    started_at       INTEGER NOT NULL
);
```

---

## 5. Bot Handler Changes

- `cmd_rob`: Read/write `economy.last_rob` instead of `_rob_cooldowns` dict
- `cmd_hack`: Read/write `economy.last_hack` instead of `_hack_cooldowns` dict; use `hack_sessions` table instead of `_hack_games` dict
- `cmd_guess`: Use `hack_sessions` table

These changes keep bot and mini-app fully in sync with no duplicated state.

---

## 6. DM Notification for Rob Victims

The mini-app server sends DMs directly via Telegram Bot API (same pattern as emoji proxy — `urllib.request` POST to `sendMessage`). Uses `config.BOT_TOKEN`. No bot instance needed.

```
POST https://api.telegram.org/bot{token}/sendMessage
{chat_id: victim_id, text: outcome_message}
```

---

## 7. UI Placement

- All 5 features appear as cards in the **Games grid** (existing `gamesGrid`)
- Each opens a modal matching the existing slot/coinflip/blackjack style
- Cooldown shown inline on the modal if active (timer countdown)
- Games grid goes from 4 cards to 9 cards total

---

## 8. Implementation Order

1. DB migrations + bot handler updates (shared state foundation)
2. Roulette (simplest — single endpoint, no session)
3. High-Low (session-based, no social component)
4. Street Craps (session-based, two-phase)
5. Hack (session + shared bot state)
6. Rob (session + DM notification + target list)
