import type { DeepRead, IdeaVersion, MemAlert, MemVault, MemVoiceTurn, MemAudit, MemCite, MemDissent, MemDigest, MemFollow, MemAsk, MemQuestion, MemReport, MemSource, MemSurface, MemUnit, WriteLevel, WriteSettings, PlanInsight, ResearchPlan, FieldMap, FieldMapSummary, LitPaper, NotePlace, ReaderAgent, ReaderNote, ReaderView, Onboarding, WizardStep, ExampleCard, HelpPage, HelpAnswer, SetupStatus, ForkPoints, RunEstimate, RunReport, RunResult, RunComparison, FeedView, FeedItem, Following, PersonCandidate, FeedIdentity, FeedPerson, ScholarPage, LibraryRef, ReportLink, OtherProject, BsAttachment, BsLink, Activity, BsCard, BsChat, BsMessage, BsSpace, CardType, Relation, ZoteroCollection, ZoteroMapping, ZoteroOptions, ZoteroStatus, AddFound, LibraryView, LitCheck, LitSearch, ProjectLiterature, ActivityEntry, AuthStatus, AuthUser, InviteInfo, KeyStatus, Person, ProjectAccess, TeamGroup, TeamRules, ClusterJobs, GpuUsage, NotificationSettings, NotifyChannel, ProjectFileEntry, ProjectFileListing, Analytics, EditableSetting, SearchHit, SuggestionOutcome,
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

/** The token of an entry point that shows a page of the console (the extension's side panel, the desktop app),
 *  given once in the address (`?token=`) and kept for the session; ordinary pages use the session cookie. */
function deviceToken(): string {
  try {
    const url = new URL(window.location.href)
    const given = url.searchParams.get('token')
    if (given) {
      sessionStorage.setItem('aurora_token', given)
      url.searchParams.delete('token')
      window.history.replaceState(null, '', url.toString())
    }
    return sessionStorage.getItem('aurora_token') || ''
  } catch {
    return ''
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = deviceToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...options,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    // signed out (or the session ended): the app shows the sign-in page
    if (res.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('aurora:signed-out'))
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

const put = (body: unknown): RequestInit => ({ method: 'PUT', body: JSON.stringify(body) })
const del: RequestInit = { method: 'DELETE' }

export const api = {
  memory: {
    capture: (body: { url?: string; text?: string; title?: string; selection?: string; via?: string }) =>
      request<MemSource>('/memory/capture', post(body)),
    sources: (limit = 50) => request<MemSource[]>(`/memory/sources?limit=${limit}`),
    source: (id: string) => request<MemSource>(`/memory/sources/${id}`),
    snapshot: (id: string) => request<{ source_id: string; title: string; url: string; text: string }>(`/memory/sources/${id}/snapshot`),
    context: (body: { app?: string; title?: string; url?: string; selection?: string; text?: string; page?: string; via?: string }) =>
      request<unknown>('/memory/context', post(body)),
    contexts: () => request<{ current: { label: string; app: string; at: number } | null; questions: MemQuestion[] }>('/memory/contexts'),
    surface: (ctx: string) => request<MemSurface>(`/memory/surface?ctx=${encodeURIComponent(ctx)}`),
    search: (q: string) => request<{ unit_id: string; claim: string; sunk: boolean; score: number; sources: number }[]>(`/memory/search?q=${encodeURIComponent(q)}`),
    unit: (id: string) => request<MemUnit>(`/memory/units/${id}`),
    editUnit: (id: string, body: { claim?: string; gist?: string }) =>
      request<MemUnit>(`/memory/units/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    changeUnit: (id: string, body: { claim?: string; gist?: string }) => request<MemUnit>(`/memory/units/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    mark: (id: string, kind: 'sunk' | 'raised' | 'moved' | 'unmoved') => request<MemUnit>(`/memory/units/${id}/mark`, post({ kind })),
    split: (evidenceId: string) => request<MemUnit>(`/memory/evidence/${evidenceId}/split`, post({})),
    merge: (id: string, into: string) => request<MemUnit>(`/memory/units/${id}/merge`, post({ into })),
    ask: (ctx: string) => request<MemAsk>('/memory/ask', post({ ctx })),
    answer: (askId: string, text: string) => request<MemAsk>(`/memory/asks/${askId}/answer`, post({ text })),
    questions: () => request<MemQuestion[]>('/memory/questions'),
    addQuestion: (text: string) => request<MemQuestion>('/memory/questions', post({ text })),
    archiveQuestion: (id: string) => request<{ ok: boolean }>(`/memory/questions/${id}`, del),
    synthesis: (id: string) => request<{ question: string; text: string; map: { map_id: string; topic: string } | null; units: { ref: string; unit_id: string; claim: string }[] }>(`/memory/questions/${id}/synthesis`),
    report: (days = 7) => request<MemReport>(`/memory/report?days=${days}`),
    settings: () => request<{ language: string }>('/memory/settings'),
    setSettings: (language: string) => request<{ language: string }>('/memory/settings', put({ language })),
    captureFile: async (file: File, via = 'orb') => request<MemSource>(`/memory/capture/file?name=${encodeURIComponent(file.name)}&via=${via}`,
      { method: 'POST', body: await file.arrayBuffer(), headers: { 'Content-Type': 'application/octet-stream' } }),
    digest: (hours = 24) => request<MemDigest>(`/memory/digest?hours=${hours}`),
    follows: () => request<MemFollow[]>('/memory/follows'),
    addFollow: (url: string, label = '', every_min = 60) => request<MemFollow>('/memory/follows', post({ url, label, every_min })),
    changeFollow: (id: string, body: { paused?: boolean; every_min?: number; label?: string }) =>
      request<MemFollow>(`/memory/follows/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteFollow: (id: string) => request<{ ok: boolean }>(`/memory/follows/${id}`, del),
    dissent: (paper: string, space = '') => request<MemDissent>(`/memory/dissent?paper=${encodeURIComponent(paper)}&space=${encodeURIComponent(space)}`),
    cite: (sentence: string, context = '') => request<MemCite>('/memory/cite', post({ sentence, context })),
    audit: (tex: string, bib: string, title = '') => request<MemAudit>('/memory/audit', post({ tex, bib, title })),
    auditPdf: (pdf: string, title = '') => request<MemAudit>('/memory/audit', post({ pdf, title })),
    audits: () => request<MemAudit[]>('/memory/audits'),
    getAudit: (id: string) => request<MemAudit>(`/memory/audits/${id}`),
    alerts: (unseen = false) => request<MemAlert[]>(`/memory/alerts?unseen=${unseen}`),
    alertSeen: (id: string) => request<MemAlert>(`/memory/alerts/${id}/seen`, post({})),
    alertVerdict: (id: string, worth: boolean) => request<MemAlert>(`/memory/alerts/${id}/verdict`, post({ worth })),
    checkRetractions: () => request<{ checked: number; retracted: MemAlert[] }>('/memory/alerts/check', post({})),
    forget: (sourceId: string) => request<{ gone_units: string[] }>(`/memory/sources/${sourceId}`, del),
    vault: () => request<MemVault>('/memory/vault'),
    setVault: (body: { path: string; folders: string[]; exclude: string[]; on: boolean }) => request<MemVault>('/memory/vault', put(body)),
    scanVault: () => request<{ read: number; removed: number }>('/memory/vault/scan', post({})),
    voiceTurns: (limit = 5) => request<MemVoiceTurn[]>(`/memory/voice/turns?limit=${limit}`),
    voiceUndo: (id: string) => request<{ undone: boolean }>(`/memory/voice/undo/${id}`, post({})),
    voice: (text: string) => request<MemVoiceTurn>('/memory/voice', post({ text })),
    toBrainstorm: (id: string, space = '', card_id = '', rel = 'supports') =>
      request<{ card_id: string }>(`/memory/units/${id}/to-brainstorm`, post({ space, card_id, rel })),
    worth: () => request<{ paper_id: string; title: string; byline: string; why: string; from: string; to_read: boolean }[]>('/memory/digest/worth'),
    toRead: (paper_id: string) => request<{ added: boolean }>('/memory/to-read', post({ paper_id })),
    compare: (a: { unit_id?: string; source_id?: string; paper_id?: string }, b: { unit_id?: string; source_id?: string; paper_id?: string }) =>
      request<{ short: string; text: string; a: string; b: string }>('/memory/compare', post({ a, b })),
    unitMarkdown: (id: string) => request<{ markdown: string }>(`/memory/units/${id}/markdown`),
  },
  auth: {
    status: () => request<AuthStatus>('/auth/status'),
    login: (email: string, password: string) => request<{ user: AuthUser }>('/auth/login', post({ email, password })),
    logout: () => request<{ ok: boolean }>('/auth/logout', post()),
    invite: (token: string) => request<InviteInfo>(`/auth/invites/${token}`),
    accept: (token: string, name: string, password: string) =>
      request<{ user: AuthUser; campaign_id: string | null }>(`/auth/invites/${token}/accept`, post({ name, password })),
  },

  me: {
    get: () => request<{ user: AuthUser; team: string | null; accounts: boolean }>('/me'),
    update: (body: { name?: string; password?: string; current_password?: string }) => request<{ user: AuthUser }>('/me', put(body)),
    keys: () => request<{ keys: KeyStatus[]; uses: string; team_key_allowed?: boolean; note?: string }>('/me/keys'),
    setKey: (provider: string, value: string) => request<KeyStatus>(`/me/keys/${provider}`, put({ value })),
    deleteKey: (provider: string) => request<KeyStatus>(`/me/keys/${provider}`, del),
    writes: () => request<WriteSettings>('/me/writes'),
    undoWrite: (id: string) => request<{ undone: boolean; how?: string }>(`/me/writes/${id}/undo`, post({})),
    devices: () => request<{ device_id: string; name: string; created_at: number; last_used: number | null }[]>('/me/devices'),
    connectDevice: (name: string) => request<{ token: string; device: { device_id: string; name: string } }>('/me/devices', post({ name })),
    revokeDevice: (id: string) => request<{ ok: boolean }>(`/me/devices/${id}`, del),
    setWrites: (levels: Record<string, WriteLevel>) => request<WriteSettings>('/me/writes', put({ levels })),
  },

  team: {
    get: () => request<{ name: string; accounts: boolean; me: AuthUser; people: number }>('/team'),
    rename: (name: string) => request<{ name: string }>('/team', put({ name })),
    people: () => request<Person[]>('/team/people'),
    setRole: (userId: string, team_role: string) => request<Person>(`/team/people/${userId}`, put({ team_role })),
    remove: (userId: string) => request<{ ok: boolean; note: string }>(`/team/people/${userId}`, del),
    invites: () => request<{ invite_id: string; email: string; team_role: string; campaign_id: string | null; expires_at: number }[]>('/team/invites'),
    invite: (email: string, team_role: string) => request<{ invite_id: string; link: string }>('/team/invites', post({ email, team_role })),
    revokeInvite: (id: string) => request<{ ok: boolean }>(`/team/invites/${id}`, del),
    groups: () => request<TeamGroup[]>('/team/groups'),
    addGroup: (name: string, members: string[]) => request<TeamGroup>('/team/groups', post({ name, members })),
    updateGroup: (id: string, body: { name?: string; members?: string[] }) => request<TeamGroup>(`/team/groups/${id}`, put(body)),
    deleteGroup: (id: string) => request<{ ok: boolean }>(`/team/groups/${id}`, del),
    rules: () => request<TeamRules>('/team/rules'),
    setRules: (changes: Partial<TeamRules>) => request<TeamRules>('/team/rules', put(changes)),
    keys: () => request<{ team: KeyStatus[]; server_key?: boolean; people: { user_id: string; name: string; own_key: boolean }[] }>('/team/keys'),
    setKey: (provider: string, value: string) => request<KeyStatus>(`/team/keys/${provider}`, put({ value })),
    deleteKey: (provider: string) => request<KeyStatus>(`/team/keys/${provider}`, del),
    activity: () => request<ActivityEntry[]>('/team/activity'),
    transfer: (campaign_id: string, to_user_id: string) => request<{ ok: boolean }>('/team/transfer', post({ campaign_id, to_user_id })),
  },

  access: {
    get: (cid: string) => request<ProjectAccess>(`/campaigns/${cid}/access`),
    set: (cid: string, body: Partial<Pick<ProjectAccess, 'team_access' | 'confidential' | 'deciders' | 'decider_user'>>) =>
      request<ProjectAccess>(`/campaigns/${cid}/access`, put(body)),
    setMember: (cid: string, principal: string, role: string, perms?: string[]) =>
      request<ProjectAccess>(`/campaigns/${cid}/access/members/${encodeURIComponent(principal)}`, put({ role, perms })),
    removeMember: (cid: string, principal: string) =>
      request<ProjectAccess>(`/campaigns/${cid}/access/members/${encodeURIComponent(principal)}`, del),
    invite: (cid: string, email: string, role: string, perms?: string[]) =>
      request<{ added: boolean; link?: string; team_role?: string; access: ProjectAccess }>(`/campaigns/${cid}/access/invites`, post({ email, role, perms })),
    cancelInvite: (cid: string, id: string) => request<ProjectAccess>(`/campaigns/${cid}/access/invites/${id}`, del),
  },

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
    saveNotifications: (body: { console_url: string; budget_warning_at: number; channels: NotifyChannel[]; quiet_start?: string; quiet_end?: string; quiet_except?: string[]; action_links?: boolean }) =>
      request<NotificationSettings>('/settings/notifications', { method: 'PUT', body: JSON.stringify(body) }),
    testNotification: (channel: NotifyChannel) =>
      request<{ ok: boolean; error: string }>('/settings/notifications/test', { method: 'POST', body: JSON.stringify(channel) }),
  },

  compute: {
    jobs: (campaignId: string, expId: string) =>
      request<ClusterJobs>(`/campaigns/${campaignId}/experiments/${expId}/compute`),
    gpus: (campaignId: string, expId: string) =>
      request<GpuUsage>(`/campaigns/${campaignId}/experiments/${expId}/compute/gpus`),
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

  literature: {
    get: (cid: string) => request<ProjectLiterature>(`/campaigns/${cid}/literature`),
    searchDetail: (cid: string, sid: string) => request<LitSearch>(`/campaigns/${cid}/literature/searches/${sid}`),
    search: (cid: string, query: string) => request<LitSearch>(`/campaigns/${cid}/literature/search`, post({ query })),
    add: (cid: string, body: { text?: string; title?: string; authors?: string; year?: number; doi?: string; arxiv_id?: string }) =>
      request<{ check: LitCheck; added: boolean; key: string; literature: ProjectLiterature }>(`/campaigns/${cid}/literature/papers`, post(body)),
    addKnown: (cid: string, paper_ids: string[], search_id = '') =>
      request<{ added: number; literature: ProjectLiterature }>(`/campaigns/${cid}/literature/papers/add`, post({ paper_ids, search_id })),
    star: (cid: string, pid: string, starred: boolean) => request<ProjectLiterature>(`/campaigns/${cid}/literature/papers/${pid}`, put({ starred })),
    remove: (cid: string, pid: string) => request<ProjectLiterature>(`/campaigns/${cid}/literature/papers/${pid}`, del),
    decide: (cid: string, refId: string, action: 'replace' | 'remove' | 'have' | 'reopen', paper_id = '') =>
      request<ProjectLiterature>(`/campaigns/${cid}/literature/refs/${refId}`, post({ action, paper_id })),
    /** What the citations in a text point to. */
    resolve: (cid: string, keys: string[], refs: string[]) =>
      request<{ keys: Record<string, import('../components/Cite').CiteInfo>; refs: Record<string, import('../components/Cite').CiteInfo> }>(
        `/campaigns/${cid}/literature/resolve`, post({ keys, refs })),
    recheck: (cid: string, refId: string) =>
      request<{ status: string; literature: ProjectLiterature }>(`/campaigns/${cid}/literature/refs/${refId}/recheck`, post()),
    importBib: (cid: string, bibtex: string) =>
      request<{ entries: { cite_key: string; status: string; reason: string; key: string; title: string }[]; added: number; literature: ProjectLiterature }>(
        `/campaigns/${cid}/literature/import`, post({ bibtex })),
    exportUrl: (cid: string) => `${BASE_URL}/campaigns/${cid}/literature/export.bib`,
    exportText: async (cid: string) => (await fetch(`${BASE_URL}/campaigns/${cid}/literature/export.bib`)).text(),
    addMany: (cid: string, text: string) =>
      request<{ entries: { cite_key: string; status: string; reason: string; key: string; title: string }[]; added: number; literature: ProjectLiterature }>(
        `/campaigns/${cid}/literature/papers/many`, post({ text })),
    upload: async (cid: string, file: File) => {
      const res = await fetch(`${BASE_URL}/campaigns/${cid}/literature/upload?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file })
      if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, res.statusText, await res.text().catch(() => '')))
      return res.json() as Promise<{ added: boolean; key: string; file: string; check: LitCheck; literature: ProjectLiterature }>
    },
    setRule: (cid: string, add_found: AddFound | '') => request<ProjectLiterature>(`/campaigns/${cid}/literature/rule`, put({ add_found })),
    citeVersion: (cid: string, pid: string, version = 0) => request<ProjectLiterature>(`/campaigns/${cid}/literature/papers/${pid}/version`, put({ version })),
    fileUrl: (cid: string, path: string) => `${BASE_URL}/campaigns/${cid}/files/raw?path=${encodeURIComponent(path)}`,
  },
  reader: {
    get: (paper: string, q: { space?: string; card_id?: string; chat_id?: string }) =>
      request<ReaderView>(`/reader/papers/${encodeURIComponent(paper)}?${new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][])}`),
    pdfUrl: (paper: string, space: string) => `${BASE_URL}/reader/papers/${encodeURIComponent(paper)}/pdf?space=${encodeURIComponent(space)}`,
    addNote: (paper: string, body: { space: string; card_id?: string; chat_id?: string; text: string; comment?: string; draft?: boolean; ask?: ReaderAgent } & NotePlace) =>
      request<ReaderNote>(`/reader/papers/${encodeURIComponent(paper)}/notes`, post(body)),
    change: (annId: string, body: { comment?: string; keep?: boolean; shared?: boolean; place?: NotePlace }) =>
      request<ReaderNote>(`/reader/notes/${annId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    remove: (annId: string) => request<{ deleted: boolean; in_zotero: boolean }>(`/reader/notes/${annId}`, { method: 'DELETE' }),
    restore: (annId: string) => request<ReaderNote>(`/reader/notes/${annId}/restore`, post()),
    reply: (annId: string, text: string) => request<ReaderNote>(`/reader/notes/${annId}/replies`, post({ text })),
    readAlong: (paper: string, body: { space: string; card_id?: string; chat_id?: string }) =>
      request<{ started: boolean; why?: string }>(`/reader/papers/${encodeURIComponent(paper)}/read-along`, post(body)),
    settings: (agents_mark: boolean) => request<{ agents_mark: boolean }>('/reader/settings', { method: 'PUT', body: JSON.stringify({ agents_mark }) }),
    toZotero: (paper: string, space: string) => request<ReaderView['zotero']>(`/reader/papers/${encodeURIComponent(paper)}/zotero`, post({ space })),
    sync: () => request<{ ok: boolean }>('/reader/sync', post()),
  },
  review: {
    maps: (space: string) => request<FieldMapSummary[]>(`/review/spaces/${encodeURIComponent(space)}/maps`),
    pane: (space: string) => request<FieldMap | null>(`/review/spaces/${encodeURIComponent(space)}/pane`),
    newMap: (space: string, body: { topic: string; brief?: string; seeds?: string[]; size?: number }) =>
      request<FieldMap>(`/review/spaces/${encodeURIComponent(space)}/maps`, post(body)),
    map: (id: string) => request<FieldMap>(`/review/maps/${id}`),
    change: (id: string, body: { topic?: string; shared?: boolean }) => request<FieldMap>(`/review/maps/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    remove: (id: string) => request<{ deleted: boolean }>(`/review/maps/${id}`, { method: 'DELETE' }),
    run: (id: string, start = '') => request<FieldMap>(`/review/maps/${id}/run`, post({ start })),
    accept: (id: string, proposal: string) => request<FieldMap>(`/review/maps/${id}/proposals/${proposal}/accept`, post()),
    dismiss: (id: string, proposal: string) => request<FieldMap>(`/review/maps/${id}/proposals/${proposal}/dismiss`, post()),
    choose: (id: string, framework: string) => request<FieldMap>(`/review/maps/${id}/framework`, post({ framework })),
    replace: (id: string, paperId: string, note: string) => request<FieldMap>(`/review/maps/${id}/papers/${paperId}/replace`, post({ note })),
    challenge: (id: string, text: string) => request<FieldMap>(`/review/maps/${id}/challenges`, post({ text })),
    apply: (id: string, challenge: string, option: string) => request<FieldMap>(`/review/maps/${id}/challenges/${challenge}/apply`, post({ option })),
    checkNew: (id: string) => request<FieldMap>(`/review/maps/${id}/check-new`, post()),
    version: (id: string, v: number) => request<FieldMap['content']>(`/review/maps/${id}/versions/${v}`),
    toBrainstorm: (id: string, problem: string) => request<{ card_id: string; links: number }>(`/review/maps/${id}/problems/${problem}/brainstorm`, post()),
    openRead: (body: { paper: string; space: string; map_id?: string; start?: boolean }) => request<DeepRead>('/review/reads', post(body)),
    findRead: (paper: string, space: string) => request<DeepRead | null>(`/review/reads?paper=${encodeURIComponent(paper)}&space=${encodeURIComponent(space)}`),
    read: (id: string) => request<DeepRead>(`/review/reads/${id}`),
    changeRead: (id: string, body: { guess?: string; revealed?: boolean; ticks?: number[]; question?: number; answer?: string; show?: boolean }) =>
      request<DeepRead>(`/review/reads/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    again: (id: string, questions: number[]) => request<DeepRead>(`/review/reads/${id}/again`, post({ questions })),
    followUp: (id: string, question: number, text: string) => request<DeepRead>(`/review/reads/${id}/followups`, post({ question, text })),
    ideaToBrainstorm: (id: string) => request<{ card_id: string; links: number }>(`/review/reads/${id}/idea`, post()),
  },
  brainstorm: {
    spaces: () => request<{ space: string; name: string; project: boolean; can_write: boolean }[]>('/brainstorm/spaces'),
    space: (space: string) => request<BsSpace>(`/brainstorm/spaces/${encodeURIComponent(space)}`),
    newChat: (space: string, title = '') => request<BsChat>(`/brainstorm/spaces/${encodeURIComponent(space)}/chats`, post({ title })),
    chat: (chatId: string) => request<{ chat: BsChat; messages: BsMessage[]; pending: boolean; here: { user_id: string; name: string }[] }>(`/brainstorm/chats/${chatId}`),
    renameChat: (chatId: string, title: string) => request<BsChat>(`/brainstorm/chats/${chatId}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
    deleteChat: (chatId: string) => request<{ ok: boolean }>(`/brainstorm/chats/${chatId}`, del),
    say: (chatId: string, text: string, focus = '', attachments: BsAttachment[] = []) =>
      request<{ message: BsMessage; agents: string[] }>(`/brainstorm/chats/${chatId}/messages`, post({ text, focus, attachments })),
    deep: (chatId: string, text: string, focus = '') =>
      request<{ message: BsMessage; agents: string[] }>(`/brainstorm/chats/${chatId}/deep`, post({ text, focus })),
    thread: (cardId: string) => request<{ card: BsCard; messages: BsMessage[]; pending: boolean; links: BsLink[] }>(`/brainstorm/cards/${cardId}/thread`),
    reply: (cardId: string, text: string) => request<{ message: BsMessage; agents: string[] }>(`/brainstorm/cards/${cardId}/thread`, post({ text })),
    link: (cardId: string, to: string, rel: Relation) => request<{ ok: boolean }>(`/brainstorm/cards/${cardId}/links`, post({ to, rel })),
    unlink: (cardId: string, to: string, rel = '') => request<{ ok: boolean }>(`/brainstorm/cards/${cardId}/links/${to}?rel=${encodeURIComponent(rel)}`, del),
    move: (cardId: string, body: { campaign_id?: string; new_project?: string }) =>
      request<{ campaign_id: string; moved: number }>(`/brainstorm/cards/${cardId}/move`, post(body)),
    doSuggestion: (id: string) => request<{ kind: string; cards: string[]; added: string }>(`/brainstorm/suggestions/${id}/do`, post()),
    dismissSuggestion: (id: string) => request<{ ok: boolean }>(`/brainstorm/suggestions/${id}/dismiss`, post()),
    ask: (chatId: string, agent: string, card_id: string) =>
      request<{ message: BsMessage; agents: string[] }>(`/brainstorm/chats/${chatId}/ask`, post({ agent, card_id })),
    share: (chatId: string, what: 'all' | 'ideas', card_ids: string[]) =>
      request<{ chat: BsChat; shared_cards: number }>(`/brainstorm/chats/${chatId}/share`, post({ what, card_ids })),
    consent: (msgId: string, share: boolean) => request<{ ok: boolean }>(`/brainstorm/messages/${msgId}/consent`, post({ share })),
    act: (msgId: string, choice: string, text?: string) => request<BsMessage>(`/brainstorm/messages/${msgId}/act`, post({ choice, text: text ?? '' })),
    versions: (cardId: string) => request<IdeaVersion[]>(`/brainstorm/cards/${cardId}/versions`),
    plan: (chatId: string) => request<ResearchPlan>(`/brainstorm/chats/${chatId}/plan`),
    changeInsight: (id: string, body: { text?: string; section?: string }) => request<PlanInsight>(`/brainstorm/insights/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteInsight: (id: string) => request<{ ok: boolean }>(`/brainstorm/insights/${id}`, del),
    addCard: (space: string, body: { type?: CardType; text: string; paper_id?: string; links?: { to: string; rel: Relation }[]; vis?: string; chat_id?: string; ask_skeptic?: boolean }) =>
      request<{ card: BsCard; chat_id?: string }>(`/brainstorm/spaces/${encodeURIComponent(space)}/cards`, post(body)),
    changeCard: (cardId: string, body: { text?: string; answer?: string; vis?: 'private' | 'project' }) =>
      request<BsCard>(`/brainstorm/cards/${cardId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    deleteCard: (cardId: string) => request<{ ok: boolean }>(`/brainstorm/cards/${cardId}`, del),
    promote: (cardId: string, body: { ideation: 'start' | 'roundtable' | 'brainstorm'; add_found?: string; campaign_id?: string }) =>
      request<{ proposal_id: string; campaign_id: string }>(`/brainstorm/cards/${cardId}/promote`, post(body)),
    setActivity: (activity: Activity) => request<{ activity: Activity }>('/brainstorm/settings', put({ activity })),
  },
  feed: {
    get: () => request<FeedView>('/feed'),
    refresh: () => request<{ refreshing: boolean; started: boolean }>('/feed/refresh', post()),
    saved: () => request<{ items: FeedItem[] }>('/feed/saved'),
    attachable: () => request<{ items: LibraryRef[] }>('/feed/library'),
    save: (itemId: string, campaign_id = '') =>
      request<{ saved: { campaign_id: string; project: string }; in_literature: boolean }>(`/feed/items/${itemId}/save`, post({ campaign_id })),
    unsave: (itemId: string) => request<{ ok: boolean }>(`/feed/items/${itemId}/save`, del),
    hide: (itemId: string) => request<{ ok: boolean }>(`/feed/items/${itemId}/hide`, post()),
    unhide: (itemId: string) => request<{ ok: boolean }>(`/feed/items/${itemId}/hide`, del),
    bring: (itemId: string, space: string) =>
      request<{ card: BsCard; space: string; space_name: string }>(`/feed/items/${itemId}/bring`, post({ space })),
    ask: (itemId: string, campaign_id: string) =>
      request<{ campaign_id: string; ref: LibraryRef; seed: boolean }>(`/feed/items/${itemId}/ask`, post({ campaign_id })),
    following: () => request<Following>('/feed/following'),
    settings: (body: Partial<Pick<Following, 'topics' | 'keywords' | 'from_objectives' | 'hf' | 'discussions'>> & { digest?: Partial<{ to: string; at: string; top: number; skip: boolean }> }) =>
      request<Following>('/feed/settings', put(body)),
    addRss: (url: string) => request<Following>('/feed/rss', post({ url })),
    removeRss: (url: string) => request<Following>(`/feed/rss?url=${encodeURIComponent(url)}`, del),
    setXKey: (key: string) => request<Following>('/feed/x-key', put({ key })),
    removeXKey: () => request<Following>('/feed/x-key', del),
    testDigest: () => request<{ ok: boolean }>('/feed/digest/test', post()),
    find: (query: string) => request<{ candidates: PersonCandidate[] }>('/feed/people/find', post({ query })),
    identities: (candidate: PersonCandidate) => request<{ ids: FeedIdentity[] }>('/feed/people/identities', post({ candidate })),
    follow: (candidate: PersonCandidate, ids: FeedIdentity[]) => request<{ person: FeedPerson }>('/feed/people', post({ candidate, ids })),
    changePerson: (personId: string, body: { wants?: Partial<FeedPerson['wants']>; ids?: FeedIdentity[]; removed?: boolean }) =>
      request<{ person: FeedPerson }>(`/feed/people/${personId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    unfollow: (personId: string) => request<{ ok: boolean }>(`/feed/people/${personId}`, del),
    person: (personId: string) => request<ScholarPage>(`/feed/people/${personId}`),
  },
  links: {
    get: (campaignId: string) => request<ReportLink>(`/campaigns/${campaignId}/link`),
    make: (campaignId: string, days: number) => request<ReportLink>(`/campaigns/${campaignId}/link`, post({ days })),
    revoke: (campaignId: string) => request<ReportLink>(`/campaigns/${campaignId}/link`, del),
  },
  others: () => request<OtherProject[]>('/campaigns/others'),
  setup: {
    get: () => request<SetupStatus>('/setup'),
    dismiss: () => request<{ ok: boolean }>('/setup/dismiss', post()),
    setKey: (key: string) => request<SetupStatus>('/setup/key', put({ key })),
    pull: () => request<SetupStatus>('/setup/pull', post()),
    demo: () => request<{ campaign_id: string; experiment_id: string }>('/demo', post()),
  },
  onboarding: {
    get: () => request<Onboarding>('/onboarding'),
    update: (body: Partial<{ step: WizardStep; done: boolean; field: string; tour_done: string; tip_seen: string;
      tips_reset: boolean; traced: boolean; examples_hidden: boolean }>) => request<Onboarding>('/onboarding', put(body)),
    defaults: (body: { autonomy: 'supervised' | 'autonomous'; max_cost_usd: number | null }) =>
      request<Onboarding>('/onboarding/defaults', put(body)),
  },
  examples: {
    list: () => request<{ examples: ExampleCard[]; hidden: boolean }>('/examples'),
    duplicate: (id: string) => request<{ campaign_id: string; name: string; brief: string }>(`/examples/${id}/duplicate`, post()),
    install: (id: string) => request<{ campaign_id: string }>(`/examples/${id}/install`, post()),
    remove: (id: string) => request<{ ok: boolean }>(`/examples/${id}`, del),
  },
  paper: (ident: string) => request<LitPaper>(`/library/papers/${encodeURIComponent(ident)}`),
  help: {
    pages: () => request<{ pages: HelpPage[] }>('/help/docs'),
    page: (path: string) => request<{ path: string; title: string; markdown: string }>(`/help/docs/${path}`),
    ask: (question: string) => request<HelpAnswer>('/help/ask', post({ question })),
  },
  results: {
    report: (cid: string, exp: string, version?: number) =>
      request<RunReport>(`/campaigns/${cid}/experiments/${exp}/report${version ? `?version=${version}` : ''}`),
    list: (cid: string, exp: string) => request<{ results: RunResult[] }>(`/campaigns/${cid}/experiments/${exp}/results`),
    exportUrl: (cid: string, exp: string, what: 'report' | 'workspace') => `${BASE_URL}/campaigns/${cid}/experiments/${exp}/export?what=${what}`,
    reproduceUrl: (cid: string, exp: string) => `${BASE_URL}/campaigns/${cid}/experiments/${exp}/reproduce.sh`,
    overleaf: (cid: string, exp: string) =>
      request<{ url: string; token: { set: boolean; hint: string } }>(`/campaigns/${cid}/experiments/${exp}/overleaf`),
    pushOverleaf: (cid: string, exp: string, body: { url?: string; token?: string; folder?: string }) =>
      request<{ ok: boolean; message: string; url: string; token: { set: boolean; hint: string } }>(
        `/campaigns/${cid}/experiments/${exp}/overleaf`, post(body)),
    compare: (cid: string, runs: string[]) => request<RunComparison>(`/campaigns/${cid}/compare?runs=${runs.join(',')}`),
    forkpoints: (cid: string, exp: string) => request<ForkPoints>(`/campaigns/${cid}/experiments/${exp}/forkpoints`),
    fork: (cid: string, exp: string, body: { point: string; name?: string; guidance?: string; direction?: string; models?: Record<string, string>; max_cost_usd?: number | null }) =>
      request<{ experiment_id: string; forked_from: { run_id: string; point: string; inherited_cost_usd: number } }>(
        `/campaigns/${cid}/experiments/${exp}/fork`, post(body)),
    estimate: (cid: string, body: { skip_phases: string[]; ideation_mode?: string; models?: Record<string, string>; model?: string; max_parallel_jobs?: number }) =>
      request<RunEstimate>(`/campaigns/${cid}/estimate`, post(body)),
  },
  zotero: {
    get: () => request<ZoteroStatus>('/zotero'),
    connect: (method: 'web' | 'local', api_key = '') => request<ZoteroStatus>('/zotero/connect', post({ method, api_key })),
    collections: () => request<{ collections: ZoteroCollection[]; projects: { campaign_id: string; name: string }[]; suggested: boolean }>('/zotero/collections'),
    save: (body: { mappings?: ZoteroMapping[]; options?: Partial<ZoteroOptions> }) => request<ZoteroStatus>('/zotero', put(body)),
    sync: (campaignId = '', soon = false) =>
      request<ZoteroStatus>(`/zotero/sync?campaign_id=${encodeURIComponent(campaignId)}${soon ? '&soon=true' : ''}`, post()),
    disconnect: () => request<ZoteroStatus>('/zotero', del),
    push: (campaignId: string, paper_ids: string[] = []) =>
      request<{ pushed: number; problems: string[]; zotero: ZoteroStatus }>(`/zotero/push/${campaignId || 'library'}`, post({ paper_ids })),
  },
  library: {
    get: () => request<LibraryView>('/library'),
    search: (query: string) => request<LitSearch>('/library/search', post({ query })),
    add: (body: { text?: string; title?: string; doi?: string; arxiv_id?: string }) =>
      request<{ check: LitCheck; added: boolean; key: string }>('/library/papers', post(body)),
    save: (paper_id: string, campaign_id = '') => request<{ added: boolean }>('/library/save', post({ paper_id, campaign_id })),
    remove: (paper_id: string) => request<{ ok: boolean }>(`/library/papers/${paper_id}`, del),
  },
  proposals: {
    confirm: (campaignId: string, proposalId: string, body: {
      add_found?: AddFound
      ideation_live?: boolean
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
      estimate?: { cost: [number, number]; hours: [number, number] }
    }) =>
      request<{ status: string; experiment_id: string; skip_phases: string[]; active_phases: string[] }>(
        `/campaigns/${campaignId}/proposals/${proposalId}/confirm`,
        post(body),
      ),
  },

  skills: {
    list: () => request<Skill[]>('/skills/'),
    pending: () => request<Skill[]>('/skills/pending'),
    decide: (skillId: string, approve: boolean) => request<Skill>(`/skills/${skillId}/approval`, post({ approve })),
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
    send: (campaignId: string, text: string, attachments: string[] = [], library: LibraryRef[] = []) =>
      request<{ status: string; response?: string; error?: string; proposal?: ConversationMessage['proposal'] }>(
        `/campaigns/${campaignId}/chat/send`,
        post({ text, attachments, library: library.map((r) => r.kind === 'feed' ? { kind: 'feed', item_id: r.item_id } : { kind: 'paper', paper_id: r.paper_id }) }),
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
