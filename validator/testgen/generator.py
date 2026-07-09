"""
Step A of the v2 test-generation pipeline (TESTGEN.md): the GENERATOR.

One LLM reads a CapabilityRequest + the tool's source code and writes:
  * input/output PAIRS   e.g. add(2, 3) -> 5
  * assertion PROPERTIES e.g. "for positive a, b: result > a and result > b"

Prompt discipline (from your locked decision):
  DESCRIPTION is the ORACLE (source of truth).
  CODE is shown only for INTERFACE reference (arg shapes / types).
  If the code disagrees with the description, the description wins.

This is one call, async, no batching (the whole task fits in one prompt).
The returned items are what Step B (judges) will judge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..contracts import CapabilityRequest
from ..scads_client import ScadsClient
from .. import config


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------
@dataclass
class Pair:
    """Concrete call with an expected result."""
    input: list                 # positional args
    expected_output: Any
    note: str = ""


@dataclass
class Property:
    """Rule the output must obey (evaluated later against the real tool)."""
    input_condition: str        # e.g. "a > 0 and b > 0"
    assertion: str              # e.g. "result > a and result > b"
    note: str = ""


@dataclass
class GeneratorResult:
    """What one generator call produces."""
    pairs: list[Pair] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_content: str = ""       # kept for debugging / audit
    parse_error: str | None = None


# ---------------------------------------------------------------------------
# Prompt (mirrors TESTGEN.md section 4A)
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You WRITE TEST CASES for a described task.\n"
    "CRITICAL: The DESCRIPTION is the ORACLE — the source of truth for what "
    "the task SHOULD do. You are also shown the tool's CODE, but ONLY as an "
    "INTERFACE REFERENCE — to learn argument shapes, types, and calling "
    "convention. DO NOT base the semantics of your test cases on the code. "
    "If the description says one thing and the code does another, produce "
    "tests that reflect the DESCRIPTION. The code may be wrong; the "
    "description is the truth.\n\n"
    "Produce two kinds of tests:\n"
    "  1. INPUT/OUTPUT PAIRS: concrete calls with expected results.\n"
    "  2. ASSERTION PROPERTIES: general rules the output must obey. Use "
    "`result` for the return value and the parameter names for inputs.\n\n"
    "Include edge cases (empty, boundary, negative, unusual-but-valid).\n"
    "Return ONLY valid JSON of the shape:\n"
    "{\n"
    '  "pairs":      [ {"input": <args-list>, "expected_output": <value>, "note": "<why>"}, ... ],\n'
    '  "properties": [ {"input_condition": "<when this holds>", "assertion": '
    '"<Python-style boolean expression using `result` and the input names>", '
    '"note": "<why>"}, ... ]\n'
    "}"
)


def _user_prompt(
    *, request: CapabilityRequest, signature: str, artifact_source: str,
    n_pairs: int, n_properties: int,
) -> str:
    example_pairs = _extract_examples(request)
    return (
        f"Task name: {request.name}\n"
        f"Description: {request.description}\n"
        f"Signature: {signature}\n"
        f"Example input/output pairs (define format + precision):\n"
        f"{json.dumps(example_pairs, ensure_ascii=False)}\n\n"
        f"Tool code (FOR INTERFACE REFERENCE ONLY — do NOT trust it for "
        f"correctness):\n```python\n{artifact_source}\n```\n\n"
        f"Generate {n_pairs} input/output pairs and {n_properties} assertion "
        f"properties. Reminder: DESCRIPTION is oracle; CODE is only for "
        f"interface shape. JSON only."
    )


def _extract_examples(request: CapabilityRequest) -> list[dict]:
    """Pull `{input: [...], output: ...}` examples from the request.

    The CapabilityRequest stores inputs/outputs as lists of dicts with a
    `value` field (Option A). We pair position-wise where lengths match.
    """
    ins = [it.get("value") for it in request.inputs if isinstance(it, dict) and "value" in it]
    outs = [it.get("value") for it in request.outputs if isinstance(it, dict) and "value" in it]
    if not ins or not outs:
        return []
    # Present as a single canonical example call
    if len(outs) == 1:
        return [{"input": ins, "output": outs[0]}]
    return [{"input": ins, "output": outs}]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------
async def generate_tests(
    request: CapabilityRequest,
    artifact_source: str,
    *,
    signature: str = "",
    model: str | None = None,
    n_pairs: int | None = None,
    n_properties: int | None = None,
) -> GeneratorResult:
    """Ask one LLM to produce pairs + properties for the tool.

    `signature` is a human-readable hint (e.g. `add(a: int, b: int) -> int`);
    if omitted, we don't try to reverse-engineer it here — the code speaks for
    itself for the LLM.
    """
    model = model or config.GENERATOR_MODEL
    n_pairs = n_pairs if n_pairs is not None else config.N_PAIRS
    n_properties = n_properties if n_properties is not None else config.N_PROPERTIES

    client = ScadsClient(model=model)
    prompt = _user_prompt(
        request=request, signature=signature or f"{request.name}(...)",
        artifact_source=artifact_source,
        n_pairs=n_pairs, n_properties=n_properties,
    )

    try:
        parsed, llm_result = await client.acomplete_json(prompt, system=_SYSTEM)
    except Exception as e:
        return GeneratorResult(
            model=model,
            parse_error=f"call_failed: {type(e).__name__}: {e}",
        )

    pairs = _to_pairs(parsed.get("pairs", []) if isinstance(parsed, dict) else [])
    properties = _to_properties(
        parsed.get("properties", []) if isinstance(parsed, dict) else []
    )

    return GeneratorResult(
        pairs=pairs, properties=properties, model=model,
        prompt_tokens=llm_result.prompt_tokens,
        completion_tokens=llm_result.completion_tokens,
        raw_content=llm_result.content,
    )


def _to_pairs(items: Any) -> list[Pair]:
    out: list[Pair] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict) or "input" not in it or "expected_output" not in it:
            continue
        inp = it["input"] if isinstance(it["input"], list) else [it["input"]]
        out.append(Pair(
            input=inp,
            expected_output=it["expected_output"],
            note=str(it.get("note", "")),
        ))
    return out


def _to_properties(items: Any) -> list[Property]:
    out: list[Property] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict) or "assertion" not in it:
            continue
        out.append(Property(
            input_condition=str(it.get("input_condition", "")),
            assertion=str(it["assertion"]),
            note=str(it.get("note", "")),
        ))
    return out
