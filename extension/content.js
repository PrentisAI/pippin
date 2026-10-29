// On every page: what the page says (for saving it) and, when you allow it, what you are reading now
// (the page, your selection), so Pippin can show what in your memory bears on it.
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
})()
