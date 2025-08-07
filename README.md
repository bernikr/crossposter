# Crossposter

A simple Python script that reblogs/retoots posts on mastodon and bluesky.

## Why?

I'm not completely happy with the way [bridging](https://fed.brid.gy/) works between mastodon and bluesky. So I still have accounts on both platforms. I bridge both of them to the other and use this script to automatically reblog/retoot posts from myself on the other platform.

That way people just have to follow one of my accounts to see most of my activity on both platforms.


## Setup

`compose.yaml`

```yaml
services:
  availability-calendar:
    image: ghcr.io/bernikr/crossposter:1.0.0
    environment:
        BSKY_USERNAME: <username to login to bsky>
        BSKY_PASSWORD: <(app)password to login to bsky>
        BSKY_SOURCE_ACCOUNTS: <comma-separated list of accounts to search for posts on bsky>
        MASTODON_SERVER: <mastodon server to login to>
        MASTODON_ACCESS_TOKEN: <access token to login to mastodon>
        MASTODON_SOURCE_ACCOUNTS: <comma-separated list of accounts to search for posts on mastodon>
        SLEEP_TIME: <time to sleep between searches> (default: 60)
```
