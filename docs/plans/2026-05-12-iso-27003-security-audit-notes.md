---
title: "2026-05-12 ISO 27003 Security Audit Notes"
parent: Plans
---

# ISO 27003 Security Audit Notes

**Started:** 2026-05-12
**Completed:** 2026-05-13 (per-module audit pass)
**Status:** Per-module audit complete (1 MEDIUM finding in `apps/intelligence/`); doc-update + agents.md follow-ups pending — see "Pending doc-update parking lot".
**Owner:** Security audit pass (Claude-assisted, human-reviewed)

## Purpose

Working notes for an end-to-end security audit of the server-maintenance codebase, framed against ISO/IEC 27001:2022 Annex A controls and ISO/IEC 27003 implementation guidance. Each module is reviewed independently; observations accumulate here. When the audit is complete, this file is the input for:

1. Edits to `docs/Security.md` (operator-facing security posture).
2. Per-app `agents.md` security sections (developer-facing rules).
3. A short "Statement of Applicability" appendix that maps the codebase's mitigations to Annex A controls.

The audit is **conservative**: only vulnerabilities at confidence >= 0.8 are flagged in the per-module audit report. Defense-in-depth gaps, design notes, and ISMS-relevant context are recorded here rather than as findings.

## Scope

Modules to audit (pipeline order, then supporting layers):

- `bin/` — installer & operator tooling ✓
- `apps/alerts/` — webhook ingestion (external trust boundary) ✓
- `apps/checkers/` — health check stage ✓
- `apps/intelligence/` — AI provider integrations ✓ (1 MEDIUM finding)
- `apps/notify/` — outbound notification delivery ✓
- `apps/orchestration/` — pipeline state machine ✓
- `config/` — middleware, settings, central security utilities ✓

## Audit framework

Per module we record:

1. **Purpose & data classification** — what the module does and what data flows through it.
2. **Trust boundary** — sources of input and which are external vs admin/env-trusted.
3. **Threat model** — what an attacker against this module could plausibly attempt.
4. **Controls in place** — concrete code references for mitigations already implemented.
5. **Sinks reviewed** — table of dangerous-operation locations and verdict.
6. **Findings (>= 0.8 confidence)** — concrete vulnerabilities.
7. **Sub-threshold observations** — defense-in-depth gaps, design caveats.
8. **Doc-update candidates** — proposed edits to `Security.md` and the module's `agents.md`.

## ISO 27001:2022 Annex A control mapping (working)

The codebase mitigations cluster around these Annex A:2022 controls. Mapping is filled in as audit progresses.

| Control | Title | Codebase mitigation |
|---|---|---|
| A.5.15 | Access control | `config/middleware/api_key_auth.py` (per-endpoint API keys, `allowed_endpoints` allowlists), Django staff/superuser auth on `/admin/` |
| A.5.17 | Authentication information | `APIKey` model in DB (40-char hex); `DJANGO_SECRET_KEY` env-only with startup check; `WEBHOOK_SECRET_<DRIVER>` env vars |
| A.5.23 | Information security for use of cloud services | (pending intelligence audit — provider URL validation) |
| A.8.2 | Privileged access rights | `bin/` toolchain confirmed not to grant sudo or install setuid; admin Django actions gated by `is_staff` |
| A.8.3 | Information access restriction | `APIKey.allowed_endpoints` path-prefix gating; admin actions outside webhook surface |
| A.8.9 | Configuration management | `.env` / `.env.sample` split; `bin/check_security.sh` runtime posture check (per `docs/plans/2026-03-30-security-check-script-design.md`) |
| A.8.11 | Data masking | Pipeline stores *references* (`normalized_payload_ref`, etc.) rather than raw payloads |
| A.8.12 | Data leakage prevention | Logging rules in `docs/Security.md` — no raw webhook payloads, tokens, or URLs in logs |
| A.8.21 | Security of network services | `config/security/url_validation.py` + `safe_urlopen` SSRF prevention with `SSRF_ALLOWED_HOSTS` allowlist |
| A.8.22 | Segregation of networks | (depends on deployment — out of code scope) |
| A.8.23 | Web filtering | n/a (we are the receiver, not a forward proxy) |
| A.8.24 | Use of cryptography | `hmac.compare_digest` for webhook signatures; Django's signed cookies; HTTPS enforcement settings documented in `Security.md` |
| A.8.25 | Secure development lifecycle | Pre-commit hooks (`bandit`, `pip-audit`, `detect-secrets`); CI security workflow; ruff `TID251` ban on raw `urlopen` |
| A.8.26 | Application security requirements | `config/security/` central utilities (`resolve_safe_path`, `validate_safe_url`); webhook auth & signature layering |
| A.8.27 | Secure system architecture | 4-stage orchestrator pattern with trust-boundary enforcement at each transition |
| A.8.28 | Secure coding | This audit; `apps/*/agents.md` developer rules; CLAUDE.md path-resolution rule |
| A.8.29 | Security testing in development | pytest suite, signature-verification tests at `apps/alerts/_tests/test_signature_verification.py` |
| A.8.30 | Outsourced development | n/a |
| A.8.31 | Separation of dev/test/prod | `DJANGO_ENV` / `DJANGO_DEBUG` separation, `.env.dev` loaded only when `DJANGO_ENV=dev` |
| A.8.32 | Change management | PRs + CI; `docs/plans/` historical decision record |

---

## Module: `bin/`

Audited: 2026-05-12. Status: **clean**. No findings at >= 0.8 confidence.

### Purpose & data classification
Installer, operator CLI, auto-update, and cluster setup scripts. Runs locally under the admin's shell or under `sudo` for system-level install. Handles `.env` writes, systemd unit installation, cron entries, and the optional auto-update flow that pulls from `origin/main`.

### Trust boundary
- All scripts are admin-invoked locally.
- Inputs: admin keystrokes via `read -p`, env vars, hard-coded constants, files under the project directory, and `origin/main` for the auto-update path.
- No network listener; no path that ingests external untrusted data.

### Threat model
- Reverse-shell injection via attacker-controlled input reaching `eval`/`bash -c`/`curl | sh`. → Not reachable; no external input.
- Privilege escalation: scripts granting sudo to the invoking user (sudoers writes, setuid binary installs). → Not present.
- Supply-chain from `origin/main` (intentional design; out of focus for app-layer audit).

### Controls in place
- `bin/lib/security_check.sh` and the planned `bin/check_security.sh` runtime posture audit.
- `sudo` only invoked on hard-coded operands (e.g. `sudo -u www-data bash -c "<literal>"` in `install/deploy.sh:303,307`).
- Generated `bin/aliases.sh` prefix validated against `^[a-zA-Z][a-zA-Z0-9_-]*$` (`install/aliases.sh:93-122`).
- HTTPS-only fetch of the official `uv` installer (`lib/checks.sh:63`).

### Sinks reviewed

| Sink | Locations | Verdict |
|---|---|---|
| `eval` | `cli.sh:74,90` | Inputs are admin-typed at `read -p` prompts; not crossing a trust boundary. |
| `curl ... \| sh` | `lib/checks.sh:63` | HTTPS official `uv` installer; supply-chain to `astral.sh`. |
| `sudo -u www-data bash -c` | `install/deploy.sh:303,307` | Operands hard-coded. |
| `sudo systemctl restart` | `lib/update.sh:395` | Hard-coded unit names. |
| `crontab -` writes | `install/cron.sh:83-133` | Built from admin prompts + hard-coded paths; runs as same user. |
| `git pull origin main` + `.env.sample` auto-append | `lib/update.sh:201,212-266` | Documented auto-update; `.env` consumed by Django, not exec'd. |
| Profile load | `lib/profile.sh:70-113` | Parses line-by-line; no `source`/`eval` of profile contents. |
| Reverse-shell primitives (`/dev/tcp/`, `mkfifo`+`sh`, `bash -i`, `nc -e`) | none | Absent. |
| Sudoers / setuid manipulation | none | Absent. |

### Findings
None.

### Sub-threshold observations
- Auto-update `--auto-env` blindly appends new `.env.sample` keys to `.env` from `origin/main`. Documented; trust in upstream repo is by design. **Doc note:** call this out explicitly in `Security.md` under a future "Supply chain" subsection.
- `confirm_and_run` builds an `eval`-able string. The bar is "no external input flows in"; if someone ever wires HTTP-received params into `confirm_and_run`, this becomes RCE. **Code-comment candidate:** add a single-line invariant comment at `cli.sh:74` stating the function is admin-input-only.

### Doc-update candidates
- `docs/Security.md` — short "Operator tooling (`bin/`)" subsection: "operator scripts are admin-invoked only; do not invoke them from HTTP handlers or task queues".
- New `bin/agents.md` (does not yet exist) — codify the invariant that `bin/` never consumes HTTP-derived or task-queue-derived input.

---

## Module: `apps/alerts/`

Audited: 2026-05-12. Status: **clean**. No findings at >= 0.8 confidence.

### Purpose & data classification
Webhook ingestion: receives alert payloads from 8 vendor drivers (Alertmanager, Cluster, Datadog, Generic, Grafana, NewRelic, OpsGenie, PagerDuty, Zabbix), normalizes them, persists `Alert`/`Incident` rows, and emits pipeline signals. **Primary internet-facing attack surface.**

### Trust boundary
- External: HTTP POST bodies to `/alerts/webhook/[<driver>/]`.
- Per-driver HMAC secrets (env) and the platform-wide `APIKey` (DB).
- Admin-controlled: `PipelineDefinition` config consumed downstream.

### Threat model
- Forged webhook → unauthenticated alert insertion / pipeline triggering.
- Body content reaching dangerous sinks (deserialization, eval, raw SQL, XXE, SSRF).
- Stored-XSS via incident fields rendered in the admin.
- Algorithm-confusion attacks on HMAC signature parsing.
- CSRF on the (intentionally exempt) webhook endpoint.

### Controls in place
- `APIKeyAuthMiddleware` (`config/middleware/api_key_auth.py`) gates `/alerts/` paths; only the exact GET `/alerts/webhook/` health probe is exempt.
- `apps/alerts/drivers/base.py:87` uses `hmac.compare_digest`; `sha256=` prefix stripped only when literally present (no silent algorithm downgrade).
- Body parsed with `json.loads` only — no `pickle`, `yaml.load`, `marshal`, `dill`, `eval`, `exec`, `compile`, `__import__`.
- Driver dispatch via fixed `DRIVER_REGISTRY` dict (`apps/alerts/drivers/__init__.py:58`); unknown driver → HTTP 400.
- Admin views use Django's `format_html(template, *args)` with `{}` placeholders — `conditional_escape` applied to all interpolated values.
- Signature verification tests at `apps/alerts/_tests/test_signature_verification.py`.

