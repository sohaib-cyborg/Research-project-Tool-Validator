"""
Data contracts for the validation pipeline.

These are the *interfaces* of the framework. Everything that flows between
states is one of these typed objects. Getting these right early is what makes
this a framework rather than a script -- your teammates (Project C, Project A)
integrate against these shapes.

Only the fields needed for the S0+S1 walking skeleton are heavily used right
now; the rest are declared so the record can grow without schema churn later.
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# INPUT contracts (what Project C sends us)
# ---------------------------------------------------------------------------
class CapabilityRequest(BaseModel):
    """A structured spec for a missing tool that would close a capability gap.

    This matches the shape you optimized earlier. It is the *only* ground-truth
    signal we get about what the tool is supposed to do -- there is no oracle.
    """
    name: str
    capability: str
    description: str
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)


class ToolArtifact(BaseModel):
    """The synthesized tool itself, plus the request that produced it.

    For the skeleton the artifact is a single Python source string. Later this
    grows (entrypoint function name, dependencies, API-wrapper metadata, etc.).
    """
    source_code: str
    entrypoint: str | None = None          # function name to call; inferred later
    request: CapabilityRequest


# ---------------------------------------------------------------------------
# PIPELINE state + verdict enums
# ---------------------------------------------------------------------------
class Stage(str, Enum):
    """The states of the machine. The skeleton implements PROVISION + PARSE."""
    PROVISION = "S0_provision"
    PARSE = "S1_parse"
    STATIC = "S2_static"          # not yet implemented
    TESTGEN = "S3_testgen"        # not yet implemented
    EXECUTE = "S4_execute"        # not yet implemented
    MUTATION = "S5_mutation"      # not yet implemented
    SCORE = "S6_score"            # not yet implemented
    SCHEMA = "S7_schema"          # not yet implemented


class Verdict(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PENDING = "PENDING"           # still flowing through the pipeline


# ---------------------------------------------------------------------------
# Per-stage result + the overall record
# ---------------------------------------------------------------------------
class StageResult(BaseModel):
    """Outcome of a single state. `passed=False` short-circuits the pipeline."""
    stage: Stage
    passed: bool
    duration_ms: float
    detail: str = ""                       # human-readable note
    data: dict[str, Any] = Field(default_factory=dict)  # structured payload


class FailureReport(BaseModel):
    """Structured rejection reason sent back to Project C for re-synthesis.

    The `category` vocabulary is where you extend ToolFactory's error taxonomy.
    """
    category: str                          # e.g. "syntax_error", "dangerous_call"
    stage: Stage
    message: str


class ValidationRecord(BaseModel):
    """The object that travels through the whole pipeline and out the other end.

    This is the S1 output for the skeleton, and it is the seed of the eventual
    Validated Tool Record handed to Project A.
    """
    tool_name: str
    verdict: Verdict = Verdict.PENDING
    stages: list[StageResult] = Field(default_factory=list)
    failure: FailureReport | None = None
    reliability_score: float | None = None  # filled by S6 later

    def record_stage(self, result: StageResult) -> None:
        self.stages.append(result)

    def reject(self, failure: FailureReport) -> None:
        self.verdict = Verdict.REJECT
        self.failure = failure


# ---------------------------------------------------------------------------
# S6 -- Reliability scoring (hierarchical; see SCORING.md for the full design)
# ---------------------------------------------------------------------------
# Two hard GATES run before scoring (enforced by the pipeline):
#   * safety violation (S2 high-severity finding)     -> auto-REJECT
#   * crash / wrong answer on the example (S3 replay)  -> auto-REJECT
#
# Hierarchy:
#   FINAL = 0.4*correctness + 0.3*safety + 0.3*test_quality   (renorm over live)
#     correctness  = min(test_results, semantics)
#     test_quality = 0.5*mutation_catch_rate + 0.5*coverage
#   Only LIVE components count; weights renormalize to sum to 1 so unbuilt
#   components don't drag every tool down. All numbers tunable on the mock set.

TOP_WEIGHTS: dict[str, float] = {
    "correctness": 0.40,
    "safety": 0.30,
    "test_quality": 0.30,
}
TESTQUALITY_WEIGHTS: dict[str, float] = {"mutation": 0.50, "coverage": 0.50}

# Band thresholds on the final (renormalized) score in [0, 1]. Tune later.
ACCEPT_THRESHOLD: float = 0.75
REJECT_THRESHOLD: float = 0.45   # below this -> REJECT; between -> NEEDS_REVIEW


class SubScoreStatus(str, Enum):
    REAL = "real"                    # genuinely evaluated
    NOT_EVALUATED = "not_evaluated"  # component not built yet; excluded from total


class Signal(BaseModel):
    """A single measured (or not-yet-measured) 0..1 signal, with provenance."""
    name: str
    value: float = 0.0
    status: SubScoreStatus = SubScoreStatus.NOT_EVALUATED
    detail: str = ""

    @property
    def is_live(self) -> bool:
        return self.status == SubScoreStatus.REAL


def _combine_min(a: Signal, b: Signal) -> tuple[float, SubScoreStatus, str]:
    """correctness = min(test_results, semantics), over whichever are live."""
    live = [s for s in (a, b) if s.is_live]
    if not live:
        return 0.0, SubScoreStatus.NOT_EVALUATED, "no live inputs"
    val = min(s.value for s in live)
    note = "min(" + ", ".join(f"{s.name}={s.value}" for s in live) + ")"
    if len(live) == 1:
        note += " (partial: other input not built)"
    return val, SubScoreStatus.REAL, note


def _combine_weighted(signals: list[Signal], weights: dict[str, float]) -> tuple[float, SubScoreStatus, str]:
    """test_quality = weighted sum over live signals, weights renormalized."""
    live = [s for s in signals if s.is_live]
    if not live:
        return 0.0, SubScoreStatus.NOT_EVALUATED, "no live inputs"
    raw = {s.name: weights.get(s.name, 0.0) for s in live}
    denom = sum(raw.values()) or 1.0
    val = sum((raw[s.name] / denom) * s.value for s in live)
    return round(val, 4), SubScoreStatus.REAL, "weighted sum of " + ", ".join(s.name for s in live)


class ReliabilityScore(BaseModel):
    """The scored report handed toward Project A.

    Holds the raw signals, the derived top-level scores, the final renormalized
    total, and the band recommendation. Every number that fed the decision is
    present, so the score is auditable rather than a black box.
    """
    signals: list[Signal] = Field(default_factory=list)      # raw leaf signals
    top_scores: list[Signal] = Field(default_factory=list)   # derived top-level
    total: float = 0.0
    recommendation: Verdict = Verdict.PENDING
    effective_weights: dict[str, float] = Field(default_factory=dict)
    notes: str = ""

    @classmethod
    def compute(
        cls,
        *,
        test_results: Signal,
        semantics: Signal,
        safety: Signal,
        mutation: Signal,
        coverage: Signal,
    ) -> "ReliabilityScore":
        """Build the hierarchy from leaf signals, per SCORING.md."""
        signals = [test_results, semantics, safety, mutation, coverage]

        # Special case: zero surviving tests -> can't judge correctness.
        if test_results.status == SubScoreStatus.REAL and test_results.detail == "no_surviving_tests":
            return cls(
                signals=signals,
                recommendation=Verdict.NEEDS_REVIEW,
                notes="no tests survived culling; cannot judge correctness",
            )

        # ----- derived top-level scores -----
        c_val, c_stat, c_note = _combine_min(test_results, semantics)
        correctness = Signal(name="correctness", value=round(c_val, 4),
                             status=c_stat, detail=c_note)

        tq_val, tq_stat, tq_note = _combine_weighted(
            [mutation, coverage], TESTQUALITY_WEIGHTS)
        test_quality = Signal(name="test_quality", value=tq_val,
                             status=tq_stat, detail=tq_note)

        safety_top = Signal(name="safety", value=safety.value,
                           status=safety.status, detail=safety.detail)

        top = [correctness, safety_top, test_quality]

        # ----- final: weighted sum over live top-level scores, renormalized -----
        live_top = [s for s in top if s.is_live]
        if not live_top:
            return cls(signals=signals, top_scores=top,
                      recommendation=Verdict.NEEDS_REVIEW,
                      notes="no live top-level scores; cannot score")

        raw = {s.name: TOP_WEIGHTS.get(s.name, 0.0) for s in live_top}
        denom = sum(raw.values()) or 1.0
        eff = {k: v / denom for k, v in raw.items()}
        total = sum(eff[s.name] * s.value for s in live_top)

        if total >= ACCEPT_THRESHOLD:
            rec = Verdict.ACCEPT
        elif total < REJECT_THRESHOLD:
            rec = Verdict.REJECT
        else:
            rec = Verdict.NEEDS_REVIEW

        excluded = [s.name for s in top if not s.is_live]
        note = "scored on live top-level scores"
        if excluded:
            note += f"; excluded (not built): {', '.join(excluded)}"

        return cls(
            signals=signals, top_scores=top, total=round(total, 4),
            recommendation=rec,
            effective_weights={k: round(v, 4) for k, v in eff.items()},
            notes=note,
        )
