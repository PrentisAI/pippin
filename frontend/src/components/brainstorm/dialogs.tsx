import { useEffect, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { Activity, AddFound, BsAttachment, BsCard, BsChat, BsSpace, ProjectFileEntry } from '../../types'
import { RULE_HINTS, RuleSelect } from '../LiteratureView'
import { Avatar, short, type Analysis } from './common'

function useEsc(onClose: () => void) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
}

export function ShareChat({ chat, ideas, members, onClose, onDone }: { chat: BsChat; ideas: BsCard[]; members: { user_id: string; name: string }[]; onClose: () => void; onDone: (msg: string) => void }) {
  const [what, setWhat] = useState<'all' | 'ideas'>('all')
  const [picked, setPicked] = useState<Set<string>>(new Set(ideas.filter((c) => c.chat_id === chat.chat_id).map((c) => c.card_id)))
  const [error, setError] = useState<string | null>(null)
  useEsc(onClose)
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Share this brainstorm" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Share “{chat.title}”</div>
        <div className="modal-body">
          {members.length > 0 && <div className="bs-row"><span className="bs-people">{members.slice(0, 6).map((m) => <Avatar key={m.user_id} name={m.name} />)}</span>
            <span className="modal-hint">{members.length} members of the project will see it.</span></div>}
          <label className="modal-check"><input type="radio" checked={what === 'all'} onChange={() => setWhat('all')} />
            <span><b>The whole conversation and its ideas</b><br /><span className="modal-hint">Members can read it, reply, and ask the agents here.</span></span></label>
          <label className="modal-check"><input type="radio" checked={what === 'ideas'} onChange={() => setWhat('ideas')} />
            <span><b>Only some ideas</b><br /><span className="modal-hint">The conversation stays private; the ideas you pick are shared with their evidence.</span></span></label>
          <div style={{ paddingLeft: 24, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {ideas.map((c) => (
              <label key={c.card_id} className="modal-check"><input type="checkbox" checked={picked.has(c.card_id)}
                onChange={(e) => { const n = new Set(picked); if (e.target.checked) n.add(c.card_id); else n.delete(c.card_id); setPicked(n) }} />{short(c.text, 80)}</label>
            ))}
            {!ideas.length && <span className="modal-hint">No private ideas of yours in this space.</span>}
          </div>
          <div className="modal-hint">Your Zotero highlights and other private material in it are not shared unless you share them one by one.</div>
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button className="btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn-sm btn-approve" onClick={async () => {
            try {
              const r = await api.brainstorm.share(chat.chat_id, what, [...picked])
              onDone(what === 'all' ? 'Shared with the project. Members can see it now.' : `Shared ${r.shared_cards} idea${r.shared_cards === 1 ? '' : 's'}; the conversation stays private.`)
            } catch (e) { setError(describeError(e)) }
          }}>Share</button>
        </div>
      </div>
    </div>
  )
}

export function Promote({ card, a, space, projects, onClose, onDone }: {
  card: BsCard; a: Analysis; space: BsSpace; projects: { space: string; name: string }[]
  onClose: () => void; onDone: (campaignId: string, live: boolean) => void
}) {
  const [ideation, setIdeation] = useState<'start' | 'roundtable' | 'brainstorm'>('start')
  const [rule, setRule] = useState<AddFound>('cited')
  const [project, setProject] = useState(space.project ? space.space : (projects[0]?.space ?? ''))
  const [hypothesis] = useState(card.text)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEsc(onClose)
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Turn into a proposal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Turn into a proposal</div>
        <div className="modal-body">
          {space.project ? <div className="modal-hint">Project: <b>{space.name}</b></div> : (
            <label className="bs-field" style={{ padding: 0 }}>Project
              <select value={project} onChange={(e) => setProject(e.target.value)}>{projects.map((s) => <option key={s.space} value={s.space}>{s.name}</option>)}</select></label>
          )}
          <div><b>Hypothesis:</b> {hypothesis}</div>
          <div className="share-section-title">Comes along</div>
          {[...a.forIt, ...a.against].map((c) => <div key={c.card_id}>• {c.paper ? `${c.paper.title} [${c.paper.key}]` : c.text} <span className="lit-ok">✓</span></div>)}
          {!a.forIt.length && !a.against.length && <div className="modal-hint">No linked papers.</div>}
          {a.questions.length > 0 && <><div style={{ marginTop: 6 }}>Open points the plan must answer:</div>{a.questions.map((q) => <div key={q.card_id}>• {q.text}{q.answer ? ` (answered: ${q.answer})` : ''}</div>)}</>}
          {a.next.length > 0 && <><div style={{ marginTop: 6 }}>Next steps from the brainstorm:</div>{a.next.map((n) => <div key={n.card_id}>• {n.text}</div>)}</>}
          <div className="modal-hint">The whole discussion on this card goes along too.</div>
          <label className="bs-field" style={{ padding: 0 }}>Papers the agents find in this run <RuleSelect value={rule} onChange={setRule} /></label>
          <div className="modal-hint">{RULE_HINTS[rule]}</div>
          <div className="share-section-title">Ideation for this run</div>
          <label className="modal-check"><input type="radio" checked={ideation === 'start'} onChange={() => setIdeation('start')} />
            <span><b>Start from this idea</b><br /><span className="modal-hint">Skip ideation; screening and planning work on it directly.</span></span></label>
          <label className="modal-check"><input type="radio" checked={ideation === 'roundtable'} onChange={() => setIdeation('roundtable')} />
            <span><b>Round table, agents only</b><br /><span className="modal-hint">As today: seats develop it and compete with alternatives; you choose at screening.</span></span></label>
          <label className="modal-check"><input type="radio" checked={ideation === 'brainstorm'} onChange={() => setIdeation('brainstorm')} />
            <span><b>Brainstorm with me</b><br /><span className="modal-hint">The round table runs live in this space; its seats add cards and you can join any discussion.</span></span></label>
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button className="btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn-sm btn-approve" disabled={busy || !project} onClick={async () => {
            setBusy(true)
            try {
              const r = await api.brainstorm.promote(card.card_id, { ideation, add_found: rule, campaign_id: space.project ? '' : project })
              onDone(r.campaign_id, ideation === 'brainstorm')
            } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
          }}>Create the proposal</button>
        </div>
      </div>
    </div>
  )
}

