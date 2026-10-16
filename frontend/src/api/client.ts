const BASE_URL = '/api'

class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new ApiError(
      res.status,
      `API error ${res.status}: ${body || res.statusText}`,
    )
  }
  return res.json()
}

export const api = {
  campaigns: {
    list: () => request<any[]>('/campaigns/'),

    get: (id: string) => request<any>(`/campaigns/${id}`),

    create: (data: { name: string; objective?: string }) =>
      request<any>('/campaigns/', {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    delete: (id: string) =>
      request<any>(`/campaigns/${id}`, { method: 'DELETE' }),

    quickCreate: (prompt: string) =>
      request<any>('/campaigns/quick', {
        method: 'POST',
        body: JSON.stringify({ prompt, auto_start: true }),
      }),
  },

  experiments: {
    start: (campaignId: string, config: Record<string, unknown>) => {
      const cleaned = Object.fromEntries(
        Object.entries(config).filter(([_, v]) => v !== '' && v !== undefined && v !== null),
      )
      return request<any>(`/campaigns/${campaignId}/experiments`, {
        method: 'POST',
        body: JSON.stringify(cleaned),
      })
    },

    get: (campaignId: string, expId: string) =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}`),

    stop: (campaignId: string, expId: string) =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}`, {
        method: 'DELETE',
      }),

    decide: (campaignId: string, expId: string, action: string, message: string = '') =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}/decide`, {
        method: 'POST',
        body: JSON.stringify({ action, message }),
      }),

    setAutonomyMode: (campaignId: string, expId: string, mode: string) =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}/autonomy`, {
        method: 'PUT',
        body: JSON.stringify({ autonomy_mode: mode }),
      }),

    rerun: (campaignId: string, expId: string) =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}/rerun`, {
        method: 'POST',
      }),

    resume: (campaignId: string, expId: string) =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}/resume`, {
        method: 'POST',
      }),
  },

  tasks: {
    list: (tasksDir?: string) =>
      request<any[]>(`/lhtb/tasks${tasksDir ? `?tasks_dir=${encodeURIComponent(tasksDir)}` : ''}`),
  },

  chat: {
    send: (campaignId: string, text: string) =>
      request<any>(`/campaigns/${campaignId}/chat/send`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      }),

    history: (campaignId: string, limit = 100) =>
      request<any[]>(`/campaigns/${campaignId}/chat/history?limit=${limit}`),

    status: (campaignId: string) =>
      request<any>(`/campaigns/${campaignId}/chat/status`),

    approve: (campaignId: string, approvalId: string, decision: string, message = '') =>
      request<any>(`/campaigns/${campaignId}/chat/approve`, {
        method: 'POST',
        body: JSON.stringify({ approval_id: approvalId, decision, message }),
      }),

    pendingApprovals: (campaignId: string) =>
      request<any>(`/campaigns/${campaignId}/chat/pending-approvals`),
  },

  expChat: {
    send: (campaignId: string, expId: string, text: string) =>
      request<any>(`/campaigns/${campaignId}/experiments/${expId}/chat/send`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      }),

    history: (campaignId: string, expId: string, limit = 100) =>
      request<any[]>(`/campaigns/${campaignId}/experiments/${expId}/chat/history?limit=${limit}`),
  },

  workspace: {
    tree: (campaignId: string, expId: string) =>
      request<{ experiment_id: string; files: any[] }>(
        `/campaigns/${campaignId}/experiments/${expId}/workspace/tree`,
      ),

    readFile: (campaignId: string, expId: string, path: string) =>
      request<{ path: string; content: string | null; binary: boolean; size: number; truncated?: boolean }>(
        `/campaigns/${campaignId}/experiments/${expId}/workspace/file?path=${encodeURIComponent(path)}`,
      ),

    writeFile: (campaignId: string, expId: string, path: string, content: string) =>
      request<{ path: string; size: number; synced_to_docker: boolean }>(
        `/campaigns/${campaignId}/experiments/${expId}/workspace/file`,
        {
          method: 'PUT',
          body: JSON.stringify({ path, content }),
        },
      ),
  },
}

export { ApiError }
