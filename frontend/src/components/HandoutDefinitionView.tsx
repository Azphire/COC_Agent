import type { PreparedHandout } from '../api/preparation'
import type { RuleSet } from '../api/characters'
import HandoutSources from './HandoutSources'

export default function HandoutDefinitionView({ handout, rules }: { handout: PreparedHandout; rules?: RuleSet | null }) {
  const adjustment = handout.adjustments
  return <div>
    {adjustment && <>
      <p>{adjustment.requirements_note}</p>
      <p>{[
        adjustment.required_age !== null ? `年龄 ${adjustment.required_age} 岁` : '',
        adjustment.required_occupation ? `职业：${rules?.occupations.find(item => item.key === adjustment.required_occupation)?.display_name ?? adjustment.required_occupation}` : '',
        adjustment.required_era ? `年代：${adjustment.required_era === 'modern' ? '现代' : '1920 年代'}` : '',
      ].filter(Boolean).join(' · ')}</p>
      {!!adjustment.attribute_points && <p>额外属性 {adjustment.attribute_points} 点，可分配：{adjustment.attribute_choices.map(key => {
        const name = rules?.attributes.find(item => item.key === key)?.display_name ?? key.toUpperCase()
        const maximum = adjustment.attribute_maxima?.[key] ?? adjustment.attribute_maximum
        return `${name}（${maximum === null ? '原文未另设上限' : `上限 ${maximum}`}）`
      }).join('、')}。</p>}
      {!!Object.keys(adjustment.skill_bonuses).length && <p>技能与信用调整：{Object.entries(adjustment.skill_bonuses).map(([key, bonus]) => `${rules?.skills.find(item => item.key === key)?.display_name ?? key} +${bonus}`).join('；')}。</p>}
      <p>最终技能上限 {adjustment.skill_maximum}{adjustment.credit_maximum !== null ? `；本 HO 最终信用上限 ${adjustment.credit_maximum}` : ''}。{adjustment.order_note}</p>
    </>}
    <details><summary>阅读 HO 原文</summary><p className="preserve-lines">{handout.text}</p></details>
    <HandoutSources handout={handout} />
  </div>
}
