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
  /** How it is shared (null: the console has no accounts). */
  sharing?: ProjectSharing | null
  /** An example that comes with Pippin: read-only for everyone. */
  example?: boolean
}

export interface ProjectSharing {
  members: number
  team_access: 'none' | 'view' | 'comment'
  confidential: boolean
  my_rights: Perm[]
}

export type AutonomyMode = 'supervised' | 'autonomous'

export type ExperimentStatus = 'starting' | 'running' | 'awaiting_human' | 'completed' | 'failed' | 'stopped'

/** Statuses of an experiment whose engine is still alive. */
export function isActiveStatus(status: string | undefined): boolean {
  return status === 'starting' || status === 'running' || status === 'awaiting_human'
}

export type GateAction = 'continue' | 'instruct' | 'propose' | 'stop'

/** Where a human gate came from (`GateRequest.source`). */
export type GateSource = 'milestone_audit' | 'supervisor' | 'screening' | 'direction' | 'termination'

/** How the console names the source of a human gate. */
export const GATE_SOURCE_LABELS: Record<string, string> = {
  milestone_audit: 'Milestone auditor',
  supervisor: 'Live auditor',
  screening: 'Screening',
  direction: 'The round table decided',
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
  /** What it was estimated to cost and take, if it was started from a card that showed an estimate. */
  estimate?: { cost: [number, number]; hours: [number, number] } | null
  /** Who started it (their key pays; their channels are notified). */
  started_by?: string
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
    /** Papers the run starts from (keys in the project's literature). */
    seed_papers?: string[]
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
  ideation_live?: boolean
  add_found?: AddFound
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
  /** Who wrote it, in a run's shared conversation on a console with accounts. */
  by?: string
  /** Items from the Library sent with it. */
  library?: LibraryRef[]
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
  /** Reaching other projects waits for a team admin: '' (not needed, or approved) | pending | rejected */
  approval?: '' | 'pending' | 'rejected'
  /** Added from the catalog: where it comes from. */
  source?: { catalog?: string; source_name?: string; repo?: string; commit?: string; license?: string; url?: string }
}

/** A skill others have shared, offered in the catalog. */
export interface CatalogSkill {
  name: string
  source: string
  source_name: string
  repo: string
  license: string
  /** Its folder on GitHub, at the pinned commit. */
  url: string
  category: string
  summary: string
  recommended: boolean
  needs: string
  /** In the library already (its skill), and where it is used. */
  skill_id: string
  scope: string
  /** Another skill in the library has this name. */
  name_taken: boolean
}

