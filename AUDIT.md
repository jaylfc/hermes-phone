# Dialtone — Technical Audit & Improvement Plan

*Audit date: 2026-06-11, against commit f9231ff (pre-fix baseline). This report describes the codebase BEFORE the accompanying fixes: PR #84 resolves findings S1, S3, C1, C2, C3, C5, D1, O1, O2 and the crash vector behind issue #51 — read those findings as historical context, not open regressions. Every finding cites file:line; facts are labelled **[F]** (verified in code or by execution) and judgments **[J]**.*

---

## Executive Summary

**Overall health grade: C.** Dialtone works in the happy path its maintainer actually runs (Twilio + Deepgram + Hermes/local voice on macOS), the test suite is genuinely good for a project this size, and there is real, recent security hardening (signature validation, PIN lockout, opaque sessions). But the product the README advertises and the product the code implements have diverged badly: the headline "offline by default" claim is not what a fresh install does, most of the 9-STT/10-TTS provider matrix is configuration UI with no implementation behind it, and the dashboard's provider browser endpoint crashes with a `KeyError` on every request. **Top 3 risks:** (1) the `/ws/call` WebSocket on the public port is unauthenticated, letting anyone bypass the PIN gate and talk to (and bill) your LLM/TTS; (2) a fresh install silently gets a No-Op agent while health reports "ollama", so the core promise fails on first contact; (3) the realtime call loop has correctness bugs (duplicated interim transcripts fed to the LLM, a 30-second silence timeout that kills the loop) and is the least-tested code in the repo. **Top 3 opportunities:** (1) shrink the advertised provider matrix to what actually works — credibility is this project's main asset; (2) extract a `voice/` provider layer mirroring the clean `agents/` pattern so the matrix can become real incrementally; (3) add a lint gate and a fresh-install integration test to CI so the docs/code drift can't recur unnoticed.

---

## Repo Map

**Purpose.** A self-hosted VoIP/IVR bridge: Twilio phone number ↔ STT ↔ pluggable AI agent ↔ TTS, plus voicemail with transcription, a web dashboard, and a macOS menu-bar app. Built primarily as a phone front-end for Hermes Agent; positioned as framework-agnostic.

**Stack.** Python 3.11+, Flask + flask-sock (WebSocket), Twilio SDK, Deepgram SDK v7, OpenAI SDK; vanilla-JS single-file HTML frontends; bash installers (macOS LaunchAgent, Linux systemd); pytest + GitHub Actions CI; no database (JSON file storage).

**Maturity.** Early-stage, single-maintainer open-source hobby/side project (~8 kLOC, MIT + Commons Clause), but it handles real phone calls and real credentials, so the security bar is production-adjacent.

**Architecture.** Two Flask apps in one process: a public webhook server on port 5050 (Twilio TwiML webhooks + `/ws/call` media stream) and a token-protected dashboard/API on port 5051. Control flow: `POST /voice/incoming` → greeting + hidden PIN `Gather` → either `<Connect><Stream>` to `/ws/call` (AI conversation: Twilio audio → Deepgram live STT → `agents.get_agent_backend().chat()` → TTS → μ-law frames back) or `<Record>` voicemail (→ download, transcribe, Telegram notify).

**Key files.**

| Path | Role |
|---|---|
| `server.py` (1,326 lines) | Everything server-side: config, both Flask apps, auth, call flow, WS loop, TTS, voicemail, settings API |
| `agents/` | Clean pluggable agent layer: `base.py` ABC, factory in `__init__.py`, Hermes / OpenAI-compat / NoOp backends |
| `provider_registry.py` | STT/TTS/agent provider metadata: pip deps, install commands, model lists |
| `local_voice.py` | Apple-Silicon local STT/TTS (mlx-whisper, Kokoro) with edge-tts fallback |
| `dashboard.html`, `settings.html` (3,200 lines) | Self-contained dashboard + settings SPA |
| `menubar.py`, `native_settings.py` | macOS menu-bar app (rumps/AppKit) |
| `install.sh`, `install-linux.sh`, `uninstall*.sh` | Interactive installers (LaunchAgent / systemd) |
| `setup.sh`, `run.sh` | **Stale** legacy dev scripts predating the current config scheme |
| `tests/` (6 files) | Behavioural tests for webhook security, dashboard auth, settings |
| `prototype/pipecat/` | Explicitly non-production reliability spike |

