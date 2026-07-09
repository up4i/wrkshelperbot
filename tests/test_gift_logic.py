import pytest
from handlers.gifts import (
    _bg_emoji, _bg_label, _tier_label,
    _format_gift_card, _collection_display_name,
    _price_floor, _price_ceiling,
)

def test_bg_emoji():
    assert _bg_emoji("black") == "⬛"
    assert _bg_emoji("onyx") == "🖤"
    assert _bg_emoji("grape") == "🟣"
    assert _bg_emoji("emerald") == "🟢"
    assert _bg_emoji("midnight") == "🔵"
    assert _bg_emoji("orange") == "🟠"

def test_bg_label():
    assert _bg_label("black") == "Black"
    assert _bg_label("onyx") == "Onyx Black"
    assert _bg_label("midnight") == "Midnight Blue"

def test_tier_label():
    assert _tier_label("low") == "⚪ Common"
    assert _tier_label("mid") == "🔵 Rare"
    assert _tier_label("high") == "🟡 Legendary"

def test_format_gift_card():
    instance = {
        "collection": "scared_cat",
        "model_number": 12,
        "model_name": "Garfield",
        "model_emoji": "🐈‍⬛",
        "model_rarity_pct": 0.5,
        "background": "black",
        "tier": "high",
    }
    card = _format_gift_card(instance, current_price=4200)
    assert "Scared Cat" in card
    assert "#12" in card
    assert "Garfield" in card
    assert "0.5%" in card
    assert "Black" in card
    assert "4,200" in card

def test_collection_display_name():
    assert _collection_display_name("scared_cat") == "Scared Cat"
    assert _collection_display_name("jack_in_the_box") == "Jack In The Box"
    assert _collection_display_name("durovs_cap") == "Durovs Cap"

def test_price_floor_ceiling():
    assert _price_floor(1000) == 400
    assert _price_ceiling(1000) == 5000
    assert _price_floor(10000) == 4000
    assert _price_ceiling(10000) == 50000
