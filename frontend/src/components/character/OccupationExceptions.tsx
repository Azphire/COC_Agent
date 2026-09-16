import type { Character, EditableFields, RuleSet } from '../../api/characters'
import OccupationExceptionSummary from './OccupationExceptionSummary'

export default function OccupationExceptions({ character, value, ruleset, onChange }: {
  character: Character; value: EditableFields; ruleset: RuleSet; onChange: (value: EditableFields) => void
}) {
  const occupation = ruleset.occupations.find(o => o.key === value.occupation)
  const replacement = value.occupation_skill_replacement, mythos = value.initial_mythos_proposal
  const originals = [...(occupation?.fixed_skills ?? []), ...Object.values(value.occupation_group_choices).flat()]
  const name = (key: string) => ruleset.skills.find(s => s.key === key)?.display_name ?? key
  const issues = character.validation.issues.filter(i => i.field.startsWith('occupation_exception_approvals.'))
  const unsaved = (['occupation', 'era', 'occupation_group_choices', 'occupation_skill_replacement', 'initial_mythos_proposal', 'attributes'] as const)
    .some(k => JSON.stringify(value[k]) !== JSON.stringify(character[k]))
  if (!occupation?.skill_replacement && !occupation?.initial_mythos && !replacement && !mythos) return null
  return <fieldset data-testid="occupation-exceptions"><legend>职业特例方案</legend>
    {occupation?.skill_replacement && <>
      <p>{occupation.skill_replacement.note} 来源：{occupation.skill_replacement.source}</p>
      <label>替换原职业技能<select id="replacement-original" value={replacement?.original_skill ?? ''} onChange={e => onChange({ ...value,
        occupation_skill_replacement: e.target.value ? { original_skill: e.target.value, replacement_skill: occupation.skill_replacement!.target_skill, reason: replacement?.reason ?? '' } : null })}>
        <option value="">不替换／撤销方案</option>{[...new Set(originals)].map(k => <option key={k} value={k}>{name(k)} → 催眠</option>)}
      </select></label>
      {replacement && <label>替换理由<textarea id="replacement-reason" maxLength={2000} value={replacement.reason} onChange={e => onChange({ ...value, occupation_skill_replacement: { ...replacement, reason: e.target.value } })} /></label>}
      <p>改动或撤销后请保存重算；失去职业资格的点数须退回，不会转为兴趣点。</p>
    </>}
    {occupation?.initial_mythos && <>
      <label><input id="initial-mythos-enabled" type="checkbox" checked={!!mythos} onChange={e => onChange({ ...value,
        initial_mythos_proposal: e.target.checked ? { source: 'occultist', value: 0, reason: '' } : null,
        occupation_group_choices: { ...value.occupation_group_choices, [occupation.initial_mythos!.selection_group]: e.target.checked ? ['cthulhu_mythos'] : [] },
      })} />申请初始神话（占个人特长名额）</label>
      <p>{occupation.initial_mythos.note}</p>
      {mythos && <>
        <label>KP确定的初始神话值<input id="initial-mythos-value" type="number" min={1} max={99} value={mythos.value} onChange={e => onChange({ ...value, initial_mythos_proposal: { ...mythos, value: Number(e.target.value) } })} /></label>
        {mythos.value > occupation.initial_mythos.recommended_maximum && <p>超过建议 {occupation.initial_mythos.recommended_maximum}%，请 KP 明确判断；此建议不是强制上限。</p>}
        <label>获取理由<textarea id="initial-mythos-reason" maxLength={2000} value={mythos.reason} onChange={e => onChange({ ...value, initial_mythos_proposal: { ...mythos, reason: e.target.value } })} /></label>
      </>}
    </>}
    <OccupationExceptionSummary character={character} rules={ruleset} />
    {issues.map(i => {
      const key = i.field.replace('occupation_exception_approvals.', '')
      return <label key={key}><input type="checkbox" id={`approve-${key}`} disabled={unsaved} checked={value.approve_occupation_exceptions?.includes(key) ?? false}
        onChange={e => onChange({ ...value, approve_occupation_exceptions: e.target.checked ? [...(value.approve_occupation_exceptions ?? []), key] : value.approve_occupation_exceptions?.filter(k => k !== key) })} />主机明确许可：{i.message}</label>
    })}
    <p>请先保存方案查看重算结果，再核准并保存。职业、方案、选择或规则变化使旧许可失效；导出许可在导入时清除。</p>
  </fieldset>
}
