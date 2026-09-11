import { useState } from 'react'
import type { Check } from '../api/agents'
import { difficultyLabels, resultLabels } from '../api/agents'
import type { Room } from '../api/rooms'

type Props = { check: Check; room: Room; busy?: boolean; command?: (path: string, body?: unknown, method?: string) => Promise<unknown> }

export default function CompoundCheckPanel({ check, room, busy = false, command }: Props) {
  const [spends, setSpends] = useState<Record<string, string>>({})
  const progress = check.compound
  if (!progress) return null
  const result = check.result || check.settlement?.push_result || check.settlement?.original_result
  if (check.combined) return <div className="compound-check">
    <p>共用一次百分骰 · {check.combined.requirement === 'any' ? '任一技能成功即可' : '两个技能必须全部成功'}</p>
    <ul>{progress.components?.map((c, i) => {
      const r = result?.components?.[i]
      return <li key={c.name}>{c.display_name} {c.value} · {difficultyLabels[c.difficulty as Check['difficulty']]}{r && ` · 骰点 ${r.total} / 目标 ${r.threshold} · ${resultLabels[r.level]} · ${r.passed ? '通过' : '未通过'}`}</li>
    })}</ul>
    {check.status === 'pending' && <p>幸运或孤注会同时重算两项技能，一次掷骰、一次扣点。</p>}
  </div>
  if (!check.opposed) return null
  const disabled = busy || room.status !== 'running'
  return <div className="compound-check">
    <p>非战斗对抗 · 比成功等级，同级比数值，完全相同为僵局 · 不可孤注</p>
    {progress.participants?.map((side, i) => {
      const member = room.members.find(m => m.id === side.member_id)
      const allowed = side.controller === 'human' && (room.self_member_id === side.member_id || room.is_host && member?.access_type === 'host_managed')
      const raw = side.settlement?.original_result
      const spend = spends[side.participant_id] || ''
      const options = (side.options?.luck || []).filter((o, index, all) => all.findIndex(v => v.result.level === o.result.level) === index)
      const choose = check.status === 'pending' && progress.stage === 'choice' && progress.choice_index === i
      const send = (suffix: string, body: Record<string, unknown> = {}) => command?.(`/checks/${check.id}/${suffix}`, { ...body, participant_id: side.participant_id })
      return <article className="check-card" key={side.participant_id}>
        <h4>{side.label} · {side.display_name} {side.value}</h4>
        <p>奖励骰 {side.bonus_dice} / 惩罚骰 {side.penalty_dice} · {side.controller === 'human' ? '真人操作' : '服务端掷骰'}</p>
        {raw ? <p>原骰点 {raw.total} · {resultLabels[raw.level]}{side.result ? ' · 已确认' : ' · 待选择'}</p> : <p>等待掷骰</p>}
        {!!side.settlement?.luck_spent && <p>花费幸运 {side.settlement.luck_spent}（{side.settlement.luck_before} → {side.settlement.luck_after}），调整为 {side.result?.total} · {resultLabels[side.result?.level || '']}；不获得技能成长标记。</p>}
        {command && allowed && check.status === 'pending' && !raw && <button disabled={disabled} onClick={() => void send('roll')}>掷自己的对抗骰</button>}
        {choose && <p>轮到 {side.label} 选择结果；确认后不能改选。</p>}
        {command && allowed && choose && <>
          <button disabled={disabled} onClick={() => void send('choice', { operation: 'accept' })}>接受原骰</button>
          {!!options.length && <form onSubmit={e => { e.preventDefault(); void send('choice', { operation: 'luck', spend: Number(spend) }) }}>
            <label>花费幸运<select required value={spend} onChange={e => setSpends({ ...spends, [side.participant_id]: e.target.value })}><option value="">选择花费</option>{options.map(o => <option key={o.spend} value={o.spend}>{o.spend} 点 → {o.result.total} · {resultLabels[o.result.level]}</option>)}</select></label>
            <button disabled={disabled || !spend}>确认扣点</button>
          </form>}
        </>}
      </article>
    })}
    {check.status === 'pending' && <p>双方原骰齐备后，按上方顺序各选择一次幸运；全部确认后才公布胜负。</p>}
    {check.result && <p><strong>{check.result.winner == null ? '完全平局：僵局' : `${progress.participants?.[check.result.winner]?.label} 获胜`}</strong>{check.result.both_failed && ' · 双方单项均未成功，按非战斗对抗规则比较'}</p>}
  </div>
}
