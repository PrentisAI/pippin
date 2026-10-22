import { useEffect, useState } from 'react'
import { api, describeError } from '../api/client'
import type { KeyStatus, Onboarding, SetupStatus, WizardStep } from '../types'
import { openDoc, setOnboarding, updateOnboarding } from './store'

const STEPS: { id: WizardStep; label: string }[] = [
  { id: 'welcome', label: 'Welcome' }, { id: 'connect', label: 'Connect' },
  { id: 'prefs', label: 'Your way' }, { id: 'start', label: 'Start' },
]
const FIELDS: [string, string][] = [['materials', 'Materials and chemistry'], ['ml', 'Machine learning'],
  ['bio', 'Biology and medicine'], ['data', 'Social and data science'], ['other', 'Something else']]
const BUDGETS: [number | null, string][] = [[5, '$5'], [20, '$20'], [50, '$50'], [null, 'No limit']]

/** The first time a person opens the console (each person, with accounts): what AURORA is, what it
 *  needs, how they want to work, and where to begin. Every step can be left and come back to. */
export function WelcomeWizard({ ob, onExample, onDemo, onOwn }: {
  ob: Onboarding
  /** Open the finished example with the tour. */
  onExample: () => void
  onDemo: () => void
  onOwn: () => void
}) {
  const [step, setStep] = useState<WizardStep>(ob.wizard.step || 'welcome')
  const at = STEPS.findIndex((s) => s.id === step)
  const go = (s: WizardStep) => { setStep(s); updateOnboarding({ step: s }).catch(() => {}) }
  const finish = async (then?: () => void) => {
    try { await updateOnboarding({ done: true }) } catch { /* shown again next time */ }
    then?.()
  }

  return (
    <div className="wiz-backdrop" role="dialog" aria-modal="true" aria-label="Welcome to AURORA">
      <div className="wiz-top">
        <div className="wiz-brand"><span className="wiz-logo">A</span> AURORA</div>
        <ol className="wiz-steps" aria-label="Steps">
          {STEPS.map((s, i) => (
            <li key={s.id} className={i < at ? 'done' : i === at ? 'now' : ''} aria-current={i === at ? 'step' : undefined}>
              <span className="wiz-dot">{i < at ? '✓' : i + 1}</span>{s.label}
            </li>
          ))}
        </ol>
      </div>
      <div className="wiz-card">
        {step === 'welcome' && <WelcomeStep name={ob.accounts ? ob.name : ''} onSkip={() => finish()} onNext={() => go('connect')} />}
        {step === 'connect' && <ConnectStep ob={ob} onBack={() => go('welcome')} onNext={() => go('prefs')} onExamples={() => go('start')} />}
        {step === 'prefs' && <PrefsStep ob={ob} onBack={() => go('connect')} onNext={() => go('start')} />}
        {step === 'start' && (
          <StartStep onBack={() => go('prefs')} onExample={() => finish(onExample)} onDemo={() => finish(onDemo)}
            onOwn={() => finish(onOwn)} />
        )}
      </div>
    </div>
  )
}

function WelcomeStep({ name, onSkip, onNext }: { name: string; onSkip: () => void; onNext: () => void }) {
  return (
    <>
      <div className="wiz-head">
        <div className="wiz-kicker">{name ? `Welcome, ${name.split(' ')[0]}` : 'Welcome'}</div>
        <h1>Research with AI agents — and you stay in charge</h1>
        <p>AURORA carries a research project from ideas to a written report. Agents do the work, two auditors check it,
          and the run asks you when a decision should be yours.</p>
      </div>
      <div className="wiz-grid3">
        <div className="wiz-tile">
          <b>How a run goes</b>
          <div className="wiz-phases">
            {['Ideas', 'Screening', 'Plan', 'Pre-flight', 'Research', 'Analysis', 'Report'].map((p) => <span key={p}>{p}</span>)}
          </div>
          <span>Each phase is a step you can watch, skip, or send back for another round.</span>
        </div>
        <div className="wiz-tile">
          <b>Who checks the work</b>
          <span>A <b>milestone auditor</b> reviews each phase before the next builds on it. A <b>live auditor</b> watches the
            research as it happens and can step in.</span>
        </div>
        <div className="wiz-tile">
          <b>Where you come in</b>
          <span>The run stops for your <b>decision</b> where it matters, and you can <b>talk to it</b> at any time without
            stopping it — also from your phone.</span>
        </div>
      </div>
      <div className="wiz-tile wiz-wide">
        <b>And around the runs</b>
        <span>Brainstorm ideas with three agents, keep your papers in a Library (with Zotero), follow new papers in a feed,
          trace every number of a report to its script, compare and fork runs, send long jobs to GPUs, and work as a team.</span>
      </div>
      <div className="wiz-foot">
        <span className="wiz-note">About two minutes. Everything here can be changed later; Help (?) brings it back.</span>
        <button className="wiz-btn ghost" onClick={onSkip}>Skip — I know AURORA</button>
        <button className="wiz-btn primary" onClick={onNext}>Get started</button>
      </div>
    </>
  )
}

