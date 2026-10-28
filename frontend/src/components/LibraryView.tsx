import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { FeedItem, FeedKind, FeedView, LibraryView as Library, LitCheck, LitSearch } from '../types'
import { ago, askFrontDesk, initialsOf, useToast } from './feed/common'
import { FeedCard, type FeedActions } from './feed/FeedCard'
import { FollowingView } from './feed/FollowingView'
import { ScholarView } from './feed/ScholarView'
import { PaperLinks } from './LiteratureView'
import { ZoteroDialog } from './ZoteroDialog'
import { FirstTip } from '../onboarding/FirstTip'

const KINDS: [FeedKind | 'all', string][] = [['all', 'All'], ['paper', 'Papers'], ['post', 'Posts'], ['blog', 'Blogs'], ['discussion', 'Discussions']]
const SOURCE_ORDER = ['arxiv', 'openalex', 's2', 'hf', 'bluesky', 'mastodon', 'rss', 'x', 'discussion']

/** The Library: today's feed, picked for your projects; what you saved; searching the indexes. */
export function LibraryView({ onOpenProject }: { onOpenProject: (campaignId: string) => void }) {
  const [sub, setSub] = useState<null | 'following' | { person: string }>(null)
  const [tab, setTab] = useState<'feed' | 'saved' | 'search'>('feed')
  const [feed, setFeed] = useState<FeedView | null>(null)
  const [lib, setLib] = useState<Library | null>(null)
  const [saved, setSaved] = useState<FeedItem[]>([])
  const [kind, setKind] = useState<FeedKind | 'all'>('all')
  const [project, setProject] = useState('all')
  const [unfold, setUnfold] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [zotero, setZotero] = useState(false)
  const [adding, setAdding] = useState(false)
  const [, toast, toastNode] = useToast()

  const loadFeed = useCallback(() => { api.feed.get().then(setFeed).catch((e) => setError(describeError(e))) }, [])
  const loadSaved = useCallback(() => {
    api.library.get().then(setLib).catch((e) => setError(describeError(e)))
    api.feed.saved().then((r) => setSaved(r.items)).catch(() => {})
  }, [])
  useEffect(() => { loadFeed(); loadSaved() }, [loadFeed, loadSaved])
  // while the sources are being asked, look again every few seconds
  useEffect(() => {
    if (!feed?.refreshing) return
    const t = setInterval(loadFeed, 3000)
    return () => clearInterval(t)
  }, [feed?.refreshing, loadFeed])

  const patch = (id: string, change: Partial<FeedItem>) =>
    setFeed((f) => f && { ...f, items: f.items.map((x) => x.item_id === id ? { ...x, ...change } : x) })
  const act = async (fn: () => Promise<unknown>) => {
    setError(null)
    try { await fn() } catch (e) { toast({ text: describeError(e) }) }
  }
  const actions: FeedActions = {
    projects: feed?.projects ?? [],
    spaces: feed?.spaces ?? [],
    onSave: (it, cid) => act(async () => {
      const before = it.saved
      const r = await api.feed.save(it.item_id, cid)
      patch(it.item_id, { saved: r.saved })
      loadSaved()
      const undo = async () => {
        if (before) await api.feed.save(it.item_id, before.campaign_id); else await api.feed.unsave(it.item_id)
        patch(it.item_id, { saved: before }); loadSaved()
      }
      toast({ text: cid ? `Saved to ${r.saved.project}.${r.in_literature ? ` It is now on ${r.saved.project}’s Literature page.` : ''}` : 'Saved for later.', action: ['Undo', undo] })
    }),
    onUnsave: (it) => act(async () => { await api.feed.unsave(it.item_id); patch(it.item_id, { saved: null }); loadSaved() }),
    onBring: (it, space, name) => act(async () => {
      await api.feed.bring(it.item_id, space)
      toast({ text: `Added to ${space === 'global' ? 'your own ideas' : `${name}’s brainstorm`}.`,
        action: ['Open', () => window.dispatchEvent(new CustomEvent('aurora:open-brainstorm', { detail: space }))] })
    }),
    onAsk: (it, cid) => act(async () => {
      const r = await api.feed.ask(it.item_id, cid)
      askFrontDesk(r.campaign_id, r.ref)
    }),
    onHide: (it) => act(async () => {
      await api.feed.hide(it.item_id)
      setFeed((f) => f && { ...f, items: f.items.filter((x) => x.item_id !== it.item_id) })
      toast({ text: 'Hidden. Similar items will rank lower.', action: ['Undo', async () => { await api.feed.unhide(it.item_id); loadFeed() }] })
    }),
    onPerson: (pid) => setSub({ person: pid }),
  }

  if (sub === 'following') {
    return <FollowingView onBack={() => { setSub(null); loadFeed() }} onPerson={(pid) => setSub({ person: pid })} />
  }
  if (sub && typeof sub === 'object') {
    return <ScholarView personId={sub.person} onBack={() => { setSub(null); loadFeed() }} onFollowing={() => setSub('following')}
      onOpenProject={onOpenProject} />
  }

  const items = feed?.items ?? []
  const pool = items.filter((x) => unfold || !x.folded)
  const shown = pool.filter((x) => (kind === 'all' || x.kind === kind) && (project === 'all' || x.fits.some((f) => f.campaign_id === project)))
  const folded = items.filter((x) => x.folded).length
  const inFeed = (feed?.projects ?? []).filter((p) => items.some((x) => x.fits.some((f) => f.campaign_id === p.campaign_id)))
  const savedOther = saved.filter((x) => !x.is_paper)
  const savedCount = (lib?.papers.length ?? 0) + savedOther.length
  const today = feed ? new Date(feed.date * 1000).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : ''

  return (
    <div className="team-page lib-page fd-page">
      <FirstTip id="library" doc="concepts/literature.md">
        <b>The Library.</b> The papers of all your projects — the ones agents cite, each checked against the scholarly indexes,
        and the ones you add — with your notes and Zotero. The feed shows what is new from the people and topics you follow.
      </FirstTip>
      {/* the title, its buttons and the tabs stay in view while the page scrolls */}
      <div className="lib-top">
      <div className="lib-head">
        <h1>Library</h1>
        <div className="bs-row">
          <button className="btn-sm lit-btn" onClick={() => setSub('following')}>Sources and people</button>
          <button className="btn-sm lit-btn" onClick={() => setAdding(true)}>Add a paper</button>
          <button className="btn-sm lit-btn" onClick={() => setZotero(true)}>Zotero</button>
        </div>
      </div>
      <div className="team-tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'feed'} className={tab === 'feed' ? 'on' : ''} onClick={() => setTab('feed')}>Feed</button>
        <button role="tab" aria-selected={tab === 'saved'} className={tab === 'saved' ? 'on' : ''} onClick={() => setTab('saved')}>Saved · {savedCount}</button>
        <button role="tab" aria-selected={tab === 'search'} className={tab === 'search' ? 'on' : ''} onClick={() => setTab('search')}>Search</button>
      </div>
      </div>
      {zotero && <ZoteroDialog onClose={() => { setZotero(false); loadSaved() }} />}
      {adding && <AddToLibrary onClose={() => setAdding(false)} onAdded={() => { loadSaved(); toast({ text: 'Added to your library.' }) }} />}
      {error && <div className="panel-message error" style={{ margin: '8px 0' }}>{error}</div>}

      <div className="fd-cols">
        <div className="fd-main">
          {tab === 'feed' && (
            <>
              <div className="fd-dayline">
                <b>Today, {today} · picked for your projects</b>
                <span className="fd-mono">{feed?.refreshing ? 'gathering…' : `${shown.length} shown`}</span>
              </div>
              <div className="fd-status">
                {feed?.refreshing ? 'Asking your sources and ranking what is new against your projects…'
                  : feed?.last ? <>Updated {ago(feed.last)} · <button className="link-btn" onClick={() => act(async () => { await api.feed.refresh(); loadFeed() })}>Look again now</button></>
                    : feed ? 'Not gathered yet.' : 'Loading…'}
              </div>
              <div className="lit-chips" role="group" aria-label="Type">
                {KINDS.map(([k, l]) => <button key={k} className={`lit-chip${kind === k ? ' on' : ''}`} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>)}
              </div>
              {inFeed.length > 0 && (
                <div className="lit-chips" role="group" aria-label="Project">
                  {[{ campaign_id: 'all', name: 'All projects' }, ...inFeed].map((p) => (
                    <button key={p.campaign_id} className={`lit-chip${project === p.campaign_id ? ' on' : ''}`} aria-pressed={project === p.campaign_id}
                      onClick={() => setProject(p.campaign_id)}>{p.name}</button>
                  ))}
                </div>
              )}
              {shown.map((it) => <FeedCard key={it.item_id} it={it} project={project} actions={actions} />)}
              {feed && !shown.length && !feed.refreshing && (
                <div className="fd-note">
                  {!items.length && !feed.following_anything
                    ? <>Nothing is followed yet. <button className="link-btn" onClick={() => setSub('following')}>Follow people, topics and feeds</button>; the feed ranks what they publish against your projects.</>
                    : !items.length ? 'Nothing new came in over the last days. The sources are asked again every few hours.'
                      : 'Nothing left for this filter today.'}
                </div>
              )}
              {!unfold && folded > 0 && (
                <button className="btn-sm lit-btn fd-unfold" onClick={() => setUnfold(true)}>Show {folded} less relevant item{folded > 1 ? 's' : ''}</button>
              )}
            </>
          )}

          {tab === 'saved' && (
            <SavedTab lib={lib} saved={savedOther} onOpenProject={onOpenProject}
              onRemoveItem={(it) => act(async () => { await api.feed.unsave(it.item_id); loadSaved(); loadFeed() })}
              onChanged={loadSaved} />
          )}
          {tab === 'search' && <SearchTab lib={lib} onChanged={loadSaved} />}
        </div>

        <aside className="fd-side">
          <section className="fd-box">
            <div className="fd-box-head"><h3>Following</h3><button className="link-btn" onClick={() => setSub('following')}>Manage</button></div>
            {(feed?.people ?? []).map((p) => (
              <button key={p.person_id} className="fd-person" onClick={() => setSub({ person: p.person_id })}>
                <span className="bs-avatar">{p.initials || initialsOf(p.name)}</span>
                <span className="fd-person-main"><span>{p.name}</span><span className="lit-sub">{p.activity}</span></span>
                {(p.fresh ?? 0) > 0 && <span className="fd-mono fd-fresh">{p.fresh} new</span>}
              </button>
            ))}
            {feed && !feed.people.length && <div className="lit-sub">Nobody yet. <button className="link-btn" onClick={() => setSub('following')}>Follow someone</button>: their papers, posts, blog and what others say about their work.</div>}
          </section>
          <section className="fd-box">
            <h3>Daily digest</h3>
            {feed && (feed.digest.to !== 'console'
              ? <div className="fd-small">Every day at {feed.digest.at}, the top {feed.digest.top} per project go to <b>{feed.digest.label}</b>.</div>
              : <div className="fd-small">Shown only here. <button className="link-btn" onClick={() => setSub('following')}>Send it to a channel</button> (WeCom, Feishu, Slack, Server酱).</div>)}
            <div className="lit-sub">Ranked against each project’s objective, its saved papers and what its runs found. “Not interested” teaches the ranking.</div>
          </section>
          {feed && (
            <section className="fd-small lit-sub">Sources today
              <div className="fd-mono fd-counts">
                {SOURCE_ORDER.filter((k) => k in feed.counts || k === 'x').map((k) => (
                  <span key={k}>{feed.sources[k] ?? k} {feed.counts[k] ?? (k === 'x' ? 'off' : 0)}</span>
                ))}
              </div>
              {Object.entries(feed.problems).map(([k, v]) => <div key={k} className="fd-warn">! {v}</div>)}
            </section>
          )}
        </aside>
      </div>
      {toastNode}
    </div>
  )
}

