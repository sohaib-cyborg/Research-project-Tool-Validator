"""
S1 -- Parse (AST compile check).

Runs on the HOST (your choice): fast, no container needed. This is the cheapest
rung on the supervision ladder. A SyntaxError here short-circuits the whole
pipeline straight to REJECT -- there is no point sandboxing code that will not
even compile.

This directly serves RQ2 ("is static analysis alone enough?"): every tool this
stage rejects is one you never had to pay a container for.
"""
from __future__ import annotations

import ast
import time

from .contracts import (
    FailureReport,
    Stage,
    StageResult,
    ToolArtifact,
    ValidationRecord,
)


def parse_stage(artifact: ToolArtifact, record: ValidationRecord) -> bool:
    """Attempt to compile the artifact's source with the AST parser.

    Returns True if it parses (pipeline continues), False if it does not
    (pipeline short-circuits; `record` is marked REJECT with a failure report).
    """
    started = time.perf_counter()
    try:
        tree = ast.parse(artifact.source_code)
        # Cheap bonus signal: how many top-level functions did we find?
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        elapsed = (time.perf_counter() - started) * 1000
        record.record_stage(
            StageResult(
                stage=Stage.PARSE,
                passed=True,
                duration_ms=round(elapsed, 3),
                detail=f"parsed OK; {len(func_names)} function(s) found",
                data={"functions": func_names},
            )
        )
        return True

    except SyntaxError as e:
        elapsed = (time.perf_counter() - started) * 1000
        msg = f"{e.msg} at line {e.lineno}"
        record.record_stage(
            StageResult(
                stage=Stage.PARSE,
                passed=False,
                duration_ms=round(elapsed, 3),
                detail=f"SyntaxError: {msg}",
                data={"lineno": e.lineno, "offset": e.offset},
            )
        )
        record.reject(
            FailureReport(
                category="syntax_error",
                stage=Stage.PARSE,
                message=msg,
            )
        )
        return False
