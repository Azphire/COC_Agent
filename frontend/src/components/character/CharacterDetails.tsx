import { useEffect, useState } from 'react'
import { charactersApi } from '../../api/characters'
import type { Character, EditableFields, EquipmentDefinition } from '../../api/characters'
import EquipmentFacts from './EquipmentFacts'

const labels = { appearance: '外貌', beliefs: '思想与信念', people: '重要之人', places: '意义非凡之地', possessions: '宝贵之物', traits: '特质' }

export default function CharacterDetails({ character, value, onChange }: {
  character: Character; value: EditableFields; onChange: (value: EditableFields) => void
}) {
  const [catalog, setCatalog] = useState<EquipmentDefinition[]>([])
  const [selection, setSelection] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { charactersApi.equipment().then(setCatalog).catch(e => setError(e.message)) }, [])
  const f = character.finances
  return <>
    <section><h2>信用与资产</h2>
      <p>已保存计算：{f.level} · {f.currency} · 现金 {f.cash.toLocaleString()} · 资产 {f.assets.toLocaleString()}{f.assets_lower_bound ? ' 以上' : ''} · 日常消费额度 {f.spending.toLocaleString()}。</p>
      <p className="hint">保存后按信用评级与年代更新。日常额度用于日常支出参考；现金与原有资产不会自动变成模组内随身物品。</p>
      {value.asset_details.map((a, i) => <div className="field-grid" key={i}>
        <label>资产说明<input aria-label={`资产说明${i + 1}`} value={a.description} maxLength={500} onChange={e => onChange({ ...value, asset_details: value.asset_details.map((v, n) => n === i ? { ...v, description: e.target.value } : v) })} /></label>
        <label>估值<input aria-label={`资产估值${i + 1}`} type="number" min={0} value={a.value} onChange={e => onChange({ ...value, asset_details: value.asset_details.map((v, n) => n === i ? { ...v, value: Number(e.target.value) } : v) })} /></label>
        <button type="button" onClick={() => onChange({ ...value, asset_details: value.asset_details.filter((_, n) => i !== n) })}>移除此资产</button>
      </div>)}
      <button type="button" onClick={() => onChange({ ...value, asset_details: [...value.asset_details, { description: '', value: 0 }] })}>添加资产明细</button>
    </section>
    <section><h2>人物背景</h2><div className="field-grid">
      {Object.entries(labels).map(([key, label]) => <label key={key}>{label}<textarea id={`background-${key}`} maxLength={2000} value={value.background[key as keyof typeof labels]}
        onChange={e => onChange({ ...value, background: { ...value.background, [key]: e.target.value } })} /></label>)}
      <label>关键背景联系<select id="key-connection" value={value.background.key_connection ?? ''} onChange={e => onChange({ ...value, background: { ...value.background, key_connection: e.target.value || null } })}>
        <option value="">未选择</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select></label></div><p className="hint">背景随角色完整保存；房间内沿用角色详细资料的可见权限。</p>
    </section>
    <section><h2>原有装备</h2>
      <p className="hint">开团时结算实际持有。《常暗之厢》等仍按模组起始遗失与选择规则处理。初始已装弹／备弹均为每件枪械的发数，默认零。普通物品用途不自动增加技能或恢复HP。</p>
      {error && <p role="alert">{error}</p>}
      <div className="action-row"><select aria-label="装备目录" value={selection} onChange={e => setSelection(e.target.value)}><option value="">自定义普通装备</option>
        {catalog.filter(c => c.eras.includes(value.era)).map(c => <option key={c.id} value={c.id}>{c.category} · {c.name}</option>)}
      </select><button type="button" onClick={() => { const selected = catalog.find(c => c.id === selection); onChange({ ...value, equipment: [...value.equipment, { id: crypto.randomUUID(), catalog_id: selected?.id ?? null, name: selected?.name ?? '自定义装备', quantity: 1, notes: '' }] }) }}>添加装备</button></div>
      <EquipmentFacts item={catalog.find(c => c.id === selection)} />
      {value.equipment.map((entry, i) => {
        const definition = catalog.find(c => c.id === entry.catalog_id)
        const firearm = definition?.weapon_template?.kind === 'firearm' ? definition.weapon_template : null
        return <div key={entry.id}><div className="field-grid">
        <label>装备名称<input aria-label={`装备名称${i + 1}`} readOnly={!!entry.catalog_id} maxLength={100} value={entry.name} onChange={e => onChange({ ...value, equipment: value.equipment.map(v => v.id === entry.id ? { ...v, name: e.target.value } : v) })} /></label>
        <label>数量<input type="number" min={1} max={100} value={entry.quantity} onChange={e => onChange({ ...value, equipment: value.equipment.map(v => v.id === entry.id ? { ...v, quantity: Number(e.target.value) } : v) })} /></label>
        <label>备注<input maxLength={1000} value={entry.notes} onChange={e => onChange({ ...value, equipment: value.equipment.map(v => v.id === entry.id ? { ...v, notes: e.target.value } : v) })} /></label>
        {firearm && <>
          <label>初始已装弹（每件）<input aria-label={`初始已装弹${i + 1}`} type="number" min={0} max={firearm.capacity} step={1} value={entry.initial_ammo ?? 0} onChange={e => onChange({ ...value, equipment: value.equipment.map(v => v.id === entry.id ? { ...v, initial_ammo: Number(e.target.value) } : v) })} /></label>
          <label>初始备弹（每件）<input aria-label={`初始备弹${i + 1}`} type="number" min={0} max={1000} step={1} value={entry.initial_reserve ?? 0} onChange={e => onChange({ ...value, equipment: value.equipment.map(v => v.id === entry.id ? { ...v, initial_reserve: Number(e.target.value) } : v) })} /></label>
        </>}
        <button type="button" onClick={() => onChange({ ...value, equipment: value.equipment.filter(v => v.id !== entry.id) })}>移除此装备</button>
      </div><EquipmentFacts item={definition} /></div>})}
    </section>
  </>
}
