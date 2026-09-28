from pathlib import Path

import pytest

from app.config import HEALTHCHECK_HOST, ConfigError, load_settings


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


def test_local_mode_is_the_default():
    settings = load_settings({})
    assert settings.access_code is None
    assert settings.event_name == "Face Finder"
    assert settings.search_rate_limit > 0


def test_blank_access_code_means_local_mode():
    assert load_settings({"ACCESS_CODE": "  "}).access_code is None


def test_event_settings_are_read():
    settings = load_settings({
        "ACCESS_CODE": " sunny-wedding-42 ",
        "EVENT_NAME": "Anna & Luca",
        "SEARCH_RATE_LIMIT": "5",
    })
    assert settings.access_code == "sunny-wedding-42"
    assert settings.event_name == "Anna & Luca"
    assert settings.search_rate_limit == 5


def test_wildcard_subdomains_are_allowed_hosts():
    settings = load_settings({"ALLOWED_HOSTS": "*.trycloudflare.com"})
    assert "*.trycloudflare.com" in settings.allowed_hosts


def test_health_check_address_is_always_allowed():
    # Event mode replaces the host list with the public name. The container's
    # health check still calls the app on its own address.
    settings = load_settings({"ALLOWED_HOSTS": "myevent.duckdns.org,*.trycloudflare.com"})
    assert settings.allowed_hosts == (
        "myevent.duckdns.org", "*.trycloudflare.com", HEALTHCHECK_HOST,
    )


def test_health_check_address_is_not_repeated():
    settings = load_settings({"ALLOWED_HOSTS": "127.0.0.1,myhost"})
    assert settings.allowed_hosts == ("127.0.0.1", "myhost")


@pytest.mark.parametrize("env", [
    {"ACCESS_CODE": "short"},
    {"ACCESS_CODE": "x" * 201},
    {"EVENT_NAME": "x" * 81},
    {"SEARCH_RATE_LIMIT": "0"},
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
