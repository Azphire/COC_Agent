import { useEffect, useState } from 'react'
import type { Character, EditableFields, RuleSet } from '../../api/characters'
import type { PreparationDocument, PreparedHandout } from '../../api/preparation'
import { api, hostToken } from '../../api/session'
import HandoutDefinitionView from '../HandoutDefinitionView'
import ModuleHandoutSummary from './ModuleHandoutSummary'

export default function ModuleHandoutEditor({ character, ruleset, value, dirty, onChange }: {
  character: Character; ruleset: RuleSet; value: EditableFields; dirty: boolean; onChange: (value: EditableFields) => void
}) {
  const [preparations, setPreparations] = useState<PreparationDocument[]>([])
  const [selectedPreparationId, setPreparationId] = useState(value.module_handout?.preparation_id ?? '')
  const preparationId = value.module_handout?.preparation_id ?? selectedPreparationId
  const [handouts, setHandouts] = useState<PreparedHandout[]>([])
  const [error, setError] = useState('')
  const token = hostToken()
  useEffect(() => {
    let active = true
    api<PreparationDocument[]>('/module-preparations', token).then(items => {
      if (active) setPreparations(items.filter(item => item.status === 'approved' && item.handouts?.some(handout => handout.adjustments?.ruleset_id === ruleset.id)))
    }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [token, ruleset.id])
  useEffect(() => {
    let active = true
    if (preparationId) api<PreparationDocument>(`/module-preparations/${preparationId}`, token).then(item => {
      if (active) setHandouts((item.handouts ?? []).filter(handout => handout.adjustments?.ruleset_id === ruleset.id))
    }).catch(error => { if (active) setError(error.message) })
    return () => { active = false }
  }, [preparationId, token, ruleset.id])
  const selection = value.module_handout
  const selected = handouts.find(item => item.id === selection?.handout_id)
    ?? (character.module_handout?.handout_id === selection?.handout_id && character.module_handout?.preparation_id === selection?.preparation_id ? character.module_handout?.definition : undefined)
  const adjustment = selected?.adjustments
  const issues = character.validation.issues.filter(issue => issue.field.startsWith('module_handout'))
  return <section data-testid="module-handout-editor"><h2>模组专属 HO 角色调整</h2>
    <p>从已批准准备包选择来源。先保存查看属性、技能、信用与派生值，再由 KP 核准当前方案。</p>
    <div className="field-grid">
      <label>准备包<select id="character-handout-preparation" value={preparationId} onChange={event => {
        setPreparationId(event.target.value); setHandouts([]); setError('')
        onChange({ ...value, module_handout: null, approve_module_handout: false })
      }}><option value="">不使用模组调整</option>{preparations.map(item => <option key={item.id} value={item.id}>{item.display_title}</option>)}
        {preparationId && !preparations.some(item => item.id === preparationId) && <option value={preparationId}>当前冻结来源 · {preparationId}</option>}
      </select></label>
      <label>HO 方案<select id="character-handout-selection" value={selection?.handout_id ?? ''} disabled={!preparationId} onChange={event => onChange({ ...value,
        module_handout: event.target.value ? { preparation_id: preparationId, handout_id: event.target.value, attribute_allocations: {} } : null,
        approve_module_handout: false,
      })}><option value="">选择 HO</option>{handouts.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
        {selected && !handouts.some(item => item.id === selected.id) && <option value={selected.id}>{selected.title}</option>}
      </select></label>
    </div>
    {!preparations.length && <p>暂无适用的已批准 HO 准备包。可在<a href="#/preparations">模组准备</a>中导入并核对。</p>}
    {selected && <HandoutDefinitionView handout={selected} rules={ruleset} />}
    {adjustment && !!adjustment.attribute_points && selection && <>
      <p>额外属性已分配 {Object.values(selection.attribute_allocations).reduce((sum, points) => sum + points, 0)} / {adjustment.attribute_points} 点。原始属性保留在上方，最终值以保存后的计算为准。</p>
      <div className="field-grid">{adjustment.attribute_choices.map(key => <label key={key}>{ruleset.attributes.find(item => item.key === key)?.display_name ?? key.toUpperCase()} · HO 加值
        <input id={`handout-attribute-${key}`} type="number" min={0} max={adjustment.attribute_points} value={selection.attribute_allocations[key] ?? 0} onChange={event => onChange({ ...value,
          module_handout: { ...selection, attribute_allocations: { ...selection.attribute_allocations, [key]: Number(event.target.value) } }, approve_module_handout: false,
        })} />
      </label>)}</div>
    </>}
    {dirty && <p className="hint">下方为上次保存结果；保存草稿后更新方案预览。</p>}
    <ModuleHandoutSummary character={character} rules={ruleset} />
    {issues.filter(issue => issue.code !== 'keeper_approval').map((issue, index) => <p role="alert" key={index}>{issue.message}</p>)}
    {issues.some(issue => issue.code === 'keeper_approval') && <label><input id="approve-module-handout" type="checkbox" checked={value.approve_module_handout ?? false} onChange={event => onChange({ ...value, approve_module_handout: event.target.checked })} />KP 已核对当前 HO 来源、人物要求、加值、上限和派生值，明确核准以上方案。勾选后保存。</label>}
    {error && <p role="alert">{error}</p>}
  </section>
}
