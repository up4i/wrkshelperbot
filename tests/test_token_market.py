import token_market


def _reset_cache(monkeypatch):
    monkeypatch.setattr(token_market, "_gram_cache", None)
    monkeypatch.setattr(token_market, "_gram_cache_time", 0.0)


def test_gram_reference_normalizes_json_float(monkeypatch):
    monkeypatch.setattr(
        token_market,
        "_get_text",
        lambda _url: '{"asset":{"kind":"Ton","dex_usd_price":1.40,"symbol":"GRAM"}}',
    )
    _reset_cache(monkeypatch)

    gram = token_market.get_gram_reference(force=True)

    assert gram["symbol"] == "GRAM"
    assert gram["price_usd"] == "1.4"
    assert gram["stale"] is False


def test_gram_reference_falls_back_to_cached_rate(monkeypatch):
    monkeypatch.setattr(
        token_market,
        "_gram_cache",
        {
            "symbol": "GRAM",
            "name": "Gram",
            "price_usd": "1.5",
            "updated_at": 100,
            "stale": False,
        },
    )
    monkeypatch.setattr(token_market, "_gram_cache_time", 0.0)
    monkeypatch.setattr(
        token_market,
        "_fetch_gram",
        lambda: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(token_market.time, "time", lambda: 2_000)

    gram = token_market.get_gram_reference(force=True)

    assert gram["price_usd"] == "1.5"
    assert gram["stale"] is True

