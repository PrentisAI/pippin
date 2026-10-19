/** Types of the web console API (see aurora/api/routes/campaigns.py). */

// ---------------------------------------------------------------------------
// Phases
// ---------------------------------------------------------------------------

/** Campaign phases, in order; these are the names used in `skip_phases`. */
export type Phase = 'ideation' | 'screening' | 'planning' | 'preflight' | 'research' | 'analysis' | 'writing'

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
  { key: 'preflight', label: 'Pre-flight', desc: 'Check each job and smoke-run it before the full run' },
  { key: 'research', label: 'Research', desc: 'The agent runs the experiments', required: true },
  { key: 'analysis', label: 'Analysis', desc: 'Check the results; loop back if needed' },
  { key: 'writing', label: 'Writing', desc: 'Write the report' },
]

export const PHASE_PRESETS: Record<'quick' | 'standard' | 'full', { label: string; skip: Phase[]; desc: string }> = {
  quick: { label: 'Quick', skip: ['ideation', 'screening', 'planning', 'preflight', 'analysis', 'writing'], desc: 'Research only' },
  standard: { label: 'Standard', skip: ['ideation', 'screening'], desc: 'Plan → Pre-flight → Research → Analyze → Write' },
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

export type GateAction = 'continue' | 'instruct' | 'propose' | 'stop'

/** Where a human gate came from (`GateRequest.source`). */
export type GateSource = 'milestone_audit' | 'supervisor' | 'screening' | 'termination'

/** How the console names the source of a human gate. */
export const GATE_SOURCE_LABELS: Record<string, string> = {
  milestone_audit: 'Milestone auditor',
  supervisor: 'Live auditor',
  screening: 'Screening',
  preflight: 'Pre-flight',
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
  /** The container's limits: live while it runs, else what a resume or re-run would use. */
  resources?: ContainerResources | null
  /** The front desk answers (and relays) messages; off: no model calls for this run. */
  frontdesk_enabled?: boolean
  /** Its cost limit (US$; null: none). */
  max_cost_usd?: number | null
}

export interface ContainerResources {
  docker: boolean
  cpus: number
  memory_mb: number
  container?: string | null
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
  params: {
    instruction: string; name?: string; inputs?: string[]; model?: string; ideation_mode?: IdeationMode; based_on?: string[]
    /** Settings the front desk put in from the conversation (the card starts from them). */
    autonomy_mode?: AutonomyMode; models?: Record<string, string>; cpus?: number; memory_gb?: number; max_parallel_jobs?: number
  }
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
  cpus?: number | null
  memory_mb?: number | null
  max_parallel_jobs?: number | null
}

export interface ConversationMessage {
  role: 'user' | 'assistant' | 'system'
  text: string
  timestamp: number
  proposal?: Proposal
  /** Files sent with the message. */
  attachments?: string[]
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
  /** The skill's text: Markdown in the standard sections (older skills: one rule). */
  rule: string
  description: string
  source_phase: string
  source_experiment: string
  source_campaign?: string
  /** experiment (one run) | campaign (one project) | projects (those in `projects`) | global (everywhere) */
  scope: string
  projects: string[]
  tags: string[]
  created_at: number
  updated_at: number
  /** human (your corrections) | self (learned from runs) | manual */
  origin: string
  /** procedure | failure | convention | correction */
  kind: string
  version: number
  history: { version: number; text: string; description?: string; updated_at: number; replaced_because?: string }[]
}

/** What the researcher did with a suggested skill (kept on the server). */
export interface SuggestionOutcome {
  state: 'added' | 'dismissed'
  skill_id?: string
  name?: string
  version?: number
  scope?: string
  decided_at?: number
}

/** Defaults for new experiments (GET /api/settings). */
export interface ModelSettings {
  agent: string
  screening: string
  planning: string
  preflight: string
  research: string
  analysis: string
  writing: string
  ideation_seats: string[]
  ideation_chair: string
  auditor: string
  /** Drafts skills from runs and corrections; empty: the milestone auditor's model. */
  skills: string
  supervisor: string
  frontdesk: string
}

export interface ConsoleSettings {
  models: ModelSettings
  autonomy_mode: AutonomyMode
  ideation_mode: IdeationMode
  max_cost_usd: number | null
  max_parallel_jobs: number
  sandbox: { docker: boolean; cpus: number; memory_mb: number; host_cpus: number; host_memory_mb: number }
  model_suggestions: string[]
  /** The console's own settings (defaults for new experiments). */
  editable: EditableSetting[]
  environment: ConsoleEnvironment
}

export interface EditableSetting {
  key: string
  group: string
  label: string
  kind: 'model' | 'models' | 'number' | 'optional-number' | 'int' | 'bool' | 'choice' | 'text'
  choices: string[] | null
  help: string
  /** What new experiments get now. */
  value: unknown
  /** What they would get from the server's base config alone. */
  server_value: unknown
  /** Changed in the console. */
  overridden: boolean
}

export interface ConsoleEnvironment {
  api_key: boolean
  api_base_url: string
  docker: { ok: boolean; detail: string }
  claude: { ok: boolean; version: string }
  host_cpus: number
  host_memory_mb: number
  aurora_home: string
  disk_free_gb: number
  server_config: string | null
  console_settings: string
}

export interface SearchHit {
  title: string
  snippet: string
  campaign_id?: string
  run_id?: string
  skill_id?: string
  status?: string
  project?: string
  path?: string
  kind?: string
}

export interface Analytics {
  days: number
  total_cost_usd: number
  runs: number
  by_status: Record<string, number>
  success_rate: number | null
  mean_hours: number | null
  cost_by_day: { day: string; cost_usd: number }[]
  cost_by_project: { campaign_id: string; project: string; cost_usd: number; runs: number }[]
  top_runs: { run_id: string; name: string; campaign_id: string; project: string; status: string; cost_usd: number; hours: number | null }[]
}

/** A file or folder uploaded to a project. */
export interface ProjectFileEntry {
  name: string
  /** Relative to the project's files. */
  path: string
  type: 'dir' | 'file'
  size: number | null
  modified: number
}

export interface ProjectFileListing {
  path: string
  entries: ProjectFileEntry[]
  usage_bytes: number
  max_file_bytes: number
  max_project_bytes: number
}