export interface SkillCatalog {
  categories: { id: string; label: string }[]
  sources: { id: string; name: string; repo: string; url: string; license: string; about: string; commit: string }[]
  entries: CatalogSkill[]
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
  /** Searching and checking papers, and the default rule for papers the agents find. */
  literature?: { enabled: boolean; add_found: AddFound }
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
  kind: 'model' | 'models' | 'number' | 'optional-number' | 'int' | 'bool' | 'choice' | 'text' | 'words'
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

export type ChannelType = 'webhook' | 'wecom' | 'feishu' | 'slack' | 'serverchan'

export interface NotifyChannel {
  id: string
  type: ChannelType
  name: string
  /** Saved channels: a shortened view of the webhook URL or key. */
  target_hint?: string
  /** Only sent when set or changed; empty keeps the saved one. */
  target?: string
  events: string[]
  enabled: boolean
}

export interface NotificationSettings {
  console_url: string
  console_url_default: string
  budget_warning_at: number
  channels: NotifyChannel[]
  events: { key: string; label: string; default: boolean }[]
  /** Quiet hours (HH:MM, the server's time): messages are held and sent together afterwards. */
  quiet_start: string
  quiet_end: string
  quiet_except: string[]
  /** "Approve" and "Stop" links in decision messages. */
  action_links: boolean
  /** How a running experiment is getting on, every this many hours (0: never), and at each new phase. */
  progress_every_hours: number
  progress_phases: boolean
}

export interface ClusterJob {
  id: number
  name: string
  command: string
  gpus: number
  cpus: number
  mem_gb: number
  time_limit: string
  partition: string
  slurm_id: string
  state: string
  reason: string
  node: string
  exit_code: number | null
  submitted_at: number
  started_at: number | null
  ended_at: number | null
  elapsed_seconds: number
  active: boolean
  gpu_hours: number
  /** Its output, as the agents see it (/app/...). */
  log: string
  dir: string
  /** "slurm" or "ssh" (a GPU machine over SSH: slurm_id is then the process ID there). */
  backend?: string
  /** ssh: the GPUs it was given. */
  gpu_ids?: string
}

export interface ClusterJobs {
  enabled: boolean
  live: boolean
  jobs: ClusterJob[]
  gpu_hours: number
  /** "SLURM" or "SSH" while the experiment runs. */
  backend?: string
  slurm_error?: string
}

export interface GpuUsage {
  backend: string
  host?: string
  live: boolean
  gpus: {
    index: number
    name: string
    util: number | null
    mem_used_mb: number | null
    mem_total_mb: number | null
    node: string
    jobs: { id: number; name: string }[]
    /** ssh: held by another experiment of this console. */
    other_experiment?: boolean
    /** ssh: in use by someone else (its memory is taken). */
    busy?: boolean
  }[]
  errors: string[]
  at?: number
}

// ---------------------------------------------------------------------------
// Accounts, teams and sharing
// ---------------------------------------------------------------------------

export type TeamRole = 'admin' | 'member' | 'guest'
export type Perm = 'view' | 'comment' | 'edit' | 'run' | 'decide' | 'share' | 'manage'
export type ProjectRole = 'owner' | 'editor' | 'commenter' | 'viewer' | 'custom'

export interface AuthUser {
  user_id: string
  email: string
  name: string
  team_role: TeamRole
  /** The single user of a console without accounts. */
  is_local: boolean
}

export interface AuthStatus {
  accounts: boolean
  user: AuthUser | null
  team: string | null
}

export interface InviteInfo {
  email: string
  team_role: TeamRole
  team: string
  project: string | null
  project_role: ProjectRole | null
  invited_by: string | null
  has_account: boolean
}

export interface Person {
  user_id: string
  email: string
  name: string
  team_role: TeamRole
  org: string
  /** Admins only. */
  last_active?: number | null
  key?: 'own' | 'team' | 'none'
}

export interface TeamGroup {
  group_id: string
  name: string
  members: string[]
}

export interface TeamRules {
  external_links: boolean
  guests_can_run: boolean
  new_projects_confidential: boolean
  skills_need_approval: boolean
  members_see_all_projects: boolean
  allowed_providers: string[]
  no_retention_providers: string[]
  team_key_for_members: boolean
  monthly_budget_usd: number | null
  per_person_usd: number | null
  per_project_usd: number | null
  member_domains: string[]
}

export interface KeyStatus {
  provider: string
  set: boolean
  hint: string
  updated_at: number | null
}

export interface ActivityEntry {
  id: number
  ts: number
  user_id: string | null
  who: string
  action: string
  target: string
  detail: string
}

export interface AccessMember {
  principal: string
  kind: 'user' | 'group'
  role: ProjectRole
  perms: Perm[]
  name: string
  email?: string
  team_role?: TeamRole
  org?: string
  members?: number
}

export interface ProjectAccess {
  owner_id: string
  team_access: 'none' | 'view' | 'comment'
  confidential: boolean
  deciders: 'starter' | 'owners' | 'user'
  decider_user: string | null
  members: AccessMember[]
  invites: { invite_id: string; email: string; role: ProjectRole; team_role: TeamRole }[]
  my_rights: Perm[]
  roles: Record<string, Perm[]>
  perm_labels: Record<Perm, string>
  team_access_options: string[]
  team: string
}

// ---------------------------------------------------------------------------
// Literature
// ---------------------------------------------------------------------------

/** How papers the agents find enter a project's literature. */
export type AddFound = 'all' | 'cited' | 'none'

/** A real paper (it was found in a scholarly index). */
export interface LitPaper {
  paper_id: string
  key: string
  title: string
  authors: string[]
  year: number | null
  venue: string
  doi: string
  arxiv_id: string
  url: string
  pdf_url: string
  abstract: string
  cited_by: number | null
  cites_per_year: number | null
  /** The indexes it was found in. */
  found_in: string[]
  byline: string
  /** paper, or a page kept in the Library (a blog, a post, code, a video, a discussion, any page). */
  kind?: 'paper' | 'page' | 'blog' | 'post' | 'code' | 'video' | 'discussion'
  /** A page's site and date, as it gives them. */
  site?: string
  published?: string
}

/** A paper in a project's literature. */
export interface LitItem extends LitPaper {
  source: string
  source_label: string
  added_by: string
  search_id: string
  starred: boolean
  added_at: number
  used_in: string[]
  /** In the asking person's Zotero. */
  in_zotero?: boolean
  /** Highlights and notes that reach the project (from someone's Zotero). */
  notes?: { kind: 'highlight' | 'note'; text: string; comment: string; page: string; by: string }[]
  /** Its PDF in the project's files, e.g. papers/behrens2012active.pdf. */
  pdf_file?: string
  /** arXiv: the version cited, the latest one, and the authors' comment on it. */
  cited_version?: number
  arxiv_version?: number
  arxiv_comment?: string
  newer_version?: boolean
}

export interface LitReference {
  title: string
  authors: string
  year: number | null
  doi: string
  arxiv_id: string
  text: string
}

/** A reference that could not be verified. */
export interface LitFlag {
  ref_id: string
  status: 'not_found' | 'mismatch' | 'unchecked'
  reference: LitReference
  reason: string
  searched: string[]
  by: string
  run_id: string
  resolution: '' | 'replaced' | 'removed' | 'have'
  decided_by: string
  created_at: number
  closest: LitPaper | null
  replaced_by: string
  used_in: string[]
}

export interface LitSearch {
  search_id: string
  run_id: string
  by: string
  context: string
  query: string
  created_at: number
  reached: string[]
  failed: string[]
  found: number
  cited: number
  in_library: number
  papers?: (LitPaper & { cited: boolean; in_library: boolean; projects?: string[] })[]
}

export interface ProjectLiterature {
  rule: AddFound
  /** The project has its own rule (else the console's default, default_rule). */
  rule_own?: boolean
  default_rule?: AddFound
  /** The asking person's Zotero, for this project (null: not connected). */
  zotero: { connected: boolean; collections: string[]; can_send: boolean; last_sync: number | null; last_error: string } | null
  papers: LitItem[]
  refs: LitFlag[]
  searches: LitSearch[]
  counts: { papers: number; flagged: number; checked: number; searches: number }
}

export interface LitCheck {
  status: 'verified' | 'not_found' | 'mismatch' | 'unchecked'
  reference: LitReference
  paper: Omit<LitPaper, 'paper_id' | 'key' | 'cites_per_year' | 'byline'> | null
  closest: Omit<LitPaper, 'paper_id' | 'key' | 'cites_per_year' | 'byline'> | null
  searched: string[]
  failed: string[]
  reason: string
}

export interface LibraryPaper extends LitPaper {
  projects: { campaign_id: string; name: string }[]
  mine: boolean
  in_zotero?: boolean
  added_at: number
}

export interface LibraryView {
  papers: LibraryPaper[]
  projects: { campaign_id: string; name: string }[]
  rule: AddFound
}

// ---------------------------------------------------------------------------
// Zotero
// ---------------------------------------------------------------------------

export interface ZoteroOptions { pdfs: boolean; notes: boolean; push: boolean; citekeys: boolean; idea_notes: boolean }

export interface ZoteroMapping { library: string; collection_key: string; name: string; campaign_id: string; project?: string }

export interface ZoteroStatus {
  connected: boolean
  method?: 'web' | 'local'
  username?: string
  can_write?: boolean
  files?: boolean
  options?: ZoteroOptions
  last_sync?: number | null
  last_error?: string
  last_report?: { collections?: number; seen?: number; added?: number; vouched?: number; pdfs?: number; notes?: number; pushed?: number; idea_notes?: number; problems?: string[]
    /** papers that would have been sent, held because writing into Zotero is off (or only when you do it) */
    held?: number; held_level?: 'off' | 'ask' | 'auto' }
  syncing?: boolean
  key_hint?: string
  mappings?: ZoteroMapping[]
}

export interface ZoteroCollection { library: string; collection_key: string; name: string; items: number; campaign_id: string | null }

// ---------------------------------------------------------------------------
// Brainstorm
// ---------------------------------------------------------------------------

export type CardType = 'idea' | 'question' | 'material' | 'note'
export type Relation = 'supports' | 'challenges' | 'builds on' | 'asks about' | 'related'
export type Activity = 'off' | 'moderate' | 'high'

export interface BsCard {
  card_id: string
  space: string
  owner: string
  owner_name: string
  type: CardType
  text: string
  stand: string
  by: string
  by_name: string
  vis: 'private' | 'project'
  answer: string
  answered_by: string
  answered_by_name?: string
  paper_id: string
  run_id: string
  chat_id: string
  live: boolean
  mine: boolean
  created_at: number
  paper?: { key: string; title: string; byline: string; doi: string; arxiv_id: string; url: string; found_in: string[] }
  /** Material from the feed that is not a paper (a post, a blog, a discussion): where it is from, and its link */
  source?: string
  url?: string
}

export interface BsLink { from_card: string; to_card: string; rel: Relation }

export interface BsChat {
  chat_id: string
  space: string
  owner: string
  owner_name: string
  title: string
  vis: 'private' | 'project'
  run_id: string
  focus: string
  mine: boolean
  updated_at: number
  /** A live round table's state: preparing, item 2 of 3, decided, finished. */
  status?: string
  /** The Mentor is guiding this conversation: the researcher's next message is for it. */
  guided?: boolean
  /** Who leads: the researcher (the agents answer, and question them when they seem too sure) or Pippin. */
  lead?: 'me' | 'pippin'
  /** A tangent: the conversation it branched from, and the message it branched at. */
  parent?: string
  branch_at?: string
}

/** A conversation as opened: its messages, its tangents, and (for a tangent) where it came from. */
export interface BsChatView {
  chat: BsChat
  messages: BsMessage[]
  pending: boolean
  here: { user_id: string; name: string }[]
  branches?: { chat_id: string; title: string; branch_at: string; mine: boolean }[]
  parent?: { chat_id: string; title: string; at: string; at_who: string } | null
}

export interface BsMessage {
  msg_id: string
  chat_id: string
  who: string
  who_name: string
  agent: boolean
  mine: boolean
  kind: 'text' | 'consent' | 'system' | 'action'
  text: string
  /** Something to act on: a proposal, a step of a map, the options of a challenge, a guess asked for. */
  widget?: MsgWidget | null
  steps: string[]
  effects: [string, string][]
  only_for: string
  consent: string
  decided: string
  pending: boolean
  created_at: number
  attachments?: BsAttachment[]
}

export type BsAttachment = { kind: 'paper'; paper_id: string; label?: string } | { kind: 'file'; path: string; label?: string }

export interface BsSuggestion {
  sug_id: string
  by: string
  kind: 'open' | 'evidence' | 'combine' | 'caveat' | 'worked'
  short: string
  act: string
  cards: string[]
  paper_id: string
  created_at: number
}

export interface BsSpace {
  space: string
  key: string
  name: string
  project: boolean
  can_write: boolean
  chats: BsChat[]
  cards: BsCard[]
  links: BsLink[]
  activity: Activity
  members: { user_id: string; name: string }[]
  agents: Record<string, { initials: string; role: string }>
  runs: number
  papers: number
  threads: Record<string, number>
  suggestions: BsSuggestion[]
}

// ---------------------------------------------------------------------------- the feed

export type FeedKind = 'paper' | 'post' | 'blog' | 'code' | 'video' | 'discussion'

export interface FeedFit { campaign_id: string; project: string; rel: number; why: string }

export interface FeedItem {
  item_id: string
  kind: FeedKind
  label: string
  source: string
  src: string
  url: string
  pdf_url: string
  /** What the open button says: PDF, Open thread, Read, See all replies */
  open: string
  title: string
  summary: string
  /** A discussion's main points */
  points: string[]
  /** A discussion's places: [label, url] */
  places: [string, string][]
  /** [name, followed person's ID or ''] */
  people: [string, string][]
  /** A checked identity: "arXiv:2610.08812", "DOI 10.…" */
  idv: string
  extra: string
  published_at: number | null
  fetched_at: number
  rel: number
  why: string
  campaign_id: string
  project: string
  fits: FeedFit[]
  folded: boolean
  saved: { campaign_id: string; project: string } | null
  is_paper: boolean
  about: string
}

export interface FeedIdentity {
  kind: 'openalex' | 's2' | 'arxiv' | 'bluesky' | 'mastodon' | 'x' | 'blog' | 'github' | 'youtube' | 'linkedin'
    /** places to open, not follow: their homepage, Scholar …; talks and videos with them */
    | 'link' | 'talk'
  handle: string
  url: string
  label: string
  /** link: to open, not to follow */
  state: 'ok' | 'warn' | 'muted' | 'link'
  follow: boolean
  note: string
  works?: number
}

export interface FeedPerson {
  person_id: string
  name: string
  initials: string
  aff: string
  ids: FeedIdentity[]
  wants: { papers: boolean; posts: boolean; blog: boolean; code: boolean; videos: boolean; about: boolean }
  coauthors: string[]
  activity?: string
  fresh?: number
}

export interface FeedDigest {
  to: string
  label: string
  at: string
  /** a second time in the day ('' : once a day) */
  at2: string
  top: number
  skip: boolean
  /** also to the chat apps you linked (Feishu, WeCom, Slack, WhatsApp) */
  chat: boolean
  /** the chat apps you linked that can take it */
  linked: string[]
  channels: { id: string; label: string }[]
}

export interface FeedView {
  date: number
  items: FeedItem[]
  projects: { campaign_id: string; name: string }[]
  spaces: { space: string; name: string }[]
  people: FeedPerson[]
  digest: FeedDigest
  counts: Record<string, number | string>
  problems: Record<string, string>
  last: number | null
  refreshing: boolean
  saved_count: number
  sources: Record<string, string>
  following_anything: boolean
}

export interface FeedRss { url: string; title: string; state: 'ok' | 'error'; last_ok: number | null; error: string }

export interface Following {
  people: FeedPerson[]
  topics: string[]
  keywords: string[]
  from_objectives: boolean
  derived: { project: string; keywords: string[]; categories: string[] }[]
  hf: boolean
  discussions: boolean
  rss: FeedRss[]
  digest: FeedDigest
  x_key: boolean
}

export interface PersonCandidate {
  cid: string
  name: string
  aff: string
  works: number
  detail: string
  coauthors: string[]
  orcid: string
  homepage?: string
  ids: FeedIdentity[]
  checked?: boolean
}

export interface ScholarEntry {
  item_id: string
  k: 'paper' | 'post' | 'blog' | 'discussion' | 'about'
  label: string
  when: number
  source: string
  title: string
  body: string
  url: string
  in_project: string
  points: string[]
}

export interface ScholarPage {
  person: FeedPerson
  timeline: ScholarEntry[]
  theirs: (LitPaper & { campaign_id: string; project: string; how: string; cited_version: number })[]
  coauthors: string[]
}

/** An item from the Library attached to a message to a project's front desk. */
export interface LibraryRef {
  kind: 'feed' | 'paper'
  item_id?: string
  paper_id?: string
  title: string
  label: string
  ok: boolean
  by: string
  where?: string
}

export interface ReportLink {
  link: { path: string; url: string; days: number; expires_at: number; created_at: number } | null
  allowed: boolean
  reason: string
  has_report: boolean
}

export interface OtherProject { campaign_id: string; name: string; owner: string; owner_email: string }

// ---------------------------------------------------------------------------- results and reports

export interface ResultFile { path: string; exists: boolean; size?: number; sha256?: string; snapshot?: string; note?: string }

/** A number the agents recorded, with what produced it. */
export interface RunResult {
  id: string
  name: string
  value: number | string
  unit: string
  uncertainty: number | null
  description: string
  command: string
  job: string
  by: string
  inputs: ResultFile[]
  outputs: ResultFile[]
  script: ResultFile | null
  at: number
  supersedes?: string
}

export interface RunOverview {
  run_id: string
  name: string
  status: string
  phase: string
  instruction: string
  started_at: number | null
  finished_at: number | null
  wall_seconds: number
  cost_usd: number
  models: Record<string, string>
  settings: { skip_phases: string[]; ideation_mode: string; autonomy: string; budget_usd: number | null; image: string; cpus: number | null; memory_mb: number | null }
  proposal: { title?: string; hypothesis?: string; scores?: Record<string, number | null> }
  claims: { statement: string; status: string; confidence: number | null }[]
  results: RunResult[]
  results_all: number
  report: string | null
  cluster_jobs: number
  forked_from: { run_id: string; name?: string; point: string } | null
}

export interface RunReport {
  overview: RunOverview
  versions: number[]
  version?: number
  path?: string
  html: string
  title: string
  figures: { src: string; url: string; path: string }[]
  environment: boolean
}

export interface RunComparison {
  runs: RunOverview[]
  results: { name: string; values: ({ id: string; value: number | string; unit: string; uncertainty: number | null; shown: string } | null)[] }[]
  all_runs: { run_id: string; name: string; status: string; started_at: number }[]
}

/** What a run will likely cost and take, before it starts. */
export interface RunEstimate {
  cost: [number, number]
  cost_mid: number
  hours: [number, number]
  basis: string
  note: string
  similar: number
  per_phase: { phase: string; cost: [number, number]; minutes: [number, number]; jobs?: [number, number] }[]
  oversight: [number, number]
}

export interface ForkPoint { id: string; label: string; phase: string; completed_jobs: string[]; round: number; cost_usd: number; at: number; files: number; bytes: number; skipped: string[] }
export interface ForkPoints { points: ForkPoint[]; ideas: { id: string; seat: string; model: string; headline: string; text: string }[]; chosen: string; running: boolean }

/** How far a paper's open PDF is: here, being fetched (bytes so far, of how many), or not to be had. */
export interface PdfProgress { state: 'ready' | 'fetching' | 'none'; loaded: number; total: number }

export interface SetupCheck {
  key: string; label: string; required: boolean; ok: boolean; detail: string; fix: string; action?: string
  /** Docker: where its commands are linked on a Mac (Homebrew's bin, else /usr/local/bin) */
  link_dir?: string
  /** what went wrong in the last install from the console */
  error?: string
}
export interface SetupStatus { checks: SetupCheck[]; ready: boolean; dismissed: boolean; local: boolean }

// ---------------------------------------------------------------------------
// The first minutes: welcome wizard, checklist, tours, tips, examples, help
// ---------------------------------------------------------------------------

export interface ChecklistItem {
  key: string
  label: string
  hint: string
  done: boolean
  /** key | tour:<chapter> | demo | start | brainstorm | library | team | settings:<section> | doc:<path> */
  action: string
}

export type WizardStep = 'welcome' | 'connect' | 'prefs' | 'start'

export interface Onboarding {
  wizard: { done: boolean; step: WizardStep }
  field: string
  autonomy: 'supervised' | 'autonomous'
  max_cost_usd: number | null
  tours: string[]
  tips_seen: string[]
  examples_hidden: boolean
  accounts: boolean
  admin: boolean
  name: string
  checklist: ChecklistItem[]
}

export interface ExampleCard {
  id: string
  kind: 'real run' | 'scripted'
  title: string
  field: string
  blurb: string
  shows: string[]
  facts: string
  campaign_id?: string
  run_id?: string
  installed: boolean
  removed: boolean
}

export interface HelpPage { path: string; title: string; section: string }
export interface HelpAnswer {
  answer: string
  note: string
  sources: { n: number; path: string; page: string; heading: string; excerpt: string }[]
}

// ---------------------------------------------------------------------------
// The paper reader: notes by you and the agents on a PDF, and their way to Zotero

export type ReaderAgent = 'Librarian' | 'Skeptic' | 'Methods'

export interface ReaderReply {
  reply_id: string
  who: string
  who_name: string
  agent: boolean
  text: string
  pending: boolean
  created_at: number
}

/** A note on a PDF: a marked passage, perhaps with a comment and a conversation. */
export interface ReaderNote {
  ann_id: string
  paper_id: string
  /** '' : the person; else the agent who wrote it (Pippin: its notes on reading the paper with you). */
  author: '' | ReaderAgent | 'Pippin'
  mine: boolean
  owner_name: string
  author_name: string
  state: 'kept' | 'suggested' | 'draft'
  text: string
  comment: string
  color: string
  page_index: number
  page_label: string
  /** [[x1, y1, x2, y2], ...] in PDF points; [] : not placed on the page yet. */
  rects: number[][]
  sort_index: string
  shared: boolean
  space: string
  card_id: string
  rel: '' | 'supports' | 'challenges' | 'asks about' | 'builds on'
  /** Written in a gap opened in the page under its words, rather than in the margin. */
  inline: boolean
  /** Pippin's notes: what kind of note it is. */
  kind: '' | 'orient' | 'question' | 'connect' | 'challenge'
  /** Pippin made a figure to try the passage out (shown in a gap under it). */
  figure: boolean
  idea_text: string
  source: 'aurora' | 'zotero'
  status: 'suggested' | 'draft' | 'synced' | 'saving' | 'local' | 'error' | 'shared'
  why: string
  created_at: number
  updated_at: number
  replies: ReaderReply[]
}

export type ZoteroNotesState = 'off' | 'not_in_zotero' | 'no_pdf' | 'read_only' | 'writes_off' | 'ok'

export interface ReaderView {
  paper: LitPaper
  space: string
  campaign_id: string
  pdf: { available: boolean; openable: boolean }
  /** A page kept in the Library: read from its snapshot (its address, to open it on the web). */
  page?: { url: string } | null
  notes: ReaderNote[]
  /** What the agents read for: an idea, or (none written down yet) the conversation's own words. */
  idea: { card_id: string; text: string; kind?: 'idea' | 'conversation' } | null
  reads: Partial<Record<ReaderAgent, { state: 'reading' | 'done' | 'failed'; detail: string }>>
  /** Pippin reading the paper against your work (once per paper and space). */
  companion: { state: 'reading' | 'done' | 'failed'; detail: string } | null
  agents_mark: boolean
  can_share: boolean
  zotero: {
    state: ZoteroNotesState
    connected: boolean
    library?: string
    can_add?: boolean
    last_sync?: number | null
    error?: string
    method?: string
    counts: { synced: number; saving: number; suggested: number; local: number }
  }
}

/** Where a passage is on the page, the way Zotero keeps it. */
export interface NotePlace {
  page_index: number
  page_label?: string
  rects: number[][]
  sort_index?: string
}

// ---------------------------------------------------------------------------
// Zoom out (a map of a research direction) and zoom in (a deep read of a paper)

export type ClaimLabel = 'paper' | 'lit' | 'inf' | 'spec'
export interface Para { text: string; label: ClaimLabel; quote?: string; page?: number }

export interface MapDim { id: string; name: string; component: string; values: { id: string; label: string; meaning: string }[]; explains: string }
export interface Placement { values: Record<string, string>; strain: boolean; note: string; evidence: string; verified: boolean; corrected?: string }
export interface Framework {
  id: string; name: string; by: string; dims: MapDim[]
  placements: Record<string, Placement>; held: Record<string, Placement>; joined?: Record<string, Placement>
  scores: { fit?: number; held?: number; orth?: number; disc?: number; disc_reason?: string; n_fit?: string; n_held?: string }
}
export interface PaperCard {
  sub_problem?: string; choice?: string; assumptions?: string[]; baselines?: string[]
  limitations?: { text: string; stated: boolean }[]; results?: { claim: string; benchmark: string }[]
  capabilities?: string[]; term_use?: string; quotes?: { field: string; text: string }[]; source?: 'pdf' | 'abstract'
}
export interface MapPaper {
  paper_id: string; key: string; title: string; year: number | null; venue: string; authors: string[]; abstract: string
  doi: string; arxiv_id: string; url: string; role: 'core' | 'held_out' | 'joined' | 'adjacent' | 'survey' | 'benchmark' | 'foundation'; card: PaperCard
}
export interface Capability { id: string; name: string; info: string; timescale: string; example: string }
export interface ProgressRow {
  capability: string; status: 'solved' | 'partly' | 'early' | 'open'; headline: string; best: string[]
  results: { key: string; claim: string; benchmark: string }[]; conditions: Para[]
  limitations: { text: string; keys?: string[] }[]; conflicts: Para[]; cost: Para[]; eval_gap: Para[]; papers: string[]
}
export interface MapProblem {
  id: string; title: string; capability: string; importance: string; gap: string; evidence: string[]
  diagnosis: { cause: string; text: string }; why_now: string
  approaches: { text: string; from: string; label: ClaimLabel }[]; first_experiment: string; failure: string
  closest: { key: string; difference: string }[]; novelty: string; novelty_note: string; card_id?: string
}
export interface MapChallenge {
  id: string; text: string; support: Para[]; state: 'pending' | 'applied'; chosen?: string
  options: { id: string; boundary: string; dimensions: string; papers: string; progress: string; joins: string[] }[]
}
export interface MapContent {
  need?: { term: string; why: Para[]; examples: { task: string; needs: string }[]; formal: Para[]; capabilities: Capability[] }
  definition?: {
    communities: { community: string; meaning: string; examples: string[]; label: ClaimLabel }[]
    terms: { term: string; meanings: string[] }[]
    conflicts: { question: string; positions: { view: string; who: string[] }[] }[]
    working: { text: string; components: { id: string; name: string; phrase: string }[]
      boundaries: { question: string; decision: string; why: string; consequence: string }[]
      covers: { capability: string; how: string }[] }
  }
  decided?: string[]
  scope?: {
    topic: string; question: string; concepts: string[]; in_scope: string[]; out_of_scope: string[]; timeframe: string
    assumptions: string[]; subquestions: { id: string; text: string }[]
    criteria: { include: string[]; exclude: { code: string; text: string }[] }; keywords: string[]; ask: string[]; amended?: string[]
  }
  prisma?: { identified?: Record<string, number>; not_found?: number; after_dedupe?: number; screened?: number; conflicts?: number
    agreement?: number | null; full_text_assessed?: number; full_text_excluded?: number; excluded_by_code?: Record<string, number>; included?: number }
  screening?: Record<string, { d: string; code: string; why: string; subs?: string[]; kind?: string; by?: string }>
  coverage?: { per_sub: Record<string, number>; skew: { dimension: string; value: string; n: number; known: number }[] }
  frameworks?: Framework[]; chosen?: string
  proposals?: { id: string; kind: string; framework: string; paper_id: string; text: string; state: 'pending' | 'accepted' | 'dismissed' }[]
  progress?: ProgressRow[]
  timeline?: { years: string; name: string; driver: string; text: string; turning: string[] }[]
  trends?: { title: string; text: string; label: ClaimLabel; strength: string; based: string }[]
  assumptions?: { text: string; keys: string[]; why: string; overturn: string; challengers: string[]; label: ClaimLabel }[]
  problems?: MapProblem[]
  adjacent?: { field: string; same: string; borrow: string; checks: { ok: boolean | null; text: string }[] }[]
  sparse?: { a: string; b: string; papers: string[] }[]
  read_next?: { key: string; paper_id: string; why: string; focus: number[] }[]
  challenges?: MapChallenge[]
  /** The space's ideas on the map: where each sits, and the unsolved problem it answers. */
  ideas?: Record<string, { text: string; framework: string; values: Record<string, string>; problem: string; note: string }>
  distribution?: { periods?: { from: number; to: number; dims: Record<string, Record<string, string[]>> }[] }
  history?: { version: number; reason: string; at: number }[]
  done?: string[]
}
export interface FieldMap {
  map_id: string; space: string; topic: string; brief: string; vis: 'private' | 'project'
  state: 'new' | 'building' | 'waiting' | 'ready' | 'failed'; step: string; step_name: string; error: string; version: number
  mine: boolean; busy: boolean; steps: { id: string; name: string; done: boolean }[]
  content: MapContent; papers: MapPaper[]; outside: { paper_id: string; key: string; title: string; year: number | null }[]
  versions: { version: number; reason: string; created_at: number }[]; log: { at: number; step: string; text: string }[]
  new_papers: number
}
export interface FieldMapSummary { map_id: string; topic: string; state: string; version: number; vis: string; mine: boolean; updated_at: number }

export interface DeepAnswer {
  state: 'pending' | 'done' | 'failed' | 'hidden'; error?: string; ready?: boolean
  paragraphs?: Para[]; followups?: { q: string; paragraphs: Para[] }[]
  example?: string; steps?: { what: string; example: string }[]; none?: boolean
  rows?: { question: string; experiment: string; answer: string }[]; claim?: string; plan?: { day: string; do: string }[]
  idea?: string; source?: string; closest?: { key: string; paper_id: string; difference: string }[]; novelty?: string; novelty_note?: string; card_id?: string
  moves?: { text: string; kind: string; label: ClaimLabel; support?: 'stated' | 'consistent' | 'unsupported'; quote?: string; page?: number }[]
  summary?: Para[]; authors_say?: Para[]; context?: { intro_paragraphs_dropped: number; had_related: boolean; earlier: string[] }
}
export interface DeepRead {
  read_id: string; paper_id: string; space: string; map_id: string; guess: string; revealed: boolean; ticks: number[]
  answers: Record<string, DeepAnswer>; busy: boolean; paper: { paper_id: string; key: string; title: string }
  questions: { n: number; title: string; key: boolean; agent: string }[]
  /** the reader's own answers, by question; the questions whose AI answer they asked to see */
  mine: Record<string, { text: string; at: number }>; shown: number[]
  /** questions the reader answered; questions whose AI answer is written (shown or not) */
  answered: number[]; ready: number[]
}

export type FinerKey = 'feasible' | 'interesting' | 'novel' | 'ethical' | 'relevant'
export type FinerScores = Partial<Record<FinerKey, { score: number; why: string }>>
/** A research question written down from a guided conversation (an RQ Summary), or AI-generated candidates. */
export interface ResearchQuestion {
  kind: 'summary' | 'candidates'
  state: 'draft' | 'settled'
  origin: 'you' | 'ai'
  question?: string; tightened?: string
  /** what the researcher said about each FINER criterion */
  self?: Partial<Record<FinerKey, string>>
  /** the formal FINER scoring (the AI's) */
  formal?: FinerScores
  quality?: string[]
  candidates?: { question: string; type: string; finer: FinerScores }[]
  focus: string[]; excluded: string[]; to_confirm: string[]
  version: number; card_id?: string
}
export interface PlanInsight {
  insight_id: string; chat_id: string; section: string; text: string; quote: string; msg_id: string
  origin: 'you' | 'ai'; created_at: number
}
/** A version of an idea: who wrote it, and how much of the text is the researcher's own (0 to 1). */
export interface IdeaVersion {
  version_id: string; card_id: string; n: number; text: string; origin: 'you' | 'ai' | 'edited'; share: number; by: string; created_at: number
}
export interface ResearchPlan {
  idea?: { card_id: string; text: string; versions: IdeaVersion[] } | null
  active: boolean; started: boolean; ended: string; suggesting: boolean
  question: ResearchQuestion | null
  sections: { id: string; name: string }[]
  insights: PlanInsight[]
}

export type MsgWidget =
  | { type: 'proposal'; action: 'map_field'; topic: string; brief: string; size: number; calls: number; state: 'pending' | 'started' | 'declined'; map_id?: string }
  | { type: 'proposal'; action: 'deep_read'; paper_id: string; key: string; title: string; state: 'pending' | 'started' | 'declined'; read_id?: string }
  | { type: 'map_step'; map_id: string; step: string }
  | { type: 'map_failed'; map_id: string; state?: string }
  | { type: 'challenge'; map_id: string; challenge_id: string; state: 'pending' | 'applied'; chosen?: string }
  | { type: 'guess'; read_id: string; state: 'waiting' | 'answered' }
  | { type: 'path'; read_id: string; paper_id: string }
  | { type: 'scope' | 'corpus'; map_id: string; state: 'waiting' | 'confirmed' | 'superseded' }
  | { type: 'drafts'; purpose: 'idea' | 'direction'; agent: string; card_id: string; round?: number; gate_id?: string; run_id?: string
      drafts: { text: string; yours?: boolean; seat?: string; chosen?: boolean }[]
      state: 'waiting' | 'taken' | 'superseded' | 'answered'; taken?: number; origin?: 'you' | 'ai' | 'edited'; share?: number; text?: string; action?: string }
  | { type: 'choice'; versions: string[]; card_id: string; state: 'waiting' | 'chosen' | 'superseded'; chosen?: number }
  | { type: 'question'; kind: 'summary' | 'candidates'; state: 'waiting' | 'confirmed' | 'superseded'; version: number; rq: ResearchQuestion; card_id?: string }
  /** A figure an agent drew to try something out: being drawn, then shown (``fig_id``). */
  | { type: 'figure'; state: 'drawing' | 'ready' | 'failed'; about: string; fig_id?: string }
  /** The Skeptic questioned the researcher unasked (friction); "not now" waves it off for a while. */
  | { type: 'friction'; state: 'open' | 'waved'; signals: Record<string, number> }
  /** What the researcher brought back from a tangent. */
  | { type: 'from_branch'; chat_id: string; title: string }

// ---------------------------------------------------------------------------
// The shared notebook

export type MemStance = 'challenges' | 'adds' | 'supports' | 'background' | 'tool'
/** An idea unit as a context shows it: in a layer, with how it stands and why it is at that depth. */
export interface MemItem {
  unit_id: string; claim: string; gist: string; kind: string; layer: 'surface' | 'absorbed' | 'sunk'
  stance: MemStance; why: string; relevance: number; sources: number; platforms: string[]; sunk: boolean
  clash_with: { unit_id: string; claim: string; yours?: boolean } | null; yours?: boolean; moved: boolean
  answers: { question: string; answer: string; at: number | null }[]
}
export interface MemSurface {
  context: { key: string; kind: 'reading' | 'question' | 'paper'; label: string; chip: string; sub: string }
  surface: MemItem[]; absorbed: MemItem[]; sunk: MemItem[]; n_sunk: number; conflicts: number
}
export interface MemEvidence {
  evidence_id: string; source_id: string; quote: string; stance: string; platform: string; url: string; title: string
  author: string; lang: string; paper_id: string; published: string; via: string; created_at: number
}
export interface MemUnit {
  unit_id: string; claim: string; gist: string; kind: string; sunk: boolean; moved: boolean
  evidence: MemEvidence[]
  links: { rel: 'contradicts' | 'refines'; note: string; unit_id: string; claim: string; direction: 'in' | 'out' }[]
  questions: { question_id: string; label: string; text: string; relevance: number }[]
  answers: MemAsk[]
}
export interface MemSource {
  source_id: string; platform: string; url: string; title: string; via: string; state: 'pending' | 'done' | 'failed'
  error: string; summary?: string; created_at: number
  result: { units?: string[]; merged?: string[]; new?: string[]; conflicts?: string[][]; questions?: string[]; duplicate_of?: string }
}
export interface MemQuestion { question_id: string; label: string; text: string; origin: 'manual' | 'mentor' }
export interface MemAsk {
  ask_id: string; question: string; basis: string[]; answer: string; created_at: number; answered_at: number | null
  basis_units?: { unit_id: string; claim: string }[]
}
export interface MemReport {
  days: number; sources: number; merged: number; new_units: number
  questions: { question_id: string; label: string; text: string; new_units: { unit_id: string; claim: string }[]; conflicts: { a: string; b: string }[] }[]
  conflicts: { a: string; b: string; why: string }[]
  moved: { unit_id: string; claim: string }[]; answers: { question: string; answer: string }[]
  archive: { unit_id: string; claim: string }[]
}
export type WriteLevel = 'off' | 'ask' | 'auto'
export interface WriteSettings {
  levels: Record<string, WriteLevel>
  writes: Record<string, { target: string; label: string }>
  options: WriteLevel[]
  log: { log_id: string; write: string; detail: Record<string, unknown>; created_at: number; undone_at: number | null }[]
}
export interface MemDigest {
  hours: number; sources: number; from_feed: number
  groups: { question_id: string; label: string; text: string
    merged: { unit_id: string; claim: string; sources: number; from: string; platform: string; via: string }[]
    new: { unit_id: string; claim: string; sources: number; from: string; platform: string; via: string }[]
    conflicts: { a: string; b: string; a_id: string; b_id: string }[] }[]
}
export interface MemFollow {
  follow_id: string; platform: string; url: string; label: string; via: 'extension' | 'server'; every_min: number
  seen: number; last_run: number; last_error: string; paused: number
}
export interface MemDissent {
  paper_id: string; note?: string
  items: { page: number; quote: string; kind: 'challenges' | 'qualifies'; why: string; units: { unit_id: string; claim: string; sources: number }[] }[]
}
export interface MemCite {
  sentence: string; note: string
  candidates: { paper_id: string; key: string; title: string; byline: string; quote: string; how: string; from: string; bibtex: string; doi: string }[]
}
export interface MemAuditEntry { key: string; title: string; status: string; reason: string; cited?: boolean; paper_id?: string; doi?: string; found_title?: string; retracted?: string; year?: number }
export interface MemAudit {
  audit_id: string; title: string; state: 'running' | 'done' | 'failed'; step: string; error: string; created_at: number
  report: {
    title?: string; entries?: MemAuditEntry[]; unused?: string[]; undefined?: string[]
    miscites?: { key: string; sentence: string; line: number; verdict: 'partly' | 'no'; issue: string; quote: string; title: string }[]
    missing?: { paper_id: string; title: string; byline: string; why: string; bibtex: string }[]
    counts?: Record<string, number>
  }
}
export interface MemAlert { alert_id: string; kind: 'retraction' | 'overlap' | 'decision'; ref?: { url?: string; doi?: string; paper_id?: string }; tier: 'now' | 'digest'; title: string; body: string; created_at: number; seen_at: number | null; verdict: string }

export interface MemVault {
  path: string; folders: string[]; exclude: string[]; on: boolean; notes?: number
}

/** Something that waits for a person, on the Today page. */
export interface TodayNeed {
  kind: 'decision' | 'alert' | 'skill'
  id: string
  title: string
  detail: string
  at: number
  /** decision: where, and what the gate offers */
  campaign_id?: string
  run_id?: string
  project?: string
  run?: string
  source?: string
  options?: GateAction[]
  /** alert: what kind, and the paper */
  alert?: string
  ref?: { url?: string; doi?: string; paper_id?: string }
}

/** The Today page: what waits, what changed since yesterday, what is worth reading. */
export interface TodayView {
  name: string
  needs: TodayNeed[]
  changed: { kind: 'run' | 'memory'; at: number; text: string; tone: '' | 'ok' | 'bad'; campaign_id?: string; run_id?: string }[]
  reading: { item_id: string; title: string; url: string; why: string; project: string; label: string; source: string; is_paper: boolean }[]
  projects: number
  running: number
}

/** A turn of the conversation with Pippin at your side (the orb, the side panel). */
export interface AssistantTurn {
  turn_id: string
  role: 'user' | 'assistant'
  text: string
  at: number
  /** typed | voice (their turns) */
  via?: string
  /** What the answer rests on, from the notebook. */
  items?: MemVoiceTurn['items']
  /** Buttons that open a page of the console (a path on it). */
  links?: { label: string; path: string }[]
  undo_id?: string
  /** What Pippin proposed to do, done only when they confirm. */
  actions?: AssistantAction[]
  /** Asked about the page open in the browser (the side panel). */
  page?: { url: string; title: string; selection: string }
  /** The answer, kept as a note on that page. */
  kept?: KeptPage & { ann_id: string; quote: string }
}

/** A page (or a paper's page) kept in the Library from the browser. */
export interface KeptPage {
  paper_id: string
  key: string
  title: string
  is_page: boolean
}

/** A proposal of Pippin's: answer a run's decision, tell a run or a project something, stop a run. */
export interface AssistantAction {
  action_id: string
  kind: 'decide' | 'message' | 'stop'
  project_id: string
  project: string
  run_id: string
  run: string
  /** What it will do, in a sentence. */
  label: string
  state: 'pending' | 'done' | 'declined' | 'failed' | 'expired'
  /** What came of it. */
  result: string
  at: number
}

export interface MemVoiceTurn {
  turn_id: string; at: number; said: string; intent: string; speech: string; text: string; undo_id: string; waiting: boolean
  items: { n?: number; kind?: string; text?: string; unit_id?: string; claim?: string; sources?: { title: string; url: string; quote: string }[] }[]
}

/** A chat app Pippin can be talked to from. */
export type ChatApp = 'feishu' | 'wecom' | 'slack' | 'whatsapp'
/** A chat app's settings as the console shows them: secrets only as whether they are set. */
export interface ChatAppSettings {
  app: ChatApp
  enabled: boolean
  ready: boolean
  set: Record<string, boolean>
  [field: string]: string | boolean | Record<string, boolean>
}

// The notebook: a page for every run, clips from papers, your notes
export type Verdict = 'held' | 'partly' | 'not' | 'untested'
export interface NbRunPage {
  run_id: string
  campaign_id: string
  name: string
  status: string
  phase: string
  idea: { title: string; description: string }
  hypothesis: string
  refuted_if: string
  why: { gap: string[]; rationale: string; alternatives: string; scores: Record<string, number> }
  expected: string[]
  claims: { statement: string; status: string; confidence: number | null }[]
  results: { name: string; value: number | string; unit: string; uncertainty: number | null; description: string }[]
  report: boolean
  has_idea: boolean
  /** What the researcher said when they chose the direction ('' if nothing). */
  you_said: string
  /** Expected against actual, judged once per run (null: nothing to judge yet, or no model key). */
  match: { state: 'judging' | 'done' | 'failed'; stale: boolean; items?: { expected: string; actual: string; verdict: Verdict }[];
    hypothesis?: { verdict: Verdict; why: string }; error?: string } | null
  note: { html: string; updated_at: number } | null
  clips: NbClip[]
}
export interface NbClip {
  clip_id: string
  paper_id: string
  paper_title: string
  space: string
  page_index: number
  rects: number[][]
  quote: string
  /** Your own words for it. */
  words: string
  campaign_id: string
  run_id: string
  created_at: number
  updated_at: number
}
export interface NbNoteSummary { note_id: string; title: string; campaign_id: string; preview: string; created_at: number; updated_at: number }
export interface NbNote extends NbNoteSummary { html: string; text: string; run_id: string }
export interface NbOverview {
  projects: { campaign_id: string; name: string; example: boolean; runs: { run_id: string; name: string; status: string; started_at: number }[] }[]
  clips: number
  notes: NbNoteSummary[]
}

/** Where what you keep while reading can go: an Obsidian vault, a Notion page. */
export interface KbTarget { ready: boolean; level: 'off' | 'ask' | 'auto'; where: string; why: string }
export interface KbStatus {
  obsidian: KbTarget
  notion: KbTarget
  notion_settings: { page: string; page_title: string; token: boolean; hint: string }
}
