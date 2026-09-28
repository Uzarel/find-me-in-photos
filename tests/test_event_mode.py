"""Event mode: the app is shared on a network behind an access code."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.access import SESSION_COOKIE, issue_token
from app.config import load_settings
from app.main import DOWNLOADS_PER_WINDOW, create_app
from app.session import MAX_FAILED_LOGINS
from tests.helpers import FakeExtractor, encode_image, make_face, make_settings, write_image

CODE = "sunny-wedding-42"
SELFIE_LEVEL = 200
# Must match FORWARDED_ALLOW_IPS in docker-compose.event.yml.
TRUSTED_PROXIES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
PROXY_ADDRESS = "172.23.0.5"
# The host list event mode sets, and the address the Dockerfile health check calls.
PUBLIC_HOSTS = "myevent.duckdns.org,*.trycloudflare.com"
HEALTHCHECK_URL = "http://127.0.0.1:8000"


def build_app(root, **overrides):
    settings = make_settings(root, **overrides)
    write_image(settings.photos_dir / "a.png", 10)
    extractor = FakeExtractor({
        10: (make_face([1, 0, 0, 0]),),
        SELFIE_LEVEL: (make_face([1, 0, 0, 0]),),
    })
    app = create_app(settings, extractor, autostart=False)
    app.state.service.run()
    return app


@pytest.fixture
def app(tmp_path):
    return build_app(tmp_path, access_code=CODE, event_name="Anna & Luca")


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def guest(client):
    assert client.post("/api/login", json={"code": CODE}).status_code == 200
    return client


def post_selfie(client):
    files = {"selfie": ("selfie.png", encode_image(SELFIE_LEVEL), "image/png")}
    return client.post("/api/search", files=files)


def test_page_and_health_are_open(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_health_check_works_behind_a_public_name(tmp_path):
    hosts = load_settings({"ALLOWED_HOSTS": PUBLIC_HOSTS}).allowed_hosts
    app = build_app(tmp_path, access_code=CODE, allowed_hosts=hosts)
    container = TestClient(app, base_url=HEALTHCHECK_URL)
    assert container.get("/api/health").status_code == 200
    stranger = TestClient(app, base_url="http://elsewhere.example")
    assert stranger.get("/api/health").status_code == 400


def test_session_reports_event_details(client):
    data = client.get("/api/session").json()["data"]
    assert data == {
        "auth_required": True,
        "authenticated": False,
        "event_name": "Anna & Luca",
    }


def test_local_mode_needs_no_login(tmp_path):
    client = TestClient(build_app(tmp_path))
    data = client.get("/api/session").json()["data"]
    assert data["auth_required"] is False
    assert data["authenticated"] is True
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/login", json={"code": CODE}).status_code == 400


@pytest.mark.parametrize("method, path", [
    ("GET", "/api/status"),
    ("GET", "/photos/a.png"),
    ("POST", "/api/search"),
    ("POST", "/api/download"),
    ("GET", "/api/anything-added-later"),
])
def test_everything_else_needs_a_session(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["success"] is False


def test_wrong_code_is_rejected(client):
    response = client.post("/api/login", json={"code": "not-the-code"})
    assert response.status_code == 401
    assert response.json()["success"] is False
    assert SESSION_COOKIE not in response.cookies
    assert client.get("/api/status").status_code == 401


@pytest.mark.parametrize("payload", [{}, {"code": ""}, {"code": "x" * 201}, {"code": 5}])
def test_malformed_login_is_rejected(client, payload):
    assert client.post("/api/login", json=payload).status_code == 422


def test_login_opens_the_gallery(guest):
    assert guest.get("/api/session").json()["data"]["authenticated"] is True
    assert guest.get("/api/status").status_code == 200
    assert guest.get("/photos/a.png").status_code == 200
    response = post_selfie(guest)
    assert response.status_code == 200
    assert [m["filename"] for m in response.json()["data"]["matches"]] == ["a.png"]


def test_session_cookie_is_hardened(client):
    response = client.post("/api/login", json={"code": CODE})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "secure" not in cookie  # plain HTTP in this test


def test_session_cookie_is_secure_over_https(app):
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/login", json={"code": CODE})
    assert "secure" in response.headers["set-cookie"].lower()
    assert client.get("/api/status").status_code == 200


def test_forged_and_foreign_cookies_are_rejected(client):
    for token in ("forged", issue_token("another-code", 4102444800)):
        client.cookies.set(SESSION_COOKIE, token)
        assert client.get("/api/status").status_code == 401


def test_logout_ends_the_session(guest):
    assert guest.post("/api/logout").status_code == 200
    assert guest.get("/api/status").status_code == 401


def test_repeated_failures_lock_the_login(client):
    for _ in range(MAX_FAILED_LOGINS):
        assert client.post("/api/login", json={"code": "guess"}).status_code == 401
    assert client.post("/api/login", json={"code": "guess"}).status_code == 429
    assert client.post("/api/login", json={"code": CODE}).status_code == 429


def test_successful_logins_are_not_limited(client):
    for _ in range(MAX_FAILED_LOGINS + 5):
        assert client.post("/api/login", json={"code": CODE}).status_code == 200


def test_searches_are_rate_limited(tmp_path):
    client = TestClient(build_app(tmp_path, search_rate_limit=2))
    assert post_selfie(client).status_code == 200
    assert post_selfie(client).status_code == 200
    response = post_selfie(client)
    assert response.status_code == 429
    assert response.json()["success"] is False
    assert client.get("/api/status").status_code == 200


def served_to(app, peer_address):
    """The app as uvicorn serves it in event mode, with a connection from peer_address."""
    trusting = ProxyHeadersMiddleware(app, trusted_hosts=TRUSTED_PROXIES)

    async def with_peer(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (peer_address, 50000)}
        await trusting(scope, receive, send)

    return TestClient(with_peer)


def behind_proxy(app):
    return served_to(app, PROXY_ADDRESS)


def test_guests_behind_the_proxy_are_limited_separately(tmp_path):
    client = behind_proxy(build_app(tmp_path, search_rate_limit=1))
    for guest_address in ("203.0.113.1", "203.0.113.2"):
        client.headers["x-forwarded-for"] = guest_address
        assert post_selfie(client).status_code == 200
        assert post_selfie(client).status_code == 429


def test_spoofed_forwarded_address_does_not_dodge_the_limits(tmp_path):
    client = behind_proxy(build_app(tmp_path, access_code=CODE))
    for attempt in range(MAX_FAILED_LOGINS):
        # The guest invents a new address each time; the tunnel appends the real one.
        client.headers["x-forwarded-for"] = f"10.1.2.{attempt}, 203.0.113.9"
        assert client.post("/api/login", json={"code": "guess"}).status_code == 401
    client.headers["x-forwarded-for"] = "10.9.9.9, 203.0.113.9"
    assert client.post("/api/login", json={"code": "guess"}).status_code == 429


def test_forwarded_headers_from_strangers_are_ignored(tmp_path):
    client = served_to(build_app(tmp_path, search_rate_limit=1), "203.0.113.50")
    client.headers["x-forwarded-for"] = "198.51.100.1"
    assert post_selfie(client).status_code == 200
    client.headers["x-forwarded-for"] = "198.51.100.2"
    assert post_selfie(client).status_code == 429


def test_clients_without_an_address_share_one_limit(tmp_path, caplog):
    app = build_app(tmp_path, search_rate_limit=1)
    statuses = []

    async def call():
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        scope = {"type": "http", "method": "POST", "path": "/api/download",
                 "headers": [(b"host", b"localhost")], "client": None,
                 "query_string": b"", "scheme": "http", "server": ("localhost", 80),
                 "root_path": "", "http_version": "1.1", "raw_path": b"/api/download"}
        await app(scope, receive, send)

    with caplog.at_level("WARNING"):
        for _ in range(DOWNLOADS_PER_WINDOW + 1):
            asyncio.run(call())
    assert statuses[-1] == 429
    assert 429 not in statuses[:-1]
    assert "address" in caplog.text.lower()


def test_logout_cookie_matches_the_login_cookie(app):
    client = TestClient(app, base_url="https://testserver")
    client.post("/api/login", json={"code": CODE})
    cookie = client.post("/api/logout").headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/api/status").status_code == 401


def test_unauthenticated_requests_do_not_use_the_search_quota(tmp_path):
    app = build_app(tmp_path, access_code=CODE, search_rate_limit=1)
    client = TestClient(app)
    assert post_selfie(client).status_code == 401
    assert post_selfie(client).status_code == 401
    client.post("/api/login", json={"code": CODE})
    assert post_selfie(client).status_code == 200
