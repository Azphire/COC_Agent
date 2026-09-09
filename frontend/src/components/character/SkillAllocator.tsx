import type { Character, EditableFields, RuleSet } from '../../api/characters'

export default function SkillAllocator({ ruleset, character, value, onChange }: {
  ruleset: RuleSet; character: Character; value: EditableFields
  onChange: (value: EditableFields) => void
}) {
  const occupation = ruleset.occupations.find(item => item.key === value.occupation)
  const allowed = new Set([...(occupation?.fixed_skills || []), ...value.selected_occupation_skills])
  if (occupation?.credit_rating_minimum !== null && occupation?.credit_rating_minimum !== undefined) allowed.add('credit_rating')
  const names = (keys: string[]) => keys.map(key => ruleset.skills.find(skill => skill.key === key)?.display_name || key).join('、')
  return <section>
    <h2>职业与技能</h2>
    <label>职业
      <select id="occupation" value={value.occupation || ''} onChange={event => onChange({
        ...value, occupation: event.target.value || null, selected_occupation_skills: [], occupation_skills: {},
      })}>
        <option value="">请选择职业</option>
        {ruleset.occupations.map(item => <option key={item.key} value={item.key}>{item.display_name}</option>)}
      </select>
    </label>
    {occupation && <>
      <p>固定职业技能：{names(occupation.fixed_skills)}</p>
      {occupation.credit_rating_minimum !== null && <p>信用评级须为 {occupation.credit_rating_minimum}–{occupation.credit_rating_maximum}，可使用职业技能点。</p>}
      <p>另选 {occupation.required_selection_count} 项职业技能（已选 {value.selected_occupation_skills.length} 项）：</p>
      <div className="choice-row">
        {occupation.selectable_skills.map(key => <label key={key}>
          <input type="checkbox" id={`occupation-choice-${key}`} checked={value.selected_occupation_skills.includes(key)}
            onChange={event => {
              const selected = event.target.checked ? [...value.selected_occupation_skills, key]
                : value.selected_occupation_skills.filter(item => item !== key)
              onChange({ ...value, selected_occupation_skills: selected,
                occupation_skills: { ...value.occupation_skills, [key]: { points: 0 } } })
            }} />{names([key])}
        </label>)}
      </div>
      {value.selected_occupation_skills.length !== occupation.required_selection_count
        && <p className="field-error">请选择规定数量的职业技能</p>}
    </>}
    <p>后端已保存余额：职业 <strong>{character.remaining_points.occupation}</strong> 点，
      兴趣 <strong>{character.remaining_points.interest}</strong> 点。两类点数分别记账，保存后更新。</p>
    <div className="table-scroll"><table>
      <thead><tr><th>技能 / 基础值</th><th>职业投入</th><th>兴趣投入</th><th>合计 / 上限</th></tr></thead>
      <tbody>{ruleset.skills.map(skill => {
        const occupational = value.occupation_skills[skill.key]?.points || 0
        const interest = value.interest_skills[skill.key]?.points || 0
        const base = character.skill_base_values[skill.key] ?? skill.base_value
        const total = base + occupational + interest
        return <tr key={skill.key}>
          <th scope="row">{skill.display_name}<small>{skill.category} · 基础 {base}</small></th>
          {(['occupation_skills', 'interest_skills'] as const).map(field => {
            const points = value[field][skill.key]?.points || 0
            const invalid = points < 0 || !Number.isInteger(points) || total > skill.maximum
            return <td key={field}>
              <input aria-label={`${skill.display_name}${field === 'occupation_skills' ? '职业' : '兴趣'}投入`}
                id={`${field}-${skill.key}`} type="number" min={0} max={skill.maximum - base}
                disabled={!skill.allocatable || (field === 'occupation_skills' && !allowed.has(skill.key))} value={points} aria-invalid={invalid}
                onChange={event => onChange({ ...value, [field]: { ...value[field], [skill.key]: { points: Number(event.target.value) } } })} />
              {invalid && <small className="field-error">须为非负整数且合计不超限</small>}
              {character.validation.issues.filter(issue => issue.field === `${field}.${skill.key}`).map((issue, index) =>
                <small className="field-error" key={index}>上次保存：{issue.message}</small>)}
            </td>
          })}
          <td>{total} / {skill.maximum}
            <small>已保存困难：{character.skill_half_values[skill.key]}<br />极难：{character.skill_fifth_values[skill.key]}</small>
          </td>
        </tr>
      })}</tbody>
    </table></div>
  </section>
}
