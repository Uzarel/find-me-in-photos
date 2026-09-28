import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "app" / "static"
HIDDEN_RULE = re.compile(r"(?<![\w\]])\[hidden\]\s*\{[^}]*display:\s*none\s*!important")
VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'
REPO_URL = "https://github.com/Uzarel/find-me-in-photos"
LINK = re.compile(r"<a\s[^>]*>")


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


def test_page_says_when_the_selfie_shows_several_faces():
    # The search uses the largest face only. The guest has to be told, or a
    # group photo returns someone else's photos with no explanation.
    assert 'id="notice"' in read_static("index.html")
    script = read_static("app.js")
    assert "faces_in_selfie" in script
    assert "el.notice" in script


def test_page_credits_the_project():
    assert f'href="{REPO_URL}"' in read_static("index.html")


def test_links_open_apart_from_the_gallery():
    # A guest must not lose their results by following a link, and the site
    # they land on must get no handle on the gallery page.
    links = LINK.findall(read_static("index.html"))
    assert links
    for link in links:
        assert 'target="_blank"' in link
        assert 'rel="noopener noreferrer"' in link
