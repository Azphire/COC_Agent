import { useEffect, useRef, useState } from 'react'
import { charactersApi } from '../api/characters'
import type { Character, RuleSet } from '../api/characters'
import type { CharacterSubmission, Result, Room } from '../api/rooms'
import { api, requestId } from '../api/session'
import { specializationName } from './character/specializations'

const statuses = { pending: '待处理', accepted: '已接受', rejected: '已拒绝' }
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败，请重试'

function CharacterPreview({ character: c }: { character: Character }) {
  const [rules, setRules] = useState<RuleSet | null>(null)
  useEffect(() => {
    let active = true
    charactersApi.rulesetVersion(c.ruleset_id, c.ruleset_version).then(r => { if (active) setRules(r) }).catch(() => {})
    return () => { active = false }
  }, [c.ruleset_id, c.ruleset_version])
  function skillName(key: string) {
    const custom = c.custom_specializations.find(s => s.id === key)
    return custom ? specializationName(custom) : rules?.skills.find(s => s.key === key)?.display_name || key
  }
  return <div data-testid="submission-preview">
    <h3>{c.name} · {c.age} 岁</h3>
    <p>{rules?.occupations.find(o => o.key === c.occupation)?.display_name || c.occupation} · {c.ruleset_id} / {c.ruleset_version} · {c.era === 'modern' ? '现代' : '1920 年代'}</p>
    <p>服务端已重算，{c.validation.valid ? '校验通过' : '数值校验通过，以下专业待本房间 KP 引入'}。来源：导入；保留文件中的原骰，导入记录不证明骰子曾由本主机生成。</p>
    {c.validation.issues.filter(i => i.code === 'keeper_approval').map(i => <p key={i.field}>{i.message}</p>)}
    <p>剩余职业点：{c.remaining_points.occupation}；兴趣点：{c.remaining_points.interest}</p>
    <div className="field-grid">{Object.entries(c.effective_attributes).map(([k, v]) => <p key={k}>{rules?.attributes.find(a => a.key === k)?.display_name || k}：{v}</p>)}</div>
    <p>{Object.entries(c.derived_values).map(([k, v]) => `${rules?.derived_values.find(d => d.key === k)?.display_name || k}：${v}`).join(' · ')}</p>
    <h4>自定义专业（本卡 {c.custom_specializations.length} 项）</h4>
    {c.custom_specializations.length === 0 ? <p>无</p> : <ul>{c.custom_specializations.map(s => <li key={s.id}>{skillName(s.id)}：{c.skill_values[s.id]}</li>)}</ul>}
    <details><summary>全部技能与投入</summary><table><thead><tr><th>技能</th><th>职业点</th><th>兴趣点</th><th>最终值</th></tr></thead><tbody>{Object.entries(c.skill_values).map(([k, v]) => <tr key={k}><td>{skillName(k)}</td><td>{c.occupation_skills[k]?.points || 0}</td><td>{c.interest_skills[k]?.points || 0}</td><td>{v}</td></tr>)}</tbody></table></details>
    <details><summary>背景、资产、装备与原骰</summary>
      <pre>{JSON.stringify({ background: c.background, finances: c.finances, asset_details: c.asset_details, equipment: c.equipment, roll_records: c.roll_records }, null, 2)}</pre>
    </details>
  </div>
}

