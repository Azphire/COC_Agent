import type { Character } from './characters'

export type Runtime = { hp: number | null; mp: number | null; san: number | null; luck: number | null; conditions: string[] }
export type SessionState = { version: 1; scene_title: string; scene_summary: string; round_number: number | null; active_slot_id: string | null; characters: Record<string, Runtime> }
export type Member = { id: string; display_name: string; role: 'host' | 'player'; controller_type: 'human' | 'agent'; access_type: 'host_managed' | 'remote'; ready: boolean; active: boolean; slot_id: string | null; last_seen_at: string | null }
export type Slot = { id: string; member_id: string | null; public_summary: { name: string; age: number | null; occupation: string | null; ruleset_id: string }; character_snapshot?: Character }
export type Room = { id: string; name: string; status: 'lobby' | 'running' | 'paused' | 'ended'; host_member_id: string; revision: number; latest_seq: number; state_version: number; self_member_id: string; is_host: boolean; session_state: SessionState; members: Member[]; character_slots: Slot[] }
export type RoomSummary = Pick<Room, 'id' | 'name' | 'status' | 'revision'>
export type RoomEvent = { seq: number; type: string; actor_member_id: string | null; visibility: string; payload: Record<string, unknown>; occurred_at: string; client_request_id: string | null }
export type Save = { id: string; name: string; creator_id: string; event_seq: number; format_version: number; created_at: string }
export type Result = { room: Room; event?: RoomEvent; snapshot?: Save; invite_code?: string; member_token?: string }
export const roomStatus = { lobby: '大厅', running: '运行中', paused: '已暂停', ended: '已结束（只读）' }
export const credentialKey = (roomId: string) => `coc.room.${roomId}`
export function storedRooms() {
  return Object.keys(localStorage).filter(key => key.startsWith('coc.room.')).map(key => key.slice(9))
}
