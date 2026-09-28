import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
HIDDEN_RULE = re.compile(r"(?<![\w\]])\[hidden\]\s*\{[^}]*display:\s*none\s*!important")
VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def read_static(name):
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_hidden_elements_stay_hidden():
    # The page shows and hides parts with the "hidden" attribute. Any rule
    # that sets "display" beats the browser's own style for it, so without
    # this the empty video and selfie preview are drawn in the camera box.
    assert HIDDEN_RULE.search(read_static("style.css"))


def test_page_adapts_to_phone_screens():
    assert VIEWPORT in read_static("index.html")
    assert "@media (max-width:" in read_static("style.css")
