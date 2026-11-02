// On every page: what the page says (for saving it) and, when you allow it, what you are reading now
// (the page, your selection), so Pippin can show what in your notebook bears on it.
(() => {
  if (window.__auroraContent) return
  window.__auroraContent = true

  const mainText = () => {
    const main = document.querySelector('article, main, [role="main"]') || document.body
    return (main ? main.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 120000)
  }
  const selection = () => String(window.getSelection ? window.getSelection() : '').trim().slice(0, 4000)

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type === 'aurora:page') {
      reply({ url: location.href, title: document.title, text: mainText(), selection: selection() })
    }
    return false
  })

  let timer = null
  const report = () => {
    if (document.visibilityState !== 'visible') return
    clearTimeout(timer)
    timer = setTimeout(() => {
      chrome.runtime.sendMessage({ type: 'aurora:context', url: location.href, title: document.title,
        selection: selection(), text: mainText().slice(0, 6000) }).catch(() => {})
    }, 1200)
  }
  document.addEventListener('visibilitychange', report)
  window.addEventListener('focus', report)
  document.addEventListener('selectionchange', () => { if (selection().length > 20) report() })
  report()

  // the side panel follows the selection, so a question is about the words selected now
  let told = null
  document.addEventListener('selectionchange', () => {
    clearTimeout(told)
    told = setTimeout(() => chrome.runtime.sendMessage({ type: 'aurora:selection' }).catch(() => {}), 400)
  })

  // "Ask Pippin" beside words you select (not in fields you type in; off in the options or on excluded sites)
  if (location.hostname.endsWith('overleaf.com')) return          // Overleaf has its own
  let chip = null
  const hide = () => { if (chip) chip.style.display = 'none' }
  const inField = (n) => {
    const el = n && (n.nodeType === 1 ? n : n.parentElement)
    return !!(el && el.closest('input, textarea, [contenteditable=""], [contenteditable="true"]'))
  }
  const make = () => {
    const host = document.createElement('div')
    host.style.cssText = 'position:fixed;z-index:2147483646;display:none'
    const root = host.attachShadow({ mode: 'closed' })
    root.innerHTML = `<style>button{font:600 12px system-ui,sans-serif;color:#fff;background:#5b57d1;border:0;border-radius:999px;
      padding:5px 11px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25)}button:hover{background:#4a46bd}</style>
      <button type="button" title="Ask Pippin about the selected words (Alt+Shift+A opens the panel)">Ask Pippin</button>`
    const b = root.querySelector('button')
    b.addEventListener('mousedown', (e) => e.preventDefault())     // keep the selection
    b.addEventListener('click', () => { hide(); chrome.runtime.sendMessage({ type: 'aurora:ask-selection' }).catch(() => {}) })
    document.documentElement.appendChild(host)
    return host
  }
  document.addEventListener('mouseup', (e) => {
    setTimeout(async () => {
      const sel = window.getSelection()
      const words = selection()
      if (!sel || sel.isCollapsed || words.length < 12 || inField(sel.anchorNode) || (chip && chip.contains(e.target))) { hide(); return }
      const { askChip = true, excluded = '' } = await chrome.storage.sync.get(['askChip', 'excluded'])
      const host = location.hostname
      if (!askChip || excluded.split(/[\s,]+/).filter(Boolean).some((h) => host === h || host.endsWith('.' + h))) return
      const r = sel.getRangeAt(0).getBoundingClientRect()
      chip = chip || make()
      chip.style.left = `${Math.max(8, Math.min(window.innerWidth - 110, r.right - 40))}px`
      chip.style.top = `${r.bottom + 8 > window.innerHeight - 40 ? Math.max(8, r.top - 36) : r.bottom + 8}px`
      chip.style.display = 'block'
    }, 10)
  })
  document.addEventListener('mousedown', (e) => { if (chip && e.composedPath()[0] !== chip && !chip.contains(e.target)) hide() })
  window.addEventListener('scroll', hide, { passive: true })
})()
