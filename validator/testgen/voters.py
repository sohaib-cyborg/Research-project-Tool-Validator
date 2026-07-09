"""
Voter models for the majority pseudo-oracle (TESTGEN.md step 2).

Three independent SCADS models vote on the correct output of a task for each
input. Independence matters: the three models come from different vendors and
training pipelines, so their errors are less likely to correlate. A model
"agreeing" with itself doesn't buy you anything; agreement across genuinely
different models is what makes the majority mean something.

Each voter is a small async function: given a task spec + a chunk of inputs,
it returns a list of {reasoning, output} predictions -- one per input, in
order. The voter DRY-RUNS THE DESCRIPTION step-by-step; it never sees the
tool's code. That's what keeps the vote independent of the tool being tested.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from ..scads_client import ScadsClient, LLMResult


# ---------------------------------------------------------------------------
# Voter roster: three different vendors/architectures for real independence.
# ---------------------------------------------------------------------------
VOTER_MODELS: list[str] = [
    "Qwen/Qwen3-Coder-30B-A3B-Instruct",     # code-specialized (Alibaba)
    "meta-llama/Llama-3.3-70B-Instruct",     # general capable (Meta)
    "openai/gpt-oss-120b",                    # third-party large
]

# Batching: inputs per single voter call (larger = cheaper, but prompt grows).
BATCH_CAP: int = 10

# Marker string used when a voter cannot determine an output.
UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Prompt (mirrors TESTGEN.md section 4 exactly)
# ---------------------------------------------------------------------------
_SYSTEM = (
    "You predict the CORRECT output of a described task for each given input. "
    "Work ONLY from the task DESCRIPTION. You are NOT given the implementation "
    "and must NOT assume one -- reason about what the correct answer SHOULD be. "
    "For each input, DRY-RUN the task step by step (trace the logic mentally), "
    "then state the final output. Match the format and numeric precision shown "
    "in the example input/output pairs. Outputs must be JSON-serializable. "
    'Return ONLY valid JSON: {"predictions": [ ... ]} where each entry is '
    '{"reasoning": "<step-by-step trace>", "output": <predicted output>} in the '
    "same order as the inputs. If you truly cannot determine the output, set "
    f'"output" to the string "{UNKNOWN}".'
)


def _user_prompt(*, name: str, description: str, signature: str,
                 example_pairs: list[dict], inputs_chunk: list[list]) -> str:
    return (
        f"Task name: {name}\n"
        f"Description: {description}\n"
        f"Signature: {signature}\n"
        f"Example input/output pairs (define expected format + precision):\n"
        f"{json.dumps(example_pairs, ensure_ascii=False)}\n\n"
        f"For each of these inputs, dry-run the task and predict the output, "
        f"in order:\n{json.dumps(inputs_chunk, ensure_ascii=False)}\n\n"
        f"Return the JSON object described above. JSON only."
    )


# ---------------------------------------------------------------------------
# Voter output type
# ---------------------------------------------------------------------------
@dataclass
class Prediction:
    """One model's prediction for one input."""
    output: Any                    # can be None, UNKNOWN, or a real value
    reasoning: str = ""
    parse_error: str | None = None  # populated if the model's reply was malformed


@dataclass
class VoterRun:
    """All predictions from one voter for one full input set, plus meta."""
    model: str
    predictions: list[Prediction]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None       # populated if the call itself failed


# ---------------------------------------------------------------------------
# Per-voter async runner (batched)
# ---------------------------------------------------------------------------
async def run_voter(
    model: str,
    *,
    name: str,
    description: str,
    signature: str,
    example_pairs: list[dict],
    inputs: list[list],
    batch_cap: int = BATCH_CAP,
) -> VoterRun:
    """Call one voter model on all inputs, chunked into batches of `batch_cap`.

    Returns a VoterRun with one Prediction per input (in the same order).
    If any batch fails, its inputs get parse_error Predictions -- we do NOT
    let one bad batch kill the whole voter run.
    """
    client = ScadsClient(model=model)
    predictions: list[Prediction] = []
    total_pt = 0
    total_ct = 0
    error: str | None = None

    # Chunk the inputs, run each batch as its own call.
    for start in range(0, len(inputs), batch_cap):
        chunk = inputs[start:start + batch_cap]
        prompt = _user_prompt(
            name=name, description=description, signature=signature,
            example_pairs=example_pairs, inputs_chunk=chunk,
        )
        try:
            parsed, result = await client.acomplete_json(prompt, system=_SYSTEM)
        except Exception as e:
            # Whole batch failed -> attribute the failure to each input in it.
            for _ in chunk:
                predictions.append(Prediction(
                    output=UNKNOWN, reasoning="",
                    parse_error=f"call_failed: {type(e).__name__}: {e}",
                ))
            error = error or str(e)
            continue

        total_pt += result.prompt_tokens
        total_ct += result.completion_tokens

        items = _extract_predictions(parsed, expected=len(chunk))
        predictions.extend(items)

    return VoterRun(
        model=model, predictions=predictions,
        prompt_tokens=total_pt, completion_tokens=total_ct, error=error,
    )


def _extract_predictions(parsed: Any, *, expected: int) -> list[Prediction]:
    """Turn a parsed JSON reply into `expected` Predictions.

    Accepts either {"predictions": [...]} or a bare list. Pads / trims to
    `expected` length so callers always see the right shape.
    """
    if isinstance(parsed, dict) and "predictions" in parsed:
        items = parsed["predictions"]
    elif isinstance(parsed, list):
        items = parsed
    else:
        return [Prediction(output=UNKNOWN, parse_error="unexpected_json_shape")
                for _ in range(expected)]

    out: list[Prediction] = []
    for it in items[:expected]:
        if isinstance(it, dict) and "output" in it:
            out.append(Prediction(
                output=it.get("output"),
                reasoning=str(it.get("reasoning", "")),
            ))
        else:
            out.append(Prediction(output=UNKNOWN, parse_error="malformed_item"))
    # Pad if the model returned too few items.
    while len(out) < expected:
        out.append(Prediction(output=UNKNOWN, parse_error="missing_item"))
    return out


# ---------------------------------------------------------------------------
# Fan out to all voters in parallel
# ---------------------------------------------------------------------------
async def run_all_voters(
    *,
    name: str,
    description: str,
    signature: str,
    example_pairs: list[dict],
    inputs: list[list],
    voter_models: list[str] | None = None,
    batch_cap: int = BATCH_CAP,
) -> list[VoterRun]:
    """Run every voter in `voter_models` concurrently on the same inputs.

    asyncio.gather with return_exceptions=False -- but each voter already
    catches its own exceptions internally, so gather won't see any.
    """
    models = voter_models or VOTER_MODELS
    tasks = [
        run_voter(
            m, name=name, description=description, signature=signature,
            example_pairs=example_pairs, inputs=inputs, batch_cap=batch_cap,
        )
        for m in models
    ]
    return await asyncio.gather(*tasks)
