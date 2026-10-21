import { useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { AddFound, BsCard, BsSpace, LitItem, ProjectLiterature } from '../../types'
import { AddPaperDialog, ImportDialog, SearchPane } from '../LiteratureView'
import { ZoteroDialog } from '../ZoteroDialog'
import { PDF, short } from './common'

export type PaperFilter = 'all' | 'star' | 'upload' | 'feed' | 'search' | 'zotero'
export const FILTER_LABELS: Record<PaperFilter, string> = {
  all: 'All papers: found by the agents, saved from the feed, uploaded, from Zotero', star: 'Starred', upload: 'Your uploads',
  feed: 'From the feed', search: 'Found by the agents', zotero: 'From your Zotero',
}
const SOURCE_LABEL: Record<string, string> = {
  agent: 'Cited by an agent', search: 'Found in a search', person: 'Added by hand', bibtex: 'Imported', zotero: 'From your Zotero',
  feed: 'From the feed', upload: 'Your upload',
}

export function inFilter(p: LitItem, f: PaperFilter): boolean {
  return f === 'all' || (f === 'star' ? p.starred : f === 'search' ? (p.source === 'search' || p.source === 'agent') : p.source === f)
}

/** The papers of a project, as the brainstorm uses them: filter, search, add, star, send to Zotero, open the PDF. */
export function PapersPane({ space, lit, setLit, filter, searchId, onSearch, flashKey, rule, onIdea }: {
  space: BsSpace; lit: ProjectLiterature | null; setLit: (l: ProjectLiterature) => void; filter: PaperFilter
  searchId: string | null; onSearch: (id: string | null) => void; flashKey: string; rule: AddFound; onIdea: (id: string) => void
}) {
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [menu, setMenu] = useState(false)
  const [dialog, setDialog] = useState<null | 'search' | 'import' | 'paste' | 'zotero' | 'bib'>(null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const files = useRef<HTMLInputElement>(null)
  const cid = space.project ? space.space : ''

  if (!space.project) return <OwnPapers space={space} />
  if (!lit) return <div className="panel-empty">Loading…</div>
  if (searchId) return <SearchPane campaignId={cid} searchId={searchId} rule={rule} onBack={() => onSearch(null)} onChanged={setLit} />

  const usedIn = new Map<string, BsCard[]>()
  for (const c of space.cards) if (c.paper_id) for (const l of space.links) if (l.from_card === c.card_id) {
    const idea = space.cards.find((x) => x.card_id === l.to_card)
    if (idea) usedIn.set(c.paper_id, [...(usedIn.get(c.paper_id) ?? []), idea])
  }
  const needle = q.toLowerCase()
  const shown = lit.papers.filter((p) => inFilter(p, filter) && (!needle || `${p.title} ${p.byline} ${p.key}`.toLowerCase().includes(needle)))
  const sel = shown.filter((p) => picked.has(p.paper_id))
  const notInZotero = lit.zotero?.can_send ? lit.papers.filter((p) => !p.in_zotero) : []
  const act = async (fn: () => Promise<ProjectLiterature | void>, ok?: string) => {
    setMessage(null)
    try { const r = await fn(); if (r) setLit(r); if (ok) setMessage({ tone: 'ok', text: ok }) } catch (e) { setMessage({ tone: 'error', text: describeError(e) }) }
  }
  const upload = async (list: FileList | null) => {
    if (!list?.length) return
    const added: string[] = []
    const kept: string[] = []
    for (const f of Array.from(list)) {
      try {
        const r = await api.literature.upload(cid, f)
        setLit(r.literature)
        if (r.added) added.push(r.key); else kept.push(f.name)
      } catch (e) { setMessage({ tone: 'error', text: `${f.name}: ${describeError(e)}` }); return }
    }
    setMessage({ tone: 'ok', text: [added.length ? `Recognised and added: ${added.join(', ')}.` : '',
      kept.length ? `Not found in any index, kept as your own: ${kept.join(', ')}.` : ''].filter(Boolean).join(' ') })
  }

  return (
    <div className="bs-papers">
      <div className="bs-row" style={{ justifyContent: 'space-between' }}><span className="lit-sub">{FILTER_LABELS[filter]}</span>
        <span className="bs-rel"><button className="btn-sm btn-modify" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>+ Add ▾</button>
          {menu && (
            <div className="bs-menu right" role="menu" onMouseLeave={() => setMenu(false)}>
              <button role="menuitem" onClick={() => { setMenu(false); setDialog('search') }}>Search for papers</button>
              <button role="menuitem" onClick={() => { setMenu(false); files.current?.click() }}>Upload PDFs</button>
              <button role="menuitem" onClick={() => { setMenu(false); setDialog('import') }}>Import BibTeX or RIS</button>
              <button role="menuitem" onClick={() => { setMenu(false); setDialog('paste') }}>Paste DOIs or arXiv IDs</button>
              <button role="menuitem" onClick={() => { setMenu(false); setDialog('zotero') }}>From Zotero</button>
            </div>
          )}</span>
        <input ref={files} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = '' }} />
      </div>
      {message && <div className={`panel-message ${message.tone}`} style={{ margin: 0 }}>{message.text}</div>}
      {notInZotero.length > 0 && (
        <div className="lit-zot-banner"><span>{notInZotero.length} checked paper{notInZotero.length > 1 ? 's are' : ' is'} not in your Zotero yet.</span>
          <button className="btn-sm lit-btn" onClick={() => act(async () => { await api.zotero.push(cid); return api.literature.get(cid) }, 'Sent to your Zotero collection, tagged “aurora”.')}>
            Add {notInZotero.length > 1 ? `all ${notInZotero.length}` : 'it'} to Zotero</button></div>
      )}
      <div className="bs-row" style={{ flexWrap: 'nowrap' }}>
        <input className="bs-search" placeholder="Search titles, authors, keys" aria-label="Search the papers" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn-sm lit-btn" onClick={() => setDialog('bib')}>Export .bib</button>
      </div>
      {sel.length > 0 && (
        <div className="lit-zot-banner"><span>{sel.length} selected</span>
          <div className="bs-row">
            <button className="btn-sm lit-btn" onClick={() => act(async () => { let r: ProjectLiterature | undefined; for (const p of sel) r = await api.literature.star(cid, p.paper_id, true); setPicked(new Set()); return r })}>Star</button>
            <button className="btn-sm lit-btn" onClick={() => act(async () => { let r: ProjectLiterature | undefined; for (const p of sel) r = await api.literature.remove(cid, p.paper_id); setPicked(new Set()); return r },
              'Removed from the project’s literature; they stay in their searches.')}>Remove from the literature</button>
            <button className="link-btn" onClick={() => setPicked(new Set())}>Clear</button>
          </div></div>
      )}
      <div className="lit-table-wrap">
        <table className="lit-table lit-compact">
          <thead><tr><th><input type="checkbox" aria-label="Select all" checked={sel.length > 0 && sel.length === shown.length}
            onChange={(e) => setPicked(e.target.checked ? new Set(shown.map((p) => p.paper_id)) : new Set())} /></th>
            <th>Key</th><th>Year</th><th title="Citations per year">Cit/yr</th><th>Paper</th></tr></thead>
          <tbody>
            {shown.map((p) => (
              <tr key={p.paper_id} id={`paper-${p.key}`} className={flashKey === p.key ? 'bs-flash-row' : ''}>
                <td><input type="checkbox" aria-label={`Select ${p.key}`} checked={picked.has(p.paper_id)}
                  onChange={(e) => { const n = new Set(picked); if (e.target.checked) n.add(p.paper_id); else n.delete(p.paper_id); setPicked(n) }} /></td>
                <td className="lit-key">{p.key}</td><td className="mono">{p.year ?? '—'}</td><td className="mono">{p.cites_per_year ?? '—'}</td>
                <td>
                  <div className="lit-title">{p.title} <span className="lit-ok" title="Checked: it exists">✓</span>
                    <button className="bs-star" aria-label={p.starred ? 'Unstar' : 'Star'} onClick={() => act(() => api.literature.star(cid, p.paper_id, !p.starred))}>{p.starred ? '★' : '☆'}</button></div>
                  <div className="lit-sub">{p.byline}</div>
                  <div className="bs-row lit-sub" style={{ gap: 8, marginTop: 3 }}>
                    <span style={{ color: 'var(--accent)' }}>{SOURCE_LABEL[p.source] ?? p.source_label}</span>
                    {(usedIn.get(p.paper_id) ?? []).length > 0 && <><span>Used in</span>{usedIn.get(p.paper_id)!.map((c) => (
                      <button key={c.card_id} className="bs-ichip" onClick={() => onIdea(c.card_id)}>{short(c.text, 30)}</button>))}</>}
                    {lit.zotero?.connected && (p.in_zotero ? <span className="lit-zot" title="In your Zotero">Z ✓</span>
                      : lit.zotero.can_send && <button className="link-btn" onClick={() => act(async () => { await api.zotero.push(cid, [p.paper_id]); return api.literature.get(cid) }, `${p.key} is in your Zotero now.`)}>+ Zotero</button>)}
                    {p.pdf_file ? <a className="link-btn" href={api.literature.fileUrl(cid, p.pdf_file)} target="_blank" rel="noreferrer">{PDF} PDF</a>
                      : p.pdf_url ? <a className="link-btn" href={p.pdf_url} target="_blank" rel="noreferrer">{PDF} PDF</a> : null}
                    {p.newer_version && <button className="link-btn" title={p.arxiv_comment} onClick={() => act(() => api.literature.citeVersion(cid, p.paper_id))}>A newer version (v{p.arxiv_version}) exists: cite it</button>}
                  </div>
                </td>
              </tr>
            ))}
            {!shown.length && <tr><td colSpan={5} className="lit-sub">{q ? `No paper matches “${q}”.` : 'Nothing here yet.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {dialog === 'search' && <AddPaperDialog campaignId={cid} onClose={() => setDialog(null)} onChanged={setLit} />}
      {dialog === 'import' && <ImportDialog campaignId={cid} onClose={() => setDialog(null)} onChanged={setLit} />}
      {dialog === 'zotero' && <ZoteroDialog onClose={() => { setDialog(null); api.literature.get(cid).then(setLit).catch(() => {}) }} />}
      {dialog === 'paste' && <PasteIds campaignId={cid} onClose={() => setDialog(null)} onChanged={setLit} />}
      {dialog === 'bib' && <BibExport campaignId={cid} name={space.name} lit={lit} onClose={() => setDialog(null)} />}
    </div>
  )
}

/** Papers in your own space: your Library's papers. */
function OwnPapers({ space }: { space: BsSpace }) {
  const [rows, setRows] = useState<{ key: string; title: string; byline: string; paper_id: string; year: number | null; cites_per_year: number | null }[] | null>(null)
  useEffect(() => { api.library.get().then((l) => setRows(l.papers.filter((p) => p.mine))).catch(() => setRows([])) }, [space.key])
  if (!rows) return <div className="panel-empty">Loading…</div>
  return (
    <div className="bs-papers">
      <div className="lit-sub">Your own papers (the Library’s “Only mine”). Papers the agents bring into this space are added here.</div>
      {rows.map((p) => <div key={p.paper_id} className="bs-paper"><div className="lit-title">{p.title}</div><div className="lit-sub">{p.byline} · <span className="lit-key">{p.key}</span></div></div>)}
      {!rows.length && <div className="bs-note">No papers yet.</div>}
    </div>
  )
}

function PasteIds({ campaignId, onClose, onChanged }: { campaignId: string; onClose: () => void; onChanged: (l: ProjectLiterature) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<{ cite_key: string; status: string; reason: string; key: string; title: string }[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Paste DOIs or arXiv IDs" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Paste DOIs or arXiv IDs</div>
        <div className="modal-body">
          {!report ? <>
            <textarea className="lit-bib" placeholder={'10.1126/science.1219831\n1706.03762\nhttps://arxiv.org/abs/2501.17070'} aria-label="DOIs or arXiv IDs" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="modal-hint">One per line (or separated by spaces or commas). Each is checked; the ones found go into the project’s literature.</div>
          </> : <>
            <div><b>{report.filter((r) => r.status === 'verified').length}</b> of {report.length} found and added.</div>
            <div className="lit-import-report">{report.map((r) => (
              <div key={r.cite_key} className={r.status === 'verified' ? '' : 'lit-warn'}>{r.status === 'verified' ? '✓' : '?'} <span className="mono">{r.cite_key}</span>
                {r.status === 'verified' ? ` → ${r.key}` : ` · ${r.reason || r.status}`}<div className="lit-sub">{r.title}</div></div>))}</div>
          </>}
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button className="btn-sm" onClick={onClose}>{report ? 'Done' : 'Cancel'}</button>
          {!report && <button className="btn-sm btn-approve" disabled={busy || !text.trim()} onClick={async () => {
            setBusy(true)
            try { const r = await api.literature.addMany(campaignId, text); onChanged(r.literature); setReport(r.entries) } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
          }}>{busy ? 'Checking…' : 'Check and add'}</button>}
        </div>
      </div>
    </div>
  )
}

function BibExport({ campaignId, name, lit, onClose }: { campaignId: string; name: string; lit: ProjectLiterature; onClose: () => void }) {
  const [text, setText] = useState('')
  const pre = useRef<HTMLPreElement>(null)
  useEffect(() => { api.literature.exportText(campaignId).then(setText).catch(() => setText('')) }, [campaignId])
  const left = lit.refs.filter((r) => !r.resolution).length
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="BibTeX export" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">{name.slice(0, 40)}.bib</div>
        <pre ref={pre} className="lit-bib" style={{ whiteSpace: 'pre-wrap', maxHeight: '50vh', overflow: 'auto' }}>{text || 'Loading…'}
          {text && (left ? `\n% ${left} reference${left === 1 ? '' : 's'} left out: not found in any index` : '\n% all references checked')}</pre>
        <div className="modal-actions">
          <span className="modal-hint" style={{ marginRight: 'auto' }}>References not found are left out, and said so.</span>
          <a className="btn-sm lit-btn" href={api.literature.exportUrl(campaignId)} download>Download</a>
          <button className="btn-sm btn-approve" onClick={() => {
            navigator.clipboard?.writeText(text).catch(() => {
              const r = document.createRange(); if (pre.current) { r.selectNodeContents(pre.current); getSelection()?.removeAllRanges(); getSelection()?.addRange(r) }
            })
          }}>Copy</button>
        </div>
      </div>
    </div>
  )
}
