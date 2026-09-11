import { useState } from 'react'
import type { Check } from '../api/agents'
import { resultLabels } from '../api/agents'
import type { Room } from '../api/rooms'

type Props = { check: Check; room: Room; busy: boolean; command: (path: string, body?: unknown, method?: string) => Promise<unknown> }

export default function CheckSettlementPanel({ check, room, busy, command }: Props) {
  const [spend, setSpend] = useState('')
  const [effort, setEffort] = useState('')
  const [reason, setReason] = useState('')
  const [kind, setKind] = useState('host_manual')
  const [description, setDescription] = useState('')
  const [condition, setCondition] = useState('')
  const [minutes, setMinutes] = useState('1')
  const p = check.settlement
  if (!p) return null
  const member = room.members.find(m => m.id === check.target_member_id)
  const canChoose = room.self_member_id === check.target_member_id || room.is_host && (member?.controller_type === 'agent' || member?.access_type === 'host_managed')
  const disabled = busy || room.status !== 'running'
  const path = `/checks/${check.id}`
  const luckChoices = (check.options?.luck || []).filter(o => o.result.passed !== p.original_result.passed || o.result.level !== p.original_result.level).filter((o, i, values) => values.findIndex(v => v.result.level === o.result.level && v.result.passed === o.result.passed) === i)
  return <div className="check-settlement">
    <p>原骰点 <strong>{p.original_result.total}</strong> · {resultLabels[p.original_result.level]} · {p.original_result.passed ? '通过' : '未通过'}{p.stage !== 'final' && ' · 尚未最终裁决'}</p>
    {!!p.luck_spent && <p>花费 Luck {p.luck_spent}（{p.luck_before} → {p.luck_after}）；不获得技能成长标记。</p>}
    {p.effort && <p>额外尝试：{p.effort}</p>}
    {p.review && <p>KP{p.review.approve ? '核准' : '拒绝'}：{p.review.reason}。{p.review.consequence && `若失败：${p.review.consequence.description}`}</p>}
    {p.push_result && <p>孤注骰点 {p.push_result.total} · {resultLabels[p.push_result.level]} · {p.push_result.passed ? '通过' : '未通过'}</p>}
    {p.handled_reason && <p>主机后果处理记录：{p.handled_reason}</p>}
    {check.status === 'pending' && p.stage === 'choice' && canChoose && <>
      <button disabled={disabled} onClick={() => void command(path + '/choice', { operation: 'accept' })}>接受原结果</button>
      {!!luckChoices.length && <form onSubmit={e => { e.preventDefault(); void command(path + '/choice', { operation: 'luck', spend: Number(spend) }) }}>
        <label>幸运消耗（可选规则）<select required value={spend} onChange={e => setSpend(e.target.value)}><option value="">选择花费与结果</option>{luckChoices.map(o => <option key={o.spend} value={o.spend}>花费 {o.spend} → {o.result.total} · {resultLabels[o.result.level]} · {o.result.passed ? '通过' : '未通过'}</option>)}</select></label><button disabled={disabled || !spend}>确认扣除 Luck 并结算</button>
      </form>}
      {check.options?.push && <form onSubmit={e => { e.preventDefault(); void command(path + '/choice', { operation: 'push', effort }) }}><label>额外努力或时间花费<textarea required maxLength={500} value={effort} onChange={e => setEffort(e.target.value)} /></label><button disabled={disabled}>申请一次孤注</button></form>}
    </>}
    {p.stage === 'push_review' && <p>KP 正在判断新尝试及更严重的失败后果。</p>}
    {p.stage === 'push_review' && room.is_host && <details><summary>主机处理异常</summary><form onSubmit={e => { e.preventDefault(); void command(path + '/push-review', { approve: true, reason, consequence: { kind, description, condition, minutes: kind === 'time' ? Number(minutes) : null } }) }}>
      <label>资格与合理性依据<textarea required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
      <label>更严重的失败后果<textarea required maxLength={500} value={description} onChange={e => setDescription(e.target.value)} /></label>
      <label>后果处理<select value={kind} onChange={e => setKind(e.target.value)}><option value="host_manual">等待主机处理（伤害、装备等）</option><option value="condition">增加角色状态</option><option value="time">流逝游戏时间</option></select></label>
      {kind === 'condition' && <label>状态<input required maxLength={120} value={condition} onChange={e => setCondition(e.target.value)} /></label>}
      {kind === 'time' && <label>分钟<input required type="number" min={1} max={1440} value={minutes} onChange={e => setMinutes(e.target.value)} /></label>}
      <button disabled={disabled}>核准，等待玩家确认掷骰</button><button type="button" disabled={disabled || !reason.trim()} onClick={() => void command(path + '/push-review', { approve: false, reason })}>拒绝并接受原失败</button>
    </form></details>}
    {p.stage === 'push_roll' && <><p>仅此一次；孤注结果不能再花幸运或孤注。</p>{canChoose && <button disabled={disabled} onClick={() => void command(path + '/push-roll', {})}>确认后果，掷孤注骰</button>}</>}
    {p.stage === 'consequence' && <><p>后果尚未处理，最终叙事等待主机确认。</p>{room.is_host && <form onSubmit={e => { e.preventDefault(); void command(path + '/consequence', { reason }) }}><label>实际处理结果<textarea required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={disabled}>记录已处理并完成结算</button></form>}</>}
  </div>
}
