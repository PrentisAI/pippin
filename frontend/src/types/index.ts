export type PipelineStage =
  | 'roundtable'
  | 'screening'
  | 'planning'
  | 'preflight'
  | 'researcher'
  | 'analysis'
  | 'writing'
  | 'followup'

export type CampaignPhase = PipelineStage | 'completed' | 'failed'

export type AutonomyMode = 'autonomous' | 'supervised' | 'collaborative'

export interface Campaign {
  campaign_id: string
  title: string
  objective: string
  phase: CampaignPhase
  autonomy_mode?: AutonomyMode
  created_at: string
  updated_at: string
  iteration_count: number
}

export interface CampaignDetail extends Campaign {
  config?: Record<string, any>
  progress?: PipelinePhaseStatus[]
}

export interface PipelinePhaseStatus {
  phase: PipelineStage
  status: 'done' | 'active' | 'pending'
  start_time?: string
  end_time?: string
}

export interface Experiment {
  job_id: string
  skill_used: string
  parameters: Record<string, any>
  status: 'pending' | 'active' | 'completed' | 'failed'
  started_at?: string
  completed_at?: string
  metrics: Record<string, number>
  failure_diagnosis?: string
}

export interface Observation {
  observation_id: string
  source_job_id: string
  description: string
  numerical_values: Record<string, number>
  timestamp: string
  validated?: boolean
}

export interface Claim {
  claim_id: string
  statement: string
  status: 'tentative' | 'accepted' | 'rejected'
  confidence: number
  supporting_evidence: string[]
  promoted_at?: string
  rejected_at?: string
  rejection_reason?: string
}

export interface ProvenanceNode {
  id: string
  type: 'experiment' | 'observation' | 'claim'
  label: string
  status: string
  metadata: Record<string, any>
}

export interface ProvenanceEdge {
  source: string
  target: string
  relation: 'produced' | 'supports'
}

export interface ProvenanceGraph {
  nodes: ProvenanceNode[]
  edges: ProvenanceEdge[]
}

export interface ActivityEvent {
  id: string
  timestamp: string
  event_type: string
  source: string
  data: Record<string, any>
  level: 'info' | 'warning' | 'error' | 'success'
}

export interface AuditRecord {
  audit_id: string
  trigger: string
  outcome: string
  finding: string
  severity: string
  timestamp: string
  iteration: number
}

export interface DashboardStats {
  total_experiments: number
  completed_experiments: number
  failed_experiments: number
  success_rate: number
  total_claims: number
  accepted_claims: number
  tentative_claims: number
  rejected_claims: number
  total_observations: number
  iteration_count: number
  current_phase: string
}

export interface TimelineEntry {
  timestamp: string
  event: string
  phase: string
  detail: string
  type: 'phase_change' | 'phase_start' | 'phase_end' | 'experiment' | 'claim' | 'audit' | 'milestone'
}

/** Labels for pipeline stages displayed in the UI */
export const PIPELINE_STAGE_LABELS: Record<PipelineStage, string> = {
  roundtable: 'Roundtable',
  screening: 'Screening',
  planning: 'Planning',
  preflight: 'Preflight',
  researcher: 'Research Loop',
  analysis: 'Analysis',
  writing: 'Writing',
  followup: 'Follow-up',
}

/** Ordered list of pipeline stages */
export const PIPELINE_STAGES: PipelineStage[] = [
  'roundtable',
  'screening',
  'planning',
  'preflight',
  'researcher',
  'analysis',
  'writing',
  'followup',
]
