"""benchmark/harness/redact.py: no key leaves a run folder, a zip's members included."""

import io
import json
import zipfile

from harness import archive as A
from harness import redact as X

ANTHROPIC = "sk-ant-api03-" + "a1B2" * 12
OPENAI = "sk-proj-" + "Zy9x" * 12
GEMINI = "AIza" + "Sy" * 18
XAI = "xai-" + "Q7w" * 12
GITHUB = "ghp_" + "k" * 36
# The other judges' 429s that name the account behind the key, in the words their APIs give them, with made-up ids
# and figures: Anthropic's limit per minute, with its organization id and without, and xAI's credit spent.
ANTHROPIC_ORGANIZATION = "3f6c1a2e-7b4d-4e8f-9a0b-1c2d3e4f5a6b"
ANTHROPIC_429 = (
    "claude-opus-4-7: RateLimitError: Error code: 429 - {'type': 'error', 'error': {'type': 'rate_limit_error', "
    f"'message': 'This request would exceed the rate limit for your organization ({ANTHROPIC_ORGANIZATION}) of 80,000 "
    "output tokens per minute. For details, refer to: https://docs.anthropic.com/en/api/rate-limits. You can see the "
    "response headers for current usage.'}}"
)
ANTHROPIC_429_REDACTED = ANTHROPIC_429.replace(ANTHROPIC_ORGANIZATION, "[redacted]").replace("of 80,000", "of [redacted]")
ANTHROPIC_429_WITHOUT_ID = (
    "This request would exceed the rate limit for your organization of 90,000 input tokens per minute. Please reduce "
    "the prompt length or the maximum tokens requested, or try again later."
)
XAI_TEAM = "9d8c7b6a-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
XAI_429 = (
    "grok-4.7: RateLimitError: Error code: 429 - {'code': 'Some resource has been exhausted', 'error': "
    f"'Your team {XAI_TEAM} has either used all available credits or reached its monthly spending limit. To "
    "continue making API requests, please purchase more credits or raise your spending limit.'}"
)
XAI_429_REDACTED = XAI_429.replace(XAI_TEAM, "[redacted]")


def test_every_key_value_and_everything_shaped_like_a_key_is_redacted(tmp_path):
    run = tmp_path / "runs" / "one"
    (run / "streams").mkdir(parents=True)
    (run / "artifacts" / "0").mkdir(parents=True)
    (run / "streams" / "cli.jsonl").write_text(
        f'{{"line": "env ANTHROPIC_API_KEY={ANTHROPIC} OPENAI={OPENAI}"}}\n{{"line": "the-judge-secret"}}\n', encoding="utf-8"
    )
    (run / "artifacts" / "0" / "answer.md").write_text(f"Found {GEMINI} and {XAI} and {GITHUB}.\n", encoding="utf-8")
    (run / "streams" / "frame.jpg").write_bytes(b"\xff\xd8\x00" + ANTHROPIC.encode() + b"\x00\xff\xd9")
    (run / "report.md").write_text("A key-shaped word: sk-short and a score of 80.\n", encoding="utf-8")
    found = X.redact_folder(tmp_path / "runs", {"the-judge-secret"})
    assert set(found) == {
        run / "streams" / "cli.jsonl",
        run / "artifacts" / "0" / "answer.md",
        run / "streams" / "frame.jpg",
    }
    blob = b"".join(p.read_bytes() for p in run.rglob("*") if p.is_file())
    for secret in (ANTHROPIC, OPENAI, GEMINI, XAI, GITHUB, "the-judge-secret"):
        assert secret.encode() not in blob
    assert b"[redacted]" in blob
    assert (run / "report.md").read_text(encoding="utf-8") == "A key-shaped word: sk-short and a score of 80.\n"


def test_the_values_come_from_every_key_name_the_harness_knows():
    env = {
        "ANTHROPIC_API_KEY": "judge-anthropic",
        "GROK_API_KEY": "judge-grok",
        "SUBJECT_ANTHROPIC_API_KEY": "subject-key",
        "PATH": "/usr/local/bin",
    }
    assert X.key_values(env) == {"judge-anthropic", "judge-grok", "subject-key"}


def test_a_short_value_is_not_taken_for_a_key():
    assert X.key_values({"OPENAI_API_KEY": "x"}) == set()


