import { bars, compact, share, templateParts, when } from './format.ts'

type Row = Record<string, any>
const app = document.getElementById('app')!

async function api<T = any>(path: string): Promise<T> {
  const r = await fetch(path)
  const body = await r.json()
  if (!r.ok) throw new Error(body.error ?? r.statusText)
  return body as T
}

function h(tag: string, attrs: Record<string, string> = {}, ...children: (Node | string | null | undefined)[]): HTMLElement {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  for (const c of children) if (c != null) el.append(c)
  return el
}

function template(t: string): HTMLElement {
  return h('code', { class: 'template' }, ...templateParts(t).map((p) => (p.variable ? h('span', { class: 'var' }, p.text) : p.text)))
}

function stat(label: string, value: string, note = ''): HTMLElement {
  return h('div', { class: 'stat' }, h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-label' }, label), note ? h('div', { class: 'muted small' }, note) : null)
}

async function overview(source: string) {
  const [o, timeline, incidents] = await Promise.all([
    api('/api/overview'),
    api<Row[]>(`/api/timeline?source=${encodeURIComponent(source)}`),
    api<Row[]>(`/api/incidents?source=${encodeURIComponent(source)}&limit=200`),
  ])
  const pick = (rows: Row[]) => rows.find((r) => r.source === source) ?? {}
  const lines = pick(o.lines), sess = pick(o.sessions), inc = pick(o.incidents)
  app.replaceChildren(
    h('h1', {}, `Source: ${source}`),
    h('p', { class: 'muted' }, lines.first ? `Log time ${when(lines.first)} – ${when(lines.last)} (UTC)` : 'No lines yet.'),
    h('div', { class: 'stats' },
      stat('log lines', compact(Number(lines.lines ?? 0))),
      stat('sessions scored', compact(Number(sess.sessions ?? 0))),
      stat('anomalous sessions', compact(Number(sess.anomalous ?? 0)), share(Number(sess.anomalous ?? 0), Number(sess.sessions ?? 0))),
      stat('incidents', String(inc.incidents ?? 0), `${inc.summarised ?? 0} summarised`),
    ),
    chart(timeline),
    h('h2', {}, 'Incidents, largest first'),
    incidentTable(incidents),
  )
}

function chart(rows: Row[]): HTMLElement {
  const W = 900, H = 120
  const values = rows.map((r) => Number(r.anomalous))
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('viewBox', `0 0 ${W} ${H + 18}`)
  svg.setAttribute('role', 'img')
  svg.setAttribute('aria-label', 'Anomalous sessions per hour of log time')
  bars(values, W, H).forEach((b, i) => {
    const rect = document.createElementNS(svg.namespaceURI, 'rect')
    rect.setAttribute('x', String(b.x))
    rect.setAttribute('y', String(H - b.h))
    rect.setAttribute('width', String(b.w))
    rect.setAttribute('height', String(Math.max(b.h, values[i] ? 1 : 0)))
    rect.setAttribute('rx', b.w > 4 ? '2' : '0')
    const title = document.createElementNS(svg.namespaceURI, 'title')
    title.textContent = `${when(rows[i].hour)}: ${rows[i].anomalous} anomalous of ${rows[i].sessions} sessions`
    rect.append(title)
    svg.append(rect)
  })
  if (rows.length) {
    for (const [i, anchor] of [[0, 'start'], [rows.length - 1, 'end']] as const) {
      const t = document.createElementNS(svg.namespaceURI, 'text')
      t.setAttribute('x', String(i === 0 ? 0 : W))
      t.setAttribute('y', String(H + 14))
      t.setAttribute('text-anchor', anchor)
      t.textContent = when(rows[i].hour)
      svg.append(t)
    }
  }
  return h('figure', { class: 'chart' }, h('figcaption', {}, `Anomalous sessions per hour (peak ${Math.max(0, ...values)}); hover a bar for its count`), svg)
}

function incidentTable(rows: Row[]): HTMLElement {
  if (!rows.length) return h('p', { class: 'muted' }, 'No incidents.')
  return h('table', {},
    h('thead', {}, h('tr', {}, h('th', {}, 'Sessions'), h('th', {}, 'When'), h('th', {}, 'What went wrong'))),
    h('tbody', {}, ...rows.map((r) =>
      h('tr', {},
        h('td', { class: 'num' }, compact(Number(r.sessions))),
        h('td', { class: 'nowrap' }, when(r.first)),
        h('td', {},
          h('a', { href: `#/incident/${r.key}` }, r.summary || 'Not summarised yet'),
          h('div', { class: 'small' }, template(r.templates[0] ?? ''))),
      ),
    )),
  )
}

async function incident(id: string) {
  const { incident: inc, sessions } = await api(`/api/incident?id=${encodeURIComponent(id)}`)
  if (!inc) {
    app.replaceChildren(h('p', {}, 'No such incident.'))
    return
  }
  const evidence = new Set<number>(inc.evidence)
  const alerts: number[] = inc.sample_alerts ?? []
  app.replaceChildren(
    h('p', {}, h('a', { href: `#/source/${inc.source}` }, `← ${inc.source}`)),
    h('h1', {}, inc.summary || 'Not summarised yet'),
    h('p', { class: 'muted' }, `${inc.sessions} session(s), ${when(inc.first)} – ${when(inc.last)} UTC`),
    inc.cause ? h('p', { class: inc.cause.startsWith('unsupported') ? 'warn' : 'cause' }, h('strong', {}, 'Probable cause: '), inc.cause) : '',
    h('p', { class: 'muted small' }, 'Written by a local language model (Qwen2.5-7B) from the lines below; the highlighted lines are the ones it says its answer rests on.'),
    h('h2', {}, 'Events the model did not expect'),
    h('ul', { class: 'templates' }, ...inc.templates.map((t: string) => h('li', {}, template(t)))),
    h('h2', {}, 'Sample lines'),
    h('ol', { class: 'samples' }, ...inc.samples.map((line: string, i: number) =>
      h('li', { class: evidence.has(i) ? 'evidence' : '' },
        evidence.has(i) ? h('span', { class: 'tag' }, '◆ cited') : null,
        alerts[i] ? h('span', { class: 'tag alert' }, '▲ labelled alert in the dataset') : null,
        h('code', {}, line)),
    )),
    h('h2', {}, 'Sessions'),
    h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, 'Session'), h('th', {}, 'Start'), h('th', { class: 'num' }, 'Events'), h('th', { class: 'num' }, 'Unexpected'), h('th', {}, 'Dataset label'))),
      h('tbody', {}, ...sessions.map((s: Row) =>
        h('tr', {}, h('td', {}, h('code', {}, s.session)), h('td', { class: 'nowrap' }, when(s.first)), h('td', { class: 'num' }, String(s.events)),
          h('td', { class: 'num' }, String(s.misses)), h('td', {}, s.truth === 1 ? 'anomaly' : s.truth === 0 ? 'normal' : '–'))),
      )),
  )
}

async function route() {
  const nav = document.getElementById('sources')!
  try {
    const o = await api('/api/overview')
    const sources: string[] = o.lines.map((r: Row) => r.source)
    nav.replaceChildren(...sources.map((s) => h('a', { href: `#/source/${s}` }, s)))
    const [, kind, arg] = location.hash.split('/')
    if (kind === 'incident' && arg) await incident(arg)
    else await overview(kind === 'source' && arg ? decodeURIComponent(arg) : sources[0] ?? '')
  } catch (e) {
    app.replaceChildren(h('p', { class: 'warn' }, `Cannot reach the data: ${(e as Error).message}`))
  }
  window.scrollTo(0, 0)
}

window.addEventListener('hashchange', route)
route()
