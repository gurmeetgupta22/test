"""Entry point for the serious_python iOS runtime.

serious_python calls this file (via SeriousPython.run(appFileName: "main_ios.py"))
in a background thread.  It starts the Flask HTTP bridge on 127.0.0.1:8766 and
then blocks forever so the server keeps running while the app is in the
foreground.

Flutter's MobileApi detects iOS and uses HTTP to talk to this server instead of
the Android MethodChannel / Chaquopy bridge.
"""
import sys
import os

# ── iOS-specific runtime directory ───────────────────────────────────────────
# serious_python sets the cwd to <Application Support>/data (writable).
# Expose it via the env-var that settings.py already respects.
os.environ.setdefault("MAX_ALPHA_RUNTIME_DIR", os.getcwd())

# ── start HTTP bridge ─────────────────────────────────────────────────────────
from agent.mobile_gateway_http import start_server  # noqa: E402

start_server(host="127.0.0.1", port=8766)

# ── keep the thread alive ─────────────────────────────────────────────────────
# serious_python runs this file in a background thread; when main() returns
# the thread exits and Flask shuts down.  Block indefinitely.
import threading  # noqa: E402

threading.Event().wait()
