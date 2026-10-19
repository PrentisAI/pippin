import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CampaignSummary } from '../types'

/** Where agents may use a skill. */
export type SkillScope = 'experiment' | 'campaign' | 'projects' | 'global'

export interface ScopeValue {
  scope: SkillScope
  /** For scope "projects": the projects that may use it. */
  projects: string[]
}

let projectsRequest: Promise<CampaignSummary[]> | null = null

/** All projects, fetched once per page. */
function useProjects(): CampaignSummary[] {
  const [projects, setProjects] = useState<CampaignSummary[]>([])
  useEffect(() => {
    projectsRequest ??= api.campaigns.list().catch(() => [])
    let live = true
    projectsRequest.then((p) => { if (live) setProjects(p) })
    return () => { live = false }
  }, [])
  return projects
}

/**
 * Choose where a skill may be used: one run, one project, selected projects, or all projects.
 * `run` and `project` are the run and project it would be tied to (options without them are hidden).
 */
export function ScopePicker({ value, onChange, run, project, idPrefix }: {
  value: ScopeValue
  onChange: (value: ScopeValue) => void
  run?: string
  project?: string
  idPrefix: string
}) {
  const projects = useProjects()
  const name = (id: string) => projects.find((p) => p.campaign_id === id)?.name || id
  const option = (scope: SkillScope, label: string) => (
    <label key={scope}>
      <input type="radio" name={`${idPrefix}-scope`} checked={value.scope === scope}
        onChange={() => onChange({ scope, projects: scope === 'projects' && !value.projects.length && project ? [project] : value.projects })} />
      {label}
    </label>
  )
  const toggle = (id: string) => onChange({
    scope: 'projects',
    projects: value.projects.includes(id) ? value.projects.filter((x) => x !== id) : [...value.projects, id],
  })
  return (
    <div className="scope-picker">
      <span className="scope-title">Use it in:</span>
      {run && option('experiment', 'this run only')}
      {project && option('campaign', `this project (${name(project)})`)}
      {option('projects', 'selected projects')}
      {option('global', 'all projects')}
      {value.scope === 'projects' && (
        <div className="scope-projects">
          {projects.length === 0 && <span className="scope-hint">no projects yet</span>}
          {projects.map((p) => (
            <label key={p.campaign_id}>
              <input type="checkbox" checked={value.projects.includes(p.campaign_id)} onChange={() => toggle(p.campaign_id)} />
              {p.name || p.campaign_id}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
