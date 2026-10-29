/** Clips: passages taken from papers word for word, each with where it is (the paper, the page) and, under it, what
 *  you make of it in your own words. A clip opens the paper at its page; it can be for a project and a run. */
import { useState } from 'react'
import { api, describeError } from '../../api/client'
import type { NbClip, NbOverview } from '../../types'
import { readPaper } from '../reader/open'

export function ClipCard({ c, projects, onChanged }: {
  c: NbClip
  /** To say which project and run it is for (left out: not changed here). */
  projects?: NbOverview['projects']
  onChanged: () => void
}) {
  const [words, setWords] = useState(c.words)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn(); onChanged() } catch (e) { setError(describeError(e)) }
  }
  const proj = projects?.find((p) => p.campaign_id === c.campaign_id)
  return (
    <div className="nb-clip">
      <p className="nb-clip-src">{c.paper_title || c.paper_id} · p. {c.page_index + 1} · {new Date(c.created_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</p>
      <blockquote className="nb-clip-q">{c.quote}</blockquote>
      {editing ? (
        <form className="nb-clip-edit" onSubmit={(e) => { e.preventDefault(); act(() => api.notebook.changeClip(c.clip_id, { words })).then(() => setEditing(false)) }}>
          <textarea rows={2} value={words} autoFocus placeholder="What it means for you, in your own words" aria-label="In your words"
            onChange={(e) => setWords(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit() }} />
          <div className="nb-clip-row"><button className="nb-btn primary">Save</button>
            <button type="button" className="nb-btn quiet" onClick={() => { setWords(c.words); setEditing(false) }}>Cancel</button></div>
        </form>
      ) : c.words ? (
        <p className="nb-clip-words" onClick={() => setEditing(true)} title="Change">{c.words}</p>
      ) : (
        <button className="nb-clip-add" onClick={() => setEditing(true)}>+ In your own words</button>
      )}
      <div className="nb-clip-row">
        <button className="nb-btn" onClick={() => readPaper({ paper: c.paper_id, space: c.space || 'global', from: 'Notebook', quote: c.quote, page: c.page_index })}>
          Open at p. {c.page_index + 1}</button>
        {projects ? (
          <select className="nb-for" value={`${c.campaign_id}|${c.run_id}`} aria-label="For"
            onChange={(e) => { const [cid, rid] = e.target.value.split('|'); act(() => api.notebook.changeClip(c.clip_id, { campaign_id: cid, run_id: rid })) }}>
            <option value="|">Not for a project</option>
            {projects.filter((p) => !p.example || p.campaign_id === c.campaign_id).map((p) => [
              <option key={p.campaign_id} value={`${p.campaign_id}|`}>For {p.name}</option>,
              ...p.runs.map((r) => <option key={r.run_id} value={`${p.campaign_id}|${r.run_id}`}>For {p.name} · {r.name}</option>),
            ])}
          </select>
        ) : proj ? <span className="lit-sub">For {proj.name}</span> : null}
        <button className="nb-btn quiet nb-clip-del" onClick={() => { if (window.confirm('Delete this clip?')) act(() => api.notebook.deleteClip(c.clip_id)) }}>Delete</button>
      </div>
      {error && <p className="nb-err">{error}</p>}
    </div>
  )
}

/** All your clips, by paper, the paper clipped last first. */
export function ClipsView({ clips, projects, onChanged }: { clips: NbClip[]; projects: NbOverview['projects']; onChanged: () => void }) {
  if (!clips.length) return (
    <div className="nb-page">
      <h1 className="nb-title">Clips</h1>
      <div className="nb-empty">
        <b>No clips yet</b>
        <span>Open a paper in Pippin’s reader, select a passage and choose Clip. It is kept word for word, with its page, and you
          add what it means for you in your own words. A clip can be for a project or a run, and goes into your notes with one click.</span>
      </div>
    </div>
  )
  const papers: { id: string; title: string; clips: NbClip[] }[] = []
  for (const c of clips) {
    let g = papers.find((p) => p.id === c.paper_id)
    if (!g) papers.push(g = { id: c.paper_id, title: c.paper_title || c.paper_id, clips: [] })
    g.clips.push(c)
  }
  return (
    <div className="nb-page">
      <h1 className="nb-title">Clips</h1>
      <p className="lit-sub">{clips.length} clip{clips.length === 1 ? '' : 's'} from {papers.length} paper{papers.length === 1 ? '' : 's'}</p>
      {papers.map((g) => (
        <section key={g.id} className="nb-field">
          <h2 className="nb-paper">{g.title}</h2>
          {[...g.clips].sort((a, b) => a.page_index - b.page_index).map((c) => <ClipCard key={c.clip_id} c={c} projects={projects} onChanged={onChanged} />)}
        </section>
      ))}
    </div>
  )
}
