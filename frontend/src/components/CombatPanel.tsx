import { useEffect, useState } from 'react'
import { charactersApi } from '../api/characters'
import type { EquipmentDefinition } from '../api/characters'
import EquipmentFacts from './character/EquipmentFacts'
import type { Room } from '../api/rooms'
import { requestId } from '../api/session'

const levels: Record<string, string> = { critical: '大成功', extreme: '极难成功', hard: '困难成功', regular: '常规成功', failure: '失败', fumble: '大失败' }
const injuries: Record<string, string> = { major_wound: '重伤', prone: '倒地', unconscious: '昏迷', dying: '濒死', dead: '死亡', stabilized: '暂时稳定' }
type Props = { room: Room; busy: boolean; command: (path: string, body?: unknown, method?: string) => Promise<unknown> }

export default function CombatPanel({ room, busy, command }: Props) {
  const [target, setTarget] = useState('')
  const [healer, setHealer] = useState('')
  const [weapon, setWeapon] = useState('unarmed')
  const [spend, setSpend] = useState(1)
  const [name, setName] = useState('')
  const [reason, setReason] = useState('')
  const [resolutionReason, setResolutionReason] = useState('')
  const [member, setMember] = useState('')
  const [loadout, setLoadout] = useState('unarmed')
  const [catalog, setCatalog] = useState<EquipmentDefinition[]>([])
  const [catalogError, setCatalogError] = useState('')
  const [weaponSkills, setWeaponSkills] = useState<Record<string, number>>({})
  const [rangeBand, setRangeBand] = useState('base')
  useEffect(() => { charactersApi.equipment(true).then(data => setCatalog(data.filter(c => c.weapon))).catch(e => setCatalogError(e.message)) }, [])
  const [ammo, setAmmo] = useState(0)
  const [reserve, setReserve] = useState(0)
  const [ready, setReady] = useState(false)
  const [stats, setStats] = useState({ hp: 12, dex: 50, con: 50, brawl: 40, dodge: 25, armor: 0 })
  const selected = catalog.find(c => c.id === loadout)
  const template = selected?.weapon_template
  const combat = room.combat
  if (!combat) return null
  const participants = Object.values(combat.participants)
  const pending = combat.pending
  const actor = combat.current_actor_id ? combat.participants[combat.current_actor_id] : undefined
  const actionWeapon = actor?.weapons?.find(w => w.id === weapon) ?? actor?.weapons?.[0]
  const chooser = pending?.participant_id ? combat.participants[pending.participant_id] : undefined
  const selectedSpend = pending?.luck_options.some(o => o.spend === spend) ? spend : pending?.luck_options[0]?.spend
  const controls = (id?: string | null) => {
    if (!id) return room.is_host
    const m = room.members.find(m => m.id === id)
    return id === room.self_member_id || room.is_host && (m?.access_type === 'host_managed' || m?.controller_type === 'agent')
  }
  const canChoose = !!pending && controls(chooser?.member_id) && room.status === 'running'
  const canChooseFreely = canChoose && !pending?.restricted_reason
  const actorBlocked = !!actor?.autonomy_blocked || !!actor?.incapacitated || room.status !== 'running'
  const healers = participants.filter(p => p.member_id && controls(p.member_id))
  const healerId = healer || healers.find(p => p.member_id === room.self_member_id)?.id || ''
  const healerBlocked = !!combat.participants[healerId]?.autonomy_blocked || !!combat.participants[healerId]?.incapacitated
  const treat = (operation: string) => command('/combat/action', { actor_id: healerId, target_id: target, operation, reason: operation === 'first_aid' ? '实际为所选伤员急救' : '实际为所选伤员进行医学治疗', client_request_id: requestId(), turn_key: combat.turn_key })
  const sendStep = (operation: string, extra = {}) => pending && command('/combat/step', { action_id: pending.id, stage: pending.stage, operation, ...extra })
  const act = (operation: string) => actor && command('/combat/action', { actor_id: actor.id, target_id: target || null, weapon_id: actionWeapon?.id, range_band: actionWeapon?.kind === 'firearm' ? rangeBand : 'base', operation, reason: operation === 'attack' ? '按面板选择发动攻击' : '按面板选择行动', client_request_id: requestId(), turn_key: combat.turn_key })
  return <section className="combat-panel" aria-label="基础战斗">
    <h2>{combat.active ? `战斗 · 第 ${combat.round} 轮` : '战斗与伤势'}</h2>
    {room.is_host && combat.unavailable_templates?.map(npc => <p key={npc.entity_id}>当前场景 {npc.title} 缺少战斗资料：{npc.missing.join('、')}。{npc.missing_by_operation?.treatment.length === 0 ? '治疗所需数值齐全，可使用治疗入口。' : '治疗仍缺少已确认的HP或体质。'}来源：{npc.source}</p>)}
    {combat.active && <p role="status">当前行动者：<strong>{actor?.label || '等待处理'}</strong>。你仍可在下方自由交谈或描述其他尝试。</p>}
    {room.is_host && combat.active && !pending && <button disabled={busy} onClick={() => void command('/combat/control', { operation: 'end', expected_revision: room.revision, reason: '主机确认双方停止冲突' })}>确认战斗结束</button>}
    <ol>{(combat.active ? combat.order : participants.map(p => p.id)).map(id => {
      const p = combat.participants[id]
      return p && <li key={id} aria-current={id === combat.current_actor_id ? 'step' : undefined}>
        <strong>{p.label}</strong> {id === combat.current_actor_id && ' ← 当前'}
        {p.hp !== undefined && <> · HP {p.hp}/{p.hp_max} · 护甲 {p.armor}</>}
        {Object.entries(injuries).filter(([key]) => p.injury?.[key]).map(([key, text]) => <span className="combat-condition" key={key}>{text}</span>)}
        {p.hp === undefined && p.incapacitated && <span> · 失去行动能力</span>}
        {p.autonomy_reason && <p role="status">{p.autonomy_reason}</p>}
        {p.weapons?.filter(w => w.kind === 'firearm').map(w => <span key={w.id}> · {w.name} {w.ammo} 发／备弹 {w.reserve}{w.jammed ? '（故障）' : ''}</span>)}
      </li>
    })}</ol>
    {pending && <div className="combat-pending"><h3>等待 {chooser?.label || '服务端'}{pending.stage === 'defense' ? '选择防御' : pending.stage.endsWith('_choice') ? '确认结果' : '掷骰'}</h3>
      {Object.entries(pending.rolls).map(([key, roll]) => <p key={key}>{key.startsWith('attack') ? '攻击' : key.startsWith('defense') ? '防御' : key.startsWith('health') ? '体质' : '治疗'}：{roll.raw?.total !== undefined && <>原骰 {roll.raw.total} → </>}{roll.result.total} {levels[roll.result.level] || roll.result.level}{roll.luck_spent ? `（幸运 −${roll.luck_spent}）` : ''}</p>)}
      {pending.restricted_reason && <div><p role="status">{pending.restricted_reason}。主机可处理此等待阶段：停止尚未掷骰的自主动作；不进行新的自主防御；接受已有结果并继续强制体质与结算。</p>{room.is_host && <><label>处理原因<input aria-label="受限阶段处理原因" value={resolutionReason} maxLength={500} onChange={e => setResolutionReason(e.target.value)} /></label><button disabled={busy || room.status !== 'running' || !resolutionReason.trim()} onClick={() => void sendStep('resolve_restricted', { reason: resolutionReason })}>确认受限阶段处理</button></>}</div>}
      {canChooseFreely && pending.stage === 'defense' && <div className="action-row">
        {pending.weapon_kind === 'firearm' ? <><button disabled={busy} onClick={() => void sendStep('cover')}>寻找掩体（失去下次行动）</button><button disabled={busy} onClick={() => void sendStep('take')}>保持行动，不寻找掩体</button></> : <><button disabled={busy} onClick={() => void sendStep('dodge')}>闪避</button><select aria-label="反击武器" value={weapon} onChange={e => setWeapon(e.target.value)}>{chooser?.weapons?.filter(w => w.kind === 'melee').map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select><button disabled={busy} onClick={() => void sendStep('fight_back', { weapon_id: weapon })}>反击</button></>}
      </div>}
      {canChoose && (!pending.restricted_reason || pending.stage.startsWith('health_')) && pending.stage.endsWith('_roll') && <button disabled={busy} onClick={() => void sendStep('roll')}>确认掷骰</button>}
      {canChoose && pending.stage.endsWith('_choice') && <div className="action-row"><button disabled={busy} onClick={() => void sendStep('accept')}>接受结果</button>{pending.luck_options.length > 0 && <><select aria-label="幸运花费" value={selectedSpend} onChange={e => setSpend(Number(e.target.value))}>{pending.luck_options.map(o => <option key={o.spend} value={o.spend}>花费 {o.spend} → {o.result.total}（{levels[o.result.level]}）</option>)}</select><button disabled={busy || selectedSpend === undefined} onClick={() => void sendStep('luck', { spend: selectedSpend })}>花费幸运</button></>}</div>}
    </div>}
    {combat.active && !pending && actor && controls(actor.member_id) && <div className="action-row">
      <select aria-label="战斗目标" value={target} onChange={e => setTarget(e.target.value)}><option value="">选择目标</option>{participants.filter(p => p.id !== actor.id).map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
      <select aria-label="行动武器" value={actionWeapon?.id || ''} onChange={e => setWeapon(e.target.value)}>{actor.weapons?.map(w => <option key={w.id} value={w.id}>{w.name} · {w.id === 'unarmed' ? '徒手' : w.id.slice(-8)}</option>)}</select>
      {actionWeapon?.kind === 'firearm' && <><span>基础射程：{actionWeapon.base_range || '旧资料未记录'}</span><select aria-label="射击距离" value={rangeBand} onChange={e => setRangeBand(e.target.value)}><option value="base">基础射程（常规）</option><option value="long">两倍射程（困难）</option><option value="extreme">四倍射程（极难）</option><option value="point_blank">抵近（DEX/5英尺，奖励骰）</option></select></>}
      {actor.autonomy_reason && <p role="status">{actor.autonomy_reason}</p>}
      <button disabled={busy || actorBlocked || !target || !combat.order.includes(target) || combat.participants[target]?.incapacitated} onClick={() => void act('attack')}>攻击</button><button disabled={busy || actorBlocked || !target} onClick={() => void act('first_aid')}>急救</button><button disabled={busy || actorBlocked} onClick={() => void act('reload')}>装填</button><button disabled={busy || actorBlocked} onClick={() => void act('pass')}>结束行动</button><button disabled={busy || actorBlocked} onClick={() => void act('end')}>脱离战斗</button>
    </div>}
    {!combat.active && !pending && healers.length > 0 && <details><summary>治疗伤势</summary><div className="action-row">
      <select aria-label="治疗行动者" value={healerId} onChange={e => setHealer(e.target.value)}><option value="">选择行动者</option>{healers.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
      <select aria-label="治疗对象" value={target} onChange={e => setTarget(e.target.value)}><option value="">选择伤员</option>{participants.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
      {combat.participants[healerId]?.autonomy_reason && <p role="status">{combat.participants[healerId].autonomy_reason}</p>}
      <button disabled={busy || healerBlocked || room.status !== 'running' || !healerId || !target} onClick={() => void treat('first_aid')}>急救</button>
      <button disabled={busy || healerBlocked || room.status !== 'running' || !healerId || !target} onClick={() => void treat('medicine')}>医学治疗</button>
    </div></details>}
    {room.is_host && !combat.active && <details><summary>主机：准备基础战斗数据</summary><form onSubmit={e => {
      e.preventDefault()
      if (!template) return
      const skills = { brawl: stats.brawl, dodge: stats.dodge, ...(template.skill !== 'brawl' ? { [template.skill]: weaponSkills[template.skill] } : {}) }
      const body = member ? { member_id: member, armor: stats.armor } : { npc: { id: requestId(), label: name, scene_id: room.game?.module?.scene.id, team: 'enemy', attributes: { dex: stats.dex, con: stats.con }, skills, hp: stats.hp, hp_max: stats.hp, armor: stats.armor, source: reason } }
      void command('/combat/setup', { ...body, weapon_loadout: [{ template_id: loadout, ammo: template.kind === 'firearm' ? ammo : 0, reserve: template.kind === 'firearm' ? reserve : 0, ready: template.kind === 'firearm' && ready }], expected_revision: room.revision, reason })
    }}><label>角色<select value={member} onChange={e => setMember(e.target.value)}><option value="">主机明确创建 NPC／敌人</option>{room.members.filter(m => m.slot_id).map(m => <option key={m.id} value={m.id}>{m.display_name}（沿用角色卡）</option>)}</select></label>
      {catalogError && <p role="alert">{catalogError}</p>}
      {!member && <><label>名称<input required value={name} onChange={e => setName(e.target.value)} /></label>{(['hp', 'dex', 'con', 'brawl', 'dodge'] as const).map(k => <label key={k}>{{ hp: '当前／最大 HP', dex: 'DEX', con: 'CON', brawl: '斗殴', dodge: '闪避' }[k]}<input type="number" min={k === 'hp' ? 1 : 0} max={999} value={stats[k]} onChange={e => setStats({ ...stats, [k]: Number(e.target.value) })} /></label>)}</>}
      <label>武器<select aria-label="准备武器" value={loadout} onChange={e => { setLoadout(e.target.value); setAmmo(0); setReserve(0); setReady(false) }}>{catalog.map(c => <option key={c.id} value={c.id}>{c.name}（{c.weapon_template?.damage}）</option>)}</select></label>
      <EquipmentFacts item={selected} />
      {!member && template && template.skill !== 'brawl' && <label>{selected?.skill_name} 数值<input aria-label="NPC武器技能" required type="number" min={0} max={999} step={1} value={weaponSkills[template.skill] ?? ''} onChange={e => setWeaponSkills({ ...weaponSkills, [template.skill]: Number(e.target.value) })} /></label>}
      {member && <p>调查员使用冻结角色技能；此入口明确配置额外战斗资料，原有实物库存仍按实际持有权显示。</p>}
      {template?.kind === 'firearm' && <><label>已装弹<input type="number" min={0} max={template.capacity} value={ammo} onChange={e => setAmmo(Number(e.target.value))} /></label><label>备弹<input type="number" min={0} max={1000} value={reserve} onChange={e => setReserve(Number(e.target.value))} /></label><label><input type="checkbox" checked={ready} onChange={e => setReady(e.target.checked)} />枪已准备好</label></>}
      <label>护甲<input type="number" min={0} value={stats.armor} onChange={e => setStats({ ...stats, armor: Number(e.target.value) })} /></label><label>资料来源／主机创建依据<input required maxLength={300} value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={busy || !!pending || !template}>保存战斗资料</button>
    </form></details>}
  </section>
}
