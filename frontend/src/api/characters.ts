export type ValidationIssue = { field: string; code: string; message: string }
export type CharacteristicValue = { value: number }
export type SkillAllocation = { points: number }
export type RuleSet = {
  id: string; version: string; display_name: string; edition: string; enabled: boolean
  verification_status: string; notice: string
  attributes: { key: string; display_name: string; minimum: number; point_buy_minimum: number | null; maximum: number; random_formula: string; random_multiplier: number; point_buy_cost: number }[]
  derived_values: { key: string; display_name: string; visible: boolean }[]
  points: { attribute_pool: number; allow_unspent_points: boolean; attribute_cost_origin: 'minimum' | 'zero'; allow_unspent_attribute_points: boolean } | null
  skills: { key: string; display_name: string; base_value: number; maximum: number; category: string; allocatable: boolean }[]
  occupations: { key: string; display_name: string; fixed_skills: string[]; selectable_skills: string[]; required_selection_count: number; credit_rating_minimum: number | null; credit_rating_maximum: number | null }[]
  age_rules: { bands: { minimum: number; maximum: number; deduction_pool: number; deduction_attributes: string[] }[] } | null
}
export type Character = {
  id: string; original_id: string | null; ruleset_id: string; ruleset_version: string
  status: 'draft' | 'finalized'; creation_mode: 'random' | 'point_buy'
  name: string; player_name: string | null; age: number | null; occupation: string | null
  selected_occupation_skills: string[]; attributes: Record<string, CharacteristicValue>
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
export type EditableFields = BasicFields & Pick<Character, 'occupation' | 'selected_occupation_skills' | 'attributes' | 'occupation_skills' | 'interest_skills' | 'age_deductions'>
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

const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${base}/api${path}`, {
      method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
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
