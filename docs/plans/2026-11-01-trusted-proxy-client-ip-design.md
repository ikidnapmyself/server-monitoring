---
title: "Trusted-proxy Client IP Design"
parent: Plans
---

# Trusted-proxy client IP

Comes before PR 12b of the console migration (`2026-10-29-sysadmin-console-auth-design.md`).

## Why

`RateLimitMiddleware._get_identity` keys an unauthenticated request on `REMOTE_ADDR`. Both
shipped deploys, systemd and Forge, run gunicorn on a unix socket behind nginx, where
gunicorn sets `REMOTE_ADDR` to an empty string. The Docker image binds TCP behind the
platform's proxy (Coolify, Dokku and the like), so there it's that proxy's address. Either way every client shares one bucket. With
`RATE_LIMIT_ENABLED=1`, 11 bad attempts a minute at `/console/login/` from anyone block
every operator's sign-in until the minute ends. Today `/admin/login/` has its own bucket.
12b removes it, so the console login becomes the only way in.

nginx already sends `X-Forwarded-For` (`deploy/nginx/nginx.conf`,
`deploy/forge/nginx-site.conf`). Nothing reads it, and reading it from any peer would let a
client pick a fresh bucket per request.

## What exists

- Only `config/middleware/rate_limit.py` reads `REMOTE_ADDR`. Nothing reads
  `X-Forwarded-For`.
- `config/checks.py` holds the config app's system checks.
- `config/_tests/test_rate_limit_middleware.py` asserts that `X-Forwarded-For` doesn't
  change the bucket.

## Design

- **Setting.** `TRUSTED_PROXIES`, a comma-separated env var of IPs and CIDR ranges. The
  entry `unix` means a peer on the unix socket, which gunicorn reports as an empty
  `REMOTE_ADDR`. Empty by default, which keeps today's behavior.
- **`config/client_ip.py`**, `client_ip(request) -> str`:
  - If the peer isn't trusted, return `REMOTE_ADDR`. A client's own header is ignored.
  - If it is, walk `X-Forwarded-For` from the right, skipping trusted addresses, and
    return the first one that isn't trusted. Reading from the right means a proxy chain
    such as Cloudflare in front of nginx works once both are listed.
  - A missing header, an empty one, a header of only trusted hops, or an entry that isn't
    an IP falls back to `REMOTE_ADDR`.
  - Entries that don't parse are skipped, so a bad setting never turns into a 500.
- **Use.** `_get_identity` calls `client_ip`. The audit trail can reuse it to record sign-in
  addresses.
- **Check.** A system check errors on any `TRUSTED_PROXIES` entry that isn't `unix`, an IP
  or a CIDR range, so `manage.py check` and `migrate` refuse it at deploy.

Rejected: a middleware that rewrites `REMOTE_ADDR` for the whole request. It changes what
every later middleware and log sees, for one consumer.

## Docs

- `.env.sample` and the Deployment.md env table gain `TRUSTED_PROXIES`.
- The systemd and Forge notes set `TRUSTED_PROXIES=unix`. The Docker README says to list the
  platform proxy's address or range.
- Security.md replaces both proxy caveats under Rate Limiting with how to set it.
  Deployment.md's console section drops its warning and points there.

## Tests

- Unset: `X-Forwarded-For` doesn't change the bucket (the existing test stays).
- An untrusted peer's header is ignored.
- Trusted `unix` and trusted IP peers: two clients get separate buckets.
- Several trusted hops resolve to the real client.
- Missing, empty, all-trusted and garbage headers fall back to `REMOTE_ADDR`.
- A bad entry is skipped at request time and reported by the check.
- 100% branch coverage on changed code.
