"""
S3 (replay strategy) -- the cheapest test-generation rung.

Idea in plain terms: the capability request comes with a worked example --
real input values and the expected output. "Replay" just means: call the tool
with those inputs and check it returns that expected output. No LLM needed.

Assumed shape of the request (Option A -- values live inside each dict):

    inputs  = [{"name": "temps", "type": "list[float]", "value": [0, 100]}]
    outputs = [{"name": "result", "type": "list[float]", "value": [32, 212]}]

This module only BUILDS the test (a small runnable Python script). Running it
in the container is done by the execution slice in sandbox_exec.py -- kept
separate so each piece stays testable on its own.
"""
from __future__ import annotations

import ast
import json

from .contracts import ToolArtifact


def infer_entrypoint(artifact: ToolArtifact) -> str | None:
    """Find the function to call: the explicit entrypoint, else the first def."""
    if artifact.entrypoint:
        return artifact.entrypoint
    try:
        tree = ast.parse(artifact.source_code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node.name
    return None


def _ordered_values(items: list[dict]) -> list:
    """Pull the 'value' from each input/output dict, preserving order."""
    return [it["value"] for it in items if "value" in it]


def build_replay_script(artifact: ToolArtifact) -> str | None:
    """Produce a self-contained Python script that runs the replay check.

    Returns None if the request has no usable example (no input values).
    The script prints a single JSON line describing the result, so the runner
    can parse it deterministically regardless of what the tool prints.
    """
    fn = infer_entrypoint(artifact)
    if fn is None:
        return None

    input_values = _ordered_values(artifact.request.inputs)
    output_values = _ordered_values(artifact.request.outputs)
    if not input_values or not output_values:
        return None  # nothing to replay

    # Single expected output is the common case; if several, compare a tuple.
    expected = output_values[0] if len(output_values) == 1 else output_values

    # The script embeds the tool source, then calls the function with the
    # example inputs and compares. Everything is captured into one JSON line.
    args_literal = ", ".join(repr(v) for v in input_values)
    script = f'''\
import json, traceback

{artifact.source_code}

def _replay():
    try:
        actual = {fn}({args_literal})
        expected = {expected!r}
        ok = actual == expected
        return {{"status": "ran", "passed": bool(ok),
                 "actual": repr(actual), "expected": repr(expected)}}
    except Exception as e:
        return {{"status": "error", "passed": False,
                 "error": f"{{type(e).__name__}}: {{e}}",
                 "trace": traceback.format_exc()[-500:]}}

print("__REPLAY__" + json.dumps(_replay()))
'''
    return script


def parse_replay_output(stdout: str) -> dict | None:
    """Extract the JSON result the replay script printed (marked line)."""
    for line in stdout.splitlines():
        if line.startswith("__REPLAY__"):
            try:
                return json.loads(line[len("__REPLAY__"):])
            except json.JSONDecodeError:
                return None
    return None