function SavedTab({ lib, saved, onOpenProject, onRemoveItem, onChanged }: {
  lib: Library | null; saved: FeedItem[]; onOpenProject: (cid: string) => void; onRemoveItem: (it: FeedItem) => void; onChanged: () => void
}) {
  const [project, setProject] = useState('all')
  if (!lib) return <div className="lit-sub">Loading…</div>
  const papers = lib.papers.filter((p) => project === 'all' || (project === 'mine' ? p.mine : p.projects.some((x) => x.campaign_id === project)))
  const save = async (paperId: string, campaignId: string) => { await api.library.save(paperId, campaignId); onChanged() }
  return (
    <>
      <div className="team-note">Papers saved to a project also appear on that project’s Literature page.</div>
      {saved.length > 0 && (
        <div className="lib-list">
          <div className="bs-eyebrow">Posts, blogs and discussions you saved</div>
          {saved.map((it) => (
            <article key={it.item_id} className="lib-card fd-saved">
              <div className="bs-row" style={{ justifyContent: 'space-between' }}>
                <span><span className={`fd-badge k-${it.kind}`}>{it.label}</span> <a className="lit-title" href={it.url} target="_blank" rel="noreferrer">{it.title}</a></span>
                <span className="lit-sub">{it.saved?.campaign_id ? it.saved.project : 'Saved for later'} · <button className="link-btn" onClick={() => onRemoveItem(it)}>Remove</button></span>
              </div>
              <div className="lit-sub">{it.source}</div>
            </article>
          ))}
        </div>
      )}
      <div className="lit-chips" role="group" aria-label="Project" style={{ marginTop: 12 }}>
        {[['all', 'All papers'], ['mine', 'Only mine'], ...lib.projects.filter((p) => lib.papers.some((x) => x.projects.some((y) => y.campaign_id === p.campaign_id))).map((p) => [p.campaign_id, p.name])].map(([k, l]) => (
          <button key={k} className={`lit-chip${project === k ? ' on' : ''}`} aria-pressed={project === k} onClick={() => setProject(k)}>{l}</button>
        ))}
      </div>
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
                onSave={(cid) => save(p.paper_id, cid)} onRemoveMine={async () => { await api.library.remove(p.paper_id); onChanged() }} />
            </div>
          </article>
        ))}
        {!papers.length && <div className="lit-empty">No papers here yet. Search above, add one by DOI or arXiv ID with “Add a paper”, bring a collection in with “Zotero”, or let the agents bring papers into your projects.</div>}
      </div>
    </>
  )
}

