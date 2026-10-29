import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import type { ZoteroCollection, ZoteroOptions, ZoteroStatus } from '../types'

function ago(ts: number | null | undefined): string {
  if (!ts) return 'not yet'
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.floor(m / 60)} h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

/** Connect Zotero, choose which collection goes with which project, and what flows each way. */
export function ZoteroDialog({ onClose, onChanged }: { onClose: () => void; onChanged?: (s: ZoteroStatus) => void }) {
  const [status, setStatus] = useState<ZoteroStatus | null>(null)
  const [step, setStep] = useState(0)          // 0: summary (connected); 1 how; 2 collections; 3 what flows
  const [method, setMethod] = useState<'web' | 'local'>('web')
  const [key, setKey] = useState('')
  const [cols, setCols] = useState<ZoteroCollection[] | null>(null)
  const [projects, setProjects] = useState<{ campaign_id: string; name: string }[]>([])
  const [suggested, setSuggested] = useState(false)
  const [options, setOptions] = useState<ZoteroOptions>({ pdfs: true, notes: true, push: true, citekeys: true, idea_notes: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const update = useCallback((s: ZoteroStatus) => {
    setStatus(s)
    if (s.options) setOptions(s.options)
    onChanged?.(s)
  }, [onChanged])

  // once, when it opens: where the connection stands. (Not again when the page behind re-renders and hands a new
  // onClose: that took the dialog back to its first step, out of the list of collections being chosen.)
  useEffect(() => {
    api.zotero.get().then((s) => { update(s); setStep(s.connected ? 0 : 1) }).catch((e) => setError(describeError(e)))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const closeNow = useRef(onClose)
  useEffect(() => { closeNow.current = onClose })
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') closeNow.current() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [])

  // while a sync runs, follow it
  useEffect(() => {
    if (!status?.syncing) return
    const t = setInterval(() => { api.zotero.get().then(update).catch(() => {}) }, 2000)
    return () => clearInterval(t)
  }, [status?.syncing, update])

  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const loadCollections = () => run(async () => {
    const r = await api.zotero.collections()
    setCols(r.collections)
    setProjects(r.projects)
    setSuggested(r.suggested)
    setStep(2)
  })
  const connect = () => run(async () => {
    update(await api.zotero.connect(method, key.trim()))
    setKey('')
    const r = await api.zotero.collections()
    setCols(r.collections)
    setProjects(r.projects)
    setSuggested(r.suggested)
    setStep(2)
  })
  const save = () => run(async () => {
    const mappings = (cols ?? []).filter((c) => c.campaign_id !== null && c.campaign_id !== undefined)
      .map((c) => ({ library: c.library, collection_key: c.collection_key, name: c.name, campaign_id: c.campaign_id as string }))
    update(await api.zotero.save({ mappings, options }))
    setStep(0)
  })

  const report = status?.last_report
  let body: React.ReactNode = <div className="modal-hint">Loading…</div>
  if (status && step === 0 && status.connected) {
    body = (
      <>
        <div className="modal-body">
          <div>Connected to {status.method === 'local' ? 'Zotero on this computer (read-only)' : <>the Zotero account <b>{status.username}</b></>}
            {status.key_hint && <span className="modal-hint"> · key {status.key_hint}</span>}.</div>
          <div>{status.syncing ? 'Syncing now…' : `Last sync ${ago(status.last_sync)}`}
            {report && !status.syncing && status.last_sync && (
              <span className="modal-hint"> · {report.seen ?? 0} items looked at, {report.added ?? 0} added, {report.pdfs ?? 0} PDFs,
                {' '}{report.notes ?? 0} highlights and notes{report.pushed ? `, ${report.pushed} papers sent to Zotero` : ''}{report.idea_notes ? `, ${report.idea_notes} idea notes` : ''}</span>
            )}</div>
          {status.last_error && <div className="modal-error">{status.last_error}</div>}
          {(report?.held ?? 0) > 0 && !status.syncing && (
            // papers the sync would have sent, but writing into Zotero is not allowed on its own: said, with the way out
            <div className="zot-held">
              <b>{report!.held} paper{report!.held === 1 ? '' : 's'} not sent to Zotero yet.</b>{' '}
              {report!.held_level === 'ask'
                ? 'Saving papers to Zotero is set to “When I do it”, so a sync does not send them on its own.'
                : 'Saving papers to Zotero is off (account button → What Pippin may write).'}
              <div className="bs-row">
                {report!.held_level === 'ask' && (
                  <button className="btn-sm btn-approve" disabled={busy} onClick={() => run(async () => {
                    for (const m of status.mappings ?? []) await api.zotero.push(m.campaign_id)
                    update(await api.zotero.get())
                  })}>Send them now</button>
                )}
                <button className={`btn-sm ${report!.held_level === 'ask' ? '' : 'btn-approve'}`} disabled={busy} onClick={() => run(async () => {
                  await api.me.setWrites({ 'zotero.save': 'auto' })
                  update(await api.zotero.sync())
                })}>Allow it, and send them on every sync</button>
                {report!.held_level !== 'ask' && (
                  <button className="btn-sm" disabled={busy} onClick={() => run(async () => {
                    await api.me.setWrites({ 'zotero.save': 'ask' })
                    for (const m of status.mappings ?? []) await api.zotero.push(m.campaign_id)
                    update(await api.zotero.get())
                  })}>Only when I ask: send them now</button>
                )}
              </div>
            </div>
          )}
          {(report?.problems?.length ?? 0) > 0 && <details className="modal-hint"><summary>{report!.problems!.length} things to look at</summary>
            <ul>{report!.problems!.slice(0, 20).map((p) => <li key={p}>{p}</li>)}</ul></details>}
          <div className="share-section-title">Collections and projects</div>
          {(status.mappings ?? []).length ? (status.mappings ?? []).map((m) => (
            <div key={m.library + m.collection_key} className="zot-map"><span>{m.name}</span><span>→ {m.project || 'a project you cannot see'}</span></div>
          )) : <div className="modal-hint">No collection is synced yet.</div>}
          <div className="modal-hint">Every {5} minutes, and when a project’s Literature page is opened. Never deleted: anything in your Zotero.</div>
        </div>
        <div className="modal-actions">
          <button className="btn-sm" disabled={busy} onClick={() => run(async () => { await api.zotero.disconnect(); update({ connected: false }); setStep(1) })}>Disconnect</button>
          <button className="btn-sm" disabled={busy} onClick={loadCollections}>{busy ? 'Loading collections…' : 'Change collections'}</button>
          <button className="btn-sm btn-approve" disabled={busy || status.syncing} onClick={() => run(async () => update(await api.zotero.sync()))}>Sync now</button>
        </div>
      </>
    )
  } else if (step === 1) {
    body = (
      <>
        <div className="modal-body">
          <div className="modal-hint">Step 1 of 3 · How should Pippin reach your Zotero?</div>
          <label className="modal-check">
            <input type="radio" name="zm" checked={method === 'web'} onChange={() => setMethod('web')} />
            <span><b>Your Zotero account</b> <span className="lit-ok">recommended</span><br />
              <span className="modal-hint">Works wherever Pippin runs, with your own library and your group libraries, both ways. Create a key at
                zotero.org → Settings → Security → <i>Create new private key</i>, with library access and, to send papers back, write access.</span></span>
          </label>
          {method === 'web' && <input type="password" placeholder="Paste the key" aria-label="Zotero API key" value={key} onChange={(e) => setKey(e.target.value)} />}
          <label className="modal-check">
            <input type="radio" name="zm" checked={method === 'local'} onChange={() => setMethod('local')} />
            <span><b>Zotero 7 on this computer</b><br />
              <span className="modal-hint">Only if Pippin runs on the computer where Zotero is. Read-only, and only while Zotero is open.
                In Zotero: Settings → Advanced → “Allow other applications on this computer to communicate with Zotero”.</span></span>
          </label>
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button className="btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn-sm btn-approve" disabled={busy || (method === 'web' && key.trim().length < 10)} onClick={connect}>{busy ? 'Connecting…' : 'Next'}</button>
        </div>
      </>
    )
  } else if (step === 2 && cols) {
    body = (
      <>
        <div className="modal-body">
          <div className="modal-hint">Step 2 of 3 · Which collection goes with which project? Collections you leave out are not touched.</div>
          <div className="zot-cols">
            {cols.map((c, i) => (
              <div key={c.library + c.collection_key} className="zot-map">
                <span>{c.name} <span className="modal-hint">· {c.items} items</span></span>
                <select aria-label={`Project for ${c.name}`} value={c.campaign_id ?? '-'}
                  onChange={(e) => { const v = e.target.value; setCols(cols.map((x, k) => k === i ? { ...x, campaign_id: v === '-' ? null : v } : x)) }}>
                  <option value="-">Don’t sync</option>
                  <option value="">Your Library (no project)</option>
                  {projects.map((p) => <option key={p.campaign_id} value={p.campaign_id}>{p.name}</option>)}
                </select>
              </div>
            ))}
            {!cols.length && <div className="modal-hint">This Zotero has no collections. Make one in Zotero for each project, then come back.</div>}
          </div>
          {suggested && <div className="modal-hint">Collections whose names match a project are suggested. Change any of them.</div>}
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          {!status?.connected && <button className="btn-sm" onClick={() => setStep(1)}>Back</button>}
          {status?.connected && <button className="btn-sm" onClick={() => setStep(0)}>Back</button>}
          <button className="btn-sm btn-approve" onClick={() => setStep(3)}>Next</button>
        </div>
      </>
    )
  } else if (step === 3) {
    const readOnly = status?.method === 'local' || !status?.can_write
    const opt = (k: keyof ZoteroOptions, label: React.ReactNode, disabled = false) => (
      <label className="modal-check"><input type="checkbox" checked={options[k] && !disabled} disabled={disabled}
        onChange={(e) => setOptions({ ...options, [k]: e.target.checked })} />{label}</label>
    )
    body = (
      <>
        <div className="modal-body">
          <div className="modal-hint">Step 3 of 3 · What should flow each way?</div>
          <div className="share-section-title">From Zotero</div>
          {opt('pdfs', 'Items and their PDFs (into the project’s files, under papers/)')}
          {opt('notes', 'Your highlights and notes, so the agents can use them')}
          <div className="share-section-title">To Zotero</div>
          {opt('push', <>Papers you save or the agents cite, once checked, into the project’s collection, tagged “aurora”
            {readOnly && <span className="modal-hint"> (needs your Zotero account with write access)</span>}</>, readOnly)}
          {opt('idea_notes', <>Idea summaries as notes on the papers they cite
            {readOnly && <span className="modal-hint"> (needs write access)</span>}</>, readOnly)}
          {opt('citekeys', 'Use Better BibTeX citation keys, if you have it, in exports and PDF names')}
          <div className="modal-hint">Never sent: references that were not found. Never done: deleting anything in your Zotero.
            Items no index has (a thesis, a report) come in as papers you vouch for.</div>
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button className="btn-sm" onClick={() => setStep(2)}>Back</button>
          <button className="btn-sm btn-approve" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save and sync'}</button>
        </div>
      </>
    )
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Zotero" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Zotero</div>
        {error && step === 0 && <div className="modal-error">{error}</div>}
        {body}
      </div>
    </div>
  )
}
