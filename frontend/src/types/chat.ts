/** Types for the real-time agent chat and stream system. */

export interface ChatMessage {
  role: 'user' | 'agent' | 'system'
  text: string
  timestamp: number
  metadata: Record<string, any>
}

export interface StreamEvent {
  event_type:
    | 'loop_started'
    | 'turn_start'
    | 'reasoning'
    | 'reasoning_delta'
    | 'tool_call_start'
    | 'tool_call_result'
    | 'tool_progress'
    | 'agent_done'
    | 'user_message_injected'
    | 'error'
    | 'loop_finished'
    // Duplex (Cerebellum-Brain) events
    | 'duplex_started'
    | 'duplex_finished'
    | 'cerebellum_response'
    | 'brain_task_started'
    | 'brain_progress'
    | 'brain_reasoning'
    | 'brain_reasoning_delta'
    | 'brain_tool_call_start'
    | 'brain_tool_call_result'
    | 'brain_agent_done'
    | 'brain_loop_finished'
    | 'user_message_received'
    // LHTB events
    | 'phase_started'
    | 'phase_ended'
    | 'verifier_started'
    | 'verifier_result'
    | 'docker_started'
    | 'run_finished'
    | 'run_error'
    | 'watchdog_interrupt'
    | 'watchdog_cycle_start'
    | 'watchdog_verdict'
    | 'watchdog_tool_call'
    | 'watchdog_escalation'
    | 'watchdog_checkpoint'
    | 'escalation'
    | 'escalation_resolved'
    | 'autonomy_changed'
    | 'agent_stderr'
    | 'workspace_extracted'
    // Engine pipeline events
    | 'campaign_started'
    | 'campaign_completed'
    | 'campaign_failed'
    | 'iteration_started'
    | 'iteration_complete'
    | 'screening_complete'
    | 'planning_complete'
    | 'analysis_complete'
    | 'writing_complete'
    // Front Desk events
    | 'frontdesk_response'
    | 'frontdesk_tool_call'
    | 'frontdesk_tool_result'
    | 'frontdesk_error'
  data: Record<string, any>
  timestamp: number
  turn?: number
}

export interface ChatStatus {
  campaign_id: string
  has_chat_handler: boolean
  agent_active: boolean
  campaign_running: boolean
  campaign_paused: boolean
  pending_messages: number
}

export interface SendMessageResult {
  status: 'queued'
  queue_size: number
  active: boolean
}
