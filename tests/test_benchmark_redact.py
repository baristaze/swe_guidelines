"""benchmark/harness/redact.py: no key leaves a run folder."""

from harness import redact as X

ANTHROPIC = "sk-ant-api03-" + "a1B2" * 12
OPENAI = "sk-proj-" + "Zy9x" * 12
GEMINI = "AIza" + "Sy" * 18
XAI = "xai-" + "Q7w" * 12
GITHUB = "ghp_" + "k" * 36


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
