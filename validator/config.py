"""
Configuration for the SCADS.ai LLM client.

All the knobs live here so they're easy to change without touching logic.

Reproducibility note: `DEFAULT_MODEL` is an ALIAS (`alias-code`), which SCADS
may repoint to a different underlying model over time. That's convenient for
development. For the FINAL thesis evaluation runs, consider pinning an explicit
model (e.g. "Qwen/Qwen3-Coder-30B-A3B-Instruct") so results are reproducible.
"""
from __future__ import annotations

from dotenv import load_dotenv  
load_dotenv()
import os

# --- SCADS.ai endpoint (OpenAI-compatible) ---------------------------------
BASE_URL = "https://llm.scads.ai/v1"
API_KEY_ENV = "SCADSAI_API_KEY"          # name of the env var holding the key

# --- Model selection -------------------------------------------------------
# alias-code = SCADS-maintained "best code model" pointer (may change over time).
# Swap freely; for RQ3 you'll compare a few of these.
DEFAULT_MODEL = "alias-code"
# Handy explicit alternatives available on the endpoint (for A/B comparison):
ALT_MODELS = {
    "qwen_coder": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "kimi_code": "moonshotai/Kimi-K2.7-Code",
    "llama70b": "meta-llama/Llama-3.3-70B-Instruct",
    "reasoning": "alias-reasoning",
}

# --- Generation defaults ---------------------------------------------------
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.2                 # low: we want stable, not creative

# --- Robustness (retry with exponential backoff) ---------------------------
MAX_RETRIES = 4
BACKOFF_BASE_S = 1.0                       # 1s, 2s, 4s, 8s
REQUEST_TIMEOUT_S = 60


def api_key() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(
            f"Missing SCADS API key: set the {API_KEY_ENV} environment variable."
        )
    return key


# =========================================================================
# S3 test-generation config (v2 — generate-then-judge; see TESTGEN.md)
# =========================================================================

# Opt-in flag: real LLM calls only when explicitly enabled.
RUN_VOTING: bool = True

# --- Generator (Step A: one LLM writes pairs + properties) ---------------
GENERATOR_MODEL: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
N_PAIRS: int = 10          # input/output pairs to ask for
N_PROPERTIES: int = 5      # assertion properties to ask for

# --- Voters / judges (Step B: N different models vote yes/no per item) ---
VOTER_MODELS: list[str] = [
    "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    "meta-llama/Llama-3.3-70B-Instruct",
    "openai/gpt-oss-120b",
]
BATCH_CAP: int = 10        # max items per voter call

# --- Option 2: LLM-invented mutants (comparison arm to real mutmut) -------
LLM_MUTANT_MODEL: str = "Qwen/Qwen3-Coder-30B-A3B-Instruct"
N_LLM_MUTANTS: int = 10
