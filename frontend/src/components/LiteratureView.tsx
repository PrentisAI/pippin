import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../api/client'
import type { AddFound, LitFlag, LitItem, LitPaper, LitSearch, ProjectLiterature } from '../types'
import { ZoteroDialog } from './ZoteroDialog'

export const RULE_LABELS: Record<AddFound, string> = {
  cited: 'Add the papers they cite',
  all: 'Add every paper they find',
  none: 'Don’t add any; I pick',
}
export const RULE_HINTS: Record<AddFound, string> = {
  cited: 'Papers an agent cites go in; the rest wait in their search for you.',
  all: 'Everything the agents find and can check goes into the project’s literature.',
  none: 'Nothing goes in by itself; add papers from each search.',
}
const STATUS_TEXT: Record<LitFlag['status'], string> = {
  not_found: 'not found',
  mismatch: 'wrong identifier',
  unchecked: 'not checked yet',
}

function ago(ts: number): string {
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  if (m < 2) return 'just now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.floor(m / 60)} h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

/** The identifier a paper is checked by, as a short label. */
export function idLabel(p: Pick<LitPaper, 'doi' | 'arxiv_id'>): string {
  return p.doi ? `DOI ${p.doi}` : p.arxiv_id ? `arXiv ${p.arxiv_id}` : ''
}

export function PaperLinks({ p }: { p: LitPaper }) {
  return (
    <span className="lit-links">
      {p.doi && <a href={`https://doi.org/${p.doi}`} target="_blank" rel="noreferrer">DOI</a>}
      {p.arxiv_id && <a href={`https://arxiv.org/abs/${p.arxiv_id}`} target="_blank" rel="noreferrer">arXiv</a>}
      {p.pdf_url && <a href={p.pdf_url} target="_blank" rel="noreferrer">PDF</a>}
      {!p.doi && !p.arxiv_id && p.url && <a href={p.url} target="_blank" rel="noreferrer">Page</a>}
    </span>
  )
}

/** A project's literature: its papers, the references that could not be verified, and every search. */
export function LiteratureView({ campaignId }: { campaignId: string }) {
  const [lit, setLit] = useState<ProjectLiterature | null>(null)
  const [filter, setFilter] = useState<'all' | 'checked' | 'flagged' | 'searches'>('all')
  const [openPaper, setOpenPaper] = useState<string | null>(null)
  const [openSearch, setOpenSearch] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [dialog, setDialog] = useState<'add' | 'import' | 'zotero' | null>(null)

  const load = useCallback(() => {
    api.literature.get(campaignId).then(setLit).catch((err) => setMessage({ tone: 'error', text: describeError(err) }))
  }, [campaignId])
  useEffect(() => {
    load()
    const timer = setInterval(load, 15000)       // agents add papers while a run goes on
    // opening the page brings the person's Zotero collection for the project in step
    api.zotero.sync(campaignId, true).then((z) => { if (z.syncing) setTimeout(load, 8000) }).catch(() => {})
    return () => clearInterval(timer)
  }, [load, campaignId])

  const act = async (key: string, fn: () => Promise<ProjectLiterature | { literature: ProjectLiterature }>, ok?: string) => {
    setBusy(key)
    setMessage(null)
    try {
      const r = await fn()
      setLit('literature' in r ? r.literature : r)
      if (ok) setMessage({ tone: 'ok', text: ok })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    } finally {
      setBusy(null)
    }
  }

  if (!lit) return <div className="lit-page"><div className="panel-empty">{message ? message.text : 'Loading the literature…'}</div></div>
  const openFlags = lit.refs.filter((r) => !r.resolution)
  const decided = lit.refs.filter((r) => r.resolution)
  const shownPapers = filter === 'checked' ? lit.papers.filter((p) => p.found_in.length) : lit.papers

  return (
    <div className="lit-page">
      <div className="lit-toolbar">
        <div className="lit-chips" role="group" aria-label="Show">
          {([['all', `All ${lit.papers.length + openFlags.length + decided.filter((r) => r.resolution === 'have').length}`], ['checked', `Checked ${lit.counts.checked}`],
            ['flagged', `Not found ${openFlags.length}`], ['searches', `Searches ${lit.searches.length}`]] as const).map(([k, l]) => (
            <button key={k} className={`lit-chip${filter === k ? ' on' : ''}${k === 'flagged' && openFlags.length ? ' warn' : ''}`}
              aria-pressed={filter === k} onClick={() => { setFilter(k); setOpenSearch(null) }}>{l}</button>
          ))}
        </div>
        <div className="lit-actions">
          <button className="btn-sm btn-modify" onClick={() => setDialog('add')}>Search and add</button>
          <button className="btn-sm lit-btn" onClick={() => setDialog('import')}>Import BibTeX</button>
          <button className="btn-sm lit-btn" onClick={() => setDialog('zotero')}
            title={lit.zotero ? (lit.zotero.collections.length ? `Synced with ${lit.zotero.collections.join(', ')}` : 'Connected; no collection goes with this project yet') : 'Connect your Zotero'}>
            Zotero{lit.zotero ? (lit.zotero.collections.length ? ' ✓' : ' ·') : ''}</button>
          <a className="btn-sm lit-btn" href={api.literature.exportUrl(campaignId)} download>Export .bib</a>
        </div>
      </div>
      <div className="lit-rule">Papers the agents find: <b>{RULE_LABELS[lit.rule]}</b>. {RULE_HINTS[lit.rule]} The default is in
        Settings → Literature; each run’s proposal can change it.</div>
      {message && <div className={`panel-message ${message.tone}`} style={{ margin: '6px 0' }}>{message.text}</div>}
      {lit.zotero?.last_error && <div className="panel-message error" style={{ margin: '6px 0' }}>Zotero: {lit.zotero.last_error}</div>}
      {(() => {
        const missing = lit.papers.filter((p) => !p.in_zotero)
        return lit.zotero?.can_send && missing.length > 0 && (
          <div className="lit-zot-banner">
            <span>{missing.length} checked paper{missing.length === 1 ? ' is' : 's are'} not in your Zotero collection yet.</span>
            <button className="btn-sm lit-btn" disabled={busy === 'zotero'}
              onClick={() => act('zotero', async () => {
                const r = await api.zotero.push(campaignId)
                if (r.problems.length) throw new Error(r.problems[0])
                return api.literature.get(campaignId)
              }, 'Sent to your Zotero collection, tagged “aurora”.')}>Send them to Zotero</button>
          </div>
        )
      })()}

      {filter === 'searches' ? (
        openSearch ? <SearchPane campaignId={campaignId} searchId={openSearch} rule={lit.rule} onBack={() => setOpenSearch(null)}
          onChanged={setLit} />
          : <SearchList searches={lit.searches} onOpen={setOpenSearch} />
      ) : (
        <div className="lit-table-wrap">
          <table className="lit-table">
            <thead><tr><th>Paper</th><th>Checked against</th><th>Used in</th><th>Added by</th></tr></thead>
            <tbody>
              {(filter === 'all' || filter === 'flagged') && openFlags.map((r) => (
                <FlagRow key={r.ref_id} r={r} busy={busy === r.ref_id}
                  onDecide={(action) => act(r.ref_id, () => api.literature.decide(campaignId, r.ref_id, action),
                    action === 'replace' ? 'The real paper is used instead, and is now in the project’s literature.'
                      : action === 'remove' ? 'It will not be used again in this project.' : 'Noted: you have this paper.')}
                  onRecheck={() => act(r.ref_id, () => api.literature.recheck(campaignId, r.ref_id))} />
              ))}
              {filter !== 'flagged' && shownPapers.map((p) => (
                <PaperRow key={p.paper_id} p={p} open={openPaper === p.paper_id}
                  onZotero={lit.zotero?.can_send && !p.in_zotero ? () => act(p.paper_id, async () => { await api.zotero.push(campaignId, [p.paper_id]); return api.literature.get(campaignId) },
                    `${p.key} is in your Zotero collection now, tagged “aurora”.`) : undefined}
                  onToggle={() => setOpenPaper(openPaper === p.paper_id ? null : p.paper_id)}
                  onStar={() => act(p.paper_id, () => api.literature.star(campaignId, p.paper_id, !p.starred))}
                  onRemove={() => act(p.paper_id, () => api.literature.remove(campaignId, p.paper_id), `${p.key} is no longer in the project’s literature.`)} />
              ))}
              {filter === 'all' && decided.filter((r) => r.resolution === 'have').map((r) => (
                <tr key={r.ref_id}>
                  <td><div className="lit-title">{r.reference.title || r.reference.text}</div>
                    <div className="lit-sub">{[r.reference.authors, r.reference.year].filter(Boolean).join(' · ')}</div></td>
                  <td><span className="lit-sub">vouched for by {r.decided_by}</span><div className="lit-sub">no index has it (a thesis or report, say)</div></td>
                  <td className="lit-sub">{r.used_in.length ? r.used_in.join(', ') : 'not used yet'}</td>
                  <td className="lit-sub">{r.by}</td>
                </tr>
              ))}
              {filter === 'flagged' && decided.map((r) => (
                <tr key={r.ref_id} className="lit-decided">
                  <td><div className="lit-title">{r.reference.title || r.reference.text}</div>
                    <div className="lit-sub">{[r.reference.authors, r.reference.year].filter(Boolean).join(' · ')} · as cited by {r.by}</div></td>
                  <td className="lit-sub">{r.resolution === 'replaced' ? `replaced with ${r.replaced_by}` : r.resolution === 'removed' ? 'not used again' : 'vouched for'}</td>
                  <td className="lit-sub">{r.used_in.join(', ')}</td>
                  <td className="lit-sub">{r.decided_by}
                    <button className="link-btn" onClick={() => act(r.ref_id, () => api.literature.decide(campaignId, r.ref_id, 'reopen'))}>Undo</button></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!lit.papers.length && !lit.refs.length && (
            <div className="lit-empty">No papers yet. Agents add the papers they cite as runs go on; you can search the indexes,
              add a paper by DOI or arXiv ID, or import a .bib file.</div>
          )}
          {filter === 'flagged' && !lit.refs.length && <div className="lit-empty">Every reference so far was found in the indexes.</div>}
        </div>
      )}

      <div className="lit-notes">
        <section><h3>How a reference is checked</h3>
          <p>Every paper an agent cites is looked up by DOI or arXiv ID, else by title and authors, in Crossref, arXiv, OpenAlex
            and Semantic Scholar. A match must agree on the title, the first author and the year (a year either way, for a preprint
            and its journal version). A reference that is not found never goes into the literature or the report’s references.</p></section>
        <section><h3>The report’s references</h3>
          <p>The writing phase cites papers by their key and gets its reference list from the papers that were found.
            Export .bib gives the same entries for your own paper.</p></section>
      </div>
      {dialog === 'add' && <AddPaperDialog campaignId={campaignId} onClose={() => setDialog(null)} onChanged={setLit} />}
      {dialog === 'import' && <ImportDialog campaignId={campaignId} onClose={() => setDialog(null)} onChanged={setLit} />}
      {dialog === 'zotero' && <ZoteroDialog onClose={() => { setDialog(null); load() }} />}
    </div>
  )
}

function PaperRow({ p, open, onToggle, onStar, onRemove, onZotero }: { p: LitItem; open: boolean; onToggle: () => void; onStar: () => void; onRemove: () => void; onZotero?: () => void }) {
  return (
    <>
      <tr className={open ? 'lit-open' : ''}>
        <td>
          <button className="lit-title-btn" aria-expanded={open} onClick={onToggle}>
            {p.starred && <span className="lit-star" aria-label="starred">★</span>}{p.title}</button>
          <div className="lit-sub">{p.byline}{p.cites_per_year != null && ` · ${p.cites_per_year} cit/yr`} · <span className="lit-key">{p.key}</span>
            {p.in_zotero && <span className="lit-zot" title="In your Zotero">Z ✓</span>}
            {(p.notes?.length ?? 0) > 0 && <span className="lit-tag" style={{ marginLeft: 6 }}>{p.notes!.length} highlight{p.notes!.length === 1 ? '' : 's'} and notes</span>}</div>
        </td>
        <td><span className="lit-ok">✓ {p.found_in.join(', ') || 'checked'}</span>{idLabel(p) && <div className="lit-sub mono">{idLabel(p)}</div>}</td>
        <td className="lit-sub">{p.used_in.length ? p.used_in.map((u) => <div key={u}>{u}</div>) : 'not used yet'}</td>
        <td className="lit-sub">{p.added_by || p.source_label}<div>{p.source_label}</div></td>
      </tr>
      {open && (
        <tr className="lit-detail"><td colSpan={4}>
          {p.abstract ? <p>{p.abstract}</p> : <p className="lit-sub">The indexes give no abstract for this paper.</p>}
          {(p.notes?.length ?? 0) > 0 && (
            <div className="lit-notes-list">
              {p.notes!.map((n, k) => (
                <div key={k} className={`lit-note ${n.kind}`}>
                  {n.kind === 'highlight' ? <q>{n.text}</q> : <span>{n.text}</span>}
                  {n.comment && <span className="lit-sub"> — {n.comment}</span>}
                  <span className="lit-sub"> · {n.by}{n.page && `, p. ${n.page}`}</span>
                </div>
              ))}
              <div className="lit-sub">From Zotero. The agents see these with the paper.</div>
            </div>
          )}
          {p.pdf_file && <p className="lit-sub">PDF in the project’s files: <span className="mono">{p.pdf_file}</span></p>}
          <div className="lit-detail-actions"><PaperLinks p={p} />
            {onZotero && <button className="link-btn" onClick={onZotero}>+ Zotero</button>}
            <button className="link-btn" onClick={onStar}>{p.starred ? 'Unstar' : 'Star'}</button>
            <button className="link-btn danger" onClick={onRemove}>Remove from the project</button></div>
        </td></tr>
      )}
    </>
  )
}

function FlagRow({ r, busy, onDecide, onRecheck }: { r: LitFlag; busy: boolean; onDecide: (a: 'replace' | 'remove' | 'have') => void; onRecheck: () => void }) {
  const ref = r.reference
  const what = r.status === 'unchecked'
    ? 'The indexes could not be reached when it was cited, so it is not checked yet. Until it is, it counts as unsupported.'
    : r.status === 'mismatch' ? `Its identifier belongs to another paper: ${r.reason}.`
      : `No paper with this title${ref.authors ? ' and these authors' : ''} exists in ${r.searched.join(', ') || 'the indexes'}. The agent probably made it up.`
  return (
    <tr className="lit-flag">
      <td>
        <div className="lit-title">{ref.title || ref.text}</div>
        <div className="lit-sub">{[ref.authors, ref.year].filter(Boolean).join(' · ')}{ref.doi && ` · DOI ${ref.doi}`}{ref.arxiv_id && ` · arXiv ${ref.arxiv_id}`} · as cited by {r.by}</div>
        <div className="lit-flag-box">
          <div>{what}</div>
          {r.closest && <div>Closest real paper: {r.closest.title} ({r.closest.byline}) <span className="lit-ok">✓ {idLabel(r.closest) || r.closest.key}</span></div>}
          <div className="lit-flag-actions">
            {r.closest && <button className="btn-sm lit-btn" disabled={busy} onClick={() => onDecide('replace')}>Use the real one instead</button>}
            <button className="btn-sm lit-btn" disabled={busy} onClick={() => onDecide('remove')}>Remove it everywhere</button>
            <button className="btn-sm lit-btn" disabled={busy} onClick={() => onDecide('have')}>I have this paper</button>
            <button className="link-btn" disabled={busy} onClick={onRecheck}>Check again</button>
          </div>
        </div>
      </td>
      <td><span className="lit-warn">? {STATUS_TEXT[r.status]}</span>{r.searched.length > 0 && <div className="lit-sub">searched {r.searched.length} indexes</div>}</td>
      <td className="lit-sub">{r.used_in.map((u) => <div key={u}>{u}</div>)}</td>
      <td className="lit-sub">{r.by}</td>
    </tr>
  )
}

function SearchList({ searches, onOpen }: { searches: LitSearch[]; onOpen: (id: string) => void }) {
  if (!searches.length) return <div className="lit-empty">No searches yet. The ideation seats search before they present, and screening looks for the closest prior work.</div>
  return (
    <div className="lit-searches">
      {searches.map((s) => (
        <button key={s.search_id} className="lit-search-row" onClick={() => onOpen(s.search_id)}>
          <span className="lit-search-icon" aria-hidden="true">⌕</span>
          <span className="lit-search-main">
            <span><b>{s.by}</b> searched <span className="mono">“{s.query}”</span></span>
            <span className="lit-sub">{s.context} · {ago(s.created_at)}{s.failed.length > 0 && ` · ${s.failed.join(', ')} did not answer`}</span>
          </span>
          <span className="lit-search-counts">found {s.found} · cited {s.cited} · in the literature {s.in_library}</span>
        </button>
      ))}
    </div>
  )
}

function SearchPane({ campaignId, searchId, rule, onBack, onChanged }: { campaignId: string; searchId: string; rule: AddFound; onBack: () => void; onChanged: (l: ProjectLiterature) => void }) {
  const [s, setS] = useState<LitSearch | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => { api.literature.searchDetail(campaignId, searchId).then(setS).catch((e) => setError(describeError(e))) }, [campaignId, searchId])
  useEffect(() => { load() }, [load])
  const add = async (ids: string[]) => {
    try {
      const r = await api.literature.addKnown(campaignId, ids, searchId)
      onChanged(r.literature)
      setPicked(new Set())
      load()
    } catch (e) { setError(describeError(e)) }
  }
  if (!s) return <div className="panel-empty">{error ?? 'Loading…'}</div>
  const addable = (s.papers ?? []).filter((p) => !p.in_library)
  const sel = addable.filter((p) => picked.has(p.paper_id))
  return (
    <div className="lit-search-pane">
      <button className="link-btn" style={{ marginLeft: 0 }} onClick={onBack}>← All searches</button>
      <div className="lit-search-head">
        <div><b>{s.by}</b> searched <span className="mono">“{s.query}”</span> <span className="lit-sub">· {s.context} · {ago(s.created_at)}</span></div>
        <div className="lit-sub">Found {s.found} · cited {s.cited} · in the literature {s.in_library}. Rule: {RULE_LABELS[rule].toLowerCase()}.
          {' '}Asked {s.reached.join(', ') || 'no index'}{s.failed.length > 0 && `; ${s.failed.join(', ')} did not answer`}.</div>
        <div className="lit-flag-actions">
          {addable.length ? <button className="btn-sm lit-btn" onClick={() => add(addable.map((p) => p.paper_id))}>Add all {addable.length} to the literature</button>
            : <span className="lit-ok">Everything here is in the literature.</span>}
          {sel.length > 0 && <button className="btn-sm btn-modify" onClick={() => add(sel.map((p) => p.paper_id))}>Add {sel.length} selected</button>}
        </div>
      </div>
      {error && <div className="modal-error">{error}</div>}
      <div className="lit-table-wrap">
        <table className="lit-table lit-compact">
          <thead><tr><th aria-label="Select" /><th>Key</th><th>Year</th><th title="Citations per year">Cit/yr</th><th>Paper</th><th /></tr></thead>
          <tbody>{(s.papers ?? []).map((p) => (
            <tr key={p.paper_id}>
              <td>{!p.in_library && <input type="checkbox" aria-label={`Select ${p.key}`} checked={picked.has(p.paper_id)}
                onChange={(e) => { const n = new Set(picked); if (e.target.checked) n.add(p.paper_id); else n.delete(p.paper_id); setPicked(n) }} />}</td>
              <td className="lit-key">{p.key}</td>
              <td className="mono">{p.year ?? '—'}</td>
              <td className="mono">{p.cites_per_year ?? '—'}</td>
              <td><div className="lit-title">{p.title}</div><div className="lit-sub">{p.byline} <PaperLinks p={p} /></div></td>
              <td className="lit-sub" style={{ whiteSpace: 'nowrap' }}>
                {p.cited && <span className="lit-tag">cited</span>}
                {p.in_library ? <span className="lit-ok">✓ in the literature</span>
                  : <button className="btn-sm lit-btn" onClick={() => add([p.paper_id])}>Add</button>}
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  )
}

const LOOKS_LIKE_ID = /(10\.\d{4,9}\/\S+)|(\d{4}\.\d{4,5})|arxiv\.org|doi\.org/i

/** Search the indexes, or add one paper by DOI, arXiv ID or exact title. */
function AddPaperDialog({ campaignId, onClose, onChanged }: { campaignId: string; onClose: () => void; onChanged: (l: ProjectLiterature) => void }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<LitSearch | null>(null)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    inputRef.current?.focus()
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])

  const addOne = async (body: { text?: string; title?: string }) => {
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.literature.add(campaignId, body)
      onChanged(r.literature)
      setMessage(r.added ? { tone: 'ok', text: `Added ${r.key}: ${r.check.paper?.title ?? ''}` }
        : { tone: 'error', text: `Not added: ${r.check.reason || r.check.status}.${r.check.closest ? ` Closest real paper: “${r.check.closest.title}”.` : ''}` })
    } catch (e) { setMessage({ tone: 'error', text: describeError(e) }) } finally { setBusy(false) }
  }
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!q.trim()) return
    if (LOOKS_LIKE_ID.test(q)) return addOne({ text: q.trim() })
    setBusy(true)
    setMessage(null)
    try { setResults(await api.literature.search(campaignId, q.trim())) } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) } finally { setBusy(false) }
  }
  const addKnown = async (pid: string) => {
    if (!results) return
    try {
      const r = await api.literature.addKnown(campaignId, [pid], results.search_id)
      onChanged(r.literature)
      setResults({ ...results, papers: results.papers?.map((p) => p.paper_id === pid ? { ...p, in_library: true } : p) })
    } catch (e) { setMessage({ tone: 'error', text: describeError(e) }) }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Search and add papers" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Search and add papers</div>
        <form className="share-invite" onSubmit={submit}>
          <input ref={inputRef} placeholder="Words to search for, or a DOI, arXiv ID or link" aria-label="Search or identifier" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn-sm btn-modify" disabled={busy || !q.trim()}>{busy ? 'Looking…' : LOOKS_LIKE_ID.test(q) ? 'Add' : 'Search'}</button>
        </form>
        <div className="share-hint">Searches OpenAlex, arXiv, Crossref and Semantic Scholar. A paper is added only if an index has it.</div>
        {message && <div className={`panel-message ${message.tone}`} style={{ margin: '6px 0' }}>{message.text}</div>}
        {results && (
          <div className="lit-results">
            {results.failed.length > 0 && <div className="share-hint">{results.failed.join(', ')} did not answer.</div>}
            {(results.papers ?? []).map((p) => (
              <div key={p.paper_id} className="lit-result">
                <div className="lit-result-main"><div className="lit-title">{p.title}</div>
                  <div className="lit-sub">{p.byline}{p.cites_per_year != null && ` · ${p.cites_per_year} cit/yr`} <PaperLinks p={p} /></div></div>
                {p.in_library ? <span className="lit-ok">✓ added</span> : <button className="btn-sm lit-btn" onClick={() => addKnown(p.paper_id)}>Add</button>}
              </div>
            ))}
            {!results.papers?.length && <div className="share-hint">Nothing found.</div>}
            <button className="link-btn" style={{ marginLeft: 0 }} disabled={busy} onClick={() => addOne({ title: q.trim() })}>Add the paper with exactly this title</button>
          </div>
        )}
        <div className="modal-actions"><button className="btn-sm btn-approve" onClick={onClose}>Done</button></div>
      </div>
    </div>
  )
}

