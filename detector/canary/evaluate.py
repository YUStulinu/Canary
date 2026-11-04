"""Evaluates the anomaly models on HDFS and BGL with a chronological split.

Sessions are ordered by start time; the last 20% are the test. Of the earlier 80%, a random 15%
chooses each model's threshold (or top-k) and the rest trains. A random split would let a model see sessions
from after the test period, which inflates results (Le and Zhang, ICSE 2022). The unsupervised
and semi-supervised models still use the validation labels to pick their threshold; this is said
in the results.

    python -m canary.evaluate HDFS|BGL   writes results/detection-<name>.json and models/
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression

from canary.models import DeepLog, best_f1_threshold, chronological_split, load, log_scale, prf

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=["HDFS", "BGL"])
    ap.add_argument("--epochs", type=int, default=100)
    args = ap.parse_args()
    name = args.name
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(22)
    data = load(ROOT / "data" / f"sessions-{name}.npz")
    tr, va, te = chronological_split(len(data))
    n_templates = int(data.events.max()) + 1
    train, valid, test = data.subset(tr), data.subset(va), data.subset(te)
    print(f"{name}: {len(data):,} sessions, {n_templates} templates; train {len(tr):,} "
          f"({train.label.mean():.1%} anomalous), valid {len(va):,}, test {len(te):,} ({test.label.mean():.1%})", flush=True)
    out = {"sessions": len(data), "templates": n_templates,
           "split": {"train": len(tr), "valid": len(va), "test": len(te),
                     "anomalous_share": {"train": round(float(train.label.mean()), 4), "test": round(float(test.label.mean()), 4)}},
           "models": {}}

    # Templates first seen after the training period cannot be known to a trained model.
    known = int(train.events.max()) + 1 if len(train.events) else 1
    Xtr, Xva, Xte = (log_scale(s.counts(known + 1)) for s in (train, valid, test))

    t0 = time.time()
    forest = IsolationForest(n_estimators=200, random_state=22, n_jobs=-1).fit(Xtr)
    s_va, s_te = -forest.score_samples(Xva), -forest.score_samples(Xte)
    t = best_f1_threshold(s_va, valid.label)
    out["models"]["Isolation Forest (unsupervised; threshold from validation)"] = {
        **prf(s_te >= t, test.label), "seconds": round(time.time() - t0)}

    t0 = time.time()
    deeplog = DeepLog(known)
    normal = train.subset(np.where(~train.label)[0])
    deeplog.fit(normal, args.epochs, device)
    ks = [1, 2, 3, 5, 9, 15]
    m_va, m_te = deeplog.misses(valid, ks, device), deeplog.misses(test, ks, device)
    best_k, best_t, best_f = 1, 1.0, -1.0
    for k in ks:
        for t in sorted(set(np.unique(m_va[k])) - {0})[:200]:
            f = prf(m_va[k] >= t, valid.label)["f1"]
            if f > best_f:
                best_k, best_t, best_f = k, float(t), f
    out["models"]["DeepLog LSTM (trained on normal sessions; top-k from validation)"] = {
        **prf(m_te[best_k] >= best_t, test.label), "top_k": best_k, "misses_to_flag": int(best_t),
        "seconds": round(time.time() - t0)}

    t0 = time.time()
    clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Xtr, train.label)
    p_va, p_te = clf.predict_proba(Xva)[:, 1], clf.predict_proba(Xte)[:, 1]
    t = best_f1_threshold(p_va, valid.label)
    out["models"]["Logistic regression (supervised reference)"] = {**prf(p_te >= t, test.label),
                                                                   "seconds": round(time.time() - t0)}

    for model, r in out["models"].items():
        print(f"  {model}: P {r['precision']} R {r['recall']} F1 {r['f1']}", flush=True)
    (ROOT / "models").mkdir(exist_ok=True)
    # Saved on the CPU, so the model loads on machines without a GPU.
    torch.save({"state": {k: v.cpu() for k, v in deeplog.state_dict().items()}, "templates": known, "window": deeplog.window, "top_k": best_k,
                "misses_to_flag": int(best_t)},
               ROOT / "models" / f"deeplog-{name}.pt")
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / f"detection-{name}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