### Sinks reviewed

| Sink | Locations | Verdict |
|---|---|---|
| HMAC compare | `drivers/base.py:87` | `hmac.compare_digest`; non-`sha256=` prefixes fail closed. |
| Deserialization | `views.py:37` (`json.loads` only) | Safe. |
| SQL / ORM | grep for `.raw(`, `.extra(`, `cursor.execute` | No hits. |
| Command exec | grep for `subprocess`, `os.system`, `shell=True` | No hits. |
| XML | grep for `etree`, `XMLParser`, `lxml`, `xml.sax` | No hits. |
| SSRF | grep for `requests.*`, `urlopen` | No hits in views/drivers/services. Single outbound HTTP in `management/commands/push_to_hub.py:115` uses `safe_urlopen` with allowlist. |
| TLS bypass | grep for `verify=False`, `CERT_NONE` | No hits. |
| Path traversal | grep for `open(`, attacker-influenced paths | Only `is_open` status property at `models.py:221` (no file I/O). |
| Template injection | none | n/a |
| Admin XSS (`format_html`) | `admin.py:146,160,170,320,336,351` | All `{}`-placeholder, conditionally escaped. |
| CSRF | `@csrf_exempt` on `AlertWebhookView` (`views.py:20`) | Appropriate; auth enforced by `APIKeyAuthMiddleware`. |

### Findings
None.

### Sub-threshold observations
- **Auto-detect path can skip HMAC verification when a per-driver secret is unset.** When a request hits `/alerts/webhook/` (no explicit driver path), the matching driver is inferred from the JSON body. If `WEBHOOK_SECRET_<DRIVER>` is unset for the inferred driver, the optional HMAC check is skipped (verified by tests at `_tests/test_signature_verification.py:120,:147`). Primary auth remains the API-key middleware, but documenting this expectation prevents accidental loosening.
- **Drivers without `signature_header` (Alertmanager, Datadog, OpsGenie, Zabbix) cannot enforce HMAC.** This is by design (those vendors use other auth or rely on the upstream API-key). Worth listing in `Security.md` as a deliberate gap.
- **`json.loads(request.body)` runs before signature verification at `views.py:37` vs `:60`.** Python's `json` parser has no known code-execution vulnerabilities on UTF-8 input, so parse-before-verify is acceptable. Note this as a deliberate trade-off (driver auto-detect needs the parsed body to pick a driver).
- **`views.py:136` returns `str(e)` to the caller** on handler exceptions. Could leak internal error messages but no secret material flows through this path. Defense-in-depth: replace with a fixed error string in production.

### Doc-update candidates
- `docs/Security.md` — add an "Auto-detect path & HMAC enforcement" note clarifying that the `/alerts/webhook/` (driver-less) path falls back to API-key auth when the inferred driver's HMAC secret is unset. Recommend setting all relevant `WEBHOOK_SECRET_<DRIVER>` env vars where the driver supports it.
- `apps/alerts/agents.md` — codify: (1) all new drivers must set `signature_header` if the vendor supports HMAC; (2) HMAC comparison must use `hmac.compare_digest`; (3) error responses must not echo `str(e)` to the caller in production paths.

---

## Module: `apps/checkers/`

Audited: 2026-05-12. Status: **clean**. No findings at >= 0.8 confidence.

### Purpose & data classification
Stage 2 of the pipeline. Runs system health checks (CPU, memory, disk variants, network, process). Reads system state via `psutil` and `subprocess.run` to platform utilities (`ping`).

### Trust boundary
- External (post-auth): `payload.get("checker_configs")` originates from a webhook body and is forwarded via `apps/orchestration/executors.py:134` into checker constructors. The reach of attacker-controlled values is restricted by what `__init__` accepts.
- Admin-controlled: `PipelineDefinition`, `CheckRun` rows.
- Env / CLI: trusted per the brief.

### Threat model
- Command injection through subprocess args derived from webhook fields.
- Path traversal where attacker-influenced paths flow to `open()`/`os.walk()`.
- Code execution via dynamic checker loading.

### Controls in place
- Fixed `CHECKER_REGISTRY` dict (`apps/checkers/checkers/__init__.py`); unknown names rejected at `executors.py:36-44` and `check_integration.py:437`.
- `BaseChecker.__init__` consumes `**kwargs` and drops unknown keys — class-level constants (e.g. `scan_targets`) cannot be overridden from a webhook payload.
- Symlink follow disabled in disk scanners (`follow_symlinks=False`, `os.path.islink` skip).
- CLI flows route paths through `config.security.resolve_safe_path` (`check_health.py:135`, `run_check.py:115`).

### Sinks reviewed

| Sink | Locations | Verdict |
|---|---|---|
| `subprocess.run([...])` with externally-influenceable arg | `checkers/network.py:62` (`["ping", "-c", N, "-W", T, host]`) | `shell=False`, list-form. `ping` does not shell-interpret its host arg; flags do not enable code exec. |
| Other `subprocess.run` | `checks.py:138,458`; `preflight/checks.py:41,95,620,627,654,668` | Hardcoded argv. |
| `psutil.disk_usage(path)` | `checkers/disk/usage.py:49` | `statvfs`-only; returns FS totals, no file open. |
| `os.walk` / `os.path.getsize` | `checkers/disk/utils.py:120,179` | Operates on class-level constants, not kwargs. |
| `cursor.execute` | `checks.py:55`, `preflight/checks.py:198` | Literal `SELECT 1`. |
| Deserialization / dynamic load | grep | None — no `pickle`, `yaml.load`, `eval`, `exec`, `importlib.import_module`. |
| SSRF | grep | No `requests.*` / `urlopen`. |
| Admin XSS | `admin.py:89,111,121` | `format_html` with `{}` placeholders. |

### Findings
None.

### Sub-threshold observations
- **Pipeline executor does not run `checker_configs` paths through `resolve_safe_path`** the way the CLI commands do. Today this is fine because the only sink that receives those paths (`psutil.disk_usage`) is stat-only. **Doc note:** record this invariant; if a new checker is added that opens or executes paths, the executor must be updated to enforce `resolve_safe_path` symmetrically.
- **`ping -c N -W T host`**: defensible at the precedent level, but ping's `-p <pattern>` flag (where supported) and platform variants offer fuzzy edges. The `host` field's source (DB/admin config or webhook?) deserves a single-line note in the checker's docstring.

### Doc-update candidates
- `docs/Security.md` — under "Path Traversal Protection", add a row to the protected-entry-points table noting that pipeline-executor paths reach `psutil.disk_usage` stat-only (no resolution needed); if a future checker performs file I/O on those paths it MUST adopt `resolve_safe_path`.
- `apps/checkers/agents.md` — codify: (1) checker constructors that accept paths or hosts MUST validate format; (2) any subprocess call from a checker MUST use list-form argv; (3) class-level `scan_targets` constants are intentionally not constructor-overridable.

---

## Module: `apps/intelligence/`

Audited: 2026-05-12. Status: **1 MEDIUM finding — FIXED 2026-05-13** (path traversal via `scan_paths` config bypass; resolved by adding `scan_paths` to `BLOCKED_CONFIG_KEYS` in `apps/intelligence/providers/__init__.py` and adding regression tests in `apps/intelligence/_tests/providers/test_registry.py::TestBlockedConfigKeys`).

### Purpose & data classification
Stage 3 of the pipeline. Consumes upstream alert + checker output, calls AI providers (Anthropic, OpenAI, Gemini, Mistral, Ollama, Grok, Copilot) and a `local` provider that walks the filesystem to produce remediation recommendations. Provider configs (base URL, API keys, model names, scan paths) come from `IntelligenceProvider` DB rows and per-request config in API bodies.

### Trust boundary
- External (post-auth): `POST /intelligence/recommendations/` body — `provider`, `config`, `incident_id`. Reachable to any API key holder whose `allowed_endpoints` covers `/intelligence/` or `/orchestration/`.
- Admin-controlled: `IntelligenceProvider` rows (base URL, API key, default config).
- Env / trusted: `SSRF_ALLOWED_HOSTS`, provider env-default API keys.
- Outbound: AI provider HTTPS endpoints. `local` provider spawns `du` subprocess and walks filesystem.

### Threat model
- SSRF via attacker- or admin-supplied `host` / `base_url` for ollama/grok/copilot.
- Command injection through the `local` provider's `du` subprocess.
- Path traversal through `/intelligence/disk/?path=...` and provider config.
- API key leakage via logs, error responses, or admin display.
- Prompt-injection from upstream content reaching LLM (per project rules, not a vulnerability *unless* LLM output then reaches a code-exec sink — verified absent).

### Controls in place
- `BLOCKED_CONFIG_KEYS = frozenset({"host", "base_url"})` (`providers/__init__.py:79`) strips client-supplied SSRF-bearing fields before `get_provider` constructs the provider.
- `validate_safe_url(host_or_base_url, allowed_hosts=settings.SSRF_ALLOWED_HOSTS)` invoked at `__init__` of `ollama.py:28`, `grok.py:28`, `copilot.py:28`.
- SDK-default providers (`anthropic`, `openai`, `gemini`, `mistral`) do not accept `base_url` kwargs — no SSRF surface.
- `/intelligence/disk/?path=...` validates via `resolve_safe_path` before any filesystem read.
- `du` subprocess in `local.py:538` uses list-form argv with `shell=False` on a `resolve_safe_path`-validated path.
- `_redact_config` (`base.py:208`) redacts known-sensitive keys before storage; provider configs are flat so shallow redaction is sufficient.
- LLM output parsed only by `json.loads` (`ai_base.py:137`); no path from LLM output to `eval`/`exec`/`subprocess`.
- Provider dispatch via fixed `PROVIDERS` dict.
- `format_html` in admin uses `{}` placeholders only.

### Sinks reviewed

