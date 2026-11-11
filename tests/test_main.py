# SPDX-FileCopyrightText: 2025 bernikr <hi@berni.kr>
#
# SPDX-License-Identifier: MIT

import random
from types import SimpleNamespace
from typing import TYPE_CHECKING

from atproto_client.exceptions import (  # type: ignore[import-untyped]
    RateLimitExceededError,
    UnauthorizedError,
)

from main import Backoff, BlueskyClient, Config, Crossposter, healthcheck

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeBsky:
    def __init__(
        self,
        *,
        session_string: str = "session-string",
        feed_error: Exception | None = None,
    ) -> None:
        self.create_session_calls = 0
        self.feed_calls = 0
        self.restored_session: str | None = None
        self.me = SimpleNamespace(did="did:me")
        self._session_string = session_string
        self._feed_error = feed_error
        self._callbacks: list[Callable[[object, object], None]] = []

    def on_session_change(self, callback: Callable[[object, object], None]) -> None:
        self._callbacks.append(callback)

    def login(
        self,
        login: str | None = None,
        password: str | None = None,
        session_string: str | None = None,
    ) -> object:
        del login, password
        if session_string is None:
            self.create_session_calls += 1
        else:
            self.restored_session = session_string
        for callback in self._callbacks:
            callback(object(), self)
        return self.me

    def export_session_string(self) -> str:
        return self._session_string

    def get_author_feed(self, _account: str) -> object:
        self.feed_calls += 1
        if self._feed_error is not None:
            raise self._feed_error
        return SimpleNamespace(feed=[])

    def repost(self, _uri: str, _cid: str) -> None:
        """Never called because the fake feed is empty."""


class RejectingFeedBsky(FakeBsky):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error
        self._rejected = False

    def get_author_feed(self, account: str) -> object:
        if not self._rejected:
            self._rejected = True
            raise self._error
        return super().get_author_feed(account)


class FakeMastodon:
    def __init__(self) -> None:
        self.run_calls = 0

    def run_once(self) -> None:
        self.run_calls += 1


def make_config(*, session_file: Path | None = None, healthcheck_file: Path | None = None) -> Config:
    return Config(
        bsky_username="test-user",
        bsky_password="test-password",
        bsky_source_accounts=("alice",),
        mastodon_server="https://mastodon.example.invalid",
        mastodon_access_token="test-token",
        mastodon_source_accounts=("bob",),
        sleep_time=60,
        session_file=session_file,
        healthcheck_file=healthcheck_file,
        healthcheck_max_failures=3,
        version="test",
    )


def make_poster(
    config: Config,
    fake_bsky: FakeBsky,
    mastodon: FakeMastodon,
    clock: FakeClock,
) -> Crossposter:
    return Crossposter(
        config,
        bsky=BlueskyClient(config, client=fake_bsky),
        mastodon=mastodon,
        clock=clock,
    )


def test_reuses_one_session_across_cycles() -> None:
    config = make_config()
    fake_bsky = FakeBsky()
    poster = make_poster(config, fake_bsky, FakeMastodon(), FakeClock())

    for _ in range(5):
        assert poster.run_cycle()

    assert fake_bsky.create_session_calls == 1
    assert fake_bsky.feed_calls == 5


def test_rate_limit_backs_off_without_logging_in_again() -> None:
    response = SimpleNamespace(headers={"retry-after": "120"})
    error = RateLimitExceededError(response)
    config = make_config()
    fake_bsky = FakeBsky(feed_error=error)
    mastodon = FakeMastodon()
    clock = FakeClock()
    poster = make_poster(config, fake_bsky, mastodon, clock)

    assert not poster.run_cycle()
    assert fake_bsky.create_session_calls == 1
    assert fake_bsky.feed_calls == 1

    clock.now = 60.0
    assert not poster.run_cycle()
    assert fake_bsky.create_session_calls == 1
    assert fake_bsky.feed_calls == 1
    assert mastodon.run_calls == 2

    clock.now = 120.0
    assert not poster.run_cycle()
    assert fake_bsky.create_session_calls == 1
    assert fake_bsky.feed_calls == 2


def test_rejected_session_logs_in_again() -> None:
    response = SimpleNamespace(status_code=401, headers={}, content=None)
    fake_bsky = RejectingFeedBsky(UnauthorizedError(response))
    config = make_config()

    assert make_poster(config, fake_bsky, FakeMastodon(), FakeClock()).run_cycle()
    assert fake_bsky.create_session_calls == 2


def test_backoff_doubles_and_caps_without_retry_after() -> None:
    backoff = Backoff(rng=random.Random(0))  # ruff: ignore[suspicious-non-cryptographic-random-usage]

    delays = [backoff.defer(float(index)) for index in range(10)]

    assert delays[0] >= 60
    assert delays[1] >= 120
    assert delays[2] >= 240
    assert all(delay <= 3600 for delay in delays)


def test_session_string_round_trips_through_session_file(tmp_path: Path) -> None:
    session_path = tmp_path / "session"
    config = make_config(session_file=session_path)

    first = FakeBsky(session_string="session-abc")
    assert make_poster(config, first, FakeMastodon(), FakeClock()).run_cycle()
    assert first.create_session_calls == 1
    assert session_path.read_text(encoding="utf-8") == "session-abc"

    second = FakeBsky(session_string="session-abc")
    assert make_poster(config, second, FakeMastodon(), FakeClock()).run_cycle()
    assert second.restored_session == "session-abc"
    assert second.create_session_calls == 0


def test_missing_session_directory_does_not_break_cycle(tmp_path: Path) -> None:
    config = make_config(session_file=tmp_path / "missing" / "session")
    fake_bsky = FakeBsky()

    assert make_poster(config, fake_bsky, FakeMastodon(), FakeClock()).run_cycle()
    assert fake_bsky.create_session_calls == 1


def test_bluesky_failure_still_runs_mastodon() -> None:
    config = make_config()
    fake_bsky = FakeBsky(feed_error=RuntimeError())
    mastodon = FakeMastodon()
    poster = make_poster(config, fake_bsky, mastodon, FakeClock())

    assert not poster.run_cycle()
    assert mastodon.run_calls == 1


def test_healthcheck_goes_unhealthy_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    health_path = tmp_path / "health"
    config = make_config(healthcheck_file=health_path)
    poster = make_poster(config, FakeBsky(), FakeMastodon(), FakeClock())

    assert poster.run_cycle()
    assert health_path.exists()

    monkeypatch.setenv("HEALTHCHECK_FILE", str(health_path))
    monkeypatch.setenv("HEALTHCHECK_MAX_FAILURES", "3")
    monkeypatch.setenv("SLEEP_TIME", "60")
    updated_at = health_path.stat().st_mtime

    assert healthcheck(now=updated_at + 60) == 0

    # A few missed cycles are tolerated; a longer outage is not.
    assert healthcheck(now=updated_at + 60 * 3) == 0
    assert healthcheck(now=updated_at + 60 * 5) == 1
