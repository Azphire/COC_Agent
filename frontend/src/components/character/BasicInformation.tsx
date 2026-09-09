import type { BasicFields } from '../../api/characters'

export default function BasicInformation({ value, onChange, ageLocked = false, ageRange }: {
  value: BasicFields; onChange: (value: BasicFields) => void; ageLocked?: boolean
  ageRange?: { minimum: number; maximum: number }
}) {
  const minimum = ageRange?.minimum ?? 1
  const maximum = ageRange?.maximum ?? 150
  return <section>
    <h2>基本信息</h2>
    <div className="field-grid">
      <label>角色姓名
        <input id="character-name" value={value.name} maxLength={120}
          onChange={event => onChange({ ...value, name: event.target.value })} />
        {!value.name.trim() && <small className="field-error">请填写角色姓名</small>}
      </label>
      <label>玩家名称（可空）
        <input value={value.player_name || ''} maxLength={120}
          onChange={event => onChange({ ...value, player_name: event.target.value || null })} />
      </label>
      <label>年龄{ageRange ? `（${minimum}–${maximum}岁）` : '（可空）'}
        <input id="character-age" type="number" min={minimum} max={maximum} value={value.age ?? ''} readOnly={ageLocked}
          onChange={event => onChange({ ...value, age: event.target.value === '' ? null : Number(event.target.value) })} />
        {((value.age === null && ageRange) || (value.age !== null && (!Number.isInteger(value.age) || value.age < minimum || value.age > maximum)))
          && <small className="field-error">年龄须为 {minimum}–{maximum} 的整数</small>}
        {ageLocked && <small>创建时已锁定年龄</small>}
      </label>
    </div>
  </section>
}
