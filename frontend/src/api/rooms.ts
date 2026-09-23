import type { Character } from './characters'
import type { GameState } from './agents'
import type { CombatView, CombatWeapon } from './combat'
import type { PreparedHandout } from './preparation'

export type SanityState = { belief?: 'believer' | 'unbeliever' | null; belief_conversion?: { cost: number; check_id: string } | null; mythos_gain?: number; kind: string; phase: string; symptom: string; day_start_san: number | null; day_loss: number; ends_minute: number | null; bout_end_minute: number | null; bout_end_round: number | null; history: Record<string, unknown>[] }
export type Runtime = { equipment_settlement?: 'unsettled' | 'retained' | 'module_pending' | 'module_settled'; hp: number | null; hp_max: number | null; mp: number | null; mp_max: number | null; mp_recovery_progress: number; san: number | null; san_max: number | null; sanity: SanityState; luck: number | null; conditions: string[]; injury: Record<string, boolean | string | number | null>; weapons: CombatWeapon[] }
export type InventoryItem = { instance_id: string; item_id: string; title: string; holder_id: string; holder_name: string; remaining_uses: number | null }
export type SessionState = { luck_spending?: boolean; version: 1; game_minute: number; game_round: number; sanity_day: number; scene_title: string; scene_summary: string; round_number: number | null; active_slot_id: string | null; characters: Record<string, Runtime> }
export type Member = { id: string; display_name: string; role: 'host' | 'player'; controller_type: 'human' | 'agent'; access_type: 'host_managed' | 'remote'; ready: boolean; active: boolean; slot_id: string | null; last_seen_at: string | null }
export type Slot = { id: string; member_id: string | null; public_summary: { name: string; age: number | null; occupation: string | null; ruleset_id: string }; character_snapshot?: Character }
export type CharacterSubmission = {
  id: string; member_id: string; version: number; status: 'pending' | 'accepted' | 'rejected'
  created_at: string; updated_at: string; character?: Character; reason?: string; slot_id?: string | null
  source?: { kind: 'imported'; original_id: string; exported_at: string; ruleset: { id: string; version: string; verification_status: string } }
}
export type RoomHandout = PreparedHandout
export type HandoutAssignment = RoomHandout & { handout_id: string; slot_id: string; member_id: string; preparation_id: string; source_id: string; source_hash: string; assigned_event_seq: number }
export type Room = { id: string; name: string; status: 'lobby' | 'running' | 'paused' | 'ended'; host_member_id: string; revision: number; latest_seq: number; state_version: number; self_member_id: string; is_host: boolean; session_state: SessionState; members: Member[]; character_slots: Slot[]; character_submissions: CharacterSubmission[]; inventory: InventoryItem[]; game?: GameState; combat?: CombatView; handouts?: { available: RoomHandout[]; assignments: HandoutAssignment[] } }
export type RoomSummary = Pick<Room, 'id' | 'name' | 'status' | 'revision'>
export type RoomEvent = { seq: number; type: string; actor_member_id: string | null; recipient_member_id?: string | null; visibility: string; payload: Record<string, unknown>; occurred_at: string; client_request_id: string | null }
export type Save = { id: string; name: string; creator_id: string; event_seq: number; format_version: number; created_at: string }
export type Result = { room: Room; event?: RoomEvent; snapshot?: Save; invite_code?: string; member_token?: string }
export const roomStatus = { lobby: '大厅', running: '运行中', paused: '已暂停', ended: '已结束（只读）' }
export const credentialKey = (roomId: string) => `coc.room.${roomId}`
export function storedRooms() {
  return Object.keys(localStorage).filter(key => key.startsWith('coc.room.')).map(key => key.slice(9))
}

export function storyEvent(event: RoomEvent) {
  return ['chat.message', 'dice.rolled', 'keeper.narration', 'npc.spoke', 'agent.spoke', 'agent.action_proposed', 'agent.needs_host_ruling', 'clue.revealed', 'entity.revealed', 'entity.corrected', 'scene.updated', 'module.completed', 'module.interaction', 'resource.item_used', 'handout.assigned', 'game.started', 'game.paused', 'game.resumed', 'game.ended'].includes(event.type)
    || ['action.', 'check.', 'rules.', 'combat.', 'sanity.'].some(prefix => event.type.startsWith(prefix))
}
