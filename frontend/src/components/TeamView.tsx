import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import { useAuth } from '../auth'
import type { ActivityEntry, KeyStatus, Person, Skill, TeamGroup, TeamRules } from '../types'

const TABS: [string, string][] = [['people', 'People'], ['groups', 'Groups'], ['rules', 'Rules'], ['keys', 'Keys'], ['activity', 'Activity']]
const RULE_LABELS: [keyof TeamRules, string][] = [
  ['external_links', 'Members can create read-only links to reports for people outside the team'],
  ['guests_can_run', 'Guests can start runs (charged to their own key)'],
  ['new_projects_confidential', 'New projects start as confidential'],
  ['skills_need_approval', 'Skills learned in a project reach the whole team only after an admin approves them'],
  ['members_see_all_projects', 'Members can see that projects they are not in exist'],
  ['team_key_for_members', 'Members without a key of their own may use the team’s key'],
]
// model providers, as OpenRouter names them
const PROVIDERS: [string, string][] = [['anthropic', 'Anthropic'], ['openai', 'OpenAI'], ['google', 'Google'], ['deepseek', 'DeepSeek'],
  ['moonshotai', 'Moonshot'], ['z-ai', 'Z.ai'], ['qwen', 'Qwen'], ['x-ai', 'xAI'], ['meta-llama', 'Meta'], ['mistralai', 'Mistral']]
const providerName = (k: string) => PROVIDERS.find(([p]) => p === k)?.[1] ?? k

function ago(ts: number | null | undefined): string {
  if (!ts) return '—'
  const m = Math.floor((Date.now() / 1000 - ts) / 60)
  if (m < 2) return 'now'
  if (m < 60) return `${m} min ago`
  if (m < 60 * 24) return `${Math.floor(m / 60)} h ago`
  return new Date(ts * 1000).toLocaleDateString()
}

