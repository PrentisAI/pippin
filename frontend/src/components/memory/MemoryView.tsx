/** The Memory page: save things into your memory, say what you are reading, keep your research questions, and see
 *  what surfaces. The browser extension and the desktop app do the saving and the "reading" for you; here you can
 *  do both by hand. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { MemAudit, MemFollow, MemQuestion, MemReport, MemSource } from '../../types'
import { MemoryPanel } from './MemoryPanel'

const PLATFORM: Record<string, string> = {
  paper: 'Paper', arxiv: 'arXiv', zhihu: 'Zhihu', x: 'X', reddit: 'Reddit', xiaohongshu: 'Xiaohongshu', blog: 'Blog',
  github: 'GitHub', web: 'Web', note: 'Your note', text: 'Text',
}

export function MemoryView() {
  const [refresh, setRefresh] = useState(0)
  const [report, setReport] = useState(false)
  const [audit, setAudit] = useState<string | null>(null)
  const bump = () => setRefresh((x) => x + 1)
  return (
    <div className="mem-page">
      <aside className="mem-side">
        <Save onSaved={bump} />
        <Reading onSet={bump} />
        <Questions onChanged={bump} />
        <Audits onOpen={setAudit} />
        <Follows />
        <Connect />
        <Notes />
        <Settings />
      </aside>
      <main className="mem-main">
        {audit ? <AuditReport id={audit} onClose={() => setAudit(null)} />
          : report ? <Report onClose={() => setReport(false)} /> : <MemoryPanel refreshKey={refresh} onReport={() => setReport(true)} />}
      </main>
    </div>
  )
}

function Save({ onSaved }: { onSaved: () => void }) {
  const [url, setUrl] = useState('')
  const [text, setText] = useState('')
  const [sources, setSources] = useState<MemSource[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [forgetting, setForgetting] = useState<string | null>(null)
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
          {s.state !== 'pending' && (forgetting === s.source_id
            ? <span className="mem-line"><span className="lit-sub">Take it out of your memory? Claims only it made go too.</span>
                <button className="btn-sm" onClick={async () => { await api.memory.forget(s.source_id); setForgetting(null); load(); onSaved() }}>Forget it</button>
                <button className="link-btn" onClick={() => setForgetting(null)}>keep</button></span>
            : <button className="link-btn mem-forget" onClick={() => setForgetting(s.source_id)}>forget</button>)}
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
        onKeyDown={async (e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing && text.trim()) { await api.memory.addQuestion(text.trim()); setText(''); load(); onChanged() } }} /></div>
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
      <span className="lit-sub">What Pippin may write into Zotero, Overleaf or your notes is set under the account button (all off until you allow it).</span>
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

const VIA_LABEL = { extension: 'read in your browser', server: 'read by Pippin' }

/** People, topics and threads followed: what they post comes into the memory on its own. */
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
        a subreddit or thread, a blog’s feed. New posts come into your memory and today’s digest.</span>
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
      <span className="lit-sub">Your own notes join your memory: what they claim is merged with what you saved, and a paper that disagrees
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

