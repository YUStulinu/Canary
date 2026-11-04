"""Writes a probable-cause summary for each incident with a local language model.

The model (Qwen2.5-7B-Instruct in llama-server) sees the incident's unexpected templates and up to
twelve sample log lines, numbered, and must answer in JSON (held to a schema): a one-sentence
summary, the probable cause, and the numbers of the lines its answer rests on. Line numbers that
do not exist are dropped, and an answer citing no real line is stored as "unsupported" instead of
being shown as a finding.

    python -m canary.summarize --source bgl [--llm http://127.0.0.1:8095] [--limit 200]
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

import httpx

from canary.clickhouse import ClickHouse

PROMPT = """You help an on-call engineer. The anomaly detector flagged an incident in the logs of \
{source}: {sessions} session(s) between {first} and {last} UTC contained events that the model of \
normal behaviour did not expect.

Unexpected event templates (<*> marks a variable part):
{templates}

Sample log lines, numbered:
{samples}

Answer in JSON:
- "summary": one sentence saying what went wrong, in plain words;
- "cause": the most likely cause, or "unclear" if the lines do not show one;
- "evidence": the numbers of the lines that show the problem (only lines listed above).
Do not invent components, hosts or errors that are not in the lines."""

SCHEMA = {"type": "object", "properties": {
    "summary": {"type": "string"}, "cause": {"type": "string"},
    "evidence": {"type": "array", "items": {"type": "integer"}}},
    "required": ["summary", "cause", "evidence"]}


def ask(client: httpx.Client, url: str, inc: dict) -> dict | None:
    samples = "\n".join(f"[{i + 1}] {line[:400]}" for i, line in enumerate(inc["samples"]))
    prompt = PROMPT.format(source=inc["source"], sessions=inc["sessions"], first=inc["first_ts"], last=inc["last_ts"],
                           templates="\n".join(f"- {t}" for t in inc["templates"]), samples=samples)
    body = {"messages": [{"role": "user", "content": prompt}], "temperature": 0, "max_tokens": 300,
            "response_format": {"type": "json_schema", "json_schema": {"name": "incident", "schema": SCHEMA}}}
    try:
        r = client.post(f"{url}/v1/chat/completions", json=body)
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    # Keep only line numbers that exist (shown 1-based, stored 0-based).
    out["evidence"] = sorted({int(e) - 1 for e in out.get("evidence", []) if 1 <= int(e) <= len(inc["samples"])})
    if not out["evidence"]:
        out["cause"] = "unsupported: the answer cited no line of the incident"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--llm", default="http://127.0.0.1:8095")
    ap.add_argument("--limit", type=int, default=200, help="incidents to summarise, largest first")
    args = ap.parse_args()
    ch = ClickHouse.from_env()
    incidents = ch.rows(
        f"SELECT * FROM incidents FINAL WHERE source = {ch.quote(args.source)} AND summary = '' "
        f"ORDER BY sessions DESC LIMIT {args.limit}")
    client = httpx.Client(timeout=300)
    with ThreadPoolExecutor(4) as pool:
        answers = list(pool.map(lambda inc: ask(client, args.llm, inc), incidents))
    rows = []
    for inc, a in zip(incidents, answers):
        if a is None:
            continue
        inc.update(summary=a["summary"].strip(), cause=a["cause"].strip(), evidence=a["evidence"])
        inc.pop("updated", None)
        rows.append(inc)
    if rows:
        ch.insert("incidents", rows)
    print(f"{len(rows)} of {len(incidents)} incidents summarised; "
          f"{sum(1 for r in rows if r['cause'].startswith('unsupported'))} answers cited no line")


if __name__ == "__main__":
    main()
