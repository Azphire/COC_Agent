import { useState } from 'react'
import type { Character, CustomSpecialization, EditableFields, RuleSet, SkillGroup } from '../../api/characters'
import { characterSkills, normalizedName, specializationLabels } from './specializations'

export default function SkillAllocator({ ruleset, character, value, onChange }: {
  ruleset: RuleSet; character: Character; value: EditableFields
  onChange: (value: EditableFields) => void
}) {
  const [occupationSearch, setOccupationSearch] = useState('')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [customGroup, setCustomGroup] = useState<CustomSpecialization['group']>('language')
  const [customName, setCustomName] = useState('')
  const [customError, setCustomError] = useState('')
  const custom = value.custom_specializations ?? []
  const skills = characterSkills(ruleset, custom)
  function addCustom() {
    const name = customName.normalize('NFKC').trim()
    if (!name || name.length > 40 || /[\p{C}()<>{}[\]]/u.test(name)) {
      setCustomError('请输入 1–40 字的专业名称，不含括号或控制字符。'); return
    }
    if (skills.some(s => s.specialization_group === customGroup && normalizedName(s.display_name.split('（').at(-1)!.replace(/）$/, '')) === normalizedName(name))) {
      setCustomError('该专业已存在，请在技能列表中选择。'); return
    }
    const id = `custom_${customGroup}_${Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('')}`
    onChange({ ...value, custom_specializations: [...custom, { id, group: customGroup, name }], selected_specializations: [...value.selected_specializations, id] })
    setCustomName(''); setCustomError('')
  }
  function removeCustom(id: string) {
    const occupationSkills = { ...value.occupation_skills }, interestSkills = { ...value.interest_skills }
    delete occupationSkills[id]; delete interestSkills[id]
    onChange({ ...value, custom_specializations: custom.filter(s => s.id !== id),
      selected_specializations: value.selected_specializations.filter(k => k !== id),
      occupation_group_choices: Object.fromEntries(Object.entries(value.occupation_group_choices).map(([g, keys]) => [g, keys.filter(k => k !== id)])),
      occupation_skills: occupationSkills, interest_skills: interestSkills })
  }
  const occupation = ruleset.occupations.find(o => o.key === value.occupation)
  const allowed = new Set([...(occupation?.fixed_skills ?? []), ...value.selected_occupation_skills,
    ...Object.values(value.occupation_group_choices).flat(), 'credit_rating'])
  const name = (key: string) => skills.find(s => s.key === key)?.display_name ?? key
  const options = (g: SkillGroup) => skills.filter(s => s.allocatable && s.key !== 'credit_rating'
    && s.eras.includes(value.era) && (g.any_skill || g.skills.includes(s.key) || !!s.specialization_group && g.specialization_groups.includes(s.specialization_group)))
  const formula = occupation?.point_formula
  const attributes = character.effective_attributes
  const occupationPool = formula ? Object.entries(formula.fixed).reduce((sum, [k, n]) => sum + (attributes[k] ?? 0) * n, 0)
    + (formula.choice_attributes.includes(value.occupation_attribute ?? '') ? (attributes[value.occupation_attribute!] ?? 0) * formula.choice_multiplier : 0)
    : character.remaining_points.occupation + Object.values(character.occupation_skills).reduce((sum, a) => sum + a.points, 0)
  const interestPool = character.remaining_points.interest + Object.values(character.interest_skills).reduce((sum, a) => sum + a.points, 0)
  const balances = { occupation: occupationPool - Object.values(value.occupation_skills).reduce((sum, a) => sum + a.points, 0),
    interest: interestPool - Object.values(value.interest_skills).reduce((sum, a) => sum + a.points, 0) }
  return <section>
    <h2>职业与技能</h2>
    <div className="field-grid">
      <label>年代<select id="character-era" value={value.era} onChange={e => onChange({ ...value, era: e.target.value as EditableFields['era'] })}>
        <option value="1920s">1920 年代</option><option value="modern">现代</option>
      </select></label>
      <label>检索职业<input type="search" value={occupationSearch} onChange={e => setOccupationSearch(e.target.value)} placeholder="职业名称或 ID" /></label>
      <label>职业<select id="occupation" value={value.occupation ?? ''} onChange={e => onChange({ ...value,
        occupation: e.target.value || null, occupation_attribute: null, selected_occupation_skills: [],
        occupation_group_choices: {}, occupation_skills: {} })}>
        <option value="">请选择职业</option>
        {ruleset.occupations.filter(o => o.key === value.occupation || o.eras.includes(value.era) && (o.display_name + o.key).toLowerCase().includes(occupationSearch.toLowerCase())).map(o =>
          <option key={o.key} value={o.key}>{o.display_name}</option>)}
      </select></label>
    </div>
    {!!Object.keys(ruleset.custom_specialization_templates ?? {}).length && <fieldset>
      <legend>自定义专业</legend>
      <p>分别分配和检定；基础值和上限沿用所选类别。已有专业请直接选择。删除会退回投入点数并清理职业分组选择。</p>
      <div className="field-grid">
        <label>专业类别<select id="custom-skill-group" value={customGroup} onChange={e => setCustomGroup(e.target.value as CustomSpecialization['group'])}>
          {Object.keys(ruleset.custom_specialization_templates).map(g => <option key={g} value={g}>{specializationLabels[g as CustomSpecialization['group']]}</option>)}
        </select></label>
        <label>专业名称<input id="custom-skill-name" value={customName} maxLength={40} onChange={e => setCustomName(e.target.value)} placeholder="例如：葡萄牙语、陶艺、地球物理学" /></label>
      </div>
      <button type="button" disabled={custom.length >= 50} onClick={addCustom}>添加专业</button>
      {customError && <p role="alert">{customError}</p>}
      {custom.map(s => <div key={s.id} className="choice-row">
        <label>{specializationLabels[s.group]}<input aria-label={`修改${s.name}名称`} value={s.name} maxLength={40}
          onChange={e => onChange({ ...value, custom_specializations: custom.map(c => c.id === s.id ? { ...c, name: e.target.value } : c) })} /></label>
        <button type="button" onClick={() => removeCustom(s.id)}>删除 {s.name}</button>
      </div>)}
      {character.validation.issues.filter(i => i.field.startsWith('custom_specializations')).map((i, n) => <p className="field-error" key={n}>{i.message}</p>)}
    </fieldset>}
    {occupation && <>
      <p>固定职业技能：{occupation.fixed_skills.map(name).join('、') || '无'}。{occupation.source}</p>
      <p>职业点数：{formula?.display ?? '按规则配置计算'}；信用评级 {occupation.credit_rating_minimum}–{occupation.credit_rating_maximum}。</p>
      {!!formula?.choice_attributes.length && <label>职业点数属性<select id="occupation-attribute" value={value.occupation_attribute ?? ''}
        onChange={e => onChange({ ...value, occupation_attribute: e.target.value || null })}>
        <option value="">选择属性</option>{formula.choice_attributes.map(k => <option key={k} value={k}>{k.toUpperCase()}（{attributes[k]}）</option>)}
      </select></label>}
      {occupation.skill_groups.map(g => <fieldset key={g.key} className="skill-choice-group">
        <legend>{g.display_name} · 选 {g.count} 项（已选 {(value.occupation_group_choices[g.key] ?? []).length}）</legend>
        <select multiple aria-label={g.display_name} id={`group-${g.key}`} size={Math.min(6, options(g).length)}
          value={value.occupation_group_choices[g.key] ?? []} onChange={e => onChange({ ...value,
            occupation_group_choices: { ...value.occupation_group_choices, [g.key]: Array.from(e.target.selectedOptions, o => o.value) } })}>
          {options(g).map(s => <option key={s.key} value={s.key}>{s.display_name}</option>)}
        </select>
        <small>多选可按住 Ctrl / Command；各组与固定项不能重复计数。</small>
      </fieldset>)}
      {!!occupation.required_selection_count && <>
        <p>另选 {occupation.required_selection_count} 项（已选 {value.selected_occupation_skills.length}）：</p>
        <div className="choice-row">{occupation.selectable_skills.map(k => <label key={k}>
          <input id={`occupation-choice-${k}`} type="checkbox" checked={value.selected_occupation_skills.includes(k)} onChange={e => onChange({ ...value,
            selected_occupation_skills: e.target.checked ? [...value.selected_occupation_skills, k] : value.selected_occupation_skills.filter(v => v !== k) })} />{name(k)}
        </label>)}</div>
      </>}
    </>}
    <details><summary>选择额外专业（兴趣技能）</summary>
      <p>每个专业分别分配和检定；职业固定或分组所选的专业已自动加入下表。</p>
      {[...new Set(skills.map(s => s.specialization_group).filter(Boolean))].map(group => <fieldset key={group}>
        <legend>{({ language: '语言', art_craft: '艺术与手艺', science: '科学', fighting: '格斗', firearms: '射击', pilot: '驾驶', survival: '生存', lore: '学问' } as Record<string, string>)[group!] ?? group}</legend>
        <div className="choice-row">{skills.filter(s => s.specialization_group === group && s.eras.includes(value.era)).map(s => <label key={s.key}>
          <input type="checkbox" id={`specialization-${s.key}`} checked={allowed.has(s.key) || value.selected_specializations.includes(s.key)} disabled={allowed.has(s.key)}
            onChange={e => onChange({ ...value, selected_specializations: e.target.checked ? [...value.selected_specializations, s.key] : value.selected_specializations.filter(k => k !== s.key) })} />{s.display_name}
        </label>)}</div>
      </fieldset>)}
    </details>
    <p role="status">当前分配余额：职业 <strong>{balances.occupation}</strong> / {occupationPool}，兴趣 <strong>{balances.interest}</strong> / {interestPool}。
      <small>公式使用已保存的年龄调整后属性；修改属性后请保存重算。后端已保存余额：职业 {character.remaining_points.occupation}，兴趣 {character.remaining_points.interest}。</small></p>
    <div className="field-grid"><label>搜索技能<input type="search" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label>技能分类<select value={category} onChange={e => setCategory(e.target.value)}><option value="">全部分类</option>
        {[...new Set(skills.map(s => s.category))].map(c => <option key={c}>{c}</option>)}
      </select></label></div>
    <div className="table-scroll"><table><thead><tr><th>技能 / 基础值</th><th>职业投入</th><th>兴趣投入</th><th>合计 / 上限</th></tr></thead>
      <tbody>{skills.filter(s => (!category || s.category === category) && (s.display_name + s.key).toLowerCase().includes(search.toLowerCase())
        && (!s.specialization_group || allowed.has(s.key) || value.selected_specializations.includes(s.key) || value.occupation_skills[s.key]?.points || value.interest_skills[s.key]?.points)).map(s => {
        const base = character.skill_base_values[s.key] ?? s.base_value
        const total = base + (value.occupation_skills[s.key]?.points ?? 0) + (value.interest_skills[s.key]?.points ?? 0)
        return <tr key={s.key}><th scope="row">{s.display_name}<small>{s.category} · 基础 {base}</small></th>
          {(['occupation_skills', 'interest_skills'] as const).map(field => <td key={field}>
            <input id={`${field}-${s.key}`} aria-label={`${s.display_name}${field === 'occupation_skills' ? '职业' : '兴趣'}投入`} type="number" min={0} max={s.maximum - base}
              disabled={!s.allocatable || !s.eras.includes(value.era) || field === 'occupation_skills' && !allowed.has(s.key)} value={value[field][s.key]?.points ?? 0}
              onChange={e => onChange({ ...value, [field]: { ...value[field], [s.key]: { points: Number(e.target.value) } } })} />
            {character.validation.issues.filter(i => i.field === `${field}.${s.key}`).map((i, n) => <small className="field-error" key={n}>{i.message}</small>)}
          </td>)}<td>{total} / {s.maximum}<small>困难 {Math.floor(total / 2)} · 极难 {Math.floor(total / 5)}</small></td></tr>
      })}</tbody></table></div>
  </section>
}
