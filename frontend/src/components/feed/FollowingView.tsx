import { useCallback, useEffect, useRef, useState } from 'react'
import { api, describeError } from '../../api/client'
import type { FeedIdentity, FeedPerson, Following, PersonCandidate } from '../../types'
import { ago, IdChip, PlacesToOpen, splitIds, useToast } from './common'

const WANTS: [keyof FeedPerson['wants'], string][] = [['papers', 'Papers'], ['posts', 'Posts'], ['blog', 'Blog'], ['code', 'Code'], ['videos', 'Videos'], ['about', 'Discussions about their work']]
const CATEGORY = /^[a-z-]+\.[A-Za-z-]+$|^(hep-th|hep-ph|hep-ex|hep-lat|gr-qc|quant-ph|nucl-th|nucl-ex|math-ph)$/

/** The people and sources the feed follows, and the daily digest. */
export function FollowingView({ onBack, onPerson, find = '' }: {
  onBack: () => void
  onPerson: (personId: string) => void
  /** Someone to look for straight away (typed into the feed's Follow box). */
  find?: string
}) {
  const [f, setF] = useState<Following | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [, toast, toastNode] = useToast()
  const load = useCallback(() => { api.feed.following().then(setF).catch((e) => setError(describeError(e))) }, [])
  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<Following | unknown>, ok?: string) => {
    setError(null)
    try {
      const r = await fn()
      if (r && typeof r === 'object' && 'people' in r) setF(r as Following); else load()
      if (ok) toast({ text: ok })
    } catch (e) { setError(describeError(e)) }
  }
  if (!f) return <div className="team-page fd-page"><button className="link-btn" onClick={onBack}>← Library</button><div className="lit-sub">{error ?? 'Loading…'}</div></div>

  const unfollow = (p: FeedPerson) => act(async () => {
    await api.feed.unfollow(p.person_id)
    toast({ text: `Unfollowed ${p.name}.`, action: ['Undo', () => act(() => api.feed.changePerson(p.person_id, { removed: false }))] })
  })

  return (
    <div className="team-page fd-page">
      <div><button className="link-btn" onClick={onBack}>← Library</button></div>
      <h1>Sources and people you follow</h1>
      <div className="team-note">One feed for all projects; each item is ranked against every project and shown under the ones it fits.</div>
      {error && <div className="panel-message error" style={{ margin: '8px 0' }}>{error}</div>}
      <div className="fd-cols">
        <div className="fd-main">
          <section className="fd-box">
            <div className="fd-box-head"><h3>People · {f.people.length}</h3><span className="lit-sub">Their papers, posts, blog, and discussions about their work</span></div>
            {f.people.map((p) => (
              <div key={p.person_id} className="fd-follow">
                <div className="bs-row">
                  <span className="bs-avatar">{p.initials}</span>
                  <div style={{ flex: 1, minWidth: 180 }}>
                    <button className="fd-followed" onClick={() => onPerson(p.person_id)}>{p.name}</button>
                    <div className="lit-sub">{p.aff}</div>
                  </div>
                  <button className="btn-sm lit-btn" onClick={() => unfollow(p)}>Unfollow</button>
                </div>
                <div className="bs-row">
                  {splitIds(p.ids).follow.map((i) => (
                    <IdentityToggle key={i.kind + (i.handle || i.url)} id={i} xKey={f.x_key}
                      onToggle={(follow) => act(() => api.feed.changePerson(p.person_id, { ids: p.ids.map((x) => x === i ? { ...x, follow } : x) }))} />
                  ))}
                </div>
                <div className="bs-row" style={{ gap: 16 }}>
                  {WANTS.map(([k, l]) => (
                    <label key={k} className="modal-check"><input type="checkbox" checked={p.wants[k]}
                      onChange={(e) => act(() => api.feed.changePerson(p.person_id, { wants: { [k]: e.target.checked } }))} /> {l}</label>
                  ))}
                </div>
              </div>
            ))}
            <FollowSomeone xKey={f.x_key} find={find} onFollowed={(p) => { load(); toast({ text: `Following ${p.name}. Their new work shows up in the feed as it comes in.` }) }} />
          </section>
        </div>

        <aside className="fd-side">
          <Topics f={f} act={act} />
          <section className="fd-box">
            <h3>Other sources</h3>
            <label className="modal-check"><input type="checkbox" checked={f.hf} onChange={(e) => act(() => api.feed.settings({ hf: e.target.checked }))} /> HF Daily Papers</label>
            <label className="modal-check"><input type="checkbox" checked={f.discussions} onChange={(e) => act(() => api.feed.settings({ discussions: e.target.checked }))} /> Discussions around papers in my projects</label>
            <div className="lit-sub">RSS and Atom feeds</div>
            {f.rss.map((r) => (
              <div key={r.url} className="fd-between fd-small">
                <a href={r.url} target="_blank" rel="noreferrer" className="fd-ellipsis" title={r.url}>{r.title || r.url}</a>
                <span className="bs-row" style={{ flexWrap: 'nowrap' }}>
                  {r.state === 'error' ? <span className="fd-warn" title={r.error}>! {r.error.includes('not') ? 'feed not found' : 'not answering'}</span>
                    : <span className="lit-ok">✓ {r.last_ok ? ago(r.last_ok) : 'added'}</span>}
                  <button className="link-btn" aria-label={`Stop following ${r.title}`} onClick={() => act(() => api.feed.removeRss(r.url))}>×</button>
                </span>
              </div>
            ))}
            <AddFeed onAdd={(url) => act(() => api.feed.addRss(url), 'Following the feed.')} />
            <XKey on={f.x_key} act={act} />
          </section>
          <Digest f={f} act={act} />
        </aside>
      </div>
      {toastNode}
    </div>
  )
}

