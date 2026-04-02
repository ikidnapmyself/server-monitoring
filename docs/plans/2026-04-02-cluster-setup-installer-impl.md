---
title: "2026-04-02 Cluster Setup Installer Implementation Plan"
parent: Plans
---

# Cluster Setup Installer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add cluster role prompts to the installer and cron setup, and expand deployment docs with verification, security, and troubleshooting guidance.

**Architecture:** Add a `dotenv_prompt_cluster` function to `bin/install.sh` that prompts for role (agent/hub/both) and writes cluster env vars. Add a `push_to_hub` cron option to `bin/setup_cron.sh` when `HUB_URL` is detected. Expand `docs/Deployment.md` cluster section.

**Tech Stack:** Bash, existing `bin/lib/` helpers (dotenv, logging, checks), bats tests.

---

### Task 1: Add cluster prompts to install.sh

**Files:**
- Modify: `bin/install.sh`

**Step 1: Add the `dotenv_prompt_cluster` function after `dotenv_prompt_setup` (after line 154)**

```bash
# ---------------------------------------------------------------------------
# Cluster role setup
# ---------------------------------------------------------------------------

dotenv_prompt_cluster() {
    local env_file="$PROJECT_DIR/.env"

    echo ""
    read -p "Configure this instance for multi-instance (cluster) mode? [y/N] " -n 1 -r
    echo ""
    if [[ ! "${REPLY:-}" =~ ^[Yy]$ ]]; then
        return 0
    fi

    echo ""
    echo "Select cluster role:"
    echo "  1) agent — run checkers locally, push results to a hub"
    echo "  2) hub   — accept alerts from remote agents"
    echo "  3) both  — agent + hub"
    echo ""
    read -p "Enter choice [1/2/3]: " -r CLUSTER_ROLE
    echo ""

    case "$CLUSTER_ROLE" in
        1|agent)  CLUSTER_ROLE="agent" ;;
        2|hub)    CLUSTER_ROLE="hub" ;;
        3|both)   CLUSTER_ROLE="both" ;;
        *)
            warn "Invalid choice '$CLUSTER_ROLE', skipping cluster setup."
            return 0
            ;;
    esac

    # Agent or both: prompt for HUB_URL and INSTANCE_ID
    if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
        local hub_url
        hub_url="$(prompt_non_empty "HUB_URL (e.g. https://monitoring-hub.example.com): ")"
        dotenv_set "$env_file" "HUB_URL" "$hub_url"

        local default_id
        default_id="$(hostname 2>/dev/null || echo "")"
        read -p "INSTANCE_ID (default: $default_id): " -r INSTANCE_ID_INPUT
        INSTANCE_ID_INPUT="${INSTANCE_ID_INPUT:-$default_id}"
        if [ -n "$INSTANCE_ID_INPUT" ]; then
            dotenv_set "$env_file" "INSTANCE_ID" "$INSTANCE_ID_INPUT"
        fi
    fi

    # Hub or both: enable CLUSTER_ENABLED
    if [ "$CLUSTER_ROLE" = "hub" ] || [ "$CLUSTER_ROLE" = "both" ]; then
        dotenv_set "$env_file" "CLUSTER_ENABLED" "1"
        success "CLUSTER_ENABLED=1 written to .env"
    fi

    # All roles: prompt for shared secret
    local secret
    secret="$(prompt_non_empty "WEBHOOK_SECRET_CLUSTER (shared secret between agents and hub): ")"
    dotenv_set "$env_file" "WEBHOOK_SECRET_CLUSTER" "$secret"

    success "Cluster configuration written to .env (role: $CLUSTER_ROLE)"

    # Agent or both: verify with dry-run
    if [ "$CLUSTER_ROLE" = "agent" ] || [ "$CLUSTER_ROLE" = "both" ]; then
        echo ""
        info "Running push_to_hub --dry-run to verify configuration..."
        if uv run python manage.py push_to_hub --dry-run 2>&1; then
            success "Dry run succeeded — agent is configured correctly"
        else
            warn "Dry run failed — check HUB_URL and try: uv run python manage.py push_to_hub --dry-run"
        fi
    fi
}
```

**Step 2: Call `dotenv_prompt_cluster` in the bare-metal path**

Insert after the aliases prompt (after line 310, before the systemd deployment section):

```bash
    # Cluster role setup
    dotenv_prompt_cluster
```

**Step 3: Call `dotenv_prompt_cluster` in the Docker path**

Insert before the `exec` handoff to `deploy-docker.sh` (after line 221, after `dotenv_prompt_setup`):

```bash
    dotenv_prompt_cluster
```

**Step 4: Verify syntax**

Run: `bash -n bin/install.sh`
Expected: no output (clean parse)

**Step 5: Commit**

```bash
git add bin/install.sh
git commit -m "feat: add cluster role prompts to installer"
```

---

### Task 2: Add push_to_hub cron option to setup_cron.sh

**Files:**
- Modify: `bin/setup_cron.sh`

**Step 1: Add push_to_hub cron section after the auto-update block (after line 112)**

