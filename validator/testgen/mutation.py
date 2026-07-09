"""
Type-aware input mutation for test generation (TESTGEN.md step 1).

Deterministic, no LLM. Takes the example input values from a CapabilityRequest
and produces MORE valid inputs by applying per-type operators (à la EvalPlus).

Design decisions (locked in TESTGEN.md):
  * Type detection: prefer the DECLARED type in the request; fall back to
    inferring the type from the example value.
  * Each mutation is TAGGED with the operator that produced it (auditable).
  * JSON-simple values only (numbers, strings, booleans, lists, dicts, None).
    File-path inputs are strings and mutate as strings; the fixture layer
    handles the actual files elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Mutation:
    """One generated input, tagged with what produced it."""
    args: list                 # the argument list to call the function with
    operator: str              # e.g. "seed", "int:zero", "str:empty"
    source_index: int          # which example this was derived from

    def __repr__(self) -> str:
        preview = repr(self.args)
        if len(preview) > 60:
            preview = preview[:57] + "..."
        return f"Mutation({self.operator}, {preview})"


# ---------------------------------------------------------------------------
# Per-type operators. Each returns a list of (operator_tag, new_value) pairs.
# Kept small on purpose: each operator has a clear intent.
# ---------------------------------------------------------------------------
def _mutate_int(v: int) -> list[tuple[str, Any]]:
    return [
        ("int:zero",      0),
        ("int:one",       1),
        ("int:neg_one",  -1),
        ("int:negate",   -v if v != 0 else -1),
        ("int:plus_one",  v + 1),
        ("int:large",     1_000_000),
    ]


def _mutate_float(v: float) -> list[tuple[str, Any]]:
    eps = 1e-6
    return [
        ("float:zero",       0.0),
        ("float:eps_plus",   v + eps),
        ("float:eps_minus",  v - eps),
        ("float:negate",    -v if v != 0 else -1.0),
        ("float:round",      round(v)),
        ("float:large",      1e6),
    ]


def _mutate_bool(v: bool) -> list[tuple[str, Any]]:
    return [("bool:flip", not v)]


def _mutate_str(v: str) -> list[tuple[str, Any]]:
    return [
        ("str:empty",        ""),
        ("str:whitespace",   "   "),
        ("str:trail_space",  v + " "),
        ("str:lead_space",   " " + v),
        ("str:upper",        v.upper()),
        ("str:lower",        v.lower()),
        ("str:long",         v * 100 if v else "x" * 200),
        ("str:unicode",      v + "üñç"),
    ]


def _mutate_list(v: list) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = [
        ("list:empty",       []),
        ("list:reversed",    list(reversed(v))),
        ("list:duplicated",  v + v),
    ]
    if v:
        out.append(("list:single", [v[0]]))
        out.append(("list:drop_last", v[:-1]))
    out.append(("list:long", v * 20 if v else [0] * 50))
    return out


def _mutate_dict(v: dict) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = [("dict:empty", {})]
    if v:
        # remove one key, keep the rest
        k = next(iter(v))
        without = {kk: vv for kk, vv in v.items() if kk != k}
        out.append(("dict:drop_first_key", without))
    # add an extra key
    extra = dict(v)
    extra["_extra_key"] = None
    out.append(("dict:extra_key", extra))
    return out


def _mutate_none() -> list[tuple[str, Any]]:
    # No obvious mutation for None; skip (returns empty)
    return []


# ---------------------------------------------------------------------------
# Type detection: prefer declared type, fall back to inferring from the value
# ---------------------------------------------------------------------------
_TYPE_ALIASES = {
    "int": "int", "integer": "int",
    "float": "float", "double": "float", "number": "float",
    "bool": "bool", "boolean": "bool",
    "str": "str", "string": "str", "text": "str",
    "list": "list", "array": "list", "sequence": "list",
    "dict": "dict", "object": "dict", "mapping": "dict",
    "none": "none", "null": "none",
}


def _normalize_declared(t: str | None) -> str | None:
    """Turn 'List[float]', 'list[int]', 'string', etc. into a canonical family."""
    if not isinstance(t, str):
        return None
    head = t.strip().lower().split("[", 1)[0].strip()
    return _TYPE_ALIASES.get(head)


def _infer_from_value(v: Any) -> str:
    if isinstance(v, bool):   return "bool"     # bool before int (bool is int in Python)
    if isinstance(v, int):    return "int"
    if isinstance(v, float):  return "float"
    if isinstance(v, str):    return "str"
    if isinstance(v, list):   return "list"
    if isinstance(v, dict):   return "dict"
    if v is None:             return "none"
    return "unknown"


def _dispatch(family: str, value: Any) -> list[tuple[str, Any]]:
    fn = {
        "int":   _mutate_int,
        "float": _mutate_float,
        "bool":  _mutate_bool,
        "str":   _mutate_str,
        "list":  _mutate_list,
        "dict":  _mutate_dict,
        "none":  lambda _: _mutate_none(),
    }.get(family)
    if fn is None:
        return []
    try:
        return fn(value)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Public entry: mutate one full example (a full argument list)
# ---------------------------------------------------------------------------
def mutate_arg_list(
    example_args: list,
    declared_types: list[str | None] | None = None,
    source_index: int = 0,
) -> list[Mutation]:
    """Produce mutated versions of one example argument list.

    Strategy: mutate one argument at a time, keeping the others at their
    example values. This isolates each mutation's effect (auditable and cheap).

    Returns Mutations WITHOUT the original seed included. Callers who want the
    seed too should prepend it with operator='seed'.
    """
    out: list[Mutation] = []
    declared_types = declared_types or [None] * len(example_args)

    for i, val in enumerate(example_args):
        family = _normalize_declared(declared_types[i] if i < len(declared_types) else None)
        if family is None:
            family = _infer_from_value(val)

        for op_tag, new_val in _dispatch(family, val):
            new_args = list(example_args)
            new_args[i] = new_val
            # Tag records position of the mutated arg too, e.g. "arg0:int:zero"
            tag = f"arg{i}:{op_tag}"
            out.append(Mutation(args=new_args, operator=tag, source_index=source_index))
    return out


# ---------------------------------------------------------------------------
# Convenience: run on a full CapabilityRequest, including the seeds
# ---------------------------------------------------------------------------
def mutate_from_request(request, include_seeds: bool = True) -> list[Mutation]:
    """Turn a CapabilityRequest's example inputs into a mutation pool.

    Reads example inputs from `request.inputs` (Option A schema: each dict has a
    `value` field). Types are pulled from the same dicts' `type` field when
    present, else inferred from values.
    """
    inputs_spec = getattr(request, "inputs", []) or []
    # Group by "example set": treat all inputs[] entries as ONE example call
    # (matches the current Option A shape: inputs = [{name,type,value}, ...]).
    example_args = [it.get("value") for it in inputs_spec if isinstance(it, dict) and "value" in it]
    declared    = [it.get("type")  for it in inputs_spec if isinstance(it, dict) and "value" in it]

    mutations: list[Mutation] = []
    if include_seeds and example_args:
        mutations.append(Mutation(args=example_args, operator="seed", source_index=0))
    mutations.extend(mutate_arg_list(example_args, declared, source_index=0))
    return mutations


def dedupe(mutations: list[Mutation]) -> list[Mutation]:
    """Drop mutations whose args duplicate an earlier one (keeps first)."""
    seen = set()
    out: list[Mutation] = []
    for m in mutations:
        key = repr(m.args)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out
