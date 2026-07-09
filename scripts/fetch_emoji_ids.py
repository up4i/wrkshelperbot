#!/usr/bin/env python3
"""
Fetch custom_emoji_id for each gift model by querying the sticker packs
listed in the @GiftChanges links from gifts.txt.

Usage:
    cd /home/ogkush/Projects/wrkshelperbot
    python3 scripts/fetch_emoji_ids.py /home/ogkush/Desktop/gifts.txt
"""

import asyncio
import re
import sys
import json
from pathlib import Path

import telegram

# Collection name → catalog key mapping
COLLECTION_NAME_MAP = {
    "Santa Hat": "santa_hat",
    "Signet Ring": "signet_ring",
    "Precious Peach": "precious_peach",
    "Plush Pepe": "plush_pepe",
    "Spiced Wine": "spiced_wine",
    "Jelly Bunny": "jelly_bunny",
    "Durov's Cap": "durovs_cap",
    "Perfume Bottle": "perfume_bottle",
    "Eternal Rose": "eternal_rose",
    "Berry Box": "berry_box",
    "Vintage Cigar": "vintage_cigar",
    "Magic Potion": "magic_potion",
    "Kissed Frog": "kissed_frog",
    "Hex Pot": "hex_pot",
    "Evil Eye": "evil_eye",
    "Sharp Tongue": "sharp_tongue",
    "Trapped Heart": "trapped_heart",
    "Skull Flower": "skull_flower",
    "Scared Cat": "scared_cat",
    "Spy Agaric": "spy_agaric",
    "Homemade Cake": "homemade_cake",
    "Lunar Snake": "lunar_snake",
    "Party Sparkler": "party_sparkler",
    "Jester Hat": "jester_hat",
    "Genie Lamp": "genie_lamp",
    "Cookie Heart": "cookie_heart",
    "Jingle Bells": "jingle_bells",
    "Snow Mittens": "snow_mittens",
    "Hanging Star": "hanging_star",
    "Love Candle": "love_candle",
    "Desk Calendar": "desk_calendar",
    "B-Day Candle": "b_day_candle",
    "Bunny Muffin": "bunny_muffin",
    "Astral Shard": "astral_shard",
    "Mad Pumpkin": "mad_pumpkin",
    "Voodoo Doll": "voodoo_doll",
    "Hypno Lollipop": "hypno_lollipop",
    "Swiss Watch": "swiss_watch",
    "Crystal Ball": "crystal_ball",
    "Flying Broom": "flying_broom",
    "Eternal Candle": "eternal_candle",
    "Ginger Cookie": "ginger_cookie",
    "Mini Oscar": "mini_oscar",
    "Ion Gem": "ion_gem",
    "Lol Pop": "lol_pop",
    "Star Notepad": "star_notepad",
    "Love Potion": "love_potion",
    "Loot Bag": "loot_bag",
    "Toy Bear": "toy_bear",
    "Diamond Ring": "diamond_ring",
    "Top Hat": "top_hat",
    "Sleigh Bell": "sleigh_bell",
    "Sakura Flower": "sakura_flower",
    "Record Player": "record_player",
    "Tama Gadget": "tama_gadget",
    "Candy Cane": "candy_cane",
    "Winter Wreath": "winter_wreath",
    "Snow Globe": "snow_globe",
    "Electric Skull": "electric_skull",
    "Neko Helmet": "neko_helmet",
    "Witch Hat": "witch_hat",
    "Jack in the Box": "jack_in_the_box",
    "Easter Egg": "easter_egg",
    "Bonded Ring": "bonded_ring",
    "Big Year": "big_year",
    "Pet Snake": "pet_snake",
    "Snake Box": "snake_box",
    "Xmas Stocking": "xmas_stocking",
    "Holiday Drink": "holiday_drink",
    "Light Sword": "light_sword",
    "Gem Signet": "gem_signet",
    "Nail Bracelet": "nail_bracelet",
    "Restless Jar": "restless_jar",
    "Heroic Helmet": "heroic_helmet",
    "Bow Tie": "bow_tie",
    "Heart Locket": "heart_locket",
    "Lush Bouquet": "lush_bouquet",
    "Whip Cupcake": "whip_cupcake",
    "Joyful Bundle": "joyful_bundle",
    "Valentine Box": "valentine_box",
    "Cupid Charm": "cupid_charm",
    "Snoop Dogg": "snoop_dogg",
    "Swag Bag": "swag_bag",
    "Snoop Cigar": "snoop_cigar",
    "Low Rider": "low_rider",
    "Westside Sign": "westside_sign",
    "Ionic Dryer": "ionic_dryer",
    "Moon Pendant": "moon_pendant",
    "Jolly Chimp": "jolly_chimp",
    "Stellar Rocket": "stellar_rocket",
    "Input Key": "input_key",
    "Artisan Brick": "artisan_brick",
    "Mighty Arm": "mighty_arm",
    "Clover Pin": "clover_pin",
    "Fresh Socks": "fresh_socks",
    "Sky Stilettos": "sky_stilettos",
    "Happy Brownie": "happy_brownie",
    "Spring Basket": "spring_basket",
    "Instant Ramen": "instant_ramen",
    "Faith Amulet": "faith_amulet",
    "Mousse Cake": "mousse_cake",
    "Ice Cream": "ice_cream",
    "Bling Binky": "bling_binky",
    "Money Pot": "money_pot",
    "Pretty Posy": "pretty_posy",
    "Khabib's Papakha": "khabibs_papakha",
    "Ufc Strike": "ufc_strike",
    "Victory Medal": "victory_medal",
    "Rare Bird": "rare_bird",
    "Mood Pack": "mood_pack",
    "Pool Float": "pool_float",
    "Timeless Book": "timeless_book",
    "Chill Flame": "chill_flame",
    "Vice Cream": "vice_cream",
    "Surge Board": "surge_board",
    "Liberty Figure": "liberty_figure",
}

