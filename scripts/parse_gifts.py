#!/usr/bin/env python3
"""Parse /home/ogkush/Desktop/gifts.txt into data/gift_catalog.py."""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

TIER_MAP = {
    # High tier
    "artisan_brick":    ("high", 45000),
    "astral_shard":     ("high", 50000),
    "durovs_cap":       ("high", 130000),
    "heart_locket":     ("high", 120000),
    "heroic_helmet":    ("high", 40000),
    "kissed_frog":      ("high", 25000),
    "mighty_arm":       ("high", 55000),
    "nail_bracelet":    ("high", 60000),
    "neko_helmet":      ("high", 22000),
    "plush_pepe":       ("high", 140000),
    "precious_peach":   ("high", 70000),
    "rare_bird":        ("high", 65000),
    "scared_cat":       ("high", 125000),
    "westside_sign":    ("high", 28000),
    # Mid tier
    "bonded_ring":      ("mid", 7000),
    "crystal_ball":     ("mid", 6500),
    "cupid_charm":      ("mid", 6000),
    "diamond_ring":     ("mid", 7500),
    "electric_skull":   ("mid", 6000),
    "eternal_rose":     ("mid", 5500),
    "gem_signet":       ("mid", 3500),
    "ion_gem":          ("mid", 5000),
    "ionic_dryer":      ("mid", 10000),
    "khabibs_papakha":  ("mid", 6500),
    "loot_bag":         ("mid", 5500),
    "love_potion":      ("mid", 5000),
    "low_rider":        ("mid", 7000),
    "mad_pumpkin":      ("mid", 5500),
    "magic_potion":     ("mid", 3000),
    "mini_oscar":       ("mid", 6000),
    "perfume_bottle":   ("mid", 7000),
    "record_player":    ("mid", 6500),
    "sharp_tongue":     ("mid", 5000),
    "signet_ring":      ("mid", 6000),
    "snoop_cigar":      ("mid", 4000),
    "swiss_watch":      ("mid", 11000),
    "top_hat":          ("mid", 3500),
    "trapped_heart":    ("mid", 3500),
    "ufc_strike":       ("mid", 7000),
    "vintage_cigar":    ("mid", 10000),
    "voodoo_doll":      ("mid", 4000),
}

# Headers that look like collection headers but are editorial notes — skip them
SKIP_HEADER_PATTERNS = [
    r"^Some .+ models have been renamed",
    r"^This is an updated post",
    r"^New NFT convertibles",
    r"^These are the \d+ models",
]

def to_key(name: str) -> str:
    name = name.replace("’", "").replace("'", "").replace("‘", "")
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

def _is_model_line(text: str) -> tuple[str, str, float] | None:
    """Return (emoji, model_name, pct) if line looks like a model entry, else None."""
    # Match: <emoji> <model name> — <pct>%  (em dash U+2014 or en dash U+2013)
    m = re.match(r"^(\S+)\s+(.+?)\s+[—–]\s*([\d.]+)%\s*$", text)
    if m:
        return m.group(1), m.group(2).strip(), float(m.group(3))
    return None

def _extract_collection_name(header_text: str) -> str | None:
    """
    Given the text after 'Gift Models 🎁: ', determine the collection name.
    Returns None if it's an editorial line or a dangling model line.
    """
    # Strip trailing stuff like "• Emoji ..." and "(N)"
    name = re.sub(r"\s*\(?\d+\)?\s*•.*$", "", header_text).strip()
    name = re.sub(r"\s*•.*$", "", name).strip()

    # Check if it's an editorial/note line
    for pat in SKIP_HEADER_PATTERNS:
        if re.search(pat, name):
            return None

    # If it looks like a model line (has em/en dash + percentage), it's a dangling model
    if re.search(r"[—–]\s*[\d.]+%", name):
        return None

    # Strip trailing "Models <emoji>" or just "Models"
    name = re.sub(r"\s+Models\s*(\S+\s*)?$", "", name).strip()
    name = re.sub(r"\s+Models\s*$", "", name).strip()

    if not name:
        return None
    return name

