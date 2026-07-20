# TextForMe — Architecture Specification (v1, FROZEN)

Lead-architect contract document. Implementation agents MUST conform to the
interfaces, schemas, and protocols below. Changes require architect approval.

## 1. Component map

```
textforme (TUI, Textual)  <-- JSON-lines over Unix socket -->  textformed (daemon)
                                                                  |-- ImsgClient (JSON-RPC 2.0 over stdio to `imsg rpc`)
                                                                  |-- AnthropicClient (Anthropic Python SDK)
                                                                  |-- Database (SQLite, ~/Library/Application Support/TextForMe/textforme.db)
                                                                  |-- Keychain (`security` CLI, service "TextForMe")
```

- The TUI never talks to imsg, Anthropic, or the Keychain value directly (it may
  check key *presence* during onboarding via `keychain.get_api_key()`).
- Only the daemon's policy layer may authorize and send an outgoing message.
- The Anthropic model receives NO tools. Text in, text out.

## 2. File ownership

| Files | Owner |
|---|---|
| `config.py`, `messaging/models.py`, `messaging/events.py`, this doc | Architect (frozen — do not edit) |
| `messaging/client.py`, `daemon.py`, `service/scheduler.py` | Agent 2 (messaging/daemon) |
| `cli.py`, `onboarding.py`, `tui/*` | Agent 3 (TUI/onboarding) |
| `anthropic/*`, `service/responder.py`, `service/policies.py` | Agent 4 (AI/policy) |
| `database.py` | Agent 5 (database) |
| `keychain.py`, `launchagent.py`, `resources/`, `README.md` | Agent 6 (keychain/packaging) |
| `tests/unit/test_<own module>.py` | each owning agent |
| `tests/integration/`, `tests/security/`, `tests/conftest.py`, extra unit tests | Agent 7 (testing) |

## 3. imsg RPC protocol (upstream, for reference)

`imsg rpc` speaks JSON-RPC 2.0, one JSON object per line on stdin/stdout.
Requests: `{"jsonrpc":"2.0","id":N,"method":...,"params":{...}}`.
Notifications (no `id`) arrive for watch events.

Methods used by TextForMe:
- `chats.list` — params `{limit}` → `{"chats":[Chat]}`. Chat fields: `id` (int),
  `guid`, `identifier`, `name`/`display_name`, `service`, `is_group`, `participants`.
- `messages.history` — params `{chat_id, limit}` → `{"messages":[Message]}`.
- `watch.subscribe` — params `{since_rowid?, include_reactions:true, debounce_ms}` →
  `{"subscription":N}`; then notifications
  `{"jsonrpc":"2.0","method":"message","params":{"subscription":N,"message":{...}}}`.
  Message fields: `id` (rowid), `guid`, `chat_id`, `sender`, `text`, `created_at`,
  `is_from_me`, `attachments`, and (with reactions) `is_reaction`, `reaction_type`.
- `send` — params `{chat_id, text}` → `{"ok":true,"id":...,"guid":"..."}`.
- `chats.list` with `limit: 1` doubles as the health check.

## 4. Database schema (SQLite, frozen)

```sql
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS contacts (
  chat_guid   TEXT PRIMARY KEY,
  chat_id     INTEGER NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  address     TEXT NOT NULL DEFAULT '',
  service     TEXT NOT NULL DEFAULT 'iMessage',
  is_group    INTEGER NOT NULL DEFAULT 0,
  ai_enabled  INTEGER NOT NULL DEFAULT 0,      -- never auto-enabled
  last_seen_message_guid TEXT,
  updated_at  TEXT NOT NULL                    -- ISO 8601 UTC
);

CREATE TABLE IF NOT EXISTS processed_messages (
  message_guid TEXT PRIMARY KEY,
  chat_guid    TEXT NOT NULL,
  received_at  TEXT NOT NULL,                  -- ISO 8601 UTC
  status       TEXT NOT NULL,                  -- 'replied' | 'failed' | 'skipped:<reason>'
  error_code   TEXT,
  reply_sent_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
```

Settings keys + defaults live in `config.DEFAULT_SETTINGS` (frozen). All values
stored as strings; `config.Settings` handles typing. The Anthropic API key is
NEVER stored in SQLite or logs — Keychain only.

## 5. Unix socket control protocol (TUI <-> daemon, frozen)

