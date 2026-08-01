from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketToken:
    symbol: str
    name: str
    initial_price_gram: str
    circulating_supply: str
    reserve_gram: str
    age_days: int
    legacy_holders: int
    lifetime_volume_gram: str


# The starting ratios preserve the market's familiar shape, but they are only
# one-time simulation seeds. Once a pool exists, all prices come from its
# persistent GRAM/token reserves.
MARKET_TOKENS = (
    MarketToken("UTYA", "Utya", "0.02827", "200000000", "85000", 418, 18_420, "28600000"),
    MarketToken("REDO", "Resistance Dog", "0.07165", "100000000", "120000", 704, 31_870, "49100000"),
    MarketToken("SCAT", "Scared Cat", "0.000978", "3000000000", "42000", 331, 12_940, "17300000"),
    MarketToken("YODA", "Yoda", "0.001062", "2000000000", "38000", 289, 9_730, "12800000"),
    MarketToken("CHERRY", "Cherry", "0.00001439", "8000000000", "26000", 246, 7_840, "9100000"),
    MarketToken("MTONGA", "MTonga", "0.002683", "500000000", "46000", 362, 11_260, "14600000"),
    MarketToken("GROYP", "Groyp", "0.04101", "100000000", "72000", 195, 6_910, "22200000"),
    MarketToken("GRAMMING", "Gramming", "0.0001619", "5000000000", "34000", 221, 8_630, "10400000"),
    MarketToken("GRM", "GRM", "0.0006741", "2000000000", "31000", 517, 15_380, "19800000"),
)

MARKET_TOKEN_BY_SYMBOL = {token.symbol: token for token in MARKET_TOKENS}
GAME_TOKEN_SYMBOLS = ("GRAM", *(token.symbol for token in MARKET_TOKENS))
WHALE_SUPPLY_DIVISOR = 100  # Badge requires strictly more than 1%.