def parse_gifts(path: str) -> dict:
    collections: dict = {}
    order: list = []   # preserve insertion order for deduplication (last wins)
    current: str | None = None
    model_num = 0

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()

            # Check for a collection header
            m = re.search(r"Gift Models 🎁: (.+?)$", line)
            if m:
                header_body = m.group(1).strip()

                # Skip crafted collections
                if "Crafted" in header_body:
                    current = None
                    continue

                col_name = _extract_collection_name(header_body)
                if col_name is None:
                    # Could be a dangling model line — try to parse it as a model
                    # and append to current collection
                    parsed = _is_model_line(header_body)
                    if parsed and current:
                        emoji, model_name, pct = parsed
                        model_num += 1
                        collections[current]["models"].append({
                            "number": model_num,
                            "name": model_name,
                            "rarity_pct": pct,
                            "custom_emoji_id": None,
                        })
                    current = None
                    continue

                key = to_key(col_name)

                # If we've seen this key before (duplicate), overwrite it
                # but keep track so we output in first-seen order
                if key not in collections:
                    order.append(key)
                collections[key] = {"name": col_name, "emoji": "🎁", "models": []}
                current = key
                model_num = 0
                continue

            if current is None:
                continue

            # Try to parse as a model line
            parsed = _is_model_line(line)
            if not parsed:
                continue
            emoji, model_name, pct = parsed
            if not collections[current]["models"]:
                collections[current]["emoji"] = emoji
            model_num += 1
            collections[current]["models"].append({
                "number": model_num,
                "name": model_name,
                "rarity_pct": pct,
                "custom_emoji_id": None,
            })

    # Return in first-seen order
    return {k: collections[k] for k in order if k in collections}

def generate(path: str) -> str:
    collections = parse_gifts(path)
    lines = ["CATALOG = {"]
    for key in collections:
        col = collections[key]
        if not col["models"]:
            continue
        tier, base_price = TIER_MAP.get(key, ("low", 1000))
        lines.append(f'    "{key}": {{')
        lines.append(f'        "name": {col["name"]!r},')
        lines.append(f'        "emoji": {col["emoji"]!r},')
        lines.append(f'        "tier": {tier!r},')
        lines.append(f'        "base_price": {base_price},')
        lines.append(f'        "models": [')
        for mdl in col["models"]:
            lines.append(
                f'            {{"number": {mdl["number"]}, "name": {mdl["name"]!r}, '
                f'"rarity_pct": {mdl["rarity_pct"]}, "custom_emoji_id": None}},'
            )
        lines.append(f'        ],')
        lines.append(f'    }},')
    lines.append("}")
    return "\n".join(lines)

if __name__ == "__main__":
    src = "/home/ogkush/Desktop/gifts.txt"
    out = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "gift_catalog.py")
    catalog_str = generate(src)
    with open(out, "w", encoding="utf-8") as f:
        f.write(catalog_str + "\n")
    print(f"Written to {out}")
    # Verify
    import importlib.util
    spec = importlib.util.spec_from_file_location("gift_catalog", out)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    CATALOG = mod.CATALOG
    total_models = sum(len(c["models"]) for c in CATALOG.values())
    print(f"Collections: {len(CATALOG)}, Total models: {total_models}")
    # Check a few known collections
    assert "scared_cat" in CATALOG, "scared_cat missing"
    assert CATALOG["scared_cat"]["tier"] == "high"
    assert CATALOG["scared_cat"]["base_price"] == 125000
    assert "santa_hat" in CATALOG, "santa_hat missing"
    assert CATALOG["santa_hat"]["tier"] == "low"
    # Check collections with no models get skipped
    empty = [k for k, v in CATALOG.items() if not v["models"]]
    assert not empty, f"Empty collections found: {empty}"
    print("Spot checks passed.")
    # Print all collections for review
    for key, col in CATALOG.items():
        print(f"  {key}: {len(col['models'])} models, tier={col['tier']}, price={col['base_price']}")
