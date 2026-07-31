import sqlite3

from gift_artwork import (
    clean_gift_model_name,
    sync_gift_custom_emoji_ids_connection,
)


def test_gift_model_name_cleanup_handles_catalog_edge_cases():
    assert clean_gift_model_name("\u200d⬛ Misty Ash") == "Misty Ash"
    assert clean_gift_model_name("№19") == "№19"
    assert clean_gift_model_name("№5 L'eau") == "№5 L'eau"
    assert (
        clean_gift_model_name(
            "[1/8/25 3:05 AM] Gift Models 🎁: 🎁 Absinthe"
        )
        == "Absinthe"
    )


def test_artwork_sync_repairs_existing_models_from_the_catalog():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE gift_models ("
        "collection TEXT, model_name TEXT, custom_emoji_id TEXT)"
    )
    connection.executemany(
        "INSERT INTO gift_models (collection, model_name) VALUES (?, ?)",
        [
            ("spiced_wine", "Absinthe"),
            ("perfume_bottle", "№19"),
            ("perfume_bottle", "№5 L'eau"),
            ("homemade_cake", "Choco Dream"),
        ],
    )

    assert sync_gift_custom_emoji_ids_connection(connection) == 4
    assert connection.execute(
        "SELECT collection, model_name, custom_emoji_id "
        "FROM gift_models ORDER BY collection, model_name"
    ).fetchall() == [
        ("homemade_cake", "Choco Dream", "5458566823744136801"),
        ("perfume_bottle", "№19", "5456361392397378741"),
        ("perfume_bottle", "№5 L'eau", "5454149295261377582"),
        ("spiced_wine", "Absinthe", "5460983503057345562"),
    ]
    assert sync_gift_custom_emoji_ids_connection(connection) == 0
