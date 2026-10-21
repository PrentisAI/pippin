import { useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { AccessMember, Perm, Person, ProjectAccess, ReportLink, TeamGroup } from '../types'

const ROLE_NAMES: [string, string][] = [['owner', 'Owner'], ['editor', 'Editor'], ['commenter', 'Commenter'], ['viewer', 'Viewer'], ['custom', 'Custom']]
const PERM_ORDER: Perm[] = ['view', 'comment', 'edit', 'run', 'decide', 'share', 'manage']

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((x) => x[0]).join('').slice(0, 2).toUpperCase() || '?'
}

/** Who is in a project, with which rights, and how it is shared. */
export function ShareDialog({ campaignId, projectName, onClose }: { campaignId: string; projectName: string; onClose: () => void }) {
  const [access, setAccess] = useState<ProjectAccess | null>(null)
  const [people, setPeople] = useState<Person[]>([])
  const [groups, setGroups] = useState<TeamGroup[]>([])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('commenter')
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string; link?: string } | null>(null)
  const [report, setReport] = useState<ReportLink | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    api.access.get(campaignId).then(setAccess).catch((err) => setMessage({ tone: 'error', text: describeError(err) }))
    api.links.get(campaignId).then(setReport).catch(() => {})
    api.team.people().then(setPeople).catch(() => {})
    api.team.groups().then(setGroups).catch(() => {})
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [campaignId, onClose])

  const act = async (fn: () => Promise<ProjectAccess>, ok?: string) => {
    setBusy(true)
    setMessage(null)
    try {
      setAccess(await fn())
      if (ok) setMessage({ tone: 'ok', text: ok })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    } finally {
      setBusy(false)
    }
  }
  const invite = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.includes('@')) return
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.access.invite(campaignId, email.trim(), role)
      setAccess(r.access)
      setEmail('')
      setMessage(r.added ? { tone: 'ok', text: `${email} is now a member.` }
        : { tone: 'ok', text: r.team_role === 'guest' ? `Invited ${email} as a guest: they will see only this project. Send them this link:` : `Invited ${email}. Send them this link:`, link: r.link })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    } finally {
      setBusy(false)
    }
  }

  if (!access) {
    return (
      <div className="modal-backdrop" onMouseDown={onClose}>
        <div className="modal share-modal" onMouseDown={(e) => e.stopPropagation()}>{message ? <div className="modal-error">{message.text}</div> : 'Loading…'}</div>
      </div>
    )
  }
  const mine = new Set(access.my_rights)
  const canShare = mine.has('share')
  const canManage = mine.has('manage')
  const addable = [...people.map((p) => ({ principal: `user:${p.user_id}`, label: `${p.name} · ${p.email}` })),
    ...groups.map((g) => ({ principal: `group:${g.group_id}`, label: `Group: ${g.name} (${g.members.length})` }))]
    .filter((x) => !access.members.some((m) => m.principal === x.principal))
  const deciders = access.members.filter((m) => m.kind === 'user' && m.perms.includes('decide'))

  const memberRow = (m: AccessMember) => {
    const showPerms = open === m.principal || (m.role === 'custom' && open !== `-${m.principal}`)
    return (
      <div key={m.principal} className="share-member">
        <span className="share-avatar">{m.kind === 'group' ? 'G' : initials(m.name)}</span>
        <div className="share-who">
          <div className="share-name">{m.name}{m.team_role === 'guest' && <span className="share-guest">Guest{m.org ? ` · ${m.org}` : ''}</span>}</div>
          <div className="share-email">{m.kind === 'group' ? `${m.members} people` : m.email}</div>
        </div>
        <select aria-label={`Role of ${m.name}`} value={m.role} disabled={!canShare || busy}
          onChange={(e) => {
            const r = e.target.value
            if (r === 'custom') { setOpen(m.principal); act(() => api.access.setMember(campaignId, m.principal, 'custom', m.perms)) }
            else act(() => api.access.setMember(campaignId, m.principal, r))
          }}>
          {ROLE_NAMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {canShare && (
          <>
            <button className="link-btn" onClick={() => setOpen(showPerms ? `-${m.principal}` : m.principal)}>{showPerms ? 'Hide' : 'Details'}</button>
            <button className="link-btn danger" aria-label={`Remove ${m.name}`} disabled={busy}
              onClick={() => act(() => api.access.removeMember(campaignId, m.principal), `Removed ${m.name}. Their private brainstorms in this project stay theirs.`)}>Remove</button>
          </>
        )}
        {showPerms && (
          <div className="share-perms">
            {PERM_ORDER.map((p) => (
              <label key={p} className="modal-check">
                <input type="checkbox" checked={m.perms.includes(p)} disabled={!canShare || busy || (!mine.has(p) && !m.perms.includes(p))}
                  onChange={(e) => {
                    const next = e.target.checked ? [...m.perms, p] : m.perms.filter((x) => x !== p)
                    act(() => api.access.setMember(campaignId, m.principal, 'custom', next))
                  }} />
                {access.perm_labels[p]}
              </label>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className="modal share-modal" role="dialog" aria-modal="true" aria-label="Share the project" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-title">Share “{projectName}”</div>
        {canShare && (
          <form className="share-invite" onSubmit={invite}>
            <input list="share-people" type="text" placeholder="Email address" aria-label="Email address" value={email} onChange={(e) => setEmail(e.target.value)} />
            <datalist id="share-people">{people.map((p) => <option key={p.user_id} value={p.email}>{p.name}</option>)}</datalist>
            <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLE_NAMES.slice(0, 4).filter(([k]) => access.roles[k].every((p) => mine.has(p))).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <button className="btn-sm btn-approve" disabled={busy || !email.includes('@')}>Invite</button>
          </form>
        )}
        {canShare && addable.some((x) => x.principal.startsWith('group:')) && (
          <div className="share-hint">Or add a group:{' '}
            {addable.filter((x) => x.principal.startsWith('group:')).map((x) => (
              <button key={x.principal} className="link-btn" onClick={() => act(() => api.access.setMember(campaignId, x.principal, role))}>{x.label}</button>
            ))}
          </div>
        )}
        <div className="share-hint">People outside the team join as guests: they see only the projects they are invited to.</div>
        {message && (
          <div className={`panel-message ${message.tone}`} style={{ margin: '6px 0' }}>
            {message.text}
            {message.link && (
              <div className="share-link"><code>{message.link}</code>
                <button className="link-btn" onClick={() => { navigator.clipboard?.writeText(message.link!).catch(() => {}) }}>Copy</button></div>
            )}
          </div>
        )}
        <div className="share-members">{access.members.map(memberRow)}</div>
        {access.invites.length > 0 && (
          <div className="share-section">
            <div className="share-section-title">Invited, not joined yet</div>
            {access.invites.map((i) => (
              <div key={i.invite_id} className="share-pending">
                <span>{i.email} · {i.role}{i.team_role === 'guest' ? ' · guest' : ''}</span>
                {canShare && <button className="link-btn danger" onClick={() => act(() => api.access.cancelInvite(campaignId, i.invite_id))}>Cancel</button>}
              </div>
            ))}
          </div>
        )}
        <div className="share-note">Members see the project’s runs, results, files and reports, and whatever someone shares with the project. Each person’s conversations and brainstorms stay private until that person shares them; owners and admins cannot open them either.</div>

        <div className="share-section">
          <div className="share-section-title">Everyone else in {access.team}</div>
          <select aria-label="Team access" value={access.team_access} disabled={!canShare || busy || access.confidential}
            onChange={(e) => act(() => api.access.set(campaignId, { team_access: e.target.value as ProjectAccess['team_access'] }))}>
            <option value="none">No access: only the people above</option>
            <option value="view">Everyone in {access.team} can see it</option>
            <option value="comment">Everyone in {access.team} can see and comment</option>
          </select>
          {access.confidential && <div className="share-hint">Not available: the project is confidential.</div>}
        </div>

        <div className="share-section">
          <div className="share-section-title">Who answers a run’s decisions</div>
          {([['starter', 'Whoever started the run (owners can step in)'], ['owners', 'Only the project’s owners'], ['user', 'One person']] as const).map(([k, l]) => (
            <label key={k} className="modal-check">
              <input type="radio" name="deciders" checked={access.deciders === k} disabled={!canShare || busy || (k === 'user' && !deciders.length)}
                onChange={() => act(() => api.access.set(campaignId, k === 'user' ? { deciders: k, decider_user: deciders[0]?.principal.slice(5) } : { deciders: k }))} />
              {l}
            </label>
          ))}
          {access.deciders === 'user' && (
            <select aria-label="Who decides" value={access.decider_user ?? ''} disabled={!canShare || busy}
              onChange={(e) => act(() => api.access.set(campaignId, { deciders: 'user', decider_user: e.target.value }))}>
              {deciders.map((m) => <option key={m.principal} value={m.principal.slice(5)}>{m.name}</option>)}
            </select>
          )}
          <div className="share-hint">Only people who may answer decisions can be chosen. Every decision records who made it.</div>
        </div>

        <div className="share-section">
          <div className="share-section-title">Links outside the team</div>
          {report && (
            <>
              <label className="modal-check">
                <input type="checkbox" checked={!!report.link} disabled={busy || (!report.allowed && !report.link) || !canShare}
                  onChange={async (e) => {
                    setBusy(true)
                    setMessage(null)
                    try { setReport(e.target.checked ? await api.links.make(campaignId, 30) : await api.links.revoke(campaignId)) }
                    catch (err) { setMessage({ tone: 'error', text: describeError(err) }) } finally { setBusy(false) }
                  }} />
                Anyone with the link can read the report (read only)
              </label>
              {report.link && (
                <>
                  <div className="share-link-row">
                    <label htmlFor="lnkdays">Link stops working after</label>
                    <select id="lnkdays" value={report.link.days} disabled={busy || !canShare}
                      onChange={async (e) => { try { setReport(await api.links.make(campaignId, Number(e.target.value))) } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) } }}>
                      {[7, 30, 90].map((d) => <option key={d} value={d}>{d}</option>)}
                    </select> days
                    <button type="button" className="btn-sm" onClick={async () => {
                      const url = report.link!.url || `${window.location.origin}${report.link!.path}`
                      try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 2000) }
                      catch { setMessage({ tone: 'ok', text: 'Copy this link:', link: url }) }
                    }}>{copied ? 'Copied' : 'Copy link'}</button>
                  </div>
                  <div className="share-hint">Until {new Date(report.link.expires_at * 1000).toLocaleDateString()}. It shows the latest report and its figures, nothing else of the project.
                    {!report.has_report && ' The project has no report yet: the link will show it once a run writes one.'}</div>
                </>
              )}
              {!report.link && report.reason && <div className="share-hint warn">{report.reason}</div>}
            </>
          )}
        </div>

        <div className="share-section">
          <label className="modal-check">
            <input type="checkbox" checked={access.confidential} disabled={!canManage || busy}
              onChange={(e) => act(async () => {
                const r = await api.access.set(campaignId, { confidential: e.target.checked })
                setReport(await api.links.get(campaignId))
                return r
              }, e.target.checked ? 'The project is now confidential.' : 'The project is no longer confidential.')} />
            <b>Confidential project</b>
          </label>
          <div className="share-hint">No links outside the team, not shown to the rest of {access.team}, skills learned here stay here, and only model providers that keep no data are used.</div>
        </div>
        <div className="modal-actions"><span className="share-hint" style={{ marginRight: 'auto' }}>Changes are saved as you make them.</span><button className="btn-sm btn-approve" onClick={onClose}>Done</button></div>
      </div>
    </div>
  )
}
