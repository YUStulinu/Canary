"""Checks the live pipeline against the offline evaluation on the same HDFS test blocks.

After the test period of HDFS.log has gone through agent -> collector -> ClickHouse -> live
detector, this compares the live verdicts with the dataset's labels and with the offline model's
verdicts, for the blocks of the offline test set. A block the live detector scored more than
once (its lines were more than `idle` apart) counts as anomalous if any of its parts was.

    python -m canary.evaluate_live   writes results/live-HDFS.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from canary.clickhouse import ClickHouse
from canary.models import DeepLog, chronological_split, load, prf

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    data = load(ROOT / "data" / "sessions-HDFS.npz")
    keys = np.load(ROOT / "data" / "sessions-HDFS.npz")["keys"]
    _, _, te = chronological_split(len(data))
    test = data.subset(te)
    test_keys = keys[te]

    ck = torch.load(ROOT / "models" / "deeplog-HDFS.pt", map_location="cpu", weights_only=False)
    model = DeepLog(ck["templates"], ck["window"])
    model.load_state_dict(ck["state"])
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    offline = model.misses(test, [ck["top_k"]], device)[ck["top_k"]] >= ck["misses_to_flag"]

    rows = ClickHouse.from_env().rows("SELECT session, anomalous, events FROM scored_sessions WHERE source = 'hdfs'")
    live, parts, events = defaultdict(bool), defaultdict(int), defaultdict(int)
    for r in rows:
        live[r["session"]] |= bool(r["anomalous"])
        parts[r["session"]] += 1
        events[r["session"]] += int(r["events"])
    seen = np.array([k in live for k in test_keys])
    pred = np.array([live.get(k, False) for k in test_keys])
    same_length = np.array([events.get(k, -1) == len(test.seq(i)) for i, k in enumerate(test_keys)])
    split = np.array([parts.get(k, 0) > 1 for k in test_keys])
    out = {
        "test_blocks": len(test_keys),
        "blocks_the_live_detector_scored": int(seen.sum()),
        "blocks_with_all_their_lines": int(same_length.sum()),
        "blocks_scored_in_several_parts": int(split.sum()),
        "live": prf(pred, test.label),
        "offline_same_model": prf(offline, test.label),
        "agreement_with_offline": round(float(np.mean(pred == offline)), 4),
        "agreement_on_blocks_scored_whole": round(float(np.mean(pred[~split] == offline[~split])), 4),
    }
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "live-HDFS.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
