from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "miniapp" / "static" / "index.html"
)


def test_wallet_is_a_primary_destination_not_a_shop_subsection():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="page-wallet"' in html
    assert 'data-page="wallet"' in html
    assert "setShopSection('wallet')" not in html
    assert 'data-shop-panel="wallet"' not in html
    assert "if (id === 'wallet') openWalletPage();" in html


def test_primary_navigation_stays_focused_and_wallet_has_sections():
    html = INDEX_HTML.read_text(encoding="utf-8")
    nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]

    assert nav.count('class="nav-btn') == 5
    for label in ("Home", "Games", "Earn", "Shop", "Wallet"):
        assert f"</span>{label}" in nav
    assert "</span>Profile" not in nav

    for section in ("overview", "assets", "gifts", "numbers"):
        assert f'data-wallet="{section}"' in html
        assert f'data-wallet-panel="{section}"' in html


def test_coin_wallet_is_explicitly_a_fictional_game_wallet():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "@tonconnect/ui" not in html
    assert "tonConnectUI" not in html
    assert "STONK Wallet" in html
    assert "simulated coins" in html
    assert "/api/game-wallet" in html
    assert "fictional" in html.lower()


def test_memecoin_shop_uses_simulated_pools_and_reserve_aware_quotes():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'data-shop="coins"' in html
    assert 'data-shop-panel="coins"' in html
    assert "/api/token-market" in html
    assert "/api/game-wallet/swap" in html
    assert "/api/game-wallet/swap-quote" in html
    assert "/api/game-wallet/topup" in html
    assert "/api/game-wallet/cashout" in html
    assert "STONK.fi" in html
    assert "constant-product liquidity pools" in html
    assert "price impact" in html.lower()


def test_underground_lives_under_earn_and_links_from_anon_wallet():
    html = INDEX_HTML.read_text(encoding="utf-8")

    for section in ("work", "underground"):
        assert f'data-earn="{section}"' in html
        assert f'data-earn-panel="{section}"' in html
    assert 'onclick="goUnderground()"' in html
    assert "A +888 number only replaces the public identity" in html
    assert "/api/underground/status" in html


def test_earn_separates_work_crime_and_underground():
    html = INDEX_HTML.read_text(encoding="utf-8")

    for section in ("work", "crime", "underground"):
        assert f'data-earn="{section}"' in html
        assert f'data-earn-panel="{section}"' in html
    work_panel = html.split('data-earn-panel="work"', 1)[1].split("</section>", 1)[0]
    crime_panel = html.split('data-earn-panel="crime"', 1)[1].split("</section>", 1)[0]
    assert "openHack()" not in work_panel
    assert "openRob()" not in work_panel
    assert "openHack()" in crime_panel
    assert "openRob()" in crime_panel


def test_home_and_leaderboard_hide_secondary_detail_by_default():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert '<details class="home-community-details">' in html
    assert 'id="lbMoreSelect"' in html
    assert "if (!tab) return;" in html
    assert "Your Telegram user ID" not in html


def test_profiles_can_be_shared_and_bot_can_be_added_to_groups():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "function shareProfile(userId)" in html
    assert "function openBotGroupPicker()" in html
    assert "startgroup=true" in html
    assert "↗ Share flex" in html


def test_underground_has_focused_board_heist_and_market_tabs():
    html = INDEX_HTML.read_text(encoding="utf-8")

    for label in ("Shadow Board", "Crew Heists", "Black Market"):
        assert f">{label}</button>" in html
    for board_filter in ("Open contracts", "My activity", "Most wanted"):
        assert f">{board_filter}</button>" in html
    assert "/api/heists/status" in html
    assert "/api/black-market" in html
    assert "Everyone gets the same prices, role timers, task patterns" in html


def test_all_four_heist_minigames_are_rendered_in_the_miniapp():
    html = INDEX_HTML.read_text(encoding="utf-8")

    for kind in ("casing", "chip_trace", "getaway", "crowd_control"):
        assert kind in html
    assert "Move the trace into each illuminated gate" in html
    assert "Switch lanes before each obstacle reaches the car" in html
    assert "Control guards. Hold position around civilians." in html


