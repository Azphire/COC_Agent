import type { Character, EditableFields, Experience, RuleSet } from '../../api/characters'
import { characterSkills } from './specializations'
import ExperienceSummary from './ExperienceSummary'

export default function ExperiencePackages({ character, ruleset, value, onChange }: {
  character: Character; ruleset: RuleSet; value: EditableFields; onChange: (value: EditableFields) => void
}) {
  if (!ruleset.experience_packages?.length) return null
  const selection = value.experience
  const policy = ruleset.experience_packages.find(p => p.key === selection?.package)
  const variant = selection && policy?.variants[selection.variant]
  const skills = characterSkills(ruleset, value.custom_specializations)
  const name = (key: string) => skills.find(s => s.key === key)?.display_name ?? key
  const change = (update: Partial<Experience>) => onChange({ ...value, experience: { ...selection!, ...update }, approve_experience: undefined })
  const allowed = new Set([...(variant?.fixed_skills ?? []), ...Object.values(selection?.choices ?? {}).flat(),
    ...skills.filter(s => s.specialization_group && variant?.specialty_groups.includes(s.specialization_group) && value.selected_specializations.includes(s.key)).map(s => s.key)])
  const spent = Object.values(value.experience_skills ?? {}).reduce((sum, a) => sum + a.points, 0)
  const issues = character.validation.issues.filter(i => i.field.startsWith('experience') || i.field === 'remaining_points.experience')
  return <section data-testid="experience-packages"><h2>可选经历包</h2>
    <p>最多选择一个，须 KP 同意。职业、兴趣、经历点分别记账；所有结果以保存后的服务端重算为准。</p>
    <label>经历包<select id="experience-package" value={selection?.package ?? ''} onChange={e => {
      const next = ruleset.experience_packages.find(p => p.key === e.target.value)
      onChange({ ...value, experience: next ? { package: next.key, variant: Object.keys(next.variants)[0], history: '', background_kind: 'scar', background_detail: '', choices: {}, war_year: null, scenario_year: null, age_at_war: null } : null, approve_experience: undefined })
    }}><option value="">不选择</option>{ruleset.experience_packages.map(p => <option key={p.key} value={p.key}>{p.display_name}</option>)}</select></label>
    {policy && selection && <>
      <p>{policy.source}。{policy.qualification}{policy.minimum_age && ` 最低 ${policy.minimum_age} 岁，不自动增加年龄。`}</p>
      <p>额外 {policy.points} 点，初始 SAN 减少 {policy.san_loss}；首次保存所选包时掷骰，撤销再选及改资料复用原骰。</p>
      <label>经历身份<select id="experience-variant" value={selection.variant} onChange={e => change({ variant: e.target.value, choices: {} })}>{Object.entries(policy.variants).map(([k, v]) => <option key={k} value={k}>{v.display_name}</option>)}</select></label>
      <label>资格经历<textarea id="experience-history" maxLength={2000} value={selection.history} onChange={e => change({ history: e.target.value })} /></label>
      <label>新增背景类型<select id="experience-background-kind" value={selection.background_kind} onChange={e => change({ background_kind: e.target.value as Experience['background_kind'] })}><option value="scar">伤疤／疤痕</option><option value="phobia">恐惧症</option><option value="mania">躁狂症</option></select></label>
      <label>与经历相关的背景<textarea id="experience-background" maxLength={1000} value={selection.background_detail} onChange={e => change({ background_detail: e.target.value })} /></label>
      {selection.package === 'war' && <><p>当前年龄应为参战年龄＋年份差。以模组时最终年龄创建草稿，沿既有年龄属性／EDU／幸运流程计算并锁定；包切换不加龄或重掷。1920年代最可能参加一战是建议。</p><div className="field-grid">{(['war_year', 'scenario_year', 'age_at_war'] as const).map((k, i) => <label key={k}>{['参战年份', '模组年份', '参战年龄'][i]}<input id={`experience-${k}`} type="number" min={1} value={selection[k] ?? ''} onChange={e => change({ [k]: e.target.value ? Number(e.target.value) : null })} /></label>)}</div></>}
      {variant?.skill_groups.map(g => <label key={g.key}>{g.display_name} · 选 {g.count} 项
        <select multiple id={`experience-group-${g.key}`} value={selection.choices[g.key] ?? []} onChange={e => change({ choices: { ...selection.choices, [g.key]: [...e.target.selectedOptions].map(o => o.value) } })}>
          {skills.filter(s => s.allocatable && s.eras.includes(value.era) && (g.skills.includes(s.key) || !!s.specialization_group && g.specialization_groups.includes(s.specialization_group))).map(s => <option key={s.key} value={s.key}>{s.display_name}</option>)}
        </select></label>)}
      {!!variant?.specialty_groups.length && <p>可投入的专业方向：{variant.specialty_groups.join('、')}；在职业与技能中的“额外专业”选择具体专业，也可使用已添加的自定义专业。</p>}
      <p>经历点剩余 {policy.points - spent} / {policy.points}（编辑预览）。职业与信用范围不增加；技能合计沿用原上限。</p>
    </>}
    {(selection || spent !== 0) && <><button type="button" onClick={() => onChange({ ...value, experience_skills: {}, approve_experience: undefined })}>退回全部经历投入</button>
      <div className="table-scroll"><table><thead><tr><th>经历技能</th><th>经历投入</th><th>保存后实际值</th></tr></thead><tbody>{skills.filter(s => allowed.has(s.key) || value.experience_skills?.[s.key]?.points).map(s => <tr key={s.key}><td>{name(s.key)}{!allowed.has(s.key) && '（已失效，请退点）'}</td><td><input id={`experience_skills-${s.key}`} type="number" min={0} max={s.maximum} value={value.experience_skills?.[s.key]?.points ?? 0} onChange={e => onChange({ ...value, experience_skills: { ...value.experience_skills, [s.key]: { points: Number(e.target.value) } }, approve_experience: undefined })} /></td><td>{character.skill_values[s.key]}</td></tr>)}</tbody></table></div></>}
    <ExperienceSummary character={character} />
    {issues.filter(i => i.code !== 'keeper_approval').map((i, n) => <p role="alert" key={n}>需修正：{i.message}</p>)}
    {issues.filter(i => i.code === 'keeper_approval').map(i => <label key={i.field}><input id="approve-experience" type="checkbox" checked={value.approve_experience?.includes(character.experience!.package) ?? false} onChange={e => onChange({ ...value, approve_experience: e.target.checked ? [character.experience!.package] : [] })} />{i.message}；确认上方服务端技能收益、SAN代价及背景后勾选并保存。</label>)}
  </section>
}
