# SPDX-FileCopyrightText: 2025 bernikr <hi@berni.kr>
#
# SPDX-License-Identifier: MIT

import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from atproto import Client  # type: ignore[import-untyped]
from atproto_client.exceptions import (  # type: ignore[import-untyped]
    BadRequestError,
    LoginRequiredError,
    RateLimitExceededError,
    UnauthorizedError,
)
from mastodon import Mastodon

if TYPE_CHECKING:
    from collections.abc import Callable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_SLEEP_TIME: Final = 60
DEFAULT_HEALTHCHECK_MAX_FAILURES: Final = 3
RATE_LIMIT_BASE_DELAY: Final = 60.0
RATE_LIMIT_MAX_DELAY: Final = 3600.0

# ATProto PDSs report rejected tokens either as HTTP 401/403 or as a 400 with one of these error codes.
SESSION_ERROR_CODES: Final = frozenset(
    {
        "AuthMissing",
        "AuthenticationRequired",
        "ExpiredToken",
        "InvalidToken",
        "TokenRefreshError",
    },
)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid value for %s: %r; using %d", name, raw, default)
        return default


def _env_optional_path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value) if value else None


def _env_accounts(name: str) -> tuple[str, ...]:
    return tuple(account.strip() for account in os.getenv(name, "").split(",") if account.strip())


def _atomic_write_text(path: Path, content: str) -> None:
    """Write content to path via a temporary file in the same directory."""
    temp_path = path.with_name(f".{path.name}.tmp")
    try:
        file_descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _retry_after(error: BaseException) -> float | None:
    value = getattr(error, "retry_after", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _is_session_rejected(error: BaseException) -> bool:
    if isinstance(error, (LoginRequiredError, UnauthorizedError)):
        return True
    if isinstance(error, BadRequestError):
        response = getattr(error, "response", None)
        content = getattr(response, "content", None)
        code = getattr(content, "error", None)
        return isinstance(code, str) and code in SESSION_ERROR_CODES
    return False


@dataclass(frozen=True)
class Config:
    bsky_username: str
    bsky_password: str
    bsky_source_accounts: tuple[str, ...]
    mastodon_server: str
    mastodon_access_token: str
    mastodon_source_accounts: tuple[str, ...]
    sleep_time: int
    session_file: Path | None
    healthcheck_file: Path | None
    healthcheck_max_failures: int
    version: str

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            bsky_username=os.getenv("BSKY_USERNAME", ""),
            bsky_password=os.getenv("BSKY_PASSWORD", ""),
            bsky_source_accounts=_env_accounts("BSKY_SOURCE_ACCOUNTS"),
            mastodon_server=os.getenv("MASTODON_SERVER", ""),
            mastodon_access_token=os.getenv("MASTODON_ACCESS_TOKEN", ""),
            mastodon_source_accounts=_env_accounts("MASTODON_SOURCE_ACCOUNTS"),
            sleep_time=_env_int("SLEEP_TIME", DEFAULT_SLEEP_TIME),
            session_file=_env_optional_path("SESSION_FILE"),
            healthcheck_file=_env_optional_path("HEALTHCHECK_FILE"),
            healthcheck_max_failures=_env_int("HEALTHCHECK_MAX_FAILURES", DEFAULT_HEALTHCHECK_MAX_FAILURES),
            version=os.getenv("VERSION", "dev"),
        )


class Backoff:
    """Exponential backoff with jitter for rate-limited Bluesky calls."""

    def __init__(
        self,
        *,
        base: float = RATE_LIMIT_BASE_DELAY,
        cap: float = RATE_LIMIT_MAX_DELAY,
        rng: random.Random | None = None,
    ) -> None:
        self._base = base
        self._cap = cap
        self._rng = rng if rng is not None else random.Random()  # ruff: ignore[suspicious-non-cryptographic-random-usage]
        self._attempt = 0
        self._next_attempt = 0.0

    def ready(self, now: float) -> bool:
        return now >= self._next_attempt

    def reset(self) -> None:
        self._attempt = 0
        self._next_attempt = 0.0

    def defer(self, now: float, retry_after: float | None = None) -> float:
        if retry_after is not None:
            delay = min(self._cap, retry_after)
        else:
            delay = min(self._cap, self._base * (2.0**self._attempt))
            self._attempt += 1
            delay = min(self._cap, delay * self._rng.uniform(1.0, 1.25))
        self._next_attempt = now + delay
        return delay