function SearchTab({ lib, onChanged }: { lib: Library | null; onChanged: () => void }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<LitSearch | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const search = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!q.trim()) return
    setBusy(true)
    setMessage(null)
    try { setResults(await api.library.search(q.trim())) } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) } finally { setBusy(false) }
  }
  const save = async (paperId: string, campaignId: string, label: string) => {
    try {
      const r = await api.library.save(paperId, campaignId)
      setMessage({ tone: 'ok', text: r.added ? `Saved to ${label}.` : `Already in ${label}.` })
      if (results) setResults({ ...results, papers: results.papers?.map((p) => p.paper_id === paperId && !campaignId ? { ...p, in_library: true } : p) })
      onChanged()
    } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) }
  }
  return (
    <>
      <form className="team-row-form" onSubmit={search}>
        <input placeholder="Search OpenAlex, arXiv, Crossref and Semantic Scholar" aria-label="Search" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
        <button className="btn-sm btn-modify" disabled={busy || !q.trim()}>{busy ? 'Searching…' : 'Search'}</button>
      </form>
      {message && <div className={`panel-message ${message.tone}`} style={{ margin: '8px 0' }}>{message.text}</div>}
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
                <SaveTo projects={lib?.projects ?? []} mine={!!p.in_library} onSave={(cid, label) => save(p.paper_id, cid, label)} />
              </div>
            </article>
          ))}
          {!results.papers?.length && <div className="lit-empty">Nothing found.</div>}
        </div>
      )}
    </>
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

