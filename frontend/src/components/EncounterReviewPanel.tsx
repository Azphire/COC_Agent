import { useState } from 'react'
import type { Room } from '../api/rooms'

type Encounter = { source_event_seq: number; entity_id: string; effect_id: string; condition: string; status: string; target_member_ids: string[] }
type Props = { room: Room; busy: boolean; command: (path: string, body?: unknown, method?: string) => Promise<unknown> }

export default function EncounterReviewPanel({ room, busy, command }: Props) {
  const [reason, setReason] = useState('')
  const [members, setMembers] = useState<string[]>([])
  const queue = (room.game?.cycle?.state?.encounter_queue || []) as Encounter[]
  const item = queue.find(e => e.status === 'review')
  if (!room.is_host || !item || room.game?.cycle?.state?.wait_reason !== 'sanity_encounter_review') return null
  const submit = (approve: boolean) => command('/sanity/encounter-review', {
    source_event_seq: item.source_event_seq, entity_id: item.entity_id, effect_id: item.effect_id,
    approve, target_member_ids: members, reason,
  })
  return <article><h3>主机确认实际 SAN 遭遇</h3>
    <p>{room.game?.host_entities?.find(e => e.id === item.entity_id)?.title} · {item.effect_id}</p>
    <p>{item.condition || '配置要求主机确认，或同一遭遇存在多个效果；请核对条件和实际受影响角色。'}</p>
    <p>实体公开不代表所有人都曾目睹。确认一个效果后，同一遭遇的其他候选效果将被排除。</p>
    {room.members.filter(m => m.active && m.role === 'player' && m.slot_id).map(m => <label key={m.id}><input type="checkbox" checked={members.includes(m.id)} onChange={e => setMembers(e.target.checked ? [...members, m.id] : members.filter(id => id !== m.id))} />{m.display_name}{item.target_member_ids.includes(m.id) ? '（行动者）' : ''}</label>)}
    <label>实际遭遇依据<textarea required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
    <button disabled={busy || room.status !== 'running' || !reason.trim() || !members.length} onClick={() => void submit(true)}>确认遭遇者与效果</button>
    <button disabled={busy || room.status !== 'running' || !reason.trim()} onClick={() => void submit(false)}>未发生此遭遇</button>
  </article>
}
