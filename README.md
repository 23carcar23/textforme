# TextForMe

TextForMe is a self-hosted macOS application that automatically replies to selected iMessage and SMS contacts using Claude AI models. Your conversations stay private: all replies are generated using your own Anthropic API key, processed locally on your Mac, and never shared with third-party services. The daemon runs in the background as a LaunchAgent, responding only to contacts you explicitly allow. Configuration happens through a native desktop window (React UI) or an equivalent terminal UI.

## Features

- **Private AI replies**: Use your own Anthropic API key; Claude never sees your identity, just the conversation.
- **Selective automation**: Per-contact allowlist—groups are always disabled.
- **Safety by design**: No tools for Claude, deduplication, a fixed reply-length cap, auto-pause on repeated failures.
- **Background daemon**: LaunchAgent runs automatically after login; survives app closing.
- **Desktop UI**: Native window with searchable contact list, live settings, editable API key, and a log viewer.
- **Terminal UI**: The same controls in a Textual TUI (`textforme tui`) — toggle contacts and settings without restarting.
- **Brief me**: One click generates a skimmable summary of the conversations the AI has handled since your last brief, so you can catch up at a glance.
- **Realistic reply timing**: An optional per-contact reply timer batches a burst of incoming texts behind a random 0–3 minute countdown and sends one reply covering the burst.
- **Prompt customization**: Describe yourself ("About me"), your texting style, and per-contact notes so replies sound like you; advanced users can replace the whole system prompt.
- **Live model switching**: Pick any Claude model from Anthropic's Models API; applies to the next reply, no restart needed.
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

Building the desktop UI requires Node (only when developing — end users never
need it, since the built assets ship inside the package):

```bash
cd frontend
npm install
npm run build   # writes src/textforme/webui/dist/, which the app serves
```

While iterating on the React UI, run Vite with hot reload and point the app at it:

```bash
npm run dev            # in frontend/, serves http://localhost:5173
textforme --dev        # opens the native window against the dev server
```

### Via pip/uv (once published)

```bash
pip install textforme
# or
uv tool install textforme
```

## First Run and Setup

1. **Start the app** (opens the native desktop window; use `textforme tui` for
   the terminal interface instead):
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
   - Select your preferred Claude model; you can change it later from either UI.

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

### Desktop UI (default)

Run `textforme` with no arguments to open the native desktop window (after
onboarding on first run):

```bash
textforme
```

The window shows a searchable **Contacts** list on the left, and on the right
the **Settings** panel and a **Contact note** box for the selected contact.
Each contact row has two toggles: **AI** (enable/disable auto-reply; group
chats cannot be enabled) and **Timer** (the realistic-texting reply timer —
when on, a burst of incoming texts is batched behind a random 0–3 minute
countdown, shown live in the row, and answered with one reply). State
refreshes from the daemon every few seconds; if the background service is not
running, a banner offers a one-click **Start service** button.

The Settings panel holds the master **AI service** switch, the **Anthropic
model** picker, the **API key** (editable — stored straight into the Keychain,
never echoed back), and **Context messages** (how many recent messages Claude
sees: 5/10/25/50/unlimited).

Header buttons:

- **Brief me** — asks Claude for a short, skimmable digest of the
  conversations the AI has handled since your last brief (or tells you there
  is nothing new). Briefs always run on Sonnet, independent of your reply
  model.
- **Prompts** — owner-authored prompt customization, stored as settings and
  applied to the next reply:
  - *About me*: who you are, so replies sound like you.
  - *My texting style*: how you write (e.g. "all lowercase, short replies").
  - *System prompt (advanced)*: replaces the built-in system prompt entirely
    (the default is shown for reference); supports `{contact_name}` and
    `{max_chars}` placeholders.
- **Logs** — the daemon log (never contains message content, only ids,
  statuses, and error codes).

### TUI (Terminal User Interface)

Run `textforme tui` to start the interactive terminal dashboard:

```bash
textforme tui
```

The app is a single screen: a header showing the service status
("Service: Running/Stopped"), the contacts table (AI | Timer | Contact |
Number), and on the right the settings panel with the contact-note box
beneath it.
The note box holds an optional description of the selected contact (e.g.
"my very strict mom so be nice to her"); press Enter to save it, and it is
added to the AI's prompt whenever it replies to that contact.

