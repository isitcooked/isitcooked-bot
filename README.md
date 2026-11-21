# IS IT COOKED? — Instagram Intelligence Radar

An Instagram-only, zero-server radar that publishes selective memecoin research automatically through GitHub Actions and the official Meta Graph API.

## What it publishes

- One daily carousel only when a token passes every market, momentum and security threshold.
- One educational story and one radar story per day.
- Existing visual templates, fonts and brand styling are preserved.
- Captions are deterministic and factual; no paid AI API is required.

## Default quality gate

- Networks: Solana and Base.
- Pair age: 5 minutes to 72 hours.
- Liquidity: at least $20,000.
- One-hour volume: at least $25,000.
- At least 35 transactions in one hour.
- More buys than sells.
- Positive one-hour momentum without an extreme late-stage spike.
- At least one official website or social link.
- RugCheck or GoPlus must respond.
- Critical security flags are rejected.
- Final transparent radar score: at least 70/100.

These values can be adjusted with GitHub Actions repository variables.

## Required GitHub Secrets

- `IG_USER_ID`
- `IG_ACCESS_TOKEN`

## Optional repository variables

- `BRAND` (default `Is It Cooked`)
- `DRY_RUN` (`false` for automatic live publishing)
- `IG_HOST` (default `https://graph.facebook.com`)
- `CHAINS` (default `solana,base`)
- `MIN_LIQUIDITY`, `MIN_VOLUME_1H`, `MIN_TXNS_1H`, `MIN_SCORE`, `MAX_AGE_HOURS`
- `REQUIRE_OFFICIAL_LINK` (default `true`)

## Storage design

JSON state is committed to the default branch. Generated images are never added to it. The workflow force-replaces a dedicated `media` branch containing only the files needed for the current Meta publication. This prevents continuous media accumulation in future repository history.

## Safe rollout

Run `diagnose` first. Use `DRY_RUN=true` for a preview, then set it to `false` after confirming the account connection. Failed publications remain pending and are not marked as posted.

Momentum signals are research prompts, not endorsements or financial advice.
