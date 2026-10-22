import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { TourChapter } from './store'
import { startTour, updateOnboarding } from './store'

/** What the tour may ask the console to do before a step (open the example, a panel, a page…). */
export interface TourActions {
  openRun: (campaignId: string, runId: string) => void
  openPanel: (panel: string) => void
  openPage: (page: 'brainstorm' | 'library' | 'team' | 'examples' | null) => void
  goHome: () => void
  /** Stream, files or jobs on the experiment's right bar. */
  setRight: (panel: string) => void
  accounts: boolean
}

interface Step {
  /** CSS selectors, tried in order; none (or none found): the step is shown in the middle. */
  target?: string[]
  title: string
  body: string
  before?: (a: TourActions, ex: { cid: string; run: string } | null) => void
  /** Click this before the step (a button that opens what the step shows). */
  click?: string
  /** Shown only with accounts. */
  accountsOnly?: boolean
}

const ab = (title: string) => [`.activity-bar [title="${title}"]`]

const CHAPTERS: Record<TourChapter, { title: string; steps: Step[] }> = {
  run: {
    title: 'A finished run',
    steps: [
      { target: ['[data-tour="sidebar"]'], title: 'Projects and their runs',
        body: 'A project holds the runs on one question, with its own chat with the front desk. This one is an example that comes with AURORA: everyone can look, nobody can change it. “Duplicate” makes a copy of its question that is yours.',
        before: (a, ex) => { a.openPanel('projects'); if (ex) a.openRun(ex.cid, ex.run) } },
      { target: ['[data-tour="status"]'], title: 'Where the run is',
        body: 'Its status and phase, the tool calls it made, how long it took, its cost against its budget, the model, and how hands-on it is. In your own runs, a click on the cost changes the budget, on the mode switches between supervised and autonomous, on Container changes CPUs and memory.' },
      { target: ['[data-tour="controls"]'], title: 'Report, re-run, fork',
        body: 'The report, once there is one. Stop and Resume while a run goes; Re-run starts it again with the same settings; Fork… starts a new run from after any phase or job of this one, with something done differently.' },
      { target: ['[data-tour="stream"]'], title: 'What happened, side by side',
        body: 'Left: the research agent’s steps — click one for its commands and output. Right: the auditors’ verdicts and the decisions, next to the step they are about. The ideation meeting, screening and planning are further up.',
        before: (a) => a.setRight('stream') },
      { target: ['[data-event="human_gate_resolved"]', '[data-event="human_gate_opened"]', '[data-tour="stream"]'], title: 'A decision',
        body: 'When an auditor escalates or a phase keeps failing, the run waits for you: approve, give an instruction, or stop. In this run screening rejected three proposals in a row and the researcher kept the latest; later the live auditor stopped the agent from copying files out of its sandbox. Notifications can bring decisions to your phone, with Approve and Stop links.' },
      { target: ['[data-tour="rightbar"]'], title: 'Stream, files, jobs',
        body: 'Switch the right side between the stream and the run’s files (its workspace, plans, transcripts, the report). When a run sends work to GPUs, a Jobs button shows each job and what its GPUs are doing.' },
      { target: ['[data-tour="tabs"]'], title: 'The project’s tabs',
        body: 'The project chat, one tab per run, the project’s Brainstorm and Literature, and Compare when there are several runs: setup, cost, results and conclusions side by side.' },
      { target: ['[data-tour="input"]'], title: 'Talk to a run',
        body: 'Ask the front desk anything about the run — it reads the run’s files — or, while a run goes, tell the agent what to change: it gets your message mid-step, without the run stopping.' },
      { click: '[data-tour="report-btn"]', target: ['.rep-actions'], title: 'The report',
        body: 'Download it with its figures, bibliography and results, as Markdown and LaTeX; get a script that reproduces the results in the run’s image; or put it into an Overleaf project in one click.' },
      { target: ['.rep-doc'], title: 'Every number has a source',
        body: 'In runs of this version every number the agents report is recorded with the script that computed it, and appears in the report as a chip: click it for the script as it was then, the command, its inputs and the job. References are checked against the scholarly indexes. (This example predates the chips.)' },
      { click: '.rep-bar .link-btn', title: 'That is a run',
        body: 'Next: a walk around the rest of AURORA, or start your own research. Help (?) in the left bar has these tours, a checklist and the guides.' },
    ],
  },
  app: {
    title: 'Around AURORA',
    steps: [
      { target: ab('Projects'), title: 'Projects', body: 'Your projects and their runs. + starts a new project; the menu next to one renames, shares or deletes it.',
        before: (a) => a.openPanel('projects') },
      { target: ab('Brainstorm'), title: 'Brainstorm', before: (a) => a.openPage('brainstorm'),
        body: 'Think out loud before you run anything: a Skeptic, Methods and a Librarian answer, ideas are kept as arguments (for, against, open questions, next steps), and a good one becomes a proposal.' },
      { target: ab('Library'), title: 'Library and feed', before: (a) => a.openPage('library'),
        body: 'The papers of all your projects — the ones agents cite, checked against the indexes, and the ones you add — with your notes, synced with Zotero. Its feed shows new papers and posts from the people and topics you follow, ranked against your projects.' },
      { target: ab('Search'), title: 'Search', before: (a) => { a.openPage(null); a.openPanel('search') },
        body: 'Projects, runs, reports, chats and skills, with the passage that matches.' },
      { target: ab('Analytics'), title: 'Analytics', before: (a) => a.openPanel('analytics'),
        body: 'What your runs cost and how they went over a week, a month or a year: cost per day and per project, the share completed, the most expensive runs.' },
      { target: ab('Explorer'), title: 'Files', before: (a) => a.openPanel('projects'),
        body: 'In a run, switches the right side to its files. A project’s own files (data you upload with + in a chat) are in its menu.' },
      { target: ab('Skills'), title: 'Skills', before: (a) => a.openPanel('skills'),
        body: 'Procedures every agent can use: the ones you write, and ones learned from earlier runs and from your corrections. A correction you make once becomes a skill, so you do not make it again.' },
      { target: ab('Settings'), title: 'Settings', before: (a) => a.openPanel('settings'),
        body: 'The defaults of new runs (the model of every role, the budget, how hands-on, the container), notifications with quiet hours, and GPU jobs on a SLURM cluster or a GPU machine over SSH.' },
      { target: ab('Team'), title: 'Team', accountsOnly: true, before: (a) => a.openPanel('projects'),
        body: 'Who is in the team, groups, and the team’s rules. Projects are private until shared, with rights from viewing to deciding; each person uses their own model key.' },
      { target: ['.activity-bar [title="Help"]'], title: 'Help', before: (a) => a.openPanel('projects'),
        body: 'Ask how to do something, the getting-started checklist, these tours, the examples and the guides.' },
    ],
  },
  start: {
    title: 'Your own research',
    steps: [
      { target: ['[data-tour="brief"]'], title: 'Say what you want to find out', before: (a) => { a.openPage(null); a.goHome() },
        body: 'In your own words: the question, the data or code you have, what a good answer looks like. + attaches files and papers from your Library. A project is made from it.' },
      { target: ['[data-tour="templates"]'], title: 'Or start from a template',
        body: 'Analyse a dataset, compare methods, reproduce a paper, explore an open question, run a simulation study: a brief with [brackets] for your parts.' },
      { title: 'The front desk asks, then proposes',
        body: 'It asks what is unclear, then shows a proposal card: the instruction the agents will get, the phases (untick the ones you do not need), the models, the budget, and an estimate of the cost and time. Nothing runs, and nothing costs money, until you confirm.' },
      { title: 'While it runs',
        body: 'Watch the stream, ask the front desk, give the agent instructions, change the budget. The run asks you when a decision should be yours — in the console, or on your phone with notifications. Long jobs can go to GPUs while the agent waits without cost.' },
      { title: 'When it ends',
        body: 'Read the report and trace its numbers, compare it with other runs, fork it to try something else, or put it into Overleaf. The checklist in Help shows what you have not tried yet.' },
    ],
  },
}