**Key commands**:
- **↑/↓**: Move within the focused table.
- **Space**: Toggle the selected contact's auto-reply on/off (saved immediately).
- **T**: Toggle the selected contact's reply timer; an active countdown is
  shown live in the Timer column.
- **Tab**: Switch focus between the contacts table, settings panel, and note box.
- **Enter** (in Settings): Cycle a setting's value, or open the model picker.
- **Enter** (in the note box): Save the selected contact's description.
- **S**: Save (settings changes are also applied immediately as you make them).
- **Shift+L**: Open the daemon log popup (refreshed every 2 seconds; the log
  never contains message content, only ids, statuses, and error codes).
  Esc closes it.
- **Q**: Quit the TUI (the daemon keeps running in the background).

Group chats are shown dimmed and cannot be toggled. The Anthropic Model row
shows the display name of the selected model and can be changed two ways,
both fed live from Anthropic's Models API via the daemon and applied to the
next reply — no onboarding re-run needed:
- **←/→** on the row cycles directly through the available models.
- **Enter** opens a picker window: move with ↑/↓, mark a model with Enter,
  then close the window (Esc) to confirm — or close without selecting to
  keep the current model.

In the TUI the API key row stays read-only; replace the key from the desktop
UI's Settings panel (or by re-running onboarding).

### Configuration (Settings)

| Setting | Default | Purpose |
|---------|---------|---------|
| `selected_model_id` | (empty) | Claude model to use for replies. |
| `global_ai_enabled` | `true` | Master on/off switch for all auto-replies. |
| `paused` | `false` | Emergency pause (ignores all messages); set automatically by auto-pause. |
| `context_message_limit` | 10 | Recent messages sent to Claude: 5/10/25/50 or `unlimited` (a 1,000-message cap). |
| `failure_pause_threshold` | 5 | Auto-pause after this many consecutive failures. |
| `system_prompt` | (empty) | Custom system prompt; empty = built-in default. Supports `{contact_name}` and `{max_chars}`. |
| `persona_prompt` | (empty) | "About me" — who the owner is, so replies sound like them. |
| `style_profile` | (empty) | How the owner texts (case, punctuation, length, phrases). |

Reply length is not a setting: every reply is capped at a fixed 300
characters so it stays text-message-sized. Free-text fields are bounded:
prompts are capped at 6,000 characters and per-contact notes at 2,000. The
prompt fields are easiest to edit from the desktop UI's **Prompts** overlay.

### Daemon Management

The daemon runs as a **LaunchAgent** (`com.textforme.daemon`) and starts automatically after login.

**Commands**:
```bash
# Install the daemon (one-time, runs on each startup after login)
textforme install

# Show LaunchAgent state and live daemon status (model, replies/hour, last error)
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
   - Is the contact enabled? (Skip if not on allowlist; groups always skip.)
   - Global switch on and not paused? (Skip otherwise.)
   - Auto-pause on repeated failures? (Pause if threshold exceeded.)
3. **Reply timer** (optional, per contact) → If the contact's timer is on, the first message of a burst starts a random 0–180 s countdown; further messages during the countdown are batched and answered with a single reply.
4. **AI generation** → Load recent conversation; send to Claude with the system prompt, plus your "About me" persona, texting-style profile, and any note you wrote about the contact (Claude gets no tools, no sensitive metadata).
5. **Reply validation** → Strip control chars, truncate to the fixed 300-character cap.
6. **Send** → `imsg rpc send` to the chat.
7. **Record** → Store `replied` + timestamp in SQLite; advance watermark.

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
- **Fixed reply cap**: Every reply is truncated to 300 characters — no setting can raise it.
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

1. Check the contact is enabled in the desktop UI or TUI.
2. Check the AI service switch is on and the daemon isn't auto-paused (`textforme status`).
3. If the contact's reply timer is on, the reply may simply be waiting out its 0–3 minute countdown (visible in either UI).
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
pytest -q
```

The suite covers unit, integration, and security tests. External effects are
mocked throughout (e.g. `subprocess.run` for Keychain and LaunchAgent), so
tests never touch your real Keychain, LaunchAgent, or Messages database.

### Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for:
- Component map and ownership.
- Database schema (SQLite).
- Unix socket protocol (TUI ↔ daemon).
- Incoming-message pipeline.
- Security boundaries.

## License

MIT
