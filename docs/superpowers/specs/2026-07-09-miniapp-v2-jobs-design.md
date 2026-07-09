# Mini-App v2: Jobs System Design

**Date:** 2026-07-09  
**Scope:** DB-backed shared work sessions + Jobs page in mini-app  
**Constraint:** Bot `/work` command must continue working identically for existing users

---

## Problem

The bot's `/work` tap-to-earn system stores active shift state in Python in-memory dicts (`_work_sessions`, `_work_cooldowns`). The mini-app is a separate FastAPI process that can't access that memory. To give the mini-app a Jobs page with a shared session, state must move to SQLite.

---

## Data Layer

### New table: `work_sessions`

```sql
CREATE TABLE IF NOT EXISTS work_sessions (
    user_id         INTEGER PRIMARY KEY,
    taps            INTEGER NOT NULL DEFAULT 0,
    earned          INTEGER NOT NULL DEFAULT 0,
    started_at      INTEGER NOT NULL,
    job_tier_index  INTEGER NOT NULL DEFAULT 0,
    tap_count_start INTEGER NOT NULL DEFAULT 0
);
```

- One row per active shift. Row is deleted when shift ends.
- `tap_count_start`: lifetime `work_count` value at shift start — used to determine tier and promotion detection at end.
- Cooldown is derived from `economy.last_work` (already persisted on shift end via `claim_work`). No separate cooldown column needed.

### DB helper functions to add in `db.py`

```
get_work_session(db_path, user_id) -> dict | None
start_work_session(db_path, user_id, tap_count_start, job_tier_index) -> dict
sync_work_session(db_path, user_id, taps_delta, earned_delta) -> dict
end_work_session(db_path, user_id) -> dict  # returns final taps/earned then deletes row
```

---

## Bot Changes (`handlers/economy.py`)

Behavior is **identical** for Telegram users. Only internals change.

- Remove `_work_sessions: dict` and `_work_cooldowns: dict`
- `cmd_work`:
  - Check active session: `await db.get_work_session(...)` instead of `user.id in _work_sessions`
  - Check cooldown: read `wallet["last_work"]` from economy table, compute `remaining = SHIFT_COOLDOWN - (now - last_work)`
  - Start session: `await db.start_work_session(...)` instead of dict assignment
- `work_callback` tap action:
  - Load session via `await db.get_work_session(...)`
  - After tap: `await db.sync_work_session(..., taps_delta=1, earned_delta=earned_this_tap)`
  - Auto-end at 50 taps as before
- `_end_shift`:
  - Load session from DB, call `await db.end_work_session(...)` to get totals and delete row
  - Continue to call `claim_work` and bump `work_count` as today

The `_shift_message` helper and all bot-facing message text stay exactly the same.

---

## Mini-App API Endpoints (`miniapp/server.py`)

### `GET /api/work/status/{user_id}`

Returns current state for the Jobs page to render.

```json
{
  "session": {
    "taps": 12,
    "earned": 1440,
    "max_taps": 50,
    "started_at": 1720555200
  } | null,
  "cooldown_remaining": 0,
  "job": {
    "title": "🔍 On-Chain Analyst",
    "tier_index": 3,
    "earn_low": 400,
    "earn_high": 800
  },
  "next_job": {
    "title": "⚙️ Protocol Dev",
    "taps_required": 1000,
    "taps_remaining": 87
  } | null,
  "lifetime_taps": 913
}
```

### `POST /api/work/start`

Body: `{"user_id": 123456}`  
Creates a `work_sessions` row. Returns same shape as status.  
Errors: 400 if session already active, 400 if cooldown not expired, 404 if user not found.

### `POST /api/work/sync`

Body: `{"user_id": 123456, "taps_delta": 5, "earned_delta": 3120}`  
Server validation:
- Session must exist
- `session.taps + taps_delta <= 50`
- `earned_delta <= taps_delta * tier_earn_high * 1.1` (10% tolerance for clock skew)

Returns updated session. If taps reach 50, auto-ends shift and returns `{"auto_ended": true, "collected": 8400, "new_balance": 52000}`.

### `POST /api/work/end`

Body: `{"user_id": 123456}`  
Ends shift early. Calls same DB logic as bot's `_end_shift`. Returns `{"collected": X, "new_balance": Y, "taps": N, "promoted": bool, "new_job": "..."}`.

---

## Mini-App UI — Jobs Page

New nav tab: **💼 Work** (5th item). Font stays 11px; tested acceptable on 375px-wide screens.

### State machine (4 states)

**State: no-wallet**  
Same login wall as Games page (user ID input).

**State: ready** (no session, cooldown = 0)  
- Job card: tier emoji + title, earn range per tap, progress bar to next tier
- "Start Shift" primary button

**State: active**  
- Shift header: job title + taps counter (`12 / 50`)
- Progress bar filling left to right
- Large tappable zone: job emoji centered, scale-pulse animation on tap
- Running `Earned: X WRK$` counter updates instantly on each tap (client-side)
- Floating `+X` bubble animates upward from tap point on each tap
- Every 5 taps: silent `POST /api/work/sync` — no loading state shown to user
- "Collect" button (gray, outline style) to end shift early
- If sync fails: queue the failed batch and retry on next sync

**State: cooldown**  
- Job card grayed (opacity 0.5)
- Countdown timer `Next shift in MM:SS`, counts down in real-time client-side
- When timer hits 0: transition to ready state without page reload

### Tap earnings calculation (client-side)

```js
function clientSideEarn(lo, hi) {
  return Math.floor(Math.random() * (hi - lo + 1)) + lo;
}
```

Client tracks `pendingTaps` and `pendingEarned`. On every 5th tap (or on end/collect), flush to server. Server is authoritative — if server returns different totals than client accumulated, use server values.

---

## What Does NOT Change

- `/work`, `/jobs` Telegram commands — identical UX
- `_JOBS` tier list, `_SHIFT_MAX_TAPS = 50`, `_SHIFT_COOLDOWN = 15 * 60` — same constants
- `claim_work` DB function — unchanged
- All existing mini-app pages (Home, Games, Leaderboard, Profile) — untouched

---

## Out of Scope (v2)

- Animated NFT gifts
- Blackjack / Crash in mini-app
- Total bet / total lost leaderboard tab
- Telegram WebApp `initData` auth (mini-app uses user ID input for now)
