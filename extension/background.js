// AURORA in the browser: saving into the memory, saying what you are reading, reading what you follow.
// Everything goes to your AURORA console (its address and, with accounts, a token are set in the options).
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
    tell(done ? (done.summary || done.error) : 'Still working on it; it will be in your memory soon.')
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

function tell(message) {
  chrome.notifications.create({ type: 'basic', iconUrl: 'icons/48.png', title: 'AURORA', message: String(message).slice(0, 300) })
}

function refreshPanel() {
  chrome.runtime.sendMessage({ type: 'aurora:refresh' }).catch(() => {})
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: 'save-page', title: 'Save this page to AURORA', contexts: ['page'] })
  chrome.contextMenus.create({ id: 'save-selection', title: 'Save the selection to AURORA', contexts: ['selection'] })
  chrome.contextMenus.create({ id: 'save-link', title: 'Save the link to AURORA', contexts: ['link'] })
  chrome.contextMenus.create({ id: 'follow-page', title: 'Follow this page in AURORA', contexts: ['page'] })
  chrome.alarms.create('follows', { periodInMinutes: 15 })
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true })
})

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === 'save-page') save(tab)
  else if (info.menuItemId === 'save-selection') save(tab, { selection: info.selectionText || '' })
  else if (info.menuItemId === 'save-link') save(tab, { link: info.linkUrl })
  else if (info.menuItemId === 'follow-page') {
    try {
      const f = await api('/memory/follows', { method: 'POST', body: JSON.stringify({ url: tab.url, label: tab.title }) })
      tell(`Following “${f.label}”. New posts come into your memory${f.via === 'extension' ? ', read here in your browser' : ''}.`)
    } catch (err) { tell(`Could not follow it: ${err.message}`) }
  }
})

chrome.commands.onCommand.addListener(async (command, tab) => {
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
  if (msg.type === 'aurora:save') {
    save(sender.tab).then((s) => reply({ ok: !!s, data: s }))
    return true
  }
  if (msg.type === 'aurora:zotero') {
    api('/memory/zotero', { method: 'POST', body: JSON.stringify({ paper_id: msg.paper_id }) })
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
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'follows') readFollows().catch(() => {}) })

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
