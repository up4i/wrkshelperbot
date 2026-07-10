# Batch A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five targeted polish improvements: BJ split on matching ranks, animated dealer reveal, gift card background fix, infinite gift scroll, and drag-and-drop gift reorder.

**Architecture:** All changes are contained to `miniapp/server.py` and `miniapp/static/index.html`. Server adds query params and one new endpoint. Frontend uses CSS animation delays for the dealer reveal (no async required), IntersectionObserver for scroll, and Sortable.js CDN for drag.

**Tech Stack:** FastAPI, SQLite, vanilla JS, CSS animations, IntersectionObserver API, Sortable.js 1.15.0

---

## Task 1: BJ Split — Same-Rank Fix

**Files:**
- Modify: `miniapp/server.py` (line ~1517)
- Test: `tests/test_economy_logic.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_economy_logic.py` (no imports needed — pure logic test):

```python
# ── Blackjack split logic ─────────────────────────────────────────────────────

def _can_split(hand, balance, bet):
    """Mirrors the can_split condition in miniapp/server.py _bj_snapshot()."""
    is_first = len(hand) == 2
    return is_first and hand[0][0] == hand[1][0] and balance >= bet

def test_split_same_rank_pairs():
    assert _can_split([('7', '♠'), ('7', '♥')], 1000, 100) is True
    assert _can_split([('A', '♠'), ('A', '♥')], 1000, 100) is True
    assert _can_split([('J', '♠'), ('J', '♦')], 1000, 100) is True
    assert _can_split([('10', '♠'), ('10', '♦')], 1000, 100) is True

def test_split_different_rank_not_allowed():
    assert _can_split([('10', '♠'), ('J', '♥')], 1000, 100) is False
    assert _can_split([('K', '♠'), ('7', '♥')], 1000, 100) is False
    assert _can_split([('A', '♠'), ('K', '♥')], 1000, 100) is False

def test_split_requires_enough_balance():
    assert _can_split([('7', '♠'), ('7', '♥')], 50, 100) is False

def test_split_only_on_first_two_cards():
    # Three-card hand — can_split should be False
    assert _can_split([('7', '♠'), ('7', '♥'), ('3', '♣')], 1000, 100) is False
```

- [ ] **Step 2: Run test to verify it fails (the 10+J test will pass unexpectedly with old logic)**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -m pytest tests/test_economy_logic.py::test_split_different_rank_not_allowed -v
```

Expected: FAIL — `_can_split` uses value equality so 10+J (both value 10) incorrectly returns True.

- [ ] **Step 3: Apply the fix in server.py**

In `miniapp/server.py`, find the `can_split` block (~line 1514) and change:

```python
# BEFORE
    can_split = (
        is_first
        and len(game["hands"]) == 1
        and _bj_card_val(hand[0][0]) == _bj_card_val(hand[1][0])
        and balance >= game["bet"]
    )

# AFTER
    can_split = (
        is_first
        and len(game["hands"]) == 1
        and hand[0][0] == hand[1][0]
        and balance >= game["bet"]
    )
```

- [ ] **Step 4: Run all split tests**

```bash
python -m pytest tests/test_economy_logic.py -k "split" -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests PASS (same count as before + 4 new).

- [ ] **Step 6: Commit**

```bash
git add miniapp/server.py tests/test_economy_logic.py
git commit -m "Fix BJ split: require same rank (not same value)"
```

---

## Task 2: Blackjack Dealer Card Reveal Animation

**Files:**
- Modify: `miniapp/static/index.html` (CSS section + `renderBjFinished` function)

- [ ] **Step 1: Add CSS keyframes**

Find the `@keyframes crash-shake` block in the CSS `<style>` section and add after it:

```css
  @keyframes bj-flip-reveal {
    from { transform: rotateY(90deg) scaleX(0.5); opacity: 0; }
    to   { transform: rotateY(0deg)  scaleX(1);   opacity: 1; }
  }
  @keyframes bj-deal-in {
    from { opacity: 0; transform: translateY(-14px); }
    to   { opacity: 1; transform: translateY(0); }
  }
```

- [ ] **Step 2: Update `renderBjFinished` to animate dealer cards**

Find `function renderBjFinished(data)` and replace the dealer card line and the innerHTML assignment:

