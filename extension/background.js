// Pippin in the browser: saving into the notebook, saying what you are reading, reading what you follow.
// Everything goes to your Pippin console (its address and, with accounts, a token are set in the options).
import { EXTRACT } from './extractors.js'

const DEFAULTS = { server: 'http://localhost:8200', token: '', reportContext: true, excluded: '', followEnabled: true }

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.sync.get(Object.keys(DEFAULTS))) }
}

async function api(path, options = {}) {
  const s = await settings()
  const headers = { 'Content-Type': 'application/json', ...(s.token ? { Authorization: `Bearer ${s.token}` } : {}) }
  const res = await fetch(`${s.server.replace(/\/$/, '')}/api${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } })
  const body = await res.text()
  let data = null
  try { data = body ? JSON.parse(body) : null } catch { data = null }
  if (!res.ok) throw new Error((data && data.detail) || `${res.status} ${res.statusText}`)
  return data
}

function excluded(url, list) {
  try {
    const host = new URL(url).hostname
    return list.split(/[\s,]+/).filter(Boolean).some((h) => host === h || host.endsWith('.' + h))
  } catch { return true }
}

// ---------------------------------------------------------------------------------------------- saving

async function pageOf(tabId) {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: 'aurora:page' })
  } catch {
    return null           // a page the content script cannot run in (the browser's own pages, the PDF viewer)
  }
}

async function follow(source) {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 1500))
    const s = await api(`/memory/sources/${source.source_id}`)
    if (s.state !== 'pending') return s
  }
  return null
}

async function save(tab, { selection = '', link = '' } = {}) {
  await chrome.action.setBadgeText({ text: '…', tabId: tab.id })
  try {
    let body
    if (link) body = { url: link, via: 'extension' }
    else {
      const page = (await pageOf(tab.id)) || { url: tab.url, title: tab.title, text: '' }
      body = { url: page.url, title: page.title, text: page.text, selection: selection || page.selection || '', via: 'extension' }
      if (/arxiv\.org|doi\.org|openreview\.net|aclanthology\.org/.test(page.url)) body.text = ''   // a paper: read from its PDF
    }
    const done = await follow(await api('/memory/capture', { method: 'POST', body: JSON.stringify(body) }))
    await chrome.action.setBadgeText({ text: done && done.state === 'done' ? '✓' : '!', tabId: tab.id })
    tell(done ? (done.summary || done.error) : 'Still working on it; it will be in your notebook soon.')
    refreshPanel()
    return done
  } catch (err) {
    await chrome.action.setBadgeText({ text: '!', tabId: tab.id })
    tell(`Could not save it: ${err.message}`)
    return null
  } finally {
    setTimeout(() => chrome.action.setBadgeText({ text: '', tabId: tab.id }), 6000)
  }
}

// Ask Pippin about the words selected on a page: the side panel opens (or comes forward) on its conversation,
// which reads the page with the selection when the question is sent.
function askAbout(tab) {
  if (!tab || !chrome.sidePanel || !chrome.sidePanel.open) return
  chrome.sidePanel.open({ tabId: tab.id }).catch(() => {})
  setTimeout(() => chrome.runtime.sendMessage({ type: 'aurora:ask', tabId: tab.id }).catch(() => {}), 300)
}

function tell(message) {
  chrome.notifications.create({ type: 'basic', iconUrl: 'icons/48.png', title: 'Pippin', message: String(message).slice(0, 300) })
}

function refreshPanel() {
  chrome.runtime.sendMessage({ type: 'aurora:refresh' }).catch(() => {})
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: 'save-page', title: 'Save this page to Pippin', contexts: ['page'] })
  chrome.contextMenus.create({ id: 'save-selection', title: 'Save the selection to Pippin', contexts: ['selection'] })
  chrome.contextMenus.create({ id: 'save-link', title: 'Save the link to Pippin', contexts: ['link'] })
  chrome.contextMenus.create({ id: 'follow-page', title: 'Follow this page in Pippin', contexts: ['page'] })
  chrome.contextMenus.create({ id: 'ask-selection', title: 'Ask Pippin about “%s”', contexts: ['selection'] })
  chrome.alarms.create('follows', { periodInMinutes: 15 })
  chrome.alarms.create('alerts', { periodInMinutes: 5 })
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
  // pages already open get the new content script, so they need no reload after an install or update
  chrome.tabs.query({ url: ['http://*/*', 'https://*/*'] }).then((tabs) => tabs.forEach((t) => {
    chrome.scripting.executeScript({ target: { tabId: t.id }, files: ['content.js'] }).catch(() => {})
  }))
})

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === 'save-page') save(tab)
  else if (info.menuItemId === 'save-selection') save(tab, { selection: info.selectionText || '' })
  else if (info.menuItemId === 'save-link') save(tab, { link: info.linkUrl })
  else if (info.menuItemId === 'ask-selection') askAbout(tab)
  else if (info.menuItemId === 'follow-page') {
    try {
      const f = await api('/memory/follows', { method: 'POST', body: JSON.stringify({ url: tab.url, label: tab.title }) })
      tell(`Following “${f.label}”. New posts come into your notebook${f.via === 'extension' ? ', read here in your browser' : ''}.`)
    } catch (err) { tell(`Could not follow it: ${err.message}`) }
  }
})

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command === 'cite-sentence') {
    const [active] = tab ? [tab] : await chrome.tabs.query({ active: true, currentWindow: true })
    chrome.tabs.sendMessage(active.id, { type: 'aurora:cite-now' }).catch(() => {})
    return
  }
  if (command !== 'save-page') return
  const [active] = tab ? [tab] : await chrome.tabs.query({ active: true, currentWindow: true })
  const page = await pageOf(active.id)
  save(active, { selection: (page && page.selection) || '' })
})

// Safari has no side panel: the toolbar button opens the panel in a small window instead
if (!chrome.sidePanel) {
  chrome.action.onClicked.addListener(async () => {
    const s = await settings()
    chrome.windows.create({ url: panelUrl(s), type: 'popup', width: 440, height: 760 })
  })
}

export function panelUrl(s) {
  const q = new URLSearchParams({ via: 'extension' })
  if (s.token) q.set('token', s.token)
  return `${s.server.replace(/\/$/, '')}/panel?${q}`
}

// ---------------------------------------------------------------------------------------------- what you are reading

let lastContext = ''
chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === 'aurora:context') {
    reportContext(msg, sender.tab).catch(() => {})
    return false
  }
  if (msg.type === 'aurora:relate') {
    api(`/memory/relate?url=${encodeURIComponent(msg.url)}&title=${encodeURIComponent(msg.title || '')}`)
      .then((r) => reply({ ok: true, data: r })).catch((err) => reply({ ok: false, error: err.message }))
    return true
  }
  if (msg.type === 'aurora:ask-selection') {    // "Ask Pippin" on the words selected on a page
    askAbout(sender.tab)
    return false
  }
  if (msg.type === 'aurora:save') {
    save(sender.tab).then((s) => reply({ ok: !!s, data: s }))
    return true
  }
  if (msg.type === 'aurora:zotero') {
    api('/memory/zotero', { method: 'POST', body: JSON.stringify({ paper_id: msg.paper_id }) })
      .then((r) => reply({ ok: true, data: r })).catch((err) => reply({ ok: false, error: err.message }))
    return true
  }
  if (msg.type === 'aurora:overleaf') {
    overleaf(sender.tab, msg).then((r) => reply({ ok: true, data: r })).catch((err) => reply({ ok: false, error: err.message }))
    return true
  }
  if (msg.type === 'aurora:api') {          // a call for a content script, with the console's address and token
    api(msg.path, msg.body === undefined ? {} : { method: msg.method || 'POST', body: JSON.stringify(msg.body) })
      .then((r) => reply({ ok: true, data: r })).catch((err) => reply({ ok: false, error: err.message }))
    return true
  }
  if (msg.type === 'aurora:read-follows') {
    readFollows().then((taken) => reply({ ok: true, taken })).catch((err) => reply({ ok: false, error: err.message }))
    return true
  }
  if (msg.type === 'aurora:panel-url') {
    settings().then((s) => reply({ url: panelUrl(s) }))
    return true
  }
  return false
})

async function reportContext(msg, tab) {
  const s = await settings()
  if (!s.reportContext || !tab || excluded(msg.url, s.excluded)) return
  const key = `${msg.url}|${msg.selection}`
  if (key === lastContext) return
  lastContext = key
  await api('/memory/context', { method: 'POST', body: JSON.stringify({
    app: 'Browser', title: msg.title, url: msg.url, selection: msg.selection, text: msg.text, via: 'extension' }) })
  refreshPanel()
}

// ---------------------------------------------------------------------------------------------- reading what you follow

let reading = false
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === 'follows') readFollows().catch(() => {})
  if (a.name === 'alerts') checkAlerts().catch(() => {})
})

// Only what you would regret missing interrupts (a retraction, a paper overlapping your work, an experiment waiting
// for your decision); once each.
async function checkAlerts() {
  const alerts = await api('/memory/alerts?unseen=true&hours=48')
  const { told = [] } = await chrome.storage.local.get('told')
  for (const a of alerts.filter((x) => x.tier === 'now' && !told.includes(x.alert_id))) {
    chrome.notifications.create(`alert:${a.alert_id}`, { type: 'basic', iconUrl: 'icons/48.png', title: a.title,
      message: a.body.slice(0, 300), priority: 2, requireInteraction: a.kind !== 'overlap' })
    if (a.ref && a.ref.url) await chrome.storage.local.set({ [`alertUrl:${a.alert_id}`]: a.ref.url })
    told.push(a.alert_id)
  }
  await chrome.storage.local.set({ told: told.slice(-200) })
}

chrome.notifications.onClicked.addListener(async (id) => {
  if (!id.startsWith('alert:')) return
  const key = `alertUrl:${id.slice(6)}`
  const stored = await chrome.storage.local.get(key)
  if (stored[key]) {                 // a decision: straight to the experiment (it is marked seen once decided)
    chrome.tabs.create({ url: stored[key] })
    chrome.storage.local.remove(key)
    return
  }
  api(`/memory/alerts/${id.slice(6)}/seen`, { method: 'POST', body: '{}' }).catch(() => {})
  const s = await settings()
  chrome.tabs.create({ url: panelUrl(s) })
})

export async function readFollows() {
  const s = await settings()
  if (!s.followEnabled || reading) return 0
  reading = true
  let taken = 0
  try {
    const due = await api('/memory/follows/due?via=extension')
    for (const f of due) {
      let items = []
      let error = ''
      try { items = await readPage(f) } catch (err) { error = String(err.message || err) }
      const r = await api(`/memory/follows/${f.follow_id}/items`, { method: 'POST', body: JSON.stringify({ items, error }) })
      taken += r.taken
    }
    // the X pages the feed reads as you (your timeline, lists): posts, and the papers they link
    const pages = await api('/feed/x/due').catch(() => [])
    for (const f of pages) {
      let items = []
      let error = ''
      try { items = await readPage(f) } catch (err) { error = String(err.message || err) }
      if (!items.length && !error) error = 'nothing could be read there (signed in to X in this browser?)'
      await api('/feed/x/items', { method: 'POST', body: JSON.stringify({ page: f.url, items, error }) }).catch(() => {})
    }
  } finally {
    reading = false
  }
  if (taken) refreshPanel()
  return taken
}

/** Open a followed page in a background tab (you are signed in there), let it render, take its posts. */
async function readPage(f) {
  const tab = await chrome.tabs.create({ url: f.url, active: false })
  try {
    await new Promise((resolve) => {
      const done = (id, info) => { if (id === tab.id && info.status === 'complete') { chrome.tabs.onUpdated.removeListener(done); resolve() } }
      chrome.tabs.onUpdated.addListener(done)
      setTimeout(resolve, 20000)
    })
    await new Promise((r) => setTimeout(r, 3500))          // these sites render their posts after loading
    const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: EXTRACT, args: [f.platform] })
    return (res && res.result) || []
  } finally {
    chrome.tabs.remove(tab.id).catch(() => {})
  }
}


// ---------------------------------------------------------------------------------------------- Overleaf

// The editor is CodeMirror 6 in the page's own world: what is around the cursor is read there, and a citation is
// inserted there, as an edit you can undo with ⌘Z.
function overleafEditor(action, text) {
  const tree = () => [...document.querySelectorAll('[role="treeitem"]')]
  const nameOf = (e) => ((e.querySelector('.item-name, .item-name-button') || e).textContent || '').trim()
  if (action === 'files') return { files: tree().map(nameOf).filter(Boolean) }
  if (action === 'open') {             // open a file of the project in the editor, by its name
    const item = tree().find((e) => nameOf(e) === text)
    if (!item) return { error: `There is no ${text} in the project` }
    ;(item.querySelector('.item-name-button, button, .entity') || item).click()
    return { opened: text }
  }
  const el = document.querySelector('.cm-content')
  const view = el && el.cmView && el.cmView.view
  if (!view) return { error: 'The Overleaf editor is not open' }
  const state = view.state
  const pos = state.selection.main.head
  if (action === 'read') {
    const doc = state.doc.toString()
    const before = doc.slice(Math.max(0, pos - 1500), pos)
    const after = doc.slice(pos, pos + 1500)
    const start = Math.max(before.lastIndexOf('. '), before.lastIndexOf('\n\n'))
    const endA = after.indexOf('. ')
    const endB = after.indexOf('\n\n')
    const end = [endA, endB].filter((x) => x >= 0).reduce((a, b) => Math.min(a, b), after.length)
    const sentence = (before.slice(start + 1) + after.slice(0, end + 1)).replace(/\s+/g, ' ').trim()
    const file = (document.querySelector('.file-tree .selected .item-name, [role="treeitem"][aria-selected="true"]') || {}).textContent || ''
    return { sentence, context: before.slice(-800) + after.slice(0, 400), file: file.trim(), pos }
  }
  if (action === 'insert') {
    // after the sentence's last word, before its full stop when the cursor sits right after one
    let at = pos
    const prev = state.doc.sliceString(Math.max(0, pos - 1), pos)
    if (prev === '.') at = pos - 1
    view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length } })
    return { inserted: text, at }
  }
  if (action === 'doc') {
    const file = (document.querySelector('.file-tree .selected .item-name, [role="treeitem"][aria-selected="true"]') || {}).textContent || ''
    return { file: file.trim(), text: state.doc.toString() }
  }
  if (action === 'append') {
    const end = state.doc.length
    view.dispatch({ changes: { from: end, insert: (state.doc.sliceString(end - 1, end) === '\n' ? '' : '\n') + text + '\n' } })
    return { appended: true }
  }
  return { error: 'unknown action' }
}

async function overleaf(tab, msg) {
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: 'MAIN', func: overleafEditor,
    args: [msg.action, msg.text || ''] })
  const out = (res && res.result) || {}
  if (out.error) throw new Error(out.error)
  if (msg.action === 'read' && out.sentence) {
    const s = await settings()
    if (s.reportContext) api('/memory/context', { method: 'POST', body: JSON.stringify({ app: 'Overleaf', title: tab.title,
      url: tab.url, selection: out.sentence, text: out.context, via: 'overleaf' }) }).catch(() => {})
  }
  return out
}
