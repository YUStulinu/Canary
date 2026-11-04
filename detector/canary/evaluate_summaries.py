"""How well the language model points at the lines that matter, on BGL.

BGL labels every line: "-" for normal, an alert category otherwise. Each incident shows the model
up to twelve sample lines and asks for the numbers of the lines its answer rests on. Over the
incidents whose samples hold both alert and normal lines (otherwise there is nothing to choose):

  evidence precision   share of the cited lines that are alerts
  baseline             share of alerts among the samples: what citing lines at random would give
  evidence recall      share of the samples' alert lines that were cited

    python -m canary.evaluate_summaries   writes results/summaries-BGL.json
"""
from __future__ import annotations

import json
from pathlib import Path

from canary.clickhouse import ClickHouse

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    rows = ClickHouse.from_env().rows(
        "SELECT samples, sample_alerts, evidence, cause FROM incidents FINAL WHERE source = 'bgl' AND summary != ''")
    cited = cited_alerts = shown = shown_alerts = used = unsupported = 0
    recall_hits = recall_total = 0
    for r in rows:
        alerts = r["sample_alerts"]
        if r["cause"].startswith("unsupported"):
            unsupported += 1
        if not alerts or all(alerts) or not any(alerts):
            continue
        used += 1
        ev = [e for e in r["evidence"] if e < len(alerts)]
        cited += len(ev)
        cited_alerts += sum(alerts[e] for e in ev)
        shown += len(alerts)
        shown_alerts += sum(alerts)
        recall_hits += sum(alerts[e] for e in ev)
        recall_total += sum(alerts)
    out = {"incidents_summarised": len(rows), "incidents_with_both_kinds_of_line": used,
           "answers_citing_no_line": unsupported,
           "evidence_precision": round(cited_alerts / cited, 3) if cited else None,
           "baseline_random_citation": round(shown_alerts / shown, 3) if shown else None,
           "evidence_recall": round(recall_hits / recall_total, 3) if recall_total else None,
           "lines_cited": cited}
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "summaries-BGL.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