```bash
# --- Cluster push option ---

# Check if HUB_URL is set in .env (agent mode)
source "$SCRIPT_DIR/lib/dotenv.sh"
_hub_url=""
if [ -f "$PROJECT_DIR/.env" ]; then
    _hub_url=$(grep -E "^HUB_URL=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)
fi

if [ -n "$_hub_url" ]; then
    echo ""
    read -p "HUB_URL detected — schedule automatic push to hub? [Y/n] " -n 1 -r
    echo ""

    if [[ -z "${REPLY:-}" || "${REPLY:-}" =~ ^[Yy]$ ]]; then
        PUSH_CMD="cd $PROJECT_DIR && $UV_PATH run python manage.py push_to_hub --json >> $PROJECT_DIR/push.log 2>&1"
        PUSH_ID="# server-maintanence cluster push"

        # Remove existing push job if present
        crontab -l 2>/dev/null | grep -v -F "$PUSH_ID" | crontab -

        # Add push job on same schedule
        (crontab -l 2>/dev/null || true; echo "$CRON_SCHEDULE $PUSH_CMD $PUSH_ID") | crontab -

        success "Cluster push cron job added"
        info "Push log: $PROJECT_DIR/push.log"
    fi
fi
```

**Step 2: Add push.log to the summary section**

In the summary block (around line 119), after the `info "Log file: $PROJECT_DIR/cron.log"` line, add:

```bash
if [ -n "${_hub_url:-}" ]; then
    info "Push log: $PROJECT_DIR/push.log"
fi
```

**Step 3: Verify syntax**

Run: `bash -n bin/setup_cron.sh`
Expected: no output

**Step 4: Commit**

```bash
git add bin/setup_cron.sh
git commit -m "feat: add push_to_hub cron option when HUB_URL is set"
```

---

### Task 3: Expand Deployment.md cluster section

**Files:**
- Modify: `docs/Deployment.md`

**Step 1: Replace the Multi-Instance (Cluster) section (lines 320-379) with expanded content**

Keep the existing Architecture, Agent setup, Hub setup, and Standalone subsections. Add three new subsections after Standalone:

```markdown
### Verification

After setting up an agent or hub, verify the configuration:

**Agent verification:**

```bash
# Dry-run: builds payload, shows what would be sent (no network call)
uv run python manage.py push_to_hub --dry-run

# Single push: sends one payload to the hub and reports the result
uv run python manage.py push_to_hub

# Push specific checkers only
uv run python manage.py push_to_hub --checkers cpu,memory --dry-run
```

**Hub verification:**

```bash
# Confirm the cluster driver is registered
uv run python manage.py shell -c "from apps.alerts.drivers import DRIVER_REGISTRY; print('cluster' in DRIVER_REGISTRY)"
# Expected output: True

# Check Django system checks pass
uv run python manage.py check
```

### Security

- **Always use HTTPS** for `HUB_URL` in production. Payloads contain server metrics and alert details.
- **`WEBHOOK_SECRET_CLUSTER`** must be identical on agents and hub. It is used to compute an HMAC-SHA256 signature sent via the `X-Cluster-Signature` header.
- Without a shared secret, payloads are accepted **unsigned** — acceptable for local development but not for production.
- The shared secret is never transmitted in the payload; only the signature is sent.

### Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `push_to_hub` exits with "HUB_URL not configured" | `HUB_URL` missing from `.env` | Add `HUB_URL=https://your-hub.example.com` to `.env` |
| `push_to_hub` exits with connection refused | Hub not running or wrong URL | Verify hub is accessible: `curl -s <HUB_URL>/alerts/webhook/cluster/` |
| `push_to_hub` returns 403 Forbidden | HMAC signature mismatch | Ensure `WEBHOOK_SECRET_CLUSTER` is identical on agent and hub |
| `push_to_hub` returns 404 Not Found | Cluster driver not registered | Set `CLUSTER_ENABLED=1` in hub `.env` and restart |
| Alerts arrive on hub but no notifications fire | Pipeline not configured | Run `uv run python manage.py setup_instance` on the hub |
| `push_to_hub --dry-run` shows 0 alerts | No checkers returned results | Run `uv run python manage.py check_health` to verify checkers work |
```

**Step 2: Update the Agent setup subsection to mention the installer**

Replace the first line of Agent setup:

```markdown
On each server you want to monitor:

1. Install the project normally (`./bin/install.sh` — select "agent" when prompted for cluster role)
```

And add after step 3 (the cron entry):

```markdown
> **Tip:** The installer and `bin/setup_cron.sh` can configure all of the above interactively. Manual `.env` editing is only needed if you skipped the prompts.
```

**Step 3: Update the Hub setup subsection similarly**

Replace the first line:

```markdown
On the central monitoring server:

1. Install the project (`./bin/install.sh` — select "hub" when prompted for cluster role)
```

**Step 4: Commit**

```bash
git add docs/Deployment.md
git commit -m "docs: expand cluster section with verification, security, troubleshooting"
```

---

### Task 4: Add bats tests

**Files:**
- Modify: `bin/tests/test_install.bats`
- Modify: `bin/tests/test_setup_cron.bats`

**Step 1: Verify syntax checks still pass (existing tests)**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/test_install.bats bin/tests/test_setup_cron.bats`
Expected: all pass

**Step 2: Commit**

No new tests needed beyond syntax — the cluster prompts are interactive (require stdin) and are gated behind `[y/N]` which defaults to no. The existing syntax checks validate the new code parses correctly.

```bash
git add bin/tests/
git commit -m "test: verify syntax after cluster setup additions"
```

(Only commit if tests were broken and fixed. If they already pass, skip this commit.)

---

### Task 5: Final verification

**Step 1: Run all bats tests**

Run: `bin/tests/test_helper/bats-core/bin/bats bin/tests/ && bin/tests/test_helper/bats-core/bin/bats bin/tests/lib/`
Expected: all pass

**Step 2: Verify install.sh --help-like behavior**

Run: `bash -n bin/install.sh && bash -n bin/setup_cron.sh`
Expected: no output

**Step 3: Spot-check Deployment.md renders**

Read through the new sections to confirm markdown is valid and tables render correctly.