```js
function renderBjFinished(data) {
  bjBusy = false;
  if (data.new_balance !== undefined) {
    state.balance = data.new_balance;
    refreshHeaderBal();
  }

  // Dealer hand: first card was already visible (no anim), hole card flips,
  // extra drawn cards slide in. Each step is 380ms after the previous.
  const dealerCardHtml = (data.dealer_hand || []).map((c, i) => {
    if (i === 0) return bjCard(c); // already visible
    const delay = (i - 1) * 380;
    const anim = i === 1
      ? `animation:bj-flip-reveal .35s ease ${delay}ms both`
      : `animation:bj-deal-in .3s ease ${delay}ms both`;
    return `<div style="${anim}">${bjCard(c)}</div>`;
  }).join('');

  const playerHtml = data.hands.map((h, i) => {
    const r = data.results[i];
    return bjHandHtml(h, r.player_value, false);
  }).join('');

  const OUTCOMES = { win: '🏆 Win', bust: '💥 Bust', lose: '😞 Lose', push: '🤝 Push', blackjack: '🎉 Blackjack!' };
  const resultLines = data.results.map((r, i) => {
    const label = data.hands.length > 1 ? `Hand ${i+1}: ` : '';
    const sign = r.delta > 0 ? '+' : '';
    const amt = r.delta !== 0 ? ` ${sign}${fmt(r.delta)} WRK$` : '';
    return `${label}${OUTCOMES[r.outcome] || r.outcome}${amt}`;
  }).join('<br>');

  const netLine = data.hands.length > 1
    ? `<br><b>Net: ${data.total_delta >= 0 ? '+' : ''}${fmt(data.total_delta)} WRK$</b>` : '';
  const resClass = data.total_delta > 0 ? 'win-box' : data.total_delta < 0 ? 'lose-box' : '';

  // Result and buttons appear after last dealer card lands
  const lastCardDelay = Math.max(0, ((data.dealer_hand || []).length - 2) * 380);
  const resultDelay = lastCardDelay + 420;
  const fadeIn = (d) => `animation:bj-deal-in .3s ease ${d}ms both`;

  document.getElementById('bjContent').innerHTML = `
    <div class="bj-table">
      <div>
        <div class="bj-section-label">Dealer — ${data.dealer_value ?? ''}</div>
        <div class="bj-hand-row">${dealerCardHtml}</div>
      </div>
      <div class="divider"></div>
      <div>
        <div class="bj-section-label">You</div>
        ${playerHtml}
      </div>
    </div>
    <div class="result-box show ${resClass}" style="font-size:14px;line-height:1.7;${fadeIn(resultDelay)}">${resultLines}${netLine}</div>
    <div class="balance-display" style="${fadeIn(resultDelay + 80)}">Balance: <span class="b">${fmt(data.new_balance)}</span> WRK$</div>
    <button class="btn" style="width:100%;${fadeIn(resultDelay + 160)}" onclick="renderBjBetting()">Play Again</button>
  `;
}
```

- [ ] **Step 3: Manual smoke test**

Start a blackjack hand, stand, and verify:
- Hole card flips into view (~350ms after result arrives)
- Any extra dealer cards slide in one by one
- Result box, balance, and Play Again button fade in after cards settle

- [ ] **Step 4: Commit**

```bash
git add miniapp/static/index.html
git commit -m "BJ dealer animation: staggered card reveal on round end"
```

---

## Task 3: Gift Card Background Fix

**Files:**
- Modify: `miniapp/static/index.html` (CSS + `buildProfileHtml`)

- [ ] **Step 1: Add border-radius to gift emoji image CSS**

Find `.gift-emoji-img {` in the `<style>` section and add `border-radius: 6px;`:

```css
  .gift-emoji-img {
    width: 40px; height: 40px;
    object-fit: contain;
    display: block; margin: 0 auto 4px;
    border-radius: 6px;
  }
```

- [ ] **Step 2: Apply gift background color to each card div**

In `buildProfileHtml`, find the gift card `<div>` line:

```js
        return `
        <div class="gift-card${ownCls}${pinnedCls}" style="cursor:pointer" onclick="openProfileGiftModal(${g.id})">
```

Replace with:

```js
        const bgColor = g.background
          ? (g.background.startsWith('#') ? g.background : `#${g.background}`)
          : 'var(--card2)';
        return `
        <div class="gift-card${ownCls}${pinnedCls}" style="cursor:pointer;background:${bgColor}" onclick="openProfileGiftModal(${g.id})">
```

- [ ] **Step 3: Manual smoke test**

Open the profile page of a user with gifts. Verify no white/gray rectangle box is visible around gift emoji images.

- [ ] **Step 4: Commit**

```bash
git add miniapp/static/index.html
git commit -m "Gift cards: apply background color to match thumbnail, soften img edges"
```

---

## Task 4: DB Migration + Profile Endpoint Pagination (Server)

**Files:**
- Modify: `miniapp/server.py` (`_startup`, `_load_profile`, `profile_by_id`)