function ConnectStep({ ob, onBack, onNext, onExamples }: { ob: Onboarding; onBack: () => void; onNext: () => void; onExamples: () => void }) {
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [keys, setKeys] = useState<{ keys: KeyStatus[]; uses: string; team_key_allowed?: boolean } | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [extras, setExtras] = useState(false)
  useEffect(() => {
    api.setup.get().then(setSetup).catch(() => {})
    if (ob.accounts) api.me.keys().then(setKeys).catch(() => {})
  }, [ob.accounts])
  const keyCheck = setup?.checks.find((c) => c.key === 'model_key')
  const keyOk = !!keyCheck?.ok
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      if (ob.accounts) await api.me.setKey('openrouter', draft.trim())
      setSetup(ob.accounts ? await api.setup.get() : await api.setup.setKey(draft.trim()))
      setDraft('')
      api.onboarding.get().then(setOnboarding).catch(() => {})
    } catch (e) { setError(describeError(e)) } finally { setBusy(false) }
  }
  const machine = (setup?.checks ?? []).filter((c) => c.required && c.key !== 'model_key')
  const optional = (setup?.checks ?? []).filter((c) => !c.required)
  const keyWhere = keyCheck?.detail ?? ''
  return (
    <>
      <div className="wiz-head">
        <div className="wiz-kicker">Step 2 · Connect</div>
        <h2>Connect AURORA to what it needs</h2>
        <p>{!setup ? 'Checking this machine…'
          : keyOk && machine.every((c) => c.ok) ? 'Everything a run needs is here.'
            : ob.accounts && !ob.admin ? 'The server is set up by your admin; what you add is your own model key.'
              : 'We checked this machine. What is left is marked.'}</p>
      </div>
      <div className="wiz-list">
        <div className={`wiz-check${keyOk ? ' ok' : ' todo'}`}>
          <span className="wiz-mark">{keyOk ? '✓' : '!'}</span>
          <div className="wiz-check-body">
            <div><b>A model key (OpenRouter)</b> <span className="wiz-dim">· {keyOk ? keyWhere : 'not set yet'}</span></div>
            <div className="wiz-dim">One key for every model — Claude, GPT, Gemini, DeepSeek. Create one at{' '}
              <a href="https://openrouter.ai/keys" target="_blank" rel="noreferrer">openrouter.ai → Keys</a>.{' '}
              {ob.accounts ? 'It is yours: your runs are charged to it, and nobody else sees it.'
                : 'It is checked with OpenRouter and stays on this server.'}
              {ob.accounts && keys?.team_key_allowed && !keyOk ? ' Or ask an admin to let members use the team’s key.' : ''}</div>
            {(!keyOk || ob.accounts) && (
              <form className="wiz-row" onSubmit={(e) => { e.preventDefault(); if (draft.trim().length >= 20) save() }}>
                <label className="sr-only" htmlFor="wiz-key">OpenRouter key</label>
                <input id="wiz-key" className="wiz-input mono" type="password" placeholder={keyOk ? 'Replace it: sk-or-…' : 'sk-or-v1-…'}
                  value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off" />
                <button className="wiz-btn primary" disabled={busy || draft.trim().length < 20}>{busy ? 'Checking…' : 'Check and save'}</button>
              </form>
            )}
            {error && <div className="wiz-error">{error}</div>}
          </div>
        </div>
        {machine.map((c) => (
          <div key={c.key} className={`wiz-check${c.ok ? ' ok' : ' todo'}`}>
            <span className="wiz-mark">{c.ok ? '✓' : '!'}</span>
            <div className="wiz-check-body">
              <div><b>{c.label}</b> <span className="wiz-dim">· {c.detail}</span></div>
              {!c.ok && c.fix && <div className="wiz-dim">{c.fix}</div>}
            </div>
          </div>
        ))}
        <button className="wiz-btn ghost left" onClick={() => setExtras(!extras)} aria-expanded={extras}>
          {extras ? 'Hide the optional extras' : `Optional extras you can add later (${optional.length + 1})`}
        </button>
        {extras && (
          <div className="wiz-grid2">
            {optional.map((c) => (
              <div key={c.key} className="wiz-extra">
                <b>{c.label}</b>
                <span className="wiz-dim">{c.ok ? c.detail : c.fix}</span>
              </div>
            ))}
            <div className="wiz-extra">
              <b>Zotero</b>
              <span className="wiz-dim">Bring your collections into the Library, with PDFs and highlights. <button className="link-btn" onClick={() => openDoc('guide/zotero.md')}>How</button></span>
            </div>
          </div>
        )}
      </div>
      <div className="wiz-foot">
        <button className="wiz-btn ghost" onClick={onBack}>Back</button>
        <div className="wiz-spacer" />
        {!keyOk && <button className="wiz-btn" onClick={onExamples}>Look at the examples first — no key needed</button>}
        <button className="wiz-btn primary" disabled={!keyOk} onClick={onNext}>Continue</button>
      </div>
    </>
  )
}

