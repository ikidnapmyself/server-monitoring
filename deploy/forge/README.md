# Managed VPS panel deployment (Laravel Forge and similar)

For panels that provision a server for you and manage nginx, SSL, environment variables,
and process daemons through a web UI — Laravel Forge, Ploi, RunCloud, ServerPilot.

The division of labour: **`bin/update.sh` owns the code**, **the panel owns the process
and the web server.** Do not script the parts the panel already manages.

## What the panel manages (web UI, not scripted)

| Concern | Where |
|---|---|
| Environment variables | Panel's environment/`.env` editor — `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_ENV=prod`, `DJANGO_DEBUG=0`, `DATABASE_PATH` |
| gunicorn process | Panel's **Daemons** feature (see command below) — the panel keeps it running and restarts it after each deploy |
| nginx site config | Panel's nginx editor — apply the 3 edits from `nginx-site.conf` inside the generated `server {}` block (replace the default `location /`, add `location /static/`, add `client_max_body_size`) |
| SSL / Let's Encrypt | Panel's SSL tab |

### gunicorn daemon command

Set this as a Daemon in the panel (Forge: Server → Daemons). It binds the unix socket
that `nginx-site.conf` proxies to:

```
/home/forge/.local/bin/uv run gunicorn config.wsgi:application \
    --bind unix:/home/forge/server/gunicorn.sock \
    --workers 2 --timeout 120
```

Directory: `/home/forge/server`. Adjust the path if your site directory differs.

`gunicorn` comes from the `prod` extra, so the deploy must run `uv sync --extra prod`
(the update script does — see below).

## What the deploy script does (code only)

The panel's **Deploy Script** field should be just the git pull plus `bin/update.sh`, which
handles dependency sync (with `--extra prod`), migrations, `collectstatic`, and env/alias sync. On this
bare-metal-prod layout it deliberately does **not** restart the process — the panel does
that. The `--rollback` flag reverts the code on failure.

```bash
cd /home/forge/server
git pull origin $FORGE_SITE_BRANCH
/home/forge/.local/bin/uv run ./bin/update.sh --rollback
```

That is the whole deploy. There is no `deploy.sh` in this directory on purpose —
`bin/update.sh` is the single source of truth for the deploy steps, and duplicating them
here would be a parallel mechanism that drifts.

## Static files

`bin/update.sh` runs `collectstatic` for you. Its pipeline is pull → sync_env →
sync_aliases → sync_deps → migrate → collectstatic → restart, and `_up_collectstatic`
(`bin/lib/update.sh`) skips only `dev` and `docker` modes. A Forge box detects as `prod`
or `systemd`, so assets land in `staticfiles/` on every deploy, ready for the `/static/`
block in `nginx-site.conf`. You do not need a separate collectstatic step.

**This depends on `DJANGO_ENV=prod` being set in the panel's environment.** With no
systemd unit installed and `DJANGO_ENV` unset, `detect_mode` (`bin/lib/health_check.sh`)
falls through to `dev`, and dev mode both skips `collectstatic` and syncs dependencies
with `--all-extras --dev` instead of `--extra prod`.

If these nodes only run the cron monitoring pipeline and do not serve a web UI, you can
skip nginx and the daemon entirely — neither is required for `manage.py run_pipeline`.
