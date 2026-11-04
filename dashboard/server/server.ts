// Serves the dashboard and a small JSON API over Canary's ClickHouse tables.
//
//   node server/server.ts [--port 3030]
//
// Every value from the page reaches ClickHouse as a typed query parameter ({name:Type}), never
// spliced into the SQL text.
import { existsSync, readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { dirname, extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const dist = join(here, '..', 'dist')
const portAt = process.argv.indexOf('--port')
const port = portAt >= 0 ? Number(process.argv[portAt + 1]) : 3030
const CH = process.env.CANARY_CLICKHOUSE ?? 'http://127.0.0.1:8124'
const AUTH = 'Basic ' + Buffer.from(`${process.env.CANARY_CLICKHOUSE_USER ?? 'canary'}:${process.env.CANARY_CLICKHOUSE_PASSWORD ?? 'canary'}`).toString('base64')

async function query(sql: string, params: Record<string, string | number> = {}): Promise<unknown[]> {
  const url = new URL(CH)
  url.searchParams.set('database', 'canary')
  for (const [k, v] of Object.entries(params)) url.searchParams.set(`param_${k}`, String(v))
  const r = await fetch(url, { method: 'POST', body: sql + ' FORMAT JSONEachRow', headers: { Authorization: AUTH } })
  const text = await r.text()
  if (!r.ok) throw new Error(text.slice(0, 300))
  return text.split('\n').filter(Boolean).map((l) => JSON.parse(l))
}

const ROUTES: Record<string, (q: URLSearchParams) => Promise<unknown>> = {
  // Per source: lines, sessions, anomalies, incidents, and the log's time span.
  '/api/overview': async () => {
    const lines = await query(`SELECT source, count() AS lines, toString(min(ts)) AS first, toString(max(ts)) AS last FROM logs GROUP BY source ORDER BY source`)
    const sessions = await query(`SELECT source, count() AS sessions, sum(anomalous) AS anomalous FROM scored_sessions GROUP BY source`)
    const incidents = await query(`SELECT source, count() AS incidents, countIf(summary != '') AS summarised FROM incidents FINAL GROUP BY source`)
    return { lines, sessions, incidents }
  },
  // Anomalous sessions per hour of log time.
  '/api/timeline': (q) =>
    query(
      `SELECT toString(toStartOfHour(first_ts)) AS hour, count() AS sessions, sum(anomalous) AS anomalous
       FROM scored_sessions WHERE source = {source:String} GROUP BY hour ORDER BY hour`,
      { source: q.get('source') ?? '' },
    ),
  '/api/incidents': (q) =>
    query(
      `SELECT toString(id) AS key, source, toString(first_ts) AS first, toString(last_ts) AS last, sessions, templates, summary, cause
       FROM incidents FINAL WHERE source = {source:String} ORDER BY sessions DESC, first_ts LIMIT {limit:UInt32}`,
      { source: q.get('source') ?? '', limit: Math.min(Number(q.get('limit') ?? 100), 1000) },
    ),
  '/api/incident': async (q) => {
    const id = q.get('id') ?? '0'
    const [incident] = await query(
      `SELECT toString(id) AS key, source, signature, toString(first_ts) AS first, toString(last_ts) AS last, sessions, templates,
              samples, sample_alerts, summary, cause, evidence FROM incidents FINAL WHERE id = {id:UInt64}`,
      { id },
    )
    const sessions = await query(
      `SELECT session, toString(first_ts) AS first, events, misses, truth FROM scored_sessions
       WHERE incident = {id:UInt64} ORDER BY first_ts LIMIT 25`,
      { id },
    )
    return { incident: incident ?? null, sessions }
  },
}

const TYPES: Record<string, string> = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }

createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://localhost')
  const route = ROUTES[url.pathname]
  if (route) {
    try {
      const body = JSON.stringify(await route(url.searchParams))
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(body)
    } catch (e) {
      res.writeHead(502, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: (e as Error).message }))
    }
    return
  }
  const path = normalize(url.pathname).replace(/^(\.\.[/\\])+/, '')
  const file = join(dist, path === '/' || path === '\\' ? 'index.html' : path)
  if (!file.startsWith(dist) || !existsSync(file)) {
    res.writeHead(404).end('not found')
    return
  }
  res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream' }).end(readFileSync(file))
}).listen(port, () => console.log(`Canary dashboard on http://localhost:${port}`))