| Sink | Locations | Verdict |
|---|---|---|
| `validate_safe_url` | `ollama.py:28`, `grok.py:28`, `copilot.py:28` | Correctly invoked with `SSRF_ALLOWED_HOSTS`. |
| SDK `base_url` kwarg passthrough | `anthropic`, `openai`, `gemini`, `mistral` | No `base_url` accepted — hardcoded SDK default. |
| `BLOCKED_CONFIG_KEYS` filter | `providers/__init__.py:79` | Filters `host` and `base_url` only. Does **NOT** include `scan_paths` → **Finding 1**. |
| `subprocess.run([...])` | `local.py:538` (`du -sh ...`) | List-form, `shell=False`, path validated via `resolve_safe_path` first. |
| Filesystem walk | `local.py:649-657` (`_find_old_logs`) | When `path == "/"`, falls back to `self.scan_paths` without re-validating. **Finding 1**. |
| `resolve_safe_path` on disk view | `views/disk.py` + `_get_disk_recommendations` | Correctly applied; non-`/` paths re-validated in `_scan_large_files` and `_find_old_logs`. |
| Deserialization | grep | No `pickle`, `yaml.load`, `eval`, `exec`, `marshal`, `dill`, `__import__`, `compile`. |
| TLS | grep | No `verify=False`, `CERT_NONE`, `_create_unverified_context`. |
| SQL | grep | No `.raw`, `.extra`, `cursor.execute`. |
| Admin XSS | `format_html` calls in admin and `config/dashboard.py:15` | `{}` placeholders only; conditionally escaped. |
| API key logging | grep | Keys not logged. Errors surface SDK message only via `_get_fallback_recommendation`. |

### Findings

#### Finding 1 — Path traversal / information disclosure via `scan_paths` config (MEDIUM, confidence 8/10) — FIXED 2026-05-13

**Resolution:** Option A applied — `scan_paths` added to `BLOCKED_CONFIG_KEYS` in `apps/intelligence/providers/__init__.py:79`. Caller-supplied `scan_paths` via `/intelligence/recommendations/` or `/orchestration/*` is now stripped before reaching the provider constructor; DB-side admin-configured `scan_paths` in `IntelligenceProvider.config` continues to flow through (admin trust boundary preserved). Regression coverage in `apps/intelligence/_tests/providers/test_registry.py::TestBlockedConfigKeys`. Also fixes a `TypeError` collision that occurred when both DB config and caller kwargs supplied `scan_paths` (uncovered by `test_db_scan_paths_not_overridden_by_kwargs`).


**Locations:**
- `apps/intelligence/providers/local.py:86-108` — `__init__` accepts `scan_paths` kwarg, stored unvalidated.
- `apps/intelligence/providers/local.py:637-692` — `_find_old_logs` validates `path` via `resolve_safe_path` only when `path != "/"`. When `path == "/"`, falls back to `self.scan_paths` raw.
- `apps/intelligence/providers/local.py:653-657` — `Path(scan_dir).expanduser()` + `rglob("*")` walks each unvalidated entry.
- `apps/intelligence/providers/__init__.py:79` — `BLOCKED_CONFIG_KEYS = frozenset({"host", "base_url"})` does not include `scan_paths`.
- `apps/intelligence/providers/__init__.py:102-103` — `get_provider` strips only those two keys before passing `**kwargs`.
- `apps/intelligence/views/recommendations.py:75` — `RecommendationsView.post` passes `**provider_config` to `get_provider`.
- `apps/orchestration/executors.py:208-211` — `AnalyzeExecutor` forwards `provider_config` from payload to `get_provider`.

**Exploit:** Holder of an API key with `allowed_endpoints` covering `/intelligence/` (or `/orchestration/`) POSTs:
```json
{"provider": "local",
 "config": {"scan_paths": ["/etc", "/root", "/home"]},
 "incident_id": "<any disk-keyword incident>"}
```
The provider's `_analyze_disk_incident` calls `_get_disk_recommendations("/")` → `_find_old_logs("/")` → fallback uses attacker's `scan_paths` → `rglob("*")` over each → response includes `path`, `size_mb`, `modified` for every matching file (>1 MB, >30 days old, capped at 100). Symlinks are followed during file iteration. Permission errors are silently caught (line ~684), so failed reads do not surface stack traces.

**Privilege boundary crossed:** API keys are not Django staff; their intended scope is API endpoints, not arbitrary filesystem enumeration. Default `LOG_DIRECTORIES` excludes `/etc`, `/root`, `/home` — adding them via `scan_paths` is a real escalation past the key's scope.

**Why >= 0.8:** Concrete reach path, confirmed by reading the code; bypass goes around the project's own `resolve_safe_path` mitigation; produces real information disclosure (full paths, sizes, mtimes); the existing `BLOCKED_CONFIG_KEYS` allowlist demonstrates the design intent was to filter SSRF-bearing kwargs, just incomplete for path-bearing kwargs.

**Caveats lowering severity to MEDIUM (not HIGH):**
- Disclosure is metadata (path/size/mtime), not file contents.
- 1 MB size floor and 30-day age cutoff filter out most short-lived sensitive files.
- `old_files[:100]` cap bounds the leak per request.
- Trigger via `/intelligence/recommendations/` requires either a matching disk-keyword incident OR root partition >70% usage.

**Fix recommendation (pick one or both):**
- **Option A (preferred, simplest):** Extend `BLOCKED_CONFIG_KEYS` in `providers/__init__.py:79` to include `scan_paths`, `large_file_targets`, `old_file_targets`, and any other path-bearing kwargs the provider accepts. Caller-supplied paths should never override server-side defaults.
- **Option B (defense-in-depth):** In `LocalRecommendationProvider.__init__`, validate each `scan_paths` entry via `config.security.resolve_safe_path(entry)` and reject the constructor on `PathNotAllowedError`. Apply the same to the fallback at line 649.
- **Option C:** Move all path defaults to settings / Django config so they're not constructor-accepted at all.

**Test plan:** Add a test under `apps/intelligence/_tests/test_local_provider.py` that asserts `get_provider("local", scan_paths=["/etc"])` either raises or silently uses `LOG_DIRECTORIES`.

### Provider-by-provider verification matrix

| Provider | SSRF guard | API key handling | Subprocess |
|---|---|---|---|
| `local` | n/a — no outbound HTTP | n/a — no key | `du` argv-list, `resolve_safe_path` upstream. Safe. |
| `anthropic` | n/a — no `base_url` accepted | Key via SDK constructor; not logged/returned. | n/a |
| `openai` | n/a — no `base_url` accepted | Same as anthropic. | n/a |
| `gemini` | n/a — no `base_url` accepted | Same. | n/a |
| `mistral` | n/a — no `base_url` accepted | Same. | n/a |
| `ollama` | `validate_safe_url(host, SSRF_ALLOWED_HOSTS)` at `__init__`; `host` in `BLOCKED_CONFIG_KEYS` | n/a — host-only | n/a |
| `grok` | `validate_safe_url(base_url, SSRF_ALLOWED_HOSTS)`; `base_url` blocked | SDK key | n/a |
| `copilot` | `validate_safe_url(base_url, SSRF_ALLOWED_HOSTS)`; `base_url` blocked | SDK key | n/a |

### Sub-threshold observations
- **`BLOCKED_CONFIG_KEYS` is an allowlist by omission.** It blocks two specific keys today. Any new provider kwarg with security implications must be added explicitly. **Doc note:** codify "any new provider kwarg accepting a host, URL, path, command, or template name MUST be added to `BLOCKED_CONFIG_KEYS` OR validated at the constructor."
- **`_redact_config` is shallow** — adequate for current flat configs. If a future provider grows nested config (e.g. tool definitions), the redaction must become recursive.
- **Permission errors in `_find_old_logs` are silently swallowed.** Currently fine, but the swallow also masks unexpected I/O errors; a future audit of operational reliability may want to log them at DEBUG.
- **Symlink following in `rglob` is the default.** Not a vulnerability when paths are validated, but compounds the impact of Finding 1.

### Doc-update candidates
- `docs/Security.md` — under "Path Traversal Protection", add: "Provider config kwargs are filtered by `BLOCKED_CONFIG_KEYS` before reaching constructors. Any kwarg accepting a host, URL, path, command, or template name MUST be added to this set OR validated at the constructor."
- `docs/Security.md` — protected-entry-point table: add `intelligence/providers/local.py: scan_paths` after Finding 1 is fixed.
- `apps/intelligence/agents.md` — codify: (1) new provider kwargs accepting a URL/host MUST call `validate_safe_url`; (2) kwargs accepting a path MUST call `resolve_safe_path`; (3) any kwarg whose default is server-controlled MUST be added to `BLOCKED_CONFIG_KEYS`; (4) `_redact_config` must remain in sync with provider config shape.

### ISO 27001:2022 Annex A controls touched
- A.5.15 (Access control) — API key path-prefix restrictions; Finding 1 is a violation of intended scope.
- A.8.3 (Information access restriction) — Finding 1 enables broader filesystem-metadata access than intended.
- A.8.21 (Security of network services) — `validate_safe_url` correctly applied across SSRF-relevant providers.
- A.8.24 (Use of cryptography) — n/a; provider auth uses bearer tokens, no HMAC.
- A.8.26 (Application security requirements) — central `config.security` utilities are correctly invoked at most boundaries; Finding 1 is an inconsistency that should be closed.

---

## Module: `apps/notify/`

Audited: 2026-05-13. Status: **clean**. No findings at >= 0.8 confidence.

### Purpose & data classification
Stage 4 of the pipeline. Delivers notifications via Email (SMTP), Slack webhook, PagerDuty Events API v2, and a Generic outbound webhook. Receives incident + recommendation data from upstream stages. Channel config (URLs, SMTP creds, integration keys, payload templates) lives in `NotificationChannel.config` JSON, admin-controlled.

### Trust boundary
- External (post-auth): `POST /notify/...` request bodies hit API key-authenticated endpoints; selectors choose channels by ID/driver from the body.
- Admin-controlled: `NotificationChannel` rows (config dict, payload templates, target URL, SMTP credentials).
- Outbound: HTTPS to Slack webhooks, PagerDuty events endpoint, attacker-named generic webhook URLs (gated by `SSRF_ALLOWED_HOSTS`), and SMTP relays.
- Upstream: incident title/severity/labels (originally webhook-derived) flow into outbound bodies as template context, not as template source.

### Threat model
- SSRF via attacker-supplied generic webhook URL.
- SSTI through DB-stored payload templates.
- Email header injection via `Subject`/`From`/`To`/`Reply-To` fields containing CR/LF.
- Webhook secret/credential leakage via logs or echoed responses.
- Driver dispatch hijack via attacker-controlled driver name.
- Template name path traversal via DB config (`resolve_safe_name`).