class BlueskyClient:
    """Bluesky client that keeps and persists a single ATProto session."""

    def __init__(self, config: Config, client: Client | None = None) -> None:
        self._config = config
        self._client: Client = client if client is not None else Client()
        self._has_session = False
        self._client.on_session_change(self._on_session_change)

    def run_once(self) -> None:
        try:
            self._ensure_session()
            self._repost()
        except Exception as error:
            if not self._has_session or not _is_session_rejected(error):
                raise
            logger.warning("Bluesky session was rejected; logging in again")
            self._has_session = False
            self._login()
            self._repost()

    def _ensure_session(self) -> None:
        if self._has_session:
            return
        if not self._restore_session():
            self._login()

    def _restore_session(self) -> bool:
        session_string = self._read_session()
        if session_string is None:
            return False
        try:
            self._client.login(session_string=session_string)
        except Exception as error:
            if isinstance(error, ValueError) or _is_session_rejected(error):
                logger.warning("Stored Bluesky session was rejected; logging in with credentials")
                return False
            raise
        logger.info("Restored Bluesky session from %s", self._config.session_file)
        self._has_session = True
        return True

    def _login(self) -> None:
        logger.info("Logging in to Bluesky...")
        profile = self._client.login(self._config.bsky_username, self._config.bsky_password)
        if profile is None:
            message = "Failed to log in to Bluesky. Check your credentials."
            raise RuntimeError(message)
        self._has_session = True
        self._write_session()

    def _repost(self) -> None:
        profile = self._client.me
        if profile is None:
            message = "Bluesky client has no logged-in profile."
            raise RuntimeError(message)
        own_did = profile.did
        for source_account in self._config.bsky_source_accounts:
            feed = self._client.get_author_feed(source_account)
            for post in feed.feed:
                if post.post.viewer and post.post.viewer.repost:
                    continue  # Already reposted
                if post.post.author.did == own_did:
                    continue  # Skip own posts
                self._client.repost(post.post.uri, post.post.cid)
                logger.info("Reposted bsky post %s", post.post.cid)

    def _read_session(self) -> str | None:
        path = self._config.session_file
        if path is None:
            return None
        try:
            session_string = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.info("No stored Bluesky session at %s", path)
            return None
        except OSError:
            logger.warning(
                "Could not read Bluesky session from %s; continuing without it",
                path,
                exc_info=True,
            )
            return None
        if not session_string:
            logger.warning("Bluesky session file %s is empty; ignoring it", path)
            return None
        return session_string

    def _write_session(self) -> None:
        path = self._config.session_file
        if path is None:
            return
        try:
            _atomic_write_text(path, self._client.export_session_string())
        except OSError:
            logger.warning(
                "Could not write Bluesky session to %s; keeping it in memory only",
                path,
                exc_info=True,
            )

    def _on_session_change(self, _event: object, _session: object) -> None:
        self._write_session()


class MastodonClient:
    """Mastodon client that keeps one API client for the life of the process."""

    def __init__(self, config: Config, client: Mastodon | None = None) -> None:
        self._config = config
        self._client = (
            client
            if client is not None
            else Mastodon(access_token=config.mastodon_access_token, api_base_url=config.mastodon_server)
        )

    def run_once(self) -> None:
        for source_account in self._config.mastodon_source_accounts:
            account = self._client.account_lookup(source_account)
            feed = self._client.account_statuses(account)
            for post in feed:
                if post.reblog or post.reblogged:
                    continue  # Skip reblogs and already reposted posts
                self._client.status_reblog(post.id)
                logger.info("Reposted mastodon post %s", post.id)


class BskyReposter(Protocol):
    def run_once(self) -> None: ...


class MastodonReposter(Protocol):
    def run_once(self) -> None: ...


class Crossposter:
    """Runs Bluesky and Mastodon mirroring independently in one cycle."""

    def __init__(
        self,
        config: Config,
        *,
        bsky: BskyReposter | None = None,
        mastodon: MastodonReposter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._bsky = bsky if bsky is not None else BlueskyClient(config)
        self._mastodon = mastodon if mastodon is not None else MastodonClient(config)
        self._backoff = Backoff()
        self._clock = clock

    def run_cycle(self) -> bool:
        healthy = True
        now = self._clock()

        if self._backoff.ready(now):
            try:
                self._bsky.run_once()
            except RateLimitExceededError as error:
                delay = self._backoff.defer(now, _retry_after(error))
                logger.warning("Bluesky rate limit exceeded; pausing Bluesky for %.0f seconds", delay)
                healthy = False
            except Exception:
                logger.exception("Bluesky cycle failed")
                healthy = False
            else:
                self._backoff.reset()
        else:
            logger.info("Bluesky is in rate-limit backoff; skipping this cycle")
            healthy = False

        try:
            self._mastodon.run_once()
        except Exception:
            logger.exception("Mastodon cycle failed")
            healthy = False

        if healthy:
            self._mark_healthy()
        return healthy

    def run_forever(self) -> None:
        while True:
            self.run_cycle()
            logger.info("Sleeping for %i seconds...", self._config.sleep_time)
            time.sleep(self._config.sleep_time)

    def _mark_healthy(self) -> None:
        path = self._config.healthcheck_file
        if path is None:
            return
        try:
            _atomic_write_text(path, str(time.time()))
        except OSError:
            logger.warning("Could not write healthcheck file %s", path, exc_info=True)


def healthcheck(*, now: float | None = None) -> int:
    """Check that a cycle completed recently.

    Returns:
        int: 0 while the last successful cycle is recent enough, 1 otherwise.
    """
    path_value = os.getenv("HEALTHCHECK_FILE")
    if not path_value:
        return 0

    max_failures = max(0, _env_int("HEALTHCHECK_MAX_FAILURES", DEFAULT_HEALTHCHECK_MAX_FAILURES))
    sleep_time = max(1, _env_int("SLEEP_TIME", DEFAULT_SLEEP_TIME))
    try:
        updated_at = Path(path_value).stat().st_mtime
    except OSError:
        return 1

    max_age = sleep_time * (max_failures + 1)
    current_time = time.time() if now is None else now
    return 0 if current_time - updated_at <= max_age else 1


def main() -> None:
    config = Config.from_env()
    logger.info("Starting up...")
    logger.info("Version %s", config.version)
    Crossposter(config).run_forever()


if __name__ == "__main__":
    main()