def a_zipped_run(tmp_path, members):
    """A run folder whose repeat 0 kept an output zip of these members, with its manifest and record."""
    run = tmp_path / "runs" / "one"
    art = run / "artifacts" / "0"
    art.mkdir(parents=True)
    with zipfile.ZipFile(art / A.ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.comment = b"0123abcd"
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 27, 12, 0, 0))
            info.external_attr = 0o755 << 16
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    A.write_manifest(art / A.ZIP)
    results = {"repeats": [{"index": 0, "archive": A.record(art / A.ZIP, run)}]}
    (run / "results.json").write_text(json.dumps(results), encoding="utf-8")
    return run, art / A.ZIP


def test_a_key_inside_a_zip_member_is_redacted_and_the_zip_written_again(tmp_path):
    run, zipped = a_zipped_run(tmp_path, {"app/.env": f"KEY={ANTHROPIC}\n", "README.md": "clean\n"})
    assert ANTHROPIC.encode() not in zipped.read_bytes()  # compressed: a scan of the zip's bytes misses it
    before = json.loads((run / "results.json").read_text(encoding="utf-8"))["repeats"][0]["archive"]["sha256"]
    found = X.redact_folder(tmp_path / "runs", set())
    assert found == {zipped: 1}
    with zipfile.ZipFile(zipped) as zf:
        assert zf.read("app/.env") == b"KEY=[redacted]\n" and zf.read("README.md") == b"clean\n"
        info = zf.getinfo("app/.env")
        assert info.date_time == (2026, 9, 27, 12, 0, 0) and info.external_attr == 0o755 << 16
        assert info.compress_type == zipfile.ZIP_DEFLATED and zf.comment == b"0123abcd"
    assert X.keys_in(zipped.read_bytes()) == []
    # The manifest and the record describe the zip that is published now.
    assert (run / "artifacts" / "0" / A.MANIFEST).read_text(encoding="utf-8") == A.manifest_text(zipped)
    archive = json.loads((run / "results.json").read_text(encoding="utf-8"))["repeats"][0]["archive"]
    assert archive["sha256"] == A.digest(zipped) != before and archive["bytes"] == zipped.stat().st_size


def test_a_milestone_s_zip_written_again_has_its_record_written_again(tmp_path):
    run, zipped = a_zipped_run(tmp_path, {"README.md": "clean\n"})
    milestone = run / "artifacts" / "0" / "milestones" / "scaffold" / A.ZIP
    milestone.parent.mkdir(parents=True)
    with zipfile.ZipFile(milestone, "w") as zf:
        zf.writestr("app/.env", f"KEY={ANTHROPIC}\n")
    A.write_manifest(milestone)
    results = json.loads((run / "results.json").read_text(encoding="utf-8"))
    results["repeats"][0]["phases"] = [{"name": "scaffold", "milestone": A.record(milestone, run)}]
    (run / "results.json").write_text(json.dumps(results), encoding="utf-8")
    assert X.redact_folder(tmp_path / "runs", set()) == {milestone: 1}
    # A later resume checks the milestone against this record, so it describes the zip that is published now.
    kept = json.loads((run / "results.json").read_text(encoding="utf-8"))["repeats"][0]["phases"][0]["milestone"]
    assert kept["sha256"] == A.digest(milestone) and kept["bytes"] == milestone.stat().st_size
    assert (milestone.parent / A.MANIFEST).read_text(encoding="utf-8") == A.manifest_text(milestone)
    assert json.loads((run / "results.json").read_text(encoding="utf-8"))["repeats"][0]["archive"]["sha256"] == A.digest(zipped)


def test_a_zip_with_no_key_is_left_as_it_is(tmp_path):
    _, zipped = a_zipped_run(tmp_path, {"README.md": "clean\n"})
    before = zipped.read_bytes()
    assert X.redact_folder(tmp_path / "runs", set()) == {}
    assert zipped.read_bytes() == before


def test_a_key_in_a_member_s_name_is_found_and_redacted(tmp_path):
    _, zipped = a_zipped_run(tmp_path, {f"keys/{GITHUB}.txt": "x"})
    assert X.keys_in(zipped.read_bytes()) == [f"keys/{GITHUB}.txt"]
    assert X.redact_file(zipped, set()) == (1, [f"keys/{GITHUB}.txt"])
    with zipfile.ZipFile(zipped) as zf:
        assert zf.namelist() == ["keys/[redacted].txt"]