function IdentityToggle({ id, xKey, onToggle }: { id: FeedIdentity; xKey: boolean; onToggle: (follow: boolean) => void }) {
  const can = id.state !== 'muted' && (id.kind !== 'x' || xKey)
  const state = id.state === 'muted' ? 'muted' : id.kind === 'x' && !xKey ? 'warn' : id.follow ? 'ok' : 'muted'
  if (!can) return <IdChip state={state} label={id.label} note={id.note} />
  return (
    <button className="fd-chipbtn" aria-pressed={id.follow} title={id.follow ? 'Followed: click to stop' : 'Not followed: click to follow'}
      onClick={() => onToggle(!id.follow)}><IdChip state={state} label={id.label + (id.follow ? '' : ' · not followed')} note={id.note} /></button>
  )
}

function Topics({ f, act }: { f: Following; act: (fn: () => Promise<unknown>, ok?: string) => void }) {
  const [kw, setKw] = useState('')
  const add = (e: React.FormEvent) => {
    e.preventDefault()
    const v = kw.trim()
    if (!v) return
    setKw('')
    if (CATEGORY.test(v)) act(() => api.feed.settings({ topics: [...f.topics, v] }), `Following arXiv ${v}.`)
    else act(() => api.feed.settings({ keywords: [...f.keywords, v] }))
  }
  return (
    <section className="fd-box">
      <h3>Topics</h3>
      <div className="lit-sub">arXiv categories</div>
      <div className="bs-row">
        {f.topics.map((t) => <button key={t} className="lit-chip" aria-label={`Remove ${t}`} onClick={() => act(() => api.feed.settings({ topics: f.topics.filter((x) => x !== t) }))}>{t} <span aria-hidden="true">×</span></button>)}
        {!f.topics.length && <span className="lit-sub">None. Type one below, like cs.LG or stat.AP.</span>}
      </div>
      <div className="lit-sub">Keywords, searched across all sources</div>
      <div className="bs-row">
        {f.keywords.map((t) => <button key={t} className="lit-chip" aria-label={`Remove ${t}`} onClick={() => act(() => api.feed.settings({ keywords: f.keywords.filter((x) => x !== t) }))}>{t} <span aria-hidden="true">×</span></button>)}
      </div>
      <form className="bs-row" style={{ flexWrap: 'nowrap' }} onSubmit={add}>
        <input className="fd-input" placeholder="Add a keyword or an arXiv category" aria-label="Add a keyword" value={kw} onChange={(e) => setKw(e.target.value)} />
        <button className="btn-sm lit-btn" disabled={!kw.trim()}>Add</button>
      </form>
      <label className="modal-check"><input type="checkbox" checked={f.from_objectives} onChange={(e) => act(() => api.feed.settings({ from_objectives: e.target.checked }))} /> Topics from my projects’ objectives</label>
      {f.from_objectives && f.derived.map((d) => (
        <div key={d.project} className="lit-sub">{d.project}: {[...d.keywords, ...d.categories].join(' · ') || 'worked out at the next update'}</div>
      ))}
    </section>
  )
}