def test_war_stays_a_blackjack_side_bet_with_a_staged_opening_deal():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "Casino War" not in html
    assert "openWar" not in html
    assert 'id="bjWarWell"' in html
    assert "war_bet: bjWarBet" in html
    assert "async function animateBjOpening" in html
    assert "bjOpeningDealer" in html
    assert "bjOpeningPlayer1" in html
    assert "bjOpeningPlayer2" in html
    assert "bjOpeningWar" in html
    assert "bjOpeningPair" in html


def test_slots_autoplay_has_a_bounded_spin_count_and_safe_stop_controls():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="slotAutoCount"' in html
    assert 'min="1" max="100"' in html
    assert "function toggleSlotsAutoplay()" in html
    assert "async function runSlotsAutoplay()" in html
    assert "state.balance < slotsAutoBet" in html
    assert "id === 'slotsModal'" in html


def test_theme_switch_is_persistent_and_updates_telegram_chrome():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="themeToggle"' in html
    assert "localStorage.setItem('wrkTheme', selected)" in html
    assert 'html[data-theme="light"]' in html
    assert "function syncTelegramTheme" in html
    assert "tg.setHeaderColor?.(pageColor)" in html
    assert "tg.setBottomBarColor?.(bottomColor)" in html


def test_roulette_results_and_craps_dice_have_clear_visual_feedback():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "roulette-winning-number" in html
    assert "roulette-win-chip" in html
    assert "Net result across" in html
    assert "[...rouletteBetTypes].map(type => rouletteBetLabel(type))" in html
    assert 'id="crapsShadow1"' in html
    assert "requestAnimationFrame(frame)" in html
    assert "const bounce = Math.abs(Math.sin" in html


def test_profile_gifts_keep_three_columns_and_use_the_requested_black():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "grid-template-columns: repeat(3, minmax(0, 104px))" in html
    assert "grid-template-columns: repeat(3, minmax(0,1fr))" in html
    assert "width: 104px;" in html
    assert "height: 134px;" in html
    assert "black: '#0e0f0f'" in html


def test_profiles_render_dynamic_token_whale_badges():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert "badge.startsWith('whale:')" in html
    assert 'class="profile-badge-slot filled whale"' in html
    assert '<small>Whale</small>' in html
    assert "Earned automatically" in html


def test_gift_artwork_stays_consistent_in_wallet_profile_and_reorder_mode():
    html = INDEX_HTML.read_text(encoding="utf-8")
    card_builder = html.split("function _buildGiftCardHtml", 1)[1].split(
        "const _BADGE_EMOJI",
        1,
    )[0]

    assert "function giftArtworkFallback" in html
    assert "function _giftArtworkHtml" in html
    assert "_giftArtworkHtml(g)" in card_builder
    assert "if (compact)" not in card_builder
    assert 'style="--gift-bg:${bgColor}"' in html
    assert "background: var(--gift-bg);" in html
    assert "function pinWalletGift(giftId)" in html
    assert "The static artwork remains visible" in html


def test_wallet_loads_every_gift_and_sales_do_not_refresh_the_page():
    html = INDEX_HTML.read_text(encoding="utf-8")
    wallet_renderer = html.split("function renderWalletGifts()", 1)[1].split(
        "async function pinWalletGift",
        1,
    )[0]
    sell_flow = html.split("function confirmRiftSell(giftId)", 1)[1].split(
        "function openMkrtListModal",
        1,
    )[0]

    assert "gifts.map(_walletGiftCardHtml)" in wallet_renderer
    assert "_walletGiftVisible" not in html
    assert "loadMoreWalletGifts" not in html
    assert 'data-wallet-gift-id="${g.id}"' in html
    assert "_removeSoldWalletGift(giftId)" in sell_flow
    assert "loadShopWallet()" not in sell_flow
    assert "walletScroller.scrollTop = scrollTop" in html
    assert "data.buyback_price" in sell_flow
    assert "data.buyback_price)} WRK$" in sell_flow


def test_underground_explanatory_copy_has_a_readable_floor():
    html = INDEX_HTML.read_text(encoding="utf-8")

    readable_block = html.split(
        "/* Underground copy must remain readable on compact Telegram screens. */",
        1,
    )[1].split("/* ── Market ── */", 1)[0]
    assert ".ug-cover-copy" in readable_block
    assert ".heist-desc" in readable_block
    assert "font-size: 11px" in readable_block
    assert "line-height: 1.55" in readable_block