- [ ] **Step 1: Add sort_index migration to `_startup()`**

In `_startup()`, after the existing `ALTER TABLE economy ADD COLUMN last_hack` try/except block, add:

```python
        try:
            db.execute("ALTER TABLE gift_instances ADD COLUMN sort_index INTEGER")
            db.commit()
        except Exception:
            pass
```

- [ ] **Step 2: Update `_load_profile` signature and gifts query**

Change the function signature and gifts query:

```python
def _load_profile(db, user_id: int, gifts_offset: int = 0, gifts_limit: int = 20) -> dict:
```

Replace the gifts query (lines ~161-166):

```python
    gifts = db.execute(
        "SELECT gi.id, gi.gift_number, gi.background, gi.acquired_at, "
        "gm.model_name, gm.model_emoji, gm.tier, gm.collection, gm.custom_emoji_id "
        "FROM gift_instances gi JOIN gift_models gm ON gm.id = gi.model_id "
        "WHERE gi.owner_id = ? "
        "ORDER BY COALESCE(gi.sort_index, 999999) ASC, gi.acquired_at DESC "
        "LIMIT ? OFFSET ?",
        (user_id, gifts_limit, gifts_offset)
    ).fetchall()
```

Add `has_more` to the return dict (after `gift_count` is computed):

```python
    return {
        "user_id": row["user_id"],
        "name": display,
        "username": username,
        "balance": row["balance"],
        "streak": row["streak"],
        "last_daily": row["last_daily"],
        "balance_rank": balance_rank,
        "streak_rank": streak_rank,
        "gift_count": gift_count,
        "gift_rank": gift_rank,
        "gifts": [dict(g) for g in gifts],
        "has_more": len(gifts) == gifts_limit and (gifts_offset + gifts_limit) < gift_count,
        "gifts_offset": gifts_offset,
        "pinned_gift": pinned_gift,
        "pinned_gift_id": row["pinned_gift_id"],
    }
```

- [ ] **Step 3: Add query params to `profile_by_id`**

```python
@app.get("/api/profile/{user_id}")
def profile_by_id(user_id: int, gifts_offset: int = 0, gifts_limit: int = 20):
    with db_conn() as db:
        return _load_profile(db, user_id, gifts_offset, gifts_limit)
```

- [ ] **Step 4: Verify server starts and existing profile loads**

```bash
cd /home/ogkush/Projects/wrkshelperbot
python -c "from miniapp.server import app; print('OK')"
```

Expected: `OK` (no import errors).

- [ ] **Step 5: Commit**

```bash
git add miniapp/server.py
git commit -m "Profile: sort_index migration, pagination (offset/limit/has_more)"
```

---

## Task 5: Reorder Endpoint (Server)

**Files:**
- Modify: `miniapp/server.py`

- [ ] **Step 1: Add the request model and endpoint**

After the existing Pydantic models (near `BetRequest`, `CoinflipRequest`, etc.), add:

```python
class ReorderRequest(BaseModel):
    user_id: int
    gift_ids: list[int]
```

After the profile endpoints, add:

```python
@app.post("/api/profile/reorder")
def profile_reorder(req: ReorderRequest):
    with db_conn() as db:
        # Verify all gift_ids belong to this user
        placeholders = ",".join("?" * len(req.gift_ids))
        owned = db.execute(
            f"SELECT id FROM gift_instances WHERE id IN ({placeholders}) AND owner_id = ?",
            (*req.gift_ids, req.user_id)
        ).fetchall()
        if len(owned) != len(req.gift_ids):
            raise HTTPException(403, "One or more gifts don't belong to this user")
        for idx, gift_id in enumerate(req.gift_ids):
            db.execute(
                "UPDATE gift_instances SET sort_index = ? WHERE id = ? AND owner_id = ?",
                (idx, gift_id, req.user_id)
            )
        db.commit()
    return {"ok": True}
```

- [ ] **Step 2: Verify endpoint is reachable**

```bash
python -c "from miniapp.server import app; routes = [r.path for r in app.routes]; assert '/api/profile/reorder' in routes, routes; print('reorder endpoint registered')"
```

Expected: `reorder endpoint registered`

- [ ] **Step 3: Commit**

```bash
git add miniapp/server.py
git commit -m "Add POST /api/profile/reorder endpoint"
```

---

## Task 6: Infinite Gift Scroll (Frontend)

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add scroll state variables**

Near the top of the `<script>` section (after `let rouletteColor = null;` or similar), add:

```js
let _giftsProfileId = null;
let _giftsOffset = 0;
let _giftsHasMore = false;
let _giftsObserver = null;
```