function AddFeed({ onAdd }: { onAdd: (url: string) => void }) {
  const [url, setUrl] = useState('')
  return (
    <form className="bs-row" style={{ flexWrap: 'nowrap' }} onSubmit={(e) => { e.preventDefault(); if (url.trim()) { onAdd(url.trim()); setUrl('') } }}>
      <input className="fd-input" placeholder="A blog or newsletter: its address" aria-label="Feed address" value={url} onChange={(e) => setUrl(e.target.value)} />
      <button className="btn-sm lit-btn" disabled={!url.trim()}>Add</button>
    </form>
  )
}

function XKey({ on, act }: { on: boolean; act: (fn: () => Promise<unknown>, ok?: string) => void }) {
  const [key, setKey] = useState('')
  return (
    <div className="fd-xrow">
      <div className="fd-between fd-small"><span>X (Twitter)</span><span className={on ? 'lit-ok' : 'lit-sub'}>{on ? '✓ on, with your key' : 'off · paid API'}</span></div>
      {on ? <button className="link-btn" onClick={() => act(() => api.feed.removeXKey(), 'The X key is removed.')}>Remove the X key</button> : (
        <>
          <form className="bs-row" style={{ flexWrap: 'nowrap' }} onSubmit={(e) => { e.preventDefault(); if (key.trim()) act(async () => { const r = await api.feed.setXKey(key.trim()); setKey(''); return r }, 'X accounts can be followed now.') }}>
            <input type="password" className="fd-input" placeholder="X API key (bearer token)" aria-label="X API key" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn-sm lit-btn" disabled={key.trim().length < 20}>Save</button>
          </form>
          <div className="lit-sub">Paste an X API key to follow X accounts. Bluesky and Mastodon work without one. The key is stored encrypted, like your other keys.</div>
        </>
      )}
    </div>
  )
}

