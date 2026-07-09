"""
Voting tally + majority pseudo-oracle (TESTGEN.md step 2).

Takes voter predictions and decides which inputs have a TRUSTED expected
output (majority of voters agree) versus which get DROPPED (no majority).
This is the deterministic part -- no LLM, testable offline.

Comparison policy (locked in TESTGEN.md section 8):
  * JSON-normalized comparison. Parse both sides, compare structurally.
  * Effectively exact after JSON parse. No float tolerance / string
    normalization yet -- add ONLY if real runs show good votes lost to
    trivial formatting.
  * The UNKNOWN marker never wins a majority (it isn't a value).

Output policy (locked in TESTGEN.md section 5):
  * Keep BOTH trusted and dropped, plus the full votes-per-model record.
  * The "fraction of inputs that got a trusted answer" is itself a result.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .voters import (
    UNKNOWN, VOTER_MODELS, VoterRun, run_all_voters,
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
@dataclass
class TrustedTest:
    """An input with a majority-agreed expected output."""
    input: list
    expected_output: Any
    agreement: str                # e.g. "2/3"
    votes: dict[str, dict]        # model -> {"reasoning": str, "output": Any}


@dataclass
class DroppedTest:
    """An input with no majority; kept for auditability."""
    input: list
    reason: str                   # "no_majority" | "all_unknown" | "voter_errors"
    votes: dict[str, dict]


@dataclass
class VotingResult:
    """The full outcome of a voting round."""
    trusted: list[TrustedTest] = field(default_factory=list)
    dropped: list[DroppedTest] = field(default_factory=list)
    voter_models: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def trusted_fraction(self) -> float:
        total = len(self.trusted) + len(self.dropped)
        return len(self.trusted) / total if total else 0.0


# ---------------------------------------------------------------------------
# Normalized equality (the "same output?" question)
# ---------------------------------------------------------------------------
def _normalize(value: Any) -> str:
    """Canonical text form for comparison.

    JSON round-trip with sorted keys and no whitespace so two structurally
    equal values compare equal even if the models formatted them differently.
    """
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    except TypeError:
        # Non-JSON-serializable (shouldn't happen with our JSON-only rule) ->
        # fall back to repr so we never crash.
        return repr(value)


def _tally_one(outputs: list[Any]) -> tuple[Any, int]:
    """Return (winning_value, count). UNKNOWN is excluded from the tally."""
    counts: Counter = Counter()
    representatives: dict[str, Any] = {}      # normalized-key -> original value
    for v in outputs:
        if v == UNKNOWN:
            continue
        key = _normalize(v)
        counts[key] += 1
        representatives.setdefault(key, v)
    if not counts:
        return UNKNOWN, 0
    top_key, top_count = counts.most_common(1)[0]
    return representatives[top_key], top_count


# ---------------------------------------------------------------------------
# Turn voter runs into trusted/dropped tests
# ---------------------------------------------------------------------------
def tally_votes(
    inputs: list[list],
    voter_runs: list[VoterRun],
    *,
    require_strict_majority: bool = True,
) -> VotingResult:
    """Combine voter predictions into TrustedTest / DroppedTest lists.

    `require_strict_majority=True` means "> half" (Raft-style). With 3 voters
    that needs 2 matching outputs; with 5 it needs 3.
    """
    n_voters = len(voter_runs)
    threshold = (n_voters // 2) + 1 if require_strict_majority else \
                (n_voters + 1) // 2

    trusted: list[TrustedTest] = []
    dropped: list[DroppedTest] = []
    pt = sum(vr.prompt_tokens for vr in voter_runs)
    ct = sum(vr.completion_tokens for vr in voter_runs)

    # Sanity: all voters should have one prediction per input. Pad defensively.
    def _pred_at(vr: VoterRun, i: int):
        return vr.predictions[i] if i < len(vr.predictions) else None

    for i, inp in enumerate(inputs):
        per_model_outputs: list[Any] = []
        votes_record: dict[str, dict] = {}
        for vr in voter_runs:
            p = _pred_at(vr, i)
            if p is None:
                per_model_outputs.append(UNKNOWN)
                votes_record[vr.model] = {"reasoning": "", "output": UNKNOWN,
                                          "error": vr.error or "no_prediction"}
                continue
            per_model_outputs.append(p.output)
            entry = {"reasoning": p.reasoning, "output": p.output}
            if p.parse_error:
                entry["error"] = p.parse_error
            votes_record[vr.model] = entry

        winner, count = _tally_one(per_model_outputs)

        if winner == UNKNOWN:
            dropped.append(DroppedTest(
                input=inp, reason="all_unknown", votes=votes_record))
        elif count >= threshold:
            trusted.append(TrustedTest(
                input=inp, expected_output=winner,
                agreement=f"{count}/{n_voters}", votes=votes_record))
        else:
            dropped.append(DroppedTest(
                input=inp, reason="no_majority", votes=votes_record))

    return VotingResult(
        trusted=trusted, dropped=dropped,
        voter_models=[vr.model for vr in voter_runs],
        prompt_tokens=pt, completion_tokens=ct,
    )


# ---------------------------------------------------------------------------
# End-to-end: fan out to voters and tally
# ---------------------------------------------------------------------------
async def run_voting_oracle_async(
    *,
    name: str,
    description: str,
    signature: str,
    example_pairs: list[dict],
    inputs: list[list],
    voter_models: list[str] | None = None,
) -> VotingResult:
    """Async entry point. Runs all voters in parallel, then tallies."""
    runs = await run_all_voters(
        name=name, description=description, signature=signature,
        example_pairs=example_pairs, inputs=inputs, voter_models=voter_models,
    )
    return tally_votes(inputs, runs)


def run_voting_oracle(
    *,
    name: str,
    description: str,
    signature: str,
    example_pairs: list[dict],
    inputs: list[list],
    voter_models: list[str] | None = None,
) -> VotingResult:
    """Sync wrapper for callers who don't want to think about async."""
    return asyncio.run(run_voting_oracle_async(
        name=name, description=description, signature=signature,
        example_pairs=example_pairs, inputs=inputs, voter_models=voter_models,
    ))
