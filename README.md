<!--
SPDX-FileCopyrightText: 2025 bernikr <hi@berni.kr>

SPDX-License-Identifier: MIT
-->

# Crossposter

A simple Python script that reblogs/retoots posts on mastodon and bluesky.

## Why?

I'm not completely happy with the way [bridging](https://fed.brid.gy/) works between mastodon and bluesky. So I still have accounts on both platforms. I bridge both of them to the other and use this script to automatically reblog/retoot posts from myself on the other platform.

That way people just have to follow one of my accounts to see most of my activity on both platforms.


## Setup

`compose.yaml`

```yaml
services:
  crossposter:
    image: ghcr.io/bernikr/crossposter:1.2.0
    environment:
        BSKY_USERNAME: <username to login to bsky>
        BSKY_PASSWORD: <(app)password to login to bsky>
        BSKY_SOURCE_ACCOUNTS: <comma-separated list of accounts to search for posts on bsky>
        MASTODON_SERVER: <mastodon server to login to>
        MASTODON_ACCESS_TOKEN: <access token to login to mastodon>
        MASTODON_SOURCE_ACCOUNTS: <comma-separated list of accounts to search for posts on mastodon>
        SLEEP_TIME: <time to sleep between searches> (default: 60)
        SESSION_FILE: <optional path to persist the bsky session, e.g. /data/bsky-session>
        HEALTHCHECK_MAX_FAILURES: <number of consecutive failed cycles tolerated before unhealthy> (default: 3)
```

`SESSION_FILE` is optional. When it is unset the Bluesky session is kept in memory only, so a container
recreation requires a new login. When it is set, the session is written atomically (mode `0600`) and reused on
the next start, which avoids a `createSession` call. The path must be on a writable, persistent volume. If it
cannot be read or written (for example on a read-only distroless filesystem), crossposter logs a warning and
continues with an in-memory session.

`HEALTHCHECK_FILE` is set by the image to `/tmp/health` and normally does not need to be configured. It is only
refreshed after a cycle in which both directions completed their work. The healthcheck stays healthy through a
few transient failures and reports unhealthy once `HEALTHCHECK_MAX_FAILURES` consecutive cycles have been missed.
