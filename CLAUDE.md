# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TextForMe is a self-hosted macOS app that auto-replies to selected iMessage/SMS
contacts using Anthropic Claude. A background daemon (`textformed`) owns all
messaging/AI/policy logic; a Textual TUI and a pywebview+React desktop UI are
thin clients that talk to the daemon over a Unix socket — neither the TUI nor
the webui ever touches imsg, Anthropic, or the raw API key directly.

## Commands

### Python environment
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Tests
```bash
pytest                                            # full suite
pytest tests/unit/test_policies.py                # one file
pytest tests/unit/test_policies.py::test_name -q  # one test
pytest tests/integration -q                       # integration only (DaemonHarness-based)
pytest tests/security -q                          # prompt-injection / secret-leak boundaries
```
Tests never touch the real Messages DB, `imsg`, Keychain, or Anthropic API —
everything goes through fakes/harnesses in `tests/conftest.py` and
`tests/fixtures/factories.py` (`FakeImsgClient`, `FakeAnthropicClient`,
`DaemonHarness` — a real `Database` + real `Daemon` over a real short-lived
`/tmp` Unix socket).

### Frontend (React desktop UI)
```bash
cd frontend && npm install
npm run build      # writes src/textforme/webui/dist/, shipped inside the wheel
npm run dev         # Vite dev server on :5173, for hot reload
```
Run the app against the dev server with `textforme --dev`. End users never
need Node — only the built `dist/` ships in the package.

### Running the app
```bash
textforme            # onboarding on first run, then the desktop UI
textforme tui         # Textual terminal UI instead of the desktop UI
textforme install     # register + start the LaunchAgent daemon
textforme status      # LaunchAgent + daemon-socket status
```

## Architecture

Full frozen contract: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Key points
an implementer needs before touching code:

```
textforme (TUI/webui)  <-- JSON-lines over Unix socket -->  textformed (daemon)
                                                                |-- ImsgClient (JSON-RPC 2.0 over stdio to `imsg rpc`)
                                                                |-- AnthropicClient (Anthropic SDK, no tools given to the model)
                                                                |-- Database (SQLite, ~/Library/Application Support/TextForMe/textforme.db)
                                                                |-- Keychain (`security` CLI, service "TextForMe")
```

- **Only the daemon's policy layer may authorize and send an outgoing
  message.** Claude receives no tools — text in, text out.
- `config.py`, `messaging/models.py`, `messaging/events.py` are the frozen
  shared contract (dataclasses, enums, exceptions, `DEFAULT_SETTINGS`,
  `Settings`) — everything else imports from here rather than redefining
  shapes.
- The incoming-message pipeline is a fixed, ordered policy chain in
  `daemon.py` (dedup → group check → paused → global toggle → contact toggle
  → fixed 5s per-chat cooldown → auto-pause → [optional reply-timer batching]
  → generate → validate → send → record). First failing check records
  `skipped:<reason>` and stops; the rowid watermark advances even on
  skip/failure so a restart never re-replies. See ARCHITECTURE.md §6 for the
  exact order before changing `daemon.py` or `service/policies.py`.
- `anthropic/prompts.py` builds the system prompt: incoming message text is
  always untrusted conversation content and must never be treated as
  instructions; owner-authored fields (`system_prompt`, `persona_prompt`,
  `style_profile`, per-contact `description`) are trusted and layered on top,
  but the built-in safety rules always take precedence over owner overrides.
- Two UI clients share one daemon-facing surface: `tui/app.py`'s
  `DaemonClient` (asyncio) is reused directly by the TUI and wrapped by
  `webui/bridge.py`'s `Bridge` for pywebview (which calls from worker threads,
  so `Bridge` runs its own event-loop thread and bridges sync↔async). Keep
  new daemon-facing features working through this one client rather than
  adding a second transport.
- The Anthropic API key lives in Keychain only (service `TextForMe`, account
  `anthropic-api-key`) — never in SQLite, logs, or crossing the Unix socket;
  the daemon re-reads it from Keychain per reply so a key change takes effect
  without restart.
- Daemon logs (`~/Library/Logs/TextForMe/daemon.*.log`) contain only message
  ids/chat ids/statuses/error codes, never message content or the API key —
  preserve this when adding logging.
