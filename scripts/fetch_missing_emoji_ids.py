#!/usr/bin/env python3
"""
Fetch custom_emoji_ids for the 88 collections missing from model_emoji_ids.json.
Merges results into data/model_emoji_ids.json and then applies to DB.

Usage:
    cd /home/ogkush/Projects/wrkshelperbot
    python3 scripts/fetch_missing_emoji_ids.py
"""

import asyncio
import json
import os
import sys

import telegram
from dotenv import load_dotenv

load_dotenv()

# collection_key → sticker set name (extracted from addemoji URL)
PACK_MAP = {
    "jelly_bunny":      "gift0emoji_5915502858152706668_by_GiftChangesHelper4Bot",
    "lunar_snake":      "gift_emoji_6028426950047957932_by_GiftChangesHelper2Bot",
    "party_sparkler":   "gift_emoji_6003643167683903930_by_GiftChangesHelper2Bot",
    "jester_hat":       "gift_emoji_5933590374185435592_by_GiftChangesHelper1Bot",
    "genie_lamp":       "gift_emoji_5933531623327795414_by_GiftChangesHelper2Bot",
    "cookie_heart":     "gift_emoji_6001538689543439169_by_GiftChangesHelper2Bot",
    "jingle_bells":     "gift_emoji_6001473264306619020_by_GiftChangesHelper1Bot",
    "snow_mittens":     "gift_emoji_5980789805615678057_by_GiftChangesHelper3Bot",
    "hanging_star":     "gift_emoji_5915733223018594841_by_GiftChangesHelper3Bot",
    "love_candle":      "gift_emoji_5915550639663874519_by_GiftChangesHelper6Bot",
    "desk_calendar":    "gift_emoji_5782988952268964995_by_GiftChangesHelper5Bot",
    "b_day_candle":     "gift_emoji_5782984811920491178_by_GiftChangesHelper1Bot",
    "bunny_muffin":     "gift_emoji_5935936766358847989_by_GiftChangesHelper5Bot",
    "astral_shard":     "gift_emoji_5933629604416717361_by_GiftChangesHelper5Bot",
    "mad_pumpkin":      "gift_emoji_5841632504448025405_by_GiftChangesHelper1Bot",
    "voodoo_doll":      "gift_emoji_5836780359634649414_by_GiftChangesHelper4Bot",
    "hypno_lollipop":   "gift_emoji_5170594532177215681_by_GiftChangesHelper2Bot",
    "swiss_watch":      "gift_emoji_5936043693864651359_by_GiftChangesHelper3Bot",
    "crystal_ball":     "gift_emoji_5841336413697606412_by_GiftChangesHelper3Bot",
    "flying_broom":     "gift_emoji_5837063436634161765_by_GiftChangesHelper2Bot",
    "eternal_candle":   "gift_emoji_5821205665758053411_by_GiftChangesHelper1Bot",
    "ginger_cookie":    "gift_emoji_5983484377902875708_by_GiftChangesHelper5Bot",
    "mini_oscar":       "gift_emoji_5879737836550226478_by_GiftChangesHelper5Bot",
    "ion_gem":          "gift_emoji_5843762284240831056_by_GiftChangesHelper2Bot",
    "lol_pop":          "gift_emoji_5170594532177215681_by_GiftChangesHelper5Bot",
    "star_notepad":     "gift_emoji_5936017773737018241_by_GiftChangesHelper5Bot",
    "love_potion":      "gift_emoji_5868348541058942091_by_GiftChangesHelper1Bot",
    "loot_bag":         "gift_emoji_5868659926187901653_by_GiftChangesHelper3Bot",
    "toy_bear":         "gift_emoji_5868220813026526561_by_GiftChangesHelper1Bot",
    "diamond_ring":     "gift_emoji_5868503709637411929_by_GiftChangesHelper3Bot",
    "top_hat":          "gift_emoji_5897593557492957738_by_GiftChangesHelper2Bot",
    "sleigh_bell":      "gift_emoji_5981026247860290310_by_GiftChangesHelper4Bot",
    "sakura_flower":    "gift_emoji_5167939598143193218_by_GiftChangesHelper1Bot",
    "record_player":    "gift_emoji_5856973938650776169_by_GiftChangesHelper6Bot",
    "tama_gadget":      "gift_emoji_6023752243218481939_by_GiftChangesHelper4Bot",
    "candy_cane":       "gift_emoji_6003373314888696650_by_GiftChangesHelper2Bot",
    "winter_wreath":    "gift_emoji_5983259145522906006_by_GiftChangesHelper3Bot",
    "snow_globe":       "gift_emoji_5981132629905245483_by_GiftChangesHelper3Bot",
    "electric_skull":   "gift_emoji_5846192273657692751_by_GiftChangesHelper6Bot",
    "neko_helmet":      "gift_emoji_5933793770951673155_by_GiftChangesHelper4Bot",
    "witch_hat":        "gift_emoji_5821384757304362229_by_GiftChangesHelper2Bot",
    "jack_in_the_box":  "gift_emoji_6005659564635063386_by_GiftChangesHelper2Bot",
    "easter_egg":       "gift_emoji_5773668482394620318_by_GiftChangesHelper2Bot",
    "nail_bracelet":    "NailBracelet_a964_emoji_by_GiftChangesHelper1Bot",
    "restless_jar":     "RestlessJar_1ee5_emoji_by_GiftChangesHelper2Bot",
    "heroic_helmet":    "RomanHelmet_f831_emoji_by_GiftChangesHelper1Bot",
    "bow_tie":          "BowTieSkins_ae12_emoji_by_GiftChangesHelper2Bot",
    "heart_locket":     "SailorHeartsdgldjgd_da3a_emoji_by_GiftChangesHelper1Bot",
    "lush_bouquet":     "LovingGift_3bcc_emoji_by_GiftChangesHelper1Bot",
    "whip_cupcake":     "Cupcakes_b1d4_emoji_by_GiftChangesHelper1Bot",
    "joyful_bundle":    "Love_YOU_package_1886_8f61_emoji_by_GiftChangesHelper2Bot",
    "valentine_box":    "heartshapedbox_9c79_emoji_by_GiftChangesHelper3Bot",
    "cupid_charm":      "CupidCharm_768a_emoji_by_GiftChangesHelper4Bot",
    "snoop_dogg":       "Dogs_2eda_emoji_by_GiftChangesHelper1Bot",
    "swag_bag":         "SwagBags_2178_emoji_by_GiftChangesHelper2Bot",
    "snoop_cigar":      "SnoopsCigars_3eca_emoji_by_GiftChangesHelper3Bot",
    "low_rider":        "Cars_776b_emoji_by_GiftChangesHelper4Bot",
    "westside_sign":    "WestSide_91ce_emoji_by_GiftChangesHelper5Bot",
    "ionic_dryer":      "Dyson_61bf_emoji_by_GiftChangesHelper1Bot",
    "moon_pendant":     "Crescent_f0ba_emoji_by_GiftChangesHelper2Bot",
    "jolly_chimp":      "ToyPrimate_2ce8_emoji_by_GiftChangesHelper3Bot",
    "stellar_rocket":   "Rocketblowwowboom23_5c65_emoji_by_GiftChangesHelper4Bot",
    "input_key":        "KeyKeyandKey_263e_emoji_by_GiftChangesHelper1Bot",
    "artisan_brick":    "Brick_d659_emoji_by_GiftChangesHelper1Bot",
    "mighty_arm":       "Bitsushka_e63a_emoji_by_GiftChangesHelper2Bot",
    "clover_pin":       "Brooch_9f32_emoji_by_GiftChangesHelper1Bot",
    "fresh_socks":      "SocksSkins_0eeb_emoji_by_GiftChangesHelper2Bot",
    "sky_stilettos":    "Heelsrgsbfy_11e2_emoji_by_GiftChangesHelper3Bot",
    "happy_brownie":    "HappyBrownie_emoji_by_GiftChangesHelper1Bot",
    "spring_basket":    "SpringBasket_emoji_by_GiftChangesHelper2Bot",
    "instant_ramen":    "InstantRamen_emoji_by_GiftChangesHelper3Bot",
    "faith_amulet":     "FaithAmulet_emoji_by_GiftChangesHelper4Bot",
    "mousse_cake":      "MousseCake_emoji_by_GiftChangesHelper5Bot",
    "ice_cream":        "IceCreamGiftsdfdbd_emoji_by_GiftChangesHelper1Bot",
    "bling_binky":      "BlingBinky_emoji_by_GiftChangesHelper1Bot",
    "money_pot":        "MoneyPot_emoji_by_GiftChangesHelper2Bot",
    "pretty_posy":      "PrettyPosy_emoji_by_GiftChangesHelper1Bot",
    "khabibs_papakha":  "Papakhadfgdsgts_emoji_by_GiftChangesHelper1Bot",
    "ufc_strike":       "UFCStrike_emoji_by_GiftChangesHelper1Bot",
    "victory_medal":    "VictoryMedal_emoji_by_GiftChangesHelper2Bot",
    "rare_bird":        "RareBird_emoji_by_GiftChangesHelper3Bot",
    "mood_pack":        "MoodPack_emoji_by_GiftChangesHelper1Bot",
    "pool_float":       "PoolFloat_emoji_by_GiftChangesHelper2Bot",
    "timeless_book":    "TimelessBook_emoji_by_GiftChangesHelper1Bot",
    "chill_flame":      "ChillFlame_emoji_by_GiftChangesHelper1Bot",
    "vice_cream":       "ViceCream_emoji_by_GiftChangesHelper2Bot",
    "surge_board":      "surgeboard_emoji_by_gftchngswrkr1",
    "liberty_figure":   "libertyfigure_emoji_by_gftchngswrkr1",
}