def zip_bytes(members, compression=zipfile.ZIP_DEFLATED):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return out.getvalue()


def test_a_key_inside_a_zip_inside_the_zip_is_redacted(tmp_path):
    inner = zip_bytes({"secrets/.env": f"KEY={OPENAI}\n"})
    _, zipped = a_zipped_run(tmp_path, {"fixtures/bundle.zip": inner, "README.md": "clean\n"})
    assert X.keys_in(zipped.read_bytes()) == ["fixtures/bundle.zip!secrets/.env"]
    assert X.redact_folder(tmp_path / "runs", set()) == {zipped: 1}
    with zipfile.ZipFile(zipped) as zf, zipfile.ZipFile(io.BytesIO(zf.read("fixtures/bundle.zip"))) as nested:
        assert nested.read("secrets/.env") == b"KEY=[redacted]\n"
    assert X.keys_in(zipped.read_bytes()) == []


def test_a_file_that_only_looks_like_a_zip_is_scanned_as_bytes(tmp_path):
    folder = tmp_path / "runs" / "one"
    folder.mkdir(parents=True)
    # A key, then the record that ends a zip: a reader could take the file for an empty zip.
    fake = folder / "notes.md"
    fake.write_bytes(f"{GEMINI}\n".encode() + b"PK\x05\x06" + b"\x00" * 18)
    assert X.redact_folder(tmp_path / "runs", set()) == {fake: 1}
    assert GEMINI.encode() not in fake.read_bytes()


def test_a_key_outside_a_zip_s_members_is_redacted_from_its_bytes():
    data = f"{XAI}\n".encode() + zip_bytes({"README.md": "clean\n"})
    clean, count, places = X.redact_blob(data, set())
    assert count == 1 and XAI.encode() not in clean and places == ["(its bytes as they stand)"]


def test_a_zip_too_deep_to_unpack_is_replaced_by_a_line_that_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "NESTING", 2)
    deepest = zip_bytes({"a.zip": zip_bytes({"b.txt": "clean\n"})})
    _, zipped = a_zipped_run(tmp_path, {"deep.zip": deepest})
    assert X.keys_in(zipped.read_bytes()) == ["deep.zip!a.zip!(not scanned: inside 2 others)"]
    assert X.redact_file(zipped, set())[0] == 1
    with zipfile.ZipFile(zipped) as zf, zipfile.ZipFile(io.BytesIO(zf.read("deep.zip"))) as nested:
        assert nested.read("a.zip") == X.UNREADABLE


def test_a_zip_redaction_could_not_scan_leaves_an_empty_manifest_and_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "UNPACKED", 4)
    run, zipped = a_zipped_run(tmp_path, {"README.md": "more than four bytes\n"})
    assert X.redact_folder(tmp_path / "runs", set()) == {zipped: 1}
    assert zipped.read_bytes() == X.UNREADABLE
    assert (run / "artifacts" / "0" / A.MANIFEST).read_text(encoding="utf-8") == ""
    archive = json.loads((run / "results.json").read_text(encoding="utf-8"))["repeats"][0]["archive"]
    assert archive["files"] == 0 and archive["sha256"] == A.digest(zipped)


def flagged(data: bytes, flag: int = 0, method: int | None = None) -> bytes:
    """A zip with a general-purpose flag set, or its compression method changed, in every header."""
    out = bytearray(data)
    for signature, flag_at, method_at in ((b"PK\x03\x04", 6, 8), (b"PK\x01\x02", 8, 10)):
        at = out.find(signature)
        while at != -1:
            out[at + flag_at] |= flag
            if method is not None:
                out[at + method_at : at + method_at + 2] = method.to_bytes(2, "little")
            at = out.find(signature, at + 4)
    return bytes(out)


def test_a_member_that_does_not_read_is_replaced_and_the_rest_kept():
    stored = zip_bytes({"secret.env": f"KEY={ANTHROPIC}\n", "README.md": "clean\n"}, zipfile.ZIP_STORED)
    for broken, why in ((flagged(stored, flag=1), "encrypted"), (flagged(stored, method=99), "not supported")):
        clean, count, places = X.redact_blob(broken, set())
        assert count >= 1 and ANTHROPIC.encode() not in clean
        assert any(p.startswith("secret.env(not scanned:") and why in p for p in places), places
        with zipfile.ZipFile(io.BytesIO(clean)) as zf:
            assert zf.read("secret.env") == X.UNREADABLE


