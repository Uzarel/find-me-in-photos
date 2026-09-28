import pytest

from app.access import (
    SESSION_SECONDS,
    RateLimiter,
    codes_match,
    is_valid_token,
    issue_token,
    session_key,
)

CODE = "sunny-wedding-42"
NOW = 1_000_000.0


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


def test_codes_match_only_when_equal():
    assert codes_match(CODE, CODE)
    assert not codes_match("wrong", CODE)
    assert not codes_match("", CODE)
    assert not codes_match(CODE + " ", CODE)


def test_codes_match_handles_non_ascii():
    assert codes_match("caffè-2026", "caffè-2026")
    assert not codes_match("caffe-2026", "caffè-2026")


def test_issued_token_is_valid():
    assert is_valid_token(issue_token(CODE, NOW), CODE, NOW)


def test_token_expires():
    token = issue_token(CODE, NOW)
    assert is_valid_token(token, CODE, NOW + SESSION_SECONDS - 1)
    assert not is_valid_token(token, CODE, NOW + SESSION_SECONDS + 1)


def test_token_is_bound_to_the_access_code():
    assert not is_valid_token(issue_token(CODE, NOW), "another-code", NOW)


def test_tampered_expiry_is_rejected():
    expiry, signature = issue_token(CODE, NOW).split(".")
    forged = f"{int(expiry) + 999999}.{signature}"
    assert not is_valid_token(forged, CODE, NOW)


@pytest.mark.parametrize("token", [
    "", "abc", "123", ".", "123.", ".abc", "12.34.56", "notanumber.abcdef", "9" * 400,
])
def test_malformed_tokens_are_rejected(token):
    assert not is_valid_token(token, CODE, NOW)


def test_rate_limiter_allows_up_to_the_limit():
    limiter = RateLimiter(limit=3, window_seconds=60, clock=FakeClock())
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_tracks_keys_separately():
    limiter = RateLimiter(limit=1, window_seconds=60, clock=FakeClock())
    assert limiter.allow("a")
    assert limiter.allow("b")
    assert not limiter.allow("a")


def test_rate_limiter_recovers_after_the_window():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
    assert limiter.allow("a")
    clock.now = 59
    assert not limiter.allow("a")
    clock.now = 61
    assert limiter.allow("a")


def test_rate_limiter_can_be_checked_without_recording():
    limiter = RateLimiter(limit=1, window_seconds=60, clock=FakeClock())
    assert not limiter.is_exhausted("a")
    assert not limiter.is_exhausted("a")
    assert limiter.allow("a")
    assert limiter.is_exhausted("a")
    assert not limiter.is_exhausted("b")


def test_rate_limiter_forgets_idle_keys():
    clock = FakeClock()
    limiter = RateLimiter(limit=5, window_seconds=60, clock=clock)
    for key in ("a", "b", "c"):
        limiter.allow(key)
    clock.now = 120
    limiter.allow("d")
    assert limiter.tracked_keys() == 1


def test_rate_limiter_caps_the_addresses_it_tracks():
    clock = FakeClock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock, max_keys=3)
    for second, key in enumerate(("a", "b", "c", "d")):
        clock.now = second
        assert limiter.allow(key)
    assert limiter.tracked_keys() == 3
    assert not limiter.is_exhausted("a")  # the oldest was dropped to make room
    assert limiter.is_exhausted("d")


def test_session_key_is_slow_to_guess_but_computed_once():
    first = session_key(CODE)
    assert session_key(CODE) is first
    assert session_key("another-code") != first
    assert len(first) == 32


@pytest.mark.parametrize("limit, window", [(0, 60), (-1, 60), (5, 0)])
def test_rate_limiter_rejects_invalid_settings(limit, window):
    with pytest.raises(ValueError):
        RateLimiter(limit=limit, window_seconds=window)
