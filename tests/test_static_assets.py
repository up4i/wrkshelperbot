import re
from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "miniapp" / "static"
INDEX_HTML = STATIC_DIR / "index.html"


def test_custom_badge_assets_referenced_by_ui_exist_and_are_not_empty():
    html = INDEX_HTML.read_text(encoding="utf-8")
    badge_paths = set(re.findall(r'src="(/badges/[^"$]+)"', html))

    assert badge_paths == {
        "/badges/admin.png",
        "/badges/ecoadmin.png",
        "/badges/owner.png",
        "/badges/plush_pepe_1.png",
    }
    for asset_path in badge_paths:
        asset = STATIC_DIR / asset_path.lstrip("/")
        assert asset.is_file()
        assert asset.stat().st_size > 1_000


def test_wrk_brand_asset_is_tracked_and_used_as_the_avatar_fallback():
    html = INDEX_HTML.read_text(encoding="utf-8")
    wrk_id = "5093898072811898950"
    fallback = STATIC_DIR / "wrk-logo.svg"

    assert fallback.is_file()
    assert fallback.stat().st_size > 500
    assert 'href="/wrk-logo.svg"' in html
    assert f'src="/emoji/{wrk_id}"' in html
    assert f"this.src='/emoji/{wrk_id}'" in html
    assert not re.search(r"placeholder\.com|placehold\.co|dummyimage", html, re.I)