/** Add a paper to your own library: a DOI, an arXiv ID or link, or a title; it is checked first. */
function AddToLibrary({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [check, setCheck] = useState<LitCheck | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.library.add({ text: text.trim() })
      setCheck(r.check)
      if (r.added) { onAdded(); onClose() }
    } catch (err) { setError(describeError(err)) } finally { setBusy(false) }
  }
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="modal lit-modal" role="dialog" aria-modal="true" aria-label="Add a paper" onMouseDown={(e) => e.stopPropagation()} onSubmit={add}>
        <div className="modal-title">Add a paper to your library</div>
        <div className="modal-body">
          <input autoFocus placeholder="A DOI, an arXiv ID or link, or the title" aria-label="Paper" value={text} onChange={(e) => setText(e.target.value)} />
          <div className="modal-hint">It is checked in OpenAlex, Crossref, arXiv and Semantic Scholar first; only a paper that is found goes in.</div>
          {check && check.status !== 'verified' && <div className="modal-error">Not added: {check.reason || 'no index has a paper like this'}.</div>}
          {error && <div className="modal-error">{error}</div>}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn-sm" onClick={onClose}>Cancel</button>
          <button className="btn-sm btn-approve" disabled={busy || text.trim().length < 4}>{busy ? 'Checking…' : 'Check and add'}</button>
        </div>
      </form>
    </div>
  )
}
