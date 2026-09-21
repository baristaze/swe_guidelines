#!/usr/bin/env python3
"""Serve a runs folder: the reports, the command line as it happens, the frames.

    uv run benchmark/serve.py --runs benchmark/runs --port 8765

Standard library only, so it starts with no install and no network of
its own. It reads; it never writes and never starts a run. The files it
serves are written by the run whether anyone watches or not, so watching
costs nothing and nothing is lost by watching late.

Routes:

- `GET /` an index page listing the runs
- `GET /runs` the runs as JSON
- `GET /runs/<id>/report.md`, `/results.json`, `/run.json` the files
- `GET /runs/<id>/streams/cli` the command line as `text/event-stream`
- `GET /runs/<id>/streams/browser.mjpeg` the frame folder as MJPEG
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

FILES = {"report.md": "text/markdown; charset=utf-8", "results.json": "application/json", "run.json": "application/json"}
POLL_S = 0.5
BOUNDARY = "benchmarkframe"


def runs_of(folder: Path) -> list[dict]:
    """Every run folder, newest name first, with what it holds."""
    out = []
    for path in sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True) if folder.is_dir() else []:
        out.append(
            {
                "id": path.name,
                "report": (path / "report.md").exists(),
                "results": (path / "results.json").exists(),
                "streams": sorted(p.name for p in (path / "streams").iterdir()) if (path / "streams").is_dir() else [],
            }
        )
    return out


def tail(path: Path, stop_after_s: float | None = None, finished: Path | None = None):
    """Yield whole lines of a file as they arrive, waiting for the file to appear.

    The file is read as bytes and a line is decoded only once it is whole,
    so a character the writer has half written never breaks the read. The
    tail ends when `finished` exists and the file has stopped growing: the
    run has written its results, so nothing more is coming.
    """
    started = time.monotonic()
    position = 0
    pending = b""
    while True:
        done = finished is not None and finished.exists()
        chunk = b""
        if path.exists():
            with path.open("rb") as fh:
                fh.seek(position)
                chunk = fh.read()
                position = fh.tell()
            pending += chunk
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace")
                if line.strip():
                    yield line
        if done and not chunk:
            last = pending.decode("utf-8", errors="replace")
            if last.strip():
                yield last
            return
        if stop_after_s is not None and time.monotonic() - started > stop_after_s:
            return
        time.sleep(POLL_S)


class RunsServer(ThreadingHTTPServer):
    """The server, holding the runs folder its handlers serve."""

    def __init__(self, address: tuple[str, int], runs: Path) -> None:
        super().__init__(address, Handler)
        self.runs_folder = Path(runs)


class Handler(BaseHTTPRequestHandler):
    """One request. The runs folder is set on the server."""

    server_version = "benchmark-serve/1"

    @property
    def runs(self) -> Path:
        assert isinstance(self.server, RunsServer)
        return self.server.runs_folder

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default
        print(f"{self.address_string()} {fmt % args}")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _run_dir(self, name: str) -> Path | None:
        """The run folder of a name, or None when the name is not one."""
        if name not in {r["id"] for r in runs_of(self.runs)}:
            return None
        return self.runs / name

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        parts = [p for p in path.split("/") if p]
        try:
            if not parts:
                return self._index()
            if parts == ["runs"]:
                return self._send(200, json.dumps(runs_of(self.runs), indent=2).encode(), "application/json")
            if len(parts) >= 3 and parts[0] == "runs":
                run_dir = self._run_dir(parts[1])
                if run_dir is None:
                    return self._send(404, b"no such run\n", "text/plain; charset=utf-8")
                rest = parts[2:]
                if rest[0] in FILES and len(rest) == 1:
                    return self._file(run_dir, run_dir / rest[0], FILES[rest[0]])
                if rest == ["streams", "cli"]:
                    return self._sse(run_dir / "streams" / "cli.jsonl", run_dir / "results.json")
                if len(rest) == 2 and rest[0] == "streams" and rest[1].endswith(".mjpeg"):
                    folder = run_dir / "streams" / rest[1][: -len(".mjpeg")]
                    return self._mjpeg(folder, run_dir / "results.json")
                if rest[0] in ("streams", "artifacts", "judgements"):
                    return self._file(run_dir, run_dir.joinpath(*rest), None)
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
        except ConnectionError:  # the viewer went away; a broken pipe is one of these
            return

    def _file(self, run_dir: Path, path: Path, content_type: str | None) -> None:
        """A file inside the run's own folder, compared as resolved paths.

        A string prefix would let `runs-private/` pass for `runs/`, and a
        `..` segment or a symlink would leave the run; both are a 404.
        """
        path = path.resolve()
        if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        guessed = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), guessed)

    def _index(self) -> None:
        rows = "\n".join(
            f"<li><code>{html.escape(r['id'])}</code> "
            f'<a href="/runs/{html.escape(r["id"])}/report.md">report</a> '
            f'<a href="/runs/{html.escape(r["id"])}/results.json">results</a> '
            f'<a href="/runs/{html.escape(r["id"])}/streams/cli">command line</a></li>'
            for r in runs_of(self.runs)
        )
        body = (
            f"<!doctype html><meta charset=utf-8><title>benchmark runs</title><h1>Runs</h1><ul>{rows or '<li>none yet</li>'}</ul>"
        )
        self._send(200, body.encode(), "text/html; charset=utf-8")

    def _sse(self, path: Path, finished: Path) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for line in tail(path, finished=finished):
            self.wfile.write(f"data: {line}\n\n".encode())
            self.wfile.flush()

    def _mjpeg(self, folder: Path, finished: Path) -> None:
        self.send_response(200)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for line in tail(folder / "index.jsonl", finished=finished):
            try:
                frame = json.loads(line)["frame"]
            except (json.JSONDecodeError, KeyError):
                continue
            file = folder / frame
            if not file.is_file():
                continue
            data = file.read_bytes()
            self.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n".encode())
            self.wfile.write(data + b"\r\n")
            self.wfile.flush()


def serve(runs: Path, port: int, host: str = "127.0.0.1") -> None:
    server = RunsServer((host, port), runs)
    print(f"serving {runs} at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark/serve.py", description="Serve a benchmark runs folder.")
    parser.add_argument("--runs", default=str(Path(__file__).resolve().parent / "runs"), help="the runs folder to serve")
    parser.add_argument("--port", type=int, default=8765, help="the port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="the address to bind")
    args = parser.parse_args(argv)
    serve(Path(args.runs), args.port, args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