def test_a_key_in_a_gzip_a_tar_a_bzip2_or_an_xz_is_redacted_and_packed_again(tmp_path):
    import bz2
    import gzip
    import lzma
    import tarfile

    secret = f"KEY={ANTHROPIC}\n".encode()
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:gz") as tf:
        info = tarfile.TarInfo("fixtures/.env")
        info.size = len(secret)
        tf.addfile(info, io.BytesIO(secret))
    for data, unpack in (
        (tar.getvalue(), None),
        (gzip.compress(secret), gzip.decompress),
        (bz2.compress(secret), bz2.decompress),
        (lzma.compress(secret, format=lzma.FORMAT_XZ), lzma.decompress),
    ):
        assert ANTHROPIC.encode() not in data
        clean, count, places = X.redact_blob(data, set())
        assert count == 1, places
        if unpack is None:
            assert places == ["fixtures/.env"]
            with tarfile.open(fileobj=io.BytesIO(clean), mode="r:gz") as tf:
                handle = tf.extractfile("fixtures/.env")
                assert handle is not None and handle.read() == b"KEY=[redacted]\n"
        else:
            assert unpack(clean) == b"KEY=[redacted]\n"
    # Inside the output's zip too.
    zipped = zip_bytes({"fixtures/env.tar.gz": tar.getvalue()})
    assert X.keys_in(zipped) == ["fixtures/env.tar.gz!fixtures/.env"]


def test_a_compressed_form_the_scan_cannot_read_or_a_broken_stream_is_replaced():
    for data in (b"\x28\xb5\x2f\xfd" + b"zstd frame", b"\x1f\x8b" + b"not a gzip stream", b"\xfd7zXZ\x00" + b"broken"):
        clean, count, places = X.redact_blob(data, set())
        assert (clean, count) == (X.UNREADABLE, 1) and "(not scanned:" in places[0]
    # Text that starts like a bzip2 stream and is not one is scanned as text.
    text = f"BZh is how a bzip2 stream starts, and {GITHUB} is not a stream\n".encode()
    clean, count, places = X.redact_blob(text, set())
    assert count == 1 and GITHUB.encode() not in clean and places == [""]


def test_a_file_redaction_cannot_read_is_named_and_the_rest_are_redacted(tmp_path, monkeypatch):
    folder = tmp_path / "runs" / "one"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text(f"{OPENAI}\n", encoding="utf-8")
    (folder / "b.md").write_text(f"{XAI}\n", encoding="utf-8")
    real = X.redact_file

    def redact_file(path, values):
        if path.name == "a.md":
            raise PermissionError("denied")
        return real(path, values)

    monkeypatch.setattr(X, "redact_file", redact_file)
    failed: dict = {}
    assert X.redact_folder(tmp_path / "runs", set(), failed) == {folder / "b.md": 1}
    assert failed == {folder / "a.md": "PermissionError: denied"}


def test_anthropic_s_limit_figure_goes_where_its_429_names_no_id_and_a_uuid_elsewhere_stays():
    said = ANTHROPIC_429_WITHOUT_ID.encode()
    assert X.redact_bytes(said, set()) == (said.replace(b"of 90,000", b"of [redacted]"), 1)
    # A UUID the harness writes, such as a run's id, is no account: only the words around an account id find it.
    for kept in (f'{{"run": "{ANTHROPIC_ORGANIZATION}"}}', f"the {XAI_TEAM} (session)"):
        assert X.redact_bytes(kept.encode(), set()) == (kept.encode(), 0)


def test_a_stripe_key_is_redacted_and_a_short_placeholder_is_not():
    # Made-up strings in Stripe's shapes, the kind a subject's payments tests hold.
    keys = ("sk_test_" + "51Ab" * 6, "sk_live_" + "9zY" * 8, "rk_test_" + "Q1w" * 8, "rk_live_" + "e" * 24, "whsec_" + "Kq3/" * 8)
    for key in keys:
        said = f'API_KEY = "{key}"'.encode()
        assert X.redact_bytes(said, set()) == (b'API_KEY = "[redacted]"', 1)
        assert X.keys_in(said) == [""]
    for placeholder in ("sk_test_x", "sk_test_secret", "rk_live_x", "whsec_test"):
        said = f'API_KEY = "{placeholder}"'.encode()
        assert X.redact_bytes(said, set()) == (said, 0)
