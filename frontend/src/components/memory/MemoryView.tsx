/** The Notebook page: what you read and save, kept as claims and checked against each other. One box saves anything
 *  (a link, some text, a file); below it is what you saved, what bears on your questions, and the week. How things
 *  come in on their own (the browser extension, the desktop app, what you follow, your notes) is set up once, at
 *  the bottom, folded away. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { MemFollow, MemQuestion, MemReport, MemSource } from '../../types'
import { MemoryPanel } from './MemoryPanel'

const PLATFORM: Record<string, string> = {
  paper: 'Paper', arxiv: 'arXiv', zhihu: 'Zhihu', x: 'X', reddit: 'Reddit', xiaohongshu: 'Xiaohongshu', blog: 'Blog',
  github: 'GitHub', web: 'Web', note: 'Your note', text: 'Text',
}

export function MemoryView() {
  const [refresh, setRefresh] = useState(0)
  const [tab, setTab] = useState<'saved' | 'bears' | 'week'>('saved')
  const bump = useCallback(() => setRefresh((x) => x + 1), [])
  return (
    <div className="nb">
      <div className="nb-col">
        <h1 className="nb-h1">Notebook</h1>
        <p className="nb-lede">What you read and save, kept as claims and checked against each other. Pippin brings it up when you
          read a paper, write, or ask; the browser extension and the desktop orb save into it as you go.</p>
        <Capture onSaved={bump} />
        <Questions onChanged={bump} />
        <div className="nb-tabs" role="tablist">
          {([['saved', 'Saved'], ['bears', 'What bears on your questions'], ['week', 'This week']] as const).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
        {tab === 'saved' ? <Saved refreshKey={refresh} onChanged={bump} />
          : tab === 'bears' ? <MemoryPanel refreshKey={refresh} bare />
          : <Report />}
        <details className="nb-setup">
          <summary>How things come in on their own <span className="lit-sub">the extension and the orb, people you follow, your Obsidian notes, the language</span></summary>
          <Connect />
          <Follows />
          <Notes />
          <Settings />
        </details>
      </div>
    </div>
  )
}

/** One box: a link, some text, or a file dropped on it. Enter saves (Shift+Enter for a new line). */
function Capture({ onSaved }: { onSaved: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const done = (what: string) => { setMsg({ tone: 'ok', text: `Reading ${what} and merging it with what you saved…` }); onSaved() }
  const save = async () => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true); setMsg(null)
    try {
      const url = /^https?:\/\/\S+$/.test(t) ? t : ''
      await api.memory.capture({ url, text: url ? '' : t, via: 'manual' })
      setText(''); done(url ? 'the page' : 'it')
    } catch (e) { setMsg({ tone: 'error', text: describeError(e) }) } finally { setBusy(false) }
  }
  const upload = async (f: File | undefined) => {
    if (!f) return
    setBusy(true); setMsg(null)
    try { await api.memory.captureFile(f, 'manual'); done(f.name) } catch (e) { setMsg({ tone: 'error', text: describeError(e) }) } finally { setBusy(false) }
  }
  return (
    <div className={`nb-capture${over ? ' over' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f); else setText(e.dataTransfer.getData('text/plain')) }}>
      <textarea rows={Math.min(8, Math.max(1, text.split('\n').length))} value={text} disabled={busy} aria-label="Save into your notebook"
        placeholder={over ? 'Drop it to save it' : 'Paste a link or some text, or drop a PDF, to save it'}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); save() } }} />
      <div className="nb-capture-row">
        <button className="link-btn" onClick={() => file.current?.click()} disabled={busy}>Choose a file</button>
        <input ref={file} type="file" hidden accept=".pdf,.md,.txt,application/pdf,text/plain"
          onChange={(e) => { upload(e.target.files?.[0]); e.target.value = '' }} />
        {msg && <span className={msg.tone === 'error' ? 'nb-err' : 'lit-sub'}>{msg.text}</span>}
        <button className="nb-save" disabled={!text.trim() || busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </div>
  )
}

/** What you saved, newest first: what came of each (merged, new, a conflict), and taking one out. */
function Saved({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const [sources, setSources] = useState<MemSource[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [forgetting, setForgetting] = useState<string | null>(null)
  const load = useCallback(async () => { try { setSources(await api.memory.sources(40)) } catch (e) { setErr(describeError(e)) } }, [])
  useEffect(() => { load() }, [load, refreshKey])
  const pending = (sources ?? []).some((s) => s.state === 'pending')
  useEffect(() => {
    if (!pending) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [pending, load])
  if (err) return <div className="panel-message error">{err}</div>
  if (!sources) return <div className="panel-empty">Loading…</div>
  if (!sources.length) return (
    <div className="nb-empty">
      <b>Nothing saved yet</b>
      <span>Paste a paper’s link above. Pippin reads it and keeps what it claims, each claim with the sentence it comes from.
        Save a second paper that says the same thing and the two become one claim with two sources; one that says the
        opposite is marked as a conflict. Later, when you read a paper in Pippin, it tells you which of your saves agree or disagree.</span>
    </div>
  )
  return (
    <div className="nb-saved">
      {sources.map((s) => (
        <article key={s.source_id} className={`nb-item st-${s.state}`}>
          <p className="nb-item-meta">{PLATFORM[s.platform] ?? s.platform} · {new Date(s.created_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            {s.state === 'pending' && ' · reading it…'}{s.state === 'failed' && ' · could not be read'}</p>
          <p className="nb-item-title">{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a> : s.title || 'Text'}</p>
          {(s.summary || s.error) && <p className="nb-item-sum">{s.summary || s.error}</p>}
          {s.state !== 'pending' && (forgetting === s.source_id
            ? <p className="nb-item-sum">Take it out of your notebook? Claims only it made go too.{' '}
                <button className="link-btn" onClick={async () => { await api.memory.forget(s.source_id); setForgetting(null); load(); onChanged() }}>Forget it</button>{' · '}
                <button className="link-btn" onClick={() => setForgetting(null)}>keep</button></p>
            : <button className="link-btn nb-forget" onClick={() => setForgetting(s.source_id)}>forget</button>)}
        </article>
      ))}
    </div>
  )
}

/** Your research questions, as a line of chips: the notebook organises what you saved by them. */
function Questions({ onChanged }: { onChanged: () => void }) {
  const [qs, setQs] = useState<MemQuestion[]>([])
  const [adding, setAdding] = useState(false)
  const [text, setText] = useState('')
  const load = useCallback(async () => setQs(await api.memory.questions()), [])
  useEffect(() => { load().catch(() => {}) }, [load])
  const add = async () => {
    if (!text.trim()) return
    await api.memory.addQuestion(text.trim()); setText(''); setAdding(false); load(); onChanged()
  }
  return (
    <div className="nb-qs">
      <span className="lit-sub" title="Questions you settle with the Mentor in a brainstorm come here on their own">Your questions</span>
      {qs.map((q) => (
        <span key={q.question_id} className="nb-q" title={q.text}><b>{q.label}</b> {q.text.length > 60 ? `${q.text.slice(0, 59)}…` : q.text}
          <button aria-label={`Archive ${q.label}`} onClick={async () => { await api.memory.archiveQuestion(q.question_id); load(); onChanged() }}>×</button></span>
      ))}
      {adding ? (
        <input className="nb-q-in" autoFocus value={text} placeholder="A question you are working on" aria-label="Add a question"
          onChange={(e) => setText(e.target.value)} onBlur={() => { if (!text.trim()) setAdding(false) }}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) add(); if (e.key === 'Escape') setAdding(false) }} />
      ) : <button className="nb-q-add" onClick={() => setAdding(true)}>+ {qs.length ? 'Add' : 'Add a question'}</button>}
    </div>
  )
}

function Settings() {
  const [lang, setLang] = useState('auto')
  useEffect(() => { api.memory.settings().then((s) => setLang(s.language)).catch(() => {}) }, [])
  return (
    <section className="mem-card">
      <h4>Language</h4>
      <label className="mem-line"><span className="lit-sub">Write explanations in</span>
        <select value={lang} onChange={async (e) => { setLang(e.target.value); await api.memory.setSettings(e.target.value) }}>
          <option value="auto">the language of what you read</option><option value="zh">Chinese</option><option value="en">English</option>
        </select></label>
      <span className="lit-sub">What Pippin may write into Zotero, Overleaf or your notes is set under the account button (all off until you allow it).</span>
    </section>
  )
}

function Report() {
  const [r, setR] = useState<MemReport | null>(null)
  useEffect(() => { api.memory.report(7).then(setR).catch(() => {}) }, [])
  return (
    <div className="mem-panel">
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

const VIA_LABEL = { extension: 'read in your browser', server: 'read by Pippin' }

/** People, topics and threads followed: what they post comes into the notebook on its own. */
function Follows() {
  const [fs, setFs] = useState<MemFollow[]>([])
  const [url, setUrl] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async () => setFs(await api.memory.follows()), [])
  useEffect(() => { load().catch(() => {}) }, [load])
  return (
    <section className="mem-card">
      <h4>What you follow</h4>
      <span className="lit-sub">A person or topic on Zhihu, X or Xiaohongshu (read in your browser by the extension, as you are signed in),
        a subreddit or thread, a blog’s feed. New posts come into your notebook and today’s digest.</span>
      {fs.map((f) => (
        <div key={f.follow_id} className="mem-follow">
          <span className="mem-line"><b>{PLATFORM[f.platform] ?? f.platform}</b><span className="mem-qtext">{f.label}</span>
            <button className="link-btn" onClick={async () => { await api.memory.changeFollow(f.follow_id, { paused: !f.paused }); load() }}>{f.paused ? 'resume' : 'pause'}</button>
            <button className="link-btn" aria-label="Stop following" onClick={async () => { await api.memory.deleteFollow(f.follow_id); load() }}>×</button></span>
          <span className="lit-sub">{VIA_LABEL[f.via]} · every {f.every_min} min · {f.seen} seen
            {f.last_run ? ` · last ${new Date(f.last_run * 1000).toLocaleTimeString()}` : ' · not read yet'}{f.last_error ? ` · ${f.last_error}` : ''}</span>
        </div>
      ))}
      <div className="mem-line"><input className="rd-in" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… to follow" aria-label="Follow"
        onKeyDown={async (e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && url.trim()) { try { await api.memory.addFollow(url.trim()); setUrl(''); setErr(null); load() } catch (x) { setErr((x as Error).message) } } }} /></div>
      {err && <div className="panel-message error">{err}</div>}
    </section>
  )
}

/** Connecting the browser extension and the desktop app: a token for each, shown once. */
function Connect() {
  const [devices, setDevices] = useState<{ device_id: string; name: string; last_used: number | null }[]>([])
  const [name, setName] = useState('')
  const [token, setToken] = useState<string | null>(null)
  const load = useCallback(async () => setDevices(await api.me.devices()), [])
  useEffect(() => { load().catch(() => {}) }, [load])
  return (
    <section className="mem-card">
      <h4>Browser extension and desktop app</h4>
      <span className="lit-sub">Each connects with a token of its own. Make one, paste it into the extension’s options or the app’s settings,
        with this console’s address ({window.location.origin}). Without accounts no token is needed.</span>
      {devices.map((d) => <div key={d.device_id} className="mem-line"><span className="mem-qtext">{d.name}</span>
        <span className="lit-sub">{d.last_used ? `used ${new Date(d.last_used * 1000).toLocaleString()}` : 'not used yet'}</span>
        <button className="link-btn" onClick={async () => { await api.me.revokeDevice(d.device_id); load() }}>revoke</button></div>)}
      <div className="mem-line"><input className="rd-in" value={name} onChange={(e) => setName(e.target.value)} placeholder="Name, e.g. Chrome on my Mac" aria-label="Device name" />
        <button className="btn-sm lit-btn" disabled={!name.trim()} onClick={async () => { const r = await api.me.connectDevice(name.trim()); setToken(r.token); setName(''); load() }}>Make a token</button></div>
      {token && <div className="mem-why"><span className="rv-small-h">Copy it now; it is not shown again</span><code className="mem-token">{token}</code>
        <button className="link-btn" onClick={() => { navigator.clipboard?.writeText(token).catch(() => {}) }}>copy</button></div>}
    </section>
  )
}

/** Your own notes: an Obsidian vault (or any folder of Markdown files) read by the console, or sent by the plugin. */
function Notes() {
  const [v, setV] = useState<{ path: string; folders: string; exclude: string; on: boolean; notes: number } | null>(null)
  const [msg, setMsg] = useState('')
  useEffect(() => {
    api.memory.vault().then((x) => setV({ path: x.path, folders: x.folders.join(', '), exclude: x.exclude.join(', '), on: x.on, notes: x.notes ?? 0 }))
      .catch(() => {})
  }, [])
  if (!v) return null
  const list = (s: string) => s.split(',').map((x) => x.trim()).filter(Boolean)
  const save = async (on: boolean) => {
    setMsg('')
    try {
      await api.memory.setVault({ path: v.path.trim(), folders: list(v.folders), exclude: list(v.exclude), on })
      setV({ ...v, on })
      if (on) { const r = await api.memory.scanVault(); setMsg(`Reading ${r.read} notes now; the rest follow every few minutes.`) }
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)) }
  }
  return (
    <section className="mem-card">
      <h4>Your notes</h4>
      <span className="lit-sub">Your own notes join your notebook: what they claim is merged with what you saved, and a paper that disagrees
        with your note is shown as such. Point the console at your Obsidian vault (a folder on the machine it runs on), or install the
        Obsidian plugin, which sends notes as you write them. Pippin never writes into the vault from here.</span>
      <input className="rd-in" value={v.path} onChange={(e) => setV({ ...v, path: e.target.value })} placeholder="Vault folder, e.g. ~/Documents/Research" aria-label="Vault folder" />
      <input className="rd-in" value={v.folders} onChange={(e) => setV({ ...v, folders: e.target.value })} placeholder="Only these subfolders (comma-separated; empty: all)" aria-label="Subfolders" />
      <input className="rd-in" value={v.exclude} onChange={(e) => setV({ ...v, exclude: e.target.value })} placeholder="Never these (e.g. Journal, Private)" aria-label="Excluded folders" />
      <div className="mem-line">
        {v.on ? <><span className="lit-sub">On · {v.notes} notes read</span><span className="mem-spacer" />
          <button className="btn-sm" onClick={() => save(true)}>Save and read now</button>
          <button className="link-btn" onClick={() => save(false)}>stop reading</button></>
          : <button className="btn-sm lit-btn" disabled={!v.path.trim()} onClick={() => save(true)}>Read this vault</button>}
      </div>
      {msg && <span className="lit-sub">{msg}</span>}
    </section>
  )
}
