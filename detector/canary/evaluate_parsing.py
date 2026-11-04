"""Parsing accuracy of Drain on LogHub-2k, whose 2,000 lines per system carry hand-made templates.

Grouping accuracy (Zhu et al., ICSE-SEIP 2019): a line counts as parsed correctly when the set of
lines in its group is exactly the set of lines sharing its true template.

    python -m canary.evaluate_parsing   writes results/parsing.json
"""
from __future__ import annotations

import csv
import json
import time
from collections import defaultdict
from pathlib import Path

from canary.drain import for_dataset

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "loghub2k"


def grouping_accuracy(truth: list[str], predicted: list[int]) -> float:
    true_groups = defaultdict(set)
    pred_groups = defaultdict(set)
    for i, (t, p) in enumerate(zip(truth, predicted)):
        true_groups[t].add(i)
        pred_groups[p].add(i)
    correct = sum(len(members) for members in pred_groups.values()
                  if members == true_groups[truth[next(iter(members))]])
    return correct / len(truth)


def main() -> None:
    out = {}
    for name in ("HDFS", "BGL"):
        rows = list(csv.DictReader(open(RAW / f"{name}_2k.log_structured.csv", encoding="utf-8")))
        drain = for_dataset(name)
        t0 = time.time()
        predicted = [drain.add(r["Content"]).id for r in rows]
        out[name] = {"lines": len(rows), "true_templates": len({r["EventId"] for r in rows}),
                     "templates_found": len(drain.groups),
                     "grouping_accuracy": round(grouping_accuracy([r["EventId"] for r in rows], predicted), 4),
                     "lines_per_second": round(len(rows) / (time.time() - t0))}
        print(name, out[name])
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "parsing.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
