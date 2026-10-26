import type { ReadRequest } from './PaperReader'

/** Open a paper in the reader, over the page it is opened from. */
export function readPaper(req: ReadRequest): void {
  window.dispatchEvent(new CustomEvent('aurora:read-paper', { detail: req }))
}

/** Open the map of a field (zoom out) for a space. */
export function openMap(req: { space: string; mapId?: string; from?: string }): void {
  window.dispatchEvent(new CustomEvent('aurora:open-map', { detail: req }))
}