**Surprises found during mapping.** Two different env templates in two formats (`.env.example` `KEY=V` vs `env.template` `export KEY=V`); installers still branded "Hermes Phone" with *different* install dirs (`~/.hermes-phone` on macOS at `install.sh:16` vs `~/.hermes/phone-agent` on Linux at `install-linux.sh:16`) while the README claims one path (`README.md:210`); a root-level `pre-commit` secrets scanner that nothing installs into `.git/hooks`.

**Review depth.** Deep: `server.py`, `agents/`, `provider_registry.py`, `local_voice.py`, tests, CI, installers, `dashboard.html` rendering paths. Lighter: `menubar.py`/`native_settings.py` (macOS-only UI), `settings.html` internals, `prototype/`.

---

## Audit Report

### Security

**S1 — CRITICAL: `/ws/call` media WebSocket is unauthenticated on the public port.** [F] The webhook `before_request` guard only validates `/voice/*` paths (`server.py:198-218`); the comment at `server.py:204-205` explicitly states the WS is unguarded. The handler (`server.py:759-825`) accepts any connection, opens a Deepgram live session, and on any transcribed speech calls the LLM and TTS. Anyone who can reach port 5050 — which is internet-exposed by design (`README.md:58`) — can speak to your agent, consume Deepgram/LLM/TTS quota, and bypass the PIN gate entirely. The PIN, lockout, and constant-time compare (`server.py:642-665`) guard a door next to an open window. *Fix direction: on the `start` event, verify `callSid` against a server-issued allowlist (e.g., a one-time token embedded in the `<Stream>` URL generated at `server.py:653-654`), and close unknown connections.*

**S2 — MEDIUM: production runs on Flask/werkzeug dev servers, plaintext HTTP, bound to 0.0.0.0.** [F] `server.py:1302-1325` runs `webhook_app.run(host="0.0.0.0")` and werkzeug `run_simple` for the dashboard; installers expose these directly. The session cookie's `Secure` flag is conditional on `request.is_secure` (`server.py:866-867`), so over the documented LAN/port-forward setups the dashboard token and cookie travel in cleartext. Acceptable for a VPN'd hobbyist, but undocumented as a hard requirement.

**S3 — MEDIUM: stored XSS possible via voicemail `sid` in an inline `onclick`.** [F → J] `dashboard.html:1595` interpolates `vm.sid` into `onclick="deleteVm('…')"` through `escHtml()`, which escapes `<>&` but **not quotes** (`dashboard.html:1440-1444`), so a single quote in `sid` breaks out of the JS string. `sid` comes from the Twilio webhook form (`server.py:671`); with `VALIDATE_TWILIO_SIGNATURE=false` (the documented local-dev mode) anyone can POST a malicious `RecordingSid` and get JS execution in the authenticated dashboard. Low likelihood with validation on (default), but the fix (use `data-sid` + `addEventListener`) is trivial. Everything else in the dashboard is escaped consistently — good.

**S4 — LOW: dashboard token accepted as a `?token=` query parameter** (`server.py:324-327`) — leaks into logs, browser history, and referrers. It exists for the menu-bar bootstrap and immediately swaps to a session cookie (`server.py:1223-1229`), which limits exposure.

**S5 — LOW: unauthenticated `/health` on the public port leaks configuration** (`server.py:831-853`): configured providers, ports, voicemail count, agent model. Documented behaviour (`README.md:308`), but it's free reconnaissance.

Positive security facts worth stating: no secrets in the working tree **or git history** [F, scanned]; `.env` chmod 600 (`install.sh:276`); settings API validates keys against a schema and strips CR/LF to prevent `.env` injection — **with a test** (`server.py:1068-1088`, `tests/test_dashboard_auth.py` `TestEnvInjection`); sensitive values masked in API responses (`server.py:1090-1099`); PIN compare is constant-time with lockout (`server.py:649`, `231-249`).

