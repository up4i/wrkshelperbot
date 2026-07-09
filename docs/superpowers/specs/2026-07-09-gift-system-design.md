# Gift System Design — wrkshelperbot

**Date:** 2026-07-09
**Status:** Approved

---

## Overview

A collectible gift system layered on top of the WRK$ economy. Each gift is a unique 1/1 item (collection + model + background combination). Players acquire gifts through daily drops or bank purchases, and trade them with other players or the bank. Prices fluctuate daily based on drift and supply/demand. Crafted model variants are excluded from this phase.

---

## Tier Classification

Sub-tiers are noted in the catalog for pricing granularity but do not affect gameplay logic — only `low`, `mid`, `high` are stored in the DB.

### High Tier (14 collections)
**Top high:** Durov's Cap · Heart Locket · Plush Pepe · Scared Cat
**Standard high:** Artisan Brick · Astral Shard · Heroic Helmet · Mighty Arm · Nail Bracelet · Precious Peach · Rare Bird
**Low-high:** Kissed Frog · Neko Helmet · Westside Sign

### Mid Tier (27 collections)
**High-mid:** Ionic Dryer · Swiss Watch · Vintage Cigar
**Standard mid:** Bonded Ring · Crystal Ball · Cupid Charm · Diamond Ring · Electric Skull · Eternal Rose · Ion Gem · Khabib's Papakha · Loot Bag · Love Potion · Low Rider · Mad Pumpkin · Mini Oscar · Perfume Bottle · Record Player · Sharp Tongue · Signet Ring · UFC Strike
**Low-mid:** Gem Signet · Magic Potion · Snoop Cigar · Top Hat · Trapped Heart · Voodoo Doll

### Low Tier (71 collections)
All remaining collections: B-Day Candle · Berry Box · Big Year · Bling Binky · Bow Tie · Bunny Muffin · Candy Cane · Chill Flame · Clover Pin · Cookie Heart · Desk Calendar · Easter Egg · Eternal Candle · Evil Eye · Faith Amulet · Flying Broom · Fresh Socks · Genie Lamp · Ginger Cookie · Hanging Star · Happy Brownie · Hex Pot · Holiday Drink · Homemade Cake · Hypno Lollipop · Ice Cream · Input Key · Instant Ramen · Jack-in-the-Box · Jelly Bunny · Jester Hat · Jingle Bells · Jolly Chimp · Joyful Bundle · Liberty Figure · Light Sword · Lol Pop · Love Candle · Lush Bouquet · Money Pot · Mood Pack · Moon Pendant · Mousse Cake · Party Sparkler · Pet Snake · Pool Float · Pretty Posy · Restless Jar · Sakura Flower · Santa Hat · Skull Flower · Sky Stilettos · Sleigh Bell · Snake Box · Snoop Dogg · Snow Globe · Snow Mittens · Spiced Wine · Spring Basket · Spy Agaric · Star Notepad · Stellar Rocket · Surge Board · Swag Bag · Tama Gadget · Timeless Book · Toy Bear · Valentine Box · Vice Cream · Victory Medal · Whip Cupcake · Winter Wreath · Witch Hat · Xmas Stocking

---

## Background Colors

Six backgrounds per model, ranked rarest to most common. Multiplier applied on top of tier base price.

| Background | Emoji | Multiplier | Drop Weight |
|---|---|---|---|
| Black | ⬛ | 3.0x | 1 |
| Onyx Black | 🖤 | 2.5x | 2 |
| Grape | 🟣 | 2.0x | 4 |
| Emerald | 🟢 | 1.5x | 8 |
| Midnight Blue | 🔵 | 1.2x | 15 |
| Orange | 🟠 | 1.0x | 30 |

---

## Data Model

### `gift_models` — static catalog (seeded from `data/gift_catalog.py`)
```sql
CREATE TABLE IF NOT EXISTS gift_models (
    id               INTEGER PRIMARY KEY,
    collection       TEXT NOT NULL,
    model_number     INTEGER NOT NULL,
    model_name       TEXT NOT NULL,
    model_emoji      TEXT NOT NULL,
    model_rarity_pct REAL NOT NULL,
    tier             TEXT NOT NULL,   -- 'low' | 'mid' | 'high'
    custom_emoji_id  TEXT,            -- Telegram custom emoji ID (future use)
    UNIQUE(collection, model_number)
);
```

### `gift_instances` — one row per model×background (6 per model)
```sql
CREATE TABLE IF NOT EXISTS gift_instances (
    id           INTEGER PRIMARY KEY,
    model_id     INTEGER NOT NULL REFERENCES gift_models(id),
    background   TEXT NOT NULL,   -- 'black' | 'onyx' | 'grape' | 'emerald' | 'midnight' | 'orange'
    owner_id     INTEGER,         -- NULL = bank owns it
    acquired_at  INTEGER,
    UNIQUE(model_id, background)
);
```

### `gift_prices` — current market price per collection + background, updated daily
```sql
CREATE TABLE IF NOT EXISTS gift_prices (
    collection      TEXT NOT NULL,
    background      TEXT NOT NULL,
    base_price      INTEGER NOT NULL,
    current_price   INTEGER NOT NULL,
    demand_pressure INTEGER NOT NULL DEFAULT 0,
    last_updated    INTEGER NOT NULL,
    PRIMARY KEY (collection, background)
);
```

