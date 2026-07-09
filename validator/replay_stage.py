"""
S3 -- Test generation + execution (replay strategy only, for now).

This first version implements exactly one strategy: REPLAY. Later this becomes
a multi-strategy stage (replay + LLM-guessed pairs + Hypothesis properties),
each behind the same interface so you can measure which catches more bugs.

Flow:
  1. Build a replay script from the request's example (replay.py).
  2. Run it inside the container (sandbox_exec.py).
  3. Parse the result and record pass/fail.

Verdict policy for this rung:
  * replay ERROR (tool crashed on its own example) -> REJECT (dangerous: it does
    not even do the one thing it was asked to do).
  * replay FAIL (ran but wrong answer on its own example) -> REJECT.
  * replay PASS -> continue (this is weak evidence -- one example -- which is
    exactly why higher rungs exist).
  * no example to replay -> record 'skipped', continue (can't reject on nothing).
"""
from __future__ import annotations

import time

from .contracts import (
    FailureReport,
    Stage,
    StageResult,
    ToolArtifact,
    ValidationRecord,
)
from .replay import build_replay_script, parse_replay_output
from .sandbox import Sandbox
from .sandbox_exec import run_script_in_container, container_identity


def testgen_stage(artifact: ToolArtifact, record: ValidationRecord, sb: Sandbox) -> bool:
    """S3 replay rung. Returns True to continue, False to short-circuit (REJECT)."""
    started = time.perf_counter()

    script = build_replay_script(artifact)
    if script is None:
        elapsed = (time.perf_counter() - started) * 1000
        record.record_stage(
            StageResult(
                stage=Stage.TESTGEN,
                passed=True,                     # nothing to test != failure
                duration_ms=round(elapsed, 3),
                detail="replay skipped (no example values in request)",
                data={"strategy": "replay", "status": "skipped"},
            )
        )
        return True

    # Need a real container to run it. If Docker is absent, record and continue.
    if not sb.available:
        elapsed = (time.perf_counter() - started) * 1000
        record.record_stage(
            StageResult(
                stage=Stage.TESTGEN,
                passed=True,
                duration_ms=round(elapsed, 3),
                detail="replay not run (no Docker; host-only mode)",
                data={"strategy": "replay", "status": "no_sandbox"},
            )
        )
        return True

    exit_code, output = run_script_in_container(sb, script, timeout_s=10)
    result = parse_replay_output(output)
    elapsed = (time.perf_counter() - started) * 1000

    # Proof of WHERE it ran + the raw container logs. Attached to every outcome
    # below so you can always see what the container actually did.
    identity = container_identity(sb)
    run_evidence = {
        "container_id": identity.get("container_id"),
        "container_hostname": identity.get("container_hostname"),
        "exit_code": exit_code,
        "container_stdout": output,          # the raw logs from inside Docker
    }

    # Couldn't parse a result -> treat as an execution failure.
    if result is None:
        record.record_stage(
            StageResult(
                stage=Stage.TESTGEN, passed=False, duration_ms=round(elapsed, 3),
                detail=f"replay produced no parseable result (exit {exit_code})",
                data={"strategy": "replay", "status": "unparseable",
                      **run_evidence},
            )
        )
        record.reject(FailureReport(
            category="replay_no_result", stage=Stage.TESTGEN,
            message=f"replay script did not return a result (exit {exit_code})",
        ))
        return False

    status = result.get("status")
    passed = result.get("passed", False)

    if status == "error":
        record.record_stage(StageResult(
            stage=Stage.TESTGEN, passed=False, duration_ms=round(elapsed, 3),
            detail=f"tool crashed on its own example: {result.get('error')}",
            data={"strategy": "replay", **result, **run_evidence},
        ))
        record.reject(FailureReport(
            category="crashes_on_example", stage=Stage.TESTGEN,
            message=result.get("error", "exception during replay"),
        ))
        return False

    if not passed:
        record.record_stage(StageResult(
            stage=Stage.TESTGEN, passed=False, duration_ms=round(elapsed, 3),
            detail=f"wrong output on example: got {result.get('actual')} "
                   f"expected {result.get('expected')}",
            data={"strategy": "replay", **result, **run_evidence},
        ))
        record.reject(FailureReport(
            category="wrong_on_example", stage=Stage.TESTGEN,
            message=f"expected {result.get('expected')}, got {result.get('actual')}",
        ))
        return False

    # passed
    record.record_stage(StageResult(
        stage=Stage.TESTGEN, passed=True, duration_ms=round(elapsed, 3),
        detail=f"replay passed in container {identity.get('container_id')}: "
               f"{result.get('actual')} == {result.get('expected')}",
        data={"strategy": "replay", **result, **run_evidence},
    ))
    return True