/** The team: people and guests, groups, rules, the team's key, and who did what. */
export function TeamView() {
  const auth = useAuth()
  const admin = auth.user.team_role === 'admin'
  const [tab, setTab] = useState('people')
  const [people, setPeople] = useState<Person[]>([])
  const [groups, setGroups] = useState<TeamGroup[]>([])
  const [rules, setRules] = useState<TeamRules | null>(null)
  const [keys, setKeys] = useState<{ team: KeyStatus[]; server_key?: boolean; people: { user_id: string; name: string; own_key: boolean }[] } | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string; link?: string } | null>(null)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')
  const [newGroup, setNewGroup] = useState('')
  const [teamKey, setTeamKey] = useState('')
  const [pending, setPending] = useState<Skill[]>([])

  const load = useCallback(async () => {
    try {
      setPeople(await api.team.people())
      setGroups(await api.team.groups())
      setRules(await api.team.rules())
      setPending(await api.skills.pending())
      if (admin) {
        setKeys(await api.team.keys())
        setActivity(await api.team.activity())
      }
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    }
  }, [admin])
  useEffect(() => { load() }, [load])

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setMessage(null)
    try {
      await fn()
      await load()
      if (ok) setMessage({ tone: 'ok', text: ok })
    } catch (err) {
      setMessage({ tone: 'error', text: describeError(err) })
    }
  }

  if (!auth.accounts) {
    return (
      <div className="team-page">
        <h1>Team</h1>
        <div className="team-note">This console has no accounts: it is single-user, and nothing asks for a sign-in.
          To work with others, create the first admin on the server with <code>aurora users add you@lab.org --admin</code>;
          from then on everyone signs in, and projects are shared from their Share button.</div>
      </div>
    )
  }
  const name = (id: string) => people.find((p) => p.user_id === id)?.name ?? 'someone'

  return (
    <div className="team-page">
      <div className="team-eyebrow">Team</div>
      <h1>{auth.team}</h1>
      <div className="team-tabs" role="tablist">
        {TABS.filter(([k]) => admin || ['people', 'groups', 'rules'].includes(k)).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {message && (
        <div className={`panel-message ${message.tone}`} style={{ margin: '10px 0' }}>{message.text}
          {message.link && <div className="share-link"><code>{message.link}</code><button className="link-btn" onClick={() => navigator.clipboard?.writeText(message.link!).catch(() => {})}>Copy</button></div>}
        </div>
      )}

      {tab === 'people' && (
        <>
          {admin && (
            <form className="team-row-form" onSubmit={async (e) => {
              e.preventDefault()
              setMessage(null)
              try {
                const r = await api.team.invite(inviteEmail.trim(), inviteRole)
                setMessage({ tone: 'ok', text: `Invited ${inviteEmail}. Send them this link (valid for 14 days):`, link: r.link })
                setInviteEmail('')
              } catch (err) { setMessage({ tone: 'error', text: describeError(err) }) }
            }}>
              <input type="email" placeholder="Invite someone to the team: email" aria-label="Email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} required />
              <select aria-label="As" value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                <option value="member">Member</option><option value="guest">Guest</option><option value="admin">Admin</option>
              </select>
              <button className="btn-sm btn-approve">Invite</button>
            </form>
          )}
          <div className="team-table-wrap">
            <table className="team-table">
              <thead><tr><th>Person</th><th>In the team</th>{admin && <th>API key</th>}{admin && <th>Last active</th>}{admin && <th />}</tr></thead>
              <tbody>
                {people.map((p) => (
                  <tr key={p.user_id}>
                    <td><div className="team-person">{p.name}{p.user_id === auth.user.user_id ? ' (you)' : ''}</div><div className="team-sub">{p.email}</div></td>
                    <td>{admin && p.user_id !== auth.user.user_id ? (
                      <select aria-label={`Role of ${p.name}`} value={p.team_role} onChange={(e) => act(() => api.team.setRole(p.user_id, e.target.value))}>
                        <option value="admin">Admin</option><option value="member">Member</option><option value="guest">Guest</option>
                      </select>
                    ) : <span className={p.team_role === 'guest' ? 'share-guest' : ''}>{p.team_role}{p.org ? ` · ${p.org}` : ''}</span>}</td>
                    {admin && <td>{p.key === 'own' ? <span className="team-ok">their own</span> : p.key === 'team' ? 'uses the team key' : <span className="team-sub">none</span>}</td>}
                    {admin && <td className="team-sub">{ago(p.last_active)}</td>}
                    {admin && <td>{p.user_id !== auth.user.user_id && (
                      <button className="link-btn danger" onClick={() => act(() => api.team.remove(p.user_id), `${p.name} was removed. What they made stays theirs; give their projects to someone else if needed.`)}>Remove</button>
                    )}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="team-note">Admins manage people, rules and the team key. They cannot read anyone’s private conversations or keys.</div>
        </>
      )}

      {tab === 'groups' && (
        <>
          <div className="team-note">Give a group access to a project in one step, from the project’s Share window.</div>
          {groups.map((g) => (
            <div key={g.group_id} className="team-card">
              <div className="team-card-head"><b>{g.name}</b>{admin && <button className="link-btn danger" onClick={() => act(() => api.team.deleteGroup(g.group_id))}>Delete</button>}</div>
              <div className="team-chips">
                {people.map((p) => {
                  const inGroup = g.members.includes(p.user_id)
                  return admin ? (
                    <label key={p.user_id} className="modal-check">
                      <input type="checkbox" checked={inGroup} onChange={(e) => act(() => api.team.updateGroup(g.group_id, { members: e.target.checked ? [...g.members, p.user_id] : g.members.filter((x) => x !== p.user_id) }))} />
                      {p.name}
                    </label>
                  ) : inGroup ? <span key={p.user_id} className="team-chip">{p.name}</span> : null
                })}
              </div>
            </div>
          ))}
          {admin && (
            <form className="team-row-form" onSubmit={(e) => { e.preventDefault(); if (newGroup.trim()) act(() => api.team.addGroup(newGroup.trim(), []).then(() => setNewGroup(''))) }}>
              <input placeholder="New group name" aria-label="New group name" value={newGroup} onChange={(e) => setNewGroup(e.target.value)} />
              <button className="btn-sm">Add group</button>
            </form>
          )}
        </>
      )}

      {tab === 'rules' && rules && (
        <>
          <div className="team-note">Rules apply to every project. A project can be stricter, not looser.</div>
          {RULE_LABELS.map(([k, l]) => (
            <label key={k} className="modal-check team-rule">
              <input type="checkbox" checked={Boolean(rules[k])} disabled={!admin} onChange={(e) => act(() => api.team.setRules({ [k]: e.target.checked }), 'Saved for the whole team.')} />
              {l}
            </label>
          ))}
          <label className="team-field">Email domains of members (others join as guests)
            <input defaultValue={rules.member_domains.join(', ')} disabled={!admin} placeholder="e.g. lab.org, university.edu"
              onBlur={(e) => act(() => api.team.setRules({ member_domains: e.target.value.split(/[\s,]+/).filter(Boolean) }), 'Saved for the whole team.')} />
          </label>
          <div className="share-section">
            <div className="share-section-title">Model providers</div>
            <div className="team-chips">
              {PROVIDERS.map(([k, l]) => {
                const all = !rules.allowed_providers.length
                const on = all || rules.allowed_providers.includes(k)
                return (
                  <label key={k} className="modal-check">
                    <input type="checkbox" checked={on} disabled={!admin} onChange={(e) => {
                      const now = all ? PROVIDERS.map(([p]) => p) : rules.allowed_providers
                      const next = e.target.checked ? [...now, k] : now.filter((x) => x !== k)
                      if (!next.length) return
                      act(() => api.team.setRules({ allowed_providers: next.length === PROVIDERS.length ? [] : next }), 'Saved for the whole team.')
                    }} /> {l}
                  </label>
                )
              })}
            </div>
            <div className="team-sub">Runs, front desks and brainstorms use only models from the providers ticked here.
              Confidential projects use only providers with a no-retention agreement: {(rules.no_retention_providers ?? []).map(providerName).join(', ')}.</div>
          </div>
          {(pending.length > 0 || rules.skills_need_approval) && (
            <div className="share-section">
              <div className="share-section-title">Skills waiting for approval · {pending.length}</div>
              {pending.map((sk) => (
                <div key={sk.skill_id} className="team-card">
                  <div className="team-card-head"><b>{sk.name}</b>
                    <span className="team-sub">{sk.scope === 'global' ? 'for every project' : `for ${sk.projects.length} project${sk.projects.length === 1 ? '' : 's'}`}</span></div>
                  {sk.description && <div className="team-sub">{sk.description}</div>}
                  <details><summary className="team-sub">Read it</summary><pre className="team-pre">{sk.rule}</pre></details>
                  {admin && (
                    <div className="team-row-form">
                      <button className="btn-sm btn-approve" onClick={() => act(() => api.skills.decide(sk.skill_id, true), `“${sk.name}” now reaches the projects it was meant for.`)}>Approve</button>
                      <button className="btn-sm" onClick={() => act(() => api.skills.decide(sk.skill_id, false), `“${sk.name}” stays in the project it was learned in.`)}>Keep it in its project</button>
                    </div>
                  )}
                </div>
              ))}
              {!pending.length && <div className="team-sub">None. Until an admin approves one, a skill is used only in the project it was learned in.</div>}
            </div>
          )}
        </>
      )}

      {tab === 'keys' && keys && (
        <>
          <div className="team-card">
            <div className="team-card-head"><b>Team OpenRouter key</b><span className="team-sub">{keys.team[0]?.set ? keys.team[0].hint : keys.server_key ? 'not set: the server’s OPENROUTER_API_KEY is used' : 'not set'}</span></div>
            <div className="team-sub">Used only by members without a key of their own, when the rules allow it. Nobody can read it back, admins included.</div>
            <form className="team-row-form" onSubmit={(e) => { e.preventDefault(); act(() => api.team.setKey('openrouter', teamKey).then(() => setTeamKey('')), 'Team key saved.') }}>
              <input type="password" placeholder="Paste a new team key" aria-label="Team key" value={teamKey} onChange={(e) => setTeamKey(e.target.value)} />
              <button className="btn-sm" disabled={teamKey.length < 8}>Save</button>
              {keys.team[0]?.set && <button type="button" className="link-btn danger" onClick={() => act(() => api.team.deleteKey('openrouter'), 'Team key removed.')}>Remove</button>}
            </form>
          </div>
          <div className="team-table-wrap">
            <table className="team-table"><thead><tr><th>Person</th><th>Own key</th></tr></thead>
              <tbody>{keys.people.map((p) => <tr key={p.user_id}><td>{p.name}</td><td>{p.own_key ? <span className="team-ok">yes</span> : <span className="team-sub">no</span>}</td></tr>)}</tbody>
            </table>
          </div>
        </>
      )}

      {tab === 'activity' && (
        <>
          <div className="team-note">Who did what. Never what anyone wrote in a private conversation.</div>
          <ol className="team-log">
            {activity.map((e) => (
              <li key={e.id}><span className="team-sub">{new Date(e.ts * 1000).toLocaleString()}</span> <b>{e.who}</b> {e.action}
                {e.target && e.target.startsWith('u_') && !e.action.includes(name(e.target)) ? ` (${name(e.target)})` : ''}{e.detail ? <span className="team-sub"> · {e.detail}</span> : null}</li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}
