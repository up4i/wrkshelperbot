# Batch A — Mini-App Polish & Fixes

**Date:** 2026-07-10  
**Scope:** Five targeted improvements to existing features — Blackjack split, dealer animation, gift card rendering, infinite gift scroll, drag-and-drop gift reorder.

---

## 1. Blackjack: Split on Matching Ranks

### Problem
`can_split` uses `_bj_card_val` (value equality), so 10+J qualifies but the intent is same-rank pairs only (7+7, J+J, A+A).

### Change
**`miniapp/server.py` — `_bj_snapshot()`:**
```python
# Before
and _bj_card_val(hand[0][0]) == _bj_card_val(hand[1][0])

# After
and hand[0][0] == hand[1][0]   # exact rank match: J+J, 7+7, A+A etc.
```

No frontend change — `can_split` bool is already wired to the split button display.

---

## 2. Blackjack: Staggered Dealer Card Reveal

### Problem
Dealer's full hand appears all at once when the round resolves — no suspense.

### Desired Sequence (after player stands / busts / doubles out)
1. **Hole card flip** — hidden card rotates `rotateY` 0→90° (150ms), JS replaces face content, rotates 90→0° (150ms). Total: 300ms.
2. **Extra dealer cards** — each additional card (drawn by dealer to reach 17+) slides in from above (`translateY(-16px)→0, opacity 0→1`) with 350ms between cards.
3. **Result banner** appears only after the last card lands.

### Implementation
- New CSS: `@keyframes bj-deal` (slide-in) and `@keyframes bj-flip-out / bj-flip-in` for hole reveal.
- New async JS function `_bjRevealDealer(knownCards, fullHand)`:
  - `knownCards` = cards already visible (dealer's face card)
  - `fullHand` = complete dealer hand from server response
  - Animates hole card reveal then appends remaining cards with staggered delays
- All round-ending paths (stand, bust, blackjack, double resolve) call `_bjRevealDealer` before rendering the result box.
- Delay formula: `(fullHand.length - 1) * 350 + 500` ms before result shown.

---

## 3. Gift Card "Box" Fix

### Problem
The WebP thumbnail for each gift has a solid (non-transparent) background. On the dark card surface this appears as a visible rectangle around the emoji.

### Change
**Frontend — profile gift grid rendering:**  
Apply the gift's `background` field (already fetched, e.g. `"#1a1a2e"`) as `background-color` on the `.gift-card` div via inline style. The card background matches the thumbnail background → box disappears visually.

```js
// gift card div
style="cursor:pointer;background:${g.background || 'var(--card2)'}"
```

Also add `border-radius: 6px` to `.gift-emoji-img` CSS to soften any remaining edge.

No server change needed — `gi.background` is already returned in the profile response.

---

## 4. Infinite Gift Scroll

### Problem
Profile gifts are hard-capped at 20 (`LIMIT 20`). Users with large collections can't see everything.

### Server Changes
**`/api/profile/{user_id}` endpoint:**
- Add optional query params `?gifts_offset=N` (default `0`) and `gifts_limit=20`.
- Return `total_gifts: int` and `has_more: bool` alongside the gifts array.
- Gifts query becomes:
  ```sql
  ORDER BY COALESCE(gi.sort_index, 999999), gi.acquired_at DESC
  LIMIT ? OFFSET ?
  ```
  (The `sort_index` ordering prepares for item 5 below.)

### Frontend Changes
- After rendering gifts, append a `<div id="giftSentinel">` below the grid.
- `IntersectionObserver` watches the sentinel. When it enters the viewport and `has_more` is true:
  - Show a small spinner inside the sentinel.
  - Fetch `/api/profile/{id}?gifts_offset=N&gifts_limit=20`.
  - Append new gift cards to the grid.
  - Update `has_more` and `gifts_offset` state.
- Observer is disconnected when `has_more` becomes false.
- Works on both own-profile view and searched profiles.

---

## 5. Drag-and-Drop Gift Reorder

### Default Order
`acquired_at DESC` (newest first) — unchanged from current behavior when no custom order is set.

### Database
```sql
ALTER TABLE gift_instances ADD COLUMN sort_index INTEGER;
-- NULL = unordered (falls back to acquired_at)
```

Added in `_startup()` via `ALTER TABLE … ADD COLUMN IF NOT EXISTS` try/except pattern (existing pattern in codebase).

### New Server Endpoint
```
POST /api/profile/reorder
Body: { user_id: int, gift_ids: [int, ...] }
```
- Verifies all gift IDs belong to `user_id`.
- Writes `sort_index = 0, 1, 2, …` for each ID in order.
- Returns `{ ok: true }`.

Auth: validated via Telegram `initData` (same as all other POST endpoints).

### Frontend
- **Library:** Sortable.js loaded from CDN (`cdnjs`, already used for lottie — same pattern).
- **Entry point:** "⠿ Reorder" button shown only on own-profile gifts section.
- **Reorder mode:**
  - Button label changes to "✓ Done".
  - Gift cards get a drag handle icon overlay (top-left corner).
  - Sortable.js initialized on the gifts grid with `animation: 150` and touch support.
- **Persist:** On `onEnd` drag event, debounce 800ms, then POST `/api/profile/reorder` with current order of all loaded gift IDs.
- **Exit:** "Done" button destroys Sortable instance, removes handles, saves final order.
- **Infinite scroll + reorder:** When in reorder mode, sentinel observer is paused (can't reorder partial data). A note "Load all gifts before reordering" is shown if `has_more` is true when reorder is toggled — user must scroll to bottom first.

---

## Files Changed

| File | Changes |
|---|---|
| `miniapp/server.py` | Split rank fix; profile endpoint offset/limit/has_more; reorder endpoint; startup migration for sort_index |
| `miniapp/static/index.html` | BJ dealer flip CSS + JS; gift card background fix; infinite scroll observer; Sortable.js CDN + reorder UI |

## Out of Scope
- Batch B–E items (separate specs)
- Anonymous numbers (deferred)
- NFT background color accuracy (confirmed correct by owner)
