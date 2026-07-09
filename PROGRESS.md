# Project D — Progress Document

*Validation layer for synthesized LLM-agent tools. Receives a tool from
Project C, decides accept / needs-review / reject, hands a scored record to
Project A.*

---

## PART 1 — Where the project is right now (snapshot)

### Plain summary
We are building the "gatekeeper" that checks whether an AI-generated tool is
safe and correct before it enters the system. It works as a pipeline of checks
that get progressively more expensive: the cheap checks run first and reject
obvious problems before we spend effort on the costly ones. So far the cheap,
deterministic half of the pipeline is built and proven to run. The scoring
system that turns all the checks into one reliability number is designed and
built. We have just connected to the university LLM service (SCADS.ai), which
powers the smarter checks we build next.

### Pipeline stages and status
The validator is a state machine of 8 stages. Status:

| Stage | What it does | Status |
|---|---|---|
| S0 provision | spin up a fresh, isolated Docker container per tool | ✅ done, runs |
| S1 parse | does the code compile? (host-side) | ✅ done, runs |
| S2 static | security scan + import check; dangerous code is rejected | ✅ done, runs |
| S3 replay | run the tool on its own example; reject if it crashes/is wrong | ✅ done, runs |
| S3 LLM tests | LLM generates extra tests beyond the example | ⬜ next |
| S3 semantics | LLM reads code vs request, rates if it means the right thing | ⬜ next |
| S4 execute | (core exists) run generated tests in the container | 🟡 partial |
| S5 mutation | break the code, check tests catch it (test quality) | ⬜ |
| S6 score | combine all signals into one reliability score + verdict | ✅ built, not wired |
| S7 schema | emit an MCP-compliant JSON schema for accepted tools | ⬜ |

### Key design decisions locked
- **Deterministic core, LLM as a subroutine.** The pipeline decides verdicts;
  the LLM only proposes tests/opinions. This is the core novelty vs. the
  baselines (ToolMaker, ToolFactory), which let an LLM judge plausibility.
- **Disposable containers**, one per tool, destroyed after — reproducible, no
  checkpointing.
- **Short-circuit on hard failure** — cheap checks first; a hard failure skips
  the rest (this is how we measure how much cheap checks alone can catch).
- **Pure Python, depth over breadth** (not language-agnostic).
- **Scoring is a hierarchy** (see SCORING.md): correctness = min(test results,
  semantics); test-quality = mutation + coverage; final = weighted sum over
  whatever is built, renormalized. Two hard gates (dangerous call, crash-on-
  example) reject before scoring.

### What still needs proving on the target machine
- The SCADS client wrapper (raw API call already confirmed working).

---

## PART 2 — History (most recent first)

**SCADS.ai client.** Built `config.py` (endpoint, default model `alias-code`,
retry settings) and `scads_client.py` (retry+backoff, text method, JSON method,
typed result with token counts; no caching yet — deferred). Raw API call
confirmed working on target machine. Wrapper verified offline (config, JSON
parsing); real-call test pending.

**S6 reliability scoring.** Designed and documented the full scoring hierarchy
in SCORING.md, then implemented it in `contracts.py` (replaced an earlier flat
version). correctness = min(test-results, semantics); test-quality = mutation +
coverage; final = 0.4/0.3/0.3 weighted sum, renormalized over built components.
Verified with 4 worked cases (including the min rule catching a
tests-pass-but-semantically-wrong tool, and the zero-tests → needs-review case).
Not yet wired into the running pipeline.

**S3 replay.** Built the "replay" check: run the tool on the worked example from
its capability request, inside the container, and reject if it crashes or gives
the wrong answer. Added a reusable container-execution helper (write script in,
run with timeout, capture logs). Proven on the target machine — correct tool
accepted, wrong-output and crash tools rejected, each in its own container with
visible logs.

**S2 static analysis.** Added security scanning (bandit) + an import allowlist.
High-severity findings hard-reject; lesser findings are recorded for scoring.
Proven — a tool with `os.system` is rejected before any container runs.

**S1 parse + S0 sandbox (walking skeleton).** Built the state-machine spine:
provision a Docker container, parse the code, emit a typed verdict. Proven on
the target machine with real Docker.

---

*See CLAUDE.md for detailed technical status and SCORING.md for the scoring
design.*
