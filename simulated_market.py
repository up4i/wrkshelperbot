from __future__ import annotations

import random
import sqlite3
import time
from decimal import Decimal, ROUND_DOWN

from game_tokens import TOKEN_SCALE, format_token_amount, parse_token_amount
from market_config import GAME_TOKEN_SYMBOLS, MARKET_TOKENS, MARKET_TOKEN_BY_SYMBOL
from token_market import get_gram_reference


SWAP_FEE_BPS = 30
FEE_DENOMINATOR = 10_000
CANDLE_SECONDS = 5 * 60
HISTORY_HOURS = 30 * 24
SIMULATION_TICK_SECONDS = 55

SIMULATED_MARKET_SCHEMA = """
CREATE TABLE IF NOT EXISTS simulated_market_pools (
    symbol                   TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    circulating_supply       INTEGER NOT NULL CHECK(circulating_supply > 0),
    reserve_token            INTEGER NOT NULL CHECK(reserve_token > 0),
    reserve_gram             INTEGER NOT NULL CHECK(reserve_gram > 0),
    anchor_price_gram        TEXT NOT NULL,
    lifetime_volume_gram     INTEGER NOT NULL DEFAULT 0,
    legacy_holders           INTEGER NOT NULL DEFAULT 0,
    created_at               INTEGER NOT NULL,
    updated_at               INTEGER NOT NULL,
    last_trade_at            INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS simulated_market_trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER,
    from_symbol    TEXT NOT NULL,
    to_symbol      TEXT NOT NULL,
    input_amount   INTEGER NOT NULL,
    output_amount  INTEGER NOT NULL,
    gram_value     INTEGER NOT NULL,
    price_impact   TEXT NOT NULL DEFAULT '0',
    is_simulated   INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sim_market_trades_created
ON simulated_market_trades(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sim_market_trades_symbols
ON simulated_market_trades(from_symbol, to_symbol, created_at DESC);
CREATE TABLE IF NOT EXISTS simulated_market_candles (
    symbol       TEXT NOT NULL,
    bucket_start INTEGER NOT NULL,
    open_gram    TEXT NOT NULL,
    high_gram    TEXT NOT NULL,
    low_gram     TEXT NOT NULL,
    close_gram   TEXT NOT NULL,
    volume_gram  INTEGER NOT NULL DEFAULT 0,
    trade_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, bucket_start)
);
CREATE TABLE IF NOT EXISTS simulated_market_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _as_atoms(value: str | Decimal) -> int:
    return parse_token_amount(str(value))


def _price_gram(row: sqlite3.Row | dict) -> Decimal:
    return Decimal(int(row["reserve_gram"])) / Decimal(int(row["reserve_token"]))


def _seed_history(connection: sqlite3.Connection, *, now: int) -> None:
    for token_index, token in enumerate(MARKET_TOKENS):
        existing = connection.execute(
            "SELECT 1 FROM simulated_market_candles WHERE symbol = ? LIMIT 1",
            (token.symbol,),
        ).fetchone()
        if existing:
            continue
        anchor = Decimal(token.initial_price_gram)
        rng = random.Random(f"wrk-stonk-history-v1:{token.symbol}")
        first_bucket = (now - HISTORY_HOURS * 3600) // 3600 * 3600
        price = anchor * Decimal(str(rng.uniform(0.82, 1.18)))
        candles = []
        for hour in range(HISTORY_HOURS):
            bucket = first_bucket + hour * 3600
            open_price = price
            mean_reversion = (anchor - price) * Decimal("0.025")
            noise = anchor * Decimal(str(rng.uniform(-0.018, 0.018)))
            close_price = max(anchor * Decimal("0.42"), price + mean_reversion + noise)
            wick = abs(anchor * Decimal(str(rng.uniform(0.002, 0.018))))
            high = max(open_price, close_price) + wick
            low = max(anchor * Decimal("0.2"), min(open_price, close_price) - wick)
            base_volume = Decimal(token.reserve_gram) * Decimal(str(rng.uniform(0.02, 0.11)))
            candles.append((
                token.symbol,
                bucket,
                str(open_price),
                str(high),
                str(low),
                str(close_price),
                _as_atoms(base_volume),
                rng.randint(7, 34),
            ))
            price = close_price
        # Make the historical series meet the starting pool price cleanly.
        last = list(candles[-1])
        last[4] = str(min(Decimal(last[4]), anchor))
        last[3] = str(max(Decimal(last[3]), anchor))
        last[5] = str(anchor)
        candles[-1] = tuple(last)
        connection.executemany(
            "INSERT INTO simulated_market_candles "
            "(symbol, bucket_start, open_gram, high_gram, low_gram, close_gram, "
            "volume_gram, trade_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            candles,
        )

        # Seed recent tape activity without changing the current pool reserves.
        recent_trades = []
        for trade_index in range(48):
            created_at = now - (48 - trade_index) * 30 * 60 - token_index * 17
            gram_value = _as_atoms(
                Decimal(token.reserve_gram) * Decimal(str(rng.uniform(0.0004, 0.004)))
            )
            is_buy = rng.random() < 0.52
            token_amount = max(
                1,
                int(Decimal(gram_value) / anchor),
            )
            recent_trades.append((
                None,
                "GRAM" if is_buy else token.symbol,
                token.symbol if is_buy else "GRAM",
                gram_value if is_buy else token_amount,
                token_amount if is_buy else gram_value,
                gram_value,
                str(Decimal(str(rng.uniform(0.0005, 0.009))) * 100),
                1,
                created_at,
            ))
        connection.executemany(
            "INSERT INTO simulated_market_trades "
            "(user_id, from_symbol, to_symbol, input_amount, output_amount, gram_value, "
            "price_impact, is_simulated, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            recent_trades,
        )


def initialize_market_connection(
    connection: sqlite3.Connection, *, now: int | None = None
) -> None:
    current_time = int(time.time()) if now is None else int(now)
    connection.executescript(SIMULATED_MARKET_SCHEMA)
    for token in MARKET_TOKENS:
        supply = _as_atoms(token.circulating_supply)
        reserve_gram = _as_atoms(token.reserve_gram)
        reserve_token = int(
            (Decimal(reserve_gram) / Decimal(token.initial_price_gram))
            .to_integral_value(rounding=ROUND_DOWN)
        )
        if reserve_token <= 0 or reserve_token >= supply:
            raise ValueError(f"Invalid initial pool reserves for {token.symbol}")
        created_at = current_time - token.age_days * 24 * 60 * 60
        connection.execute(
            "INSERT INTO simulated_market_pools "
            "(symbol, name, circulating_supply, reserve_token, reserve_gram, "
            "anchor_price_gram, lifetime_volume_gram, legacy_holders, created_at, "
            "updated_at, last_trade_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol) DO NOTHING",
            (
                token.symbol,
                token.name,
                supply,
                reserve_token,
                reserve_gram,
                token.initial_price_gram,
                _as_atoms(token.lifetime_volume_gram),
                token.legacy_holders,
                created_at,
                current_time,
                current_time - 45,
            ),
        )
    _seed_history(connection, now=current_time)
    connection.execute(
        "INSERT INTO simulated_market_state (key, value) VALUES ('seed_version', '1') "
        "ON CONFLICT(key) DO NOTHING"
    )
    connection.commit()


def initialize_market(db_path: str, *, now: int | None = None) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        initialize_market_connection(connection, now=now)


def _record_candle(
    connection: sqlite3.Connection,
    symbol: str,
    before: Decimal,
    after: Decimal,
    volume_gram: int,
    created_at: int,
) -> None:
    bucket = created_at // CANDLE_SECONDS * CANDLE_SECONDS
    existing = connection.execute(
        "SELECT * FROM simulated_market_candles WHERE symbol = ? AND bucket_start = ?",
        (symbol, bucket),
    ).fetchone()
    if existing:
        connection.execute(
            "UPDATE simulated_market_candles SET high_gram = ?, low_gram = ?, "
            "close_gram = ?, volume_gram = volume_gram + ?, trade_count = trade_count + 1 "
            "WHERE symbol = ? AND bucket_start = ?",
            (
                str(max(Decimal(existing["high_gram"]), before, after)),
                str(min(Decimal(existing["low_gram"]), before, after)),
                str(after),
                volume_gram,
                symbol,
                bucket,
            ),
        )
    else:
        connection.execute(
            "INSERT INTO simulated_market_candles "
            "(symbol, bucket_start, open_gram, high_gram, low_gram, close_gram, "
            "volume_gram, trade_count) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (
                symbol,
                bucket,
                str(before),
                str(max(before, after)),
                str(min(before, after)),
                str(after),
                volume_gram,
            ),
        )


def _constant_product_output(reserve_in: int, reserve_out: int, amount_in: int) -> int:
    amount_after_fee = amount_in * (FEE_DENOMINATOR - SWAP_FEE_BPS) // FEE_DENOMINATOR
    if amount_after_fee <= 0:
        raise ValueError("Swap amount is too small after the market fee")
    amount_out = reserve_out * amount_after_fee // (reserve_in + amount_after_fee)
    if amount_out <= 0 or amount_out >= reserve_out:
        raise ValueError("That trade is too large for the available liquidity")
    return amount_out


def _swap_pool_leg(
    connection: sqlite3.Connection,
    symbol: str,
    amount_in: int,
    *,
    gram_to_token: bool,
    created_at: int,
) -> tuple[int, int, Decimal, Decimal]:
    pool = connection.execute(
        "SELECT * FROM simulated_market_pools WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    if not pool:
        raise ValueError(f"${symbol} does not have a simulated liquidity pool")
    before = _price_gram(pool)
    reserve_in = int(pool["reserve_gram"] if gram_to_token else pool["reserve_token"])
    reserve_out = int(pool["reserve_token"] if gram_to_token else pool["reserve_gram"])
    amount_out = _constant_product_output(reserve_in, reserve_out, amount_in)
    if gram_to_token:
        new_reserve_gram = int(pool["reserve_gram"]) + amount_in
        new_reserve_token = int(pool["reserve_token"]) - amount_out
        gram_value = amount_in
    else:
        new_reserve_token = int(pool["reserve_token"]) + amount_in
        new_reserve_gram = int(pool["reserve_gram"]) - amount_out
        gram_value = amount_out
    if new_reserve_gram > 9_000_000_000_000_000_000 or new_reserve_token > 9_000_000_000_000_000_000:
        raise ValueError("That trade exceeds the simulated pool limits")
    after = Decimal(new_reserve_gram) / Decimal(new_reserve_token)
    connection.execute(
        "UPDATE simulated_market_pools SET reserve_token = ?, reserve_gram = ?, "
        "lifetime_volume_gram = lifetime_volume_gram + ?, updated_at = ?, last_trade_at = ? "
        "WHERE symbol = ?",
        (new_reserve_token, new_reserve_gram, gram_value, created_at, created_at, symbol),
    )
    _record_candle(connection, symbol, before, after, gram_value, created_at)
    return amount_out, gram_value, before, after


def execute_market_swap(
    connection: sqlite3.Connection,
    from_symbol: str,
    to_symbol: str,
    input_amount: int,
    *,
    user_id: int | None,
    is_simulated: bool = False,
    created_at: int | None = None,
) -> dict:
    from_symbol = from_symbol.upper()
    to_symbol = to_symbol.upper()
    if from_symbol == to_symbol or from_symbol not in GAME_TOKEN_SYMBOLS or to_symbol not in GAME_TOKEN_SYMBOLS:
        raise ValueError("Choose two different listed game tokens")
    if input_amount <= 0:
        raise ValueError("Swap amount must be positive")
    now = int(time.time()) if created_at is None else int(created_at)
    before_prices = {"GRAM": Decimal(1)}
    for symbol in {from_symbol, to_symbol} - {"GRAM"}:
        row = connection.execute(
            "SELECT * FROM simulated_market_pools WHERE symbol = ?", (symbol,)
        ).fetchone()
        if not row:
            raise ValueError(f"${symbol} market is unavailable")
        before_prices[symbol] = _price_gram(row)

    route = []
    if from_symbol == "GRAM":
        output_amount, gram_value, _before, _after = _swap_pool_leg(
            connection, to_symbol, input_amount, gram_to_token=True, created_at=now
        )
        route.append(to_symbol)
    elif to_symbol == "GRAM":
        output_amount, gram_value, _before, _after = _swap_pool_leg(
            connection, from_symbol, input_amount, gram_to_token=False, created_at=now
        )
        route.append(from_symbol)
    else:
        intermediate_gram, gram_value, _before, _after = _swap_pool_leg(
            connection, from_symbol, input_amount, gram_to_token=False, created_at=now
        )
        output_amount, second_volume, _before, _after = _swap_pool_leg(
            connection, to_symbol, intermediate_gram, gram_to_token=True, created_at=now
        )
        gram_value = max(gram_value, second_volume)
        route.extend((from_symbol, "GRAM", to_symbol))

    no_impact_output = (
        Decimal(input_amount)
        * before_prices[from_symbol]
        / before_prices[to_symbol]
    )
    impact = max(
        Decimal(0),
        (Decimal(1) - Decimal(output_amount) / no_impact_output) * Decimal(100),
    )
    connection.execute(
        "INSERT INTO simulated_market_trades "
        "(user_id, from_symbol, to_symbol, input_amount, output_amount, gram_value, "
        "price_impact, is_simulated, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            from_symbol,
            to_symbol,
            input_amount,
            output_amount,
            gram_value,
            str(impact),
            int(is_simulated),
            now,
        ),
    )
    return {
        "from_symbol": from_symbol,
        "to_symbol": to_symbol,
        "input_amount": input_amount,
        "output_amount": output_amount,
        "gram_value": gram_value,
        "price_impact_pct": str(impact.quantize(Decimal("0.0001"))),
        "fee_bps": SWAP_FEE_BPS * (2 if len(route) == 3 else 1),
        "route": route,
    }


def quote_market_swap(
    db_path: str,
    from_symbol: str,
    to_symbol: str,
    input_amount: int,
) -> dict:
    initialize_market(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        from_symbol = from_symbol.upper()
        to_symbol = to_symbol.upper()
        if from_symbol == to_symbol or from_symbol not in GAME_TOKEN_SYMBOLS or to_symbol not in GAME_TOKEN_SYMBOLS:
            raise ValueError("Choose two different listed game tokens")
        if input_amount <= 0:
            raise ValueError("Swap amount must be positive")
        pools = {}
        prices = {"GRAM": Decimal(1)}
        for symbol in {from_symbol, to_symbol} - {"GRAM"}:
            pool = connection.execute(
                "SELECT * FROM simulated_market_pools WHERE symbol = ?", (symbol,)
            ).fetchone()
            if not pool:
                raise ValueError(f"${symbol} market is unavailable")
            pools[symbol] = pool
            prices[symbol] = _price_gram(pool)
        if from_symbol == "GRAM":
            pool = pools[to_symbol]
            output_amount = _constant_product_output(
                int(pool["reserve_gram"]), int(pool["reserve_token"]), input_amount
            )
            gram_value = input_amount
            route = [to_symbol]
        elif to_symbol == "GRAM":
            pool = pools[from_symbol]
            output_amount = _constant_product_output(
                int(pool["reserve_token"]), int(pool["reserve_gram"]), input_amount
            )
            gram_value = output_amount
            route = [from_symbol]
        else:
            source_pool = pools[from_symbol]
            intermediate_gram = _constant_product_output(
                int(source_pool["reserve_token"]),
                int(source_pool["reserve_gram"]),
                input_amount,
            )
            target_pool = pools[to_symbol]
            output_amount = _constant_product_output(
                int(target_pool["reserve_gram"]),
                int(target_pool["reserve_token"]),
                intermediate_gram,
            )
            gram_value = intermediate_gram
            route = [from_symbol, "GRAM", to_symbol]
        no_impact_output = Decimal(input_amount) * prices[from_symbol] / prices[to_symbol]
        impact = max(
            Decimal(0),
            (Decimal(1) - Decimal(output_amount) / no_impact_output) * Decimal(100),
        )
        return {
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "input_amount": input_amount,
            "output_amount": output_amount,
            "gram_value": gram_value,
            "price_impact_pct": str(impact.quantize(Decimal("0.0001"))),
            "fee_bps": SWAP_FEE_BPS * (2 if len(route) == 3 else 1),
            "route": route,
        }


def _sparkline(connection: sqlite3.Connection, symbol: str, now: int) -> list[str]:
    rows = connection.execute(
        "SELECT close_gram FROM simulated_market_candles "
        "WHERE symbol = ? AND bucket_start >= ? ORDER BY bucket_start DESC LIMIT 24",
        (symbol, now - 24 * 60 * 60),
    ).fetchall()
    return [row["close_gram"] for row in reversed(rows)]


def get_market_snapshot(db_path: str, *, now: int | None = None) -> dict:
    current_time = int(time.time()) if now is None else int(now)
    initialize_market(db_path, now=current_time)
    gram = get_gram_reference()
    gram_usd = Decimal(gram["price_usd"])
    cutoff = current_time - 24 * 60 * 60
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        pools = connection.execute(
            "SELECT * FROM simulated_market_pools ORDER BY rowid"
        ).fetchall()
        tokens = []
        total_liquidity_gram = 0
        total_volume_24h = 0
        total_legacy_holders = 0
        oldest = current_time
        for pool in pools:
            symbol = pool["symbol"]
            price_gram = _price_gram(pool)
            price_usd = price_gram * gram_usd
            liquidity_gram_atoms = int(pool["reserve_gram"]) * 2
            volume_row = connection.execute(
                "SELECT COALESCE(SUM(gram_value), 0) AS volume, COUNT(*) AS trades "
                "FROM simulated_market_trades WHERE created_at >= ? "
                "AND (from_symbol = ? OR to_symbol = ?)",
                (cutoff, symbol, symbol),
            ).fetchone()
            old_candle = connection.execute(
                "SELECT close_gram FROM simulated_market_candles "
                "WHERE symbol = ? AND bucket_start <= ? "
                "ORDER BY bucket_start DESC LIMIT 1",
                (symbol, cutoff),
            ).fetchone()
            old_price = Decimal(old_candle["close_gram"]) if old_candle else price_gram
            change = (price_gram / old_price - 1) * 100 if old_price else Decimal(0)
            actual_holders = connection.execute(
                "SELECT COUNT(*) AS count FROM game_token_balances "
                "WHERE symbol = ? AND amount > 0",
                (symbol,),
            ).fetchone()["count"]
            supply_tokens = Decimal(int(pool["circulating_supply"])) / TOKEN_SCALE
            market_cap = supply_tokens * price_usd
            volume_atoms = int(volume_row["volume"])
            tokens.append({
                "symbol": symbol,
                "name": pool["name"],
                "kind": "simulated_amm",
                "price_usd": str(price_usd),
                "price_gram": str(price_gram),
                "price_change_24h": str(change),
                "market_cap_usd": str(market_cap),
                "liquidity_gram": format_token_amount(liquidity_gram_atoms),
                "liquidity_usd": str(Decimal(liquidity_gram_atoms) / TOKEN_SCALE * gram_usd),
                "volume_24h_gram": format_token_amount(volume_atoms),
                "volume_24h_usd": str(Decimal(volume_atoms) / TOKEN_SCALE * gram_usd),
                "lifetime_volume_gram": format_token_amount(int(pool["lifetime_volume_gram"])),
                "circulating_supply": format_token_amount(int(pool["circulating_supply"])),
                "holders": int(pool["legacy_holders"]) + int(actual_holders),
                "trades_24h": int(volume_row["trades"]),
                "age_days": max(1, (current_time - int(pool["created_at"])) // 86400),
                "updated_at": int(pool["updated_at"]),
                "source": "STONK.fi simulated AMM",
                "sparkline": _sparkline(connection, symbol, current_time),
                "fee_bps": SWAP_FEE_BPS,
                "stale": False,
            })
            total_liquidity_gram += liquidity_gram_atoms
            total_volume_24h += volume_atoms
            total_legacy_holders += int(pool["legacy_holders"])
            oldest = min(oldest, int(pool["created_at"]))

    gram_supply = Decimal("2800000000")
    gram_token = {
        **gram,
        "kind": "game_base",
        "price_gram": "1",
        "price_change_24h": "0",
        "market_cap_usd": str(gram_supply * gram_usd),
        "liquidity_gram": format_token_amount(total_liquidity_gram),
        "liquidity_usd": str(Decimal(total_liquidity_gram) / TOKEN_SCALE * gram_usd),
        "volume_24h_gram": format_token_amount(total_volume_24h),
        "volume_24h_usd": str(Decimal(total_volume_24h) / TOKEN_SCALE * gram_usd),
        "circulating_supply": str(gram_supply),
        "holders": total_legacy_holders,
        "trades_24h": sum(token["trades_24h"] for token in tokens),
        "age_days": max(1, (current_time - oldest) // 86400),
        "source": "Live GRAM anchor · simulated game balances",
        "sparkline": [],
        "fee_bps": 0,
        "stale": bool(gram.get("stale")),
    }
    return {
        "updated_at": current_time,
        "market_status": "active",
        "market_model": "constant_product_amm",
        "fee_bps": SWAP_FEE_BPS,
        "tokens": [gram_token, *tokens],
        "errors": {},
    }


def get_market_chart(db_path: str, symbol: str, *, hours: int = 24) -> dict:
    symbol = symbol.upper()
    if symbol not in MARKET_TOKEN_BY_SYMBOL:
        raise ValueError("Chart history is available for simulated memecoin pools")
    hours = max(1, min(int(hours), HISTORY_HOURS))
    cutoff = int(time.time()) - hours * 3600
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM simulated_market_candles WHERE symbol = ? AND bucket_start >= ? "
            "ORDER BY bucket_start",
            (symbol, cutoff),
        ).fetchall()
    return {"symbol": symbol, "hours": hours, "candles": [dict(row) for row in rows]}


def simulate_market_activity(db_path: str, *, now: int | None = None) -> int:
    current_time = int(time.time()) if now is None else int(now)
    initialize_market(db_path, now=current_time)
    with sqlite3.connect(db_path, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute(
            "SELECT value FROM simulated_market_state WHERE key = 'last_activity_tick'"
        ).fetchone()
        if state and current_time - int(state["value"]) < SIMULATION_TICK_SECONDS:
            connection.rollback()
            return 0
        connection.execute(
            "INSERT INTO simulated_market_state (key, value) VALUES ('last_activity_tick', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(current_time),),
        )
        pools = connection.execute("SELECT * FROM simulated_market_pools").fetchall()
        rng = random.Random(f"wrk-market-tick:{current_time // SIMULATION_TICK_SECONDS}")
        trade_count = min(len(pools), rng.randint(1, 3))
        for pool in rng.sample(pools, trade_count):
            price = _price_gram(pool)
            anchor = Decimal(pool["anchor_price_gram"])
            if price > anchor * Decimal("1.015"):
                buy = rng.random() < 0.22
            elif price < anchor * Decimal("0.985"):
                buy = rng.random() < 0.78
            else:
                buy = rng.random() < 0.51
            fraction = Decimal(str(rng.uniform(0.00015, 0.0012)))
            if buy:
                amount = max(1, int(Decimal(pool["reserve_gram"]) * fraction))
                execute_market_swap(
                    connection,
                    "GRAM",
                    pool["symbol"],
                    amount,
                    user_id=None,
                    is_simulated=True,
                    created_at=current_time,
                )
            else:
                amount = max(1, int(Decimal(pool["reserve_token"]) * fraction))
                execute_market_swap(
                    connection,
                    pool["symbol"],
                    "GRAM",
                    amount,
                    user_id=None,
                    is_simulated=True,
                    created_at=current_time,
                )
        connection.commit()
        return trade_count