- [ ] **Step 2: Extract gift card HTML builder**

In `buildProfileHtml`, extract the per-gift card template into a standalone function. Find the gift card map block and replace with a call to a new helper:

```js
function _buildGiftCardHtml(g, isOwn, pinnedGiftId) {
  const gemHtml = g.custom_emoji_id
    ? `<img class="gift-emoji-img" src="/emoji/${g.custom_emoji_id}" alt="🎁" onerror="this.outerHTML='<div class=\\'gem\\'>🎁</div>'">`
    : `<div class="gem">${g.model_emoji || '🎁'}</div>`;
  const isPinned = pinnedGiftId === g.id;
  const ownCls = isOwn ? ' own-gift' : '';
  const pinnedCls = isPinned ? ' pinned-gift' : '';
  const bgColor = g.background
    ? (g.background.startsWith('#') ? g.background : `#${g.background}`)
    : 'var(--card2)';
  return `
    <div class="gift-card${ownCls}${pinnedCls}" style="cursor:pointer;background:${bgColor}" onclick="openProfileGiftModal(${g.id})">
      ${gemHtml}
      <div class="gname">${esc(g.model_name)}</div>
      <div class="gtier ${({common:'',uncommon:'',rare:'rare',epic:'epic',legendary:'legendary'})[g.tier]||''}">${g.tier} #${g.gift_number||g.id}</div>
      ${isPinned ? '<div style="font-size:10px;color:var(--primary);margin-top:2px">📌 pinned</div>' : ''}
    </div>`;
}
```

Then in `buildProfileHtml`, replace the existing per-gift map with:

```js
  const giftHtml = d.gifts.length
    ? d.gifts.map(g => _buildGiftCardHtml(g, isOwn, d.pinned_gift_id)).join('')
    : '<div class="empty" style="grid-column:1/-1">No gifts yet</div>';
```

- [ ] **Step 3: Add sentinel div and observer setup to `buildProfileHtml`**

In `buildProfileHtml`, replace the gifts grid section:

```js
  // Track scroll state for this profile
  _giftsProfileId = d.user_id;
  _giftsOffset = d.gifts.length;
  _giftsHasMore = d.has_more;

  const sentinelHtml = d.has_more
    ? `<div id="giftSentinel" style="grid-column:1/-1;text-align:center;padding:12px;color:var(--muted);font-size:13px">Loading…</div>`
    : '';
```

And update the returned HTML gifts section to include the sentinel:

```js
      <div class="gifts-grid" id="giftsGrid">${giftHtml}${sentinelHtml}</div>
```

Change `<div class="gifts-grid">${giftHtml}</div>` to the above.

- [ ] **Step 4: Wire up the IntersectionObserver after profile renders**

In `loadProfile()` and in `openProfilePage()` (own profile), call a new `_setupGiftsObserver()` after the content renders. Add this function:

```js
function _setupGiftsObserver() {
  if (_giftsObserver) { _giftsObserver.disconnect(); _giftsObserver = null; }
  if (!_giftsHasMore) return;
  const sentinel = document.getElementById('giftSentinel');
  if (!sentinel) return;
  _giftsObserver = new IntersectionObserver(async entries => {
    if (!entries[0].isIntersecting || !_giftsHasMore) return;
    _giftsHasMore = false; // prevent double-fire while loading
    try {
      const d = await api(`/api/profile/${_giftsProfileId}?gifts_offset=${_giftsOffset}&gifts_limit=20`);
      const grid = document.getElementById('giftsGrid');
      if (!grid) return;
      // Remove old sentinel
      const old = document.getElementById('giftSentinel');
      if (old) old.remove();
      // Append new cards
      const isOwn = window._profileIsOwn || false;
      d.gifts.forEach(g => {
        window._profileGifts[g.id] = g;
        grid.insertAdjacentHTML('beforeend', _buildGiftCardHtml(g, isOwn, window._profilePinnedId));
      });
      _giftsOffset += d.gifts.length;
      _giftsHasMore = d.has_more;
      // Re-add sentinel if more remain
      if (_giftsHasMore) {
        grid.insertAdjacentHTML('beforeend',
          `<div id="giftSentinel" style="grid-column:1/-1;text-align:center;padding:12px;color:var(--muted);font-size:13px">Loading…</div>`);
        _setupGiftsObserver();
      }
    } catch(e) {
      console.error('gift scroll error', e);
      _giftsHasMore = true; // allow retry
    }
  }, { threshold: 0.1 });
  _giftsObserver.observe(sentinel);
}
```

Call `_setupGiftsObserver()` at the end of `loadProfile()` (after `content.style.display = 'flex'`) and at the end of the own-profile rendering in `openProfilePage()`.

- [ ] **Step 5: Manual smoke test**

Open a profile with more than 20 gifts. Scroll to the bottom of the gift grid. Verify more gifts load automatically.

- [ ] **Step 6: Commit**

```bash
git add miniapp/static/index.html
git commit -m "Infinite gift scroll: IntersectionObserver + server pagination"
```

---

## Task 7: Drag-and-Drop Gift Reorder (Frontend)

**Files:**
- Modify: `miniapp/static/index.html`

- [ ] **Step 1: Add Sortable.js CDN**

In `<head>`, after the existing CDN script tags (lottie, confetti), add:

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"></script>
```

