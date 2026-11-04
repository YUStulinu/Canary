"""The live detector: reads new log lines from ClickHouse, scores sessions, groups incidents.

  1. Lines arrive in seq order (the collector numbers them as they come).
  2. Each line gets its Drain template; Drain keeps learning, starting from the state saved by
     `canary.prepare`, so template ids match the ones the model was trained on.
  3. Lines are grouped into sessions as in training (HDFS blocks, BGL minutes). A session is
     scored when it has been quiet for `idle` seconds of log time, or when the run ends.
  4. DeepLog scores it: a session with at least `misses_to_flag` events outside the model's
     top_k predictions is anomalous.
  5. Anomalous sessions with the same unexpected templates, close in time, form an incident.

    python -m canary.live --source hdfs --dataset HDFS [--follow]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

from canary.clickhouse import ClickHouse
from canary.drain import Drain
from canary.logformat import BLOCK
from canary.models import DeepLog, Sessions

ROOT = Path(__file__).resolve().parents[2]
WINDOW = 60  # BGL windows, as in canary.prepare
SAMPLES = 12  # log lines kept per incident for the reader and the language model


@dataclass
class Open:
    first: float
    last: float
    events: list[int] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # a few, for incident samples
    labels: list[str] = field(default_factory=list)


@dataclass
class Incident:
    id: int
    signature: str
    templates: list[int]
    first: float
    last: float
    sessions: int = 0
    samples: list[str] = field(default_factory=list)
    sample_labels: list[str] = field(default_factory=list)


class Detector:
    def __init__(self, source: str, dataset: str, ch: ClickHouse, idle: float, gap: float, device: str):
        self.source, self.dataset, self.ch, self.idle, self.gap = source, dataset, ch, idle, gap
        self.drain = Drain.load(ROOT / "models" / f"drain-{dataset}.json")
        ck = torch.load(ROOT / "models" / f"deeplog-{dataset}.pt", map_location="cpu", weights_only=False)
        self.model = DeepLog(ck["templates"], ck["window"])
        self.model.load_state_dict(ck["state"])
        self.model.to(device)
        self.top_k, self.misses_to_flag, self.device = ck["top_k"], ck["misses_to_flag"], device
        self.open: dict[str, Open] = {}
        self.incidents: dict[str, Incident] = {}  # the open incident of each signature
        self.all_incidents: dict[int, Incident] = {}
        self.scored: list[dict] = []
        self.labels = self._labels()
        self.stats = defaultdict(int)
        self.clock = 0.0  # the newest log time seen

    def _labels(self) -> dict[str, bool]:
        """The dataset's own labels, stored next to each score for evaluation (never used to score)."""
        if self.dataset != "HDFS":
            return {}
        import csv
        path = ROOT / "data" / "raw" / "HDFS_v1" / "preprocessed" / "anomaly_label.csv"
        return {r["BlockId"]: r["Label"] == "Anomaly" for r in csv.DictReader(open(path))} if path.exists() else {}

    def keys(self, row: dict) -> set[str]:
        if self.dataset == "HDFS":
            return set(BLOCK.findall(row["message"]))
        return {str(int(row["t"] // WINDOW))}

    def add(self, row: dict) -> None:
        gid = self.drain.add(row["message"]).id
        ts = row["t"]
        self.clock = max(self.clock, ts)
        for k in self.keys(row):
            s = self.open.get(k)
            if s is None:
                s = self.open[k] = Open(ts, ts)
            s.last = max(s.last, ts)
            s.events.append(gid)
            if len(s.lines) < 64:
                s.lines.append(f"{row['ts_text']} {row['level']} {row['component']}: {row['message']}")
                s.labels.append(row["label"])
        self.stats["lines"] += 1

    def close_idle(self, everything: bool = False) -> None:
        done = [k for k, s in self.open.items() if everything or self.clock - s.last > self.idle]
        if not done:
            return
        sessions = [self.open.pop(k) for k in done]
        offsets = np.concatenate([[0], np.cumsum([len(s.events) for s in sessions])])
        batch = Sessions(offsets, np.concatenate([s.events for s in sessions]).astype(np.int32),
                         np.zeros(len(sessions), bool), np.array([s.first for s in sessions]))
        misses, unexpected = self._score(batch)
        for k, s, m, unexp in zip(done, sessions, misses, unexpected):
            bad = bool(m >= self.misses_to_flag)
            truth = self.labels.get(k, -1) if self.dataset == "HDFS" else int(any(l not in ("-", "") for l in s.labels))
            inc = self._incident(s, unexp) if bad else None
            self.scored.append({"source": self.source, "session": k, "first_ts": s.first, "last_ts": s.last,
                                "events": len(s.events), "misses": int(m), "anomalous": int(bad),
                                "unexpected": sorted(unexp), "incident": inc.id if inc else 0, "truth": int(truth)})
            self.stats["sessions"] += 1
            self.stats["anomalous"] += bad

    def _score(self, batch: Sessions) -> tuple[np.ndarray, list[Counter]]:
        """Misses per session and how often each template was among the unexpected events."""
        model = self.model
        model.eval()
        xs, ys, owner = [], [], []
        for i in range(len(batch)):
            x, y = model.windows(batch.seq(i))
            xs.append(x)
            ys.append(y)
            owner.append(np.full(len(y), i))
        X = torch.from_numpy(np.concatenate(xs)).long()
        owner = np.concatenate(owner)
        raw = batch.events
        miss = np.zeros(len(X), bool)
        with torch.no_grad():
            for b in range(0, len(X), 16384):
                logits = model(X[b:b + 16384].to(self.device))
                y = torch.from_numpy(np.concatenate(ys)[b:b + 16384]).long().to(self.device)
                rank = (logits > logits.gather(1, y[:, None])).sum(1)
                miss[b:b + 16384] = (rank >= self.top_k).cpu().numpy()
        counts = np.bincount(owner, weights=miss, minlength=len(batch))
        unexpected = [Counter() for _ in range(len(batch))]
        for pos in np.where(miss)[0]:
            unexpected[owner[pos]][int(raw[pos])] += 1
        return counts, unexpected

    def _incident(self, s: Open, unexpected: Counter) -> Incident:
        """Sessions whose most frequent unexpected template is the same, and that come within `gap`
        seconds of the incident's last session, belong to the same incident."""
        top = [t for t, _ in unexpected.most_common(3)]
        signature = str(top[0]) if top else "none"
        inc = self.incidents.get(signature)
        if inc is None or s.first - inc.last > self.gap:
            ident = int(hashlib.sha1(f"{self.source}/{signature}/{s.first}".encode()).hexdigest()[:15], 16)
            inc = Incident(ident, signature, top, s.first, s.last)
            self.incidents[signature] = inc
            self.all_incidents[inc.id] = inc
            self.stats["incidents"] += 1
        inc.sessions += 1
        inc.first, inc.last = min(inc.first, s.first), max(inc.last, s.last)
        # Samples: the lines of the first few sessions, unexpected events first.
        if len(inc.samples) < SAMPLES:
            texts = set(self.drain.template(t) for t in unexpected)
            ordered = sorted(zip(s.lines, s.labels), key=lambda p: not any(_matches(p[0], t) for t in texts))
            for line, label in ordered[: SAMPLES - len(inc.samples)]:
                if line not in inc.samples:
                    inc.samples.append(line)
                    inc.sample_labels.append(label)
        return inc

    def flush(self) -> None:
        """Writes scored sessions, incidents and templates to ClickHouse."""
        if self.scored:
            self.ch.insert("scored_sessions", self.scored)
            self.scored = []
        if self.all_incidents:
            self.ch.insert("incidents", [{
                "id": i.id, "source": self.source, "signature": i.signature, "first_ts": i.first, "last_ts": i.last,
                "sessions": i.sessions, "templates": [self.drain.template(t) for t in i.templates],
                "samples": i.samples, "sample_alerts": [int(l not in ("-", "")) for l in i.sample_labels],
                "summary": "", "cause": "", "evidence": []} for i in self.all_incidents.values()])
        self.ch.insert("templates", [{"source": self.source, "id": g.id, "template": " ".join(g.template)}
                                     for g in self.drain.groups])


def _matches(line: str, template: str) -> bool:
    words = [w for w in template.split() if w != "<*>"]
    return all(w in line for w in words[:4])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--dataset", choices=["HDFS", "BGL"], required=True)
    # HDFS blocks: in the training period 99.9% of blocks never go quiet for more than 12.9 hours
    # (deletions come hours after writes), so a block is scored after 13 quiet hours. BGL windows
    # are complete a minute after they start.
    ap.add_argument("--idle", type=float, default=None, help="seconds of log time after which a session is scored")
    ap.add_argument("--gap", type=float, default=3600, help="seconds between sessions of one incident")
    ap.add_argument("--follow", action="store_true", help="keep waiting for new lines")
    args = ap.parse_args()
    ch = ClickHouse.from_env()
    idle = args.idle if args.idle is not None else {"HDFS": 13 * 3600, "BGL": WINDOW}[args.dataset]
    det = Detector(args.source, args.dataset, ch, idle, args.gap, "cuda" if torch.cuda.is_available() else "cpu")
    last, t0, quiet = 0, time.time(), 0
    while True:
        # (toString before toFloat64: ClickHouse lets a select alias shadow the column it names.)
        rows = ch.rows(f"SELECT seq, toString(ts) AS ts_text, toFloat64(ts) AS t, level, component, message, label "
                       f"FROM logs WHERE source = {ch.quote(args.source)} AND seq > {last} ORDER BY seq LIMIT 200000")
        for r in rows:
            det.add(r)
        if rows:
            last = rows[-1]["seq"]
            det.close_idle()
            quiet = 0
            print(f"{det.stats['lines']:,} lines, {det.stats['sessions']:,} sessions scored, "
                  f"{det.stats['anomalous']:,} anomalous, {det.stats['incidents']} incidents", flush=True)
        else:
            quiet += 1
            if not args.follow and quiet >= 3:
                break
            time.sleep(1)
        det.flush()
    det.close_idle(everything=True)
    det.flush()
    out = {**det.stats, "seconds": round(time.time() - t0, 1)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
