# Add setup_aliases.sh to install.sh — Design

> **Status:** Approved 2026-02-23

**Goal:** Add shell alias setup as an optional final step in `bin/install.sh`, so new users discover aliases during installation.

**Context:** `install.sh` already has two optional post-install prompts (health check, cron setup) but doesn't mention `setup_aliases.sh`. Users only discover aliases if they read the README or `bin/README.md`.

## Approach

Delegate to existing `setup_aliases.sh` (same pattern as the cron setup on line 349). No logic duplication — the alias script already handles interactive prefix prompting, validation, file generation, and shell profile sourcing.

## Files to Update

| File | Change |
|------|--------|
| `bin/install.sh` | Add `[y/N]` prompt + `setup_aliases.sh` call after cron setup block |
| `bin/README.md` | Add "Optionally sets up shell aliases" to `install.sh` feature list |

## install.sh Change

Add after the cron setup block (after line 350, before `success "Setup complete!"`):

```bash
echo ""
read -p "Would you like to set up shell aliases (e.g., sm-check-health)? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    "$SCRIPT_DIR/setup_aliases.sh"
fi
```

## bin/README.md Change

Add bullet to `install.sh` "What it does" list:
- `Optionally sets up shell aliases`