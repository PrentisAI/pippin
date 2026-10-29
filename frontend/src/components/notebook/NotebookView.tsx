/** The Notebook: on the left, every project with its runs (a page each, written from the run), your clips, your
 *  notes and what you saved; on the right, the one open. Opened on a run from anywhere with openNotebook(). */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { NbClip, NbNote, NbOverview } from '../../types'
import { MemoryView } from '../memory/MemoryView'
import { ClipsView } from './Clips'
import { Editor } from './Editor'
import { RunPage } from './RunPage'

type Sel = { kind: 'run'; cid: string; rid: string } | { kind: 'clips' } | { kind: 'note'; id: string } | { kind: 'saved' }

let pending: Sel | null = null
/** Open the notebook on a run's page (or the clips, a note), from anywhere in the console. */
export function openNotebook(sel: Sel) {
  pending = sel
  window.dispatchEvent(new CustomEvent('aurora:open-page', { detail: 'memory' }))
  window.dispatchEvent(new CustomEvent('aurora:open-notebook'))
}

const STATUS_DOT: Record<string, string> = { completed: 'done', running: 'live', starting: 'live', awaiting_human: 'wait', failed: 'bad', error: 'bad' }

export function NotebookView({ onOpenRun }: { onOpenRun: (cid: string, rid: string, report?: boolean) => void }) {
  const [data, setData] = useState<NbOverview | null>(null)
  const [clips, setClips] = useState<NbClip[]>([])
  const [error, setError] = useState<string | null>(null)
  const [sel, setSel] = useState<Sel | null>(() => { const p = pending; pending = null; return p })
  const [closed, setClosed] = useState<Set<string>>(new Set())
  const load = useCallback(async () => {
    try {
      const [d, c] = await Promise.all([api.notebook.get(), api.notebook.clips()])
      setData(d); setClips(c)
      // first open: the latest run of your own work, else what you saved
      setSel((s) => {
        if (s) return s
        const own = d.projects.filter((p) => !p.example && p.runs.length)
        const p = own[0] ?? d.projects.find((x) => x.runs.length)
        return p ? { kind: 'run', cid: p.campaign_id, rid: p.runs[p.runs.length - 1].run_id } : { kind: 'saved' }
      })
    } catch (e) { setError(describeError(e)) }
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const take = () => { if (pending) { setSel(pending); pending = null } }
    window.addEventListener('aurora:open-notebook', take)
    return () => window.removeEventListener('aurora:open-notebook', take)
  }, [])

  const newNote = async () => {
    try { const n = await api.notebook.newNote({}); await load(); setSel({ kind: 'note', id: n.note_id }) } catch (e) { setError(describeError(e)) }
  }
  const is = (s: Sel) => JSON.stringify(s) === JSON.stringify(sel)
  const project = sel?.kind === 'run' ? data?.projects.find((p) => p.campaign_id === sel.cid) : undefined

  return (
    <div className="nbv">
      <nav className="nbv-nav" aria-label="Notebook">
        <p className="nbv-h">Notebook</p>
        {error && <p className="nb-err">{error}</p>}
        <p className="nbv-sec">Experiments</p>
        {data && !data.projects.some((p) => p.runs.length) && <p className="nbv-none">A page is written here for every run you start.</p>}
        {data?.projects.filter((p) => p.runs.length).map((p) => {
          const shut = closed.has(p.campaign_id)
          return (
            <div key={p.campaign_id} className="nbv-proj">
              <button className="nbv-proj-h" aria-expanded={!shut}
                onClick={() => setClosed((s) => { const n = new Set(s); if (shut) n.delete(p.campaign_id); else n.add(p.campaign_id); return n })}>
                <span className="nbv-caret" aria-hidden="true">{shut ? '▸' : '▾'}</span>{p.name}</button>
              {!shut && p.runs.map((r, i) => {
                const me: Sel = { kind: 'run', cid: p.campaign_id, rid: r.run_id }
                return (
                  <button key={r.run_id} className={`nbv-item${is(me) ? ' on' : ''}`} onClick={() => setSel(me)} title={r.name}>
                    <span className={`nbv-dot ${STATUS_DOT[r.status] ?? ''}`} aria-hidden="true" />Run {i + 1} · {r.name}</button>
                )
              })}
            </div>
          )
        })}
        <p className="nbv-sec">Reading</p>
        <button className={`nbv-item${is({ kind: 'clips' }) ? ' on' : ''}`} onClick={() => setSel({ kind: 'clips' })}>
          Clips{clips.length ? <span className="nbv-n">{clips.length}</span> : null}</button>
        <button className={`nbv-item${is({ kind: 'saved' }) ? ' on' : ''}`} onClick={() => setSel({ kind: 'saved' })}
          title="Links, posts and files you saved, with the claims taken from them">Saved</button>
        <p className="nbv-sec">Notes<button className="nbv-add" onClick={newNote} title="A new note" aria-label="A new note">+</button></p>
        {data?.notes.map((n) => {
          const me: Sel = { kind: 'note', id: n.note_id }
          return <button key={n.note_id} className={`nbv-item${is(me) ? ' on' : ''}`} onClick={() => setSel(me)}>{n.title || 'Untitled'}</button>
        })}
        {data && !data.notes.length && <button className="nbv-item nbv-ghost" onClick={newNote}>Write a note</button>}
      </nav>
      <div className="nbv-main">
        {!sel || !data ? <div className="nb-page"><p className="lit-sub">Loading…</p></div>
          : sel.kind === 'run' ? <RunPage key={`${sel.cid}/${sel.rid}`} campaignId={sel.cid} runId={sel.rid} project={project?.name ?? ''} onOpenRun={onOpenRun} clips={clips} />
          : sel.kind === 'clips' ? <ClipsView clips={clips} projects={data.projects} onChanged={load} />
          : sel.kind === 'note' ? <NotePage key={sel.id} id={sel.id} clips={clips} onChanged={load} onGone={() => { setSel({ kind: 'clips' }); load() }} />
          : <MemoryView embedded />}
      </div>
    </div>
  )
}

