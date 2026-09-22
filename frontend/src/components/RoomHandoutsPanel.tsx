import { useRef, useState } from 'react'
import type { Result, Room } from '../api/rooms'
import { api, requestId } from '../api/session'
import HandoutSources from './HandoutSources'

export default function RoomHandoutsPanel({ room, token, acceptRoom }: {
  room: Room; token: string; acceptRoom: (room: Room) => void
}) {
  const [handoutId, setHandoutId] = useState('')
  const [slotId, setSlotId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pending = useRef<{ fingerprint: string; id: string } | null>(null)
  const available = room.is_host ? room.handouts?.available ?? [] : []
  const assignments = (room.handouts?.assignments ?? []).filter(item => room.is_host || item.member_id === room.self_member_id)
  const selected = available.find(item => item.id === handoutId)
  const slots = room.character_slots.filter(slot => slot.member_id && !assignments.some(item => item.slot_id === slot.id) && room.members.some(member => member.id === slot.member_id && member.active))
  const slot = slots.find(item => item.id === slotId)
  const recipient = room.members.find(member => member.id === slot?.member_id)
  const editable = ['lobby', 'paused'].includes(room.status)

  async function assign() {
    if (!selected || !slot?.member_id) return
    const body = { handout_id: selected.id, slot_id: slot.id, member_id: slot.member_id }
    const fingerprint = JSON.stringify(body)
    if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, id: requestId() }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<Result>(`/rooms/${room.id}/handout-assignments`, token, 'POST', { ...body, client_request_id: pending.current.id })
      acceptRoom(result.room); pending.current = null
      setNotice(`已将 ${selected.title} 分配给 ${recipient?.display_name ?? slot.public_summary.name}。`)
      setHandoutId(''); setSlotId('')
    } catch (error) { setError(error instanceof Error ? error.message : 'HO 分配失败，请重试') }
    finally { setBusy(false) }
  }

  return <section data-testid="room-handouts"><h2>{room.is_host ? 'HO 私密资料与分配' : '我的 HO 私密资料'}</h2>
    <p>每份资料仅本人和 KP 可见；AI 调查员只会读取分配给自己的资料。</p>
    {room.is_host && <>
      {!available.length && <p>当前准备包尚无可分配的文本 HO。请先在模组准备中导入已校对的准备包，并在房间绑定。</p>}
      {!!available.length && <form onSubmit={event => { event.preventDefault(); void assign() }}>
        <fieldset disabled={busy || !editable}><legend>定向分配 HO</legend>
          <div className="field-grid">
            <label>准备包 HO<select id="handout-selection" value={handoutId} onChange={event => { setHandoutId(event.target.value); setNotice('') }} required>
              <option value="">选择 HO</option>{available.map(item => <option key={item.id} value={item.id} disabled={assignments.some(assignment => assignment.handout_id === item.id)}>{item.title}{assignments.some(assignment => assignment.handout_id === item.id) ? '（已分配）' : ''}</option>)}
            </select></label>
            <label>接收角色与成员<select id="handout-recipient" value={slotId} onChange={event => { setSlotId(event.target.value); setNotice('') }} required>
              <option value="">选择已分配角色</option>{slots.map(item => <option key={item.id} value={item.id}>{item.public_summary.name} · {room.members.find(member => member.id === item.member_id)?.display_name}</option>)}
            </select></label>
          </div>
          {!slots.length && <p>暂无尚未收到 HO 的成员角色。可先发布新角色并分配给成员。</p>}
          <p>每份 HO 与角色只分配一次，请核对接收成员。已分发的秘密无法收回，带 HO 的角色不能转配给其他成员。</p>
          {selected && <details open><summary>核对资料 · {selected.title}</summary><p className="preserve-lines">{selected.text}</p><HandoutSources handout={selected} /></details>}
          <button disabled={!selected || !slot}>分配给 {recipient?.display_name || '所选成员'}</button>
        </fieldset>
        {!editable && <p>请在大厅或暂停时分配 HO。</p>}
      </form>}
    </>}
    {!assignments.length && <p>{room.is_host ? '尚未分配 HO。' : '尚未收到 HO 私密资料。'}</p>}
    {assignments.map(item => <article key={item.id} data-testid="assigned-handout" data-handout-id={item.handout_id}>
      <h3>{item.title}</h3>
      {room.is_host && <p>接收成员：{room.members.find(member => member.id === item.member_id)?.display_name ?? '已离开成员'} · 角色：{room.character_slots.find(candidate => candidate.id === item.slot_id)?.public_summary.name ?? '原分配角色'}</p>}
      <p className="preserve-lines">{item.text}</p>
      <HandoutSources handout={item} />
    </article>)}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
