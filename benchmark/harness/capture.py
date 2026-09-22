"""Capture what happened: the command line as JSON lines, the screen as frames.

Everything here writes to the run folder as the run happens. Nothing
waits for a subscriber, and nothing costs anything when no one watches:
`serve.py` tails the same files afterwards, or while the run is going.

`CliStream` and `FrameSink` are standard library. `CdpScreencast` talks
to a headless Chrome over its DevTools websocket and imports the
`websockets` package inside its own method.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class CliStream:
    """One JSON line per output line: `{"t": <unix>, "s": "out"|"err", "line": ...}`.

    Two reader threads write here at once, so every write holds a lock and
    flushes. A tail of the file is therefore always whole lines.

    The file is created, never reopened: a stream file that is already
    there belongs to another run, and writing into it would mix the two.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = self.path.open("x", encoding="utf-8")
        self.count = 0

    def write(self, stream: str, line: str, t: float | None = None) -> None:
        """Append one line. `stream` is `out` or `err`."""
        if stream not in ("out", "err"):
            raise ValueError(f"stream is 'out' or 'err', got {stream!r}")
        record = {"t": time.time() if t is None else t, "s": stream, "line": line.rstrip("\n")}
        with self._lock:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
            self.count += 1

    def note(self, line: str) -> None:
        """A line the harness itself writes into the stream."""
        self.write("err", line)

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()

    def __enter__(self) -> CliStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def read(path: str | Path) -> list[dict]:
        """Every whole record of a stream file; a torn last line is left out."""
        out: list[dict] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


class FrameSink:
    """A frame folder: `NNNNNN.jpg` files and an `index.jsonl` beside them.

    The index is what a viewer reads to know the order and the timing;
    the file names alone would lose the timing.
    """

    def __init__(self, folder: str | Path, ext: str = "jpg") -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.index = self.folder / "index.jsonl"
        self.ext = ext
        self._lock = threading.Lock()
        self.count = 0

    def add(self, data: bytes, t: float | None = None) -> Path:
        """Write one frame and its index entry; return the frame's path."""
        with self._lock:
            name = f"{self.count:06d}.{self.ext}"
            path = self.folder / name
            path.write_bytes(data)
            entry = {"t": time.time() if t is None else t, "frame": name}
            with self.index.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            self.count += 1
            return path

    def frames(self) -> list[dict]:
        """The index as records, in order."""
        if not self.index.exists():
            return []
        return CliStream.read(self.index)


@dataclass
class ScreencastResult:
    """What one screencast produced."""

    frames: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.frames > 0


class CdpScreencast:
    """Frames from a headless Chrome over the DevTools protocol.

    Chrome is started elsewhere with `--remote-debugging-port`. This
    connects to the page target, turns on `Page.startScreencast`, and
    writes every `Page.screencastFrame` into a `FrameSink`. Each frame is
    acknowledged, which is what makes Chrome send the next one.
    """

    def __init__(
        self, sink: FrameSink, host: str = "127.0.0.1", port: int = 9222, quality: int = 70, max_width: int = 1280
    ) -> None:
        self.sink = sink
        self.host = host
        self.port = port
        self.quality = quality
        self.max_width = max_width
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.result = ScreencastResult(frames=0, error="not started")

    def target_url(self, url: str | None = None) -> str:
        """The websocket URL of a page target, from Chrome's HTTP endpoint."""
        import urllib.request  # standard library, imported where it is used

        base = f"http://{self.host}:{self.port}"
        if url:
            with urllib.request.urlopen(f"{base}/json/new?{url}", data=b"", timeout=10) as resp:
                target = json.loads(resp.read().decode("utf-8"))
        else:
            with urllib.request.urlopen(f"{base}/json/list", timeout=10) as resp:
                targets = [t for t in json.loads(resp.read().decode("utf-8")) if t.get("type") == "page"]
            if not targets:
                raise RuntimeError(f"no page target at {base}")
            target = targets[0]
        ws = target.get("webSocketDebuggerUrl")
        if not ws:
            raise RuntimeError(f"target at {base} has no webSocketDebuggerUrl")
        return ws

    def capture(self, seconds: float = 5.0, url: str | None = None) -> ScreencastResult:
        """Capture for a while, in this thread. Returns what it got."""
        import asyncio

        try:
            ws_url = self.target_url(url)
        except Exception as exc:  # the endpoint is the usual failure, and it is worth naming
            self.result = ScreencastResult(frames=0, error=f"{type(exc).__name__}: {exc}")
            return self.result
        try:
            asyncio.run(self._pump(ws_url, seconds))
            self.result = ScreencastResult(frames=self.sink.count, error=None)
        except Exception as exc:
            self.result = ScreencastResult(frames=self.sink.count, error=f"{type(exc).__name__}: {exc}")
        return self.result

    async def _pump(self, ws_url: str, seconds: float) -> None:
        import asyncio

        import websockets  # a dependency of run.py, imported here

        deadline = time.monotonic() + seconds
        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            await ws.send(
                json.dumps(
                    {
                        "id": 2,
                        "method": "Page.startScreencast",
                        "params": {"format": "jpeg", "quality": self.quality, "maxWidth": self.max_width, "everyNthFrame": 1},
                    }
                )
            )
            next_id = 3
            while time.monotonic() < deadline and not self._stop.is_set():
                remaining = max(0.1, deadline - time.monotonic())
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                message = json.loads(raw)
                if message.get("method") != "Page.screencastFrame":
                    continue
                params = message["params"]
                self.sink.add(base64.b64decode(params["data"]))
                await ws.send(
                    json.dumps({"id": next_id, "method": "Page.screencastFrameAck", "params": {"sessionId": params["sessionId"]}})
                )
                next_id += 1
            await ws.send(json.dumps({"id": next_id, "method": "Page.stopScreencast"}))

    def start(self, seconds: float = 30.0, url: str | None = None) -> None:
        """Capture in a thread beside a subject that is running."""
        self._stop.clear()
        self._thread = threading.Thread(target=self.capture, args=(seconds, url), daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> ScreencastResult:
        """Ask the thread to finish and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return self.result
