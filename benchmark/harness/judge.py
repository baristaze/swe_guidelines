"""The judges: one prompt, four providers, one structured verdict each.

Every provider gets the same text: the rubric, the subject, the
artifact, and the evidence when the scenario gives some, each truncated
at a stated limit so a judge is never guessing whether it saw the whole
thing. Every provider answers in the same
shape, through its own structured-output path, so the scores compare.

A provider without a key is skipped and named in the results. A
provider that answers with an error keeps the error; nothing is
invented for a provider that did not answer.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import providers as P

EFFORTS = ("low", "medium", "high")
ARTIFACT_LIMIT = 60_000
MAX_OUTPUT_TOKENS = 16_000
# A model under load answers 503 and means "ask again"; a model out of quota
# answers 429 and means "ask something else". The first is retried here, the
# second falls through to the next model in the matrix.
TRANSIENT = ("503", "unavailable", "overloaded", "high demand", "timeout", "temporarily")
RETRIES = 2
RETRY_WAIT_S = 4.0

# The matrix a monthly run redefines. `models.yaml` beside `run.py` is the
# copy to edit; this is the fallback when the file is missing, and it is the
# shape the file follows: one model, optional fallbacks tried in order when a
# model is refused or out of quota, and the effort word each SDK expects.
DEFAULT_MATRIX: dict[str, dict[str, Any]] = {
    "anthropic": {
        "model": "claude-opus-5",
        "fallbacks": ["claude-sonnet-5"],
        "effort": {"low": "low", "medium": "medium", "high": "high"},
    },
    "openai": {
        "model": "gpt-5.5",
        "fallbacks": ["gpt-5.1"],
        "effort": {"low": "low", "medium": "medium", "high": "high"},
    },
    "gemini": {
        "model": "gemini-3.1-pro-preview",
        "fallbacks": ["gemini-3.8-flash"],
        "effort": {"low": "low", "medium": "medium", "high": "high"},
    },
    "xai": {
        "model": "grok-4",
        "fallbacks": [],
        "effort": {"low": "low", "medium": "high", "high": "high"},
    },
}

PROMPT = """\
You are judging one artifact against one rubric. You are a senior
architect reviewing a colleague's work, not a cheerleader and not a
pedant.

## Rubric

{rubric}

## What produced the artifact

{subject}

## Artifact

{artifact}
{evidence}
## How to answer

Give `score` as an integer from 0 to 100. Give `verdict` as `pass`
(the artifact does what the rubric asks), `weak` (it does part of it),
or `fail` (it does not). Give `findings` as a list of
`{{severity, note}}`, severity one of `high`, `medium`, `low`, each
note one sentence naming what is wrong and where. Give `strengths` as
a list of one-sentence notes. Give `rationale` as at most four
sentences saying what decided the score. Judge only what the artifact
says; an artifact that was cut off is judged on what is there, and the
cut is a finding.{check}
"""

EVIDENCE = """
## Evidence

The harness gives you this so you do not have to take the artifact's
word for anything.

{evidence}
"""

CHECK = """ Check every claim the artifact makes against the evidence:
a claim the evidence does not bear out is a finding, and so is an
expected finding the artifact misses."""


@dataclass(frozen=True)
class Finding:
    severity: str
    note: str

    def as_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "note": self.note}


@dataclass(frozen=True)
class Verdict:
    """One judge's answer, in the shape every provider returns."""

    score: int
    verdict: str
    findings: list[Finding] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    rationale: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "findings": [f.as_dict() for f in self.findings],
            "strengths": list(self.strengths),
            "rationale": self.rationale,
        }

    @staticmethod
    def from_data(data: dict[str, Any]) -> "Verdict":
        findings = []
        for raw in data.get("findings") or []:
            if isinstance(raw, dict):
                findings.append(Finding(severity=str(raw.get("severity", "low")), note=str(raw.get("note", ""))))
            else:
                findings.append(Finding(severity="low", note=str(raw)))
        score = int(round(float(data.get("score", 0))))
        return Verdict(
            score=max(0, min(100, score)),
            verdict=str(data.get("verdict", "weak")),
            findings=findings,
            strengths=[str(s) for s in (data.get("strengths") or [])],
            rationale=str(data.get("rationale", "")),
        )


