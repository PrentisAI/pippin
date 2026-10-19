/** Types of the experiment event stream (WebSocket `experiment_event` messages). */

/** Event types the engine and the web API emit (see the backend for their `data` fields). */
export type KnownEventType =
  // Campaign lifecycle
  | 'campaign_started'
  | 'phase_started'
  | 'agent_session_started'
  | 'agent_session_complete'
  | 'campaign_completed'
  | 'campaign_failed'
  | 'campaign_stopped'
  | 'run_finished'
  | 'run_error'
  | 'autonomy_changed'
  // Phase results
  | 'ideation_complete'
  | 'screening_complete'
  | 'planning_complete'
  | 'planning_fallback'
  | 'job_started'
  | 'iteration_complete'
  | 'experiment_started'
  | 'experiment_complete'
  | 'experiment_failed'
  | 'research_loop_complete'
  | 'analysis_complete'
  | 'analysis_verdict_missing'
  | 'writing_complete'
  | 'report_missing'
  | 'skill_suggestion'
  // The research agent
  | 'loop_started'
  | 'reasoning'
  | 'tool_call_start'
  | 'tool_call_result'
  | 'permission_denied'
  | 'agent_stderr'
  | 'agent_done'
  | 'verifier_started'
  | 'verifier_result'
  // Oversight
  | 'milestone_audit_started'
  | 'milestone_audit'
  | 'supervisor_continue'
  | 'supervisor_advise'
  | 'supervisor_interrupt'
  | 'supervisor_escalate'
  | 'human_gate_opened'
  | 'human_gate_resolved'
  // The researcher and the front desk
  | 'chat_message'
  | 'instruction_relayed'
  | 'instruction_queued'
  | 'ideation_progress'
  | 'agent_interrupted'
  | 'agent_reply_to_human'
  | 'frontdesk_response'
  | 'frontdesk_tool_call'
  | 'frontdesk_tool_result'
  | 'frontdesk_error'

export interface StreamEvent {
  /** One of {@link KnownEventType}; unknown types are still shown, by name. */
  event_type: KnownEventType | (string & {})
  /** Who published the event (e.g. "engine", "supervisor"). */
  source?: string
  data: Record<string, any>
  timestamp: number
  turn?: number
}
