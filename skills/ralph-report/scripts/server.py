"""HTTP server with idle auto-exit for ralph-report.

Routes:
  GET /        → full report HTML (resets idle timer)
  GET /health  → JSON ``{"ok": true, "last_refresh": "<iso>"}`` (resets idle timer)
  Anything else → 404

The sibling scripts (``snapshot.py``, ``git_walker.py``, ``render.py``)
live in the same kebab-case directory, which is not a valid Python
package — so they cannot be relative-imported. Instead, this module
loads each sibling via :func:`importlib.util.spec_from_file_location`
at import time and caches them in :mod:`sys.modules`. The same
``_load``-by-path pattern is used by ``tests/skills/test_ralph_report.py``
and ``tests/skills/test_ralph_status.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str, file_name: str) -> ModuleType:
    """Load a sibling kebab-dir script as a top-level module."""
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = _SCRIPTS_DIR / file_name
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_snapshot = _load_sibling("ralph_report_snapshot", "snapshot.py")
_git_walker = _load_sibling("ralph_report_git_walker", "git_walker.py")
_render = _load_sibling("ralph_report_render", "render.py")


@dataclass
class ServerHandle:
    """Handle to the running HTTP server."""

    port: int
    thread: threading.Thread
    server: ThreadingHTTPServer
    _cancel_idle: Any

    def shutdown(self) -> None:
        """Idempotent: cancel idle timer, stop serve_forever.

        Socket close is handled by the ``serve()`` daemon thread's
        ``finally`` block — calling ``server_close()`` here as well would
        close the socket a second time.
        """
        cancel = self._cancel_idle
        if cancel is not None:
            cancel()
        self.server.shutdown()


def start_server(
    *,
    repo_path: Path,
    port: int = 0,
    idle_seconds: int = 1800,
    bind_host: str = "127.0.0.1",
) -> ServerHandle:
    """Start the report HTTP server in a daemon thread and return a handle.

    ``port=0`` lets the OS pick a free port; the chosen value is on
    ``handle.port``. The idle timer fires ``server.shutdown()`` after
    ``idle_seconds`` of no requests; each ``/`` or ``/health`` request
    resets the timer.
    """
    repo_path = repo_path.resolve()
    httpd = ThreadingHTTPServer((bind_host, port), _make_handler_cls(repo_path))
    actual_port = httpd.server_address[1]

    timer_state: dict[str, threading.Timer | None] = {"timer": None}
    timer_lock = threading.Lock()

    def _shutdown_for_idle() -> None:
        # Called from the Timer thread. ``shutdown()`` is safe from any
        # thread other than the one running ``serve_forever``.
        httpd.shutdown()

    def reset_timer() -> None:
        with timer_lock:
            existing = timer_state["timer"]
            if existing is not None:
                existing.cancel()
            new_timer = threading.Timer(idle_seconds, _shutdown_for_idle)
            new_timer.daemon = True
            new_timer.start()
            timer_state["timer"] = new_timer

    def cancel_timer() -> None:
        with timer_lock:
            existing = timer_state["timer"]
            if existing is not None:
                existing.cancel()
                timer_state["timer"] = None

    # Stash the reset callback on the server so the handler can find it.
    httpd._reset_idle_timer = reset_timer  # type: ignore[attr-defined]

    def serve() -> None:
        try:
            httpd.serve_forever(poll_interval=0.1)
        finally:
            httpd.server_close()

    thread = threading.Thread(target=serve, daemon=True, name="ralph-report-http")
    thread.start()
    reset_timer()
    return ServerHandle(port=actual_port, thread=thread, server=httpd, _cancel_idle=cancel_timer)


def _make_handler_cls(repo_path: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            # Silence the default per-request access log on stderr.
            return

        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            if self.path == "/":
                body = self._render_full().encode("utf-8")
                self._respond(200, "text/html; charset=utf-8", body)
                self._kick_idle_timer()
            elif self.path == "/health":
                payload = {
                    "ok": True,
                    "last_refresh": datetime.now(tz=UTC).isoformat(),
                }
                self._respond(
                    200,
                    "application/json; charset=utf-8",
                    json.dumps(payload).encode("utf-8"),
                )
                self._kick_idle_timer()
            else:
                self._respond(404, "text/plain; charset=utf-8", b"not found")

        def _render_full(self) -> str:
            snap = _snapshot.load_snapshot(repo_path=repo_path)
            events = _git_walker.walk_log(repo_path=repo_path)
            return _render.render_page(  # type: ignore[no-any-return]
                repo_path=repo_path,
                snapshot=snap,
                events=events,
                now=datetime.now(tz=UTC),
            )

        def _kick_idle_timer(self) -> None:
            reset = getattr(self.server, "_reset_idle_timer", None)
            if callable(reset):
                reset()

        def _respond(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler
