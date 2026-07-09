# Reliability Scoring — Design Spec (S6)

Status: **design finalized, implementation done.** This document is the
contract the S6 scoring code is built against. All weights and thresholds are
starting values to be **tuned on the adversarial mock dataset** later.

---

## 1. Purpose

Turn the raw signals collected by the pipeline into a single **reliability
score** in `[0, 1]`, plus an **accept / needs-review / reject** recommendation,
and a per-dimension breakdown so any decision is explainable to Project A and
in the thesis.

---

## 2. Two hard gates run BEFORE scoring

Scoring only ever runs on tools that already cleared these gates (the pipeline
short-circuits to REJECT otherwise):

- **Safety violation** — a dangerous call flagged by static analysis (S2).
- **Crash / wrong answer on the tool's own example** — replay failure (S3).

So the weighted sum never has to "rescue" a dangerous or self-broken tool.

---

## 3. The hierarchy

Three **top-level** scores. Two of them are **derived** from lower signals.

```
FINAL = 0.4·correctness + 0.3·safety + 0.3·test_quality
        (weights renormalized over whichever top-level scores are LIVE)

├── CORRECTNESS = min(test_results, semantics)
│     ├── test_results = pass rate on SURVIVING tests
│     │                  (tests that beat mutation testing; weak ones culled)
│     └── semantics    = average of yes/no rubric checks (rubber-duck LLM)
│
├── SAFETY  (rule-based, deterministic)
│     dangerous call → 0  (also a hard gate upstream)
│     else graded down from 1.0 by resource-behaviour findings
│
└── TEST_QUALITY = 0.5·mutation_catch_rate + 0.5·coverage
```

### Why `min` for correctness (not a weighted sum)

A tool can **pass its tests** yet **do the wrong thing** — the classic
"passes sparse tests but is semantically wrong" case (cf. EvalPlus). A weighted
sum would let such a tool score ~0.6 and survive. `min` says: a tool is only as
correct as its *weakest* correctness signal. If either the tests OR the semantic
check says "wrong", correctness is low. This favours catching false-accepts
(slip rate), which is the metric the thesis most cares about.

Trade-off accepted: because semantics is an LLM judgement, one bad LLM call can
tank a good tool (a false rejection). We accept this and will measure it; if
the false-rejection rate is too high on the mock dataset, revisit this choice.

### Combine rules for the other two

- **test_quality** = weighted sum (0.5 / 0.5) of mutation catch rate + coverage.
  Both are deterministic; a plain average is fine and interpretable.
- **FINAL** = weighted sum (0.4 / 0.3 / 0.3), renormalized over live scores.

---

## 4. How each raw signal becomes a 0..1 number

| Signal | Definition | 0..1 mapping |
|---|---|---|
| `test_results` | pass rate on surviving tests | passed / total_surviving |
| `semantics` | rubber-duck rubric | (# yes answers) / (# rubric checks) |
| `safety` | rule-based | 1.0 clean; −penalty per resource finding; 0 if dangerous |
| `mutation_catch_rate` | mutants killed by surviving tests | killed / total_mutants |
| `coverage` | lines/branches exercised | covered / total |

Safety penalty rule (starting point, tune later): start at 1.0; subtract a
fixed penalty for each medium/low resource-behaviour finding (heavy disk I/O,
os calls, hardware/network access beyond declared). Floor at 0.0. Any
HIGH-severity dangerous call is already a hard-gate REJECT, so within scoring
safety is really grading "how noisy but non-fatal is this tool."

---

## 5. Live vs. not-yet-built (renormalization)

Each sub-score carries a **status**: `real` or `not_evaluated`. Only `real`
scores enter the total; their weights are **renormalized to sum to 1** so
unbuilt components don't silently drag every tool down.

**Live today:** safety (from S2) and the `test_results` half of correctness
(from S3 replay). **Not yet built:** semantics, mutation_catch_rate, coverage.

Because correctness needs `min(test_results, semantics)` and semantics isn't
built yet, correctness in the current build uses `test_results` alone and is
marked partial. When semantics lands, the `min` activates automatically.

---

## 6. Bands (starting thresholds — tune later)

| Final score | Recommendation |
|---|---|
| ≥ 0.75 | ACCEPT |
| 0.45 – 0.75 | NEEDS_REVIEW |
| < 0.45 | REJECT |

**Special case:** if **zero tests survive** culling, `test_results` is
undefined → we cannot judge correctness → **NEEDS_REVIEW** (never guess).

---

## 7. Constants (all tunable, one place)

```
TOP_WEIGHTS         = {correctness: 0.40, safety: 0.30, test_quality: 0.30}
CORRECTNESS         = min(test_results, semantics)
TESTQUALITY_WEIGHTS = {mutation: 0.50, coverage: 0.50}
ACCEPT_THRESHOLD    = 0.75
REJECT_THRESHOLD    = 0.45
SAFETY_PENALTY      = per-finding penalty (starting value TBD on data)
```

---

## 8. Output shape (what Project A receives)

```
ReliabilityScore:
  total: float                     # final renormalized score
  recommendation: ACCEPT|NEEDS_REVIEW|REJECT
  top_scores:  [correctness, safety, test_quality]   # each: value + status
  signals:     [test_results, semantics, mutation, coverage]  # each: value + status
  effective_weights: {...}         # after renormalization (auditable)
  notes: str                       # e.g. which components were excluded
```

Every number that fed the decision is present, so the score is never a black box.