/** Checking a manuscript's references before you submit it. */
function Audits({ onOpen }: { onOpen: (id: string) => void }) {
  const [list, setList] = useState<MemAudit[]>([])
  const [tex, setTex] = useState('')
  const [bib, setBib] = useState('')
  const [open, setOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const load = useCallback(async () => setList(await api.memory.audits()), [])
  useEffect(() => { load().catch(() => {}) }, [load])
  const readFile = (f: File | undefined, set: (s: string) => void) => { if (f) f.text().then(set) }
  return (
    <section className="mem-card">
      <h4>Check a manuscript’s references</h4>
      <span className="lit-sub">Before you submit: whether each reference exists, was retracted, says what your sentence says it does,
        and which key work is missing. Nothing in the manuscript is changed. From Overleaf, the extension does this in one click.</span>
      {!open ? <div className="cw-row"><button className="btn-sm lit-btn" onClick={() => setOpen(true)}>Check one</button></div> : (
        <>
          <label className="lit-sub">The manuscript as a PDF <input type="file" accept=".pdf,application/pdf" onChange={async (e) => {
            const f = e.target.files?.[0]
            if (!f) return
            setErr(null)
            try {
              const bytes = new Uint8Array(await f.arrayBuffer())
              let bin = ''
              for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
              const a = await api.memory.auditPdf(btoa(bin), f.name.replace(/\.pdf$/i, ''))
              setOpen(false); load(); onOpen(a.audit_id)
            } catch (x) { setErr((x as Error).message) }
          }} /></label>
          <span className="lit-sub">or its sources:</span>
          <label className="lit-sub">The LaTeX (.tex) <input type="file" accept=".tex,.txt" onChange={(e) => readFile(e.target.files?.[0], setTex)} /></label>
          <textarea className="rd-in" rows={3} value={tex} onChange={(e) => setTex(e.target.value)} placeholder="or paste it" aria-label="LaTeX" />
          <label className="lit-sub">The bibliography (.bib) <input type="file" accept=".bib,.txt" onChange={(e) => readFile(e.target.files?.[0], setBib)} /></label>
          <textarea className="rd-in" rows={3} value={bib} onChange={(e) => setBib(e.target.value)} placeholder="or paste it" aria-label="BibTeX" />
          <div className="cw-row"><button className="btn-sm btn-modify" disabled={!tex.trim()} onClick={async () => {
            try { const a = await api.memory.audit(tex, bib); setOpen(false); setTex(''); setBib(''); load(); onOpen(a.audit_id) } catch (e) { setErr((e as Error).message) }
          }}>Check the references</button><button className="btn-sm lit-btn" onClick={() => setOpen(false)}>Cancel</button></div>
          {err && <div className="panel-message error">{err}</div>}
        </>
      )}
      {list.map((a) => <button key={a.audit_id} className="mem-row" onClick={() => onOpen(a.audit_id)}>
        <span>{a.title}</span><span className="lit-sub">{a.state === 'running' ? a.step || 'running…' : a.state} · {new Date(a.created_at * 1000).toLocaleDateString()}</span></button>)}
    </section>
  )
}

function AuditReport({ id, onClose }: { id: string; onClose: () => void }) {
  const [a, setA] = useState<MemAudit | null>(null)
  useEffect(() => {
    let gone = false
    const tick = async () => {
      const x = await api.memory.getAudit(id).catch(() => null)
      if (gone) return
      setA(x)
      if (x && x.state === 'running') setTimeout(tick, 2500)
    }
    tick()
    return () => { gone = true }
  }, [id])
  const r = a?.report ?? {}
  const flagged = (r.entries ?? []).filter((e) => e.status !== 'verified' || e.retracted)
  return (
    <div className="mem-panel">
      <div className="mem-head"><b className="mem-logo">{a?.title || 'References'}</b><button className="link-btn" onClick={onClose}>← Back</button></div>
      {!a ? <div className="panel-empty">Loading…</div> : a.state === 'running' ? <div className="panel-empty">{a.step || 'Checking…'}</div>
        : a.state === 'failed' ? <div className="panel-message error">{a.error}</div> : (
        <div className="mem-body">
          <span className="lit-sub">{r.counts?.entries} references · {r.counts?.verified} found in the indexes · {r.counts?.retracted} retracted ·
            {' '}{r.counts?.miscites} citations that do not fully hold · {r.counts?.missing} key works not cited</span>
          {(r.entries ?? []).some((e) => e.retracted) && <section className="mem-zone"><h4>Retracted</h4>
            {(r.entries ?? []).filter((e) => e.retracted).map((e) => <div key={e.key} className="mem-row static clash"><span><b>[{e.key}]</b> {e.found_title || e.title}</span><span className="lit-sub">{e.retracted}</span></div>)}</section>}
          {flagged.some((e) => e.status !== 'verified') && <section className="mem-zone"><h4>Not found as written</h4>
            {flagged.filter((e) => e.status !== 'verified').map((e) => <div key={e.key} className="mem-row static"><span><b>[{e.key}]</b> {e.title}</span><span className="lit-sub">{e.status}: {e.reason}</span></div>)}</section>}
          {(r.miscites ?? []).length > 0 && <section className="mem-zone"><h4>Citations that do not fully hold</h4>
            {r.miscites!.map((m, i) => <div key={i} className="mem-why">
              <span><b>[{m.key}]</b> line {m.line} · {m.verdict === 'no' ? 'not supported' : 'partly supported'}</span>
              <span className="lit-sub">“{m.sentence}”</span><span>{m.issue}</span>
              {m.quote && <blockquote className="pl-quote">{m.quote}</blockquote>}</div>)}</section>}
          {(r.missing ?? []).length > 0 && <section className="mem-zone"><h4>Key work not cited</h4>
            {r.missing!.map((m) => <div key={m.paper_id} className="mem-why"><span><b>{m.title}</b> <span className="lit-sub">{m.byline}</span></span><span>{m.why}</span>
              <button className="link-btn" style={{ alignSelf: 'flex-start' }} onClick={() => navigator.clipboard?.writeText(m.bibtex).catch(() => {})}>copy BibTeX</button></div>)}</section>}
          {((r.unused ?? []).length > 0 || (r.undefined ?? []).length > 0) && <section className="mem-zone"><h4>Housekeeping</h4>
            {(r.undefined ?? []).length > 0 && <span>Cited but not in the .bib: {r.undefined!.join(', ')}</span>}
            {(r.unused ?? []).length > 0 && <span className="lit-sub">In the .bib but never cited: {r.unused!.join(', ')}</span>}</section>}
        </div>
      )}
    </div>
  )
}
