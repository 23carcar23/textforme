# TextForMe

TextForMe is a self-hosted macOS app that auto-replies to selected iMessage
and SMS contacts using Claude, via your own Anthropic API key. Everything
runs locally on your Mac: a background daemon (`textformed`) handles all the
messaging, AI, and policy logic, and you control it from a native desktop UI
(or a terminal UI).

## Features

- Per-contact allowlist — you opt in each contact individually; groups can never be enabled.
- Your own Anthropic API key, stored in macOS Keychain (never on disk or in logs).
- Fixed 300-character reply cap and a fixed 5-second per-chat cooldown — anti-loop guards, not configurable.
- Optional "realistic texting" reply timer — batches a burst behind a random 0–3 minute delay.
- "Brief me" — one click summarizes conversations the AI has handled since your last check-in.
- Prompt/persona customization — "About me," texting style, per-contact notes, or a full custom system prompt.
- Live model switching — pick any Claude model, applies to the next reply, no restart.
- Daemon logs contain only message ids/chat ids/statuses — never message content.

## Requirements

- macOS 14+ (Sonoma or newer)
- Python 3.12+
- `imsg` CLI: `brew install steipete/tap/imsg`
- Messages app signed in to your Apple ID
- An Anthropic API key ([console.anthropic.com](https://console.anthropic.com))

## Install

```bash
# install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

uv tool install textforme
textforme
```

For from-source setup (developing the Python daemon or the React frontend), see [CLAUDE.md](CLAUDE.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick setup

1. Run `textforme`. First run starts onboarding.
2. Onboarding checks your system (macOS version, `imsg` CLI, Full Disk Access).
3. Paste your Anthropic API key — it's stored in Keychain, never logged.
4. Pick a Claude model.
5. Contacts sync from Messages; all contacts start **disabled**.
6. The background service (LaunchAgent) installs and starts automatically.

### Full Disk Access

Grant Full Disk Access in **System Settings → Privacy & Security → Full Disk
Access** to two things:

1. **Your terminal app** (Terminal, iTerm, etc.).
2. **The real `imsg` binary** — `/opt/homebrew/bin/imsg` is only a wrapper
   script; granting FDA to it does nothing. The actual binary lives at
   `/opt/homebrew/Cellar/imsg/<version>/libexec/imsg`. In the file picker,
   press **⌘⇧G** and paste that path (with your installed version) to select
   it directly.

After granting access, restart the service:

```bash
textforme stop && textforme start
```

The first time the daemon sends a reply, macOS shows a one-time Messages
automation prompt ("TextForMe" would like to access your Messages) — click
**Allow**.

If the daemon still can't read Messages, the desktop UI shows a "daemon
lacks Full Disk Access" banner, and `textforme status` reports it too.

## Daemon management

```bash
textforme install    # register + start the LaunchAgent (one-time)
textforme status      # LaunchAgent + daemon-socket status
textforme start       # manually start the daemon
textforme stop        # stop the daemon (stays installed)
textforme uninstall   # remove the LaunchAgent
```

## How it works

1. `imsg` watches Messages and notifies the daemon of new messages.
2. Policy checks run in order: is the contact on the allowlist (groups never), is the global switch on and not paused, and has the 5-second per-chat cooldown elapsed.
3. If the contact's reply timer is on, incoming messages batch behind a random 0–3 minute countdown before generating a reply.
4. Claude generates a reply (no tools given to the model, output capped at 300 characters).
5. The daemon sends the reply via `imsg`.
6. The daemon records the reply in SQLite so the message is never double-replied.

This pipeline is at-most-once: on restart or crash, TextForMe is biased
toward not replying rather than replying twice.

## Troubleshooting

- **Daemon not starting** — check `textforme status`, then
  `~/Library/Logs/TextForMe/daemon.err.log`, then confirm `imsg` itself works
  with `imsg rpc chats.list --limit 1`.
- **Reply not sent** — is the contact enabled? Is the daemon paused? Is the
  reply timer still counting down?
- **"Daemon lacks Full Disk Access" banner** — grant FDA to the Cellar
  `libexec/imsg` binary and to your Python/terminal app (see above), then
  `textforme stop && textforme start`.
- **API key rejected** — verify the key at
  [console.anthropic.com](https://console.anthropic.com), then re-enter it
  from the desktop UI's Settings panel.

## Uninstall

```bash
textforme uninstall

rm -rf ~/Library/Application\ Support/TextForMe
rm -rf ~/Library/Logs/TextForMe
security delete-generic-password -s TextForMe -a anthropic-api-key

uv tool uninstall textforme
```

## License

MIT (see LICENSE)
