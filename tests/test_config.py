from pathlib import Path

import pytest

from app.config import ConfigError, load_settings


def test_defaults():
    settings = load_settings({})
    assert settings.photos_dir == Path("/photos")
    assert settings.data_dir == Path("/data")
    assert 0 < settings.min_score < settings.default_threshold < 1
    assert settings.max_upload_bytes > 0


def test_overrides():
    settings = load_settings({
        "PHOTOS_DIR": "/somewhere",
        "MATCH_THRESHOLD": "0.5",
        "MIN_FACE_SIZE": "40",
    })
    assert settings.photos_dir == Path("/somewhere")
    assert settings.default_threshold == 0.5
    assert settings.min_face_size == 40


def test_allowed_hosts_default_to_local_names():
    assert load_settings({}).allowed_hosts == ("localhost", "127.0.0.1")


def test_allowed_hosts_are_parsed():
    settings = load_settings({"ALLOWED_HOSTS": " myhost , 127.0.0.1,"})
    assert settings.allowed_hosts == ("myhost", "127.0.0.1")


@pytest.mark.parametrize("env", [
    {"ALLOWED_HOSTS": " , "},
    {"ALLOWED_HOSTS": "*"},
    {"MATCH_THRESHOLD": "abc"},
    {"MATCH_THRESHOLD": "1.5"},
    {"MIN_SCORE": "0.9", "MATCH_THRESHOLD": "0.4"},
    {"MIN_FACE_SIZE": "0"},
    {"MAX_UPLOAD_MB": "-1"},
    {"GALLERY_MAX_SIDE": "ten"},
])
def test_invalid_values_raise(env):
    with pytest.raises(ConfigError):
        load_settings(env)