export function NewCard({ type, space, activity, onClose, onAdd }: {
  type: 'idea' | 'question' | 'note'; space: BsSpace; activity: Activity; onClose: () => void
  onAdd: (body: { type: 'idea' | 'question' | 'note'; text: string; vis: string; ask_skeptic: boolean }) => Promise<void>
}) {
  const [text, setText] = useState('')
  const [shareIt, setShareIt] = useState(false)
  const [ask, setAsk] = useState(activity !== 'off')
  const [error, setError] = useState<string | null>(null)
  useEsc(onClose)
  const label = type === 'question' ? 'What do you want answered?' : type === 'idea' ? 'The idea, in a sentence or two' : 'Note'
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="modal" role="dialog" aria-modal="true" aria-label={`New ${type}`} onMouseDown={(e) => e.stopPropagation()}
        onSubmit={async (e) => { e.preventDefault(); if (!text.trim()) return; try { await onAdd({ type, text: text.trim(), vis: shareIt ? 'project' : 'private', ask_skeptic: ask && type === 'idea' }) } catch (err) { setError(describeError(err)) } }}>
        <div className="modal-title">New {type}</div>
        <div className="modal-body">
          <label className="bs-field" style={{ padding: 0 }}>{label}
            <textarea className="lit-bib" style={{ minHeight: 80, fontFamily: 'inherit', fontSize: 13 }} autoFocus aria-label={label}
              placeholder={type === 'idea' ? 'e.g. Rebalancing should target leisure stations before noon on weekends' : ''} value={text} onChange={(e) => setText(e.target.value)} /></label>
          {space.project && <label className="modal-check"><input type="checkbox" checked={shareIt} onChange={(e) => setShareIt(e.target.checked)} />Share with the project (otherwise only you see it)</label>}
          {type === 'idea' && <label className="modal-check"><input type="checkbox" checked={ask} onChange={(e) => setAsk(e.target.checked)} />Let the Skeptic take a first look</label>}
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions"><button type="button" className="btn-sm" onClick={onClose}>Cancel</button><button className="btn-sm btn-approve" disabled={!text.trim()}>Add to the board</button></div>
      </form>
    </div>
  )
}

/** A paper from the Library (the project's literature, or your own), for the board or a message. */
export function PickPaper({ space, title, onClose, onPick }: { space: BsSpace; title: string; onClose: () => void; onPick: (paperId: string, label: string) => void }) {
  const [rows, setRows] = useState<{ paper_id: string; key: string; title: string; byline: string }[] | null>(null)
  const [q, setQ] = useState('')
  useEsc(onClose)
  useEffect(() => {
    if (space.project) api.literature.get(space.space).then((l) => setRows(l.papers)).catch(() => setRows([]))
    else api.library.get().then((l) => setRows(l.papers)).catch(() => setRows([]))
  }, [space.project, space.space])
  const shown = (rows ?? []).filter((p) => !q || `${p.title} ${p.byline} ${p.key}`.toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">{title}</div>
        <input placeholder="Search titles, authors, keys" aria-label="Search" value={q} onChange={(e) => setQ(e.target.value)} className="bs-search" autoFocus />
        <div className="lit-results">
          {shown.map((p) => (
            <button key={p.paper_id} className="bs-choice" onClick={() => onPick(p.paper_id, p.key)}>
              <span className="lit-title">{p.title}</span><span className="lit-sub">{p.byline} · {p.key}</span></button>
          ))}
          {rows && !shown.length && <div className="modal-hint">{rows.length ? 'Nothing matches.' : 'No papers yet: search the indexes or import some on the Literature page.'}</div>}
          {!rows && <div className="modal-hint">Loading…</div>}
        </div>
        <div className="modal-actions"><button className="btn-sm" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  )
}

/** A file from the project's files, to attach to a message. */
export function PickFile({ campaignId, onClose, onPick }: { campaignId: string; onClose: () => void; onPick: (a: BsAttachment) => void }) {
  const [path, setPath] = useState('')
  const [entries, setEntries] = useState<ProjectFileEntry[] | null>(null)
  useEsc(onClose)
  useEffect(() => { api.projectFiles.list(campaignId, path).then((r) => setEntries(r.entries)).catch(() => setEntries([])) }, [campaignId, path])
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Attach a project file" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Attach a file from the project</div>
        <div className="lit-sub">/{path} {path && <button className="link-btn" onClick={() => setPath(path.split('/').slice(0, -1).join('/'))}>← up</button>}</div>
        <div className="lit-results">
          {(entries ?? []).map((e) => (
            <button key={e.path} className="bs-choice" onClick={() => e.type === 'dir' ? setPath(e.path) : onPick({ kind: 'file', path: e.path, label: e.name })}>
              <span className="lit-title">{e.type === 'dir' ? `${e.name}/` : e.name}</span></button>
          ))}
          {entries && !entries.length && <div className="modal-hint">No files here. Upload files through the project chat’s “+”.</div>}
        </div>
        <div className="modal-actions"><button className="btn-sm" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  )
}
