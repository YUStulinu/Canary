"""Creates Canary's ClickHouse tables (db/schema.sql) through the HTTP interface.

docker compose does this on first start; this is for a server started otherwise (CI).

    python scripts/apply_schema.py [--url http://127.0.0.1:8124] [--user canary --password canary]
"""
from __future__ import annotations

import argparse
import base64
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8124")
    ap.add_argument("--user", default="canary")
    ap.add_argument("--password", default="canary")
    args = ap.parse_args()
    auth = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    for _ in range(60):
        try:
            urllib.request.urlopen(args.url + "/ping", timeout=2)
            break
        except OSError:
            time.sleep(2)
    sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if "CREATE" in s]
    for stmt in statements:
        body = "\n".join(line for line in stmt.splitlines() if not line.strip().startswith("--"))
        req = urllib.request.Request(args.url + "/", data=body.encode(), headers={"Authorization": auth})
        urllib.request.urlopen(req).read()
    print(f"{len(statements)} statements applied")


if __name__ == "__main__":
    main()
