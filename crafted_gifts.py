"""Crafted Telegram gift variants that were released after the base catalog."""

import sqlite3


CRAFTED_GIFT_MODELS = {
    "desk_calendar": [
        (101, "Óðinsdagr", "5208568717453070401"),
        (102, "Day of Mars", "5206436592608120719"),
        (103, "Celestial Map", "5206555107935688206"),
        (104, "Loki's Day", "5206455782521997867"),
        (105, "May the Fourth", "5208666496678531072"),
        (106, "Aphrodite", "5206244444361234933"),
        (107, "Selena", "5208806332223757383"),
        (108, "Týsdagr", "5206531206442684698"),
        (109, "Frjádagr", "5208959628196487300"),
        (110, "Þórsdagr", "5206285199305906367"),
        (111, "TON Core", "5208426687179560514"),
        (112, "Kronos", "5208760569347217172"),
        (113, "Payday", "5208556262047910225"),
        (114, "Royal Flush", "5206171223758769570"),
        (115, "Sol Invictus", "5206487603934696683"),
        (116, "Shinto Shrine", "5208477281894307967"),
        (117, "Lucky Day", "5208715016924074189"),
        (118, "Mánadagr", "5208601367794454437"),
        (119, "Treasure Map", "5206469401863294279"),
        (120, "Glam Day", "5208974205315485953"),
        (121, "Frog Day", "5208756764006193080"),
        (122, "First Date", "5208942242168870111"),
        (123, "Space Era", "5206409590148732058"),
        (124, "Weekly Set", "5208594023400378490"),
        (125, "Grimoire", "5206299166539549817"),
        (126, "Cat Seasons", "5208908243207753194"),
        (127, "Artwork", "5208605877510116935"),
        (128, "Ghost Party", "5208584750565987519"),
        (129, "Zeus", "5206375720036635530"),
        (130, "Outlaw", "5206384378690704963"),
        (131, "Samhain", "5206603078425414788"),
        (132, "Hermes", "5208828283801603072"),
        (133, "Count Dracula", "5208594805084427178"),
        (134, "Wedding", "5208624710941708935"),
        (135, "Sekhmet", "5208600233923086856"),
        (136, "Cyberpunk", "5208481851739509872"),
        (137, "Anniversary", "5208964902416323146"),
        (138, "Crunch Time", "5208915823825033400"),
        (139, "Mesozoic", "5208535349852146242"),
        (140, "Orchestra", "5206593028201942059"),
        (141, "Daily Bread", "5206376948397281319"),
        (142, "Helios", "5206320237649108238"),
        (143, "God of Wine", "5206551470098386675"),
        (144, "Steampunk", "5206355842927989159"),
        (145, "Launch Date", "5206662988924228977"),
        (146, "Shuffle", "5206498800914437095"),
        (147, "Time Spin", "5206390267090869264"),
        (148, "Holy Month", "5208945983085384385"),
        (149, "Anno Domini", "5208669674954333284"),
        (150, "New Year", "5206673674802862568"),
        (151, "Vacation", "5206449696553343221"),
        (152, "Shopping List", "5208883581505540992"),
        (153, "April Fools", "5208823653826861783"),
        (154, "Women's Day", "5208727382134920654"),
        (155, "Vintage", "5206330403836695520"),
        (156, "Yoga Time", "5208638051110131594"),
        (157, "Toy Calendar", "5206533100523263006"),
    ],
    "jingle_bells": [
        (101, "Hot Cherry", "5206461262900267776"),
        (102, "Dragon Lantern", "5208829688255909865"),
        (103, "Golden Dice", "5206513085975663959"),
        (104, "Maneki Neko", "5208636814159549649"),
        (105, "Duality", "5206356843655367908"),
        (106, "Krampus", "5208896109925141210"),
        (107, "Cash Bags", "5206210355205804805"),
        (108, "Little Gifts", "5206436334910084924"),
        (109, "Silver Maces", "5208577754064263611"),
        (110, "Lucky Bell", "5206405252231762469"),
        (111, "White Owl", "5208693834145370231"),
        (112, "Stranding", "5206429677710774932"),
        (113, "Hedgehogs", "5208583505025471863"),
        (114, "Mushrooms", "5208830774882635516"),
        (115, "Black Gold", "5208427876885500987"),
        (116, "Tinker Bell", "5208575267278195942"),
        (117, "Jungle Bloom", "5206304539543638167"),
        (118, "Circus", "5206208534139672519"),
        (119, "Dolls", "5208421468794294435"),
        (120, "Nutcracker", "5206301975448162740"),
        (121, "Cash Machine", "5208894980348744334"),
        (122, "Bullfinch", "5206533276616919393"),
        (123, "Love Song", "5208854135209763911"),
        (124, "Grinch", "5206204784633225164"),
        (125, "Royal Call", "5208915823825033406"),
        (126, "Wind Chimes", "5206257608435995477"),
        (127, "Candy Houses", "5208798429483928220"),
        (128, "Noble Pearl", "5208706255190790110"),
        (129, "Ice Queen", "5208541659159106507"),
        (130, "Fabergé", "5208529693380219134"),
        (131, "Crystal", "5206472807772361474"),
        (132, "Blue Sapphire", "5206699728074479260"),
        (133, "Santa Claus", "5208727815926617091"),
        (134, "Pharaoh", "5206483197298250104"),
        (135, "Sleigh Bells", "5208694066073607285"),
        (136, "Red Lotus", "5206255375053000971"),
        (137, "Sylvan Echo", "5206502537535991508"),
        (138, "Sarcophagus", "5208899412754994348"),
        (139, "Peonies", "5206391254933344431"),
        (140, "Royal Charm", "5206279053207703413"),
        (141, "Festive Night", "5208607702871215024"),
        (142, "Pink Bow", "5208879406797329902"),
        (143, "Spring Knell", "5208937169812495011"),
        (144, "Royal Hour", "5206195644942815969"),
        (145, "Festive Duo", "5208806512612378235"),
        (146, "Cozy Winter", "5208471689846887512"),
        (147, "Flashlights", "5206683488803131312"),
        (148, "Purple Jingle", "5206346784841961114"),
        (149, "Steampunk", "5206204943547011614"),
        (150, "Reindeer", "5208413166622512066"),
        (151, "Orchestra", "5206292247347237336"),
    ],
}

