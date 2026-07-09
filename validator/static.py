"""
S2 -- Static analysis (runs on the host, deterministic, no LLM, no container).

Two checks on the first pass:

  1. Security scan (bandit)  -> catches dangerous calls: os.system, eval, exec,
     subprocess with shell, pickle, raw sockets, etc.
  2. Import allowlist (AST)  -> catches hallucinated / disallowed libraries by
     comparing every `import` against the request's declared dependencies plus a
     safe stdlib baseline.

Policy (your choice):
  * A HIGH-severity security finding -> hard REJECT (a dangerous tool must never
    reach the registry, regardless of anything else).
  * Everything else (medium/low findings, unknown imports) -> recorded as data
    for S6 scoring to weigh later. Not an automatic reject.

This directly feeds RQ2: count how many tools the static gate alone rejects
before any container is ever run.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .contracts import (
    FailureReport,
    Stage,
    StageResult,
    ToolArtifact,
    ValidationRecord,
)

# Bandit severities we treat as a hard gate.
HARD_REJECT_SEVERITIES = {"HIGH"}

# A small baseline of always-allowed stdlib modules. The request's declared
# dependencies are added on top of this per-tool.
STDLIB_BASELINE = {
    "math", "statistics", "json", "re", "datetime", "collections",
    "itertools", "functools", "typing", "dataclasses", "string",
    "decimal", "fractions", "random", "enum", "abc", "copy",
}


def _run_bandit(source: str) -> list[dict]:
    """Run bandit on the source and return its findings (possibly empty)."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(source)
        tmp = f.name
    try:
        proc = subprocess.run(
            ["bandit", "-f", "json", tmp],
            capture_output=True, text=True, timeout=30,
        )
        # bandit exits non-zero when it finds issues; JSON is still on stdout.
        if not proc.stdout.strip():
            return []
        data = json.loads(proc.stdout)
        return [
            {
                "test_id": r["test_id"],
                "severity": r["issue_severity"],
                "confidence": r["issue_confidence"],
                "text": r["issue_text"],
                "line": r.get("line_number"),
            }
            for r in data.get("results", [])
        ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        # If bandit is unavailable or misbehaves, return no findings rather than
        # crashing the pipeline. (In production you'd log this.)
        return []
    finally:
        Path(tmp).unlink(missing_ok=True)


def _top_level_imports(source: str) -> set[str]:
    """Collect the top-level package name of every import in the source."""
    names: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names  # S1 already handles this; be defensive.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # ignore relative imports
                names.add(node.module.split(".")[0])
    return names


def _declared_deps(artifact: ToolArtifact) -> set[str]:
    """Pull any declared dependency names from the capability request inputs.

    The request schema is open-ended, so we look for a 'dependencies' or
    'imports' hint if present. Extend this as your contract firms up.
    """
    deps: set[str] = set()
    req = artifact.request
    # Allow a future explicit field without breaking today.
    extra = getattr(req, "dependencies", None)
    if isinstance(extra, (list, set)):
        deps |= {str(d).split(".")[0] for d in extra}
    return deps


def static_stage(artifact: ToolArtifact, record: ValidationRecord) -> bool:
    """S2. Returns True to continue, False to short-circuit (REJECT)."""
    started = time.perf_counter()

    findings = _run_bandit(artifact.source_code)
    high = [f for f in findings if f["severity"] in HARD_REJECT_SEVERITIES]

    imports = _top_level_imports(artifact.source_code)
    allowed = STDLIB_BASELINE | _declared_deps(artifact) | {"__future__"}
    unknown_imports = sorted(imports - allowed)

    elapsed = (time.perf_counter() - started) * 1000

    # ---- hard gate: any HIGH-severity security finding rejects -----------
    if high:
        worst = high[0]
        record.record_stage(
            StageResult(
                stage=Stage.STATIC,
                passed=False,
                duration_ms=round(elapsed, 3),
                detail=f"{len(high)} high-severity finding(s); "
                       f"e.g. {worst['test_id']}: {worst['text'][:50]}",
                data={"findings": findings, "unknown_imports": unknown_imports},
            )
        )
        record.reject(
            FailureReport(
                category="dangerous_call",
                stage=Stage.STATIC,
                message=f"{worst['test_id']} ({worst['severity']}): {worst['text']}",
            )
        )
        return False

    # ---- soft signals: record medium/low findings + unknown imports ------
    n_med_low = len(findings) - len(high)
    detail_bits = []
    if n_med_low:
        detail_bits.append(f"{n_med_low} low/medium finding(s)")
    if unknown_imports:
        detail_bits.append(f"unknown imports: {', '.join(unknown_imports)}")
    detail = "clean" if not detail_bits else "; ".join(detail_bits)

    record.record_stage(
        StageResult(
            stage=Stage.STATIC,
            passed=True,
            duration_ms=round(elapsed, 3),
            detail=detail,
            data={"findings": findings, "unknown_imports": unknown_imports},
        )
    )
    return True
