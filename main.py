import asyncio
import os

from atproto import AsyncClient

BSKY_USERNAME = os.getenv("BSKY_USERNAME", "")
BSKY_PASSWORD = os.getenv("BSKY_PASSWORD", "")
BSKY_SOURCE_ACCOUNTS = os.getenv("BSKY_SOURCE_ACCOUNTS", "").split(",")


async def main() -> None:
    bsky = AsyncClient()
    await bsky.login(BSKY_USERNAME, BSKY_PASSWORD)
    for source_account in BSKY_SOURCE_ACCOUNTS:
        feed = await bsky.get_author_feed(source_account)
        for post in feed.feed:
            if post.post.viewer and not post.post.viewer.repost:
                await bsky.repost(post.post.uri, post.post.cid)


if __name__ == "__main__":
    asyncio.run(main())
