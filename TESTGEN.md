# LLM Test Generation — Final Design (S3, LLM strategy)

Status: **design LOCKED. Ready to implement.** Numbers are starting values,
tunable on the mock dataset.

Core problem: there is no ground-truth oracle for an arbitrary synthesized tool.
We build a *pseudo-oracle* by majority vote across independent LLMs, trusting
only test cases where voters agree. Grounded in CodeT (agreement as soft oracle)
and EvalPlus (type-aware input mutation).

---

## 1. What this stage produces

A set of **trusted test cases** — `(input, expected_output)` pairs where a
majority of independent voter models agreed on the output — plus the full voting
record (for auditability). Feeds:
- `test_results` (correctness): pass rate of the tool on trusted tests
- later: `test_quality` (mutation catch rate + coverage over the trusted set)

---

## 2. Locked decisions

| Decision | Locked value |
|---|---|
| Voters | N different SCADS models (default 3), configurable |
| Agreement | majority (> half agree on the same output) |
| Disagreement | input dropped (no trusted answer) |
| Inputs from | (a) type-aware mutation of examples + (b) LLM-generated novel/edge |
| Representation | JSON-simple values passed directly; FILES passed as path strings + fixtures; exotic in-memory objects unsupported |
| File inputs | detected via (1) request declaration, (2) type hint, (3) LLM inference; small sample files created as fixtures in the sandbox (teammate-provided files a future option) |
| Voter method | voters DRY-RUN THE DESCRIPTION (not the code) with explicit step-by-step reasoning, staying independent of the tool |
| Tolerance | INFERRED by each voter from the example input/output pairs in the request (no separate field yet) |
| Batching | one call per voter model; inputs chunked, cap N per call (default 10) |
| Start size | ~20 candidate input/output pairs per tool |
| Reproducibility | trusted set frozen to disk by tool hash; regenerate only via flag |

---

## 3. The flow

```
(A) INPUT GENERATION   -> ~20 candidate inputs (mutation + LLM), JSON-only
(B) VOTING ORACLE      -> batched call per voter; majority output kept, rest dropped
(C) [later] MUTATION KILL -> keep tests that catch injected bugs
(D) [later] SAMPLE KILL   -> cull weak / redundant tests
(E) RUN TOOL           -> pass rate on trusted tests -> test_results
```

This doc specifies (A) and (B). (C)-(E) noted so data shapes line up.

### (A) Input generation — two sources

1. **Type-aware mutation of provided examples** (deterministic, no LLM). Per type:
   - int: +1, -1, 0, negative, large
   - float: ±epsilon, 0.0, negative
   - str: empty, whitespace, long, unicode, case flips
   - list: empty, single, reversed, duplicated, long
   Cheap, guaranteed-valid edge cases.
2. **LLM-generated novel inputs** — model reads description + signature, proposes
   diverse VALID inputs incl. edge cases. INPUTS ONLY at this step.

Combine + de-duplicate -> candidate input pool (target ~20).

### (B) Voting oracle — batched, majority, drop-on-disagreement

For all candidate inputs:
1. Send them in **one batched call per voter model** (inputs chunked, <=10 per
   call). Each voter predicts outputs for the whole chunk from the SPEC — NOT by
   running the tool. Tolerance/precision is inferred by the voter from the
   example pairs in the request.
2. **Majority:** for each input, if > half the voters return the same output
   (after JSON-normalized comparison), that value is the trusted expected output.
3. **Disagreement -> drop.** No majority = no trusted answer = input discarded.

Why voting not one big LLM: a single model is still a guess and can be
confidently wrong. Independent majority agreement is stronger (still imperfect).
This is the honest best in the no-oracle setting — the central research move.

---

## 4. The voter prompt (detailed)

Two prompts: one for input generation (A2), one for the voting oracle (B).

### A2 — Input generation prompt

SYSTEM:
```
You generate test INPUTS for a Python function. You do NOT provide outputs.
Return ONLY valid JSON. All inputs must be valid for the described function
and JSON-serializable (numbers, strings, booleans, lists, dicts, null).
Include a mix of typical inputs and edge cases (empty, boundary, large,
unusual-but-valid).
```