### Controls in place
- `safe_urlopen` is invoked at every outbound HTTP call site: `drivers/slack.py:79`, `drivers/pagerduty.py:106`, `drivers/generic.py:93`. Redirect targets are re-validated by `safe_urlopen` against the allowlist.
- Slack `webhook_url` is additionally prefix-validated to `https://hooks.slack.com/` (the trailing slash blocks userinfo-host bypass like `https://hooks.slack.com@evil/`).
- PagerDuty endpoint is a hardcoded constant `https://events.pagerduty.com/v2/enqueue` — no URL surface.
- Templates are loaded via `resolve_safe_name` + `_FILENAME_PATTERN` regex; bare-string Jinja syntax ({% raw %}`{{`, `{%`, `{#`{% endraw %}) is explicitly rejected at `notify/templating.py:115-125` before name resolution.
- Inline templates run in `jinja2.sandbox.ImmutableSandboxedEnvironment`, blocking standard SSTI gadgets (`__class__`, `__mro__`, `__subclasses__`). Explicit regression tests under `apps/notify/_tests/` cover these gadgets.
- Email headers go through Python's `email.header.Header.encode()` which raises `HeaderParseError` on CRLF-embedded values.
- Outbound JSON payloads are built via `json.dumps` (not concatenation); templates can use the `|tojson` filter.
- All views are `APIKeyAuthMiddleware`-gated; `@csrf_exempt` is appropriate for stateless API endpoints.
- Driver dispatch via fixed `DRIVER_REGISTRY` dict.
- `DriversView` GET returns only static schema metadata, never channel configs.
- `NotificationChannelAdmin.__str__` shows `name (driver) [status]` only — no secrets.
- Logs include endpoint, status code, dedup key, message title, and remote error bodies — **not** raw API keys, SMTP passwords, integration keys, or routing keys.

### Sinks reviewed

| Sink | Locations | Verdict |
|---|---|---|
| Outbound HTTP | `drivers/slack.py:79`, `drivers/pagerduty.py:106`, `drivers/generic.py:93` | `safe_urlopen` with `SSRF_ALLOWED_HOSTS`. Slack: additional prefix validation. PagerDuty: hardcoded URL. |
| Redirect handling | `config/security/http.py` | `safe_urlopen`'s redirect handler re-validates each target. |
| Template body source | `notify/templating.py:115-125` + `ImmutableSandboxedEnvironment` | Bare-string Jinja rejected; sandboxed render; SSTI regression tests. |
| Template name | `notify/templating.py` via `resolve_safe_name` + `_FILENAME_PATTERN` | Path traversal closed. |
| Email headers | `drivers/email.py` | `Header.encode()` raises on CRLF. |
| Driver dispatch | `notify/drivers/__init__.py` | Fixed dict; unknown driver rejected. |
| Webhook payload assembly | drivers | `json.dumps` only; no string concatenation into JSON. |
| Logs | grep `logger.*` | Only endpoint URLs, IDs, status codes, message titles, and remote `error_body` strings. No config / secret echo. |
| SQL | grep | No `.raw`, `.extra`, `cursor.execute`. |
| Deserialization | grep | No `pickle`, `yaml.load`, `eval`, `exec`, `marshal`, `dill`, `__import__`. |
| TLS bypass | grep | No `verify=False`, `CERT_NONE`, `_create_unverified_context`. |
| Subprocess | grep | None. |
| Admin XSS | admin module | No `mark_safe` on user input; admin display strings static. |

### Driver-by-driver verification

| Driver | `safe_urlopen` | Secret leak risk | Template handling | Notes |
|---|---|---|---|---|
| `slack` | Yes (line 79); `webhook_url` prefix-validated to `https://hooks.slack.com/` | Logs `message.title` + remote `response_body` ("ok" / short error); webhook URL not logged | Sandboxed Jinja, `\|tojson` outputs, `json.dumps` payload | Redirect handler re-validates |
| `pagerduty` | Yes (line 106); URL is hardcoded constant | Logs `dedup_key` + extracted `error_msg`; routing/integration key sent but not logged | Sandboxed Jinja; `payload_template` required | Hardcoded URL eliminates URL-injection class |
| `generic` | Yes (line 93); host fully user-controlled; SSRF allowlist is the gating control | Logs endpoint URL + status; echoes response body back to API caller (documented behavior, gated by allowlist) | Sandboxed Jinja; `payload_template` required | User-supplied `headers` dict goes through `urllib.Request`; Python's `urllib` rejects CRLF in header values since 3.6 |
| `email` | n/a — SMTP | Logs only `message_id` (UUID); SMTP user/pass not logged | Sandboxed Jinja for body | `Header.encode()` rejects embedded-header injection |

### Findings
None.

### Sub-threshold observations
- **`format_map` fallback path in `notify/templating.py`.** Activates only if Jinja2 fails to import; Jinja2 is a hard dependency (`jinja2>=3.1.2` in `pyproject.toml`), so the path is unreachable today. The fallback rejects {% raw %}`{{`/`{%`/`{#`{% endraw %} substrings, blocking obvious SSTI vectors, but {% raw %}`{x.__class__}`{% endraw %}-style `format_map` exploits would technically work if the branch ever ran. Defense-in-depth: remove the fallback entirely or raise on missing Jinja2 at import time. (Confidence too low to flag as a finding; documenting here in case dependency strategy changes.)
- **Generic driver echoes remote response body back to the API caller.** Documented behavior; the SSRF allowlist (`SSRF_ALLOWED_HOSTS`) is the gating control. If allowlist policy ever loosens, this becomes a half-blind SSRF read primitive — note this dependency explicitly in `Security.md`.
- **Slack `webhook_url` prefix validation** depends on Slack never adding an open-redirect at `hooks.slack.com`. Currently no such redirect exists; `safe_urlopen`'s redirect re-validation closes the gap defensively.
- **Email driver's SMTP host has no `validate_safe_url` equivalent.** Acceptable because SMTP is not HTTP — the standard SSRF model does not apply — and `email.message_from_string`-style internal exfil channels are not present. Worth one-line acknowledgment in the email driver's docstring.

### Doc-update candidates
- `docs/Security.md` "Webhook security" subsection — add an explicit row for the Slack `https://hooks.slack.com/` prefix check.
- `docs/Security.md` "SSRF prevention" — note that the generic driver echoes the remote response body, so the allowlist is the gating control, not just a defense-in-depth layer.
- `apps/notify/agents.md` (create if absent) — codify: (1) any new outbound HTTP driver MUST use `safe_urlopen`; (2) templates MUST go through `resolve_safe_name` or be rendered in the `ImmutableSandboxedEnvironment`; (3) bare-string Jinja syntax in DB-stored template *names* is rejected on purpose; (4) no logger may include `channel.config` or other secret-bearing fields; (5) outbound JSON payloads MUST use `json.dumps`, never concatenation.

### ISO 27001:2022 Annex A controls touched
- A.5.15 (Access control) — `APIKeyAuthMiddleware` gates `/notify/`.
- A.8.21 (Security of network services) — `safe_urlopen` applied at all three HTTP call sites; redirect re-validation in place.
- A.8.24 (Use of cryptography) — TLS not bypassed in any driver.
- A.8.25 (Secure development lifecycle) — SSTI regression tests for sandbox bypasses.
- A.8.26 (Application security requirements) — Slack `hooks.slack.com` prefix check, PagerDuty hardcoded URL, email `Header.encode()` defense.
- A.8.28 (Secure coding) — sandboxed Jinja for templates; `|tojson` filter usage in payload templates.

---

## Module: `apps/orchestration/`

Audited: 2026-05-13. Status: **clean**. No findings at >= 0.8 confidence.

### Purpose & data classification
Pipeline state machine and stage dispatcher. Owns the `PipelineRun` / `StageExecution` / `PipelineDefinition` schema, the 4-stage hardcoded orchestrator (`PipelineOrchestrator`), and the JSON-config-driven `DefinitionBasedOrchestrator`. Receives payloads at `/orchestration/pipeline*` and `/orchestration/definitions/*/execute/`, dispatches to executors that wrap `apps/alerts`, `apps/checkers`, `apps/intelligence`, `apps/notify`. Emits monitoring signals via a pluggable backend (`LoggingBackend`, `StatsdBackend`).

### Trust boundary
- External (post-auth): `POST /orchestration/pipeline/`, `/pipeline/sync/`, `/pipeline/<run_id>/resume/`, `/definitions/<name>/execute/`, `/definitions/<name>/validate/` and the read endpoints. All gated by `APIKeyAuthMiddleware`. Bodies carry `payload`, `source`, `trace_id`, `environment`, `incident_id`, `provider`, `provider_config`, `notify_driver`, `notify_config`, `notify_channel`, `checker_names`, `checker_configs`, `labels`, `hostname`, etc.
- Admin-controlled: `PipelineDefinition.config` (JSON schema of nodes), `PipelineRun.mark_for_retry` and `mark_failed` actions via Django admin.
- Internal: Celery worker invocations of `run_pipeline_task` / `resume_pipeline_task` / `start_pipeline_task`. Broker is treated as trusted infrastructure; transport serializer is JSON-only (`CELERY_ACCEPT_CONTENT = ["json"]`).
- Outbound: none in orchestration itself — outbound HTTP/SMTP is delegated to `apps/notify` drivers; provider HTTP is delegated to `apps/intelligence` providers.

### Threat model
- Payload-field forgery: attacker-controlled `provider_config`, `notify_config`, `incident_id`, `trace_id` flowing through executors and node handlers into downstream stages.
- SSTI via attacker-supplied Jinja2 source in `notify_config` (handled by `_PAYLOAD_TEMPLATE_KEYS` strip in `executors.py:347`).
- Code/expression injection via `PipelineDefinition.config` (admin-controlled JSON) — does the node-type registry strictly bound which handlers can run?
- Idempotency-key collision: attacker chooses values that collide an existing `idempotency_key`, causing stage skip on retry.
- Replay / forgery of `run_id` / `trace_id` to attach work to a foreign pipeline.
- Deserialization of `output_snapshot` JSONField at admin time (XSS through `prettify_json` rendering).
- Cross-incident reference: attacker-supplied `incident_id` on `/definitions/<name>/execute/` causes downstream `Analyze` to fetch an unrelated incident.
- Celery task argument injection (broker compromise scenario; out of scope for app-level audit).

