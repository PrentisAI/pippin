import type {
  AutonomyMode,
  CampaignDetail,
  CampaignSummary,
  ConversationMessage,
  ExperimentDetail,
  ExperimentSummary,
  FileNode,
  GateAction,
  ProjectChatStatus,
  Skill,
  StartExperimentRequest,
  StartExperimentResponse,
} from '../types'

const BASE_URL = '/api'

/** An HTTP error from the API; `message` is the server's explanation (FastAPI's `detail`). */
class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** The server's explanation of a failed request, from `{"detail": ...}` when there is one. */
function errorMessage(status: number, statusText: string, body: string): string {
  try {
    const detail = JSON.parse(body)?.detail
    if (typeof detail === 'string') return detail
    if (detail != null) return JSON.stringify(detail)
  } catch {
    // not JSON
  }
  return body || `${status} ${statusText}`
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(res.status, errorMessage(res.status, res.statusText, body))
  }
  return res.json()
}

const post = (body?: unknown): RequestInit => ({
  method: 'POST',
  body: body === undefined ? undefined : JSON.stringify(body),
})

const experimentPath = (campaignId: string, expId: string) => `/campaigns/${campaignId}/experiments/${expId}`

export const api = {
  campaigns: {
    list: () => request<CampaignSummary[]>('/campaigns/'),

    get: (id: string) => request<CampaignDetail>(`/campaigns/${id}`),

    create: (data: { name: string; objective?: string }) =>
      request<CampaignSummary>('/campaigns/', post(data)),

    delete: (id: string) =>
      request<{ ok: boolean }>(`/campaigns/${id}`, { method: 'DELETE' }),

    quickCreate: (prompt: string) =>
      request<{ campaign_id: string; name: string; objective: string }>('/campaigns/quick', post({ prompt })),
  },

  experiments: {
    start: (campaignId: string, config: StartExperimentRequest) => {
      const cleaned = Object.fromEntries(
        Object.entries(config).filter(([, v]) => v !== '' && v !== undefined && v !== null),
      )
      return request<StartExperimentResponse>(`/campaigns/${campaignId}/experiments`, post(cleaned))
    },

    get: (campaignId: string, expId: string) =>
      request<ExperimentDetail>(experimentPath(campaignId, expId)),

    /** Stop a running experiment; it ends at the next safe point and keeps its files. */
    stop: (campaignId: string, expId: string) =>
      request<ExperimentSummary>(experimentPath(campaignId, expId), { method: 'DELETE' }),

    /** Answer the experiment's open human gate. `instruct` needs a message. */
    decide: (campaignId: string, expId: string, action: GateAction, message = '', gateId = '') =>
      request<{ ok: boolean; decision_id: string; action: GateAction }>(
        `${experimentPath(campaignId, expId)}/decide`,
        post({ action, message, gate_id: gateId }),
      ),

    setAutonomyMode: (campaignId: string, expId: string, mode: AutonomyMode) =>
      request<{ ok: boolean; autonomy_mode: AutonomyMode; previous_mode: AutonomyMode }>(
        `${experimentPath(campaignId, expId)}/autonomy`,
        { method: 'PUT', body: JSON.stringify({ autonomy_mode: mode }) },
      ),

    /** Start a new experiment with the same settings. */
    rerun: (campaignId: string, expId: string) =>
      request<StartExperimentResponse>(`${experimentPath(campaignId, expId)}/rerun`, post()),

    /** Continue a stopped or failed experiment from its checkpoint (409 when that is not possible). */
    resume: (campaignId: string, expId: string) =>
      request<StartExperimentResponse>(`${experimentPath(campaignId, expId)}/resume`, post()),
  },

  proposals: {
    confirm: (campaignId: string, proposalId: string, body: { skip_phases?: string[]; model?: string; max_cost_usd?: number | null; ideation_mode?: string }) =>
      request<{ status: string; experiment_id: string; skip_phases: string[]; active_phases: string[] }>(
        `/campaigns/${campaignId}/proposals/${proposalId}/confirm`,
        post(body),
      ),
  },

  skills: {
    /** Accept a suggested skill (creates it, or updates the rule of a skill with the same name). */
    suggest: (skill: {
      name: string
      rule: string
      description?: string
      source_phase?: string
      source_experiment?: string
      source_campaign?: string
      scope: 'experiment' | 'campaign' | 'global'
    }) => request<Skill>('/skills/suggest', post(skill)),
  },

  /** The project conversation with the front desk. */
  chat: {
    send: (campaignId: string, text: string) =>
      request<{ status: string; response?: string; error?: string; proposal?: ConversationMessage['proposal'] }>(
        `/campaigns/${campaignId}/chat/send`,
        post({ text }),
      ),

    history: (campaignId: string, limit = 100) =>
      request<ConversationMessage[]>(`/campaigns/${campaignId}/chat/history?limit=${limit}`),

    status: (campaignId: string) =>
      request<ProjectChatStatus>(`/campaigns/${campaignId}/chat/status`),
  },

  /** The conversation with a running experiment. */
  expChat: {
    send: (campaignId: string, expId: string, text: string) =>
      request<{ status: string; response: string }>(`${experimentPath(campaignId, expId)}/chat/send`, post({ text })),

    history: (campaignId: string, expId: string, limit = 100) =>
      request<ConversationMessage[]>(`${experimentPath(campaignId, expId)}/chat/history?limit=${limit}`),
  },

  workspace: {
    tree: (campaignId: string, expId: string) =>
      request<{ experiment_id: string; files: FileNode[] }>(`${experimentPath(campaignId, expId)}/workspace/tree`),

    readFile: (campaignId: string, expId: string, path: string) =>
      request<{ path: string; content: string | null; binary: boolean; size: number; truncated?: boolean }>(
        `${experimentPath(campaignId, expId)}/workspace/file?path=${encodeURIComponent(path)}`,
      ),

    rawUrl: (campaignId: string, expId: string, path: string) =>
      `${BASE_URL}${experimentPath(campaignId, expId)}/workspace/raw?path=${encodeURIComponent(path)}`,

    writeFile: (campaignId: string, expId: string, path: string, content: string) =>
      request<{ path: string; size: number; synced_to_docker: boolean }>(
        `${experimentPath(campaignId, expId)}/workspace/file`,
        { method: 'PUT', body: JSON.stringify({ path, content }) },
      ),
  },
}

export { ApiError }

/** A message to show the user for a failed call. */
export function describeError(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}
