/** Settings › Chat apps: talk to Pippin from Feishu/Lark, WeCom, Slack or WhatsApp, typed or spoken. You link your chat account with a code; a team
 *  admin connects the team's app once (its IDs and secrets, and the callback address to give it). */
import { useCallback, useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import { useAuth } from '../auth'
import type { ChatApp, ChatAppSettings } from '../types'

const APPS: ChatApp[] = ['feishu', 'wecom', 'slack', 'whatsapp']
const NAME: Record<ChatApp, string> = { feishu: 'Feishu / Lark (飞书)', wecom: 'WeCom (企业微信)', slack: 'Slack', whatsapp: 'WhatsApp' }
const FIELDS: Record<ChatApp, { key: string; label: string; secret?: boolean; hint?: string }[]> = {
  feishu: [
    { key: 'app_id', label: 'App ID' },
    { key: 'app_secret', label: 'App Secret', secret: true },
    { key: 'verification_token', label: 'Verification Token', secret: true, hint: 'Events & callbacks › Encryption strategy' },
    { key: 'encrypt_key', label: 'Encrypt Key', secret: true, hint: 'Optional: events then come encrypted' },
    { key: 'domain', label: 'Domain', hint: 'open.feishu.cn, or open.larksuite.com for Lark' },
  ],
  wecom: [
    { key: 'corp_id', label: 'Corp ID' },
    { key: 'agent_id', label: 'AgentId' },
    { key: 'secret', label: 'Secret', secret: true },
    { key: 'token', label: 'Token', secret: true, hint: 'Receive messages › API settings' },
    { key: 'aes_key', label: 'EncodingAESKey', secret: true },
  ],
  slack: [
    { key: 'bot_token', label: 'Bot User OAuth Token', secret: true, hint: 'OAuth & Permissions: starts with xoxb-' },
    { key: 'signing_secret', label: 'Signing Secret', secret: true, hint: 'Basic Information › App Credentials' },
  ],
  whatsapp: [
    { key: 'phone_number_id', label: 'Phone number ID', hint: 'WhatsApp › API Setup' },
    { key: 'access_token', label: 'Access token', secret: true, hint: 'A permanent token of a system user' },
    { key: 'app_secret', label: 'App secret', secret: true, hint: 'App settings › Basic' },
    { key: 'verify_token', label: 'Verify token', secret: true, hint: 'Any phrase: give the same one to the webhook' },
    { key: 'version', label: 'Graph API version', hint: 'v21.0 when empty' },
  ],
}
const HOW: Record<ChatApp, string> = {
  feishu: 'Create a custom app in the Feishu developer console, give it the bot ability and the permissions to read and send messages to users (im:message) and to get and upload files (im:resource, for voice messages), subscribe to “Receive messages” (im.message.receive_v1) with the callback address below, and publish it.',
  wecom: 'Create a self-built app in the WeCom admin console, turn on “Receive messages” with the callback address below and the Token and EncodingAESKey it gives, and add this server’s IP to the app’s trusted IPs. Voice answers need an ffmpeg that writes AMR; otherwise Pippin answers voice in writing.',
  slack: 'Create an app at api.slack.com/apps. Under OAuth & Permissions give the bot the scopes chat:write, im:history, im:write, app_mentions:read, files:read and files:write, and install it to the workspace. Under Event Subscriptions turn events on with the callback address below and subscribe the bot to message.im and app_mention. Under App Home allow people to send messages from the Messages tab.',
  whatsapp: 'In developers.facebook.com, create a Business app and add the WhatsApp product with your business number. Make a permanent token for a system user with whatsapp_business_messaging. Under WhatsApp › Configuration set the webhook to the callback address below with your verify token, and subscribe to messages. WhatsApp lets Pippin write first only within 24 hours of someone’s last message, so a notification reaches a person who wrote to Pippin that day.',
}

export function ChatAppsSection() {
  const { user } = useAuth()
  const admin = user.is_local || user.team_role === 'admin'
  const [me, setMe] = useState<{ links: { app: ChatApp; name: string; at: number }[]; ready: ChatApp[] } | null>(null)
  const [code, setCode] = useState<{ code: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => api.im.me().then(setMe).catch((e) => setError(describeError(e))), [])
  useEffect(() => { load() }, [load])
  // while a code is out, look for the link it makes
  useEffect(() => {
    if (!code) return
    const t = window.setInterval(load, 4000)
    return () => window.clearInterval(t)
  }, [code, load])

  const linked = (me?.links ?? []).length > 0
  return (
    <div className="settings-group">
      <div className="search-group-title">Chat apps</div>
      <div className="panel-hint" style={{ padding: '0 0 6px' }}>
        Talk to Pippin from Feishu, WeCom, Slack or WhatsApp: ask about your projects, and answer a run’s decision by replying to its message.
        What Pippin offers to do happens when you reply 确认 / yes. You can send a voice message too: Pippin answers in writing and aloud,
        when this console has speech recognition (a model key, or whisper in AURORA_WHISPER) and a voice set up.
      </div>
      {error && <div className="panel-message error">{error}</div>}
      {(me?.links ?? []).map((l) => (
        <div key={l.app} className="env-row">
          <span className="env-ok">✓</span> Linked to {NAME[l.app]}{l.name ? ` as ${l.name}` : ''}
          <button className="link-btn danger" style={{ marginLeft: 'auto' }}
            onClick={async () => { await api.im.unlink(l.app); load() }}>unlink</button>
        </div>
      ))}
      {me && me.ready.length > 0 ? (
        code && !linked ? (
          <div className="im-code">
            <span>Send this to the Pippin bot in {me.ready.map((a) => NAME[a]).join(' or ')}:</span>
            <b className="mono">{me.ready.every((a) => a === 'slack' || a === 'whatsapp') ? 'bind' : '绑定'} {code.code}</b>
            <span className="lit-sub">Valid for 15 minutes. This page notices when it is done.</span>
          </div>
        ) : (
          <button className="btn-sm" style={{ alignSelf: 'flex-start' }} onClick={async () => {
            try { const r = await api.im.code(); setCode({ code: r.code }); window.setTimeout(() => setCode(null), r.expires_in * 1000) } catch (e) { setError(describeError(e)) }
          }}>{linked ? 'Link another account' : 'Link my chat account'}</button>
        )
      ) : me && <div className="env-row muted">{admin ? 'No chat app is connected yet: connect one below.' : 'No chat app is connected yet: ask a team admin.'}</div>}
      {admin && <TeamApps onChanged={load} />}
    </div>
  )
}

function TeamApps({ onChanged }: { onChanged: () => void }) {
  const [data, setData] = useState<{ apps: Record<ChatApp, ChatAppSettings>; callbacks: Record<ChatApp, string> } | null>(null)
  const [open, setOpen] = useState<ChatApp | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)
  const load = useCallback(() => api.im.settings().then(setData).catch((e) => setMsg({ tone: 'error', text: describeError(e) })), [])
  useEffect(() => { load() }, [load])
  if (!data) return null

  const edit = (app: ChatApp) => {
    const s = data.apps[app]
    setDraft(Object.fromEntries(FIELDS[app].map((f) => [f.key, f.secret ? '' : String(s[f.key] ?? '')])))
    setOpen(app); setMsg(null)
  }
  const save = async (app: ChatApp, enabled: boolean) => {
    setBusy(true); setMsg(null)
    try {
      await api.im.save(app, { ...draft, enabled })
      await load(); onChanged()
      setMsg({ tone: 'ok', text: enabled ? `${NAME[app]} saved.` : `${NAME[app]} is off.` })
      setOpen(null)
    } catch (e) { setMsg({ tone: 'error', text: describeError(e) }) } finally { setBusy(false) }
  }

  return (
    <div className="im-team">
      <div className="settings-label">The team’s apps (admins)</div>
      {msg && <div className={`panel-message ${msg.tone}`}>{msg.text}</div>}
      {APPS.map((app) => {
        const s = data.apps[app]
        return (
          <div key={app} className={`notify-channel${s.enabled ? '' : ' off'}`}>
            <div className="notify-channel-head">
              <span className="notify-channel-name">{NAME[app]}</span>
              <span className="notify-channel-type">{s.ready ? 'connected' : s.enabled ? 'missing settings' : 'off'}</span>
              {open !== app && <button className="link-btn" onClick={() => edit(app)}>{s.ready || s.enabled ? 'change' : 'connect'}</button>}
              {open !== app && s.enabled && <button className="link-btn danger" disabled={busy} onClick={() => { setDraft({}); save(app, false) }}>turn off</button>}
            </div>
            {open !== app && s.enabled && (
              <div className="settings-field"><label>
                <span className="settings-label">Callback address</span>
                <input readOnly value={data.callbacks[app]} onFocus={(e) => e.target.select()} />
              </label></div>
            )}
            {open === app && (
              <>
                <div className="panel-hint" style={{ padding: '2px 0 4px' }}>{HOW[app]}</div>
                <div className="settings-field"><label>
                  <span className="settings-label">Callback address</span>
                  <input readOnly value={data.callbacks[app]} onFocus={(e) => e.target.select()} />
                </label></div>
                {data.callbacks[app].startsWith('/') && <div className="panel-hint" style={{ padding: 0 }}>Set the console address under Notifications first: the app needs the full address, reachable from the internet.</div>}
                {FIELDS[app].map((f) => (
                  <div key={f.key} className="settings-field" title={f.hint}><label>
                    <span className="settings-label">{f.label}</span>
                    <input type={f.secret ? 'password' : 'text'} value={draft[f.key] ?? ''} autoComplete="off"
                      placeholder={f.secret && s.set[f.key] ? 'saved (leave empty to keep)' : f.hint ?? ''}
                      onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
                  </label></div>
                ))}
                <div className="notify-actions">
                  <button className="btn-sm" disabled={busy} onClick={() => setOpen(null)}>Cancel</button>
                  <button className="btn-sm btn-approve" disabled={busy} onClick={() => save(app, true)}>Save and turn on</button>
                </div>
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}
