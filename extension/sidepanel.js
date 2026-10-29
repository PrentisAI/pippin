// The side panel shows your notebook panel from the Pippin console; it is told when what you are reading changes.
const frame = document.getElementById('panel')
const msg = document.getElementById('msg')

chrome.runtime.sendMessage({ type: 'aurora:panel-url' }).then(({ url }) => {
  frame.src = url
  frame.hidden = false
  msg.hidden = true
  frame.addEventListener('error', () => { msg.hidden = false; msg.textContent = `Cannot reach Pippin at ${url}. Check the address in the extension’s options.` })
})

chrome.runtime.onMessage.addListener((m) => {
  if (m.type === 'aurora:refresh' && frame.contentWindow) frame.contentWindow.postMessage({ type: 'aurora:refresh' }, '*')
})

// A PDF open in the browser: the extension cannot draw on Chrome's PDF viewer, so the sentences your saves argue with
// are listed here, each opening its page.
const pdfBox = document.getElementById('pdf')
const pdfHead = document.getElementById('pdfhead')
const pdfList = document.getElementById('pdflist')
const esc = (s) => String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
let shown = ''

const isPdf = (url) => /\.pdf($|[?#])/i.test(url) || /arxiv\.org\/pdf\//.test(url) || /openreview\.net\/pdf/.test(url)

async function checkPdf() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  const url = (tab && tab.url) || ''
  const base = url.split('#')[0]
  if (!isPdf(url)) { pdfBox.hidden = true; shown = ''; return }
  if (base === shown) return
  shown = base
  pdfBox.hidden = false
  pdfHead.textContent = 'Reading this PDF against your notebook…'
  pdfList.innerHTML = ''
  chrome.runtime.sendMessage({ type: 'aurora:api', path: '/memory/context', body: { app: 'Browser', title: tab.title || '', url: base, via: 'extension' } })
  const rel = await chrome.runtime.sendMessage({ type: 'aurora:relate', url: base, title: tab.title || '' })
  const paper = rel && rel.ok && rel.data.paper
  if (!paper) { pdfHead.textContent = 'Pippin does not recognise this PDF as a paper.'; return }
  const r = await chrome.runtime.sendMessage({ type: 'aurora:api', path: `/memory/dissent?paper=${encodeURIComponent(paper.paper_id)}` })
  if (base !== shown) return
  if (!r || !r.ok) { pdfHead.textContent = (r && r.error) || 'Could not reach Pippin'; return }
  const items = r.data.items
  if (!items.length) { pdfHead.textContent = r.data.note || 'Nothing you saved argues with this paper.'; return }
  pdfHead.textContent = `${items.length} sentence${items.length > 1 ? 's' : ''} your saves argue with`
  pdfBox.open = true
  pdfList.innerHTML = items.map((it, i) => `<div class="it" data-i="${i}"><b>p. ${it.page + 1}</b> “${esc(it.quote)}”
    <div class="sub">${esc(it.why)}</div>${it.units.map((u) => `<div class="sub">· ${esc(u.claim)}</div>`).join('')}</div>`).join('')
  pdfList.querySelectorAll('.it').forEach((el) => {
    el.onclick = () => chrome.tabs.update(tab.id, { url: `${base}#page=${items[Number(el.dataset.i)].page + 1}` })
  })
}

checkPdf().catch(() => {})
chrome.tabs.onActivated.addListener(() => checkPdf().catch(() => {}))
chrome.tabs.onUpdated.addListener((id, change) => { if (change.url || change.status === 'complete') checkPdf().catch(() => {}) })
