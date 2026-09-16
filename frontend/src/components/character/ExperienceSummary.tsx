import type { Character } from '../../api/characters'

export default function ExperienceSummary({ character: c }: { character: Character }) {
  const effect = c.experience_effects
  if (!c.experience) return null
  const roll = c.experience_rolls?.[c.experience.package]
  return <div data-testid="experience-summary">
    <h4>{effect?.name ?? c.experience.package} · 服务端保存结果</h4>
    <p>{effect?.approved ? '已由本地主机核准' : '候选效果，待 KP 明确核准'}。经历点池 {effect?.pool ?? 0}，剩余 {c.remaining_points.experience ?? 0}。</p>
    <p>初始 SAN：POW {effect?.san_before ?? c.effective_attributes.pow} − 经历损失 {effect?.san_loss ?? 0}，再限制上限 {effect?.san_max ?? 99 - c.initial_mythos} → {c.derived_values.san}。</p>
    {roll && <p>经历原骰：{roll.formula} → [{roll.dice.join(', ')}] + {roll.modifier} = {roll.total}；{roll.source} · {roll.rolled_at}</p>}
    <p>限定免疫：{effect?.immunity_reasons?.join('、') || '无已核实效果'}；仅匹配批准遭遇的独立原因类别，未分类及范围外恐怖照常处理。</p>
    <p>资格背景：{c.experience.history}；新增{({ scar: '伤疤', phobia: '恐惧症', mania: '躁狂症' })[c.experience.background_kind]}：{c.experience.background_detail}</p>
    {c.experience.package === 'war' && <p>参战 {c.experience.war_year} 年／{c.experience.age_at_war} 岁 → 模组 {c.experience.scenario_year} 年／当前 {c.age} 岁。</p>}
    <p>来源：{effect?.source}</p>
  </div>
}