@dataclass
class Judgement:
    """One provider's judgement of one repeat, answered or not."""

    provider: str
    model: str
    effort: str
    status: str = "ok"  # ok | skipped | error
    latency_s: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    raw: str = ""
    error: str | None = None
    verdict: Verdict | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "effort": self.effort,
            "status": self.status,
            "latency_s": round(self.latency_s, 3),
            "usage": dict(self.usage),
            "error": self.error,
            "verdict": self.verdict.as_dict() if self.verdict else None,
        }


def is_transient(exc: Exception) -> bool:
    """Whether an error says "ask again" rather than "ask something else"."""
    text = str(exc).lower()
    return any(word in text for word in TRANSIENT)


def truncate(text: str, limit: int = ARTIFACT_LIMIT) -> str:
    """Cut long text and say so in the text itself, so the judge knows."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated at {limit} characters of {len(text)} ...]"


def build_prompt(rubric: str, subject: str, artifact: str, limit: int = ARTIFACT_LIMIT, evidence: str = "") -> str:
    """The one prompt every provider gets. `evidence` is rendered by `harness.evidence`."""
    return PROMPT.format(
        rubric=rubric.strip(),
        subject=subject.strip(),
        artifact=truncate(artifact, limit),
        evidence=EVIDENCE.format(evidence=evidence.strip()) if evidence.strip() else "",
        check=CHECK if evidence.strip() else "",
    )


def load_matrix(path: str | Path | None) -> dict[str, dict[str, Any]]:
    """The model matrix from `models.yaml`, or the built-in one."""
    if path is None:
        return json.loads(json.dumps(DEFAULT_MATRIX))
    path = Path(path)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_MATRIX))
    from .scenario import parse_text

    data = parse_text(path.read_text(encoding="utf-8"), path.suffix)
    matrix = json.loads(json.dumps(DEFAULT_MATRIX))
    for name, spec in (data or {}).items():
        if name in matrix and isinstance(spec, dict):
            matrix[name].update(spec)
    return matrix


def models_for(matrix: dict[str, dict[str, Any]], provider: str) -> list[str]:
    """The model to try first and the fallbacks after it."""
    spec = matrix.get(provider, {})
    return [str(spec.get("model", ""))] + [str(m) for m in spec.get("fallbacks", [])]


def effort_for(matrix: dict[str, dict[str, Any]], provider: str, effort: str) -> str:
    """The word this provider's SDK expects for an effort level."""
    if effort not in EFFORTS:
        raise ValueError(f"effort is one of {', '.join(EFFORTS)}, got {effort!r}")
    return str(matrix.get(provider, {}).get("effort", {}).get(effort, effort))


def _verdict_model():
    """The pydantic model the SDKs parse into. Imported here, not at module import."""
    from pydantic import BaseModel, Field

    class FindingModel(BaseModel):
        severity: str = Field(description="high, medium, or low")
        note: str

    class VerdictModel(BaseModel):
        score: int
        verdict: str
        findings: list[FindingModel]
        strengths: list[str]
        rationale: str

    return VerdictModel


# Each call returns (data, raw_text, usage). `data` is the verdict as plain data.


def call_anthropic(model: str, effort: str, prompt: str, key: str) -> tuple[dict, str, dict]:
    from anthropic import Anthropic

    client = Anthropic(api_key=key)
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_format=_verdict_model(),
        output_config={"effort": effort},
    )
    parsed = response.parsed_output
    data = parsed.model_dump() if parsed is not None else {}
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
    }
    return data, json.dumps(data), usage


def call_openai(model: str, effort: str, prompt: str, key: str) -> tuple[dict, str, dict]:
    from openai import OpenAI

    client = OpenAI(api_key=key)
    response = client.responses.parse(
        model=model,
        input=prompt,
        text_format=_verdict_model(),
        reasoning={"effort": effort},
    )
    parsed = response.output_parsed
    data = parsed.model_dump() if parsed is not None else {}
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
    }
    return data, response.output_text or json.dumps(data), usage


def gemini_client(key: str):
    """A Gemini client on the key given, with the ambient Google key out of the way.

    `google-genai` reads `GOOGLE_API_KEY` from the environment and says so
    in a warning. The key the harness was handed is the one that judges, so
    the ambient name is removed for the length of the call.
    """
    import os as _os

    from google import genai

    ambient = _os.environ.pop("GOOGLE_API_KEY", None)
    try:
        return genai.Client(api_key=key)
    finally:
        if ambient is not None:
            _os.environ["GOOGLE_API_KEY"] = ambient


