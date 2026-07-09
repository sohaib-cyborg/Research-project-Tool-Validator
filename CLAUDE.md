# Project D — Tool Validator: Context & Handoff

## What this project is
The validation stage of a self-evolving LLM-agent tool pipeline:
B (gap detection) → C (synthesis) → **D (validation, this repo)** → A (registry).
We receive a synthesized tool artifact + its CapabilityRequest, and emit a
Validated Tool Record: verdict + reliability score + MCP schema, or a structured
failure report on rejection.

This is a master's thesis. The novelty is **oracle-free validation**: deciding
whether a synthesized tool is correct WITHOUT a ground-truth reference answer.
The two baselines (ToolMaker, ToolFactory) both validate weakly with an LLM
"does this look plausible?" judge. We aim to do better with deterministic checks
plus a voting pseudo-oracle across independent LLMs.

## Non-negotiable architecture decisions (do not violate)
1. **Deterministic core, LLM as subroutine.** Pipeline is a plain state machine.
   LLM is called only inside test-generation and semantics; nowhere does an LLM
   decide the verdict.
2. **Disposable containers, not checkpointing.** Fresh container per tool from
   `python:3.12-slim`. Capture results, destroy.
3. **Short-circuit on hard failure.** Cheap checks first; a hard failure skips
   the rest. This ordering is how we measure RQ2.
4. **Pure Python, go deep.** Not language-agnostic. Depth over breadth.
5. **Every stage has the same shape:** function taking (artifact, record,
   [sandbox]) that appends a StageResult and returns True/False.

## Pipeline state machine
S0 provision → S1 parse → S2 static → S3 testgen → S4 execute → S5 mutation →
S6 score → S7 schema. Three terminals: ACCEPT / REJECT / NEEDS_REVIEW.

## Status
- S0 provision  — DONE, proven on real Docker (validator/sandbox.py)
- S1 parse      — DONE (validator/parse.py)
- S2 static     — DONE (validator/static.py; bandit + import allowlist)
- S3 replay     — DONE (validator/testgen.py + replay.py + sandbox_exec.py)
- S3 LLM-tests  — DESIGN LOCKED (TESTGEN.md). Mutation step BUILT
                  (validator/testgen/mutation.py + test_mutation.py). Voting
                  logic (majority pseudo-oracle across independent SCADS
                  models) is next.
- S6 score      — HIERARCHICAL SCHEMA DONE (contracts.py: ReliabilityScore,
                  Signal; design in SCORING.md). NOT YET WIRED into
                  pipeline.py.
- S4, S5, S7    — not started.

## SCADS.ai API (for the S3 LLM stage)
- Endpoint: https://llm.scads.ai/v1  (OpenAI-compatible; `openai` client)
- Auth: bearer key in env var SCADSAI_API_KEY
- Default model: `alias-code` (SCADS-maintained code-model alias)
- Alt models for RQ3 comparison: Qwen3-Coder-30B, Llama-3.3-70B,
  gpt-oss-120b, Kimi-K2.7-Code, etc.
- Rate limits: unknown; handle 429 with retry + exponential backoff.
- For the voting oracle: DIFFERENT models vote (real independence, not
  multiple samples of one). See TESTGEN.md.

## Key files
- validator/contracts.py — Pydantic models + S6 hierarchy (Signal,
  ReliabilityScore, weights, thresholds)
- validator/sandbox.py, sandbox_exec.py — S0 + container-exec helper
- validator/parse.py, static.py — S1, S2
- validator/testgen.py, replay.py — S3 replay strategy
- validator/testgen/mutation.py — S3 type-aware input mutation (deterministic)
- validator/scads_client.py, config.py — SCADS API wrapper
- validator/pipeline.py — the state machine; new stages plug in here

## Design docs
- SCORING.md   — the S6 reliability score hierarchy
- TESTGEN.md   — the LLM test-generation stage
- PROGRESS.md  — snapshot + history

## Adversarial mock dataset (owned by Project D, build in Month 1)
~40 (CapabilityRequest, ToolArtifact, expected_verdict) triples across bug
categories: clean-correct, subtle-logic-bug, dangerous-syscall, wrong-types,
missing-auth, hallucinated-import, wrong-variable-name, resource-exhaustion.
Plus a small (~10-15) hand-verified golden set. Harvest from ToolMaker/TM-Bench,
EvalPlus, ToolFactory.

## Baseline & evaluation
Baseline: ToolMaker (has TM-Bench: 15 tasks, ~124 held-out unit tests). Use
those tests as the answer key to grade OUR verdicts, not as our pipeline.
Headline metrics: slip rate, false-rejection rate, per-category recall,
reliability-score calibration, MCP schema validity rate.