function Digest({ f, act }: { f: Following; act: (fn: () => Promise<unknown>, ok?: string) => void }) {
  const d = f.digest
  const [at, setAt] = useState(d.at)
  const [top, setTop] = useState(String(d.top))
  useEffect(() => { setAt(d.at); setTop(String(d.top)) }, [d.at, d.top])
  return (
    <section className="fd-box">
      <h3>Daily digest</h3>
      <label className="fd-field">Send to
        <select value={d.to} onChange={(e) => act(() => api.feed.settings({ digest: { to: e.target.value } }))}>
          {d.channels.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          <option value="console">Only in the console</option>
        </select>
      </label>
      {!d.channels.length && <div className="lit-sub">To get it in WeCom, Feishu, Slack or WeChat (Server酱), add a channel under Settings → Notifications.</div>}
      <div className="bs-row fd-small">
        <label htmlFor="dg-at">At</label>
        <input id="dg-at" className="fd-input" style={{ width: 70 }} value={at} onChange={(e) => setAt(e.target.value)}
          onBlur={() => at !== d.at && act(() => api.feed.settings({ digest: { at } }))} />
        <label htmlFor="dg-top">top</label>
        <input id="dg-top" className="fd-input" style={{ width: 50 }} inputMode="numeric" value={top} onChange={(e) => setTop(e.target.value)}
          onBlur={() => Number(top) !== d.top && act(() => api.feed.settings({ digest: { top: Number(top) } }))} />
        <span>per project</span>
      </div>
      <label className="modal-check"><input type="checkbox" checked={d.skip} onChange={(e) => act(() => api.feed.settings({ digest: { skip: e.target.checked } }))} /> Skip days when nothing fits a project well</label>
      {d.to !== 'console' && <button className="link-btn" style={{ alignSelf: 'flex-start' }} onClick={() => act(() => api.feed.testDigest(), `Sent today’s digest to ${d.label}.`)}>Send today’s now, to see it</button>}
    </section>
  )
}

/** What the search is doing, as it does it: the steps so far, the latest one still going. */
function SearchSteps({ steps }: { steps: string[] }) {
  const shown = steps.length ? steps : ['Starting the search…']
  return (
    <ol className="fd-steps" aria-live="polite">
      {shown.slice(-8).map((s, i, all) => (
        <li key={steps.length - all.length + i} className={i === all.length - 1 ? 'now' : ''}>{s}</li>
      ))}
    </ol>
  )
}

/** Find a person, tell namesakes apart, and choose which of their places to follow. */
function FollowSomeone({ xKey, find: first = '', onFollowed }: { xKey: boolean; find?: string; onFollowed: (p: FeedPerson) => void }) {
  const [step, setStep] = useState(0)
  const [q, setQ] = useState(first)
  const box = useRef<HTMLDivElement>(null)
  const [cands, setCands] = useState<PersonCandidate[]>([])
  const [pick, setPick] = useState(0)
  const [ids, setIds] = useState<FeedIdentity[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [steps, setSteps] = useState<string[]>([])
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try { await fn() } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const search = (who: string) => {
    setSteps([])
    run(async () => {
      const r = await api.feed.findStream(who, (s) => setSteps((all) => [...all, s]))
      if (!r.candidates.length) { setError(`Nobody found for “${who}”. Try their profile link or ORCID.`); return }
      setCands(r.candidates)
      setPick(0)
      if (r.candidates.length === 1) await places(r.candidates[0]); else setStep(1)
    })
  }
  const find = (e: React.FormEvent) => { e.preventDefault(); search(q.trim()) }
  // asked for from the feed: look for them straight away, here in view
  const asked = useRef(false)
  useEffect(() => {
    if (!first.trim() || asked.current) return
    asked.current = true
    box.current?.scrollIntoView({ block: 'center' })
    search(first.trim())
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [first])
  const places = async (c: PersonCandidate) => {
    const r = await api.feed.identities(c)
    setIds(r.ids)
    setStep(2)
  }
  const cand = cands[pick]
  const name = cand?.name || q
  return (
    <div className="fd-follow" ref={box}>
      <h3>Follow someone</h3>
      {step === 0 && (
        <form className="bs-row" onSubmit={find}>
          <label className="fd-field" style={{ flex: 1, minWidth: 220 }}>Name, profile link, ORCID or handle
            <input className="fd-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ada Kowalski · 0000-0002-… · @someone.bsky.social" /></label>
          <button className="btn-sm btn-approve" style={{ alignSelf: 'flex-end' }} disabled={busy || !q.trim()}>{busy ? 'Searching the web…' : 'Find'}</button>
        </form>
      )}
      {step === 0 && busy && <SearchSteps steps={steps} />}
      {step === 1 && (
        <>
          <div className="fd-small">{cands.length} people could be “{q.trim()}”. Which one?</div>
          {cands.map((c, i) => (
            <label key={c.cid} className={`fd-choice${pick === i ? ' picked' : ''}`}>
              <input type="radio" name="pick" checked={pick === i} onChange={() => setPick(i)} />
              <span><span className="fd-bright">{c.name}{c.aff ? ` · ${c.aff}` : ''}{c.works || !c.checked ? ` · ${c.works} paper${c.works === 1 ? '' : 's'}` : ''}</span><br />
                <span className="lit-sub">{c.detail || 'No recent paper listed.'}</span></span>
            </label>
          ))}
          <div className="bs-row">
            <button className="btn-sm btn-approve" disabled={busy} onClick={() => run(() => places(cands[pick]))}>{busy ? 'Looking for their places…' : 'Next'}</button>
            <button className="btn-sm" onClick={() => setStep(0)}>Back</button>
          </div>
        </>
      )}
      {step === 2 && cand && (
        <>
          <div className="fd-small">{ids.some((i) => !['openalex', 's2', 'arxiv'].includes(i.kind) && i.state !== 'muted' && i.state !== 'link')
            ? `Where ${name} publishes and posts. Follow these?` : `Only their papers were found. Add their Bluesky, Mastodon or blog later from their page.`}</div>
          <div className="fd-idlist">
            {ids.map((i, k) => {
              if (i.state === 'link') return null
              const disabled = i.state === 'muted' || (i.kind === 'x' && !xKey)
              return (
                <label key={i.kind + k} className="modal-check" style={{ opacity: disabled ? 0.6 : 1 }}>
                  <input type="checkbox" checked={i.follow && !disabled} disabled={disabled}
                    onChange={(e) => setIds(ids.map((x, j) => j === k ? { ...x, follow: e.target.checked } : x))} />
                  <span>{i.label}{i.note && <span className="lit-sub"> · {i.note}</span>}</span>
                </label>
              )
            })}
          </div>
          <PlacesToOpen ids={ids} />
          <div className="bs-row">
            <button className="btn-sm btn-approve" disabled={busy || !ids.some((i) => i.follow)} onClick={() => run(async () => {
              const r = await api.feed.follow(cand, ids)
              setStep(0); setQ(''); setCands([]); setIds([])
              onFollowed(r.person)
            })}>Follow {name}</button>
            <button className="btn-sm" onClick={() => setStep(cands.length > 1 ? 1 : 0)}>Back</button>
          </div>
        </>
      )}
      {error && <div className="modal-error">{error}</div>}
    </div>
  )
}