_BACKGROUNDS = ("black", "onyx", "grape", "emerald", "midnight", "orange")


def seed_crafted_gifts_connection(connection: sqlite3.Connection) -> int:
    """Add missing crafted models and their Rift inventory idempotently."""
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gift_models'"
    ).fetchone()
    if not table_exists:
        return 0

    before = connection.total_changes
    for collection, models in CRAFTED_GIFT_MODELS.items():
        template = connection.execute(
            "SELECT model_emoji, tier FROM gift_models WHERE collection = ? LIMIT 1",
            (collection,),
        ).fetchone()
        if not template:
            continue
        model_emoji, tier = template[0], template[1]
        for model_number, model_name, emoji_id in models:
            connection.execute(
                "INSERT OR IGNORE INTO gift_models "
                "(collection, model_number, model_name, model_emoji, "
                "model_rarity_pct, tier, custom_emoji_id) "
                "VALUES (?, ?, ?, ?, 0, ?, ?)",
                (collection, model_number, model_name, model_emoji, tier, emoji_id),
            )
            connection.execute(
                "UPDATE gift_models SET model_name = ?, model_rarity_pct = 0, "
                "custom_emoji_id = ? WHERE collection = ? AND model_number = ? "
                "AND (model_name <> ? OR model_rarity_pct <> 0 "
                "OR COALESCE(custom_emoji_id, '') <> ?)",
                (
                    model_name,
                    emoji_id,
                    collection,
                    model_number,
                    model_name,
                    emoji_id,
                ),
            )
            model_id = connection.execute(
                "SELECT id FROM gift_models WHERE collection = ? AND model_number = ?",
                (collection, model_number),
            ).fetchone()[0]
            for background_index, background in enumerate(_BACKGROUNDS, 1):
                gift_number = (model_number - 1) * len(_BACKGROUNDS) + background_index
                connection.execute(
                    "INSERT OR IGNORE INTO gift_instances "
                    "(model_id, background, gift_number) VALUES (?, ?, ?)",
                    (model_id, background, gift_number),
                )
                connection.execute(
                    "UPDATE gift_instances SET gift_number = ? "
                    "WHERE model_id = ? AND background = ? AND gift_number IS NULL",
                    (gift_number, model_id, background),
                )
    connection.commit()
    return connection.total_changes - before


def seed_crafted_gifts(db_path: str) -> int:
    with sqlite3.connect(db_path) as connection:
        return seed_crafted_gifts_connection(connection)
