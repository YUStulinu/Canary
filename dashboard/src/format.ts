// Small helpers for the dashboard, kept apart from the DOM so they can be tested.

/** Highlights the variable parts ("<*>") of a template. */
export function templateParts(template: string): { text: string; variable: boolean }[] {
  return template.split(/(<\*>)/).filter(Boolean).map((text) => ({ text, variable: text === '<*>' }))
}

export function compact(n: number): string {
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)} M`
  if (n >= 1e4) return `${Math.round(n / 1e3)} k`
  return n.toLocaleString('en')
}

export function share(part: number, whole: number): string {
  if (!whole) return '–'
  const p = (100 * part) / whole
  return `${p < 0.1 && p > 0 ? p.toFixed(2) : p.toFixed(1)}%`
}

/** "2008-11-09 20:35:18.000000" -> "9 Nov 2008, 20:35". */
export function when(ts: string): string {
  const d = new Date(ts.replace(' ', 'T').slice(0, 19) + 'Z')
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
}

/** Evenly spaced bars over a time series, for a chart `width` wide. */
export function bars(values: number[], width: number, height: number): { x: number; w: number; h: number }[] {
  const max = Math.max(1, ...values)
  const step = width / Math.max(1, values.length)
  const gap = step > 4 ? 1 : 0
  return values.map((v, i) => ({ x: i * step, w: Math.max(1, step - gap), h: (v / max) * height }))
}
