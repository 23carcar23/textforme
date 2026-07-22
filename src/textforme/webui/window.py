"""Opens the native window. Blocking, like tui.app.run_app()."""

from __future__ import annotations

from pathlib import Path

from .bridge import Bridge

DIST_DIR = Path(__file__).parent / "dist"
DEV_URL = "http://localhost:5173"


def run_webui(dev: bool = False) -> int:
    import webview  # deferred: pywebview import spawns Cocoa machinery

    if dev:
        target = DEV_URL
    else:
        index = DIST_DIR / "index.html"
        if not index.exists():
            print("The UI has not been built yet. From the repo, run:")
            print("  cd frontend && npm install && npm run build")
            print("(or use `textforme --dev` against `npm run dev`)")
            return 1
        target = str(index)

    bridge = Bridge()
    webview.create_window(
        "TextForMe",
        target,
        js_api=bridge,
        width=1060,
        height=700,
        min_size=(880, 560),
    )
    try:
        webview.start(debug=dev)
    finally:
        bridge.close()
    return 0
