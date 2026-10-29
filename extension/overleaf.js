// In Overleaf: a citation for the sentence you are writing, from your own library (⌥⇧C or the Pippin button), each
// with the passage that backs it; and a check of the whole project's references before you submit.
// Writing into the manuscript (inserting \cite, adding to the .bib) happens only as far as you allowed it in
// Pippin (account button → What Pippin may write); otherwise you get the text to copy.
(() => {
  if (window.__auroraOverleaf) return
  window.__auroraOverleaf = true
  const send = (msg) => chrome.runtime.sendMessage(msg)
  const call = (path, body, method) => send({ type: 'aurora:api', path, body, method })
  const esc = (s) => String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:2147483646'
  const root = host.attachShadow({ mode: 'open' })
  document.documentElement.appendChild(host)
  root.innerHTML = `<style>
    *{box-sizing:border-box;font-family:system-ui,-apple-system,"PingFang SC",sans-serif}
    .fab{display:flex;gap:6px}.fab button{border:0;border-radius:999px;padding:8px 12px;background:#2d6cdf;color:#fff;cursor:pointer;font-size:12.5px;box-shadow:0 4px 14px rgba(0,0,0,.2)}
    .fab button.q{background:#fff;color:#2d6cdf;border:1px solid #2d6cdf}
    .card{position:absolute;right:0;bottom:46px;width:420px;max-height:70vh;overflow:auto;background:#fff;color:#1f2328;border:1px solid #d7dbe3;border-radius:12px;box-shadow:0 8px 28px rgba(0,0,0,.18);padding:14px;font-size:13px;line-height:1.5}
    .head{display:flex;justify-content:space-between;align-items:center}.x{border:0;background:none;font-size:18px;cursor:pointer;color:#667}
    .sub{color:#667;font-size:12px}.c{border:1px solid #e3e6ec;border-radius:10px;padding:8px 10px;margin:8px 0}
    blockquote{margin:6px 0;padding:4px 8px;border-left:3px solid #2d6cdf;color:#445;font-size:12.5px}
    .row{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
    .row button{border:1px solid #2d6cdf;background:#2d6cdf;color:#fff;border-radius:8px;padding:4px 10px;cursor:pointer;font-size:12px}
    .row button.q{background:#fff;color:#2d6cdf}textarea{width:100%;font:11px ui-monospace,monospace}
    .hint{position:absolute;right:0;bottom:46px;width:300px;background:#fff;color:#1f2328;border:1px solid #d7dbe3;border-left:3px solid #2d6cdf;border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.15);padding:8px 10px;font-size:12.5px;line-height:1.45;cursor:pointer}
    .hint .sub{margin-top:2px}
    .bad{color:#b42318}.warn{color:#b45309}h4{margin:10px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#556}
  </style>
  <div class="card" hidden></div><div class="hint" hidden></div>
  <div class="fab"><button class="q" data-a="audit" title="Check the project's references before you submit">Check references</button>
  <button data-a="cite" title="Find a citation for this sentence (⌥⇧C)">Cite this</button></div>`
  const card = root.querySelector('.card')
  const show = (html) => { card.innerHTML = `<div class="head"><b>Pippin</b><button class="x">×</button></div>${html}`; card.hidden = false
    card.querySelector('.x').onclick = () => { card.hidden = true } }

  async function level(write) {
    const r = await call('/me/writes')
    return r && r.ok ? r.data.levels[write] : 'off'
  }

  async function cite() {
    show('<div class="sub">Reading the sentence at your cursor…</div>')
    const r = await send({ type: 'aurora:overleaf', action: 'read' })
    if (!r || !r.ok) return show(`<div class="bad">${esc((r && r.error) || 'Could not read the editor')}</div>`)
    const { sentence, context, file } = r.data
    if (!sentence || sentence.length < 8) return show('<div class="sub">Put the cursor in the sentence that needs a citation.</div>')
    show(`<div class="sub">Looking in your library for what backs:</div><blockquote>${esc(sentence)}</blockquote>`)
    const res = await call('/memory/cite', { sentence, context })
    if (!res || !res.ok) return show(`<div class="bad">${esc((res && res.error) || 'Pippin is not reachable')}</div>`)
    const d = res.data
    const canCite = (await level('overleaf.cite')) !== 'off'
    const canBib = (await level('overleaf.bib')) !== 'off'
    const items = d.candidates.map((c, i) => `<div class="c"><b>${esc(c.title)}</b><div class="sub">${esc(c.byline)} · from ${esc(c.from)}</div>
      <blockquote>${esc(c.quote)}</blockquote><div class="sub">${esc(c.how)}</div>
      <div class="row">${canCite ? `<button data-i="${i}" data-a="ins">Insert \\cite{${esc(c.key)}}</button>` : `<button class="q" data-i="${i}" data-a="copy">Copy \\cite{${esc(c.key)}}</button>`}
      ${file.endsWith('.bib') && canBib ? `<button class="q" data-i="${i}" data-a="bib">Add to this .bib</button>` : `<button class="q" data-i="${i}" data-a="copybib">Copy BibTeX</button>`}</div></div>`).join('')
    show(`<blockquote>${esc(sentence)}</blockquote>${items || `<div class="sub">${esc(d.note)}</div>`}
      ${!canCite && d.candidates.length ? '<div class="sub">Inserting into the manuscript is off; turn on “Insert citations in the manuscript” in Pippin to insert with one click.</div>' : ''}`)
    card.querySelectorAll('button[data-i]').forEach((b) => { b.onclick = async () => {
      const c = d.candidates[Number(b.dataset.i)]
      if (b.dataset.a === 'copy') { await navigator.clipboard.writeText(`\\cite{${c.key}}`); b.textContent = 'Copied' }
      else if (b.dataset.a === 'copybib') { await navigator.clipboard.writeText(c.bibtex); b.textContent = 'Copied: paste it into your .bib' }
      else if (b.dataset.a === 'ins') {
        const done = await send({ type: 'aurora:overleaf', action: 'insert', text: `\\cite{${c.key}}` })
        if (done && done.ok) {
          b.textContent = 'Inserted (⌘Z undoes it)'; b.disabled = true
          call('/me/writes/log', { write: 'overleaf.cite', detail: { key: c.key, sentence }, undo: { kind: 'overleaf', how: 'Undo it in the Overleaf editor (⌘Z).' } })
          if (canBib && !file.endsWith('.bib')) {           // the entry goes into the project's .bib too, if missing
            b.textContent = 'Inserted; adding it to the .bib…'
            const r = await addToBib(c, file)
            b.textContent = r === 'added' ? `Inserted, and added to ${addedTo}` : r === 'there' ? 'Inserted (already in the .bib)'
              : `Inserted. ${r}`
          }
        } else b.textContent = (done && done.error) || 'Could not insert'
      } else if (b.dataset.a === 'bib') {
        const done = await send({ type: 'aurora:overleaf', action: 'append', text: c.bibtex })
        if (done && done.ok) {
          b.textContent = 'Added to the .bib'; b.disabled = true
          call('/me/writes/log', { write: 'overleaf.bib', detail: { key: c.key }, undo: { kind: 'overleaf', how: 'Undo it in the Overleaf editor (⌘Z).' } })
        }
      }
    } })
  }

  // The .bib the manuscript uses (\bibliography{refs} or \addbibresource{refs.bib}), else the project's first one:
  // opened in the editor, the entry appended if its key is missing, then back to the file you were writing.
  let addedTo = ''
  const editor = (action, text) => send({ type: 'aurora:overleaf', action, text })
  async function waitFor(name) {
    for (let i = 0; i < 25; i++) {
      await new Promise((r) => setTimeout(r, 200))
      const d = await editor('doc')
      if (d && d.ok && d.data.file === name) return d.data
    }
    return null
  }
  async function addToBib(c, back) {
    const here = await editor('doc')
    const files = await editor('files')
    const all = (files && files.ok && files.data.files) || []
    const m = here && here.ok && here.data.text.match(/\\(?:bibliography|addbibresource)\{([^}]+)\}/)
    let bibName = m ? m[1].split(',')[0].trim().split('/').pop() : ''
    if (bibName && !bibName.endsWith('.bib')) bibName += '.bib'
    if (!all.includes(bibName)) bibName = all.find((f) => f.endsWith('.bib')) || ''
    if (!bibName) return 'There is no .bib in the project: copy the BibTeX instead.'
    const opened = await editor('open', bibName)
    if (!opened || !opened.ok) return (opened && opened.error) || `Could not open ${bibName}`
    const bib = await waitFor(bibName)
    let out
    if (!bib) out = `Could not open ${bibName}`
    else if (new RegExp(`@\\w+\\s*\\{\\s*${c.key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*,`).test(bib.text)) out = 'there'
    else {
      const done = await editor('append', c.bibtex)
      if (done && done.ok) {
        out = 'added'; addedTo = bibName
        call('/me/writes/log', { write: 'overleaf.bib', detail: { key: c.key, file: bibName },
          undo: { kind: 'overleaf', how: `Remove the entry ${c.key} from ${bibName} (⌘Z there undoes it).` } })
      } else out = (done && done.error) || 'Could not add it'
    }
    if (back) { await editor('open', back); await waitFor(back) }
    return out
  }

  // ------------------------------------------------------------------ the project's sources, for the check

  async function projectFiles() {
    const id = location.pathname.split('/')[2]
    const r = await fetch(`/project/${id}/download/zip`, { credentials: 'include' })
    if (!r.ok) throw new Error(`Overleaf did not give the project's source (${r.status})`)
    return unzip(new Uint8Array(await r.arrayBuffer()))
  }

  async function unzip(buf) {
    const dv = new DataView(buf.buffer)
    let eocd = buf.length - 22
    while (eocd >= 0 && dv.getUint32(eocd, true) !== 0x06054b50) eocd--
    if (eocd < 0) throw new Error('not a zip')
    const n = dv.getUint16(eocd + 10, true)
    let p = dv.getUint32(eocd + 16, true)
    const files = {}
    for (let i = 0; i < n; i++) {
      const method = dv.getUint16(p + 10, true)
      const csize = dv.getUint32(p + 20, true)
      const nlen = dv.getUint16(p + 28, true), elen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true)
      const off = dv.getUint32(p + 42, true)
      const name = new TextDecoder().decode(buf.slice(p + 46, p + 46 + nlen))
      p += 46 + nlen + elen + clen
      if (!/\.(tex|bib)$/i.test(name)) continue
      const lnl = dv.getUint16(off + 26, true), lel = dv.getUint16(off + 28, true)
      const data = buf.slice(off + 30 + lnl + lel, off + 30 + lnl + lel + csize)
      let bytes = data
      if (method === 8) bytes = new Uint8Array(await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).arrayBuffer())
      files[name] = new TextDecoder().decode(bytes)
    }
    return files
  }

  async function audit() {
    show('<div class="sub">Reading the project’s .tex and .bib files…</div>')
    let files
    try { files = await projectFiles() } catch (err) { return show(`<div class="bad">${esc(err.message)}</div>`) }
    const tex = Object.entries(files).filter(([k]) => /\.tex$/i.test(k)).map(([, v]) => v).join('\n\n')
    const bib = Object.entries(files).filter(([k]) => /\.bib$/i.test(k)).map(([, v]) => v).join('\n\n')
    const start = await call('/memory/audit', { tex, bib, title: document.title.replace(/ - Overleaf.*/, '') })
    if (!start || !start.ok) return show(`<div class="bad">${esc((start && start.error) || 'Pippin is not reachable')}</div>`)
    const id = start.data.audit_id
    for (;;) {
      await new Promise((r) => setTimeout(r, 3000))
      const r = await call(`/memory/audits/${id}`)
      if (!r || !r.ok) return show('<div class="bad">Lost Pippin while checking.</div>')
      const a = r.data
      if (a.state === 'running') { show(`<div class="sub">${esc(a.step || 'Checking…')}</div>`); continue }
      if (a.state === 'failed') return show(`<div class="bad">${esc(a.error)}</div>`)
      const rep = a.report, c = rep.counts || {}
      const retracted = (rep.entries || []).filter((e) => e.retracted)
      const notFound = (rep.entries || []).filter((e) => e.status !== 'verified')
      return show(`<div class="sub">${c.entries} references · ${c.retracted} retracted · ${c.miscites} citations that do not fully hold · ${c.missing} key works not cited</div>
        ${retracted.length ? '<h4>Retracted</h4>' + retracted.map((e) => `<div class="bad">[${esc(e.key)}] ${esc(e.found_title || e.title)}</div>`).join('') : ''}
        ${notFound.length ? '<h4>Not found as written</h4>' + notFound.map((e) => `<div class="warn">[${esc(e.key)}] ${esc(e.title)}: ${esc(e.reason)}</div>`).join('') : ''}
        ${(rep.miscites || []).length ? '<h4>Citations that do not fully hold</h4>' + rep.miscites.map((m) => `<div class="c"><b>[${esc(m.key)}]</b> line ${m.line}<div class="sub">${esc(m.sentence)}</div><div>${esc(m.issue)}</div>${m.quote ? `<blockquote>${esc(m.quote)}</blockquote>` : ''}</div>`).join('') : ''}
        ${(rep.missing || []).length ? '<h4>Key work not cited</h4>' + rep.missing.map((m) => `<div class="c"><b>${esc(m.title)}</b><div class="sub">${esc(m.byline)}</div><div>${esc(m.why)}</div></div>`).join('') : ''}
        ${(rep.undefined || []).length ? `<h4>Cited but not in the .bib</h4><div>${esc(rep.undefined.join(', '))}</div>` : ''}
        <div class="sub" style="margin-top:8px">Nothing was changed in the manuscript. The full report is in the console’s Library, under Check references.</div>`)
    }
  }

  root.querySelector('[data-a="cite"]').onclick = cite
  root.querySelector('[data-a="audit"]').onclick = audit
  chrome.runtime.onMessage.addListener((m) => { if (m.type === 'aurora:cite-now') cite() })

  // what you are writing, as context: the sentence at the cursor, a few seconds after you stop typing. When it is a
  // claim without a citation and papers you have may back it, a quiet hint offers one (nothing is asked of a model
  // until you take it).
  const hintEl = root.querySelector('.hint')
  let timer = null
  let hinted = ''
  const CLAIMISH = /\d|%|\b(show|outperform|improv|reduc|increas|decreas|achiev|lead|known|prior|previous|recent|stud|work|has been|have been|is|are)/i
  document.addEventListener('keyup', () => {
    clearTimeout(timer)
    hintEl.hidden = true
    timer = setTimeout(async () => {
      const r = await send({ type: 'aurora:overleaf', action: 'read' }).catch(() => null)
      if (!r || !r.ok || !card.hidden) return
      const s = r.data.sentence || ''
      if (s.length < 60 || /\\cite/.test(s) || !CLAIMISH.test(s) || r.data.file.endsWith('.bib') || s === hinted) return
      hinted = s
      const h = await call('/memory/cite/hint', { sentence: s })
      if (!h || !h.ok || !h.data.papers) return
      hintEl.innerHTML = `<b>${h.data.papers} paper${h.data.papers > 1 ? 's' : ''} you have may back this sentence</b>
        <div class="sub">${esc(h.data.top.join(' · ')).slice(0, 220)}</div><div class="sub">Click, or ⌥⇧C, for the passages.</div>`
      hintEl.hidden = false
    }, 4000)
  })
  hintEl.onclick = () => { hintEl.hidden = true; cite() }
})()
