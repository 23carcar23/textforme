# TextForMe

TextForMe is a self-hosted macOS application that automatically replies to selected iMessage and SMS contacts using Claude AI models. Your conversations stay private: all replies are generated using your own Anthropic API key, processed locally on your Mac, and never shared with third-party services. The daemon runs in the background as a LaunchAgent, responding only to contacts you explicitly allow.

## Features

- **Private AI replies**: Use your own Anthropic API key; Claude never sees your identity, just the conversation.
- **Selective automation**: Per-contact allowlist—groups are always disabled.
- **Safety by design**: No tools for Claude, deduplication, cooldowns, rate limits, auto-pause on repeated failures.
- **Background daemon**: LaunchAgent runs automatically after login; survives app closing.
- **Live TUI configuration**: Toggle contacts on/off, adjust quiet hours, rate limits, and emergency pause without restarting.
- **Messages integration**: Reads and sends via macOS Messages app (requires Full Disk Access and Messages automation permission).

## Requirements

- **macOS 14+** (Sonoma or newer)
- **Python 3.12+**
- **`imsg` CLI**: `brew install steipete/tap/imsg` — for iMessage and SMS access
- **Messages app signed in** to your Apple ID
- **Anthropic API key** (generated at [console.anthropic.com](https://console.anthropic.com); never expires unless revoked)

## Installation

### From source (development)

```bash
cd /path/to/textforme
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Via pip/uv (once published)

```bash
pip install textforme
# or
uv tool install textforme
```

## First Run and Setup

1. **Start the TUI**:
   ```bash
   textforme
   ```

2. **Onboarding checks** (automatic):
   - Verifies the macOS version (14+).
   - Checks that the `imsg` CLI is installed.
   - Checks that the Messages database is readable (Full Disk Access).
   - Messages sign-in and Automation permission are verified by a dry
     connectivity test at the end of setup (no message is sent).

3. **Masked API key entry**:
   - Prompted to paste your Anthropic API key (input is hidden).
   - Stored securely in macOS Keychain (service: `TextForMe`, account: `anthropic-api-key`).
   - Never logged, echoed, or persisted to disk.

4. **Model selection**:
   - Live query to [Anthropic Models API](https://docs.anthropic.com/en/api/models/list).
   - Select your preferred Claude model (e.g., `claude-3-5-sonnet-20241022`).

5. **Contact synchronization**:
   - Syncs your iMessage and SMS contacts from Messages.
   - All contacts default to **disabled** (no auto-reply until you enable them).

## Granting Required Permissions

### 1. Full Disk Access (for Python / Terminal)

TextForMe needs Full Disk Access to read your Messages database:

1. **System Settings** → **Privacy & Security** → **Full Disk Access**.
2. Click the **+** button and add:
   - Your **Terminal application** (if running via `terminal`), *or*
   - Your **Python executable** (if running via `uv`/`venv`), *or*
   - Your IDE (VS Code, PyCharm, etc., if running from there).
3. Restart the app after granting access.

> **Tip**: To find your Python path, run `which python` or `which python3` in the terminal.

### 2. Messages Automation (on first send)

When the daemon sends its first auto-reply, macOS displays an automation permission prompt:
- **"TextForMe" would like to access your Messages.**
- Click **Allow** to proceed. This happens once per app restart.

## Usage

### TUI (Terminal User Interface)

Run `textforme` to start the interactive dashboard:

```bash
textforme
```

The app is a single screen: a header showing the service status
("Service: Running/Stopped"), the contacts table (AI | Contact | Number),
and on the right the settings panel with the contact-note box beneath it.
The note box holds an optional description of the selected contact (e.g.
"my very strict mom so be nice to her"); press Enter to save it, and it is
added to the AI's prompt whenever it replies to that contact.

**Key commands**:
- **↑/↓**: Move within the focused table.
- **Space**: Toggle the selected contact's auto-reply on/off (saved immediately).
- **Tab**: Switch focus between the contacts table, settings panel, and note box.
- **Enter** (in Settings): Cycle a setting's value, or open the quiet-hours prompt.
- **Enter** (in the note box): Save the selected contact's description.
- **S**: Save (settings changes are also applied immediately as you make them).
- **Shift+L**: Open the daemon log popup (refreshed every 2 seconds; the log
  never contains message content, only ids, statuses, and error codes).
  Esc closes it.
- **Q**: Quit the TUI (the daemon keeps running in the background).

Group chats are shown dimmed and cannot be toggled. Pressing Enter on the
Anthropic Model row opens a picker fed live from Anthropic's Models API
(via the daemon) — switch models any time without re-running onboarding;
the change applies to the next reply. The API key row stays read-only;
replace the key by re-running onboarding.

### Configuration (Settings Tab)

| Setting | Default | Purpose |
|---------|---------|---------|
| `selected_model_id` | (empty) | Claude model to use for replies. |
| `global_ai_enabled` | `true` | Master on/off switch for all auto-replies. |
| `paused` | `false` | Emergency pause (ignores all messages). |
| `maximum_reply_length` | 300 | Hard limit on reply text length (truncated at word boundary). |
| `response_delay_seconds` | 3 | Delay before sending reply (allows user to read incoming message). |
| `context_message_limit` | 10 | Recent messages from the conversation sent to Claude. |
| `quiet_hours_start` | (empty) | ISO time `HH:MM` (local); empty = disabled. |
| `quiet_hours_end` | (empty) | ISO time `HH:MM` (local); empty = disabled. |
| `global_rate_limit_per_hour` | 20 | Max replies across all contacts per hour. |
| `contact_cooldown_seconds` | 60 | Min seconds between replies to the same contact. |
| `failure_pause_threshold` | 5 | Auto-pause after this many consecutive failures. |

### Daemon Management

The daemon runs as a **LaunchAgent** (`com.textforme.daemon`) and starts automatically after login.

**Commands**:
```bash
# Install the daemon (one-time, runs on each startup after login)
textforme install

# Check if daemon is installed
textforme status

# Manually start the daemon
textforme start

# Stop the daemon (but keep it installed)
textforme stop

# Uninstall the daemon
textforme uninstall
```

## How It Works

1. **Incoming message** → `imsg rpc watch.subscribe` notifies the daemon.
2. **Policy checks**:
   - Is it from someone? (Ignore if from me.)
   - Is the contact enabled? (Skip if not on allowlist.)
   - Quiet hours? Global/contact rate limits? Cooldowns? (Skip if any fail.)
   - Auto-pause on repeated failures? (Pause if threshold exceeded.)
3. **AI generation** → Load recent conversation; send to Claude with system prompt (Claude gets no tools, no sensitive metadata).
4. **Reply validation** → Strip control chars, truncate to `maximum_reply_length`.
5. **Send** → `imsg rpc send` to the chat.
6. **Record** → Store `replied` + timestamp in SQLite; advance watermark.

All steps are logged to `~/Library/Logs/TextForMe/daemon.*.log` (message
content is never logged — only message ids, chat ids, and statuses).

**Reliability semantics — at-most-once.** TextForMe deliberately errs on the
side of *not* replying. Every processed event advances a persisted cursor, so
after a crash, restart, or Mac sleep the daemon never re-replies to a message
it already handled. The trade-offs of this design: if the daemon crashes at
exactly the wrong moment, a message that was mid-processing may never get its
reply (the cursor had already moved past it), and in the extremely narrow
window between sending a reply and recording it, a crash could cause one
duplicate reply after restart. Both windows are milliseconds wide and were
accepted in the final security review as the correct bias for an
auto-messaging tool.

## Safety Model

- **Allowlist only**: Replies only to contacts you explicitly enable; groups are always disabled.
- **Deduplication**: Each message processed at most once (rowid watermark in database).
- **Cooldowns & rate limits**: Per-contact cooldown + global hourly limit prevent spam.
- **Auto-pause**: After `failure_pause_threshold` consecutive errors, auto-pause until you resume.
- **No Claude tools**: Claude can only generate text; it cannot execute actions, change settings, or query external APIs.
- **API key in Keychain only**: Never logged, printed, or persisted to disk.

## Troubleshooting

### "Messages database locked" error

- Close Messages app and try again.
- The daemon retries with exponential backoff.

### Daemon not starting

1. Check the daemon is installed: `textforme status`.
2. Check logs: `tail -f ~/Library/Logs/TextForMe/daemon.err.log`.
3. Verify `imsg` CLI is working: `imsg rpc chats.list --limit 1`.
4. Ensure Full Disk Access is granted to your Python executable.

### Reply not sent

1. Check contact is enabled in the TUI.
2. Check `global_ai_enabled` and `paused` in Settings.
3. Check quiet hours don't overlap with the send time.
4. Review `~/Library/Logs/TextForMe/daemon.err.log` for API errors.

### API key rejected

- Verify the key at [console.anthropic.com](https://console.anthropic.com).
- Check that billing is active and the account hasn't hit a spending limit.
- Delete the key and re-enter it: `security delete-generic-password -s TextForMe -a anthropic-api-key`.

### Permission errors

- **"Full Disk Access denied"**: Add your Python to System Settings → Privacy & Security → Full Disk Access (see above).
- **"Messages automation denied"**: Allow the permission prompt when the daemon sends its first reply.

## Uninstall

To completely remove TextForMe:

```bash
# Stop and uninstall the daemon
textforme uninstall

# Remove app data, logs, and database
rm -rf ~/Library/Application\ Support/TextForMe
rm -rf ~/Library/Logs/TextForMe

# Remove the API key from Keychain
security delete-generic-password -s TextForMe -a anthropic-api-key

# Uninstall the package
pip uninstall textforme
# or (if installed via uv)
uv tool uninstall textforme
```

## Development

### Setup

```bash
git clone <repo-url>
cd textforme
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running tests

```bash
pytest tests/unit/test_keychain.py tests/unit/test_launchagent.py -q
```

Tests use mocked `subprocess.run` to avoid touching the real Keychain or LaunchAgent.

### Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for:
- Component map and ownership.
- Database schema (SQLite).
- Unix socket protocol (TUI ↔ daemon).
- Incoming-message pipeline.
- Security boundaries.

## License

MIT
