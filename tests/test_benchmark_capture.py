"""benchmark/harness/capture.py: the command line stream and the frame folder."""

import json

from harness.capture import CdpScreencast, CliStream, FrameSink

import pytest


def test_every_line_is_one_json_record(tmp_path):
    with CliStream(tmp_path / "cli.jsonl") as stream:
        stream.write("out", "first\n")
        stream.write("err", "second")
        stream.note("a note from the harness")
    records = CliStream.read(tmp_path / "cli.jsonl")
    assert [r["s"] for r in records] == ["out", "err", "err"]
    assert [r["line"] for r in records][:2] == ["first", "second"]
    assert all(isinstance(r["t"], float) for r in records)


def test_a_stream_name_that_is_not_out_or_err_is_refused(tmp_path):
    with CliStream(tmp_path / "cli.jsonl") as stream:
        with pytest.raises(ValueError, match="'out' or 'err'"):
            stream.write("log", "x")


def test_a_torn_last_line_is_left_out(tmp_path):
    path = tmp_path / "cli.jsonl"
    path.write_text(json.dumps({"t": 1.0, "s": "out", "line": "whole"}) + '\n{"t": 2.0, "s":', encoding="utf-8")
    assert [r["line"] for r in CliStream.read(path)] == ["whole"]


def test_frames_are_numbered_and_indexed(tmp_path):
    sink = FrameSink(tmp_path / "browser")
    first = sink.add(b"one", t=1.0)
    sink.add(b"two", t=2.0)
    assert first.name == "000000.jpg"
    assert (tmp_path / "browser" / "000001.jpg").read_bytes() == b"two"
    assert [f["frame"] for f in sink.frames()] == ["000000.jpg", "000001.jpg"]
    assert [f["t"] for f in sink.frames()] == [1.0, 2.0]


def test_a_screencast_with_no_chrome_reports_the_failure_and_raises_nothing(tmp_path):
    cast = CdpScreencast(FrameSink(tmp_path / "browser"), port=1)
    result = cast.capture(seconds=0.1)
    assert not result.ok
    assert result.frames == 0
    assert result.error
