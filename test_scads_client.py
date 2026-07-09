"""
Verify the ScadsClient wrapper against the real SCADS endpoint.
Run on your machine (where the API is reachable and SCADSAI_API_KEY is set):

    python test_scads_client.py
"""
from validator.scads_client import ScadsClient


def main():
    client = ScadsClient()
    print(f"Using model: {client.model}\n")

    # 1. plain text
    r = client.complete_text("Reply with exactly: WRAPPER OK", max_tokens=10)
    print("text ->", repr(r.content))
    print(f"     tokens: prompt={r.prompt_tokens} completion={r.completion_tokens} "
          f"attempts={r.attempts}\n")

    # 2. structured JSON
    parsed, res = client.complete_json(
        'Return a JSON object with keys "tool" and "status". '
        'Set tool to "validator" and status to "ok".',
        max_tokens=60,
    )
    print("json ->", parsed)
    print(f"     tokens: prompt={res.prompt_tokens} completion={res.completion_tokens}")


if __name__ == "__main__":
    main()