const PAD = 8

/** The guided tour: a spotlight on one element at a time, with what it is and what to do there. */
export function Tour({ actions }: { actions: TourActions }) {
  const [chapter, setChapter] = useState<TourChapter | null>(null)
  const [index, setIndex] = useState(0)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const [example, setExample] = useState<{ cid: string; run: string } | null>(null)
  const card = useRef<HTMLDivElement>(null)
  const [cardSize, setCardSize] = useState({ w: 340, h: 200 })

  const steps = chapter ? CHAPTERS[chapter].steps.filter((s) => !s.accountsOnly || actions.accounts) : []
  const step = steps[index]

  useEffect(() => {
    const on = (e: Event) => { setChapter((e as CustomEvent<TourChapter>).detail); setIndex(0) }
    window.addEventListener('aurora:tour', on)
    return () => window.removeEventListener('aurora:tour', on)
  }, [])
  useEffect(() => {
    if (chapter !== 'run' || example) return
    api.examples.list().then((r) => {
      const ex = r.examples.find((e) => e.kind === 'real run' && e.installed && !e.removed)
      if (ex?.campaign_id && ex.run_id) setExample({ cid: ex.campaign_id, run: ex.run_id })
    }).catch(() => {})
  }, [chapter, example])

  // before a step: what it needs opened, then find its element (it may take a moment to appear)
  useEffect(() => {
    if (!step) return
    if (chapter === 'run' && !example && index === 0) return     // wait for the example's address
    let cancelled = false
    setRect(null)
    step.before?.(actions, example)
    if (step.click) (document.querySelector(step.click) as HTMLElement | null)?.click()
    const started = Date.now()
    const find = () => {
      if (cancelled) return
      for (const sel of step.target ?? []) {
        const el = document.querySelector(sel) as HTMLElement | null
        if (el && el.getBoundingClientRect().width > 0) {
          el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
          setRect(el.getBoundingClientRect())
          return
        }
      }
      if (step.target && Date.now() - started < 3000) setTimeout(find, 120)
    }
    setTimeout(find, 120)
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chapter, index, example])

  // follow the element when the window changes
  useEffect(() => {
    if (!step?.target) return
    const again = () => {
      for (const sel of step.target ?? []) {
        const el = document.querySelector(sel)
        if (el) {
          const r = el.getBoundingClientRect()
          setRect((old) => (old && old.top === r.top && old.left === r.left && old.width === r.width && old.height === r.height ? old : r))
          return
        }
      }
    }
    window.addEventListener('resize', again)
    window.addEventListener('scroll', again, true)
    const timer = window.setInterval(again, 400)          // the page may still be moving (a card loading above it)
    return () => { window.removeEventListener('resize', again); window.removeEventListener('scroll', again, true); window.clearInterval(timer) }
  }, [step])

  useLayoutEffect(() => {
    if (card.current) {
      const r = card.current.getBoundingClientRect()
      if (Math.abs(r.width - cardSize.w) > 1 || Math.abs(r.height - cardSize.h) > 1) setCardSize({ w: r.width, h: r.height })
    }
  }, [chapter, index, rect, cardSize.w, cardSize.h])

  const close = useCallback(() => { setChapter(null); setIndex(0) }, [])
  const finish = useCallback((next?: TourChapter) => {
    if (chapter) updateOnboarding({ tour_done: chapter }).catch(() => {})
    close()
    if (next) setTimeout(() => startTour(next), 50)
  }, [chapter, close])

  useEffect(() => {
    if (!chapter) return
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
      else if (e.key === 'ArrowRight' && index < steps.length - 1) setIndex(index + 1)
      else if (e.key === 'ArrowLeft' && index > 0) setIndex(index - 1)
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [chapter, index, steps.length, close])

  if (!chapter || !step) return null
  const last = index === steps.length - 1
  const vw = window.innerWidth
  const vh = window.innerHeight
  let pos: React.CSSProperties
  if (rect) {
    const spaceRight = vw - rect.right
    const spaceLeft = rect.left
    const spaceBelow = vh - rect.bottom
    let left: number
    let top: number
    if (spaceRight > cardSize.w + 24) { left = rect.right + 16; top = rect.top }
    else if (spaceLeft > cardSize.w + 24) { left = rect.left - cardSize.w - 16; top = rect.top }
    else if (spaceBelow > cardSize.h + 24) { left = rect.left; top = rect.bottom + 16 }
    else { left = rect.left; top = rect.top - cardSize.h - 16 }
    pos = { left: Math.max(12, Math.min(left, vw - cardSize.w - 12)), top: Math.max(12, Math.min(top, vh - cardSize.h - 12)) }
  } else {
    pos = { left: (vw - cardSize.w) / 2, top: Math.max(24, (vh - cardSize.h) / 2) }
  }
  const nextChapter: TourChapter | undefined = chapter === 'run' ? 'app' : chapter === 'app' ? 'start' : undefined
  return (
    <div className="tour-layer" role="dialog" aria-modal="true" aria-label={`Tour: ${CHAPTERS[chapter].title}`}>
      {rect
        ? <div className="tour-spot" style={{ left: rect.left - PAD, top: rect.top - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2 }} />
        : <div className="tour-dim" />}
      <div className="tour-card" ref={card} style={pos}>
        <div className="tour-meta">
          <span>{CHAPTERS[chapter].title} · {index + 1} of {steps.length}</span>
          <div className="tour-progress"><div style={{ width: `${((index + 1) / steps.length) * 100}%` }} /></div>
        </div>
        <div className="tour-title">{step.title}</div>
        <div className="tour-body">{step.body}</div>
        <div className="tour-buttons">
          <button className="tour-skip" onClick={close}>{last ? 'Close' : 'Skip the tour'}</button>
          <div className="wiz-spacer" />
          {index > 0 && <button className="tour-btn ghost" onClick={() => setIndex(index - 1)}>Back</button>}
          {!last && <button className="tour-btn" onClick={() => setIndex(index + 1)} autoFocus>Next</button>}
          {last && nextChapter && <button className="tour-btn ghost" onClick={() => finish()}>Done</button>}
          {last && <button className="tour-btn" autoFocus onClick={() => (nextChapter ? finish(nextChapter) : finish())}>
            {nextChapter ? (nextChapter === 'app' ? 'Walk around AURORA' : 'Start your own research') : 'Done'}
          </button>}
        </div>
      </div>
    </div>
  )
}
