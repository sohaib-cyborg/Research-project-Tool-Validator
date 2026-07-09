"""
The pipeline orchestrator -- the state machine itself.

Walking skeleton scope: S0 (provision) -> S1 (parse) -> verdict.

The structure here is deliberately extensible: each stage is a step that either
passes (continue) or fails (short-circuit to REJECT). Adding S2..S7 later means
appending steps, not rewriting the loop. That is the whole point of a framework.
"""
from __future__ import annotations

import time

from .contracts import (
    Stage,
    StageResult,
    ToolArtifact,
    ValidationRecord,
    Verdict,
)
from .parse import parse_stage
from .static import static_stage
from .replay_stage import testgen_stage
from .sandbox import provision


def validate(artifact: ToolArtifact) -> ValidationRecord:
    """Run the (skeleton) validation pipeline on one artifact."""
    record = ValidationRecord(tool_name=artifact.request.name)

    # ---- S0: provision a fresh disposable sandbox -----------------------
    # We open it now to prove the container loop works end-to-end, even though
    # S1 (parse) runs on the host. Later stages (S4+) will exec inside `sb`.
    started = time.perf_counter()
    with provision() as sb:
        elapsed = (time.perf_counter() - started) * 1000
        record.record_stage(
            StageResult(
                stage=Stage.PROVISION,
                passed=sb.available,
                duration_ms=round(elapsed, 3),
                detail=(
                    f"container up from {sb.image}"
                    if sb.available
                    else "no Docker daemon; host-only mode (S1 still runs)"
                ),
                data={"docker_available": sb.available, "image": sb.image},
            )
        )

        # ---- S1: parse on the host --------------------------------------
        if not parse_stage(artifact, record):
            # short-circuit: REJECT already set by parse_stage
            return record

        # ---- S2: static analysis on the host ----------------------------
        if not static_stage(artifact, record):
            # short-circuit: REJECT already set by static_stage
            return record

        # ---- S3: test generation + execution (replay rung) --------------
        # Needs the sandbox `sb` to run the tool on its own example.
        if not testgen_stage(artifact, record, sb):
            # short-circuit: REJECT already set by testgen_stage
            return record

        # (S4..S7 will be added here, each guarded by the previous passing.)

    # Skeleton: if we parsed, we call it a provisional ACCEPT so the happy
    # path is visible. Real ACCEPT will require S2..S6 to pass.
    record.verdict = Verdict.ACCEPT
    return record