IDS_PATH = os.path.join(os.path.dirname(__file__), "../data/model_emoji_ids.json")


async def main():
    token = os.environ["BOT_TOKEN"]
    bot = telegram.Bot(token=token)

    # Load catalog to get model names in order
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from data.gift_catalog import CATALOG

    # Load existing IDs
    with open(IDS_PATH) as f:
        all_ids = json.load(f)

    errors = []
    for col_key, pack_name in PACK_MAP.items():
        if col_key not in CATALOG:
            print(f"  SKIP {col_key} — not in catalog")
            continue

        models = CATALOG[col_key]["models"]
        print(f"  Fetching {col_key!r} ({len(models)} models) — {pack_name}")
        try:
            ss = await bot.get_sticker_set(pack_name)
            stickers = ss.stickers
            print(f"    Got {len(stickers)} stickers")

            mapping = {}
            for mdl, sticker in zip(models, stickers):
                eid = sticker.custom_emoji_id
                mapping[mdl["name"]] = eid

            if len(stickers) < len(models):
                print(f"    ⚠️  Pack has {len(stickers)} stickers but catalog has {len(models)} models — partial match")

            all_ids[col_key] = mapping
        except Exception as e:
            print(f"    ERROR: {e}")
            errors.append((col_key, str(e)))

    with open(IDS_PATH, "w") as f:
        json.dump(all_ids, f, indent=2, ensure_ascii=False)

    total = sum(len(v) for v in all_ids.values())
    print(f"\nSaved {len(all_ids)} collections / {total} models to {IDS_PATH}")
    if errors:
        print(f"\nFailed ({len(errors)}):")
        for k, e in errors:
            print(f"  {k}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
