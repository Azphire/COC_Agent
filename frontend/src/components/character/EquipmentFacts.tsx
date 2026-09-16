import type { EquipmentDefinition } from '../../api/characters'

export default function EquipmentFacts({ item }: { item?: EquipmentDefinition }) {
  if (!item) return null
  const w = item.weapon_template
  return <div className="hint">
    <p>{item.category} · {item.eras.map(e => e === 'modern' ? '现代' : '1920年代').join('／')} · {item.description}</p>
    {w && <p>{item.skill_name} · 伤害 {w.damage}{w.kind === 'melee' && w.uses_db ? '＋DB' : ''} · {w.impale ? '可贯穿' : '不贯穿'} · 基础射程 {w.base_range || '未记录'}{w.kind === 'firearm' && <> · 容量 {w.capacity} · 故障 {w.malfunction}</>}</p>}
    <details><summary>装备来源</summary>{item.source}</details>
  </div>
}
