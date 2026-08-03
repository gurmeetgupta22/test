"""HTTP bridge for the iOS serious_python runtime.

Flutter on iOS cannot call Python functions directly (serious_python runs
Python in a background thread).  This module starts a lightweight Flask server
on 127.0.0.1:8766 that exposes the same operations the Android MethodChannel
bridge provides, so the Dart MobileApi class can talk to the bot via HTTP with
identical semantics on both platforms.

Port 8766 is chosen to avoid conflict with the dashboard file-server on 8765.
"""
from __future__ import annotations

import json
import logging
import threading

from flask import Flask, jsonify, request

from . import mobile_gateway as gw

log = logging.getLogger("MaxAlphaV4")

app = Flask(__name__)

# ── helpers ──────────────────────────────────────────────────────────────────

def _ok(data=None):
    return jsonify({"ok": True, "result": data})


def _err(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


# ── endpoints ────────────────────────────────────────────────────────────────

@app.route("/ping")
def ping():
    return _ok("pong")


@app.route("/configure", methods=["POST"])
def configure():
    try:
        values = request.get_json(force=True) or {}
        result = gw.configure(values)
        return _ok(result)
    except Exception as exc:
        log.exception("configure failed")
        return _err(str(exc))


@app.route("/dashboard")
def dashboard():
    try:
        return _ok(gw.dashboard())
    except Exception as exc:
        log.exception("dashboard failed")
        return _err(str(exc))


@app.route("/logs")
def logs():
    try:
        return _ok({
            "lines": gw.logs(),
            "running": gw.is_running(),
        })
    except Exception as exc:
        log.exception("logs failed")
        return _err(str(exc))


@app.route("/signals")
def signals():
    try:
        return _ok({"content": gw.signals()})
    except Exception as exc:
        log.exception("signals failed")
        return _err(str(exc))


@app.route("/startDashboard", methods=["POST"])
def start_dashboard():
    try:
        return _ok(gw.start_dashboard())
    except Exception as exc:
        log.exception("startDashboard failed")
        return _err(str(exc))


@app.route("/startBot", methods=["POST"])
def start_bot():
    try:
        body = request.get_json(force=True) or {}
        config = body.get("configuration") or {}
        if config:
            gw.configure(config)
        budget = body.get("budget")
        gw.start_bot(float(budget) if budget is not None else None)
        return _ok(None)
    except Exception as exc:
        log.exception("startBot failed")
        return _err(str(exc))


@app.route("/stopBot", methods=["POST"])
def stop_bot():
    try:
        gw.stop_bot()
        return _ok(None)
    except Exception as exc:
        log.exception("stopBot failed")
        return _err(str(exc))


# ── server lifecycle ─────────────────────────────────────────────────────────

_server_started = False
_server_lock = threading.Lock()


def start_server(host: str = "127.0.0.1", port: int = 8766) -> None:
    """Start the Flask server in a daemon thread (idempotent)."""
    global _server_started
    with _server_lock:
        if _server_started:
            return
        _server_started = True

    log.info("MaxAlpha iOS HTTP bridge starting on %s:%d", host, port)
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
        name="max-alpha-http-bridge",
    )
    thread.start()
    log.info("MaxAlpha iOS HTTP bridge running")
