import { useEffect, useState } from 'react'
import { api, hostToken, requestId } from '../api/session'
import { charactersApi } from '../api/characters'
import type { EditableFields, RuleSet } from '../api/characters'
import type { AgentProfileInput } from '../api/agents'
import { personaFields } from '../api/agents'
import type { PartyBatch, PartyMember } from '../api/launch'
import BasicInformation from './character/BasicInformation'
import SkillAllocator from './character/SkillAllocator'
import CharacterDetails from './character/CharacterDetails'

const editableKeys: (keyof EditableFields)[] = ['name', 'player_name', 'age', 'occupation', 'selected_occupation_skills', 'attributes', 'occupation_skills', 'interest_skills', 'age_deductions', 'occupation_attribute', 'occupation_group_choices', 'selected_specializations', 'custom_specializations', 'era', 'background', 'asset_details', 'equipment', 'occupation_skill_replacement', 'initial_mythos_proposal', 'experience', 'experience_skills']

export default function PartyMemberEditor({ batchId, member, disabled, onSaved }: { batchId: string; member: PartyMember; disabled: boolean; onSaved: (batch: PartyBatch) => void }) {
  const original = member.character
  const [form, setForm] = useState<EditableFields>(original)
  const [profile, setProfile] = useState<AgentProfileInput>(member.profile || { role: 'investigator', name: original.name, background: '', personality: '', goals: '', speaking_style: '', action_tendency: '', model_preset: 'default' })
  const [rules, setRules] = useState<RuleSet | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { charactersApi.rulesetVersion(original.ruleset_id, original.ruleset_version).then(setRules).catch(e => setError(e.message)) }, [original.ruleset_id, original.ruleset_version])
  return <form className="party-editor" onSubmit={async event => {
    event.preventDefault(); setSaving(true); setError('')
    try {
      // Send only edited fields. The server keeps private HO-derived fields untouched.
      const character = Object.fromEntries(editableKeys.filter(key => JSON.stringify(form[key]) !== JSON.stringify(original[key as keyof typeof original])).map(key => [key, form[key]]))
      onSaved(await api<PartyBatch>(`/party-batches/${batchId}/members/${member.index}`, hostToken(), 'PATCH', { request_id: requestId(), character, ...(!member.private_profile ? { profile: { ...profile, name: form.name } } : {}) }))
    } catch (e) { setError(e instanceof Error ? e.message : '保存失败；原成员已保留。') } finally { setSaving(false) }
  }}><fieldset disabled={disabled || saving}>
    <BasicInformation value={form} ageLocked onChange={value => setForm({ ...form, ...value })} />
    {!member.private_profile && personaFields.map(([key, title]) => <label key={key}>{title}<textarea required maxLength={key === 'speaking_style' || key === 'action_tendency' ? 500 : 1000} value={profile[key]} onChange={e => setProfile({ ...profile, [key]: e.target.value })} /></label>)}
    <details><summary>编辑职业、技能与装备</summary>
      {rules ? <SkillAllocator ruleset={rules} character={original} value={form} onChange={setForm} /> : <p role="status">正在读取对应版本的建卡规则…</p>}
      {!member.private_profile && <CharacterDetails character={original} value={form} onChange={setForm} />}
      <p>保存后由服务端重新计算点数和合法性；全部采用时沿用最终确认校验。</p>
    </details>
    <button disabled={!rules || !form.name.trim()}>保存编辑</button>{error && <p role="alert" className="preserve-lines">{error}</p>}
  </fieldset></form>
}