function ImportDialog({ campaignId, onClose, onChanged }: { campaignId: string; onClose: () => void; onChanged: (l: ProjectLiterature) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<{ cite_key: string; status: string; reason: string; key: string; title: string }[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await api.literature.importBib(campaignId, text)
      onChanged(r.literature)
      setReport(r.entries)
    } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const found = report?.filter((r) => r.status === 'verified').length ?? 0
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Import BibTeX" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Import a .bib file</div>
        {!report ? (
          <div className="modal-body">
            <label className="btn-sm lit-btn" style={{ alignSelf: 'flex-start' }}>Choose a .bib file
              <input type="file" accept=".bib,text/plain" hidden onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()) }} /></label>
            <textarea className="lit-bib" placeholder="…or paste BibTeX entries here" aria-label="BibTeX" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="modal-hint">Every entry is checked against the indexes first. Entries that are found go into the project’s literature; the others are listed so you can fix them. From Zotero: right-click a collection → Export collection → BibTeX.</div>
            {error && <div className="modal-error">{error}</div>}
          </div>
        ) : (
          <div className="modal-body">
            <div><b>{found}</b> of {report.length} entries were found and added.</div>
            <div className="lit-import-report">
              {report.map((r) => (
                <div key={r.cite_key} className={r.status === 'verified' ? '' : 'lit-warn'}>
                  {r.status === 'verified' ? '✓' : '?'} <span className="mono">{r.cite_key}</span> {r.status === 'verified' ? `→ ${r.key}` : `· ${r.reason || r.status}`}
                  <div className="lit-sub">{r.title}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        <div className="modal-actions">
          <button className="btn-sm" onClick={onClose}>{report ? 'Done' : 'Cancel'}</button>
          {!report && <button className="btn-sm btn-approve" disabled={busy || !text.trim()} onClick={run}>{busy ? 'Checking…' : 'Check and import'}</button>}
        </div>
      </div>
    </div>
  )
}

/** The import rule as a select (the proposal card uses it). */
export function RuleSelect({ value, onChange }: { value: AddFound; onChange: (v: AddFound) => void }) {
  return (
    <select aria-label="Papers the agents find" value={value} onChange={(e) => onChange(e.target.value as AddFound)}>
      {(Object.keys(RULE_LABELS) as AddFound[]).map((k) => <option key={k} value={k}>{RULE_LABELS[k]}</option>)}
    </select>
  )
}

