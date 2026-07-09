"""
Offline smoke test for the voting tally logic.

Does NOT call SCADS. Feeds hand-crafted VoterRun records into tally_votes()
and verifies the trusted/dropped split behaves correctly across the tricky
cases: full agreement, 2-of-3 majority, 3-way disagreement, and UNKNOWN.

Run:
    python test_voting_offline.py
"""
from validator.testgen.voters import Prediction, VoterRun, UNKNOWN
from validator.testgen.voting import tally_votes


def make_run(model: str, outputs: list) -> VoterRun:
    """Build a fake VoterRun from a list of just-outputs (empty reasoning)."""
    preds = [Prediction(output=o) for o in outputs]
    return VoterRun(model=model, predictions=preds)


def show(label: str, result) -> None:
    print(f"\n=== {label} ===")
    print(f"  trusted:  {len(result.trusted)}")
    for t in result.trusted:
        print(f"    input={t.input}  expected={t.expected_output!r}  agreement={t.agreement}")
    print(f"  dropped:  {len(result.dropped)}")
    for d in result.dropped:
        outs = {m: v['output'] for m, v in d.votes.items()}
        print(f"    input={d.input}  reason={d.reason}  outputs={outs}")
    print(f"  trusted fraction: {result.trusted_fraction:.2f}")


def main() -> None:
    inputs = [
        [[0, 100]],       # everyone agrees: [32, 212]
        [[-40]],          # 2 of 3 agree: [-40]  (one voter guesses wrong)
        [[42]],           # 3-way disagree: everyone says something different
        [[999]],          # everyone says UNKNOWN
        [[7]],            # 2 of 3 agree, 1 says UNKNOWN (still trusted)
    ]

    a = make_run("model-A", [[32, 212], [-40],    [107.6], UNKNOWN, [44.6]])
    b = make_run("model-B", [[32, 212], [-40],    [42],    UNKNOWN, [44.6]])
    c = make_run("model-C", [[32, 212], [-100],   ["oops"], UNKNOWN, UNKNOWN])

    result = tally_votes(inputs, [a, b, c])
    show("3-voter majority (trusted requires 2-of-3)", result)

    # Sanity: expected counts
    assert len(result.trusted) == 3, f"expected 3 trusted, got {len(result.trusted)}"
    assert len(result.dropped) == 2, f"expected 2 dropped, got {len(result.dropped)}"
    reasons = sorted(d.reason for d in result.dropped)
    assert reasons == ["all_unknown", "no_majority"], f"unexpected reasons: {reasons}"
    print("\n[OK] tally passed the 5-case smoke test.")


if __name__ == "__main__":
    main()
