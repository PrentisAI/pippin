/** The Memory page: save things into your memory, say what you are reading, keep your research questions, and see
 *  what surfaces. The browser extension and the desktop app do the saving and the "reading" for you; here you can
 *  do both by hand. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { MemQuestion, MemReport, MemSource } from '../../types'
import { MemoryPanel } from './MemoryPanel'

const PLATFORM: Record<string, string> = {
  paper: 'Paper', arxiv: 'arXiv', zhihu: 'Zhihu', x: 'X', reddit: 'Reddit', xiaohongshu: 'Xiaohongshu', blog: 'Blog',
  github: 'GitHub', web: 'Web', note: 'Note', text: 'Text',
}

export function MemoryView() {
  const [refresh, setRefresh] = useState(0)
  const [report, setReport] = useState(false)
  const bump = () => setRefresh((x) => x + 1)
  return (
    <div className="mem-page">
      <aside className="mem-side">
        <Save onSaved={bump} />
        <Reading onSet={bump} />
        <Questions onChanged={bump} />
        <Settings />
      </aside>
      <main className="mem-main">
        {report ? <Report onClose={() => setReport(false)} /> : <MemoryPanel refreshKey={refresh} onReport={() => setReport(true)} />}
      </main>
    </div>
  )
}

function Save({ onSaved }: { onSaved: () => void }) {
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [sources, setSources] = useState<MemSource[]>([])
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async () => { try { setSources(await api.memory.sources(12)) } catch (e) { setErr((e as Error).message) } }, [])
  useEffect(() => { load() }, [load])
  const pending = sources.some((s) => s.state === 'pending')
  useEffect(() => {
    if (!pending) return
    const t = setInterval(async () => { await load(); }, 1500)
    return () => clearInterval(t)
  }, [pending, load])
  const [wasPending, setWasPending] = useState(false)
  useEffect(() => { if (wasPending && !pending) onSaved(); setWasPending(pending) }, [pending, wasPending, onSaved])
  const save = async () => {
    setErr(null)
    try {
      const looksUrl = /^https?:\/\//.test(url.trim())
      await api.memory.capture({ url: looksUrl ? url.trim() : '', text: looksUrl ? text : [url, text].filter(Boolean).join('\n\n'), via: 'manual' })
      setUrl(''); setText(''); load()
    } catch (e) { setErr((e as Error).message) }
  }
  return (
    <section className="mem-card">
      <h4>Save into your memory</h4>
      <input className="rd-in" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="A link (arXiv, a post, a page) or a few words" aria-label="Link" />
      <textarea className="rd-in" rows={3} value={text} onChange={(e) => setText(e.target.value)} aria-label="Text"
        placeholder="Or paste the text (for pages behind a login, until the browser extension saves them for you)" />
      <div className="cw-row"><button className="btn-sm btn-modify" disabled={!url.trim() && !text.trim()} onClick={save}>Save</button>
        <span className="lit-sub">It is read, and merged with what you saved before.</span></div>
      {err && <div className="panel-message error">{err}</div>}
      <div className="mem-saves">{sources.map((s) => (
        <div key={s.source_id} className={`mem-save st-${s.state}`}>
          <span className="mem-line"><b>{PLATFORM[s.platform] ?? s.platform}</b><span className="lit-sub">{s.title || s.url || 'Text'}</span></span>
          <span className="lit-sub">{s.state === 'pending' ? 'Reading it and merging…' : s.summary}</span>
        </div>
      ))}</div>
    </section>
  )
}

function Reading({ onSet }: { onSet: () => void }) {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  return (
    <section className="mem-card">
      <h4>What you are reading</h4>
      <span className="lit-sub">The browser extension and the desktop app say this on their own. By hand: a title, and the passage you are on.</span>
      <input className="rd-in" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" aria-label="What you are reading" />
      <textarea className="rd-in" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="The passage" aria-label="The passage" />
      <div className="cw-row"><button className="btn-sm lit-btn" disabled={!title.trim() && !text.trim()}
        onClick={async () => { await api.memory.context({ app: 'Console', title, selection: text, text, via: 'console' }); onSet() }}>Use as context</button></div>
    </section>
  )
}

function Questions({ onChanged }: { onChanged: () => void }) {
  const [qs, setQs] = useState<MemQuestion[]>([])
  const [text, setText] = useState('')
  const load = useCallback(async () => setQs(await api.memory.questions()), [])
  useEffect(() => { load() }, [load])
  return (
    <section className="mem-card">
      <h4>Your research questions</h4>
      {qs.map((q) => (
        <div key={q.question_id} className="mem-line"><b>{q.label}</b><span className="mem-qtext">{q.text}</span>
          {q.origin === 'mentor' && <span className="lit-sub">with the Mentor</span>}
          <button className="link-btn" aria-label="Archive" onClick={async () => { await api.memory.archiveQuestion(q.question_id); load(); onChanged() }}>×</button></div>
      ))}
      {!qs.length && <span className="lit-sub">None yet. Questions you settle with the Mentor come here on their own.</span>}
      <div className="mem-line"><input className="rd-in" value={text} onChange={(e) => setText(e.target.value)} placeholder="Add a question" aria-label="Add a question"
        onKeyDown={async (e) => { if (e.key === 'Enter' && text.trim()) { await api.memory.addQuestion(text.trim()); setText(''); load(); onChanged() } }} /></div>
    </section>
  )
}

function Settings() {
  const [lang, setLang] = useState('auto')
  useEffect(() => { api.memory.settings().then((s) => setLang(s.language)).catch(() => {}) }, [])
  return (
    <section className="mem-card">
      <h4>Settings</h4>
      <label className="mem-line"><span className="lit-sub">Write explanations in</span>
        <select value={lang} onChange={async (e) => { setLang(e.target.value); await api.memory.setSettings(e.target.value) }}>
          <option value="auto">the language of what you read</option><option value="zh">Chinese</option><option value="en">English</option>
        </select></label>
      <span className="lit-sub">What AURORA may write into Zotero, Overleaf or your notes is set under the account button (all off until you allow it).</span>
    </section>
  )
}

function Report({ onClose }: { onClose: () => void }) {
  const [r, setR] = useState<MemReport | null>(null)
  useEffect(() => { api.memory.report(7).then(setR).catch(() => {}) }, [])
  return (
    <div className="mem-panel">
      <div className="mem-head"><b className="mem-logo">This week</b><button className="link-btn" onClick={onClose}>← Back</button></div>
      {!r ? <div className="panel-empty">Loading…</div> : (
        <div className="mem-body">
          <span className="lit-sub">{r.sources} saved · {r.merged} merged into what you had · {r.new_units} new idea units · {r.conflicts.length} new conflicts</span>
          {r.questions.map((q) => (
            <section key={q.question_id} className="mem-zone"><h4>{q.label} · {q.text}</h4>
              {q.new_units.map((u) => <span key={u.unit_id} className="mem-row static">{u.claim}</span>)}
              {q.conflicts.map((c, i) => <span key={i} className="mem-row static clash">{c.a} ⟷ {c.b}</span>)}
              {!q.new_units.length && !q.conflicts.length && <span className="lit-sub">Nothing new.</span>}
            </section>
          ))}
          {r.moved.length > 0 && <section className="mem-zone"><h4>Changed your view</h4>{r.moved.map((m) => <span key={m.unit_id} className="mem-row static">{m.claim}</span>)}</section>}
          {r.answers.length > 0 && <section className="mem-zone"><h4>What you answered</h4>{r.answers.map((a, i) => <div key={i} className="mem-why"><span className="lit-sub">{a.question}</span><span>“{a.answer}”</span></div>)}</section>}
          {r.archive.length > 0 && <section className="mem-zone deep"><h4>Long sunk: could be archived</h4>{r.archive.map((m) => <span key={m.unit_id} className="mem-dot">{m.claim}</span>)}</section>}
        </div>
      )}
    </div>
  )
}