### `gift_offers` — pending P2P trade offers
```sql
CREATE TABLE IF NOT EXISTS gift_offers (
    id          INTEGER PRIMARY KEY,
    from_user_id INTEGER NOT NULL,
    to_user_id   INTEGER NOT NULL,
    instance_id  INTEGER NOT NULL REFERENCES gift_instances(id),
    wrk_offered  INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'accepted' | 'declined' | 'expired'
    created_at   INTEGER NOT NULL
);
```

---

## Price Engine

### Base prices by tier (before background multiplier)

| Tier | Base WRK$ range |
|---|---|
| Low | 500 – 2,000 |
| Mid | 3,000 – 12,000 |
| High | 20,000 – 150,000 |

Each collection gets a fixed `base_price` assigned at seed time within its tier range. Sub-tier notes in the catalog are used to place collections at the right end of their range (e.g. high-mid collections sit at 10,000–12,000, low-mid at 3,000–5,000).

### Daily price update (runs at midnight via APScheduler)
1. Random drift: ±5–20% per collection
2. Demand pressure: each net bank buy that day adds +3%; each net sell subtracts -2% (capped at ±30% total swing from demand)
3. Combined result clamped to: `[base_price × 0.40, base_price × 5.0]`
4. `demand_pressure` column resets to 0 after each daily update

### Sell-to-bank rate
Players receive **80%** of current market price when selling to the bank.

---

## Commands

| Command | Who | Description |
|---|---|---|
| `/inv` · `/inventory` | Any | Pageable gift inventory |
| `/gift <collection> <number>` | Any | Flex a specific gift in chat |
| `/shop [collection]` | Any | Browse bank's listings with current prices |
| `/buy <collection> <number> <bg>` | Any | Buy from bank at current price |
| `/sell <collection> <number> <bg>` | Any | Sell to bank at 80% of market |
| `/offer @user <amount> for <collection> <number> <bg>` | Any | Send WRK$ offer to another user via DM |
| `/offers` | Any | View pending incoming/outgoing offers |

### `/inv` — Inventory UI

- 5 gifts per page with ◀ Prev / Next ▶ pagination buttons
- Each gift shown as a tappable button: `🐈‍⬛ Scared Cat #12 ⬛`
- Tapping edits the message to show the detail card:

```
🐈‍⬛ Scared Cat #12

Model: 🐈‍⬛ Garfield · 0.5%
Background: ⬛ Black · 3.0x

Current value: 4,200 WRK$
```

- Detail card has a **⬅️ Back** button that returns to the inventory page

### `/gift <collection> <number>` — Flex in chat

Posts the gift detail card as a standalone message (not an edit). Background specified by context — if user owns multiple backgrounds of the same model, defaults to their rarest one, or user can specify: `/gift scaredcat 12 black`.

### `/offer` — P2P trade flow

1. Sender runs `/offer @jerry 5000 for scaredcat 12 black`
2. Bot DMs Jerry:
   ```
   💌 Offer from [Bryce]

   🐈‍⬛ Scared Cat #12 ⬛ Black
   Offer: 5,000 WRK$
   Current market value: 4,200 WRK$

   [✅ Accept] [❌ Decline]
   ```
3. Jerry taps Accept → WRK$ transfers, gift ownership transfers, both parties notified
4. Offer expires after 24 hours if not answered

---

## Daily Drop Integration

`/daily` gains a **25% chance** to award one random low-tier gift alongside the WRK$ reward.

- Collection chosen uniformly at random from all low-tier collections
- Model chosen weighted by rarity % (lower % = rarer = lower weight)
- Background chosen weighted by drop weight (Orange 30, Midnight 15, Emerald 8, Grape 4, Onyx 2, Black 1)
- If all instances of the rolled model+background are already owned (by players or bank has 0), reroll up to 3 times then skip the gift drop gracefully
- Gift is transferred from bank to user (owner_id set)
- Drop message appended to the daily claim reply

---

## Custom Emoji Support

The gift catalog includes a `custom_emoji_id` field per model for future Telegram custom emoji display. Currently unused — all display falls back to the collection's standard emoji (🐈‍⬛, 🎁, 🐸, etc.). The emoji IDs are available in the source data and can be extracted and populated when Telegram custom emoji support is enabled for the bot.

---

## File Structure

```
data/
  gift_catalog.py     ← static catalog dict; you set tier, base_price, models, custom_emoji_id
handlers/
  gifts.py            ← /inv, /gift, /shop, /buy, /sell, /offer, /offers
db.py                 ← 4 new tables + seed functions
bot.py                ← register gift handlers
jobs.py               ← daily price update job added
```

---

## Seeding

On first boot (or via a `/seedgifts` owner-only command), the bot:
1. Reads `data/gift_catalog.py`
2. Inserts all models into `gift_models` (skips existing)
3. Creates 6 `gift_instances` per model (one per background), owner_id = NULL (bank)
4. Inserts initial `gift_prices` rows using base_price × background multiplier

Re-running seed is idempotent — uses `INSERT OR IGNORE`.

---

## Out of Scope (This Phase)

- Crafted model variants
- Gift crafting system
- Gift display on user profile
- Telegram custom emoji rendering (field reserved, not implemented)
