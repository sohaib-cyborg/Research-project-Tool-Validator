"""
Batch runner: runs all tools in samples_batch/ through the current pipeline
(S0 → S1 → S2 → S3-replay) and prints a summary table with expected vs actual.

Reads the .json sidecar of each .py sample for the CapabilityRequest AND the
expected verdict. Marks each row ✓ or ✗ based on whether the pipeline's
verdict matched the expected one, and also whether the reject stage was the
expected one.

Also writes:
  - artifacts/batch_report.json     — the full structured record
  - artifacts/pipeline_survivors.json — list of paths that reached ACCEPT
    (used by the generator batch runner as its input set)

Run:
    python batch_pipeline.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from validator.contracts import CapabilityRequest, ToolArtifact
from validator.pipeline import validate


SAMPLES_DIR = Path("samples_batch")
OUT_DIR = Path("artifacts")


def load_sample(py_path: Path) -> tuple[ToolArtifact, dict]:
    src = py_path.read_text()
    sidecar = py_path.with_suffix(".json")
    if not sidecar.exists():
        raise FileNotFoundError(f"missing sidecar: {sidecar}")
    spec = json.loads(sidecar.read_text())
    req = CapabilityRequest(
        name=spec["name"],
        capability=spec.get("capability", ""),
        description=spec.get("description", ""),
        inputs=spec.get("inputs", []),
        outputs=spec.get("outputs", []),
    )
    return ToolArtifact(source_code=src, request=req), spec


def reject_stage_of(record) -> str | None:
    """Which stage did the pipeline reject at (if any)?"""
    if record.failure:
        return record.failure.stage.value
    return None


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)

    paths = sorted(SAMPLES_DIR.glob("*.py"))
    if not paths:
        print(f"No .py samples found in {SAMPLES_DIR}/")
        return 1

    rows = []
    survivors: list[str] = []

    print(f"\nRunning {len(paths)} tools through the pipeline...\n")

    for path in paths:
        artifact, spec = load_sample(path)
        record = validate(artifact)

        actual_verdict = record.verdict.value
        actual_reject_at = reject_stage_of(record)
        expected_verdict = spec.get("expected_verdict", "?")
        expected_reject_at = spec.get("expected_reject_stage")

        verdict_ok = (actual_verdict == expected_verdict)
        stage_ok = (
            expected_reject_at is None
            or actual_reject_at == expected_reject_at
        )
        overall_ok = verdict_ok and stage_ok

        rows.append({
            "tool": path.name,
            "expected_verdict": expected_verdict,
            "actual_verdict": actual_verdict,
            "expected_reject_at": expected_reject_at,
            "actual_reject_at": actual_reject_at,
            "verdict_ok": verdict_ok,
            "stage_ok": stage_ok,
            "overall_ok": overall_ok,
            "failure_category": (record.failure.category if record.failure else None),
            "failure_message": (record.failure.message if record.failure else None),
            "note": spec.get("note", ""),
        })

        if actual_verdict == "ACCEPT":
            survivors.append(str(path))

    # --- print the summary table -----------------------------------------
    header = f"{'#':<3} {'TOOL':<32} {'EXPECT':<8} {'GOT':<8} {'REJECT@':<14} {'OK':<3}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        got_at = r["actual_reject_at"] or "-"
        mark = "✓" if r["overall_ok"] else "✗"
        print(f"{i:<3} {r['tool']:<32} {r['expected_verdict']:<8} "
              f"{r['actual_verdict']:<8} {got_at:<14} {mark:<3}")
        if not r["overall_ok"]:
            print(f"    NOTE: {r['note']}")
            if r["failure_category"]:
                print(f"    failure: {r['failure_category']} — {r['failure_message']}")

    # --- summary counts ---------------------------------------------------
    n_correct = sum(1 for r in rows if r["overall_ok"])
    print(f"\nSummary: {n_correct}/{len(rows)} tools produced the expected verdict.")
    print(f"Survivors (ACCEPT): {len(survivors)}")

    # --- write structured artifacts --------------------------------------
    (OUT_DIR / "batch_report.json").write_text(json.dumps(rows, indent=2, default=str))
    (OUT_DIR / "pipeline_survivors.json").write_text(json.dumps(survivors, indent=2))
    print(f"\nDetailed report: {OUT_DIR/'batch_report.json'}")
    print(f"Survivors list:  {OUT_DIR/'pipeline_survivors.json'}")

    return 0 if n_correct == len(rows) else 2


if __name__ == "__main__":
    sys.exit(main())
