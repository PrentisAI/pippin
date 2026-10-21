import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { LibraryView as Library, LitSearch } from '../types'
import { PaperLinks } from './LiteratureView'
import { ZoteroDialog } from './ZoteroDialog'

/** Your Library: papers you saved and the papers of the projects you can see; search the indexes. */
export function LibraryView({ onOpenProject }: { onOpenProject: (campaignId: string) => void }) {
  const [lib, setLib] = useState<Library | null>(null)
  const [tab, setTab] = useState<'saved' | 'search'>('saved')
  const [project, setProject] = useState<string>('all')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<LitSearch | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const [zotero, setZotero] = useState(false)

  const load = useCallback(() => { api.library.get().then(setLib).catch((e) => setMessage({ tone: 'error', text: describeError(e) })) }, [])
  useEffect(() => { load() }, [load])

  const search = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!q.trim()) return
    setBusy(true)
    setMessage(null)
    try { setResults(await api.library.search(q.trim())) } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) } finally { setBusy(false) }
  }
  const save = async (paperId: string, campaignId: string, label: string) => {
    setMessage(null)
    try {
      const r = await api.library.save(paperId, campaignId)
      setMessage({ tone: 'ok', text: r.added ? `Saved to ${label}.` : `Already in ${label}.` })
      if (results) setResults({ ...results, papers: results.papers?.map((p) => p.paper_id === paperId && !campaignId ? { ...p, in_library: true } : p) })
      load()
    } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) }
  }

  const papers = (lib?.papers ?? []).filter((p) => project === 'all' || (project === 'mine' ? p.mine : p.projects.some((x) => x.campaign_id === project)))
  return (
    <div className="team-page lib-page">
      <div className="lib-head"><h1>Library</h1><button className="btn-sm lit-btn" onClick={() => setZotero(true)}>Zotero</button></div>
      {zotero && <ZoteroDialog onClose={() => { setZotero(false); load() }} />}
      <div className="team-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'saved'} className={tab === 'saved' ? 'on' : ''} onClick={() => setTab('saved')}>Saved · {lib?.papers.length ?? 0}</button>
        <button role="tab" aria-selected={tab === 'search'} className={tab === 'search' ? 'on' : ''} onClick={() => setTab('search')}>Search</button>
      </div>
      {message && <div className={`panel-message ${message.tone}`} style={{ margin: '8px 0' }}>{message.text}</div>}

      {tab === 'saved' && lib && (
        <>
          <div className="lit-chips" role="group" aria-label="Project">
            {[['all', 'Everything'], ['mine', 'Only mine'], ...lib.projects.filter((p) => lib.papers.some((x) => x.projects.some((y) => y.campaign_id === p.campaign_id))).map((p) => [p.campaign_id, p.name])].map(([k, l]) => (
              <button key={k} className={`lit-chip${project === k ? ' on' : ''}`} aria-pressed={project === k} onClick={() => setProject(k)}>{l}</button>
            ))}
          </div>
          <div className="team-note">Papers you saved, and the literature of the projects you are in. Papers saved to a project also appear on that project’s Literature page.</div>
          <div className="lib-list">
            {papers.map((p) => (
              <article key={p.paper_id} className="lib-card">
                <div className="lit-title">{p.title}</div>
                <div className="lit-sub">{p.byline}{p.cites_per_year != null && ` · ${p.cites_per_year} cit/yr`} · <span className="lit-key">{p.key}</span> <PaperLinks p={p} /></div>
                <div className="lib-tags">
                  {p.mine && <span className="lit-tag">yours</span>}
                  {p.in_zotero && <span className="lit-zot" title="In your Zotero">Z ✓</span>}
                  {p.projects.map((x) => <button key={x.campaign_id} className="lit-tag lib-proj" onClick={() => onOpenProject(x.campaign_id)}>{x.name}</button>)}
                  <SaveTo projects={lib.projects.filter((x) => !p.projects.some((y) => y.campaign_id === x.campaign_id))} mine={p.mine}
                    onSave={(cid, label) => save(p.paper_id, cid, label)}
                    onRemoveMine={async () => { await api.library.remove(p.paper_id); load() }} />
                </div>
              </article>
            ))}
            {!papers.length && <div className="lit-empty">Nothing here yet. Search the indexes, or let the agents bring papers into your projects.</div>}
          </div>
        </>
      )}

      {tab === 'search' && (
        <>
          <form className="team-row-form" onSubmit={search}>
            <input placeholder="Search OpenAlex, arXiv, Crossref and Semantic Scholar" aria-label="Search" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
            <button className="btn-sm btn-modify" disabled={busy || !q.trim()}>{busy ? 'Searching…' : 'Search'}</button>
          </form>
          {results && (
            <div className="lib-list">
              {results.failed.length > 0 && <div className="team-note">{results.failed.join(', ')} did not answer; results come from {results.reached.join(', ') || 'no index'}.</div>}
              {(results.papers ?? []).map((p) => (
                <article key={p.paper_id} className="lib-card">
                  <div className="lit-title">{p.title}</div>
                  <div className="lit-sub">{p.byline}{p.cites_per_year != null && ` · ${p.cites_per_year} cit/yr`} · <span className="lit-ok">✓ {p.found_in.join(', ')}</span> <PaperLinks p={p} /></div>
                  {p.abstract && <p className="lib-abstract">{p.abstract.split(' ').slice(0, 60).join(' ')}{p.abstract.split(' ').length > 60 ? '…' : ''}</p>}
                  <div className="lib-tags">
                    {p.in_library && <span className="lit-tag">yours</span>}
                    <SaveTo projects={lib?.projects ?? []} mine={p.in_library} onSave={(cid, label) => save(p.paper_id, cid, label)} />
                  </div>
                </article>
              ))}
              {!results.papers?.length && <div className="lit-empty">Nothing found.</div>}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function SaveTo({ projects, mine, onSave, onRemoveMine }: {
  projects: { campaign_id: string; name: string }[]
  mine: boolean
  onSave: (campaignId: string, label: string) => void
  onRemoveMine?: () => void
}) {
  return (
    <span className="lib-save">
      {!mine && <button className="btn-sm lit-btn" onClick={() => onSave('', 'your library')}>Save</button>}
      {projects.length > 0 && (
        <select aria-label="Add to a project" value="" onChange={(e) => { const p = projects.find((x) => x.campaign_id === e.target.value); if (p) onSave(p.campaign_id, p.name) }}>
          <option value="">Add to a project…</option>
          {projects.map((p) => <option key={p.campaign_id} value={p.campaign_id}>{p.name}</option>)}
        </select>
      )}
      {mine && onRemoveMine && <button className="link-btn" onClick={onRemoveMine}>Remove from yours</button>}
    </span>
  )
}
