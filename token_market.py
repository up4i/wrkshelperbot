from __future__ import annotations

import json
import threading
import time
import urllib.request
from decimal import Decimal, InvalidOperation


GRAM_ASSET_ADDRESS = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
GRAM_PRICE_URL = f"https://api.ston.fi/v1/assets/{GRAM_ASSET_ADDRESS}"
PRICE_CACHE_SECONDS = 90
PRICE_STALE_SECONDS = 15 * 60

_cache_lock = threading.Lock()
_gram_cache: dict | None = None
_gram_cache_time = 0.0


def _get_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "WRKS-Economy/1.0 (+https://wrk.money)"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_gram() -> dict:
    payload = json.loads(_get_text(GRAM_PRICE_URL))
    asset = payload["asset"]
    try:
        price = Decimal(str(asset["dex_usd_price"]))
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("GRAM price response is invalid") from exc
    if asset.get("kind") != "Ton" or not price.is_finite() or price <= 0:
        raise ValueError("STON.fi returned an invalid native GRAM asset")
    return {
        "symbol": "GRAM",
        "name": "Gram",
        "kind": "game_base",
        "price_usd": str(price),
        "updated_at": int(time.time()),
        "source": "STON.fi GRAM reference",
        "source_url": GRAM_PRICE_URL,
        "stale": False,
    }


def get_gram_reference(*, force: bool = False) -> dict:
    """Fetch the sole external rate: GRAM/USD for WRK$ entry and exit."""
    global _gram_cache, _gram_cache_time
    now = time.time()
    with _cache_lock:
        if not force and _gram_cache and now - _gram_cache_time < PRICE_CACHE_SECONDS:
            return dict(_gram_cache)
    try:
        result = _fetch_gram()
    except Exception:
        with _cache_lock:
            if not _gram_cache:
                raise
            fallback = dict(_gram_cache)
        fallback["stale"] = now - fallback["updated_at"] > PRICE_STALE_SECONDS
        return fallback
    with _cache_lock:
        _gram_cache = result
        _gram_cache_time = now
    return dict(result)

