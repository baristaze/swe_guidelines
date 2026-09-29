"""The worker's HTTP surface, one thread on the metrics port: `/metrics`
for the collector beside the process and `/healthz` for the container
probe. The probe asks the serving loop for its last beat, on the loop's
event loop, so a blocked loop fails the probe, a probe boots nothing, and
a cache outage does not fail it."""

import asyncio
import json
import logging
import threading
from collections.abc import Callable, Coroutine
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from acme.infra.observability import metrics_exposition

log = logging.getLogger(__name__)

Probe = Callable[[], Coroutine[Any, Any, bool]]
"""True while the serving worker's loop has beaten recently."""


class WorkerHttpServer:
    def __init__(
        self,
        host: str,
        port: int,
        probe: Probe,
        loop: asyncio.AbstractEventLoop,
        *,
        probe_timeout: timedelta = timedelta(seconds=5),
    ) -> None:
        self._probe = probe
        self._loop = loop
        self._timeout = probe_timeout.total_seconds()
        self._server = ThreadingHTTPServer((host, port), self._handler_class())
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="worker-http", daemon=True
        )

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _alive(self) -> bool:
        future = asyncio.run_coroutine_threadsafe(self._probe(), self._loop)
        try:
            return future.result(timeout=self._timeout)
        except Exception:
            log.warning("health probe failed", exc_info=True)
            future.cancel()
            return False

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = self.path.partition("?")[0]
                if path == "/healthz":
                    alive = server._alive()
                    body = json.dumps({"status": "ok" if alive else "missing"}).encode()
                    self._reply(200 if alive else 503, "application/json", body)
                elif path == "/metrics":
                    body, content_type = metrics_exposition()
                    self._reply(200, content_type, body)
                else:
                    self._reply(404, "text/plain", b"not found")

            def _reply(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                """Probes and scrapes are not worth a line each."""

        return Handler
