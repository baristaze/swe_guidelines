"""benchmark/serve.py: the runs listing, the file routes, and the tail."""

import http.client
import importlib.util
import json
import threading
from pathlib import Path

import pytest

SERVE = Path(__file__).resolve().parent.parent / "benchmark" / "serve.py"


def load_serve():
    spec = importlib.util.spec_from_file_location("benchmark_serve", SERVE)
    assert spec is not None and spec.loader is not None
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
    server = serve_module.RunsServer(("127.0.0.1", 0), runs)
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


def test_a_file_inside_the_run_is_served(client):
    status, _, body = get(client, "/runs/20260101-000000-one/streams/cli.jsonl")
    assert status == 200 and b'"hello"' in body


def test_a_path_that_leaves_the_run_is_a_404(client, runs):
    # a sibling whose name starts with the runs folder's name passed a string prefix check
    private = runs.parent / "runs-private"
    private.mkdir()
    (private / "secret.txt").write_text("secret\n", encoding="utf-8")
    other = runs / "20260101-000000-two"
    other.mkdir()
    (other / "report.md").write_text("# Another run\n", encoding="utf-8")
    run = "/runs/20260101-000000-one"
    assert get(client, f"{run}/streams/%2e%2e/%2e%2e/%2e%2e/runs-private/secret.txt")[0] == 404
    assert get(client, f"{run}/artifacts/%2e%2e/%2e%2e/runs-private/secret.txt")[0] == 404
    assert get(client, f"{run}/judgements/%2e%2e/%2e%2e/20260101-000000-two/report.md")[0] == 404
    assert get(client, f"{run}/streams/..%2f..%2f..%2fruns-private%2fsecret.txt")[0] == 404


def test_the_tail_decodes_a_character_only_once_it_is_whole(serve_module, tmp_path):
    path = tmp_path / "cli.jsonl"
    whole = "é\n".encode()
    path.write_bytes(b"one\n" + whole[:1])
    serve_module.POLL_S = 0.01
    lines = serve_module.tail(path, stop_after_s=5)
    assert next(lines) == "one"
    with path.open("ab") as fh:
        fh.write(whole[1:])
    assert next(lines) == "é"


def test_the_tail_ends_once_the_run_has_finished_and_the_file_stopped_growing(serve_module, tmp_path):
    path = tmp_path / "cli.jsonl"
    path.write_text("one\ntwo\nlast", encoding="utf-8")
    finished = tmp_path / "results.json"
    serve_module.POLL_S = 0.01
    finished.write_text("{}", encoding="utf-8")
    assert list(serve_module.tail(path, finished=finished)) == ["one", "two", "last"]


def test_the_tail_keeps_waiting_while_the_run_is_unfinished(serve_module, tmp_path):
    path = tmp_path / "cli.jsonl"
    path.write_text("one\n", encoding="utf-8")
    serve_module.POLL_S = 0.01
    lines = serve_module.tail(path, stop_after_s=0.05, finished=tmp_path / "results.json")
    assert list(lines) == ["one"]  # ended by the time limit, not by the finish


def test_a_viewer_that_goes_away_is_not_an_error(serve_module, runs, monkeypatch):
    server = serve_module.RunsServer(("127.0.0.1", 0), runs)

    def reset(self, *_args):
        raise ConnectionResetError("the viewer went away")

    monkeypatch.setattr(serve_module.Handler, "_index", reset)
    unhandled: list[object] = []
    monkeypatch.setattr(server, "handle_error", lambda request, address: unhandled.append(address))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", "/")
        with pytest.raises((http.client.RemoteDisconnected, ConnectionError)):
            connection.getresponse()
        connection.close()
        assert unhandled == []  # the handler caught it; the server never saw an error
        # the server still answers the next viewer
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        assert get(connection, "/runs")[0] == 200
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_tail_ends_when_an_unfinished_run_has_gone_quiet(serve_module, tmp_path):
    path = tmp_path / "cli.jsonl"
    path.write_text("one\nlast", encoding="utf-8")
    serve_module.POLL_S = 0.01
    lines = serve_module.tail(path, stop_after_s=5, finished=tmp_path / "results.json", idle_s=0.05)
    assert list(lines) == ["one", "last"]  # ended by the idle cutoff, well before the time limit
    assert list(serve_module.tail(tmp_path / "never.jsonl", stop_after_s=5, idle_s=0.05)) == []


def test_every_answer_carries_the_security_headers(client):
    client.request("GET", "/")
    response = client.getresponse()
    response.read()
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert "script-src" not in response.headers["Content-Security-Policy"]  # default-src 'none' holds scripts off


def test_a_request_for_another_host_name_is_refused(client):
    client.request("GET", "/runs", headers={"Host": "attacker.example:8765"})
    response = client.getresponse()
    response.read()
    assert response.status == 421


def test_the_frame_stream_serves_only_frames_inside_its_folder(client, runs, tmp_path):
    # The index names one frame of its own, one file outside the folder, and
    # one that is not a JPEG; only the first is served.
    run = runs / "20260101-000000-one"
    frames = run / "streams" / "browser"
    frames.mkdir(parents=True)
    (frames / "0001.jpg").write_bytes(b"FRAME-INSIDE")
    (tmp_path / "secret.jpg").write_bytes(b"SECRET-OUTSIDE")
    (frames / "notes.txt").write_text("NOT-A-FRAME", encoding="utf-8")
    index = [{"frame": "0001.jpg"}, {"frame": "../../../../secret.jpg"}, {"frame": "notes.txt"}]
    (frames / "index.jsonl").write_text("".join(json.dumps(i) + "\n" for i in index), encoding="utf-8")
    (run / "results.json").touch()  # finished, so the stream ends
    status, _, body = get(client, "/runs/20260101-000000-one/streams/browser.mjpeg")
    assert status == 200
    assert b"FRAME-INSIDE" in body
    assert b"SECRET-OUTSIDE" not in body and b"NOT-A-FRAME" not in body


def test_a_server_bound_to_every_address_answers_its_addresses_and_refuses_names(serve_module, runs):
    server = serve_module.RunsServer(("0.0.0.0", 0), runs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        for host, status in (
            (f"127.0.0.1:{port}", 200),
            (f"localhost:{port}", 200),
            (f"192.0.2.7:{port}", 200),  # the machine's address on its network
            (f"[::1]:{port}", 200),
            ("192.0.2.7:1", 421),  # another port
            (f"attacker.example:{port}", 421),  # a name that could rebind to this machine
        ):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", "/runs", headers={"Host": host})
            response = connection.getresponse()
            response.read()
            connection.close()
            assert response.status == status, host
    finally:
        server.shutdown()
        server.server_close()