function NotePage({ id, clips, onChanged, onGone }: { id: string; clips: NbClip[]; onChanged: () => void; onGone: () => void }) {
  const [n, setN] = useState<NbNote | null>(null)
  const [title, setTitle] = useState('')
  const [error, setError] = useState<string | null>(null)
  // a title typed and not yet saved is saved when the note is left
  const unsaved = useRef<string | null>(null)
  useEffect(() => {
    api.notebook.note(id).then((x) => { setN(x); setTitle(x.title) }).catch((e) => setError(describeError(e)))
    return () => { if (unsaved.current !== null) api.notebook.saveNote(id, { title: unsaved.current }).then(onChanged).catch(() => {}) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])
  if (error) return <div className="nb-page"><p className="nb-err">{error}</p></div>
  if (!n) return <div className="nb-page"><p className="lit-sub">Loading…</p></div>
  return (
    <article className="nb-page nb-note">
      <div className="nb-note-top">
        <input className="nb-note-title" value={title} placeholder="Untitled" aria-label="Title"
          onChange={(e) => { setTitle(e.target.value); unsaved.current = e.target.value }}
          onBlur={async () => { if (unsaved.current !== null) { unsaved.current = null; await api.notebook.saveNote(id, { title }); onChanged() } }}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
        <button className="nb-btn quiet" onClick={async () => { if (window.confirm('Delete this note?')) { await api.notebook.deleteNote(id); onGone() } }}>Delete</button>
      </div>
      <Editor html={n.html} clips={clips} autofocus={!n.text} placeholder="Write as much as you like. Headings, lists and quotes from the toolbar; put in a clip where you need its words."
        onSave={async (html, text) => { await api.notebook.saveNote(id, { html, text }); if (!n.text) onChanged() }} />
    </article>
  )
}
