import json
from pathlib import Path

import numpy as np
import torch

from canary.drain import Drain, for_dataset
from canary.evaluate_parsing import grouping_accuracy
from canary.logformat import PARSERS
from canary.models import DeepLog, Sessions, chronological_split, prf

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "lines.json"


def test_shared_format_fixture():
    for case in json.loads(FIXTURE.read_text(encoding="utf-8")):
        got = PARSERS[case["format"]](case["line"])
        want = case["parsed"]
        if want is None:
            assert got is None, case["line"]
            continue
        assert abs(got.timestamp - want["timestamp"]) < 1e-5
        assert (got.level, got.component, got.session, got.message, got.label) == \
            (want["level"], want["component"], want["session"], want["message"], want["label"])


def test_drain_generalises_variables_and_keeps_shapes_apart():
    d = for_dataset("HDFS")
    a = d.add("Receiving block blk_123 src: /10.0.0.1:5000 dest: /10.0.0.2:50010")
    b = d.add("Receiving block blk_-9 src: /10.0.0.7:41 dest: /10.0.0.8:50010")
    c = d.add("Deleting block blk_5 file /mnt/hadoop/dfs/data/current/subdir1/blk_5")
    assert a.id == b.id != c.id
    assert d.template(a.id) == "Receiving block <*> src: /<*> dest: /<*>"
    # Matching after training does not change any template.
    assert d.match("Receiving block blk_77 src: /1.2.3.4:1 dest: /1.2.3.5:2").id == a.id
    assert d.template(a.id) == "Receiving block <*> src: /<*> dest: /<*>"


def test_drain_state_round_trip(tmp_path):
    d = for_dataset("BGL")
    for m in ["instruction cache parity error corrected", "data TLB error interrupt", "ciod: failed to read message prefix"]:
        d.add(m)
    d.save(tmp_path / "d.json")
    e = Drain.load(tmp_path / "d.json")
    assert [g.template for g in e.groups] == [g.template for g in d.groups]
    assert e.add("data TLB error interrupt").id == 1


def test_grouping_accuracy():
    assert grouping_accuracy(["A", "A", "B"], [0, 0, 1]) == 1.0
    # Over-splitting template A makes its two lines wrong; B is still right.
    assert abs(grouping_accuracy(["A", "A", "B"], [0, 2, 1]) - 1 / 3) < 1e-9


def test_chronological_split_keeps_the_test_last():
    tr, va, te = chronological_split(100)
    assert len(te) == 20 and te.min() == 80
    assert max(tr.max(), va.max()) < 80
    assert not set(tr) & set(va)


def test_prf():
    y = np.array([True, True, False, False])
    assert prf(np.array([True, False, True, False]), y) == {"precision": 0.5, "recall": 0.5, "f1": 0.5, "tp": 1, "fp": 1, "fn": 1}


def test_deeplog_learns_a_normal_sequence_and_flags_a_deviation():
    torch.manual_seed(0)
    normal = [np.array([0, 1, 2, 3] * 3, dtype=np.int32) for _ in range(50)]
    lengths = [len(s) for s in normal]
    train = Sessions(np.concatenate([[0], np.cumsum(lengths)]), np.concatenate(normal), np.zeros(50, bool), np.zeros(50))
    model = DeepLog(4, window=3, hidden=16, layers=1, embed=8)
    model.fit(train, epochs=60, device="cpu", lr=0.02)
    odd = np.array([0, 1, 2, 3, 0, 2, 1, 3], dtype=np.int32)
    test = Sessions(np.array([0, 12, 20]), np.concatenate([normal[0], odd]), np.array([False, True]), np.zeros(2))
    misses = model.misses(test, [1], "cpu")[1]
    assert misses[0] == 0 and misses[1] >= 2
