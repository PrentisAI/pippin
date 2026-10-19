import type { ClusterJobs, NotificationSettings, NotifyChannel, ProjectFileEntry, ProjectFileListing, Analytics, EditableSetting, SearchHit, SuggestionOutcome,
  AutonomyMode,
  CampaignDetail,
  CampaignSummary,
  ConsoleSettings,
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
  if (res.status === 204) return undefined as T
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

    rename: (id: string, name: string) =>
      request<CampaignSummary>(`/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify({ name }) }),

    /** Bytes the project's files take on disk. */
    diskUsage: (id: string) => request<{ bytes: number; path: string }>(`/campaigns/${id}/disk`),

    /** Remove a project (stop its experiments first); with deleteFiles, also its files on disk. */
    delete: (id: string, deleteFiles = false) =>
      request<{ ok: boolean }>(`/campaigns/${id}?delete_files=${deleteFiles}`, { method: 'DELETE' }),

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
      request<ExperimentSummary>(`${experimentPath(campaignId, expId)}/stop`, post()),

    rename: (campaignId: string, expId: string, name: string) =>
      request<ExperimentSummary>(experimentPath(campaignId, expId), { method: 'PATCH', body: JSON.stringify({ name }) }),

    diskUsage: (campaignId: string, expId: string) =>
      request<{ bytes: number; path: string }>(`${experimentPath(campaignId, expId)}/disk`),

    /** Remove a stopped run; with deleteFiles, also its files on disk. */
    remove: (campaignId: string, expId: string, deleteFiles = false) =>
      request<{ ok: boolean }>(`${experimentPath(campaignId, expId)}?delete_files=${deleteFiles}`, { method: 'DELETE' }),

    /** Answer the experiment's open human gate. `instruct` needs a message. */
    decide: (campaignId: string, expId: string, action: GateAction, message = '', gateId = '', rerun: string[] = []) =>
      request<{ ok: boolean; decision_id: string; action: GateAction }>(
        `${experimentPath(campaignId, expId)}/decide`,
        post({ action, message, gate_id: gateId, rerun }),
      ),

    setResources: (campaignId: string, expId: string, cpus: number | null, memoryMb: number | null) =>
      request<{ ok: boolean; live: boolean; cpus: number; memory_mb: number }>(
        `${experimentPath(campaignId, expId)}/resources`,
        { method: 'PUT', body: JSON.stringify({ cpus, memory_mb: memoryMb }) },
      ),

    /** Switch the experiment's front desk on or off (off: no model calls for it). */
    setFrontdesk: (campaignId: string, expId: string, enabled: boolean) =>
      request<{ ok: boolean; frontdesk_enabled: boolean }>(
        `${experimentPath(campaignId, expId)}/frontdesk`, { method: 'PUT', body: JSON.stringify({ enabled }) }),

    setBudget: (campaignId: string, expId: string, maxCostUsd: number | null) =>
      request<{ ok: boolean; max_cost_usd: number | null; spent_usd: number; note?: string }>(
        `${experimentPath(campaignId, expId)}/budget`, { method: 'PUT', body: JSON.stringify({ max_cost_usd: maxCostUsd }) }),

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

  settings: {
    get: () => request<ConsoleSettings>('/settings'),
    /** Change the defaults for new experiments (dotted keys); reset puts keys back to the server's value. */
    update: (changes: Record<string, unknown>, reset: string[] = []) =>
      request<{ saved: boolean; editable: EditableSetting[] }>('/settings', { method: 'PUT', body: JSON.stringify({ changes, reset }) }),
    notifications: () => request<NotificationSettings>('/settings/notifications'),
    /** Replace the notification settings; a channel without `target` keeps its saved one. */
    saveNotifications: (body: { console_url: string; budget_warning_at: number; channels: NotifyChannel[] }) =>
      request<NotificationSettings>('/settings/notifications', { method: 'PUT', body: JSON.stringify(body) }),
    testNotification: (channel: NotifyChannel) =>
      request<{ ok: boolean; error: string }>('/settings/notifications/test', { method: 'POST', body: JSON.stringify(channel) }),
  },

  compute: {
    jobs: (campaignId: string, expId: string) =>
      request<ClusterJobs>(`/campaigns/${campaignId}/experiments/${expId}/compute`),
    cancel: (campaignId: string, expId: string, job: number) =>
      request<{ message: string }>(`/campaigns/${campaignId}/experiments/${expId}/compute/${job}/cancel`, { method: 'POST' }),
  },

  search: (q: string, limit = 8) =>
    request<{ query: string; results: Partial<Record<'projects' | 'runs' | 'conversations' | 'reports' | 'skills', SearchHit[]>> }>(
      `/search?q=${encodeURIComponent(q)}&limit=${limit}`),

  analytics: (days = 30) => request<Analytics>(`/analytics?days=${days}`),

  /** Files uploaded to a project (shared by its runs). Uploads go file by file (see upload.ts). */
  projectFiles: {
    list: (campaignId: string, path = '') =>
      request<ProjectFileListing>(`/campaigns/${campaignId}/files?path=${encodeURIComponent(path)}`),
    uploadUrl: (campaignId: string, path: string, extract = false) =>
      `${BASE_URL}/campaigns/${campaignId}/files?path=${encodeURIComponent(path)}&extract=${extract}`,
    preview: (campaignId: string, path: string) =>
      request<{ path: string; binary: boolean; size: number; content?: string; truncated?: boolean }>(
        `/campaigns/${campaignId}/files/preview?path=${encodeURIComponent(path)}`),
    mkdir: (campaignId: string, path: string) =>
      request<ProjectFileEntry>(`/campaigns/${campaignId}/files/folder?path=${encodeURIComponent(path)}`, post()),
    remove: (campaignId: string, path: string) =>
      request<{ ok: boolean }>(`/campaigns/${campaignId}/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
    /** One note in the project chat about a finished upload. */
    noteUploaded: (campaignId: string, names: string[]) =>
      request<{ ok: boolean }>(`/campaigns/${campaignId}/files/uploaded`, post({ names })),
  },

  proposals: {
    confirm: (campaignId: string, proposalId: string, body: {
      skip_phases?: string[]
      model?: string
      max_cost_usd?: number | null
      ideation_mode?: string
      autonomy_mode?: string
      models?: Record<string, unknown>
      cpus?: number
      memory_mb?: number
      max_parallel_jobs?: number
      inputs?: string[]
    }) =>
      request<{ status: string; experiment_id: string; skip_phases: string[]; active_phases: string[] }>(
        `/campaigns/${campaignId}/proposals/${proposalId}/confirm`,
        post(body),
      ),
  },

  skills: {
    list: () => request<Skill[]>('/skills/'),
    template: () => request<{ sections: { name: string; what: string }[]; template: string }>('/skills/template'),
    create: (skill: { name: string; rule: string; description?: string; scope?: string; kind?: string }) =>
      request<Skill>('/skills/', post({ origin: 'manual', scope: 'global', ...skill })),
    /** A new version of a skill; the current one goes into its history. */
    revise: (skillId: string, rule: string, reason: string, description?: string) =>
      request<Skill>(`/skills/${skillId}/revise`, post({ rule, reason, description })),
    update: (skillId: string, fields: Partial<Pick<Skill, 'name' | 'description' | 'scope' | 'kind' | 'projects'>>) =>
      request<Skill>(`/skills/${skillId}`, { method: 'PATCH', body: JSON.stringify(fields) }),
    remove: (skillId: string) => request<void>(`/skills/${skillId}`, { method: 'DELETE' }),
    /** Accept a suggested skill: a new one, or a revision of the skill it names. */
    suggest: (skill: {
      name: string
      rule: string
      description?: string
      source_phase?: string
      source_experiment?: string
      source_campaign?: string
      scope: 'experiment' | 'campaign' | 'projects' | 'global'
      origin?: string
      kind?: string
      skill_id?: string
      reason?: string
      suggestion_id?: string
      projects?: string[]
    }) => request<Skill>('/skills/suggest', post(skill)),
    /** What was done with suggestions, by ID: added (with where) or dismissed. */
    outcomes: (ids: string[]) =>
      request<Record<string, SuggestionOutcome>>(`/skills/suggestions?ids=${encodeURIComponent(ids.join(','))}`),
    dismiss: (suggestionId: string) =>
      request<{ state: string }>(`/skills/suggestions/${encodeURIComponent(suggestionId)}/dismiss`, post()),
  },

  /** The project conversation with the front desk. */
  chat: {
    send: (campaignId: string, text: string, attachments: string[] = []) =>
      request<{ status: string; response?: string; error?: string; proposal?: ConversationMessage['proposal'] }>(
        `/campaigns/${campaignId}/chat/send`,
        post({ text, attachments }),
      ),

    history: (campaignId: string, limit = 100) =>
      request<ConversationMessage[]>(`/campaigns/${campaignId}/chat/history?limit=${limit}`),

    status: (campaignId: string) =>
      request<ProjectChatStatus>(`/campaigns/${campaignId}/chat/status`),
  },

  /** The conversation with a running experiment. */
  expChat: {
    send: (campaignId: string, expId: string, text: string, attachments: string[] = []) =>
      request<{ status: string; response: string }>(`${experimentPath(campaignId, expId)}/chat/send`, post({ text, attachments })),

    history: (campaignId: string, expId: string, limit = 100) =>
      request<ConversationMessage[]>(`${experimentPath(campaignId, expId)}/chat/history?limit=${limit}`),
  },

  workspace: {
    /** Where to PUT a file uploaded into a run (path relative to its directory; workspace/ is /app). */
    uploadUrl: (campaignId: string, expId: string, path: string) =>
      `${BASE_URL}${experimentPath(campaignId, expId)}/workspace/upload?path=${encodeURIComponent(path)}`,
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
