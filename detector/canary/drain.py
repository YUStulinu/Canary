"""Drain: online log template mining (He, Zhu, Zheng and Lyu, ICWS 2017).

Each message is tokenised and routed down a tree of fixed depth: first by its number of tokens,
then by its first few tokens (a token holding a digit counts as a wildcard, since it is probably
a variable). At the leaf, the message joins the most similar group if the share of positions
whose tokens agree passes a threshold, and the group's template turns the disagreeing positions
into "<*>"; otherwise it starts a new group.

The state (the groups and the tree) can be saved and loaded, so the live detector assigns the
same template ids as training did.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

WILDCARD = "<*>"


@dataclass
class Group:
    id: int
    template: list[str]
    size: int = 0


@dataclass
class Drain:
    depth: int = 4  # tree depth: the length layer, depth - 2 token layers, then the leaves
    similarity: float = 0.5
    max_children: int = 100
    # Variables masked before mining, per dataset: block ids, IPs, numbers and the like.
    masks: list[tuple[str, str]] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    tree: dict = field(default_factory=dict)

    def __post_init__(self):
        self._compiled = [(re.compile(p), r) for p, r in self.masks]

    def tokens(self, message: str) -> list[str]:
        for pattern, repl in self._compiled:
            message = pattern.sub(repl, message)
        return message.strip().split()

    def add(self, message: str) -> Group:
        """Assigns a message to a group (creating or generalising one) and returns it."""
        toks = self.tokens(message)
        leaf = self._leaf(toks, create=True)
        best, score = self._best(leaf, toks)
        if best is not None and score >= self.similarity:
            best.template = [a if a == b else WILDCARD for a, b in zip(best.template, toks)]
            best.size += 1
            return best
        g = Group(len(self.groups), list(toks), 1)
        self.groups.append(g)
        leaf.append(g.id)
        return g

    def match(self, message: str) -> Group | None:
        """The group of a message without changing any group (for messages after training)."""
        toks = self.tokens(message)
        leaf = self._leaf(toks, create=False)
        if leaf is None:
            return None
        best, score = self._best(leaf, toks, exact_wildcards=True)
        return best if best is not None and score >= self.similarity else None

    def _leaf(self, toks: list[str], create: bool) -> list[int] | None:
        node = self.tree.setdefault(str(len(toks)), {}) if create else self.tree.get(str(len(toks)))
        if node is None:
            return None
        for i in range(min(self.depth - 2, len(toks))):
            tok = toks[i]
            key = WILDCARD if any(c.isdigit() for c in tok) else tok
            if key not in node:
                if not create:
                    key = WILDCARD if WILDCARD in node else None
                    if key is None:
                        return None
                elif len(node) >= self.max_children:
                    key = WILDCARD
            node = node.setdefault(key, {}) if create else node[key]
        if create:
            return node.setdefault("_groups", [])
        return node.get("_groups")

    def _best(self, leaf: list[int], toks: list[str], exact_wildcards: bool = False) -> tuple[Group | None, float]:
        best, best_score, best_params = None, -1.0, -1
        for gid in leaf:
            g = self.groups[gid]
            same = params = 0
            for a, b in zip(g.template, toks):
                if a == WILDCARD:
                    params += 1
                    if not exact_wildcards:
                        continue
                    same += 1
                elif a == b:
                    same += 1
            score = same / len(toks) if toks else 1.0
            # Ties go to the more specific template (fewer wildcards), as in the paper.
            if score > best_score or (score == best_score and params < best_params):
                best, best_score, best_params = g, score, params
        return best, best_score

    def template(self, gid: int) -> str:
        return " ".join(self.groups[gid].template)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "depth": self.depth, "similarity": self.similarity, "max_children": self.max_children,
            "masks": self.masks, "groups": [[g.id, g.template, g.size] for g in self.groups], "tree": self.tree}))

    @staticmethod
    def load(path: Path) -> "Drain":
        d = json.loads(path.read_text())
        drain = Drain(d["depth"], d["similarity"], d["max_children"], [tuple(m) for m in d["masks"]])
        drain.groups = [Group(i, t, s) for i, t, s in d["groups"]]
        drain.tree = d["tree"]
        return drain


# Masks per dataset, following the LogHub benchmark's settings for Drain.
MASKS = {
    "HDFS": [(r"blk_-?\d+", "<*>"), (r"(\d+\.){3}\d+(:\d+)?", "<*>")],
    "BGL": [(r"core\.\d+", "<*>")],
}
SETTINGS = {"HDFS": {"depth": 4, "similarity": 0.5}, "BGL": {"depth": 4, "similarity": 0.5}}


def for_dataset(name: str) -> Drain:
    return Drain(masks=MASKS[name], **SETTINGS[name])
