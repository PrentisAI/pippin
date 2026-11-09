import { api } from '../../api/client'
import type { ReadRequest } from './PaperReader'

/** Open a paper in the reader, over the page it is opened from. */
export function readPaper(req: ReadRequest): void {
  window.dispatchEvent(new CustomEvent('aurora:read-paper', { detail: req }))
}

/** Open a web page in the reader: it is kept in the Library first (the project's when you may add to it), and its
 *  snapshot is taken as it opens. When it cannot be kept, it opens on the web. */
export async function readPage(url: string, space: string, from?: string): Promise<void> {
  try {
    const r = await api.reader.keepPage(url, space)
    readPaper({ paper: r.paper_id, space: r.space, from })
  } catch {
    window.open(url, '_blank', 'noopener')
  }
}

/** Open the map of a field (zoom out) for a space. */
export function openMap(req: { space: string; mapId?: string; from?: string }): void {
  window.dispatchEvent(new CustomEvent('aurora:open-map', { detail: req }))
}
