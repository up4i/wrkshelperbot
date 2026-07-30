from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "miniapp" / "static" / "index.html"
)


def test_wallet_is_a_primary_destination_not_a_shop_subsection():
    html = INDEX_HTML.read_text()

    assert 'id="page-wallet"' in html
    assert 'data-page="wallet"' in html
    assert "setShopSection('wallet')" not in html
    assert 'data-shop-panel="wallet"' not in html
    assert "if (id === 'wallet') openWalletPage();" in html


def test_primary_navigation_stays_focused_and_wallet_has_sections():
    html = INDEX_HTML.read_text()
    nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]

    assert nav.count('class="nav-btn') == 5
    for label in ("Home", "Games", "Earn", "Shop", "Wallet"):
        assert f"</span>{label}" in nav
    assert "</span>Profile" not in nav

    for section in ("overview", "gifts", "numbers"):
        assert f'data-wallet="{section}"' in html
        assert f'data-wallet-panel="{section}"' in html


def test_underground_lives_under_earn_and_links_from_anon_wallet():
    html = INDEX_HTML.read_text()

    for section in ("work", "underground"):
        assert f'data-earn="{section}"' in html
        assert f'data-earn-panel="{section}"' in html
    assert 'onclick="goUnderground()"' in html
    assert "A +888 number only replaces the public identity" in html
    assert "/api/underground/status" in html


def test_underground_has_focused_board_heist_and_market_tabs():
    html = INDEX_HTML.read_text()

    for label in ("Shadow Board", "Crew Heists", "Black Market"):
        assert f">{label}</button>" in html
    for board_filter in ("Open contracts", "My activity", "Most wanted"):
        assert f">{board_filter}</button>" in html
    assert "/api/heists/status" in html
    assert "/api/black-market" in html
    assert "Everyone gets the same prices, role timers, task patterns" in html


def test_all_four_heist_minigames_are_rendered_in_the_miniapp():
    html = INDEX_HTML.read_text()

    for kind in ("casing", "chip_trace", "getaway", "crowd_control"):
        assert kind in html
    assert "Move the trace into each illuminated gate" in html
    assert "Switch lanes before each obstacle reaches the car" in html
    assert "Control guards. Hold position around civilians." in html