def call_gemini(model: str, effort: str, prompt: str, key: str) -> tuple[dict, str, dict]:
    client = gemini_client(key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": _verdict_model(),
            "thinking_config": {"thinking_level": effort},
        },
    )
    parsed = response.parsed
    data = parsed.model_dump() if parsed is not None else json.loads(response.text or "{}")
    meta = response.usage_metadata
    usage = {
        "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
        "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
    }
    return data, response.text or json.dumps(data), usage


def call_xai(model: str, effort: str, prompt: str, key: str) -> tuple[dict, str, dict]:
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.parse(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format=_verdict_model(),
        reasoning_effort=effort,
    )
    message = response.choices[0].message
    parsed = message.parsed
    data = parsed.model_dump() if parsed is not None else json.loads(message.content or "{}")
    usage = {
        "input_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
    }
    return data, message.content or json.dumps(data), usage


CALLS: dict[str, Callable[[str, str, str, str], tuple[dict, str, dict]]] = {
    "anthropic": call_anthropic,
    "openai": call_openai,
    "gemini": call_gemini,
    "xai": call_xai,
}


def judge_one(
    provider: P.Provider,
    prompt: str,
    effort: str,
    matrix: dict[str, dict[str, Any]],
    env: dict[str, str] | None = None,
    call: Callable[[str, str, str, str], tuple[dict, str, dict]] | None = None,
) -> Judgement:
    """One provider's judgement. Never raises: a failure is a Judgement too."""
    name = P.name(provider)
    wanted = effort_for(matrix, name, effort)
    models = [m for m in models_for(matrix, name) if m]
    key = P.key(provider, env)
    if not key:
        return Judgement(
            provider=name,
            model=models[0] if models else "",
            effort=wanted,
            status="skipped",
            error=f"no key: set {' or '.join(P.KEY_NAMES[provider])}",
        )
    dispatch = call or CALLS[name]
    errors: list[str] = []
    for model in models:
        data: dict = {}
        raw, usage, latency = "", {}, 0.0
        for attempt in range(RETRIES):
            started = time.monotonic()
            try:
                data, raw, usage = dispatch(model, wanted, prompt, key)
                latency = time.monotonic() - started
                break
            except Exception as exc:
                message = f"{model}: {type(exc).__name__}: {str(exc)[:400]}"
                errors.append(message)
                if attempt + 1 < RETRIES and is_transient(exc):
                    time.sleep(RETRY_WAIT_S)
                    continue
                break
        if not data:
            errors.append(f"{model}: no parsed verdict")
            continue
        return Judgement(
            provider=name,
            model=model,
            effort=wanted,
            status="ok",
            latency_s=latency,
            usage=usage,
            raw=raw,
            verdict=Verdict.from_data(data),
        )
    return Judgement(
        provider=name,
        model=models[0] if models else "",
        effort=wanted,
        status="error",
        error="; ".join(errors) or "no model configured",
    )


def judge_all(
    flags: P.Provider,
    prompt: str,
    effort: str,
    matrix: dict[str, dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
    call: Callable[[str, str, str, str], tuple[dict, str, dict]] | None = None,
) -> list[Judgement]:
    """Every selected provider's judgement of one artifact, in flag order."""
    matrix = matrix or DEFAULT_MATRIX
    return [judge_one(p, prompt, effort, matrix, env, call) for p in P.members(flags)]


def ask(provider: P.Provider, model: str, prompt: str, key: str, effort: str = "medium") -> tuple[str, dict[str, int]]:
    """One free-text answer from a provider model: the subject of a `qa` scenario."""
    name = P.name(provider)
    if name == "anthropic":
        from anthropic import Anthropic

        client = Anthropic(api_key=key)
        with client.messages.stream(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort},
        ) as stream:
            message = stream.get_final_message()
        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        usage = {"input_tokens": message.usage.input_tokens or 0, "output_tokens": message.usage.output_tokens or 0}
        return text, usage
    if name == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=key)
        response = client.responses.create(model=model, input=prompt, reasoning={"effort": effort})
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
        }
        return response.output_text or "", usage
    if name == "gemini":
        client = gemini_client(key)
        response = client.models.generate_content(
            model=model, contents=prompt, config={"thinking_config": {"thinking_level": effort}}
        )
        meta = response.usage_metadata
        usage = {
            "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
        }
        return response.text or "", usage
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], reasoning_effort=effort
    )
    usage = {
        "input_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
    }
    return response.choices[0].message.content or "", usage
