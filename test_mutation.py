"""
Smoke script for the type-aware mutator.

Runs the mutator on the Celsius->Fahrenheit CapabilityRequest and prints
the resulting mutations, tagged with the operator that produced each one.
Also shows the dispatch on a couple of other types for sanity.

Usage:
    python test_mutation.py
"""
from __future__ import annotations

from validator.contracts import CapabilityRequest
from validator.testgen.mutation import (
    Mutation,
    dedupe,
    mutate_arg_list,
    mutate_from_request,
)


def show(title: str, mutations: list[Mutation]) -> None:
    print(f"\n=== {title} ({len(mutations)} mutations) ===")
    for m in mutations:
        print(f"  {m.operator:28s} -> {m.args}")


def main() -> None:
    # --- Case 1: the Celsius example (list-of-numbers input) --------------
    req = CapabilityRequest(
        name="celsius_to_fahrenheit",
        capability="unit_conversion",
        description="Convert a list of Celsius temperatures to Fahrenheit.",
        inputs=[{"name": "temps", "type": "list[float]", "value": [0, 100]}],
        outputs=[{"name": "result", "type": "list[float]", "value": [32.0, 212.0]}],
    )
    muts = dedupe(mutate_from_request(req))
    show("Celsius->Fahrenheit", muts)

    # --- Case 2: multi-arg call with a string + an int --------------------
    #   Example: greet("Alice", 3) -> "Hi Alice, Hi Alice, Hi Alice"
    muts2 = dedupe(mutate_arg_list(
        example_args=["Alice", 3],
        declared_types=["str", "int"],
        source_index=0,
    ))
    show("greet(name, times)", muts2)

    # --- Case 3: a bool input (small operator set on purpose) -------------
    muts3 = dedupe(mutate_arg_list(
        example_args=[True],
        declared_types=["bool"],
    ))
    show("is_prime(flag)", muts3)


if __name__ == "__main__":
    main()
