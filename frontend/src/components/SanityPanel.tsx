import { useState } from 'react'
import type { Room } from '../api/rooms'
import EncounterReviewPanel from './EncounterReviewPanel'

const kinds: Record<string, string> = { none: '清醒', temporary: '临时性疯狂', indefinite: '不定性疯狂', permanent: '永久性疯狂' }
const phases: Record<string, string> = { none: '无发作', awaiting_symptom: '等待主机选择症状', bout: '疯狂发作', underlying: '潜在疯狂' }
type Props = { room: Room; busy: boolean; command: (path: string, body?: unknown, method?: string) => Promise<unknown> }

export function SanityPanel({ room, busy, command }: Props) {
  const [slot, setSlot] = useState('')
  const [operation, setOperation] = useState('advance')
  const [reason, setReason] = useState('')
  const [symptom, setSymptom] = useState('')
  const [mode, setMode] = useState('realtime')
  const [basis, setBasis] = useState('elapsed')
  const [minute, setMinute] = useState('')
  const [round, setRound] = useState('')
  const [resource, setResource] = useState('san')
  const [value, setValue] = useState('')
  const [effect, setEffect] = useState('')
  const [sourceSeq, setSourceSeq] = useState('')
  const [repeatConfirmed, setRepeatConfirmed] = useState(false)
  const effects = (room.game?.host_entities || []).flatMap(e => (e.sanity_effects || []).map(s => ({ ...s, entity_id: e.id, title: e.title, key: `${e.id}:${s.id}` })))
  const legacyMaximum = (id: string) => {
    const snapshot = room.character_slots.find(s => s.id === id)?.character_snapshot
    return snapshot?.ruleset_id === 'coc7-character-creation' ? Math.max(0, 99 - (snapshot.skill_values.cthulhu_mythos || 0)) : '—'
  }
  return <section><h2>理智与疯狂</h2><p>游戏第 {room.session_state.sanity_day + 1} 天 · 第 {room.session_state.game_minute} 分钟 · 第 {room.session_state.game_round} 轮</p>
    {Object.entries(room.session_state.characters).map(([id, c]) => <article key={id} data-sanity-slot={id}><h3>{room.character_slots.find(s => s.id === id)?.public_summary.name} · Luck {c.luck ?? '—'} · SAN {c.san ?? '—'} / {c.san_max ?? legacyMaximum(id)}</h3><p>{kinds[c.sanity.kind]} · {phases[c.sanity.phase]} · {c.sanity.symptom || '尚无症状记录'}</p><p>本日累计损失 {c.sanity.day_loss}；日初 SAN {c.sanity.day_start_san ?? '首次遭遇时记录'}；临时疯狂结束分钟 {c.sanity.ends_minute ?? '—'}；发作结束 {c.sanity.bout_end_round != null ? `第 ${c.sanity.bout_end_round} 轮` : c.sanity.bout_end_minute != null ? `第 ${c.sanity.bout_end_minute} 分钟` : '—'}</p><details><summary>症状与恢复记录</summary><pre>{JSON.stringify(c.sanity.history, null, 2)}</pre></details></article>)}
    <EncounterReviewPanel room={room} busy={busy} command={command} />
    <p>幸运消耗可选规则：{room.session_state.luck_spending ? '已启用' : '未启用'}</p>
    {room.is_host && <button disabled={busy || !!room.game?.cycle && ['running', 'waiting_for_roll', 'waiting_for_review', 'failed'].includes(room.game.cycle.status)} onClick={() => void command('/check-rules', { expected_revision: room.revision, luck_spending: !room.session_state.luck_spending }, 'PATCH')}>{room.session_state.luck_spending ? '关闭' : '启用'}幸运消耗可选规则</button>}
    {room.is_host && ['running', 'paused'].includes(room.status) && <details><summary>主机：资源更正、游戏时间与恢复</summary><p>时间只随主机推进。不定性疯狂需要记录治疗检定结果或章节结束裁定。症状可按规则表选择；伴随的装备、伤害等后果仍由主机处理。</p><form onSubmit={e => { e.preventDefault(); void command('/sanity/manage', { expected_revision: room.revision, operation, reason, slot_id: slot || null, minute: minute === '' ? null : Number(minute), round: round === '' ? null : Number(round), symptom, mode, recovery_basis: basis }) }}>
      <label>角色<select value={slot} onChange={e => setSlot(e.target.value)}><option value="">全部（仅时间）</option>{room.character_slots.map(s => <option key={s.id} value={s.id}>{s.public_summary.name}</option>)}</select></label>
      <label>操作<select value={operation} onChange={e => setOperation(e.target.value)}><option value="advance">推进游戏时间</option><option value="new_day">充分休息，开始新游戏日</option><option value="symptom">确认症状并掷发作持续时间</option><option value="end_bout">按时间结束发作</option><option value="recover">确认恢复</option></select></label>
      <div className="field-grid"><label>游戏分钟<input type="number" min={room.session_state.game_minute} value={minute} onChange={e => setMinute(e.target.value)} /></label><label>游戏轮<input type="number" min={room.session_state.game_round} value={round} onChange={e => setRound(e.target.value)} /></label></div>
      {operation === 'symptom' && <><label>症状与具体表现<textarea required value={symptom} onChange={e => setSymptom(e.target.value)} placeholder="例如偏执：认为有人正监视自己" /></label><label>发作形式<select value={mode} onChange={e => setMode(e.target.value)}><option value="realtime">即时：1d10 轮</option><option value="summary">总结：1d10 小时</option></select></label></>}
      {operation === 'recover' && <label>恢复依据<select value={basis} onChange={e => setBasis(e.target.value)}><option value="elapsed">临时疯狂持续时间已满</option><option value="safe_sleep">安全场所良好睡眠</option><option value="host_treatment">主机确认治疗检定成功</option><option value="chapter_end">主机裁定章节结束恢复</option></select></label>}
      <label>裁定依据／说明<textarea required maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={busy}>确认</button></form>
      <form onSubmit={e => { e.preventDefault(); void command('/resources/correct', { expected_revision: room.revision, slot_id: slot, resource, value: value === '' ? null : Number(value), reason }) }}><h3>资源更正</h3><p>使用上方选择的角色与说明；更正不会计为遭遇损失。HP、MP、Luck 可留空记录为未定义。</p><label>资源<select value={resource} onChange={e => setResource(e.target.value)}>{['hp', 'mp', 'san', 'luck'].map(r => <option key={r}>{r}</option>)}</select></label><label>更正现值<input required={resource === 'san'} type="number" min={0} value={value} onChange={e => setValue(e.target.value)} /></label><button disabled={busy || !slot || !reason}>记录更正</button></form>
      <form onSubmit={e => { e.preventDefault(); const chosen = effects.find(s => s.key === effect); if (chosen) void command('/sanity/encounters', { target_member_id: room.character_slots.find(s => s.id === slot)?.member_id, entity_id: chosen.entity_id, effect_id: chosen.id, source_event_seq: Number(sourceSeq), encounter_confirmed: true, repeat_confirmed: repeatConfirmed, reason }) }}><h3>发起已批准 SAN 遭遇</h3><p>使用上方角色。遭遇事件编号须指向该角色针对该实体的调查行动，或配置要求的实体揭示事件。</p><label>批准效果<select required value={effect} onChange={e => setEffect(e.target.value)}><option value="">选择效果</option>{effects.map(s => <option key={s.key} value={s.key}>{s.title} · {s.encounter} · {s.success_loss}/{s.failure_loss}</option>)}</select></label><label>遭遇事件编号<input required type="number" min={1} value={sourceSeq} onChange={e => setSourceSeq(e.target.value)} /></label><button disabled={busy || !slot || !effect || !reason.trim() || room.status !== 'running'}>确认实际遭遇，等待掷骰</button><label><input type="checkbox" checked={repeatConfirmed} onChange={e => setRepeatConfirmed(e.target.checked)} />确认这是新的重复遭遇（须有批准配置）</label></form>
    </details>}
  </section>
}
