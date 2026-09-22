import { useEffect, useState } from 'react'
import { CharacterApiError, charactersApi } from '../api/characters'
import type { Character, EditableFields, RuleSet, ValidationIssue } from '../api/characters'
import BasicInformation from '../components/character/BasicInformation'
import CharacteristicEditor from '../components/character/CharacteristicEditor'
import SkillAllocator from '../components/character/SkillAllocator'
import ValidationSummary from '../components/character/ValidationSummary'
import JsonTransfer from '../components/character/JsonTransfer'
import CharacterDetails from '../components/character/CharacterDetails'
import { characterSkills } from '../components/character/specializations'
import ExperiencePackages from '../components/character/ExperiencePackages'
import ModuleHandoutEditor from '../components/character/ModuleHandoutEditor'

const empty: EditableFields = {
  name: '', player_name: null, age: 25, occupation: null, selected_occupation_skills: [],
  attributes: {}, occupation_skills: {}, interest_skills: {}, age_deductions: {},
  occupation_attribute: null, occupation_group_choices: {}, selected_specializations: [], era: '1920s',
  custom_specializations: [],
  occupation_skill_replacement: null, initial_mythos_proposal: null,
  experience: null, experience_skills: {},
  background: { appearance: '', beliefs: '', people: '', places: '', possessions: '', traits: '', key_connection: null },
  asset_details: [], equipment: [],
}

function obviousInvalid(form: EditableFields, ruleset: RuleSet, saved: Character): boolean {
  if (!form.name.trim() || !form.occupation) return true
  if (form.age !== null && (!Number.isInteger(form.age) || form.age < 1 || form.age > 150)) return true
  if (ruleset.attributes.some(item => {
    const value = form.attributes[item.key]?.value
    const minimum = saved.creation_mode === 'point_buy' ? item.point_buy_minimum ?? item.minimum : item.minimum
    return value === undefined || !Number.isInteger(value) || value < minimum || value > item.maximum
  })) return true
  if (saved.creation_mode === 'point_buy') {
    const spent = ruleset.attributes.reduce((sum, item) => sum + (form.attributes[item.key].value
      - (ruleset.points?.attribute_cost_origin === 'zero' ? 0 : item.minimum)) * item.point_buy_cost, 0)
    const pool = ruleset.points?.attribute_pool || 0
    if (spent > pool || (!ruleset.points?.allow_unspent_attribute_points && spent !== pool)) return true
  }
  const occupation = ruleset.occupations.find(item => item.key === form.occupation)
  if (!occupation || form.selected_occupation_skills.length !== occupation.required_selection_count) return true
  return characterSkills(ruleset, form.custom_specializations).some(skill => {
    const values = [form.occupation_skills[skill.key]?.points || 0, form.interest_skills[skill.key]?.points || 0]
    return values.some(value => !Number.isInteger(value) || value < 0)
      || (saved.skill_base_values[skill.key] ?? skill.base_value) + values[0] + values[1] > skill.maximum
  })
}