USER (filled from the CapabilityRequest):
```
Function name: {name}
Description: {description}
Signature: {signature}
Example inputs (for format reference): {example_inputs}

Produce {k} diverse, valid input sets as a JSON list. Each element is the
argument list for one call, e.g. [[0, 100], [-40], []]. JSON only.
```

### B — Voting-oracle prompt (one per voter model, batched)

Voters predict the CORRECT output from the DESCRIPTION by DRY-RUNNING it (mental
step-by-step reasoning), NOT by reading or running the tool's code. This keeps
them independent of the tool — they say what the answer SHOULD be, which we then
compare against what the tool DOES. Each item carries its own reasoning so the
model actually thinks per input, and so disagreements are auditable.

SYSTEM:
```
You predict the CORRECT output of a described task for each given input.
Work ONLY from the task DESCRIPTION. You are NOT given the implementation and
must NOT assume one — reason about what the correct answer SHOULD be.
For each input, DRY-RUN the task step by step (trace the logic mentally), then
state the final output. Match the format and numeric precision shown in the
example input/output pairs. Outputs must be JSON-serializable.
Return ONLY valid JSON: a list, one element per input, in order, each of the form
{"reasoning": "<your step-by-step trace>", "output": <predicted output>}.
If you truly cannot determine the output, set "output" to the string "UNKNOWN".
```

USER (filled):
```
Task name: {name}
Description: {description}
Signature: {signature}
Example input/output pairs (define expected format + precision):
{example_pairs}

For each of these inputs, dry-run the task and predict the output, in order:
{inputs_chunk}

Return the JSON list described above. JSON only.
```

Notes:
- Per-item `{"reasoning", "output"}` keeps batching (few calls) while forcing
  per-input thinking — the balance of cost vs quality.
- We KEEP all reasoning (agree and disagree). On disagreement it shows WHY;
  on agreement it lets us check they agreed for the right reason, not a shared
  wrong one. Storage is cheap.
- "UNKNOWN" never wins a majority (not a value), correctly pushing uncertain
  inputs toward drop.
- Precision/tolerance rides in via the example pairs — the voter mirrors the
  format it sees, so the request implicitly controls it.

---

## 5. Output shape

```
GeneratedTests:
  trusted: [ {input, expected_output, agreement:"2/3",
              votes:{model: {reasoning, output}} } ]
  dropped: [ {input, reason:"no_majority" | "unknown",
              votes:{model: {reasoning, output}} } ]
  fixtures: [ {path, kind, note} ]      # sample files created for file-path inputs
  meta:    { n_voters, models, n_candidates, n_trusted, tokens, est_cost }
```

Trusted AND dropped are kept -> voting is auditable, and "fraction of inputs that
got a trusted answer" is itself a measurable result.

---

## 6. Feeds the score (per SCORING.md)

- `test_results` = (tool passes) / (trusted tests run). Only trusted tests count.
- ZERO trusted tests -> `test_results` undefined -> NEEDS_REVIEW (handled).
- `test_quality` (later) = mutation catch rate + coverage over the trusted set.

---

## 7. Config (tunable, one place)

```
N_VOTERS         = 3                 # configurable
VOTER_MODELS     = [qwen_coder, llama70b, gpt_oss]   # from config.ALT_MODELS
AGREEMENT        = "majority"
CANDIDATES_TARGET= 20                # candidate inputs before voting
BATCH_CAP        = 10                # max inputs per voter call
MUTATIONS_PER_SEED = few per example
STUB_VOTER       = for logic dev (canned outputs, no API) [dev aid]
```

---

## 8. Comparison / normalization for "agreement"

JSON-normalized comparison: parse both to JSON values, compare structurally.
Start effectively exact (after JSON parse). Add float tolerance / string
normalization ONLY if real runs show good votes lost to trivial formatting.
Guardrail: too-loose tolerance would let INVALID pairs count as agreeing — keep
tolerance tight and driven by the example pairs' visible precision.

---

## 9. Build order

1. Type-aware mutation (A1) — deterministic, no LLM, testable offline.
2. Stub voter + voting/majority logic (B) — testable offline with canned outputs.
3. LLM input generation (A2) — real SCADS call.
4. Real voter models (B) — real SCADS calls, batched.
5. Freeze-to-disk cache (reproducibility).
6. Wire test_results into the pipeline + score.