### Correctness (the ugly parts — utmost priority)

**C1 — HIGH: a fresh install gets a No-Op agent while claiming "offline by default with Ollama".** [F] `server.py:83` defaults `AGENT_PROVIDER` to `"ollama"`, but that constant is only used for *display*. The actual factory reads `os.environ.get("AGENT_PROVIDER", "")` (`agents/__init__.py:35`) — empty default → auto-detect → no Hermes URL in env → `OpenAICompatAgent()` whose own defaults point at **Xiaomi's cloud endpoint** (`agents/openai_compat.py:42-44`) → no API key → `NoOpAgent` (`agents/__init__.py:98-100`). Neither installer writes `AGENT_PROVIDER` to `.env` [F: grep of `install.sh`/`install-linux.sh`], and `install-linux.sh:215` seeds `LLM_PROVIDER=openai`. Net effect: out of the box, callers hear "No agent backend configured" while `/health` reports `agent_backend: "ollama"` (`server.py:844`). The offline-defaults test (`tests/test_offline_defaults.py`) asserts the cosmetic constant, not the selected backend, so CI is green while the feature is broken.

**C2 — HIGH: `GET /api/providers` returns 500 on every request.** [F — verified by executing `get_provider_status()`: `KeyError: 'backend'`.] The agent entries in `PROVIDER_DEPS` (`provider_registry.py:323-356`) have no `"backend"` key, and `get_provider_status()` does `info["backend"]` (`provider_registry.py:388`). The dashboard's provider-discovery feature (`server.py:1249-1252`, advertised at `README.md:154`) has been broken since agent entries were added. No test covers this endpoint.

**C3 — HIGH: interim STT results are appended to the transcript buffer, so the LLM receives duplicated/stuttered user text.** [F on code, J on impact] Deepgram is opened with `interim_results=True` (`server.py:772`), and `on_message` appends **every** non-empty transcript — interim and final alike — to `transcript_buf`, joining them all when a final arrives (`server.py:783-791`, `809-812`). A user saying "book a table" plausibly reaches the LLM as "book book a book a table". This degrades every AI conversation, the product's core interaction.

**C4 — HIGH: the port-forwarding install path produces a broken AI call path.** [F] `install.sh:292` configures Twilio with `http://$STATIC_IP:5050/voice/incoming`, but `_ws_url()` always emits `wss://` (`server.py:221-227`, correctly — Twilio Media Streams require TLS). A wss handshake against a plain-HTTP port fails, so for installer option 1 (no TLS terminator) the PIN succeeds and then the stream silently dies. Voicemail works; the AI never answers.

**C5 — MEDIUM: 30 seconds of caller silence kills the conversation loop.** [F] `ws.receive(timeout=30)` returns `None` on timeout in flask-sock, and `None` breaks the loop (`server.py:795-797`). Twilio keeps streaming keepalive media frames during silence, which masks this in practice [J], but any 30s gap in frames (hold, network blip) permanently ends AI handling while the call stays up.

**C6 — MEDIUM: settings UI promises live edits the server can't deliver.** [F] `update_setting` writes `.env` and `os.environ` (`server.py:1086-1088`), but ~40 settings were snapshotted into module constants at import (`server.py:66-177`) and the agent backend is a never-invalidated singleton (`agents/__init__.py:26-33`). Only the handful of values re-read via `env()` per-request apply live. Concretely: changing `DASHBOARD_TOKEN` in the UI does nothing until restart — `check_auth` compares against the import-time constant (`server.py:109`, `255-258`) — yet its hint claims "Changing this signs out existing dashboard sessions" (`server.py:1053`), which is false on both counts. Same for `VOICEMAIL_PIN`, `AGENT_PROVIDER`, `TTS_PROVIDER`, etc., none of which carry the "Restart required" hint that ports do.

**C7 — LOW: `.env` quote round-trip corruption.** [F] `update_setting` escapes quotes as `\"` (`server.py:1072`) but `load_env`/`get_all_settings` only strip outer quotes (`server.py:57`, `1065`), so a value containing `"` gains a literal backslash on every save/load cycle.

