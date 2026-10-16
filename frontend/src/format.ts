/** Small display formatters shared by the views. */

/** "$1.23" (cost in US dollars). */
export function formatCost(usd: number | null | undefined): string {
  return `$${(Number(usd) || 0).toFixed(2)}`
}

/** "12m 3s" / "45s" / "2h 5m". */
export function formatDuration(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}h ${m}m`
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

/** Clip `text` to `max` characters, adding an ellipsis. */
export function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}
