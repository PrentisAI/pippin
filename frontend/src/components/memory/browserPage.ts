/** The page open in the browser, as the extension's side panel knows it: the panel runs in a frame of the extension,
 *  which reads the page (as the person sees it, signed-in pages too) and tells the panel when it changes. */

export interface BrowserPage {
  url: string
  title: string
  /** What the page says (up to about 120,000 characters); empty for the browser's own pages and its PDF viewer. */
  text: string
  selection: string
}

/** In the extension's side panel (not the desktop orb, not the console). */
export const inBrowser = () =>
  window.parent !== window && new URLSearchParams(window.location.search).get('via') === 'extension'

let asked = 0

/** The page in front now, read fresh; null outside the side panel or when the extension does not answer. */
export function currentPage(timeout = 4000): Promise<BrowserPage | null> {
  if (!inBrowser()) return Promise.resolve(null)
  const id = ++asked
  return new Promise((resolve) => {
    const done = (p: BrowserPage | null) => { window.removeEventListener('message', on); clearTimeout(t); resolve(p) }
    const on = (e: MessageEvent) => {
      const d = e.data as { type?: string; id?: number; page?: BrowserPage | null }
      if (e.source === window.parent && d && d.type === 'aurora:page' && d.id === id) done(d.page ?? null)
    }
    const t = window.setTimeout(() => done(null), timeout)
    window.addEventListener('message', on)
    window.parent.postMessage({ type: 'aurora:get-page', id }, '*')
  })
}

/** Told when the page in front changes (another tab, a new address, a new selection), and when the person asked
 *  from the page itself ("Ask Pippin" on a selection): `ask` is then true. */
export function onPageChange(cb: (page: BrowserPage | null, ask: boolean) => void): () => void {
  const on = (e: MessageEvent) => {
    const d = e.data as { type?: string; page?: BrowserPage | null; ask?: boolean }
    if (e.source === window.parent && d && d.type === 'aurora:page-changed') cb(d.page ?? null, !!d.ask)
  }
  window.addEventListener('message', on)
  return () => window.removeEventListener('message', on)
}
