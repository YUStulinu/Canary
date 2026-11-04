"""The anomaly models.

  IsolationForest   unsupervised, on how often each template occurs in a session.
  DeepLog           an LSTM that learns, from normal sessions, which template comes next after
                    the last `window` ones (Du, Li, Zheng and Srikumar, CCS 2017); a session is
                    anomalous when a template it shows is not among the `top_k` predictions.
  Supervised        logistic regression on template counts: a reference that sees labels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
import torch


@dataclass
class Sessions:
    offsets: np.ndarray
    events: np.ndarray
    label: np.ndarray
    start: np.ndarray

    def __len__(self) -> int:
        return len(self.label)

    def seq(self, i: int) -> np.ndarray:
        return self.events[self.offsets[i]:self.offsets[i + 1]]

    def subset(self, idx: np.ndarray) -> "Sessions":
        lengths = self.offsets[idx + 1] - self.offsets[idx]
        offsets = np.concatenate([[0], np.cumsum(lengths)])
        events = np.concatenate([self.seq(i) for i in idx]) if len(idx) else np.zeros(0, np.int32)
        return Sessions(offsets, events, self.label[idx], self.start[idx])

    def counts(self, n_templates: int) -> sp.csr_matrix:
        rows = np.repeat(np.arange(len(self)), np.diff(self.offsets))
        ev = np.minimum(self.events, n_templates - 1)
        m = sp.csr_matrix((np.ones(len(ev), np.float32), (rows, ev)), shape=(len(self), n_templates))
        m.sum_duplicates()
        return m


def load(path) -> Sessions:
    d = np.load(path)
    return Sessions(d["offsets"], d["events"], d["label"].astype(bool), d["start"])


def best_f1_threshold(scores: np.ndarray, y: np.ndarray) -> float:
    """The threshold on validation scores with the best F1 (higher score = more anomalous)."""
    best_t, best_f = float(np.max(scores)) + 1, -1.0
    for t in np.unique(np.quantile(scores, np.linspace(0.5, 1.0, 400))):
        pred = scores >= t
        tp = np.sum(pred & y)
        if tp == 0:
            continue
        p, r = tp / pred.sum(), tp / y.sum()
        f = 2 * p * r / (p + r)
        if f > best_f:
            best_t, best_f = float(t), f
    return best_t


class DeepLog(torch.nn.Module):
    def __init__(self, n_templates: int, window: int = 10, hidden: int = 64, layers: int = 2, embed: int = 32):
        super().__init__()
        self.n = n_templates
        self.window = window
        # Index n is padding (the start of a session) and n + 1 stands for rare and unseen
        # templates: rare ones in training teach the model where unusual events are normal.
        self.embed = torch.nn.Embedding(n_templates + 2, embed)
        self.lstm = torch.nn.LSTM(embed, hidden, layers, batch_first=True)
        self.out = torch.nn.Linear(hidden, n_templates + 2)
        self.register_buffer("vocab", torch.arange(n_templates))

    def forward(self, x):
        h, _ = self.lstm(self.embed(x))
        return self.out(h[:, -1])

    def windows(self, seq: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(history, next) pairs over a session, padded at its start."""
        vocab = self.vocab.cpu().numpy()
        mapped = np.where(seq < self.n, vocab[np.minimum(seq, self.n - 1)], self.n + 1)
        s = np.concatenate([np.full(self.window, self.n), mapped])
        idx = np.arange(len(seq))[:, None] + np.arange(self.window)
        return s[idx], s[self.window:]

    def fit(self, train: Sessions, epochs: int, device: str, batch: int = 1024, lr: float = 2e-3,
            min_count: int = 5) -> None:
        freq = np.bincount(train.events, minlength=self.n)
        vocab = np.arange(self.n)
        vocab[freq < min_count] = self.n + 1
        self.vocab = torch.from_numpy(vocab)
        xs, ys = zip(*(self.windows(train.seq(i)) for i in range(len(train))))
        X = torch.from_numpy(np.concatenate(xs)).long()
        Y = torch.from_numpy(np.concatenate(ys)).long()
        # Repeated windows (the common case in normal sessions) are trained on once, weighted.
        keyed = torch.cat([X, Y[:, None]], 1)
        uniq, counts = torch.unique(keyed, dim=0, return_counts=True)
        X, Y, W = uniq[:, :-1], uniq[:, -1], counts.float()
        self.to(device)
        opt = torch.optim.Adam(self.parameters(), lr=lr)
        loss_fn = torch.nn.CrossEntropyLoss(reduction="none")
        for epoch in range(epochs):
            perm = torch.randperm(len(X))
            total = 0.0
            for b in range(0, len(X), batch):
                i = perm[b:b + batch]
                x, y, w = X[i].to(device), Y[i].to(device), W[i].to(device)
                loss = (loss_fn(self(x), y) * w).sum() / w.sum()
                opt.zero_grad()
                loss.backward()
                opt.step()
                total += loss.item() * len(i)
            print(f"  deeplog epoch {epoch + 1}: loss {total / len(X):.4f} ({len(X):,} distinct windows)", flush=True)

    @torch.no_grad()
    def misses(self, sessions: Sessions, ks: list[int], device: str, batch: int = 8192) -> dict[int, np.ndarray]:
        """For each top_k in ks, how many of each session's events were not among the model's
        top_k predictions. The original DeepLog flags a session with one miss; long sessions
        (BGL's busy minutes) need a higher count, chosen on validation."""
        self.eval()
        xs, ys, owner = [], [], []
        for i in range(len(sessions)):
            x, y = self.windows(sessions.seq(i))
            xs.append(x)
            ys.append(y)
            owner.append(np.full(len(y), i))
        X = torch.from_numpy(np.concatenate(xs)).long()
        Y = torch.from_numpy(np.concatenate(ys)).long()
        owner = np.concatenate(owner)
        rank = np.empty(len(Y), dtype=np.int64)
        for b in range(0, len(X), batch):
            logits = self(X[b:b + batch].to(device))
            y = Y[b:b + batch].to(device)
            target = logits.gather(1, y[:, None])
            rank[b:b + batch] = (logits > target).sum(1).cpu().numpy()
        return {k: np.bincount(owner, weights=rank >= k, minlength=len(sessions)) for k in ks}


def chronological_split(n: int, test_share: float = 0.2, valid_share: float = 0.15, seed: int = 22):
    """Sessions are ordered by start time: the latest test_share are the test set. Validation is
    a random share of the earlier period, so it comes from before the test as training does, but
    is not one stretch of time: anomalies come in bursts, and a single stretch can hold almost
    none of them."""
    test_start = int(n * (1 - test_share))
    rng = np.random.default_rng(seed)
    early = rng.permutation(test_start)
    n_valid = int(test_start * valid_share)
    return np.sort(early[n_valid:]), np.sort(early[:n_valid]), np.arange(test_start, n)


def prf(pred: np.ndarray, y: np.ndarray) -> dict:
    tp = int(np.sum(pred & y))
    fp = int(np.sum(pred & ~y))
    fn = int(np.sum(~pred & y))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3), "tp": tp, "fp": fp, "fn": fn}


def log_scale(x):
    return np.log1p(x) if not sp.issparse(x) else x.log1p()


__all__ = ["Sessions", "load", "DeepLog", "chronological_split", "prf", "best_f1_threshold", "log_scale", "math"]
