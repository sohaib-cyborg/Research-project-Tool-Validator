"""
Walking-skeleton runner.

Usage:
    python run_skeleton.py                 # runs all samples/ artifacts
    python run_skeleton.py samples/foo.py  # runs one artifact
    python run_skeleton.py --probe         # just check Docker/S0 health

Each artifact is wrapped in a minimal CapabilityRequest and pushed through the
S0 -> S1 pipeline. The verdict and per-stage trace are printed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from validator.contracts import CapabilityRequest, ToolArtifact
from validator.pipeline import validate
from validator.sandbox import provision_probe


def _load(path: Path) -> ToolArtifact:
    src = path.read_text()
    # Optional sidecar: samples/foo.json holds the CapabilityRequest example.
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        spec = json.loads(sidecar.read_text())
        req = CapabilityRequest(
            name=spec.get("name", path.stem),
            capability=spec.get("capability", "demo"),
            description=spec.get("description", ""),
            inputs=spec.get("inputs", []),
            outputs=spec.get("outputs", []),
        )
    else:
        req = CapabilityRequest(
            name=path.stem,
            capability="demo",
            description=f"Skeleton test artifact loaded from {path.name}",
        )
    return ToolArtifact(source_code=src, request=req)


def _print_record(path: Path, record) -> None:
    icon = {"ACCEPT": "✓", "REJECT": "✗", "NEEDS_REVIEW": "?", "PENDING": "…"}
    v = record.verdict.value
    print(f"\n{icon.get(v, '·')} {path.name:22s} -> {v}")
    for s in record.stages:
        flag = "pass" if s.passed else "FAIL"
        print(f"     [{flag}] {s.stage.value:14s} {s.duration_ms:>7.2f}ms  {s.detail}")
        # For the execution stage, surface the container proof + logs.
        if s.stage.value == "S3_testgen" and s.data.get("container_id"):
            cid = s.data.get("container_id")
            host = s.data.get("container_hostname")
            print(f"            ⤷ ran in Docker container {cid} (hostname {host})")
            logs = (s.data.get("container_stdout") or "").strip()
            if logs:
                for ln in logs.splitlines():
                    print(f"            │ {ln}")
    if record.failure:
        print(f"     failure: category={record.failure.category!r} "
              f"msg={record.failure.message!r}")


def main(argv: list[str]) -> int:
    if "--probe" in argv:
        print("S0 probe:", json.dumps(provision_probe(), indent=2))
        return 0

    args = [a for a in argv if not a.startswith("--")]
    if args:
        paths = [Path(a) for a in args]
    else:
        paths = sorted(Path("samples").glob("*.py"))

    if not paths:
        print("No artifacts found. Put .py files in samples/ or pass a path.")
        return 1

    for p in paths:
        record = validate(_load(p))
        _print_record(p, record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
