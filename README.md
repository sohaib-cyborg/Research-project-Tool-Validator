# Tool Validator — Walking Skeleton (S0 + S1)

The minimal end-to-end spine of the Project D validation pipeline. It proves the
state-machine loop works: an artifact comes in, a sandbox is provisioned (S0),
the code is parsed (S1), and a typed verdict comes out. Everything else (S2–S7)
plugs into the same loop later.

## Design decisions baked in

- **Pipeline runs on the host**, talks to Docker via `docker-py`. The tool runs
  *in* a container. (S1 parse runs on the host — fast, no container needed.)
- **Fresh, disposable container per tool** from a pinned base image
  (`python:3.12-slim`). No within-tool checkpointing — we adjudicate, not iterate,
  so immutability gives reproducibility for free.
- **Deterministic core.** No LLM anywhere in the skeleton. (S3 test-gen, the one
  nondeterministic stage, will cache its output to disk so re-runs stay reproducible.)
- **Short-circuit on hard failure.** A `SyntaxError` in S1 rejects immediately and
  never pays for a container run — this is how you measure RQ2 later.
- **Typed everything.** Every state emits a `StageResult`; the run produces a
  `ValidationRecord` with a structured `FailureReport` on rejection. That failure
  report is what goes back to Project C.

## Layout

```
validator/
  contracts.py   # Pydantic models: CapabilityRequest, ToolArtifact, ValidationRecord
  sandbox.py     # S0 — Docker provisioning (graceful host-only fallback)
  parse.py       # S1 — AST compile check (host-side)
  static.py      # S2 — bandit security scan + AST import-allowlist (host-side)
  pipeline.py    # the state machine: S0 -> S1 -> S2 -> verdict
samples/         # clean tools + broken (syntax) + dangerous + hallucinated-import
run_skeleton.py  # CLI runner
```

## Run it

```bash
pip install -r requirements.txt

python run_skeleton.py            # validate everything in samples/
python run_skeleton.py --probe    # just check Docker/S0 health
python run_skeleton.py samples/clean_tool.py   # one artifact
```

On a machine with Docker running, `--probe` should print `"docker": true` and
S0 will show `[pass]`. Without a daemon it degrades to host-only mode and S1
still runs (handy for CI).

## Where the next stages plug in

In `pipeline.py`, inside the `with provision() as sb:` block, after the S1 check:

```python
if not static_stage(artifact, record, sb):   # S2
    return record
tests = testgen_stage(artifact, record)       # S3 (LLM once, cached to disk)
if not execute_stage(artifact, tests, record, sb):  # S4 (runs in sb)
    return record
mutation_stage(artifact, tests, record, sb)   # S5
score_stage(record)                           # S6
schema_stage(artifact, record)                # S7
```

Each is a function with the same shape as `parse_stage`: take the artifact +
record (+ sandbox when it needs to execute), append a `StageResult`, and either
continue or short-circuit to REJECT.

## Verified behaviour

| Artifact | Rejected at | Verdict |
|---|---|---|
| `clean_tool.py`, `clean_email.py` | — | ACCEPT (provisional) |
| `broken_syntax.py` | S1 (`'(' never closed`) | REJECT (`syntax_error`) |
| `broken_indent.py` | S1 (missing indent) | REJECT (`syntax_error`) |
| `broken_unclosed.py` | S1 (unclosed paren) | REJECT (`syntax_error`) |
| `dangerous_syscall.py` | S2 (`B605` HIGH) | REJECT (`dangerous_call`) |
| `hallucinated_import.py` | — (soft signal only) | ACCEPT + `unknown imports` noted |

S2 policy: HIGH-severity security findings hard-reject; medium/low findings and
unknown imports are recorded as data for S6 scoring, not an automatic reject.

Note timing: S1 runs in ~0.1ms, S2 in ~130ms (bandit startup). The ladder pays
the cheap check first and only pays the expensive one if it passes — that per-
stage timing is your RQ2 evidence.

Note: "ACCEPT" here is provisional — it only means S1+S2 passed. Real ACCEPT will
require S3–S6 to pass once they exist.
