from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from drawing_rng.use_case_simulator import simulate_use_cases, simulation_summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a Markdown use-case simulation report from a verification JSON payload.")
    ap.add_argument("input", help="JSON file containing verification_result and optionally enrollment_result")
    ap.add_argument("--output", default="results/use_case_simulation_report.md")
    ap.add_argument("--domain", default="example.com")
    args = ap.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    verification = payload.get("verification_result") or payload.get("verification") or payload
    enrollment = payload.get("enrollment_result") or payload.get("enrollment") or {}
    sims = simulate_use_cases(verification, enrollment, args.domain)
    summary = simulation_summary(sims)

    lines = ["# Draw2Seed Use-Case Simulation Report", "", summary["takeaway"], ""]
    for sim in sims:
        lines.append(f"## {sim['title']}")
        lines.append("")
        lines.append(f"- State: `{sim['state']}`")
        lines.append(f"- Outcome: {sim['outcome']}")
        lines.append(f"- Action: {sim['action']}")
        lines.append(f"- Security note: {sim['security_note']}")
        metrics = sim.get("metrics") or {}
        if metrics:
            lines.append(f"- Metrics: `{json.dumps(metrics, sort_keys=True)}`")
        lines.append("")

    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