function PrefsStep({ ob, onBack, onNext }: { ob: Onboarding; onBack: () => void; onNext: () => void }) {
  const [field, setField] = useState(ob.field || '')
  const [autonomy, setAutonomy] = useState(ob.autonomy)
  const [budget, setBudget] = useState<number | null>(ob.max_cost_usd)
  const [error, setError] = useState<string | null>(null)
  const next = async () => {
    try {
      await api.onboarding.defaults({ autonomy, max_cost_usd: budget })
      await updateOnboarding({ field })
      onNext()
    } catch (e) { setError(describeError(e)) }
  }
  return (
    <>
      <div className="wiz-head">
        <div className="wiz-kicker">Step 3 · Your way of working</div>
        <h2>Three questions, then you are set</h2>
        <p>{ob.accounts ? 'These become your own defaults.' : 'These become the console’s defaults.'} Every run can still change them on its proposal card.</p>
      </div>
      <fieldset className="wiz-q">
        <legend>Your field</legend>
        <div className="wiz-chips">
          {FIELDS.map(([id, label]) => (
            <button key={id} type="button" className={`wiz-chip${field === id ? ' on' : ''}`} aria-pressed={field === id}
              onClick={() => setField(id)}>{label}</button>
          ))}
        </div>
        <span className="wiz-dim">Used to put the templates and examples of your field first.</span>
      </fieldset>
      <fieldset className="wiz-q">
        <legend>How hands-on</legend>
        <div className="wiz-grid2">
          <button type="button" className={`wiz-pick${autonomy === 'supervised' ? ' on' : ''}`} aria-pressed={autonomy === 'supervised'}
            onClick={() => setAutonomy('supervised')}>
            <b>Ask me at each key decision</b>
            <span>The run waits for you when an auditor escalates or a direction needs confirming.</span>
            <span className="mono wiz-dim">supervised · recommended to start</span>
          </button>
          <button type="button" className={`wiz-pick${autonomy === 'autonomous' ? ' on' : ''}`} aria-pressed={autonomy === 'autonomous'}
            onClick={() => setAutonomy('autonomous')}>
            <b>Only tell me what happened</b>
            <span>Decisions are taken and recorded; you read them and can step in at any time.</span>
            <span className="mono wiz-dim">autonomous</span>
          </button>
        </div>
      </fieldset>
      <fieldset className="wiz-q">
        <legend>Budget of a run</legend>
        <div className="wiz-chips">
          {BUDGETS.map(([v, label]) => (
            <button key={label} type="button" className={`wiz-chip${budget === v ? ' on' : ''}`} aria-pressed={budget === v}
              onClick={() => setBudget(v)}>{label}</button>
          ))}
        </div>
        <span className="wiz-dim">A run stops at its budget. Before you confirm one, it shows what it will likely cost and take.</span>
      </fieldset>
      {error && <div className="wiz-error">{error}</div>}
      <div className="wiz-foot">
        <button className="wiz-btn ghost" onClick={onBack}>Back</button>
        <div className="wiz-spacer" />
        <button className="wiz-btn primary" onClick={next}>Continue</button>
      </div>
    </>
  )
}

function StartStep({ onBack, onExample, onDemo, onOwn }: { onBack: () => void; onExample: () => void; onDemo: () => void; onOwn: () => void }) {
  const [hasExample, setHasExample] = useState(true)
  useEffect(() => {
    api.examples.list().then((r) => setHasExample(r.examples.some((e) => e.kind === 'real run' && e.installed && !e.removed)))
      .catch(() => {})
  }, [])
  return (
    <>
      <div className="wiz-head">
        <div className="wiz-kicker">Step 4 · Start</div>
        <h2>Where would you like to begin?</h2>
      </div>
      <div className="wiz-list">
        {hasExample && (
          <button className="wiz-pick on" onClick={onExample}>
            <span className="wiz-row"><b className="wiz-big">Walk through a finished run</b><span className="wiz-tag">recommended</span></span>
            <span>Example: <b>Citi Bike usage patterns</b> — a real run from question to report: the ideation meeting, a decision,
              the auditors at work, the report. A guided tour shows what to look at.</span>
          </button>
        )}
        <button className="wiz-pick" onClick={onDemo}>
          <b className="wiz-big">Watch a run happen</b>
          <span>A scripted study plays out in seconds: three models argue at the ideation meeting, every phase runs, the auditors
            check each hand-off. No key, no cost.</span>
        </button>
        <button className="wiz-pick" onClick={onOwn}>
          <b className="wiz-big">Start my own research</b>
          <span>Describe your question or start from a template. The front desk drafts a proposal; nothing runs until you confirm it.</span>
        </button>
      </div>
      <div className="wiz-foot">
        <button className="wiz-btn ghost" onClick={onBack}>Back</button>
        <div className="wiz-spacer" />
        <span className="wiz-note">Help (?) in the left bar has a checklist, the tours and the guides.</span>
      </div>
    </>
  )
}