### Architecture & design

**A1 — HIGH (judgment, high confidence): the provider matrix is three inconsistent sources of truth, most of it unimplemented.** [F on each instance] `STT_PROVIDER` is read (`server.py:150`) and **never used to select an engine** — the live call path is hardwired to Deepgram (`server.py:766-776`) and voicemail transcription to local-voice-then-Deepgram (`server.py:712-719`). TTS implements exactly: local engine, MiMo, and an OpenAI fallback (`server.py:486-537`); picking ElevenLabs, Cartesia, Azure, Edge, Piper, etc. in settings yields **silent calls**. Meanwhile `server.py:936-978` lists 14 STT / 20 TTS options (including vosk, wav2vec2, canary, tortoise, styletts2 — implemented nowhere), `provider_registry.py` lists an overlapping-but-mismatched set (UI id `whisper` vs registry `whisper-api`; UI TTS id `openai` collides with the registry's *agent* entry `openai`, so the install button installs the wrong thing), and the README advertises "9 STT providers, 10+ TTS providers" as shipped (`README.md:94-122`, `415`). This is the repo's biggest credibility and maintenance liability.

**A2 — MEDIUM: `server.py` is a god module.** [J] Config, two Flask apps, auth, telephony, the realtime loop, TTS dispatch, voicemail storage, a settings framework, and embedded login HTML in one 1,326-line file. It's still navigable, but every fix above lands in the same file, and the realtime loop is untestable as written (no seams; module-level state). The `agents/` package proves the team knows the right pattern — voice providers and the webhook/dashboard split should follow it.

**A3 — MEDIUM: blocking pipeline inside the WS receive loop.** [F] `backend.chat()` (timeout 30s, `agents/hermes_gateway.py:64`) and `synthesize_speech()` run inline in the receive loop (`server.py:815-819`); no media is read while they run and there is no barge-in. Acceptable latency floor for v0 [J], and the Pipecat prototype shows this is a known limitation — but it bounds conversation quality.

**A4 — LOW: unbounded in-memory state.** [F] `call_states` entries (with full transcripts) are only removed by `/voice/status` (`server.py:749-757`), which inbound TwiML never registers (`server.py:620-640`) — cleanup depends on the user having configured a status callback on the Twilio number. `pin_attempts` and `dashboard_sessions` also only shrink opportunistically. Slow leak at hobby scale.

### Code quality

**Q1 — MEDIUM:** dead/stale legacy scripts that actively mislead: `setup.sh` writes an obsolete `.env` (e.g. `TTS_PROVIDER=deepgram`, which is not a TTS option anywhere) and `run.sh` references the old single-port layout. [F] A newcomer following `setup.sh` gets a broken config.
**Q2 — LOW:** error handling is consistently "catch-all + print": ~30 broad `except Exception` blocks and a bare `except:` at `server.py:611`; failures (TTS down, Telegram errors, voicemail download failures at `server.py:707-708` which `return` silently) never surface to the dashboard. Consequence: silent calls with no operator-visible cause.
**Q3 — LOW:** duplicated `.env` parsing in three places (`server.py:49-58`, `server.py:1056-1066`, `menubar.py:42-52`); duplicated history-trimming in both `server.py:474-475` and `agents/openai_compat.py:120`.

### Testing

**T1 — HIGH: the core call path has zero tests.** [F] The six test files cover auth, signatures, settings, and helpers well, but nothing exercises `/voice/incoming` TwiML, `/voice/check-pin` (the route — lockout helpers are tested, the route isn't), the WS loop, `synthesize_speech`, `get_llm_response`, voicemail processing, or any `/api/providers` endpoint. That is exactly where C2/C3/C5 live undetected.
**T2 — MEDIUM:** `tests/test_offline_defaults.py` asserts display constants, not behaviour (see C1) — a test that passes while the feature it names is broken.
Strength: existing tests assert behaviour, not execution, and `test_all_settings_editable.py` is a genuinely clever invariant test (every env var read must be schema-editable).

### Performance

Healthy for its scale. The notable items are architectural (A3 blocking loop) and minor: `synthesize_speech` builds a new OpenAI client per utterance (`server.py:495-496`); voicemail JSON is re-read per request (`server.py:370-376`) — fine at hundreds of voicemails. No N+1 or pathological allocation patterns found.

### Dependencies

**D1 — MEDIUM:** `requirements.txt` has **no platform markers** despite `README.md:370` claiming "Dependencies with platform markers" — `rumps`/`pywebview` are listed unconditionally, so `pip install -r requirements.txt` fails on Linux [F; the CI workflow comment admits this and hand-picks deps instead]. Consequently CI installs a hand-maintained, unpinned dep list (`.github/workflows/ci.yml:28-31`) that will drift from `requirements.txt`. No pins/lockfile anywhere (all `>=` ranges).
Otherwise the dependency set is small and mainstream. No SCA tool was run as part of this audit, but **GitHub reports 1 open high-severity Dependabot alert on the default branch** (https://github.com/jaylfc/dialtone/security/dependabot/1) — triage it as part of Milestone 1.

### DevEx & operations

**O1 — MEDIUM:** no linting or formatting anywhere in CI (CodeRabbit's ruff applies only to PR review comments, `.coderabbit.yaml`). **O2 — LOW:** the root `pre-commit` secrets hook is wired into nothing — neither installer nor docs install it into `.git/hooks` [F]. **O3 — LOW:** logging is `print()` with emoji throughout; LaunchAgent/systemd capture it to a single unrotated `server.log` (`install.sh:368-371`). **O4 — LOW:** dual-brand split — installers, service names, paths, and `local_voice.py:2` still say "Hermes Phone" post-rename (PR #65 renamed UI only).

### Documentation

**Doc1 — HIGH:** the README oversells in load-bearing ways: provider matrix (A1), "offline by default" (C1), "Dependencies with platform markers" (D1), `.env.example` ships cloud-first defaults (`STT_PROVIDER=deepgram`, `TTS_PROVIDER=polly`, `LLM_PROVIDER=xiaomi`) contradicting the offline story, and two env templates in incompatible formats (`env.template` uses `export KEY=` syntax that `load_env()` cannot parse — `server.py:53-58` would read the key as `export AGENT_PROVIDER`). For an open-source project soliciting testers (`README.md:430`), each tester who hits C1/C2 on day one is lost.
Strengths: the README is otherwise well-structured, the security section accurately describes the *implemented* auth mechanisms, and the roadmap honestly separates shipped from planned.

### Strengths (what to preserve)

1. **Real, recent security engineering with tests**: Twilio signature validation, constant-time PIN + lockout, opaque revocable sessions, `.env`-injection sanitisation, secret masking, clean git history, secrets pre-commit scanner. The trajectory (PRs #52, #53, #57, #61) shows security is taken seriously.
2. **The `agents/` package is the architectural template the rest should follow** — small ABC, lazy factory, graceful NoOp fallback.
3. **Test culture punches above project size** — behavioural assertions, fixtures that isolate env mutation, an invariant test.
4. **CI on 3.11 + 3.12 from day one**, CodeRabbit auto-review wired up.
5. **Honest prototype hygiene** — the Pipecat spike is clearly fenced in `prototype/`.

---

## Improvement Strategy

### Theme 1 — Close the gap between the advertised product and the implemented one

Most findings (C1, C2, A1, D1, Doc1, T2) are one disease: **surface area grew by configuration and documentation, not implementation, and nothing forces the three layers (README / settings schema / code) to agree.** Target state: a single provider registry is the only source of truth; the settings UI and README render *from* it (or are asserted against it by a test, extending the existing `test_all_settings_editable.py` pattern); every selectable provider either works or is labelled "planned". Principle: *a small true matrix beats a large aspirational one.* Done when: README matrix == implemented set, an integration test asserts the backend actually selected on a default install, and `/api/providers` has a test.

### Theme 2 — Harden and test the realtime call path (the core 20%)

S1, C3, C4, C5, A3, T1 all live in ~200 lines of `server.py`. Target state: the WS loop is extracted into a testable unit with injected STT/agent/TTS, authenticated via per-call stream tokens, with correct final-transcript assembly and an explicit silence policy. Principle: *the code that talks to paying phone lines deserves the most tests, not the fewest.* Done when: WS connections without a valid call token are closed; a unit test feeds fake Deepgram interim+final events and asserts the exact LLM input; CI covers `/voice/*` TwiML routes.

### Theme 3 — One configuration system with honest semantics

C1, C6, C7, Q3: there are four config readers (import-time constants, live `env()`, the settings file parser, menubar's copy) and two defaults for the same key. Target state: one accessor module; every setting declared once with `live` vs `restart` semantics that the UI hints render from; the backend singleton invalidates when relevant keys change. Done when: the defaults in `server.py` and `agents/` come from one place and a test asserts UI hints match actual reload behaviour.

### Theme 4 — Modest CI/ops floor

O1, O2, D1: ruff in CI, `requirements.txt` with platform markers as the single dep source for CI, pre-commit hook auto-installed. Done when: CI fails on lint errors and installs from `requirements.txt`.

### Explicitly NOT recommended (trade-offs)
- **Don't build the 20-provider matrix.** Cut it to the ~5 that work; add others only when someone implements *and tests* them. Effort/payoff is wildly against breadth right now.
- **Don't adopt Pipecat/LiveKit yet** (A3). The prototype exists; swapping the pipeline before the current one has tests would trade known bugs for unknown ones.
- **Don't add a database.** JSON + lock is fine at voicemail scale; revisit at >5k voicemails.
- **Don't build an observability stack.** Structured stdlib logging with levels is enough; the platform supervisors already capture output.
- **Don't rewrite the HTML frontends or split the repo.** They're cohesive and mostly correct.
- **Defer TLS termination automation** (S2): document "use a tunnel/reverse proxy with TLS" as a requirement instead; that's the norm for self-hosted tools at this maturity.

---

## Task Plan

### Milestone 0 — Safety net (before touching the call path)

| # | Task | Files | Acceptance criteria | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 0.1 | **Tests for `/voice/*` TwiML routes**: incoming greeting/PIN gather, check-pin success→`<Connect><Stream>`, failure→`<Record>`, lockout path, voicemail-complete metadata write | `tests/test_call_flow.py` (new) | TwiML asserted by content for each branch; runs in CI | M | None (test-only) | — |
| 0.2 | **Extract WS loop into a testable function** with injected STT events, agent, TTS (pure refactor, no behaviour change) + characterisation test reproducing C3 | `server.py:759-825` → new `call_session.py` | Existing manual behaviour preserved; failing test documents the interim-duplication bug | M | Medium (touches live path) | 0.1 |
| 0.3 | **CI installs from `requirements.txt`** (with platform markers added) + ruff lint gate | `requirements.txt`, `.github/workflows/ci.yml`, `pyproject.toml` (ruff config) | CI green; `pip install -r requirements.txt` succeeds on ubuntu runner; CI fails on lint error | S | Low | — |

### Milestone 1 — Critical & high fixes (security + correctness)

| # | Task | Files | Acceptance criteria | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 1.1 | **Authenticate `/ws/call`** (S1): embed a one-time per-call token in the `<Stream>` URL at TwiML generation; validate on `start`; close otherwise | `server.py:642-665`, `221-227`, `call_session.py` | Unauthenticated WS test gets closed before any STT/LLM call; legitimate-call test passes | M | Medium | 0.2 |
| 1.2 | **Fix default-backend split brain** (C1): one default (`ollama`) defined once; installers write `AGENT_PROVIDER`; integration test asserts `type(get_agent_backend())` on a clean env | `agents/__init__.py:35`, `server.py:83`, `install*.sh`, `tests/test_offline_defaults.py` | Fresh env (no `.env`) selects the documented default; health reports the *actual* backend | S | Low | — |
| 1.3 | **Fix interim-transcript duplication** (C3): buffer only `is_final` segments; use `speech_final`/endpointing for turn end | `call_session.py` (was `server.py:783-791`) | Test from 0.2 flips to green: LLM input == concatenated finals only | S | Medium | 0.2 |
| 1.4 | **Fix `/api/providers` 500** (C2): make `backend` key required-by-construction (or `.get` with default) + endpoint test | `provider_registry.py:380-394`, `tests/` | `GET /api/providers` returns 200 with all entries; test in CI | S | Low | — |
| 1.5 | **Fix silence timeout** (C5): treat receive-timeout as keepalive continue, bounded by Twilio's own stream close | `call_session.py` | Test: 31s without frames does not terminate the session | S | Low | 0.2 |
| 1.6 | **Fix port-forward install path** (C4): installer requires an https/wss-capable URL (or auto-offers the tunnel option) and writes `WEBHOOK_URL_OVERRIDE`; warn loudly when `_ws_url()` would target a non-TLS host | `install.sh:279-337`, `server.py:221-227` | Installer cannot produce a config where TwiML points at `http://` while streams need `wss://` | S | Low | — |
| 1.7 | **Fix `onclick` sid injection** (S3): replace inline handler with `data-sid` + delegated listener; extend `escHtml` to quotes | `dashboard.html:1440-1444`, `1595` | sid containing `'<">` renders inert; manual check + JS-free assertion on generated markup | S | Low | — |

### Milestone 2 — High-leverage improvements

| # | Task | Files | Acceptance criteria | Effort | Risk | Deps |
|---|---|---|---|---|---|---|
| 2.1 | **Single provider source of truth + honest matrix** (A1): merge `STT_PROVIDERS`/`TTS_PROVIDERS` (`server.py:936-989`) into `provider_registry.py` with a `status: implemented\|planned` field; settings UI renders from it; remove or grey out unimplemented options; rewrite README tables to the implemented set | `provider_registry.py`, `server.py`, `settings.html`, `README.md` | One registry; invariant test asserts UI ids ⊆ registry ids; README matrix lists only `implemented` | L | Medium | 1.4 |
| 2.2 | **Extract `voice/` provider layer** mirroring `agents/`: `VoiceProvider` ABC, Deepgram/local/OpenAI-TTS/MiMo as first implementations; `synthesize_speech` and STT selection dispatch via `STT_PROVIDER`/`TTS_PROVIDER` | new `voice/` pkg, `server.py:486-578`, `local_voice.py` | Selecting an unimplemented provider yields a logged error + documented fallback, never a silent call; unit tests per provider with mocked HTTP | L | Medium | 2.1 |
| 2.3 | **Unify config system** (C6/C7/Q3): one `config.py` accessor; declare `live` vs `restart` per key; backend singleton invalidation on relevant change; fix quote round-trip; correct/false UI hints (`server.py:1053`) | `server.py:47-183, 1056-1099`, `agents/__init__.py`, `menubar.py` | Test: changing `DASHBOARD_TOKEN` via API takes effect per its documented semantics; round-trip property test on `update_setting`→`load_env` | L | Medium | 1.2 |
| 2.4 | **Split `server.py`** along existing section comments: `webhooks.py`, `dashboard_api.py`, `voicemail.py`, `call_session.py`, `config.py` (mechanical move, no logic change) | `server.py` | All tests pass; no module >500 lines; imports acyclic | M | Medium | 2.2, 2.3 |

### Milestone 3 — Quality & polish

| # | Task | Effort | Notes |
|---|---|---|---|
| 3.1 | Delete or rewrite stale `setup.sh`/`run.sh`; delete `env.template` or convert to the parseable format (Q1, Doc1) | S | Quick win |
| 3.2 | Finish the rename: installers, service labels, `local_voice.py` header; pick ONE install dir and document a migration note (O4) | M | Breaking for existing installs — needs a migration shim |
| 3.3 | `logging` module with levels instead of `print`; surface last-error in `/health` and dashboard (Q2, O3) | M | |
| 3.4 | Bound in-memory state: TTL-prune `call_states`/`pin_attempts`; register status callback on inbound TwiML (A4) | S | |
| 3.5 | Auto-install the `pre-commit` hook via a `make setup` / docs note (O2) | S | Quick win |
| 3.6 | Document TLS expectations and dev-server caveat; recommend tunnel/reverse-proxy (S2) | S | Quick win |
| 3.7 | Stop accepting `?token=` for anything but the menubar bootstrap path, or move to a short-lived single-use bootstrap token (S4) | S | |

### Quick wins (do immediately, all S effort, high impact)

**1.4** (`/api/providers` KeyError — one line + test), **1.2** (default-backend alignment), **1.3** (interim transcript fix), **0.3** (CI from requirements.txt + ruff), **3.1** (delete stale scripts), **1.7** (onclick fix).

### Implementation sketches — top 3 tasks

**1.1 WS authentication.** In `check_pin`/`handle_outgoing`, generate `token = secrets.token_urlsafe(16)`, store `stream_tokens[token] = call_sid` (TTL ~120s), and build the stream URL as `wss://host/ws/call?t={token}` (Twilio passes query params through; alternatively use `<Stream><Parameter>` which arrives in the `start.customParameters` payload — prefer this, it avoids URL logging). In the WS handler, on the `start` event verify the parameter maps to the expected `callSid`; otherwise `ws.close()` before initialising Deepgram. Gotchas: `_ws_url()` has two call sites plus the override path; outbound calls create state before TwiML fetch, so mint the token at `/voice/outgoing` time, not at `POST /call` time; keep the token single-use.

**1.2 Default-backend alignment.** Make `agents/__init__.py:35` read `os.environ.get("AGENT_PROVIDER", "ollama")` — but the real fix is one shared constant (e.g., `DEFAULT_AGENT_PROVIDER = "ollama"` in `agents/`, imported by `server.py`). Update both installers to write `AGENT_PROVIDER=` explicitly from the wizard choice (they currently write only `LLM_PROVIDER`, which the factory ignores for routing — note the wizard's "OpenAI" choice currently produces a config the factory routes to *ollama-or-auto*; map wizard choices to `AGENT_PROVIDER` values directly). Replace `tests/test_offline_defaults.py` assertions with `assert isinstance(get_agent_backend(), OpenAICompatAgent)` + base_url check under a scrubbed env (reset the `_backend` singleton in the fixture — it caches across tests). Gotcha: auto-detect (`""`) is still a documented value (`README.md:80`); keep it but make `auto` the explicit spelling and `""`→default-not-auto, or the reverse — decide once, document in the schema hint.

**1.3 Interim transcript fix.** In `on_message`, append `text` only when `getattr(msg, "is_final", False)`; set the turn boundary from `msg.speech_final` (Deepgram's utterance-end signal) rather than `is_final` (which fires per segment). Keep interim results enabled only if you later want barge-in; otherwise set `interim_results=False` and simplify. Write the test first against the extracted loop (task 0.2): feed events `[interim "book", interim "book a", final "book a table" (speech_final)]` and assert the agent receives exactly `"book a table"`. Gotcha: `transcript_buf`/`speech_final` are closure variables shared with the receive loop across threads (Deepgram callbacks fire on its own thread) — guard with a lock or a queue while you're in there.

---

## Open Questions (need a human decision)

1. **Product intent for the provider matrix**: is breadth (many providers) actually a goal worth implementation investment, or was it speculative? Recommendation: cut to Deepgram + local + OpenAI-TTS + MiMo + Edge and mark the rest "planned", but that's a positioning call.
2. **Who is the primary non-Hermes user?** The README solicits OpenAI/Ollama testers; fixing C1 changes what those testers get by default. Confirm `ollama` (offline) vs `auto` (Hermes-first) as the shipped default.
3. **Deprecation candidates**: may `setup.sh`, `run.sh`, and `env.template` be deleted outright, or do existing users depend on them?
4. **Install-dir unification** (3.2) breaks existing installs — is a migration shim required, or is the user base small enough to document a manual move?
5. **Performance target for the call loop**: is current turn latency acceptable, or is sub-second response a goal? That decides whether the Pipecat migration (long-term roadmap item) gets pulled forward — this audit recommends *after* Milestone 1, not before.
6. **TLS stance**: require a tunnel/reverse proxy (document-only fix) or invest in built-in TLS/caddy automation?
