import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import FakeExtractor, encode_image, make_face, make_settings, write_image

SELFIE_LEVEL = 200
FACELESS_LEVEL = 99


@pytest.fixture
def app(tmp_path):
    settings = make_settings(tmp_path)
    write_image(settings.photos_dir / "a.png", 10)
    write_image(settings.photos_dir / "b.png", 20)
    write_image(settings.photos_dir / "c.png", 30)
    (tmp_path / "secret.txt").write_text("secret")
    extractor = FakeExtractor({
        10: (make_face([1, 0, 0, 0]),),
        20: (make_face([0.9, 0.1, 0, 0]),),
        30: (make_face([0, 0, 1, 0]),),
        SELFIE_LEVEL: (
            make_face([0, 1, 0, 0], box=(0, 0, 5, 5)),
            make_face([1, 0, 0, 0], box=(0, 0, 90, 90)),
        ),
    })
    return create_app(settings, extractor, autostart=False)


@pytest.fixture
def client(app):
    app.state.service.run()
    return TestClient(app)


def post_selfie(client, data, content_type="image/png"):
    return client.post("/api/search", files={"selfie": ("selfie.png", data, content_type)})


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_status_when_ready(client):
    body = client.get("/api/status").json()
    assert body["success"] is True
    assert body["data"]["state"] == "ready"
    assert body["data"]["photos"] == 3
    assert body["data"]["faces"] == 3
    assert body["data"]["default_threshold"] == 0.36


def test_status_while_indexing(app):
    body = TestClient(app).get("/api/status").json()
    assert body["data"]["state"] == "indexing"


def test_search_uses_largest_selfie_face(client):
    response = post_selfie(client, encode_image(SELFIE_LEVEL))
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["faces_in_selfie"] == 2
    matches = body["data"]["matches"]
    assert [m["filename"] for m in matches] == ["a.png", "b.png"]
    assert matches[0]["score"] == pytest.approx(1.0, abs=1e-4)
    assert matches[0]["url"] == "/photos/a.png"


def test_search_without_face_is_rejected(client):
    response = post_selfie(client, encode_image(FACELESS_LEVEL))
    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert "face" in body["error"].lower()


def test_search_with_invalid_image_is_rejected(client):
    response = post_selfie(client, b"definitely not an image")
    assert response.status_code == 400
    assert response.json()["success"] is False


def test_search_with_oversized_upload_is_rejected(client):
    response = post_selfie(client, b"\0" * (1024 * 1024 + 1))
    assert response.status_code == 413


def test_search_without_file_is_rejected(client):
    response = client.post("/api/search")
    assert response.status_code == 422
    assert response.json()["success"] is False


def test_search_before_index_is_ready(app):
    response = post_selfie(TestClient(app), encode_image(SELFIE_LEVEL))
    assert response.status_code == 503


def test_photo_is_served(client):
    response = client.get("/photos/a.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


@pytest.mark.parametrize("name", ["missing.png", "..%2Fsecret.txt", "%2E%2E%2Fsecret.txt"])
def test_unknown_photo_is_not_served(client, name):
    assert client.get(f"/photos/{name}").status_code == 404


def test_download_returns_zip(client):
    response = client.post("/api/download", json={"filenames": ["a.png", "b.png"]})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["a.png", "b.png"]


def test_download_works_when_archive_spills_to_disk(client, monkeypatch):
    monkeypatch.setattr("app.main.ZIP_SPOOL_BYTES", 1)
    response = client.post("/api/download", json={"filenames": ["a.png", "c.png"]})
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == ["a.png", "c.png"]


def test_download_reports_unreadable_photo(app, client):
    (app.state.settings.photos_dir / "a.png").unlink()
    response = client.post("/api/download", json={"filenames": ["a.png"]})
    assert response.status_code == 500
    assert response.json()["success"] is False


def test_unexpected_errors_keep_the_envelope(app, monkeypatch):
    app.state.service.run()

    def explode(image, max_side):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(app.state.extractor, "extract", explode)
    client = TestClient(app, raise_server_exceptions=False)
    response = post_selfie(client, encode_image(SELFIE_LEVEL))
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert "secret" not in body["error"]


def test_unknown_host_is_rejected(client):
    response = client.get("/api/status", headers={"host": "evil.example.com"})
    assert response.status_code == 400


def test_known_host_with_port_is_accepted(client):
    response = client.get("/api/status", headers={"host": "localhost:8000"})
    assert response.status_code == 200


def test_security_headers_are_set(client):
    headers = client.get("/").headers
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["cross-origin-resource-policy"] == "same-origin"


def test_oversized_body_is_rejected_before_parsing(client):
    response = post_selfie(client, b"\0" * (3 * 1024 * 1024))
    assert response.status_code == 413
    assert response.json()["success"] is False


def test_oversized_chunked_body_is_rejected(client):
    def chunks():
        for _ in range(3 * 1024):
            yield b"\0" * 1024

    response = client.post(
        "/api/search", content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert response.status_code == 413
    assert response.json()["success"] is False


def test_invalid_content_length_is_rejected(client):
    response = client.post("/api/download", content=b"{}",
                           headers={"content-length": "abc"})
    assert response.status_code == 400


def test_unreadable_photo_is_not_served(tmp_path):
    settings = make_settings(tmp_path)
    write_image(settings.photos_dir / "a.png", 10)
    (settings.photos_dir / "broken.jpg").write_bytes(b"not an image")
    app = create_app(settings, FakeExtractor({}), autostart=False)
    app.state.service.run()
    client = TestClient(app)
    assert client.get("/photos/a.png").status_code == 200
    assert client.get("/photos/broken.jpg").status_code == 404
    response = client.post("/api/download", json={"filenames": ["broken.jpg"]})
    assert response.status_code == 400


def test_photo_replaced_by_symlink_is_not_served(app, client, tmp_path):
    photo = app.state.settings.photos_dir / "a.png"
    photo.unlink()
    photo.symlink_to(tmp_path / "secret.txt")
    assert client.get("/photos/a.png").status_code == 404
    response = client.post("/api/download", json={"filenames": ["a.png"]})
    assert response.status_code == 400


def test_download_rejects_non_json_content_type(client):
    response = client.post("/api/download", content='{"filenames": ["a.png"]}',
                           headers={"content-type": "text/plain"})
    assert response.status_code == 422


def test_download_enforces_size_budget(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_DOWNLOAD_BYTES", 10)
    response = client.post("/api/download", json={"filenames": ["a.png"]})
    assert response.status_code == 413
    assert response.json()["success"] is False


def test_health_is_ok_when_ready(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_health_is_ok_while_indexing(app):
    assert TestClient(app).get("/api/health").status_code == 200


def test_health_fails_when_indexing_failed(tmp_path):
    settings = make_settings(tmp_path)
    failing = create_app(settings, FakeExtractor({}), autostart=False)
    failing.state.service.run()
    response = TestClient(failing).get("/api/health")
    assert response.status_code == 503
    assert response.json()["success"] is False


@pytest.mark.parametrize("payload", [
    {"filenames": []},
    {"filenames": ["../secret.txt"]},
    {"filenames": ["a.png", "unknown.png"]},
    {"filenames": "a.png"},
    {},
])
def test_download_rejects_invalid_requests(client, payload):
    response = client.post("/api/download", json=payload)
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