PACK_NAME_RE = re.compile(r't\.me/addemoji/([^\s)]+)')
HEADER_RE = re.compile(r'Gift Models[^:]*:\s+(.+?)\s+Models\s+', re.IGNORECASE)
MODEL_LINE_RE = re.compile(r'^.+?\s+(.+?)\s+—\s+([\d.]+)%')


def parse_gifts_file(path: str):
    """Parse gifts.txt and return list of (catalog_key, pack_name, [model_names])."""
    collections = []
    current_collection = None
    current_models = []

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        line = line.strip()

        # Collection header
        m = HEADER_RE.search(line)
        if m:
            # Save previous collection
            if current_collection and current_models:
                collections.append(current_collection)
            col_name = m.group(1).strip()
            catalog_key = COLLECTION_NAME_MAP.get(col_name)
            current_collection = {
                "name": col_name,
                "key": catalog_key,
                "pack_name": None,
                "models": [],
            }
            current_models = current_collection["models"]
            continue

        # Emoji pack link
        pm = PACK_NAME_RE.search(line)
        if pm and current_collection:
            current_collection["pack_name"] = pm.group(1)
            collections.append(current_collection)
            current_collection = None
            current_models = []
            continue

        # Model line (emoji + name + percentage)
        if current_collection and " — " in line and "%" in line:
            # Strip leading emoji (could be multi-codepoint)
            # Find the dash separator
            parts = line.split(" — ")
            if len(parts) >= 2:
                name_part = parts[0].strip()
                # Remove leading emoji chars
                name = re.sub(r'^[\U00010000-\U0010ffff☀-⛿✀-➿\U0001f300-\U0001f9ff\s]+', '', name_part).strip()
                if name:
                    current_models.append(name)

    return collections


async def fetch_emoji_ids(token: str, collections: list):
    """For each collection, fetch sticker set and map custom_emoji_ids to models."""
    bot = telegram.Bot(token=token)
    results = {}

    for col in collections:
        key = col["key"]
        pack = col["pack_name"]
        if not pack or not key:
            print(f"  SKIP {col['name']!r} — no pack name or unknown catalog key")
            continue

        print(f"  Fetching {col['name']!r} ({key}) pack: {pack}")
        try:
            ss = await bot.get_sticker_set(pack)
            stickers = ss.stickers
            print(f"    Got {len(stickers)} stickers, {len(col['models'])} models")

            mapping = {}
            for i, (model_name, sticker) in enumerate(zip(col["models"], stickers), start=1):
                eid = sticker.custom_emoji_id
                mapping[model_name] = eid
                print(f"    #{i:3d} {model_name!r:30s} → {eid}")

            results[key] = mapping
        except Exception as e:
            print(f"    ERROR: {e}")

    return results


async def main():
    gifts_path = sys.argv[1] if len(sys.argv) > 1 else "/home/ogkush/Desktop/gifts.txt"

    import os
    from dotenv import load_dotenv
    load_dotenv("/home/ogkush/Projects/wrkshelperbot/.env")
    token = os.environ["BOT_TOKEN"]

    print(f"Parsing {gifts_path}...")
    collections = parse_gifts_file(gifts_path)
    print(f"Found {len(collections)} collections\n")

    print("Fetching emoji IDs from Telegram...")
    results = await fetch_emoji_ids(token, collections)

    out_path = "/home/ogkush/Projects/wrkshelperbot/data/model_emoji_ids.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out_path}")
    print(f"Got IDs for {len(results)} collections")


if __name__ == "__main__":
    asyncio.run(main())