- [ ] **Step 2: Add reorder mode state variable**

Near the other state variables, add:

```js
let _sortableInstance = null;
let _reorderDebounce = null;
```

- [ ] **Step 3: Add reorder toggle button to gift section in `buildProfileHtml`**

Find the `card-title` line in the return HTML:

```js
      <div class="card-title">Gift Collection (${d.gift_count})${isOwn ? ' — tap to pin' : ''}</div>
```

Replace with:

```js
      <div class="card-title" style="display:flex;justify-content:space-between;align-items:center">
        <span>Gift Collection (${d.gift_count})${isOwn ? ' — tap to pin' : ''}</span>
        ${isOwn ? `<button class="btn outline" id="reorderBtn" onclick="toggleReorderMode()" style="font-size:12px;padding:4px 10px">⠿ Reorder</button>` : ''}
      </div>
```

- [ ] **Step 4: Add `toggleReorderMode()` function**

```js
function toggleReorderMode() {
  const btn = document.getElementById('reorderBtn');
  const grid = document.getElementById('giftsGrid');
  if (!grid || !btn) return;

  if (_sortableInstance) {
    // Exit reorder mode
    _sortableInstance.destroy();
    _sortableInstance = null;
    btn.textContent = '⠿ Reorder';
    grid.querySelectorAll('.reorder-handle').forEach(h => h.remove());
    return;
  }

  // Can't reorder partial load
  if (_giftsHasMore) {
    alert('Scroll to load all your gifts before reordering.');
    return;
  }

  btn.textContent = '✓ Done';

  // Add drag handles
  grid.querySelectorAll('.gift-card').forEach(card => {
    const handle = document.createElement('div');
    handle.className = 'reorder-handle';
    handle.style.cssText = 'position:absolute;top:4px;left:4px;font-size:14px;cursor:grab;color:var(--muted);z-index:5;line-height:1';
    handle.textContent = '⠿';
    card.style.position = 'relative';
    card.prepend(handle);
  });

  _sortableInstance = new Sortable(grid, {
    animation: 150,
    handle: '.reorder-handle',
    ghostClass: 'gift-card-ghost',
    onEnd() {
      clearTimeout(_reorderDebounce);
      _reorderDebounce = setTimeout(_saveGiftOrder, 800);
    }
  });
}

async function _saveGiftOrder() {
  const grid = document.getElementById('giftsGrid');
  if (!grid) return;
  const giftIds = [...grid.querySelectorAll('.gift-card[onclick]')]
    .map(el => {
      const m = el.getAttribute('onclick').match(/openProfileGiftModal\((\d+)\)/);
      return m ? +m[1] : null;
    })
    .filter(Boolean);
  if (!giftIds.length) return;
  try {
    await api('/api/profile/reorder', { method: 'POST', json: { user_id: +state.userId, gift_ids: giftIds } });
  } catch(e) {
    console.error('reorder save failed', e);
  }
}
```

- [ ] **Step 5: Add ghost card CSS**

In the `<style>` section, add:

```css
  .gift-card-ghost { opacity: .4; background: var(--primary) !important; }
```

- [ ] **Step 6: Manual smoke test**

Open own profile. Tap "⠿ Reorder". Drag a gift to a new position. Wait ~1s. Reload the profile. Verify the new order is preserved.

- [ ] **Step 7: Final integration test**

1. Verify split button appears for 7+7 but not for 10+J
2. Stand in blackjack — dealer cards animate in one by one
3. Gift cards on profile show no white box
4. Scroll below 20 gifts — more load automatically
5. Drag-reorder persists after reload

- [ ] **Step 8: Deploy commit + push**

```bash
git add miniapp/static/index.html
git commit -m "Gift reorder: Sortable.js drag-and-drop, persisted via /api/profile/reorder"
git push
```

Then on the Pi:
```bash
git pull && systemctl --user restart miniapp wrkshelperbot
```