export default function CharacterCreationPage({ characterId }: { characterId?: string }) {
  const [rulesets, setRulesets] = useState<RuleSet[]>([])
  const [rulesetId, setRulesetId] = useState('')
  const [mode, setMode] = useState<Character['creation_mode']>('random')
  const [saved, setSaved] = useState<Character | null>(null)
  const [form, setForm] = useState<EditableFields>(empty)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState('')
  const [apiIssues, setApiIssues] = useState<ValidationIssue[]>([])
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let active = true
    Promise.all([charactersApi.rulesets(), characterId ? charactersApi.get(characterId) : Promise.resolve(null)])
      .then(async ([rules, character]) => {
        if (character && !rules.some(r => r.id === character.ruleset_id && r.version === character.ruleset_version)) {
          const archived = await charactersApi.rulesetVersion(character.ruleset_id, character.ruleset_version)
          rules = rules.map(r => r.id === archived.id ? archived : r)
        }
        if (!active) return
        setRulesets(rules)
        setRulesetId(character?.ruleset_id || rules.find(item => item.enabled && item.edition === 'coc7')?.id || rules.find(item => item.enabled)?.id || '')
        if (character) { setSaved(character); setForm(character); setMode(character.creation_mode) }
      }).catch(error => { if (active) setError(error.message) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [characterId])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const ruleset = rulesets.find(item => item.id === rulesetId)
  const locked = saved?.status === 'finalized'
  const unavailable = !ruleset?.enabled || (!!saved && saved.ruleset_version !== ruleset.version)
  const band = ruleset?.age_rules?.bands.find(item => form.age !== null && item.minimum <= form.age && form.age <= item.maximum)
  const update = (value: EditableFields) => {
    const changed = (['occupation', 'era', 'occupation_group_choices', 'occupation_skill_replacement', 'initial_mythos_proposal', 'attributes'] as const)
      .some(k => JSON.stringify(value[k]) !== JSON.stringify(form[k]))
    const experienceChanged = (['experience', 'experience_skills', 'attributes', 'age_deductions', 'occupation', 'era', 'occupation_group_choices', 'occupation_skills', 'interest_skills', 'selected_specializations', 'custom_specializations', 'initial_mythos_proposal', 'occupation_skill_replacement'] as const)
      .some(k => JSON.stringify(value[k]) !== JSON.stringify(form[k]))
    const handoutChanged = experienceChanged || JSON.stringify(value.module_handout) !== JSON.stringify(form.module_handout)
      || JSON.stringify(value.background) !== JSON.stringify(form.background)
    setForm({ ...value, ...(changed ? { approve_occupation_exceptions: undefined } : {}), ...(experienceChanged ? { approve_experience: undefined } : {}), ...(handoutChanged ? { approve_module_handout: false } : {}) })
    setDirty(true); setNotice(''); setApiIssues([])
  }
  function accept(character: Character, message: string) {
    setSaved(character); setForm(character); setDirty(false); setApiIssues([]); setNotice(message)
  }
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setApiIssues([])
    try { await action() }
    catch (error) {
      setError(error instanceof Error ? error.message : '操作失败')
      if (error instanceof CharacterApiError) setApiIssues(error.issues)
    } finally { setBusy(false) }
  }
  async function create() {
    await act(async () => {
      const character = await charactersApi.create(mode, {
        ruleset_id: rulesetId, name: form.name, player_name: form.player_name, age: form.age,
      })
      setDirty(false)
      window.location.hash = `#/characters/${character.id}`
    })
  }
  async function save() {
    if (!saved) return
    await act(async () => {
      const body: Partial<EditableFields> & { version: number } = {
        version: saved.version, name: form.name, player_name: form.player_name, age: form.age,
        occupation: form.occupation, selected_occupation_skills: form.selected_occupation_skills,
        occupation_skills: form.occupation_skills, interest_skills: form.interest_skills,
        age_deductions: form.age_deductions,
        occupation_attribute: form.occupation_attribute, occupation_group_choices: form.occupation_group_choices,
        selected_specializations: form.selected_specializations, era: form.era, background: form.background,
        custom_specializations: form.custom_specializations,
        approve_specializations: form.approve_specializations,
        occupation_skill_replacement: form.occupation_skill_replacement,
        initial_mythos_proposal: form.initial_mythos_proposal,
        approve_occupation_exceptions: form.approve_occupation_exceptions,
        experience: form.experience, experience_skills: form.experience_skills,
        approve_experience: form.approve_experience,
        module_handout: form.module_handout ? {
          preparation_id: form.module_handout.preparation_id, handout_id: form.module_handout.handout_id,
          attribute_allocations: form.module_handout.attribute_allocations,
        } : null,
        approve_module_handout: form.approve_module_handout,
        asset_details: form.asset_details, equipment: form.equipment,
      }
      if (saved.creation_mode === 'point_buy') body.attributes = form.attributes
      accept(await charactersApi.patch(saved.id, body), '草稿已保存，已重新计算并校验。')
    })
  }
  if (loading) return <p role="status">正在加载车卡规则…</p>
  if (characterId && !saved) return <p role="alert">{error || '无法读取角色'}</p>
  return <>
    <section>
      <h2>{saved ? (locked ? '角色卡 · 已最终确认' : '编辑角色草稿') : '创建角色'}</h2>
      {saved && <><p className="hint">角色 ID：<span data-testid="character-id">{saved.id}</span> · 版本 {saved.version}</p>
        {saved.original_id && <p className="hint">导入自：{saved.original_id}</p>}</>}
      <div className="field-grid">
        <label>规则集
          <select id="ruleset" value={rulesetId} disabled={!!saved || busy} onChange={event => setRulesetId(event.target.value)}>
            {rulesets.map(item => <option key={item.id} value={item.id} disabled={!item.enabled}>
              {item.display_name}{item.enabled ? '' : '（未启用）'}
            </option>)}
          </select>
        </label>
        <label>创建模式
          <select id="creation-mode" value={mode} disabled={!!saved || busy} onChange={event => setMode(event.target.value as Character['creation_mode'])}>
            <option value="random">随机生成</option><option value="point_buy">购点分配</option>
          </select>
        </label>
      </div>
      {ruleset && <p className={ruleset.verification_status === 'verified' ? 'hint' : 'rules-notice'}>{ruleset.notice}</p>}
      {unavailable && <p role="alert">规则集不可用或版本不匹配，角色仅供读取。</p>}
    </section>
    <fieldset disabled={busy || locked || unavailable}>
      <BasicInformation value={form} ageLocked={!!saved && !!ruleset?.age_rules}
        ageRange={ruleset?.age_rules ? { minimum: ruleset.age_rules.bands[0].minimum, maximum: ruleset.age_rules.bands.at(-1)!.maximum } : undefined}
        onChange={value => update({ ...form, ...value })} />
      {!saved && <button type="button" onClick={create} disabled={busy || !ruleset?.enabled || (!!ruleset.age_rules && !band)}>
        {mode === 'random' ? '随机生成整组属性并保存草稿' : '创建购点草稿'}
      </button>}
      {!saved && ruleset?.age_rules && <p className="hint">创建时生成幸运与教育检定并锁定年龄。90岁及以上缺少本地完整调整条款，尚不支持；购点采用460点可选规则。</p>}
      {!saved && <p className="hint">模组专属角色请先在<a href="#/preparations">模组准备的 HO 资料</a>中核对年龄、职业等要求，再按要求创建；保存草稿后可选择 HO 调整方案。</p>}
      {!saved && !!ruleset?.experience_packages?.length && <p className="hint">如需经历包，请先确定最终年龄：警务至少25岁、罪犯20岁、医务30岁；战场按参战时年龄与模组年份差计算。创建后在下方选包并分配经历点，最多一包，须KP许可。</p>}
      {saved && ruleset && <>
        <CharacteristicEditor character={saved} ruleset={ruleset} values={form.attributes}
          onChange={attributes => update({ ...form, attributes })} />
        {!!band?.deduction_pool && <section>
          <h2>年龄属性扣减</h2>
          <p>合计扣减 {band.deduction_pool} 点；已分配 {Object.values(form.age_deductions).reduce((sum, value) => sum + value, 0)} 点。其他固定年龄调整由后端计算。</p>
          <div className="field-grid">{band.deduction_attributes.map(key => <label key={key}>
            {ruleset.attributes.find(item => item.key === key)?.display_name} 扣减
            <input id={`age-deduction-${key}`} type="number" min={0} max={band.deduction_pool} value={form.age_deductions[key] || 0}
              onChange={event => update({ ...form, age_deductions: { ...form.age_deductions, [key]: Number(event.target.value) } })} />
          </label>)}</div>
        </section>}
        <SkillAllocator character={saved} ruleset={ruleset} value={form} onChange={update} />
        <ExperiencePackages character={saved} ruleset={ruleset} value={form} onChange={update} />
        <ModuleHandoutEditor key={`${saved.id}:${saved.module_handout?.preparation_id ?? ''}`} character={saved} ruleset={ruleset} value={form} dirty={dirty} onChange={update} />
        <CharacterDetails character={saved} value={form} onChange={update} />
      </>}
    </fieldset>
    {saved && <>
      <section>
        <h2>派生值（后端计算，只读）</h2>
        {dirty && <p className="hint">显示上次保存结果，保存草稿后更新。</p>}
        <dl className="derived-grid">{Object.entries(saved.derived_values).filter(([key]) =>
          !key.endsWith('_half') && !key.endsWith('_fifth') && ruleset?.derived_values.find(item => item.key === key)?.visible !== false,
        ).map(([key, value]) => <div key={key}>
          <dt>{ruleset?.derived_values.find(item => item.key === key)?.display_name || (key === 'luck' ? '幸运 LUCK' : key)}</dt><dd>{value}</dd>
        </div>)}</dl>
      </section>
      {saved.roll_records.length > 0 && <section>
        <h2>掷骰记录</h2>
        <p className="hint">教育增长骰仅在对应增强检定成功时使用；记录在创建时一次保存，读取及编辑不会重掷。</p>
        <ul>{saved.roll_records.map(record => <li key={record.id}>
          {ruleset?.attributes.find(item => item.key === record.attribute)?.display_name || record.attribute}：
          {record.formula} → [{record.dice.join(', ')}] {record.modifier >= 0 ? '+' : ''}{record.modifier} = {record.total}
          {record.multiplier !== 1 && ` × ${record.multiplier} = ${record.total * record.multiplier}`}
          <small>{new Date(record.rolled_at).toLocaleString()} · {record.source} · {record.purpose}</small>
        </li>)}</ul>
      </section>}
      <ValidationSummary issues={saved.validation.issues} dirty={dirty} />
      <div className="action-row">
        <button type="button" onClick={save} disabled={busy || locked || unavailable || !dirty}>保存草稿</button>
        <button type="button" id="finalize-character" disabled={busy || locked || unavailable || dirty || !saved.validation.valid || (!!ruleset && obviousInvalid(form, ruleset, saved))}
          onClick={() => act(async () => accept(await charactersApi.finalize(saved.id, saved.version), '角色已最终确认。'))}>最终确认</button>
        <button type="button" onClick={() => act(async () => accept(await charactersApi.get(saved.id), '已重新加载，未保存修改已舍弃。'))}
          disabled={busy}>重新加载已保存角色</button>
      </div>
      {!locked && <p className="hint">先保存草稿并通过校验，再最终确认。确认后不能修改；未分配的技能点在确认时放弃。</p>}
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
    {apiIssues.length > 0 && <ul role="alert">{apiIssues.map((issue, index) => <li key={index}>{issue.message}（{issue.field}）</li>)}</ul>}
    <JsonTransfer characterId={saved?.id} exportDisabled={dirty || busy} />
  </>
}