### Controls in place
- `APIKeyAuthMiddleware` gates every `/orchestration/` endpoint (per `Security.md` and confirmed by `config/middleware/api_key_auth.py` not exempting orchestration paths).
- `csrf_exempt` is correctly applied per stateless API conventions; views require API key, not session.
- Celery is JSON-only: `CELERY_ACCEPT_CONTENT = ["json"]`, `CELERY_TASK_SERIALIZER = "json"`, `CELERY_RESULT_SERIALIZER = "json"` (`config/settings.py:174-176`). No pickle path on the broker.
- `_PAYLOAD_TEMPLATE_KEYS = frozenset({"template", "payload_template", "html_template", "text_template"})` (`executors.py:34`) — stripped from payload-supplied notify config before `NotifySelector.resolve()` so untrusted payloads cannot inject Jinja2 source.
- Node-type dispatch is via a fixed in-process registry (`apps/orchestration/nodes/__init__.py:_NODE_HANDLERS`) registered only at module load. `DefinitionBasedOrchestrator.validate()` rejects any node with `type` not in `list_node_types()`.
- Stage dispatch via fixed `self.executors` dict on `PipelineOrchestrator`, keyed by enum values — no string-based dispatch from payload.
- `idempotency_key = f"{run_id}:{stage}:{attempt}"` uses server-generated `run_id` (uuid4) — attacker cannot collide a key without knowing another caller's `run_id`.
- `run_id` is always server-generated (`uuid.uuid4()` in `orchestrator.start_pipeline` and `DefinitionBasedOrchestrator.execute`). Attacker-supplied `run_id` in the body is ignored.
- `_stage_completed()` queries by `(pipeline_run, stage, StageStatus.SUCCEEDED)` — resume cannot be forged because `pipeline_run` is the lookup key from the URL path.
- `PipelineResumeView` requires the pipeline status to be `FAILED` or `RETRYING` before accepting a resume; otherwise 400.
- `_should_skip` expression handling in `definition_orchestrator.py:336-372` is a fixed substring matcher (`.has_errors`) — **no `eval`, `exec`, `compile`, `__import__`, or Jinja2** involved. Comment notes "no exec, only basic comparisons" and the implementation matches.
- All admin display uses `format_html` with `{}` placeholders (`admin.py:207-228`) and `format_html_join` over already-escaped `SafeString` parts. No `mark_safe` on user input.
- `JSONField`s for `config`, `tags`, `output_snapshot` are managed through Django ORM; `JSONEditorWidget` in admin renders into a JSON-validating editor, not raw HTML.
- `output_snapshot` is admin-rendered via `prettify_json` (defined in `config/dashboard.py:15`) — verified during `apps/intelligence` audit to use `format_html` with `{}` placeholders.
- CLI management commands (`run_pipeline --file`, `--config`) route attacker-controlled paths through `resolve_safe_path` + `PathNotAllowedError` (`run_pipeline.py:225, 367`). `setup_instance` uses `input()` only for interactive admin-trusted DB-config seeding.
- `mark_for_retry` and `mark_failed` admin actions are gated by Django staff (admin app default).

### Sinks reviewed

| Sink | Locations | Verdict |
|---|---|---|
| `subprocess` / `os.system` / `Popen` | grep across `apps/orchestration/**/*.py` | **None.** |
| `eval` / `exec` / `compile` / `__import__` | grep | **None.** `_should_skip` uses string replace + dict lookup only. |
| `pickle` / `yaml.load` / `marshal` / `dill` | grep | **None.** |
| Outbound HTTP (`urlopen`, `requests`, `safe_urlopen`) | grep | **None** in orchestration; delegated to `apps/notify` and `apps/intelligence`. |
| TLS bypass (`verify=False`, `CERT_NONE`, `_create_unverified_context`) | grep | **None.** |
| Raw SQL (`.raw`, `.extra`, `cursor.execute`) | grep | **None.** All DB access via Django ORM. |
| Celery serializer | `config/settings.py:174-176` | JSON-only on accept, task, and result. |
| Idempotency-key construction | `orchestrator.py:450`, `definition_orchestrator.py:307` | Built from server-side `run_id` (uuid4) + stage + attempt. Attacker-incollidable. |
| `run_id` / `trace_id` source | `orchestrator.py:152-155`, `definition_orchestrator.py:140-142` | `run_id` always server-generated; `trace_id` accepts caller value (correlation hint only, not security-bearing — see Sub-threshold). |
| Node-type dispatch | `nodes/__init__.py:_NODE_HANDLERS` + `DefinitionBasedOrchestrator.validate()` | Fixed registry; unknown types rejected at validation. |
| Stage dispatch | `orchestrator.py:124-129` | Fixed dict keyed by `PipelineStage` enum. |
| `_should_skip` expression eval | `definition_orchestrator.py:358-372` | Fixed `.has_errors` substring matcher; no expression evaluation. |
| `PipelineDefinition.config` JSON loaded into nodes | `definition_orchestrator.py:178, 290-309` | Goes through `validate()` (node type allowlist, ID uniqueness, `next` reference check) before `_execute_node`. |
| `provider_config` passthrough | `executors.py:208-211`, `nodes/intelligence.py:56` | Forwarded to `apps.intelligence.providers.get_provider`; gating relies on `BLOCKED_CONFIG_KEYS` in that module. See cross-ref to Finding 1 in intelligence module. |
| Template / config strip | `executors.py:347-349` | `_PAYLOAD_TEMPLATE_KEYS` rejects Jinja-source keys from payload before `NotifySelector.resolve`. |
| Admin XSS sinks (`mark_safe`, `format_html`) | `admin.py:178-229` | Uses `format_html` placeholders and `format_html_join` over pre-escaped parts. No `mark_safe` calls. |
| Logger calls | `orchestrator.py:167-170, 412-415`; `executors.py:98, 257, 525-536`; `definition_orchestrator.py:158-165, 196-199, 222-225, 241, 274` | Log `trace_id`, `run_id`, `provider` name, `channel` name, `title`, `severity`, `idempotency_key`, error type / message. **No** logging of API keys, provider credentials, or raw `provider_config` / `notify_config`. |
| CLI file paths | `management/commands/run_pipeline.py:225, 367` | `resolve_safe_path` + `PathNotAllowedError`. |
| Interactive input | `management/commands/setup_instance.py` | `input()` for admin-only DB seeding; values flow to DB config, not subprocess/eval/file ops. |

### Findings
None.

### Sub-threshold observations
- **`_should_skip` design caveat.** Today's implementation is a fixed `.has_errors` substring matcher — safe. The inline comment "Simple safe evaluation (no exec, only basic comparisons) ... For now, support basic patterns like 'node_id.has_errors'" reads as an invitation to expand into a real expression evaluator. If that ever happens, the obvious "easy" route (Python `eval`, `ast.literal_eval` against attacker-controlled `PipelineDefinition.config`, or Jinja2) opens a code-execution or SSTI surface. Codify in `apps/orchestration/agents.md` that `skip_if_condition` MUST remain a fixed-pattern matcher; any richer condition language must go through an explicit safe-expression parser (e.g., `simpleeval` with no names/attrs, or AST allowlist).
- **`incident_id` accepted from request body without authorization check.** `PipelineDefinitionExecuteView.post` (`views.py:372`) passes the caller's `incident_id` through to `DefinitionBasedOrchestrator.execute`, which writes it directly onto `PipelineRun.incident_id` (`definition_orchestrator.py:154`). Downstream `AnalyzeExecutor` will then fetch the incident and pass its content to the AI provider. In a single-tenant deployment (the current model) this is not a vulnerability — every API key has access to every incident anyway. In any future multi-tenant deployment it becomes a cross-tenant information-disclosure primitive. Doc this as a "single-tenant assumption" in `Security.md` so it surfaces during any multi-tenancy redesign.
- **`trace_id` is caller-controllable.** Used only for log correlation, not authorization. Attacker can forge a `trace_id` matching another caller's to interleave log records — confusing forensics but not a vulnerability in itself. Acknowledge in `agents.md`: trace IDs are correlation hints, never authorization tokens.
- **Synchronous `time.sleep(backoff_factor**attempt)` in `_execute_stage_with_retry`** (`orchestrator.py:528, 551`). For Celery workers this is fine; in `PipelineView` sync mode it ties up the request thread. Operational concern (DoS amplification if `max_retries` is large and a stage fails fast), not a code-injection finding. Note: `ORCHESTRATION_MAX_RETRIES_PER_STAGE` and `ORCHESTRATION_BACKOFF_FACTOR` come from settings, not payload — attacker cannot expand the sleep window beyond admin-configured bounds.
- **`PipelineDefinitionExecuteView` sets HTTP 500 on `status != "completed"`** (`views.py:383`). This is intentional, but a more typical pattern is 422 / 4xx for definition-validation failures. Cosmetic, not security-impacting.
- **`PipelineResumeView` does not re-authenticate the caller against the original pipeline.** Any API key holder whose `allowed_endpoints` covers `/orchestration/pipeline/` can resume any failed pipeline. Acceptable for single-tenant; should be revisited before any per-tenant separation.
- **`DefinitionBasedOrchestrator.validate()` returns errors at HTTP 200 with `"valid": false`** rather than a 4xx. Consistent with the rest of the validate-endpoint convention; flagged here only as a doc note.

### Doc-update candidates
- `docs/Security.md`:
  - "Pipeline orchestration" subsection — explicit single-tenant assumption: `incident_id` is accepted from request bodies without per-actor authorization; revisit before multi-tenancy.
  - Note that `trace_id` is a correlation hint, not an auth token; forging it does not grant access.
  - Confirm Celery JSON-only is documented in the existing "Serialization" subsection (already noted in pre-audit — verify wording matches).
- `apps/orchestration/agents.md` (extend, or create if absent):
  - Pipeline payload fields (`provider`, `provider_config`, `notify_driver`, `notify_config`, `incident_id`, `trace_id`, `checker_configs`) are external, post-API-key, attacker-controlled. Treat them as untrusted in every executor and node handler.
  - Any new node type's `validate_config` MUST be implemented and called from `DefinitionBasedOrchestrator.validate()`. Nodes without it become an attack surface for `PipelineDefinition` admin.
  - `_should_skip` `skip_if_condition` MUST remain a fixed-pattern matcher. Do **not** introduce `eval`, `exec`, `compile`, `ast.literal_eval` over attacker data, or Jinja2 expression evaluation here.
  - Any new provider kwarg that accepts a host, URL, path, command, or template name MUST be added to `apps.intelligence.providers.BLOCKED_CONFIG_KEYS` because orchestration forwards `provider_config` verbatim — cross-reference Finding 1 in the intelligence audit.
  - `_PAYLOAD_TEMPLATE_KEYS` set in `executors.py` is allowlist-by-omission. Any future template-bearing config key MUST be added to this set.
  - `run_id` MUST always be server-generated (`uuid.uuid4()`); do not accept caller-supplied `run_id` to identify or attach work.
  - `trace_id` is a correlation hint only; never use it for authorization decisions.

