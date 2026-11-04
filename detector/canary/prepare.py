"""Turns a raw LogHub log into labelled sessions of template ids.

The log is read in order and Drain mines templates online, so every line is assigned using only
the lines before it, as the live detector does. Lines are then grouped into sessions:

  HDFS  one session per block (every block id a line mentions); a block's label comes from
        anomaly_label.csv.
  BGL   fixed one-minute windows over the whole system; a window is anomalous if any of its
        lines carries an alert label.

    python -m canary.prepare HDFS|BGL [--limit N]

writes data/sessions-<name>.npz (offsets, events, start times, labels) and models/drain-<name>.json.
"""
from __future__ import annotations

import argparse
import csv
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from canary.drain import for_dataset
from canary.logformat import BLOCK, PARSERS

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
WINDOW = 60  # seconds, BGL


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=["HDFS", "BGL"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    name = args.name
    path = RAW / ("HDFS_v1/HDFS.log" if name == "HDFS" else "BGL/BGL.log")
    parse = PARSERS[name]
    drain = for_dataset(name)
    sessions: dict[str, list[int]] = defaultdict(list)
    start: dict[str, float] = {}
    alert: dict[str, bool] = defaultdict(bool)
    t0 = time.time()
    n = bad = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = parse(raw)
            if line is None:
                bad += 1
                continue
            gid = drain.add(line.message).id
            if name == "HDFS":
                keys = set(BLOCK.findall(line.message))
            else:
                keys = {str(int(line.timestamp // WINDOW))}
                if line.label != "-":
                    alert[next(iter(keys))] = True
            for k in keys:
                sessions[k].append(gid)
                start.setdefault(k, line.timestamp)
            n += 1
            if n % 1_000_000 == 0:
                print(f"{n:,} lines, {len(drain.groups)} templates, {time.time() - t0:.0f}s", flush=True)
            if args.limit and n >= args.limit:
                break

    if name == "HDFS":
        labels = {r["BlockId"]: r["Label"] == "Anomaly" for r in csv.DictReader(open(RAW / "HDFS_v1/preprocessed/anomaly_label.csv"))}
        keys = [k for k in sessions if k in labels]
        y = [labels[k] for k in keys]
    else:
        keys = list(sessions)
        y = [alert[k] for k in keys]
    order = np.argsort([start[k] for k in keys], kind="stable")
    keys = [keys[i] for i in order]
    lengths = np.array([len(sessions[k]) for k in keys], dtype=np.int64)
    out = ROOT / "data" / f"sessions-{name}.npz"
    np.savez_compressed(out, offsets=np.concatenate([[0], np.cumsum(lengths)]),
                        events=np.concatenate([sessions[k] for k in keys]).astype(np.int32),
                        start=np.array([start[k] for k in keys]), label=np.array([y[i] for i in order]),
                        keys=np.array(keys))
    (ROOT / "models").mkdir(exist_ok=True)
    drain.save(ROOT / "models" / f"drain-{name}.json")
    print(f"{name}: {n:,} lines ({bad} unreadable), {len(drain.groups)} templates, {len(keys):,} sessions, "
          f"{int(np.sum([y[i] for i in order])):,} anomalous, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
