import logging
import os
import time

from atproto import Client  # type: ignore[import-untyped]
from mastodon import Mastodon

BSKY_USERNAME = os.getenv("BSKY_USERNAME", "")
BSKY_PASSWORD = os.getenv("BSKY_PASSWORD", "")
BSKY_SOURCE_ACCOUNTS = os.getenv("BSKY_SOURCE_ACCOUNTS", "").split(",")

MASTODON_SERVER = os.getenv("MASTODON_SERVER", "")
MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN", "")
MASTODON_SOURCE_ACCOUNTS = os.getenv("MASTODON_SOURCE_ACCOUNTS", "").split(",")

SLEEP_TIME = int(os.getenv("SLEEP_TIME", "60"))

VERSION = os.getenv("VERSION", "dev")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def bsky_repost() -> None:
    bsky = Client()
    bsky.login(BSKY_USERNAME, BSKY_PASSWORD)
    if bsky.me is None:
        logger.error("Failed to log in to Bluesky. Check your credentials.")
        return
    me = bsky.me.did
    for source_account in BSKY_SOURCE_ACCOUNTS:
        feed = bsky.get_author_feed(source_account)
        for post in feed.feed:
            if post.post.viewer and post.post.viewer.repost:
                continue  # Already reposted
            if post.post.author.did == me:
                continue  # Skip own posts
            bsky.repost(post.post.uri, post.post.cid)
            logger.info("Reposted bsky post %s", post.post.cid)


def mastodon_repost() -> None:
    mastodon = Mastodon(access_token=MASTODON_ACCESS_TOKEN, api_base_url=MASTODON_SERVER)
    for source_account in MASTODON_SOURCE_ACCOUNTS:
        account = mastodon.account_lookup(source_account)
        feed = mastodon.account_statuses(account)
        for post in feed:
            if post.reblog:
                continue  # Skip reblogs
            if post.reblogged:
                continue  # Already reposted
            mastodon.status_reblog(post.id)
            logger.info("Reposted mastodon post %s", post.id)


if __name__ == "__main__":
    logger.info("Starting up...")
    logger.info("Version %s", VERSION)
    while True:
        try:
            logger.info("Searching for new posts...")
            bsky_repost()
            mastodon_repost()
        except Exception:
            logger.exception("Something went wrong")
        logger.info("Sleeping for %i seconds...", SLEEP_TIME)
        time.sleep(SLEEP_TIME)
