"""benchmark/serve.py: the runs listing, the file routes, and the tail."""

import http.client
import importlib.util
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SERVE = Path(__file__).resolve().parent.parent / "benchmark" / "serve.py"


def load_serve():
    spec = importlib.util.spec_from_file_location("benchmark_serve", SERVE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def serve_module():
    return load_serve()


@pytest.fixture
def runs(tmp_path):
    folder = tmp_path / "runs"
    run = folder / "20260101-000000-one"
    (run / "streams").mkdir(parents=True)
    (run / "report.md").write_text("# Benchmark run\n", encoding="utf-8")
    (run / "results.json").write_text(json.dumps({"run_id": "20260101-000000-one"}), encoding="utf-8")
    (run / "streams" / "cli.jsonl").write_text(json.dumps({"t": 1.0, "s": "out", "line": "hello"}) + "\n", encoding="utf-8")
    return folder


@pytest.fixture
def client(serve_module, runs):
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve_module.Handler)
    server.runs_folder = runs
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    yield connection
    connection.close()
    server.shutdown()
    server.server_close()


def get(connection, path):
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read()
    return response.status, response.headers.get("Content-Type"), body


def test_the_runs_listing_names_what_each_run_holds(serve_module, runs):
    listed = serve_module.runs_of(runs)
    assert listed == [{"id": "20260101-000000-one", "report": True, "results": True, "streams": ["cli.jsonl"]}]
    assert serve_module.runs_of(runs / "missing") == []


def test_the_index_and_the_json_listing_answer(client):
    status, content_type, body = get(client, "/")
    assert status == 200 and "text/html" in content_type
    assert b"20260101-000000-one" in body
    status, content_type, body = get(client, "/runs")
    assert status == 200 and content_type == "application/json"
    assert json.loads(body)[0]["id"] == "20260101-000000-one"


def test_the_report_and_the_results_are_served_with_their_own_types(client):
    status, content_type, body = get(client, "/runs/20260101-000000-one/report.md")
    assert status == 200 and "text/markdown" in content_type and body.startswith(b"# Benchmark run")
    status, content_type, _ = get(client, "/runs/20260101-000000-one/results.json")
    assert status == 200 and content_type == "application/json"


def test_a_run_that_does_not_exist_is_a_404(client):
    assert get(client, "/runs/nothing/report.md")[0] == 404
    assert get(client, "/runs/20260101-000000-one/secrets")[0] == 404
    assert get(client, "/elsewhere")[0] == 404


def test_the_tail_yields_whole_lines_and_gives_up_when_told(serve_module, tmp_path):
    path = tmp_path / "cli.jsonl"
    path.write_text("one\ntwo\nhalf", encoding="utf-8")
    serve_module.POLL_S = 0.01
    assert list(serve_module.tail(path, stop_after_s=0.02)) == ["one", "two"]


def test_the_tail_waits_for_a_file_that_is_not_there_yet(serve_module, tmp_path):
    serve_module.POLL_S = 0.01
    assert list(serve_module.tail(tmp_path / "later.jsonl", stop_after_s=0.02)) == []
