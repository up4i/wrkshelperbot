import json
import re
import sqlite3
from pathlib import Path


_DEFAULT_IDS_PATH = Path(__file__).resolve().parent / "data" / "model_emoji_ids.json"


def clean_gift_model_name(name: str) -> str:
    """Normalize model names copied from Telegram gift-model dumps."""
    if "Gift Models 🎁:" in name:
        name = name.rsplit("Gift Models 🎁:", 1)[1]
    name = re.sub(r"^[\u200d\u200c\ufe0f\u20e3⬛\s]+", "", name)
    name = re.sub(r"^[^\w№'\"(]+", "", name)
    return name.strip()


def gift_artwork_rows(ids_path: str | Path | None = None) -> list[tuple[str, str, str]]:
    path = Path(ids_path) if ids_path is not None else _DEFAULT_IDS_PATH
    with path.open(encoding="utf-8") as source:
        id_map = json.load(source)

    rows: list[tuple[str, str, str]] = []
    for collection, models in id_map.items():
        for raw_name, emoji_id in models.items():
            model_name = clean_gift_model_name(raw_name)
            if model_name and emoji_id:
                rows.append((str(emoji_id), collection, model_name))
    return rows


def sync_gift_custom_emoji_ids_connection(
    connection: sqlite3.Connection,
    ids_path: str | Path | None = None,
) -> int:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gift_models'"
    ).fetchone()
    if not table_exists:
        return 0

    before = connection.total_changes
    connection.executemany(
        "UPDATE gift_models SET custom_emoji_id = ? "
        "WHERE collection = ? AND model_name = ? "
        "AND COALESCE(custom_emoji_id, '') <> ?",
        [
            (emoji_id, collection, model_name, emoji_id)
            for emoji_id, collection, model_name in gift_artwork_rows(ids_path)
        ],
    )
    connection.commit()
    return connection.total_changes - before


def sync_gift_custom_emoji_ids(
    db_path: str,
    ids_path: str | Path | None = None,
) -> int:
    with sqlite3.connect(db_path) as connection:
        return sync_gift_custom_emoji_ids_connection(connection, ids_path)
