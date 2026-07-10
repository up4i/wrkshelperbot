import pytest
from handlers.economy import (
    _daily_streak_multiplier,
    _slots_result,
    _bj_hand_value,
    _bj_is_blackjack,
    _generate_crash_point,
    _crash_multiplier,
    _rob_outcome,
    _roulette_result,
    _craps_come_out,
    _highlow_result,
)


# --- Daily ---

def test_streak_multiplier_days_1_to_6():
    for d in range(1, 7):
        assert _daily_streak_multiplier(d) == 1

def test_streak_multiplier_day_7():
    assert _daily_streak_multiplier(7) == 2

def test_streak_multiplier_day_14():
    assert _daily_streak_multiplier(14) == 3

def test_streak_multiplier_day_30_plus():
    assert _daily_streak_multiplier(30) == 4
    assert _daily_streak_multiplier(99) == 4


# --- Slots ---

def test_slots_three_sevens():
    outcome, multiplier = _slots_result(['7️⃣', '7️⃣', '7️⃣'])
    assert outcome == "jackpot"
    assert multiplier == 50

def test_slots_three_match_non_seven():
    outcome, multiplier = _slots_result(['🍒', '🍒', '🍒'])
    assert outcome == "three_match"
    assert multiplier == 10

def test_slots_two_match():
    outcome, multiplier = _slots_result(['🍒', '🍒', '🍋'])
    assert outcome == "two_match"
    assert multiplier == 2

def test_slots_no_match():
    outcome, multiplier = _slots_result(['🍒', '🍋', '🔔'])
    assert outcome == "no_match"
    assert multiplier == 0


# --- Blackjack ---

def test_bj_hand_value_simple():
    assert _bj_hand_value([('10', '♠'), ('7', '♥')]) == 17

def test_bj_hand_value_face_cards():
    assert _bj_hand_value([('K', '♠'), ('Q', '♥')]) == 20

def test_bj_hand_value_ace_high():
    assert _bj_hand_value([('A', '♠'), ('9', '♥')]) == 20

def test_bj_hand_value_ace_low_to_avoid_bust():
    assert _bj_hand_value([('A', '♠'), ('9', '♥'), ('5', '♣')]) == 15

def test_bj_hand_value_bust():
    assert _bj_hand_value([('K', '♠'), ('Q', '♥'), ('5', '♣')]) == 25

def test_bj_is_blackjack_true():
    assert _bj_is_blackjack([('A', '♠'), ('K', '♥')]) is True

def test_bj_is_blackjack_false_three_cards():
    assert _bj_is_blackjack([('A', '♠'), ('5', '♥'), ('5', '♣')]) is False

def test_bj_is_blackjack_false_21_not_natural():
    assert _bj_is_blackjack([('7', '♠'), ('7', '♥'), ('7', '♣')]) is False


# --- Crash ---

def test_generate_crash_point_range():
    for _ in range(200):
        point = _generate_crash_point()
        assert 1.5 <= point <= 2500.0

def test_crash_multiplier_starts_at_one():
    assert _crash_multiplier(0) == 1.0

def test_crash_multiplier_grows():
    assert _crash_multiplier(10) > _crash_multiplier(5)


# --- Rob ---

def test_rob_outcome_success_range():
    for _ in range(100):
        result = _rob_outcome(success=True, robber_balance=1000, victim_balance=1000)
        assert result["outcome"] == "success"
        stolen = result["amount"]
        assert 30 <= stolen <= 100  # 3-10% of 1000

def test_rob_outcome_fail_variants():
    outcomes = set()
    for _ in range(500):
        result = _rob_outcome(success=False, robber_balance=1000, victim_balance=1000)
        outcomes.add(result["outcome"])
    assert "fine" in outcomes
    assert "bail" in outcomes
    assert "getaway" in outcomes


# ── Roulette ──────────────────────────────────────────────────────────────────

def test_roulette_green_slot_wins_on_green():
    won, mult = _roulette_result(0, "green")
    assert won is True
    assert mult == 14

def test_roulette_green_slot_loses_on_red():
    won, mult = _roulette_result(1, "red")
    assert won is False
    assert mult == 0

def test_roulette_red_slot_wins_on_red():
    won, mult = _roulette_result(2, "red")
    assert won is True
    assert mult == 2

def test_roulette_red_slot_loses_on_black():
    won, mult = _roulette_result(10, "black")
    assert won is False
    assert mult == 0

def test_roulette_black_slot_wins_on_black():
    won, mult = _roulette_result(20, "black")
    assert won is True
    assert mult == 2

def test_roulette_black_boundary_slot_37():
    won, mult = _roulette_result(37, "black")
    assert won is True
    assert mult == 2

def test_roulette_red_boundary_slot_19():
    won, mult = _roulette_result(19, "red")
    assert won is True
    assert mult == 2


# ── Craps ─────────────────────────────────────────────────────────────────────

def test_craps_come_out_7_wins():
    assert _craps_come_out(7) == "win"

def test_craps_come_out_11_wins():
    assert _craps_come_out(11) == "win"

def test_craps_come_out_2_loses():
    assert _craps_come_out(2) == "lose"

def test_craps_come_out_3_loses():
    assert _craps_come_out(3) == "lose"

def test_craps_come_out_12_loses():
    assert _craps_come_out(12) == "lose"

def test_craps_come_out_4_sets_point():
    assert _craps_come_out(4) == "point"

def test_craps_come_out_10_sets_point():
    assert _craps_come_out(10) == "point"


# ── High-Low ──────────────────────────────────────────────────────────────────

def test_highlow_higher_correct():
    assert _highlow_result(5, 8, "higher") == "correct"

def test_highlow_higher_wrong():
    assert _highlow_result(8, 5, "higher") == "wrong"

def test_highlow_higher_equal_is_wrong():
    assert _highlow_result(7, 7, "higher") == "wrong"

def test_highlow_lower_correct():
    assert _highlow_result(9, 3, "lower") == "correct"

def test_highlow_lower_wrong():
    assert _highlow_result(3, 9, "lower") == "wrong"

def test_highlow_lower_equal_is_wrong():
    assert _highlow_result(5, 5, "lower") == "wrong"
