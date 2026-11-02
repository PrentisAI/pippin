const DEFAULTS = { server: 'http://localhost:8200', token: '', reportContext: true, excluded: '', followEnabled: true, askChip: true }
const $ = (id) => document.getElementById(id)
const status = (t) => { $('status').textContent = t }

chrome.storage.sync.get(Object.keys(DEFAULTS)).then((saved) => {
  const s = { ...DEFAULTS, ...saved }
  $('server').value = s.server; $('token').value = s.token; $('excluded').value = s.excluded
  $('reportContext').checked = s.reportContext; $('followEnabled').checked = s.followEnabled; $('askChip').checked = s.askChip
})

$('save').onclick = async () => {
  await chrome.storage.sync.set({ server: $('server').value.trim() || DEFAULTS.server, token: $('token').value.trim(),
    excluded: $('excluded').value, reportContext: $('reportContext').checked, followEnabled: $('followEnabled').checked,
    askChip: $('askChip').checked })
  status('Saved.')
}

$('test').onclick = async () => {
  const server = ($('server').value.trim() || DEFAULTS.server).replace(/\/$/, '')
  const token = $('token').value.trim()
  try {
    const r = await fetch(`${server}/api/memory/questions`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    status(r.ok ? `Connected: ${(await r.json()).length} research questions.` : `The console answered ${r.status}: check the token.`)
  } catch (err) { status(`Cannot reach ${server}: ${err.message}`) }
}

$('now').onclick = async () => {
  status('Reading…')
  const r = await chrome.runtime.sendMessage({ type: 'aurora:read-follows' })
  status(r && r.ok ? `Took in ${r.taken} new posts.` : `Could not read them: ${(r && r.error) || 'no answer'}`)
}
