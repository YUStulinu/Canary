"""Splitting raw log lines of each dataset into header fields and the message.

The Rust agent parses the same formats (agent/src/format.rs); both are tested on the same lines.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK = re.compile(r"blk_-?\d+")


@dataclass
class Line:
    timestamp: float  # seconds since the epoch, UTC
    level: str
    component: str
    message: str
    session: str  # HDFS: the block id; BGL: the node
    label: str = ""  # BGL only: "-" for normal lines, an alert category otherwise


def parse_hdfs(line: str) -> Line | None:
    """081109 203518 143 INFO dfs.DataNode$DataXceiver: Receiving block blk_-16089 ..."""
    parts = line.rstrip("\n").split(" ", 5)
    if len(parts) < 6 or len(parts[0]) != 6 or len(parts[1]) != 6:
        return None
    date, time, _pid, level, component, message = parts
    try:
        ts = _epoch(2000 + int(date[:2]), int(date[2:4]), int(date[4:6]), int(time[:2]), int(time[2:4]), int(time[4:6]))
    except ValueError:
        return None
    block = BLOCK.search(message)
    return Line(ts, level, component.rstrip(":"), message, block.group(0) if block else "")


def parse_bgl(line: str) -> Line | None:
    """- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.675872 R02-M1-N0-C:J12-U11 RAS KERNEL INFO message"""
    parts = line.rstrip("\n").split(" ", 9)
    if len(parts) < 9:
        return None
    label, epoch, _date, node, stamp, _node2, _type, component, level = parts[:9]
    message = parts[9] if len(parts) > 9 else ""
    try:
        frac = float("0." + stamp.rsplit(".", 1)[1]) if "." in stamp else 0.0
        ts = int(epoch) + frac
    except ValueError:
        return None
    return Line(ts, level, component, message, node, label)


def _epoch(y: int, mo: int, d: int, h: int, mi: int, s: int) -> float:
    import calendar
    return float(calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)))


PARSERS = {"HDFS": parse_hdfs, "BGL": parse_bgl}