### ISO 27001:2022 Annex A controls touched
- A.5.15 (Access control) — `APIKeyAuthMiddleware` gates every `/orchestration/` endpoint; admin retry/mark-failed actions gated by Django staff.
- A.8.2 (Privileged access rights) — `PipelineDefinition` config edits, retry/mark-failed actions are staff-only via Django admin.
- A.8.9 (Configuration management) — `PipelineDefinition.config` is admin-controlled JSON validated through a fixed node-type registry; dynamic code loading is not possible.
- A.8.21 (Security of network services) — orchestration emits no outbound HTTP itself; delegates to `apps/notify` and `apps/intelligence` where SSRF is mitigated.
- A.8.24 (Use of cryptography) — Celery serializer is JSON-only on accept/task/result; no pickle path through the broker.
- A.8.25 (Secure development lifecycle) — `_should_skip` design discipline (fixed-pattern matcher) is recorded as a forward-looking constraint in agents.md.
- A.8.26 (Application security requirements) — node-type dispatch via fixed in-process registry; stage dispatch via enum-keyed dict; payload template keys stripped before driver resolution.
- A.8.28 (Secure coding) — `format_html` placeholders + `format_html_join` over `SafeString` parts in admin; no `mark_safe` on user data.

---

## Module: `config/` (middleware + security utilities)

Audited: 2026-05-13. Status: **clean**. No findings at >= 0.8 confidence.

### Purpose & data classification
The cross-cutting security and infrastructure layer. Owns:
- `config/middleware/api_key_auth.py` — `APIKeyAuthMiddleware`, the single auth gate for every `/alerts/`, `/orchestration/`, `/notify/`, `/intelligence/` request (admin/static paths exempt).
- `config/middleware/rate_limit.py` — `RateLimitMiddleware`, fixed-window rate limiter on mutating requests.
- `config/security/url_validation.py` — `validate_safe_url` (SSRF check against private/reserved IPs).
- `config/security/http.py` — `safe_urlopen` (SSRF-validating drop-in for `urllib.request.urlopen`, with redirect re-validation).
- `config/security/path_traversal.py` — `resolve_safe_path` / `resolve_safe_name`.
- `config/models.py` — `APIKey` model (hashed-at-rest with `secrets.token_hex(20)`-generated raw tokens).
- `config/settings.py` — Django/Celery configuration, including SSRF allowlist, rate limits, logging.
- `config/dashboard.py` — admin dashboard data + `prettify_json` (XSS-safe JSON pretty-printer).
- `config/checks.py` — Django system checks for misconfiguration.

### Trust boundary
- External (pre-auth): HTTP requests entering Django before middleware. `APIKeyAuthMiddleware` is the only gate between unauthenticated callers and the API.
- Admin-controlled: `APIKey` rows, `SSRF_ALLOWED_HOSTS` env var, `RATE_LIMITS` settings, `ALLOWED_FILESYSTEM_ROOTS`, env (`DJANGO_SECRET_KEY`, etc.).
- Internal: Django session-auth path (`/admin/`) is exempt from API-key middleware; admin gates via Django staff.
- Outbound: none directly — all outbound HTTP goes through `safe_urlopen` (called from `apps/notify` drivers) or external SDKs (from `apps/intelligence` providers, with `validate_safe_url` called at provider `__init__`).

### Threat model
- Auth bypass: path-prefix smuggling against `EXEMPT_PATH_PREFIXES`, missing-Authorization fallback to header smuggling, method-spoofing (e.g., GET-only exemption misuse).
- Timing attack on API key validation.
- Rate-limit bypass via cache-key collision, identity spoofing (`REMOTE_ADDR` behind a proxy), or window jitter.
- SSRF bypass via DNS rebinding, redirect-chain misuse, scheme confusion, userinfo-host smuggling, IPv6 transitional addresses.
- Path-traversal bypass via symlink TOCTOU, NULL-byte injection, `..` after resolution, allowlist root containing `/`.
- API-key leakage via admin display, logs, or hash inversion.
- `ALLOWED_HOSTS` misconfiguration enabling Host-header attacks on the Django side.
- Celery deserialization via non-JSON serializers.

