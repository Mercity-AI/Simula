"""Merge analysis.json + judge2.jsonl summaries for pilot arms into one markdown table."""
import collections
import json
import sys
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs" / "pilot"


def judge(run: Path, version: str, max_index: int | None = None) -> dict:
    path = run / "judge2.jsonl"
    if not path.exists():
        return {}
    js = [json.loads(l) for l in path.open() if l.strip()]
    import random
    keep = set(random.Random(7).sample(range(200), 100)) if max_index else None  # the paired first-200 judge sample
    js = [j for j in js if j["version"] == version and "verdict" in j and (keep is None or j["attempt_index"] in keep)]
    n = len(js)
    if not n:
        return {}
    yes = lambda k: sum(1 for j in js if j["verdict"].get(k) == "yes") / n
    return {
        "n": n,
        "product": yes("product_fit"),
        "complexity": yes("complexity_fit"),
        "faithful": sum(1 for j in js if not j["verdict"].get("unsupported_fields") and not j["verdict"].get("missing_constraints")) / n,
        "unsupported": sum(1 for j in js if j["verdict"].get("unsupported_fields")) / n,
        "missing": sum(1 for j in js if j["verdict"].get("missing_constraints")) / n,
        "nonatomic": sum(1 for j in js if j["verdict"].get("non_atomic_fields")) / n,
        "realism": sum(float(j["verdict"].get("realism", 0)) for j in js) / n,
        "coherence": sum(float(j["verdict"].get("coherence", 0)) for j in js) / n,
    }


def main() -> None:
    arms = sys.argv[1:]
    max_index = None
    if arms and arms[0].startswith("--max-index="):
        max_index = int(arms[0].split("=")[1])
        arms = arms[1:]
    print("| arm | accepted | calls/row | $/1K list | $/1K billed | $/1K critic off | size✓ | Vendi | local | Vendi~len | top-3 openers | path leak | product fit | complexity fit | faithful | unsupported | missing | non-atomic | realism | coherence |")
    print("|" + "---|" * 20)
    for arm in arms:
        a = json.loads((RUNS / arm / "analysis.json").read_text())
        for version in ("final", "pre"):
            j = judge(RUNS / arm, version, max_index)
            if version == "pre" and not j:
                continue
            name = arm if version == "final" else f"{arm} (critic off)"
            if version == "final":
                money = (f"{a['accepted']}/{a['attempts']} | {a['calls_per_accepted']:.2f} | {a['list_per_1k_accepted']:.2f} | "
                         f"{a['billed_per_1k_accepted']:.2f} | {a['list_per_1k_critic_off']:.2f} | " if a["billed_per_1k_accepted"] else
                         f"{a['accepted']}/{a['attempts']} | {a['calls_per_accepted']:.2f} | {a['list_per_1k_accepted']:.2f} | – | {a['list_per_1k_critic_off']:.2f} | ")
                shape = (f"{a['shape']['size_in_range']:.0%} | {a['diversity']['vendi']:.1f} | {a['diversity']['local_diversity']:.3f} | "
                         f"{a['diversity_len_matched']['vendi']:.1f} | {a['top3_opener_share']:.0%} | {a['taxonomy_path_leak']:.1%} | ")
            else:
                money = "| | | | | "
                shape = f"{a['shape_pre_critic']['size_in_range']:.0%} | | | | | | "
            q = (f"{j['product']:.0%} | {j['complexity']:.0%} | {j['faithful']:.0%} | {j['unsupported']:.0%} | {j['missing']:.0%} | "
                 f"{j['nonatomic']:.0%} | {j['realism']:.2f} | {j['coherence']:.2f} |") if j else "| | | | | | | |"
            print(f"| {name} | {money}{shape}{q}")


if __name__ == "__main__":
    main()
