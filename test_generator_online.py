"""
Online smoke test for the generator (Step A of TESTGEN.md v2).

Hits real SCADS with the Celsius example. Costs one API call to the model
pinned in config.GENERATOR_MODEL.

Run:
    python test_generator_online.py

Requires SCADSAI_API_KEY to be set (via .env or environment).
"""
from __future__ import annotations

import asyncio
import json

from validator.contracts import CapabilityRequest
from validator.testgen.generator import generate_tests


CELSIUS_SOURCE = '''\
def celsius_to_fahrenheit(temps):
    """Convert a list of Celsius temperatures to Fahrenheit."""
    return [t * 9 / 5 + 32 for t in temps]
'''


async def main() -> None:
    request = CapabilityRequest(
        name="celsius_to_fahrenheit",
        capability="unit_conversion",
        description="Convert a list of Celsius temperatures to Fahrenheit.",
        inputs=[{"name": "temps", "type": "list[float]", "value": [0, 100]}],
        outputs=[{"name": "result", "type": "list[float]", "value": [32.0, 212.0]}],
    )
    signature = "celsius_to_fahrenheit(temps: list[float]) -> list[float]"

    print(f"Calling generator (model: from config)...\n")
    result = await generate_tests(
        request=request,
        artifact_source=CELSIUS_SOURCE,
        signature=signature,
    )

    if result.parse_error:
        print(f"[FAIL] {result.parse_error}")
        print("\nRaw content:\n", result.raw_content[:500])
        return

    print(f"model: {result.model}")
    print(f"tokens: prompt={result.prompt_tokens} completion={result.completion_tokens}")
    print()

    print(f"=== {len(result.pairs)} input/output pair(s) ===")
    for i, p in enumerate(result.pairs, 1):
        print(f"  [{i}] {p.input} -> {p.expected_output!r}")
        if p.note:
            print(f"      note: {p.note}")

    print(f"\n=== {len(result.properties)} property assertion(s) ===")
    for i, p in enumerate(result.properties, 1):
        cond = f"when [{p.input_condition}] " if p.input_condition else ""
        print(f"  [{i}] {cond}assert {p.assertion}")
        if p.note:
            print(f"      note: {p.note}")

    print("\n[OK] generator produced pairs and properties.")


if __name__ == "__main__":
    asyncio.run(main())
