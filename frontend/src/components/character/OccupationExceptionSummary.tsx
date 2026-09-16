import type { Character, RuleSet } from '../../api/characters'

export default function OccupationExceptionSummary({ character: c, rules }: { character: Character; rules?: RuleSet | null }) {
  const occupation = rules?.occupations.find(o => o.key === c.occupation)
  const replacement = c.occupation_skill_replacement, mythos = c.initial_mythos_proposal
  const name = (key: string) => rules?.skills.find(s => s.key === key)?.display_name ?? key
  const status = (key: string) => c.occupation_exception_approvals?.[key] ? '已由本地主机核准' : '候选效果，待 KP 明确核准'
  if (!replacement && !mythos) return null
  return <div data-testid="occupation-exception-summary">
    <h4>职业特例 · 服务端保存结果</h4>
    {replacement && <>
      <p>{name(replacement.original_skill)} → {name(replacement.replacement_skill)}；{status('skill_replacement')}。</p>
      <p>理由：{replacement.reason}。原技能职业投入 {c.occupation_skills[replacement.original_skill]?.points ?? 0}，替换技能职业投入 {c.occupation_skills[replacement.replacement_skill]?.points ?? 0}；名额与点池不增加。原技能遗留投入须退回职业池。</p>
      <p>来源：{occupation?.skill_replacement?.source}。{occupation?.skill_replacement?.note}</p>
    </>}
    {mythos && <>
      <p>初始克苏鲁神话 {c.initial_mythos}（申请 {mythos.value}）；困难 {c.skill_half_values.cthulhu_mythos} · 极难 {c.skill_fifth_values.cthulhu_mythos}；{status('initial_mythos')}。</p>
      <p>初始 SAN：POW {c.effective_attributes.pow} → {c.derived_values.san}；SAN 上限 {c.derived_values.san_max ?? 99}。职业条款等额扣减 0，上限约束减少 {Math.max(0, c.effective_attributes.pow - Number(c.derived_values.san))}。职业／兴趣点花费均为 0，占个人特长名额。</p>
      <p>理由：{mythos.reason}。来源：{occupation?.initial_mythos?.source}。</p>
      <p>{occupation?.initial_mythos?.note}</p>
    </>}
  </div>
}