### Controls in place
- **Auth gate:** `APIKeyAuthMiddleware.__call__` (`api_key_auth.py:33`) checks `EXEMPT_PATH_PREFIXES = ("/admin/", "/static/")`, then `API_PATH_PREFIXES = ("/alerts/", "/orchestration/", "/notify/", "/intelligence/")`. Health-check exemption is a **path-equality** check (`path in HEALTH_CHECK_PATHS`) — not a startswith match — so it cannot be bypassed with a `/alerts/webhook/xyz` suffix.
- **API key hashing:** raw key is `secrets.token_hex(20)` (40 hex chars / 160 bits of entropy). Persisted as SHA-256 hex (`models.py:51`). DB lookup hashes user-supplied input first, so the `WHERE key=...` query is constant-time in user input; the DB-side index lookup is timing-stable per (b-tree node) and a side-channel attacker can only learn "valid hash exists" — and they must produce a 256-bit collision to forge one.
- **Per-key allowlist:** `APIKey.allowed_endpoints` (path-prefix list) gates per-endpoint authorization (`api_key_auth.py:69-74`).
- **Rate limiting:** `RateLimitMiddleware` exempts `/admin/`, `/static/`, all GET requests, and unknown prefixes — only mutating writes are throttled. Atomic `cache.add` + `cache.incr` correctly handles the get-then-set race; `incr` ValueError on expired-key boundary is handled by re-seeding the bucket conservatively (`rate_limit.py:68-75`).
- **Most-specific prefix wins:** `RateLimitMiddleware` sorts known prefixes by descending length before matching (`rate_limit.py:48`).
- **SSRF base check:** `validate_safe_url` (`url_validation.py:31`) enforces: scheme ∈ {http, https}; non-empty hostname; allowlist bypass requires exact hostname match; otherwise `socket.getaddrinfo()` resolves the hostname and every returned IP must be public (rejects `is_private`, `is_loopback`, `is_link_local`, `is_reserved`, `is_multicast`). Python's `ipaddress` covers `0.0.0.0/8` (`is_private`), `::/128` (private), `100.64.0.0/10` (CGNAT, `is_private`), `fc00::/7` (ULA, `is_private`).
- **SSRF redirect re-validation:** `safe_urlopen` builds an opener using `_SSRFRedirectHandler`, which calls `validate_safe_url` on every redirect target (`http.py:19-21`).
- **Userinfo-host parsing:** `urllib.parse.urlparse(url).hostname` returns the real hostname, not `userinfo@hostname`. So `http://hooks.slack.com@evil.attacker.com/` parses with `hostname="evil.attacker.com"` and fails SSRF validation; userinfo cannot be used to smuggle a trusted-looking allowlist match.
- **Lint enforcement:** ruff banned-API (`pyproject.toml:89-90`) makes raw `urllib.request` import a lint error project-wide — `# noqa: TID251` is only present in `config/security/http.py:3` where `safe_urlopen` is implemented.
- **Path-traversal base check:** `resolve_safe_path` (`path_traversal.py:12`) requires absolute path, calls `Path.resolve()` (resolves symlinks at validation time), then containment checks against `ALLOWED_FILESYSTEM_ROOTS = (/var, /tmp, /home, /opt, /srv, /usr)` — **`/` and `/etc` and `/root` excluded** by construction.
- **Filename validation:** `resolve_safe_name` (`path_traversal.py:38`) rejects `/`, `\`, leading `.` (blocks `.env`, `.git`), and any `..` substring.
- **NULL-byte handling:** `Path()` constructor raises `ValueError` on embedded `\x00` (Python 3.10+) — `resolve_safe_path` propagates this as `ValueError` to the caller.
- **Celery serializers:** `CELERY_ACCEPT_CONTENT = ["json"]`, `CELERY_TASK_SERIALIZER = "json"`, `CELERY_RESULT_SERIALIZER = "json"` (`settings.py:174-176`). No pickle path on the broker.
- **DEBUG & SECRET_KEY discipline:** `settings.py:33-38` raises `RuntimeError` at startup if `DJANGO_SECRET_KEY` is missing, preventing default-secret deployments.
- **Auth-disabled production check:** `config/checks.py:check_auth_enabled` emits `config.W002` warning if `API_KEY_AUTH_ENABLED=False` and `DEBUG=False` (caught by `manage.py check` / preflight).
- **Rate-limit cache backend check:** `config/checks.py:check_rate_limit_cache` warns if rate limit is enabled with in-memory cache (`config.W001`).
- **Admin display safety:** `prettify_json` (`dashboard.py:15`) uses `format_html` with `{}` placeholder — JSON content is HTML-escaped before insertion into the `<pre>` block.
- **APIKey admin discipline:** `APIKeyAdmin.masked_key` shows `f"{prefix}***"` (8-hex-char prefix only); `key` is in `readonly_fields` so the SHA-256 digest cannot be edited or replaced via admin; full raw key is never persisted nor displayed.
- **`request.api_key` attachment:** middleware sets `request.api_key` after successful auth (`api_key_auth.py:77`). Downstream middleware/views can read this attribute but cannot un-authenticate the request mid-flight (subsequent middleware is single-pass).
- **Django CSRF + admin:** `CsrfViewMiddleware` is present in `MIDDLEWARE` (`settings.py:70`); API views use `@csrf_exempt` (stateless API pattern); admin uses Django's session-CSRF flow. `XFrameOptionsMiddleware` is enabled (clickjacking protection on admin).

### Sinks reviewed

| Sink | Location | Verdict |
|---|---|---|
| `subprocess` / `os.system` / `Popen` | grep `config/**/*.py` | **None.** |
| `eval` / `exec` / `compile` | grep | **None.** `__import__` only in `config/_tests/*` for module reloading (test scope). |
| `pickle` / `yaml.load` / `marshal` / `dill` | grep | **None.** |
| Raw SQL (`.raw`, `.extra`, `cursor.execute`) | grep | **None.** All DB access through Django ORM. |
| `urllib.request.urlopen` direct use | grep | Only at `config/security/http.py:3, 8, 46` (the safe-wrapper implementation itself), all with `# noqa: TID251`. `tool.ruff` ban (`pyproject.toml:89-90`) blocks every other call site. |
| TLS bypass | grep | **None.** No `verify=False`, `CERT_NONE`, `_create_unverified_context`. |
| `mark_safe` / SafeString construction | grep | **None.** `prettify_json` uses `format_html` placeholder. |
| Authorization header parsing | `api_key_auth.py:81-85` | `Bearer ` prefix with strip; falls back to `X-API-Key`. No multi-header concatenation handling needed (WSGI joins duplicates with comma; `startswith` would then reject). |
| Health-check exemption | `api_key_auth.py:45` | **Equality** check (`path in HEALTH_CHECK_PATHS`), not startswith — closes prefix-smuggling on health endpoints. |
| API key DB lookup | `api_key_auth.py:62-67` | Hashes input first, query is on hashed digest. No timing leak. |
| Path-prefix exempt match | `api_key_auth.py:39, 42` | `startswith` against `EXEMPT_PATH_PREFIXES = ("/admin/", "/static/")`. URL routing does not resolve `..` segments in Django, so `/admin/../alerts/webhook/` exempts at middleware but Django's URL resolver fails to match (no admin sub-path resolves `../alerts/...`), yielding 404. **See Sub-threshold #1.** |
| Rate-limit identity | `rate_limit.py:90-96` | Uses `request.api_key.name` (admin-set) or `REMOTE_ADDR`; `REMOTE_ADDR` correctness depends on reverse-proxy config — see Sub-threshold #2. |
| Cache key construction | `rate_limit.py:59` | f-string with `identity`, `matched_prefix`, `window`; no untrusted-byte injection possible (Redis/locmem accept any bytes; Memcached would reject control chars but identity is admin-named or IP, prefix is from `RATE_LIMITS` settings, window is int). |
| `validate_safe_url` DNS resolution | `url_validation.py:60` | Calls `getaddrinfo` once; redirect handler re-calls per redirect. **DNS rebinding — see Sub-threshold #3.** |
| `_SSRFRedirectHandler.redirect_request` | `http.py:19-21` | Validates each redirect target before delegating to base handler. |
| `Path.resolve` symlink handling | `path_traversal.py:23` | Symlinks resolved at validation; TOCTOU between resolve and open is the standard limitation — see Sub-threshold #4. |
| `secrets.token_hex(20)` | `models.py:56` | 160 bits of CSPRNG entropy. |
| Celery broker URL | `settings.py:166` | Env-controlled, no user input. JSON-only serializer prevents pickle deserialization regardless of broker compromise scope. |
| Logger config | `settings.py:257-288` | `FileHandler` to env-controlled `LOGS_DIR`; no PII fields named; per-app audits confirmed no secret echo. **No log rotation configured — operational, not security.** |
| Dashboard query layer | `dashboard.py:29-130` | All ORM aggregations; no string interpolation into SQL. |

### Findings
None.

### Sub-threshold observations
1. **`EXEMPT_PATH_PREFIXES` is a startswith match against unnormalized `request.path`.** Django's URL routing does not normalize `..` segments before middleware sees the path. A request like `GET /admin/../alerts/webhook/` would (a) be exempted from API-key auth by `path.startswith("/admin/")` and then (b) fail to route (no admin sub-path matches `../alerts/...`), yielding a 404. So the current routes happen to make this unreachable. Two failure modes to watch:
   - If a future view is mounted under a path that would resolve through an exempt prefix (e.g., adding routes inside admin that pass through to API logic), the exemption could become a real bypass.
   - If a reverse proxy or WSGI server normalizes `..` before forwarding (some do, some don't), the behavior changes per deployment.
   Codify in `agents.md`: any new `EXEMPT_PATH_PREFIX` must be paired with a routing review confirming no API-bearing sub-path is reachable below it. Consider switching to an exact-equality or end-of-path-anchored match — same pattern already used for `HEALTH_CHECK_PATHS`.

2. **Rate-limit identity uses `REMOTE_ADDR` without proxy-aware override.** `_get_identity` reads `request.META.get("REMOTE_ADDR", "unknown")` directly. Behind a reverse proxy (nginx, Cloudflare, ALB), `REMOTE_ADDR` is the proxy's IP — every external client shares a single rate-limit bucket and the limiter becomes effectively a global throttle. The Django way to handle this is to set `USE_X_FORWARDED_HOST` / parse `X-Forwarded-For` carefully (only trust upstream proxy IPs). Document the deployment requirement in `Security.md`: when serving behind a proxy, either configure a proxy-aware `REMOTE_ADDR` setter middleware **before** `RateLimitMiddleware`, or rely solely on per-API-key bucketing (most operational deployments use named API keys, which already side-step this).

3. **`validate_safe_url` is vulnerable to DNS rebinding in the abstract.** The check resolves DNS at validation time (`socket.getaddrinfo` in `url_validation.py:60`); the actual `urlopen` connect later re-resolves DNS. An attacker controlling a DNS record with TTL=0 could return a public IP at validation and a private IP at connect (or vice versa). **In the current codebase the reach path is narrow:**
   - `apps/notify/drivers/slack.py` — `webhook_url` is admin-set in `NotificationChannel.config`; in practice always resolves to Slack-controlled DNS.
   - `apps/notify/drivers/pagerduty.py` — URL is a hardcoded constant.
   - `apps/notify/drivers/generic.py` — `endpoint_url` is admin-set.
   - `apps/intelligence/providers/{ollama,grok,copilot}.py` — `host`/`base_url` is in `BLOCKED_CONFIG_KEYS` (per Finding 1 in intelligence module), so API callers cannot supply it; admin sets it in `IntelligenceProvider`.
   - In **no** code path is the URL itself attacker-supplied via `/alerts/`, `/orchestration/`, `/notify/`, or `/intelligence/` request bodies.
   The practical exploit therefore requires either (a) admin to have configured a URL whose DNS the attacker controls, or (b) `SSRF_ALLOWED_HOSTS` to include a host whose DNS the attacker controls. Both are extreme scenarios in single-tenant deployments. Defense-in-depth options: pinned-IP HTTP (resolve once, connect with the resolved IP, set Host header to original hostname); or route outbound traffic through an egress proxy that re-validates at the network layer. Doc this gap explicitly in `Security.md` so the assumption is recorded.

4. **`resolve_safe_path` is subject to TOCTOU between resolve and use.** `Path.resolve()` resolves symlinks at validation time; if a path is replaced with a symlink to an out-of-tree target *after* resolve but *before* the caller `open()`s it, the open follows the new symlink. Defense requires `os.open(path, O_NOFOLLOW)` or similar fd-based handling — neither is used. In single-user / single-tenant deployments the attacker would already need filesystem-write access to exploit; for shared-host or multi-tenant deployments this becomes a real concern. Note this in the path-validation discussion in `Security.md`.

5. **`/home` is in `ALLOWED_FILESYSTEM_ROOTS`.** On a server where only the service user's home directory should be readable, `/home` lets any path under `/home/*` resolve through `resolve_safe_path`. Operational hygiene, not a vuln in the codebase — admin should narrow `ALLOWED_FILESYSTEM_ROOTS` to e.g. `/home/<service-user>` for production. Worth one row in the operator hardening checklist.

6. **No log rotation configured.** `LOGGING.handlers.file` is a plain `FileHandler` writing to `LOGS_DIR/django.log`. Disk fill is operational risk. Recommend `RotatingFileHandler` or `TimedRotatingFileHandler` in `settings.py`; or external rotation via logrotate. Not a security vulnerability.

7. **Django production hardening settings not set in `config/settings.py`.** No explicit `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_PROXY_SSL_HEADER`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_REFERRER_POLICY`, or `SECURE_CONTENT_TYPE_NOSNIFF` overrides. Django's defaults apply. `Security.md` should list these as operator-set-in-production with the recommended values (or move them under a `DJANGO_PRODUCTION=1` settings branch). Same for `ALLOWED_HOSTS` — empty by default; admin must populate before running with `DEBUG=False`.

8. **API key `prefix` is admin-displayable.** 8 hex chars (~32 bits) of the raw key are visible in the admin list view. Remaining 128 bits of entropy keep brute-force out of reach; documenting the disclosure model in `Security.md` is sufficient (admin display is a trust-bearing context).

9. **`RateLimitMiddleware` skips all GET methods.** Read endpoints like `/orchestration/pipelines/`, `/notify/channels/`, `/intelligence/providers/` are exempt from rate-limiting and could be abused for enumeration / metadata harvesting at scale. Per-API-key allowlists already gate which read endpoints each key sees, so this is an operational concern (cost of read amplification) rather than a security one. Acknowledge in `Security.md` that rate limiting protects mutating actions; read-endpoint pacing depends on the upstream proxy.

10. **Admin and static path exemptions assume admin lives at `/admin/`.** If `ROOT_URLCONF` ever moves admin to a non-default path (a common hardening practice), the exempt-prefix list must be updated in lockstep.

### Doc-update candidates
- `docs/Security.md`:
  - "Authentication" subsection — explicit note that path-prefix exemptions assume admin lives at `/admin/`; any rebase must update `EXEMPT_PATH_PREFIXES`.
  - "Rate limiting" subsection — proxy-aware-REMOTE_ADDR caveat (Sub-threshold #2); explicit note that only mutating methods are rate-limited (Sub-threshold #9).
  - "SSRF Protection" subsection — record the DNS-rebinding gap explicitly (Sub-threshold #3): "URLs reachable via `safe_urlopen` come from admin DB config in every current code path; DNS rebinding by an attacker who has compromised DNS for an admin-configured host is the residual risk."
  - "Path Traversal Protection" subsection — record the resolve-vs-open TOCTOU caveat (Sub-threshold #4) and the `/home`-in-allowlist operator-hygiene note (Sub-threshold #5).
  - "Production hardening" subsection (new) — checklist of `SECURE_*` settings, `ALLOWED_HOSTS` requirement, log rotation requirement, narrowed `ALLOWED_FILESYSTEM_ROOTS`.
  - "API key model" subsection — explicit note that the 8-char `prefix` is admin-displayable (Sub-threshold #8) and that remaining entropy is 128 bits.
- `apps/<every-app>/agents.md` (already-planned per cross-cutting parking lot):
  - Cross-reference the central utilities: `validate_safe_url` / `safe_urlopen` for any outbound HTTP; `resolve_safe_path` / `resolve_safe_name` for any path input; `APIKey.allowed_endpoints` for per-endpoint authorization.
- `config/_tests/` and `apps/*/_tests/` (test-plan):
  - Add a unit test asserting `EXEMPT_PATH_PREFIXES` cannot reach an API path through `..` segments under the configured WSGI server.
  - Add a unit test asserting `validate_safe_url` rejects `0.0.0.0`, `100.64.0.1` (CGNAT), `fc00::1` (ULA), and an IPv4-mapped-IPv6 form like `::ffff:127.0.0.1`.

### ISO 27001:2022 Annex A controls touched
- A.5.15 (Access control) — `APIKeyAuthMiddleware` enforces the project's single API-key auth gate; `APIKey.allowed_endpoints` enforces per-key endpoint restriction.
- A.5.17 (Authentication information) — `secrets.token_hex(20)`-generated raw keys, SHA-256-at-rest, raw never persisted; `DJANGO_SECRET_KEY` env-only with startup check.
- A.8.2 (Privileged access rights) — admin path (`/admin/`) uses Django session+staff; only `is_staff` users can edit `APIKey` and `IntelligenceProvider` / `NotificationChannel` config.
- A.8.3 (Information access restriction) — `APIKey.allowed_endpoints` provides path-prefix scoping per key.
- A.8.5 (Secure authentication) — Bearer-token + `X-API-Key` header model; no cookie-based API auth (CSRF surface narrowed by `@csrf_exempt` on stateless API).
- A.8.9 (Configuration management) — env-loading discipline (`config/env.py` with `override=False`); Django system checks (`config/checks.py`) surface misconfigurations.
- A.8.20 (Networks security) — `validate_safe_url` private/reserved-IP allowlist on outbound HTTP destinations.
- A.8.21 (Security of network services) — `safe_urlopen` with redirect re-validation; ruff `TID251` ban as compile-time gate.
- A.8.23 (Web filtering) — n/a (no inbound URL filtering beyond Django routing).
- A.8.24 (Use of cryptography) — Celery JSON-only serializer; SHA-256 for API key digests; no TLS bypass anywhere.
- A.8.25 (Secure development lifecycle) — ruff banned-API rule encodes the SSRF contract at lint time.
- A.8.26 (Application security requirements) — central `config.security` package consolidates the project's URL/path mitigations; admin uses `format_html` placeholders consistently.
- A.8.28 (Secure coding) — `secrets.token_hex` for key generation; `hashlib.sha256` for storage; no `mark_safe` on user data; HTML output via `format_html` placeholders.

---

## Cross-cutting observations (final)

1. **`format_html` discipline is consistent across every audited admin module** (`alerts`, `checkers`, `intelligence`, `notify`, `orchestration`, `config`). Every interpolated value goes through a `{}` placeholder; `format_html_join` operates over pre-escaped `SafeString` parts. No `mark_safe` on user data anywhere. Codify as a single bullet across all `apps/*/agents.md`.
2. **`hmac.compare_digest` discipline is correct in alerts**; `apps/notify` has no inbound-HMAC sink (outbound cluster push uses a bearer-style `WEBHOOK_SECRET_CLUSTER` shared secret, not an HMAC signature on payload); `apps/orchestration` has no HMAC sink. The only inbound HMAC path is in webhook drivers under `apps/alerts/drivers/*`.
3. **Trust-boundary discipline is consistent.** The codebase cleanly separates external (HTTP/webhook), admin (DB / env-trusted config), and internal (CLI / Celery / settings) trust. Preserve this in every `agents.md` so future contributors don't blur the boundaries.
4. **Path validation has a documented utility (`config/security`) but inconsistent application.** CLI commands and the intelligence `disk` view route through `resolve_safe_path`; pipeline-executor paths do not (currently safe because the sink is stat-only). The intelligence audit (Finding 1) is the first concrete case where the inconsistency produced a real gap. Codify in `apps/intelligence/agents.md` (already in plan): any provider kwarg accepting a path MUST be validated at the constructor or filtered via `BLOCKED_CONFIG_KEYS`.
5. **The auto-update flow (`bin/lib/update.sh`) intentionally trusts `origin/main`.** Not a vulnerability, but `Security.md` needs a "Supply chain" subsection acknowledging this.
6. **Central security utilities consolidate the project's mitigations.** `validate_safe_url`, `safe_urlopen`, `resolve_safe_path`, `resolve_safe_name`, `APIKeyAuthMiddleware`, `APIKey` are all in `config/`. The ruff `TID251` ban on `urllib.request` enforces this at lint time. This is the strongest secure-by-default pattern in the codebase and should be highlighted in `Security.md`'s "Defense-in-depth" section.
7. **Allowlist-by-omission is a recurring pattern that's currently complete but fragile.** Three explicit allowlists: `BLOCKED_CONFIG_KEYS` (`apps.intelligence.providers`), `_PAYLOAD_TEMPLATE_KEYS` (`apps.orchestration.executors`), `EXEMPT_PATH_PREFIXES` (`config.middleware.constants`). Each one closes a real attack class, but each one must be kept in sync as the surrounding contract grows. Codify "audit this allowlist before adding new kwargs / templates / paths" rules in the relevant `agents.md` files.
8. **DNS rebinding is the residual SSRF risk** (config Sub-threshold #3). No code path lets an attacker supply the URL directly — but if `SSRF_ALLOWED_HOSTS` ever broadens, or if admin-configured DNS is compromised, the bypass becomes reachable.
9. **Single-tenant assumption is load-bearing in at least two places** — `PipelineRun.incident_id` is request-supplied (orchestration Sub-threshold), `PipelineResumeView` lacks per-actor authorization. Document the single-tenant assumption explicitly in `Security.md` so it surfaces during any future multi-tenancy work.
10. **No `pickle` / `yaml.load` / `eval` / `exec` / `compile` / raw SQL anywhere in the audited code.** Celery JSON-only across the board. The codebase passes the entire baseline of "don't write CVE-class code" sinks.

## Pending doc-update parking lot (final)

Per-module audit is complete; the following doc / `agents.md` / test follow-ups are needed before the audit pass is closed.

### 1. `docs/Security.md` edits
- "Operator tooling (`bin/`)" subsection — invariants about admin-only execution.
- "Webhook driver auto-detect & HMAC enforcement" note — clarify the fallback behavior when a driver's secret is unset.
- "Supply chain" subsection — `bin/lib/update.sh` auto-update trust assumption on `origin/main`.
- Path-traversal table row clarifying pipeline-executor stat-only sinks.
- "SSRF Protection" subsection update: record DNS-rebinding residual risk (config Sub-threshold #3); note that the generic notify driver echoes remote response bodies back to the API caller (notify Sub-threshold).
- "Authentication" subsection: explicit single-`/admin/`-path assumption for `EXEMPT_PATH_PREFIXES`; note that the 8-char API-key `prefix` is admin-displayable.
- "Rate limiting" subsection: proxy-aware-REMOTE_ADDR caveat; mutating-methods-only scope.
- "Path Traversal Protection" subsection: resolve-vs-open TOCTOU caveat; `/home` in allowlist hygiene note.
- "Production hardening" subsection (new): checklist of `SECURE_*` settings, `ALLOWED_HOSTS`, log rotation, narrowed `ALLOWED_FILESYSTEM_ROOTS`.
- "Pipeline orchestration" subsection (new): single-tenant assumption on `PipelineRun.incident_id` and `PipelineResumeView`; `trace_id` is a correlation hint, not an auth token.
- "Provider config kwargs" — under "Path Traversal Protection": "Provider config kwargs are filtered by `BLOCKED_CONFIG_KEYS` before reaching constructors. Any kwarg accepting a host, URL, path, command, or template name MUST be added to this set OR validated at the constructor."
- ISO 27001:2022 Annex A "Statement of Applicability" appendix using the mapping above.

### 2. `apps/alerts/agents.md`
- `signature_header` requirement for new drivers.
- `hmac.compare_digest` requirement.
- No `str(e)` in error responses in production.

### 3. `apps/checkers/agents.md`
- List-form argv only for subprocess.
- Validate any host/path field on the constructor.
- Class-level `scan_targets` constants are intentionally not kwargs.

### 4. `apps/intelligence/agents.md`
- Any new provider kwarg accepting a URL/host MUST call `validate_safe_url`.
- Any kwarg accepting a path MUST call `resolve_safe_path`.
- Any kwarg whose default is server-controlled MUST be added to `BLOCKED_CONFIG_KEYS` (or validated at constructor).
- `_redact_config` must remain in sync with provider config shape.

### 5. `apps/notify/agents.md` (create if absent)
- Any new outbound HTTP driver MUST use `safe_urlopen`.
- Templates MUST go through `resolve_safe_name` or be rendered in `ImmutableSandboxedEnvironment`.
- Bare-string Jinja syntax in DB-stored template *names* is rejected on purpose.
- No logger may include `channel.config` or other secret-bearing fields.
- Outbound JSON payloads MUST use `json.dumps`, never concatenation.

### 6. `apps/orchestration/agents.md` (create/extend)
- Pipeline payload fields are post-API-key but still untrusted — treat as attacker-controlled in every executor and node handler.
- Every new node type's `validate_config` MUST be implemented and called from `DefinitionBasedOrchestrator.validate()`.
- `_should_skip`'s `skip_if_condition` MUST remain a fixed-pattern matcher (no `eval`/`exec`/Jinja).
- Any new provider kwarg accepting a host/URL/path/command/template MUST be added to `apps.intelligence.providers.BLOCKED_CONFIG_KEYS` (orchestration forwards `provider_config` verbatim).
- `_PAYLOAD_TEMPLATE_KEYS` is allowlist-by-omission; any new template-bearing config key MUST be added.
- `run_id` MUST always be server-generated (`uuid.uuid4()`).
- `trace_id` is a correlation hint only; never an authorization token.

### 7. `bin/agents.md` (new file)
- Operator-tooling invariant — never consume HTTP/task-queue input.
- `confirm_and_run` is admin-input-only; add a code comment at the function.

### 8. Test plan additions
- `config/_tests/`: assert `EXEMPT_PATH_PREFIXES` cannot reach an API path through `..` segments under the configured WSGI server.
- `config/_tests/`: assert `validate_safe_url` rejects `0.0.0.0`, `100.64.0.1` (CGNAT), `fc00::1` (ULA), and `::ffff:127.0.0.1` (IPv4-mapped IPv6).
- `apps/intelligence/_tests/test_local_provider.py`: assert `get_provider("local", scan_paths=["/etc"])` either raises or silently uses `LOG_DIRECTORIES` (Finding 1 regression test).

### 9. Code fix (Finding 1, intelligence) — DONE 2026-05-13
- Option A applied: `scan_paths` added to `BLOCKED_CONFIG_KEYS` in `apps/intelligence/providers/__init__.py:79`.
- Regression tests added: `apps/intelligence/_tests/providers/test_registry.py::TestBlockedConfigKeys` (6 tests covering get_provider strip, get_active_provider strip, DB-config still honored, DB-not-overridden, direct constructor still works, host/base_url still blocked).