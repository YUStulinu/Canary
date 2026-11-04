"""A small ClickHouse client over HTTP (JSONEachRow in and out)."""
from __future__ import annotations

import json
import os

import httpx


class ClickHouse:
    def __init__(self, url: str, user: str, password: str, database: str = "canary"):
        self.url = url.rstrip("/")
        self.auth = (user, password)
        self.database = database
        self.client = httpx.Client(timeout=300)

    @staticmethod
    def from_env() -> "ClickHouse":
        return ClickHouse(os.environ.get("CANARY_CLICKHOUSE", "http://127.0.0.1:8124"),
                          os.environ.get("CANARY_CLICKHOUSE_USER", "canary"),
                          os.environ.get("CANARY_CLICKHOUSE_PASSWORD", "canary"))

    @staticmethod
    def quote(s: str) -> str:
        return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"

    def query(self, sql: str, body: bytes | None = None) -> str:
        r = self.client.post(self.url, params={"database": self.database, "query": sql if body is not None else None},
                             content=body if body is not None else sql.encode(), auth=self.auth)
        if r.status_code != 200:
            raise RuntimeError(f"ClickHouse: {r.text.strip()[:500]}")
        return r.text

    def rows(self, sql: str) -> list[dict]:
        text = self.query(sql + " FORMAT JSONEachRow")
        return [json.loads(line) for line in text.splitlines() if line]

    def insert(self, table: str, rows: list[dict]) -> None:
        body = "\n".join(json.dumps(r) for r in rows).encode()
        self.query(f"INSERT INTO {table} FORMAT JSONEachRow", body)
