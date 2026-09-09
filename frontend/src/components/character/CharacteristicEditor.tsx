import type { Character, EditableFields, RuleSet } from '../../api/characters'

export default function CharacteristicEditor({ ruleset, character, values, onChange }: {
  ruleset: RuleSet; character: Character; values: EditableFields['attributes']
  onChange: (values: EditableFields['attributes']) => void
}) {
  const random = character.creation_mode === 'random'
  const remaining = (ruleset.points?.attribute_pool || 0) - ruleset.attributes.reduce((sum, item) =>
    sum + ((values[item.key]?.value ?? item.point_buy_minimum ?? item.minimum)
      - (ruleset.points?.attribute_cost_origin === 'zero' ? 0 : item.minimum)) * item.point_buy_cost, 0)
  return <section>
    <h2>属性</h2>
    {random ? <p className="hint">整组属性已生成并保存，不提供逐项重掷。</p>
      : <p>购点预估剩余：<strong data-testid="estimated-points">{remaining}</strong>；
        后端已保存余额：{character.remaining_points.attributes}。
        {ruleset.points?.attribute_cost_origin === 'zero' ? '八项基础属性合计460点，年龄调整另算。' : '从属性下限开始计费。'}</p>}
    {remaining < 0 && !random && <p role="alert">属性点数超支，请降低属性后保存。</p>}
    <div className="field-grid">
      {ruleset.attributes.map(item => {
        const value = values[item.key]?.value
        const minimum = random ? item.minimum : item.point_buy_minimum ?? item.minimum
        const invalid = value === undefined || !Number.isInteger(value) || value < minimum || value > item.maximum
        return <label key={item.key}>{item.display_name}
          <input id={`attribute-${item.key}`} type="number" readOnly={random}
            min={minimum} max={item.maximum} value={value ?? ''}
            aria-invalid={invalid} onChange={event => onChange({ ...values, [item.key]: { value: Number(event.target.value) } })} />
          <small>{minimum}–{item.maximum} · {random ? `${item.random_formula} × ${item.random_multiplier}` : `每级 ${item.point_buy_cost} 点`}</small>
          {ruleset.age_rules && <small>已保存的年龄调整后值：{character.effective_attributes[item.key] ?? '—'}</small>}
          {ruleset.age_rules && <small>困难：{character.derived_values[`${item.key}_half`]} · 极难：{character.derived_values[`${item.key}_fifth`]}</small>}
          {invalid && <small className="field-error">请输入范围内的整数</small>}
        </label>
      })}
    </div>
  </section>
}
