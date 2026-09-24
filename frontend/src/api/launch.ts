import { api, hostToken } from './session'
import type { Character } from './characters'
import type { AgentProfileInput } from './agents'
import type { Room } from './rooms'

export type PartyMember = { index: number; reroll_count: number; status: string; character: Character; profile: AgentProfileInput | null; private_profile?: boolean; error: string | null; model_calls: number; latency_ms: number; generation_stage?: string; recovery?: { can_continue: boolean; can_repair: boolean; repair_calls: number; can_edit_text: boolean } }
export type PartyBatch = { id: string; status: string; count: number; era: '1920s' | 'modern'; completed: number; seed: string; ruleset_id: string; ruleset_version: string; preparation_id: string | null; preparation_version: number | null; members: PartyMember[]; character_ids: string[]; profile_ids: string[]; error: string | null; metrics?: { model_calls: number; elapsed_ms: number; rerolls: number } }
export type LaunchIssue = { code: string; message: string; repair?: string }
export type RuleReference = { source_id: string; source_hash: string }
export type LaunchModule = { id: string; title: string; version: number; source_hash: string; public_introduction: string; requirements?: { minimum_players?: number | null; maximum_players?: number | null; recommended_players?: number; era?: string | null; source?: string; note?: string; [key: string]: unknown }; handouts: { id: string; title: string; age?: number; era?: string }[] }
export type LaunchDocument = { preparation_id: string; character_id?: string | null; party_batch_id?: string | null; own_batch_id?: string | null; ai_count?: number; reserved_humans?: number; era?: '1920s' | 'modern'; own_handout?: string | null; name?: string; rules?: RuleReference[]; handout_acknowledged?: boolean; acknowledge_unspent?: boolean }
export type LaunchDraft = { id: string; version: number; status: string; room_id: string | null; room_revision?: number; invite_code?: string; local_member_id?: string; document: LaunchDocument; steps: Record<string, unknown>; issues: LaunchIssue[]; can_start?: boolean }
export type LaunchOptions = { preparations: LaunchModule[]; characters: (Pick<Character, 'id' | 'name' | 'age' | 'occupation'> & { module_handout?: { preparation_id: string; handout_id: string } | null; remaining_points?: Character['remaining_points'] })[]; rules: (RuleReference & { title: string })[]; default_rules: RuleReference[]; model: { provider: string; model: string; state?: string; error?: string | null }; drafts?: LaunchDraft[] }
export type PlaySession = { room: Room | null; member_token: string | null; local_members: { id: string; display_name: string }[]; selected_member_id: string | null }
export const launchApi = {
  options: () => api<LaunchOptions>('/launch/options', hostToken()),
  draft: (id: string) => api<LaunchDraft>(`/launch-drafts/${id}`, hostToken()),
  create: (body: LaunchDocument & { client_request_id: string }) => api<LaunchDraft>('/launch-drafts', hostToken(), 'POST', body),
  update: (id: string, body: Partial<LaunchDocument> & { version: number }) => api<LaunchDraft>(`/launch-drafts/${id}`, hostToken(), 'PATCH', body),
  step: (id: string, step: 'preflight' | 'assemble' | 'start' | 'repair', body: unknown = {}) => api<LaunchDraft>(`/launch-drafts/${id}/${step}`, hostToken(), 'POST', body),
  party: (id: string) => api<PartyBatch>(`/party-batches/${id}`, hostToken()),
  play: (roomId: string, memberId?: string) => api<PlaySession>(`/rooms/${roomId}/play-session`, hostToken(), 'POST', memberId ? { member_id: memberId } : {}),
}
