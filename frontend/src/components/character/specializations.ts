import type { CustomSpecialization, RuleSet, Skill } from '../../api/characters'

export function characterSkills(ruleset: RuleSet, custom: CustomSpecialization[] = []): Skill[] {
  return [...ruleset.skills, ...custom.flatMap(s => {
    const template = ruleset.skills.find(t => t.key === ruleset.custom_specialization_templates?.[s.group])
    return template ? [{ ...template, key: s.id, display_name: `${template.display_name.split('（')[0]}（${s.name}）` }] : []
  })]
}

export const specializationLabels = { language: '外语', art_craft: '艺术与手艺', science: '科学', pilot: '驾驶', survival: '生存', lore: '学问' }
export const specializationName = (s: CustomSpecialization) => `${specializationLabels[s.group]}（${s.name}）`
export const normalizedName = (name: string) => name.normalize('NFKC').toLocaleLowerCase().replace(/\s/g, '')
