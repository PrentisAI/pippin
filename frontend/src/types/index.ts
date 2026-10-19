/** Types of the web console API (see aurora/api/routes/campaigns.py). */

// ---------------------------------------------------------------------------
// Phases
// ---------------------------------------------------------------------------

/** Campaign phases, in order; these are the names used in `skip_phases`. */
export type Phase = 'ideation' | 'screening' | 'planning' | 'research' | 'analysis' | 'writing'

export interface PhaseInfo {
  key: Phase
  label: string
  desc: string
  required?: boolean
}

export const PHASES: readonly PhaseInfo[] = [
  { key: 'ideation', label: 'Ideation', desc: 'Generate and debate candidate hypotheses' },
  { key: 'screening', label: 'Screening', desc: 'Pick the most promising idea' },
  { key: 'planning', label: 'Planning', desc: 'Break the work into jobs' },
  { key: 'research', label: 'Research', desc: 'The agent runs the experiments', required: true },
  { key: 'analysis', label: 'Analysis', desc: 'Check the results; loop back if needed' },
  { key: 'writing', label: 'Writing', desc: 'Write the report' },
]

export const PHASE_PRESETS: Record<'quick' | 'standard' | 'full', { label: string; skip: Phase[]; desc: string }> = {
  quick: { label: 'Quick', skip: ['ideation', 'screening', 'planning', 'analysis', 'writing'], desc: 'Research only' },
  standard: { label: 'Standard', skip: ['ideation', 'screening'], desc: 'Plan → Research → Analyze → Write' },
  full: { label: 'Full', skip: [], desc: 'The whole pipeline, from ideation to the report' },
}

/** Human-readable name of a phase (also accepts names the engine uses internally, such as "audit"). */
export function phaseLabel(phase: string | undefined | null): string {
  if (!phase) return ''
  const known = PHASES.find((p) => p.key === phase)
  if (known) return known.label
  return phase.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

// ---------------------------------------------------------------------------
// Projects and experiments
// ---------------------------------------------------------------------------

export interface CampaignSummary {
  campaign_id: string
  name: string
  objective: string
  created_at: number
  updated_at: number
  experiment_count: number
  active_experiments: number
  completed_experiments: number
  needs_attention: boolean
}

export type AutonomyMode = 'supervised' | 'autonomous'

export type ExperimentStatus = 'starting' | 'running' | 'awaiting_human' | 'completed' | 'failed' | 'stopped'

/** Statuses of an experiment whose engine is still alive. */
export function isActiveStatus(status: string | undefined): boolean {
  return status === 'starting' || status === 'running' || status === 'awaiting_human'
}

export type GateAction = 'continue' | 'instruct' | 'stop'

/** Where a human gate came from (`GateRequest.source`). */
export type GateSource = 'milestone_audit' | 'supervisor' | 'screening' | 'termination'

/** How the console names the source of a human gate. */
export const GATE_SOURCE_LABELS: Record<string, string> = {
  milestone_audit: 'Milestone auditor',
  supervisor: 'Live auditor',
  screening: 'Screening',
  termination: 'Before finishing',
}

export interface PendingDecision {
  decision_id: string
  source: GateSource | string
  context: string
  /** The gate request: gate_id, source, title, detail, options, created_at. */
  detail: { gate_id?: string; title?: string; detail?: string; [key: string]: unknown }
  options: GateAction[]
  created_at: number
  resolved: boolean
}

export interface ExperimentSummary {
  run_id: string
  campaign_id: string
  task_name: string
  model: string
  autonomy_mode: AutonomyMode
  status: ExperimentStatus
  phase: string
  turn: number
  cost_usd: number
  started_at: number
  finished_at: number | null
  event_count: number
  error: string | null
  pending_decision: PendingDecision | null
  decisions_made: number
}

export interface ExperimentEventRecord {
  type?: string
  run_id?: string
  event_type: string
  source?: string
  data?: Record<string, any>
  timestamp?: number
}

export interface ExperimentDetail extends ExperimentSummary {
  events: ExperimentEventRecord[]
  decision_history: Record<string, any>[]
  /** Path of the final report, relative to the experiment directory, or null. */
  report: string | null
}

export interface CampaignDetail extends CampaignSummary {
  experiments: ExperimentSummary[]
}

export interface StartExperimentRequest {
  instruction: string
  name?: string
  skip_phases?: Phase[]
  model?: string
  docker_image?: string
  inputs?: string[]
  autonomy_mode?: AutonomyMode
  ideation_mode?: 'roundtable' | 'independent'
  max_cost_usd?: number
}

export interface StartExperimentResponse extends ExperimentSummary {
  experiment_id: string
  skip_phases: Phase[]
  active_phases: Phase[]
}

/** A node of an experiment's file tree. While the experiment runs, the first node is
 *  "app (live container)", whose file paths start with "@container/". */
export interface FileNode {
  name: string
  /** "dir", or the file's extension ("file" when it has none). */
  type: string
  path?: string
  children?: FileNode[]
}

// ---------------------------------------------------------------------------
// Project conversation
// ---------------------------------------------------------------------------

export type IdeationMode = 'roundtable' | 'independent'

export interface Proposal {
  proposal_id: string
  kind: 'custom'
  params: { instruction: string; name?: string; inputs?: string[]; model?: string; ideation_mode?: IdeationMode }
  max_cost_usd?: number | null
  skip_phases: Phase[]
  active_phases: Phase[]
  summary: string
  created_at?: number
  confirmed: boolean
  superseded: boolean
  confirmed_settings?: ConfirmedSettings | null
}

/** What the researcher started from a proposal card. */
export interface ConfirmedSettings {
  experiment_id: string
  skip_phases: Phase[]
  ideation_mode?: IdeationMode
  max_cost_usd?: number | null
  autonomy_mode?: AutonomyMode | string | null
  models?: Partial<Record<string, string | string[]>>
}

export interface ConversationMessage {
  role: 'user' | 'assistant' | 'system'
  text: string
  timestamp: number
  proposal?: Proposal
}

export interface ProjectChatStatus {
  campaign_id: string
  agent_active: boolean
  active_experiments: number
  awaiting_human: number
  pending_decisions: (PendingDecision & { experiment_id: string })[]
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface Skill {
  skill_id: string
  name: string
  rule: string
  description: string
  source_phase: string
  source_experiment: string
  scope: string
  tags: string[]
  created_at: string
  updated_at: string
}

/** Defaults for new experiments (GET /api/settings). */
export interface ModelSettings {
  agent: string
  screening: string
  planning: string
  research: string
  analysis: string
  writing: string
  ideation_seats: string[]
  ideation_chair: string
  auditor: string
  supervisor: string
  frontdesk: string
}

export interface ConsoleSettings {
  models: ModelSettings
  autonomy_mode: AutonomyMode
  ideation_mode: IdeationMode
  max_cost_usd: number | null
  model_suggestions: string[]
}
