import { authHeaders } from './session'

export type ValidationIssue = { field: string; code: string; message: string }
export type CharacteristicValue = { value: number }
export type SkillAllocation = { points: number }
export type Skill = { key: string; display_name: string; base_value: number; maximum: number; category: string; allocatable: boolean; specialization_group: string | null; eras: string[] }
export type SkillGroup = { key: string; display_name: string; count: number; skills: string[]; specialization_groups: string[]; any_skill: boolean }
export type CustomSpecialization = { id: string; group: 'language' | 'art_craft' | 'science'; name: string }
export type Equipment = { id: string; catalog_id: string | null; name: string; quantity: number; notes: string }
export type EquipmentDefinition = { id: string; name: string; eras: string[]; weapon: boolean }
export type Background = { appearance: string; beliefs: string; people: string; places: string; possessions: string; traits: string; key_connection: string | null }
export type RuleSet = {
  id: string; version: string; display_name: string; edition: string; enabled: boolean
  verification_status: string; notice: string
  attributes: { key: string; display_name: string; minimum: number; point_buy_minimum: number | null; maximum: number; random_formula: string; random_multiplier: number; point_buy_cost: number }[]
  derived_values: { key: string; display_name: string; visible: boolean }[]
  points: { attribute_pool: number; allow_unspent_points: boolean; attribute_cost_origin: 'minimum' | 'zero'; allow_unspent_attribute_points: boolean } | null
  skills: Skill[]
  custom_specialization_templates: Record<string, string>
  occupations: { key: string; display_name: string; fixed_skills: string[]; selectable_skills: string[]; required_selection_count: number; credit_rating_minimum: number | null; credit_rating_maximum: number | null; eras: string[]; source: string; skill_groups: SkillGroup[]; point_formula: { fixed: Record<string, number>; choice_attributes: string[]; choice_multiplier: number; display: string } | null }[]
  age_rules: { bands: { minimum: number; maximum: number; deduction_pool: number; deduction_attributes: string[] }[] } | null
}
export type Character = {
  id: string; original_id: string | null; ruleset_id: string; ruleset_version: string
  status: 'draft' | 'finalized'; creation_mode: 'random' | 'point_buy'
  name: string; player_name: string | null; age: number | null; occupation: string | null
  selected_occupation_skills: string[]; attributes: Record<string, CharacteristicValue>
  occupation_attribute: string | null; occupation_group_choices: Record<string, string[]>; selected_specializations: string[]
  custom_specializations: CustomSpecialization[]
  era: '1920s' | 'modern'; background: Background; asset_details: { description: string; value: number }[]; equipment: Equipment[]
  finances: { currency: string; level: string; cash: number; assets: number; spending: number; assets_lower_bound: boolean }
  derived_values: Record<string, number | string>; occupation_skills: Record<string, SkillAllocation>
  effective_attributes: Record<string, number>; age_deductions: Record<string, number>
  interest_skills: Record<string, SkillAllocation>; skill_values: Record<string, number>; skill_base_values: Record<string, number>
  skill_half_values: Record<string, number>; skill_fifth_values: Record<string, number>
  roll_records: { id: string; attribute: string; formula: string; dice: number[]; modifier: number; total: number; rolled_at: string; source: string; purpose: string; multiplier: number }[]
  validation: { valid: boolean; issues: ValidationIssue[] }
  remaining_points: { attributes: number | null; occupation: number; interest: number }
  created_at: string; updated_at: string; version: number
}
export type BasicFields = Pick<Character, 'name' | 'player_name' | 'age'>
export type EditableFields = BasicFields & Pick<Character, 'occupation' | 'selected_occupation_skills' | 'attributes' | 'occupation_skills' | 'interest_skills' | 'age_deductions' | 'occupation_attribute' | 'occupation_group_choices' | 'selected_specializations' | 'custom_specializations' | 'era' | 'background' | 'asset_details' | 'equipment'>
export type CharacterExport = {
  schema_version: number; exported_at: string; character: Character
  ruleset: { id: string; version: string; verification_status: string }
}

export class CharacterApiError extends Error {
  issues: ValidationIssue[]
  constructor(message: string, issues: ValidationIssue[] = []) {
    super(message)
    this.issues = issues
  }
}

const base = ''

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${base}/api${path}`, {
      method, headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000),
    })
  } catch {
    throw new CharacterApiError('无法连接后端或请求超时，请检查服务后重新加载角色')
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    const detail = data.detail
    if (Array.isArray(detail)) {
      throw new CharacterApiError('请求字段不合法', detail.map(item => ({
        field: (item.loc || []).filter((part: string) => part !== 'body').join('.'),
        code: item.type, message: item.msg,
      })))
    }
    throw new CharacterApiError(detail?.message || `请求失败（HTTP ${response.status}）`, detail?.issues)
  }
  return response.json()
}

export const charactersApi = {
  rulesets: () => request<RuleSet[]>('/character-rulesets'),
  rulesetVersion: (id: string, version: string) => request<RuleSet>(`/character-rulesets/${encodeURIComponent(id)}?version=${encodeURIComponent(version)}`),
  equipment: () => request<EquipmentDefinition[]>('/character-equipment'),
  list: () => request<Character[]>('/characters'),
  get: (id: string) => request<Character>(`/characters/${encodeURIComponent(id)}`),
  create: (mode: Character['creation_mode'], body: BasicFields & { ruleset_id: string }) =>
    request<Character>(`/characters/${mode === 'random' ? 'random' : 'point-buy'}`, 'POST', body),
  patch: (id: string, body: Partial<EditableFields> & { version: number }) =>
    request<Character>(`/characters/${id}`, 'PATCH', body),
  finalize: (id: string, version: number) => request<Character>(`/characters/${id}/finalize`, 'POST', { version }),
  export: (id: string) => request<CharacterExport>(`/characters/${id}/export`),
  import: (body: unknown) => request<Character>('/characters/import', 'POST', body),
}
