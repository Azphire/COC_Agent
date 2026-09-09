import type { KnowledgeBinding } from './knowledge'

export type AgentProfileInput = {
  role: 'keeper' | 'investigator'; name: string; background: string; personality: string;
  goals: string; speaking_style: string; action_tendency: string; model_preset: 'default';
}
export type AgentProfile = AgentProfileInput & { id: string; created_at: string; updated_at: string }
export type Check = {
  id: string; target_member_id: string; name: string; kind: string; value: number;
  difficulty: 'regular' | 'hard' | 'extreme'; bonus_dice: number; penalty_dice: number;
  reason: string; visibility: string; status: 'pending' | 'resolved' | 'cancelled';
  dice: { units: number; tens: number[]; candidates: number[]; selected: number } | null;
  result: { total: number; threshold: number; level: string; passed: boolean; outcome: string } | null;
}
export type GameState = {
  knowledge?: KnowledgeBinding;
  enabled: boolean;
  module: { id: string; title: string; public_introduction: string; scene: { id: string; title: string; public_description: string }; clues: { id: string; title: string; content: string }[]; completed: boolean } | null;
  cycle: { id: string; status: string; current_node: string; safe_error: string | null; state?: Record<string, unknown> } | null;
  checks: Check[];
  bindings: { id: string; member_id: string; profile_id: string; role: string; name: string; status: string }[];
}
export const difficultyLabels = { regular: '普通', hard: '困难', extreme: '极难' }
export const resultLabels: Record<string, string> = { critical: '大成功', extreme: '极难成功', hard: '困难成功', regular: '普通成功', failure: '失败', fumble: '大失败' }
export const nodeLabels: Record<string, string> = { collect_context: '整理现场', keeper_decide: 'KP 思考中', validate_keeper_actions: 'KP 思考中', execute_keeper_tools: 'KP 推进调查', wait_for_human_roll: '等待检定', resolve_keeper_response: 'KP 叙事中', narrate_publicly: 'KP 叙事中', run_teammates: '队友行动中', update_memories: '整理记忆', finish_cycle: '回合结束' }