Socket: `config.SOCKET_PATH` (`~/Library/Application Support/TextForMe/daemon.sock`),
mode 0600. JSON lines. Request `{"id":N,"method":str,"params":{}}` →
Response `{"id":N,"ok":true,"result":{...}}` or
`{"id":N,"ok":false,"error":{"code":str,"message":str}}`.

Methods:
- `ping` → `{}`
- `status` → `{running:true, imsg_ok:bool, global_ai_enabled:bool, paused:bool,
   model_id:str, replies_last_hour:int, last_error:str|null}`
- `contacts.list` → `{contacts:[{chat_guid, chat_id, display_name, address,
   service, is_group, ai_enabled}]}` (groups included but toggling them is rejected)
- `contacts.set_ai` params `{chat_guid, enabled}` → `{}`; error code `GROUP_FORBIDDEN`
  for group chats.
- `contacts.refresh` → re-sync contacts from imsg → `{count:int}`
- `settings.get` → `{settings:{...all keys...}}`
- `models.list` → `{models:[{model_id, display_name}]}` — live Anthropic Models
  API list fetched BY THE DAEMON (the key never crosses the socket), cached
  ~10 min. Errors: `NO_API_KEY`, `ANTHROPIC_UNAVAILABLE`. Feeds the TUI's
  model picker so the model can change without re-running onboarding.
- `settings.set` params `{key, value}` → `{}`; error `UNKNOWN_KEY` if key not in
  DEFAULT_SETTINGS; `selected_model_id`, `paused`, `global_ai_enabled` take effect
  immediately.
- `service.pause` / `service.resume` → `{}` (emergency pause: sets `paused`)

Error codes: `UNKNOWN_METHOD`, `UNKNOWN_KEY`, `GROUP_FORBIDDEN`, `BAD_PARAMS`, `INTERNAL`.

## 6. Incoming-message pipeline (frozen order)

For each watch event, in order — first failure records `skipped:<reason>` (or is
silently ignored where noted) and stops:

1. `is_from_me` → ignore silently (no DB record).
2. Reaction / empty text / attachment-only → ignore silently.
3. Dedup: `message_guid` already in `processed_messages` → ignore.
4. Contact lookup by `chat_id` (sync from imsg if unknown).
5. Group chat → skip (`skipped:group`).
6. Emergency `paused` → skip (`skipped:paused`).
7. `global_ai_enabled` off → skip (`skipped:global_off`).
8. Contact `ai_enabled` off → skip (`skipped:contact_off`).
9. Quiet hours → skip (`skipped:quiet_hours`).
10. Per-contact cooldown (`contact_cooldown_seconds` since last reply to that chat) → skip (`skipped:cooldown`).
11. Global rate limit (`global_rate_limit_per_hour` replies across contacts) → skip (`skipped:rate_limit`).
12. Auto-pause: if the last `failure_pause_threshold` processing attempts all failed,
    set `paused=true` and skip.
13. Wait `response_delay_seconds`; then re-evaluate the FULL policy (steps 5–12)
    with fresh state under the daemon's rate lock, counting in-flight authorized
    replies toward the global rate limit (reserved capacity) so a concurrent
    burst can never exceed `global_rate_limit_per_hour`.
14. Load last `context_message_limit` messages via `messages.history`.
15. `AnthropicResponder.generate_reply(...)` with timeout; validate reply.
16. `send` via ImsgClient.
17. Record `replied` + `reply_sent_at`; update contact `last_seen_message_guid`;
    persist `last_seen_rowid` watermark (cursor) in settings.

Failures in 14–16 record status `failed` with `error_code` from
`messaging.events.ErrorCode`. The rowid watermark advances even for
skipped/failed messages so restarts never re-reply.

## 7. Security boundaries (frozen)

- Incoming message text is UNTRUSTED. It is passed to Claude only as
  conversation content, never as instructions; the system prompt says so.
- Claude gets no tools and cannot change settings, toggles, or send anything.
  The daemon sends at most ONE validated reply per authorized incoming message.
- Reply validation: non-empty after strip, control chars stripped, hard-truncated
  to `maximum_reply_length` chars at a word boundary where possible.
- API key: Keychain only; masked entry; never echoed, logged, or persisted elsewhere.
- Tests must never talk to real imsg or the real Anthropic API (fakes only).

## 8. Module contracts

The stub files in `src/textforme/` are the normative typed contracts — implement
exactly those signatures. Shared dataclasses/enums/exceptions live in
`config.py`, `messaging/models.py`, `messaging/events.py` (frozen).
