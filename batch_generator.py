"""
Generator batch runner: for each pipeline survivor, calls the LLM generator
(one real SCADS call per tool) and prints the pairs + properties it produces.

Reads artifacts/pipeline_survivors.json (produced by batch_pipeline.py).
If no survivors, exits gracefully.

Also writes:
  - artifacts/generator_report.json — per-tool structured output

Cost: 1 SCADS call per survivor. Cheap, but not free.

Run:
    python batch_generator.py
"""
from __future__ import annotations

import utils.session
import asyncio
import json
from pathlib import Path

from validator.contracts import CapabilityRequest
from validator.testgen.generator import generate_tests


SURVIVORS_FILE = Path("artifacts/pipeline_survivors.json")
OUT_FILE = Path("artifacts/generator_report.json")


def build_request_and_source(py_path: Path) -> tuple[CapabilityRequest, str]:
    src = py_path.read_text()
    sidecar = py_path.with_suffix(".json")
    spec = json.loads(sidecar.read_text())
    req = CapabilityRequest(
        name=spec["name"],
        capability=spec.get("capability", ""),
        description=spec.get("description", ""),
        inputs=spec.get("inputs", []),
        outputs=spec.get("outputs", []),
    )
    return req, src


async def run_one(py_path: Path) -> dict:
    req, src = build_request_and_source(py_path)
    signature = f"{req.name}(...)"
    result = await generate_tests(
        request=req, artifact_source=src, signature=signature,
    )
    return {
        "tool": py_path.name,
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "n_pairs": len(result.pairs),
        "n_properties": len(result.properties),
        "pairs": [
            {"input": p.input, "expected_output": p.expected_output, "note": p.note}
            for p in result.pairs
        ],
        "properties": [
            {"input_condition": p.input_condition, "assertion": p.assertion, "note": p.note}
            for p in result.properties
        ],
        "parse_error": result.parse_error,
    }


async def main() -> int:
    if not SURVIVORS_FILE.exists():
        print(f"No survivors file at {SURVIVORS_FILE}.")
        print("Run  python batch_pipeline.py  first.")
        return 1

    survivors: list[str] = json.loads(SURVIVORS_FILE.read_text())
    if not survivors:
        print("No pipeline survivors — nothing to run the generator on.")
        return 0

    print(f"Running generator on {len(survivors)} survivor(s)... "
          f"({len(survivors)} SCADS call(s))\n")

    reports: list[dict] = []
    for path_str in survivors:
        path = Path(path_str)
        print(f"--- {path.name} ---")
        try:
            r = await run_one(path)
        except Exception as e:
            r = {"tool": path.name, "error": f"{type(e).__name__}: {e}"}
        reports.append(r)

        if "error" in r:
            print(f"  ERROR: {r['error']}\n")
            continue
        if r.get("parse_error"):
            print(f"  parse_error: {r['parse_error']}\n")
            continue

        print(f"  model: {r['model']}  |  tokens: prompt={r['prompt_tokens']} "
              f"completion={r['completion_tokens']}")
        print(f"  produced {r['n_pairs']} pair(s), {r['n_properties']} property assertion(s)\n")

        for i, p in enumerate(r["pairs"], 1):
            print(f"    pair[{i}] {p['input']} -> {p['expected_output']!r}")
            if p["note"]:
                print(f"           note: {p['note']}")
        for i, p in enumerate(r["properties"], 1):
            cond = f"when [{p['input_condition']}] " if p["input_condition"] else ""
            print(f"    prop[{i}] {cond}assert {p['assertion']}")
            if p["note"]:
                print(f"           note: {p['note']}")
        print()

    OUT_FILE.write_text(json.dumps(reports, indent=2, default=str))
    print(f"Structured report written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