export default function CharacterSubmissionPanel({ room, token, acceptRoom }: { room: Room; token: string; acceptRoom: (room: Room) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [candidate, setCandidate] = useState<{ document: unknown; character: Character; version: number; filename: string } | null>(null)
  const [selected, setSelected] = useState<CharacterSubmission | null>(null)
  const [reason, setReason] = useState('')
  const pending = useRef<{ fingerprint: string; id: string } | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const prefix = `/rooms/${room.id}/character-submissions`
  const own = room.character_submissions.find(s => s.member_id === room.self_member_id)
  const editable = room.status === 'lobby'
  const shown = room.is_host ? selected : own
  const member = room.members.find(m => m.id === shown?.member_id)
  const existing = room.character_slots.find(s => s.member_id === (room.is_host ? shown?.member_id : room.self_member_id))
  const stale = shown && room.character_submissions.find(s => s.member_id === shown.member_id)?.id !== shown.id
  const processed = shown && room.character_submissions.find(s => s.id === shown.id)?.status !== 'pending'

  async function mutate(path: string, body: Record<string, unknown>) {
    const fingerprint = JSON.stringify({ path, body })
    if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, id: requestId() }
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await api<Result>(path, token, 'POST', { ...body, client_request_id: pending.current.id })
      acceptRoom(result.room); pending.current = null
      return result.room
    } catch (error) { setError(errorText(error)); return null } finally { setBusy(false) }
  }

  return <section data-testid="character-submissions"><h2>{room.is_host ? '玩家角色提交' : '提交角色文件'}</h2>
    <p>玩家提交现有 CharacterExport JSON；主机接受后自动分配给提交者，再由玩家点击准备。</p>
    {!editable && <p>游戏已开始，角色提交和审批已关闭。</p>}
    {error && <p role="alert" className="preserve-lines">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!room.is_host && editable && <>
      <label>角色 JSON 文件（请求上限 256 KiB）<input ref={input} type="file" accept=".json,application/json" disabled={busy} onChange={async event => {
        const file = event.target.files?.[0]
        setCandidate(null); setError(''); setNotice('')
        if (!file) return
        setBusy(true)
        try {
          if (file.size > 256 * 1024) throw new Error('角色文件不能超过 256 KiB')
          let document: unknown
          try { document = JSON.parse(await file.text()) } catch { throw new Error('文件不是有效 JSON，请从原车卡工具重新导出 CharacterExport 文件') }
          const version = own?.version || 0
          const character = await api<Character>(`${prefix}/preview`, token, 'POST', { document })
          setCandidate({ document, character, version, filename: file.name })
        } catch (error) { setError(errorText(error)) } finally { setBusy(false) }
      }} /></label>
      {candidate && <><p>文件：{candidate.filename}</p><CharacterPreview character={candidate.character} />
        <button disabled={busy} onClick={async () => {
          if (await mutate(prefix, { document: candidate.document, expected_version: candidate.version })) {
            setCandidate(null); if (input.current) input.current.value = ''; setNotice('角色已提交，等待主机处理。')
          }
        }}>{own ? '提交新版本' : '提交角色'}</button></>}
    </>}
    {room.character_submissions.length === 0 && <p>暂无角色提交</p>}
    <ul>{room.character_submissions.map(s => <li key={s.id}>
      {room.members.find(m => m.id === s.member_id)?.display_name} · 版本 {s.version} · {statuses[s.status]}
      {room.is_host && <button disabled={busy} onClick={async () => {
        setBusy(true); setError(''); setReason('')
        try { setSelected(await api<CharacterSubmission>(`${prefix}/${s.id}`, token)) }
        catch (error) { setError(errorText(error)) } finally { setBusy(false) }
      }}>预览 · {room.members.find(m => m.id === s.member_id)?.display_name}</button>}
    </li>)}</ul>
    {existing && <p>当前已分配角色：<strong>{existing.public_summary.name}</strong>。接受新角色前，请先在“房间调查员”中取消原角色分配。</p>}
    {shown?.character && <article>
      <h3>{member?.display_name} · 提交版本 {shown.version} · {statuses[shown.status]}</h3>
      {stale && <p role="alert">已有新版本，请重新打开当前提交预览；不能批准此旧版本。</p>}
      <p>原角色 ID：{shown.source?.original_id} · 文件导出时间：{shown.source?.exported_at}</p>
      {shown.reason && <p>处理说明：{shown.reason}</p>}
      <CharacterPreview character={shown.character} />
      {room.is_host && editable && shown.status === 'pending' && <>
        {shown.character.validation.issues.some(i => i.code === 'keeper_approval') && <p>接受并分配同时核准上列待引入专业。请先核对年代、人物背景及游戏范围；不同意可拒绝并说明。</p>}
        <label>处理说明（可选）<textarea maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <div className="action-row">{(['accept', 'reject'] as const).map(decision => <button key={decision} disabled={busy || !!stale || !!processed || (decision === 'accept' && (!!existing || !member?.active))} onClick={async () => {
          const next = await mutate(`${prefix}/${shown.id}/review`, { expected_version: shown.version, decision, reason,
            approve_specializations: decision === 'accept' ? shown.character!.validation.issues.filter(i => i.code === 'keeper_approval').map(i => i.field.replace('specialization_approvals.', '')) : [] })
          if (next) { setSelected(next.character_submissions.find(s => s.id === shown.id) || null); setNotice(decision === 'accept' ? '已接受并分配，请玩家准备。' : '已拒绝，玩家可以提交新版本。') }
        }}>{decision === 'accept' ? '接受并分配' : '拒绝提交'}</button>)}</div>
      </>}
    </article>}
  </section>
}
