import type { Experience } from '../../api/characters'
import { requestId } from '../../api/session'

export default function MythosExperience({ value: v, onChange }: { value: NonNullable<Experience['mythos']>; onChange: (v: NonNullable<Experience['mythos']>) => void }) {
  const change = (fields: Partial<typeof v>) => onChange({ ...v, ...fields })
  return <div data-testid="mythos-experience">
    <p>神话值由 KP 决定；1D10+5 只是可选建议。此包无普通技能点、额外职业名额或免疫。</p>
    <label>知识取得方式<select id="mythos-knowledge" value={v.knowledge} onChange={e => change({ knowledge: e.target.value as typeof v.knowledge })}><option value="reading">读书／学术研究</option><option value="direct">实际经历</option></select></label>
    <label>初始相信者状态<select id="mythos-belief" value={v.belief} onChange={e => change({ belief: e.target.value as typeof v.belief })}><option value="believer">相信者（等额扣 SAN）</option><option value="unbeliever">不信者（读书来源；仍受 SAN 上限约束）</option></select></label>
    <label>初始神话方案<select id="mythos-method" value={v.method} onChange={e => change({ method: e.target.value as typeof v.method, value: e.target.value === 'manual' ? 0 : null })}><option value="manual">KP 手定</option><option value="suggested_roll">选择建议骰 1D10+5</option></select></label>
    {v.method === 'manual' ? <label>KP 决定的数值<input id="mythos-value" type="number" min={0} max={99} value={v.value ?? ''} onChange={e => change({ value: e.target.value === '' ? null : Number(e.target.value) })} /></label> : <p>首次保存掷一次初始神话骰；撤销、重选、切换方案及修改资料均保留原骰。导入缺失的原骰不会补掷。</p>}
    {v.backgrounds.map((b, i) => <div key={i}><label>神话背景 {i + 1} 类型<select id={`mythos-background-kind-${i}`} value={b.kind} onChange={e => change({ backgrounds: v.backgrounds.map((b, n) => n === i ? { ...b, kind: e.target.value as typeof b.kind } : b) })}><option value="scar">伤疤／疤痕</option><option value="phobia">恐惧症</option><option value="mania">躁狂症</option><option value="encounter">遭遇的怪异存在</option></select></label><label>背景 {i + 1} 内容<textarea id={`mythos-background-${i}`} maxLength={1000} value={b.detail} onChange={e => change({ backgrounds: v.backgrounds.map((b, n) => n === i ? { ...b, detail: e.target.value } : b) })} /></label></div>)}
    <p>初始法术可不选。候选需 KP 核对来源并许可；本阶段仅作已知记录，尚不支持施放。</p>
    {v.spells.map((s, i) => <div key={s.id}>
      <label>法术名称<input id={`mythos-spell-name-${i}`} maxLength={120} value={s.name} onChange={e => change({ spells: v.spells.map((s, n) => n === i ? { ...s, name: e.target.value } : s) })} /></label>
      <label>法术来源／页码<input id={`mythos-spell-source-${i}`} maxLength={500} value={s.source} onChange={e => change({ spells: v.spells.map((s, n) => n === i ? { ...s, source: e.target.value } : s) })} /></label>
      <button type="button" onClick={() => change({ spells: v.spells.filter((_, n) => n !== i) })}>移除此法术候选</button>
    </div>)}
    <button id="mythos-add-spell" type="button" disabled={v.spells.length >= 50} onClick={() => change({ spells: [...v.spells, { id: requestId(), name: '', source: '', acquisition: 'experience_mythos' }] })}>添加法术候选记录</button>
  </div>
}
