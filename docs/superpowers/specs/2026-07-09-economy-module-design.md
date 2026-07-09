# Economy Module Design — wrkshelperbot

**Date:** 2026-07-09  
**Currency:** WRK$  
**Scope:** Global (shared across all groups)

---

## Overview

A self-contained economy minigame module added to wrkshelperbot. Users earn, gamble, and steal WRK$ across all groups the bot is in. Balances are global — one wallet per Telegram user ID regardless of which group they're playing in.

---

## Data Layer

### New SQLite table: `economy`

```sql
CREATE TABLE IF NOT EXISTS economy (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    full_name   TEXT,
    balance     INTEGER NOT NULL DEFAULT 0,
    streak      INTEGER NOT NULL DEFAULT 0,
    last_daily  INTEGER NOT NULL DEFAULT 0
);
```

- `last_daily` — Unix timestamp of last /daily claim
- `streak` — consecutive daily claim days
- No per-chat scoping; `user_id` is the sole key

### In-memory state (no DB persistence needed)
- Active crash games (per `chat_id`)
- Active blackjack games (per `user_id`)

### New db.py functions
- `get_wallet(user_id)` → dict or None
- `upsert_wallet(user_id, username, full_name)`
- `update_balance(user_id, delta)` — atomic add/subtract
- `get_leaderboard(limit=10)` → list of top balances
- `set_daily(user_id, streak, timestamp)`

---

## Commands

| Command | Who | Description |
|---|---|---|
| `/balance` | Any user | Show your WRK$ balance and streak |
| `/daily` | Any user | Claim daily reward (24hr cooldown) |
| `/rob @user` | Any user | Attempt to rob another user |
| `/slots <bet>` | Any user | Spin the slot machine |
| `/coinflip <bet>` | Any user | 50/50 double or nothing |
| `/dice <bet>` | Any user | Roll dice vs the bot |
| `/blackjack <bet>` | Any user | Card game vs the house |
| `/crash <bet>` | Any user | Start or join a multiplayer crash game |
| `/leaderboard` | Any user | Top 10 WRK$ holders globally |

All gambling commands require sufficient balance. All commands work in any group the bot is in.

---

## Daily Rewards

- Base roll: random integer 500–1500 WRK$
- Cooldown: 24 hours from last claim (not calendar day)
- Streak multipliers:
  - Days 1–6: 1x (base roll)
  - Day 7: 2x
  - Day 14: 3x
  - Day 30+: 4x (permanent until streak breaks)
- Missing a claim by more than 48 hours resets streak to 0
- Bot reply shows amount earned, current streak, and next streak milestone

---

## Robbery

Usage: `/rob @username`

### Success (50% chance)
- Steal random 3–10% of victim's current balance
- Victim must have at least 100 WRK$ to be a valid target

### Failure (50% chance) — three outcomes
| Outcome | Probability | Penalty |
|---|---|---|
| Fine (ran away) | 60% | Lose 50–200 WRK$ flat |
| Bail (caught) | 30% | Lose 5–15% of own balance |
| Clean getaway | 10% | No loss |

### Constraints
- 1 hour cooldown per robber (regardless of outcome)
- Cannot rob someone with less than 100 WRK$
- Cannot rob yourself

---

## Gambling

All gambling commands validate the user has sufficient balance before proceeding. Minimum bet: 10 WRK$.

### Slots `/slots <bet>`
Three reels, each with 7 symbols. Payouts:
- Three 7s: 50x
- Three matching (non-7): 10x
- Two matching: 2x
- No match: lose bet

### Coinflip `/coinflip <bet>`
- 50/50 chance
- Win: double the bet
- Lose: lose the bet
- User can optionally pick heads/tails (cosmetic only, no effect on odds)

### Dice `/dice <bet>`
- User and bot each roll 1d6
- User wins tie-break: user wins 1.8x bet
- Bot wins: user loses bet

### Blackjack `/blackjack <bet>`
- Standard rules: target 21, bust over 21
- Bot is the dealer, hits until 17+
- Blackjack (natural 21): pays 1.5x
- Win: 2x bet returned (1x profit)
- Push (tie): bet returned
- Lose/bust: lose bet
- Inline keyboard buttons: **Hit** / **Stand**
- One active game per user; starting a new one while one is open is rejected

### Crash `/crash <bet>`

**Starting a game:**
1. First user runs `/crash <bet>` — bot posts announcement:
   ```
   🚀 @user has started Crash! Place your bet to join.
   Crash starts in 10...
   [Join Crash]
   ```
2. Countdown ticks down (edited message, every second)
3. Other users click **Join Crash** — bot DMs them: "Reply with your bet amount." Their response registers them.
4. Alternatively, any user in the chat can type `/crash <bet>` during the countdown to join directly.

**During the game:**
- Bot edits the message every 1.5 seconds with the live multiplier:
  ```
  🚀 CRASH LIVE — 2.45x
  Players: @alice (500 WRK$), @bob (1000 WRK$)
  
  [💰 Cash Out @ 2.45x]
  ```
- Clicking **Cash Out** locks in that multiplier for that user; they're removed from the active list
- Crash point is generated at game start using weighted random: heavily weighted toward 1x–5x, rare path to 2500x
- House edge: ~5% (expected return 0.95x)

**Crash distribution (approximate):**
| Range | Probability |
|---|---|
| 1x–2x | 50% |
| 2x–5x | 25% |
| 5x–20x | 15% |
| 20x–100x | 8% |
| 100x–2500x | 2% |

**On crash:**
- Bot edits message: `💥 CRASHED @ 3.21x`
- Cashed-out users receive winnings: `bet × multiplier_at_cashout`
- Remaining users lose their bet
- Summary posted with all outcomes

**Constraints:**
- One active crash game per group at a time
- If starter leaves/bot restarts mid-game, game is cancelled and bets refunded
- Minimum 1 player for crash to start (the initiator)

---

## Leaderboard `/leaderboard`

- Top 10 users globally by WRK$ balance
- Shows rank, name, and balance
- No @mentions (display name only)

---

## File Structure

```
handlers/
  economy.py       ← all economy command handlers + crash/blackjack state
db.py              ← new economy DB functions added alongside existing ones
bot.py             ← register new command handlers
```

All economy logic lives in a single `handlers/economy.py` file. DB functions follow the existing pattern in `db.py`.

---

## Starting Balance

New users receive **1000 WRK$** when they first interact with any economy command (auto-created wallet).

---

## Error Handling

- Insufficient balance: `❌ You don't have enough WRK$. Your balance: X WRK$`
- Invalid bet (non-number, below minimum): `❌ Bet must be at least 10 WRK$`
- Crash already running: `❌ A crash game is already in progress in this chat`
- Rob target too poor: `❌ @user doesn't have enough WRK$ to rob (minimum 100)`
- Blackjack already active: `❌ You already have an active blackjack game`
