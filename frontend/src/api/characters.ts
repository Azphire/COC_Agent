import { authHeaders } from './session'
import type { PreparedHandout } from './preparation'

export type ValidationIssue = { field: string; code: string; message: string }
export type CharacteristicValue = { value: number }
export type SkillAllocation = { points: number }
export type Skill = { key: string; display_name: string; base_value: number; maximum: number; category: string; allocatable: boolean; specialization_group: string | null; eras: string[] }
export type SkillGroup = { key: string; display_name: string; count: number; skills: string[]; specialization_groups: string[]; any_skill: boolean; distinct_directions: boolean }
export type CustomSpecialization = { id: string; group: 'language' | 'art_craft' | 'science' | 'pilot' | 'survival' | 'lore'; name: string }
export type Equipment = { id: string; catalog_id: string | null; name: string; quantity: number; notes: string; initial_ammo?: number; initial_reserve?: number }
export type WeaponTemplate = { template_id: string | null; name: string; skill: string; kind: 'melee' | 'firearm'; damage: string; impale: boolean; uses_db: boolean; base_range: string | null; capacity: number; malfunction: number; source: string }
export type EquipmentDefinition = { id: string; name: string; eras: string[]; weapon: boolean; category: string; description: string; source: string; skill_name: string | null; weapon_template: WeaponTemplate | null }
export type Background = { appearance: string; beliefs: string; people: string; places: string; possessions: string; traits: string; key_connection: string | null }
export type OccupationSkillReplacement = { original_skill: string; replacement_skill: string; reason: string }
export type InitialMythos = { source: 'occultist'; value: number; reason: string }
export type OccupationExceptionPolicy = { source: string; note: string }
export type Experience = {
  package: 'war' | 'police' | 'criminal' | 'medical' | 'mythos'; variant: string; history: string
  mythos?: { knowledge: 'reading' | 'direct'; belief: 'believer' | 'unbeliever'; method: 'manual' | 'suggested_roll'; value: number | null; backgrounds: { kind: 'scar' | 'phobia' | 'mania' | 'encounter'; detail: string }[]; spells: SpellCandidate[] } | null
  background_kind: 'scar' | 'phobia' | 'mania'; background_detail: string
  choices: Record<string, string[]>; war_year: number | null; scenario_year: number | null; age_at_war: number | null
}
export type ExperienceDefinition = {
  key: Experience['package']; display_name: string; source: string; qualification: string
  minimum_age: number | null; occupations: string[]; points: number; san_loss: string | null; initial_mythos_roll?: string | null; immunity: string[]
  variants: Record<string, { display_name: string; fixed_skills: string[]; skill_groups: SkillGroup[]; specialty_groups: string[] }>
}
export type ExperienceEffects = {
  package?: string; name?: string; source?: string; pool?: number; allowed_skills?: string[]
  san_loss?: number; san_before?: number; san_after?: number; san_max?: number; approved?: boolean
  immunity_reasons?: string[]; background_kind?: string; background_detail?: string
}
export type SpellCandidate = { id: string; name: string; source: string; acquisition: 'experience_mythos' }
export type ModuleHandoutSelection = { preparation_id: string; handout_id: string; attribute_allocations: Record<string, number> }
export type ModuleHandoutEffect = { target: 'attribute' | 'skill'; key: string; base_value: number; adjustment: number; final_value: number; source_pages: number[] }
export type RuleSet = {
  id: string; version: string; display_name: string; edition: string; enabled: boolean
  verification_status: string; notice: string
  attributes: { key: string; display_name: string; minimum: number; point_buy_minimum: number | null; maximum: number; random_formula: string; random_multiplier: number; point_buy_cost: number }[]
  derived_values: { key: string; display_name: string; visible: boolean }[]
  points: { attribute_pool: number; allow_unspent_points: boolean; attribute_cost_origin: 'minimum' | 'zero'; allow_unspent_attribute_points: boolean } | null
  skills: Skill[]
  experience_packages: ExperienceDefinition[]
  custom_specialization_templates: Record<string, string>
  specialization_policies: Record<string, { requires_keeper_approval: boolean; custom_only: boolean; note: string }>
  occupations: { key: string; display_name: string; fixed_skills: string[]; selectable_skills: string[]; required_selection_count: number; credit_rating_minimum: number | null; credit_rating_maximum: number | null; eras: string[]; source: string; skill_groups: SkillGroup[]; skill_replacement: (OccupationExceptionPolicy & { target_skill: string }) | null; initial_mythos: (OccupationExceptionPolicy & { selection_group: string; recommended_maximum: number }) | null; point_formula: { fixed: Record<string, number>; choice_attributes: string[]; choice_multiplier: number; display: string } | null }[]
  age_rules: { bands: { minimum: number; maximum: number; deduction_pool: number; deduction_attributes: string[] }[] } | null
}
export type Character = {
  module_handout?: (ModuleHandoutSelection & { definition: PreparedHandout }) | null
  module_handout_approval?: string | null; module_handout_effects?: ModuleHandoutEffect[]
  id: string; original_id: string | null; ruleset_id: string; ruleset_version: string
  status: 'draft' | 'finalized'; creation_mode: 'random' | 'point_buy'
  name: string; player_name: string | null; age: number | null; occupation: string | null
  selected_occupation_skills: string[]; attributes: Record<string, CharacteristicValue>
  occupation_attribute: string | null; occupation_group_choices: Record<string, string[]>; selected_specializations: string[]
  custom_specializations: CustomSpecialization[]
  specialization_approvals: Record<string, string>
  occupation_skill_replacement: OccupationSkillReplacement | null
  initial_mythos_proposal: InitialMythos | null
  occupation_exception_approvals: Record<string, string>
  effective_occupation_skills: string[]
  initial_mythos: number
  initial_mythos_sources?: { source: string; value: number; san_cost: number; approved: boolean }[]
  initial_belief?: 'believer' | 'unbeliever' | null
  known_spells?: (SpellCandidate & { permission: string; casting_status: 'unsupported' })[]
  experience: Experience | null; experience_skills: Record<string, SkillAllocation>
  experience_approvals: Record<string, string>; experience_effects: ExperienceEffects
  experience_rolls: Record<string, Character['roll_records'][number]>
  era: '1920s' | 'modern'; background: Background; asset_details: { description: string; value: number }[]; equipment: Equipment[]
  finances: { currency: string; level: string; cash: number; assets: number; spending: number; assets_lower_bound: boolean }
  derived_values: Record<string, number | string>; occupation_skills: Record<string, SkillAllocation>
  effective_attributes: Record<string, number>; age_deductions: Record<string, number>
  interest_skills: Record<string, SkillAllocation>; skill_values: Record<string, number>; skill_base_values: Record<string, number>
  skill_half_values: Record<string, number>; skill_fifth_values: Record<string, number>
  roll_records: { id: string; attribute: string; formula: string; dice: number[]; modifier: number; total: number; rolled_at: string; source: string; purpose: string; multiplier: number }[]
  validation: { valid: boolean; issues: ValidationIssue[] }
  remaining_points: { attributes: number | null; occupation: number; interest: number; experience: number }
  created_at: string; updated_at: string; version: number
}
export type BasicFields = Pick<Character, 'name' | 'player_name' | 'age'>
export type EditableFields = BasicFields & Pick<Character, 'occupation' | 'selected_occupation_skills' | 'attributes' | 'occupation_skills' | 'interest_skills' | 'age_deductions' | 'occupation_attribute' | 'occupation_group_choices' | 'selected_specializations' | 'custom_specializations' | 'era' | 'background' | 'asset_details' | 'equipment' | 'occupation_skill_replacement' | 'initial_mythos_proposal' | 'experience' | 'experience_skills'> & { approve_specializations?: string[]; approve_occupation_exceptions?: string[]; approve_experience?: string[]; module_handout?: ModuleHandoutSelection | null; approve_module_handout?: boolean }
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
  equipment: (includeUnarmed = false) => request<EquipmentDefinition[]>(`/character-equipment?include_unarmed=${includeUnarmed}`),
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
