import type { Character } from '../../api/characters'

export default function ExperienceSummary({ character: c }: { character: Character }) {
  const effect = c.experience_effects
  if (!c.experience) return null
  const roll = c.experience_rolls?.[c.experience.package]
  if (c.experience.package === 'mythos') return <div data-testid="experience-summary">
    <h4>神话经历包 · 服务端保存结果</h4>
    <p>{effect?.approved ? '已由本地主机核准' : '候选效果，待 KP 明确核准'}。无额外点池或职业名额。</p>
    <p>知识背景：{c.experience.history}；初始状态：{c.initial_belief === 'believer' ? '相信者' : c.initial_belief === 'unbeliever' ? '不信者' : '此版本未支持'}。</p>
    <p>初始神话合计 {c.initial_mythos}；职业来源 {c.initial_mythos_sources?.find(s => s.source === 'occupation:occultist')?.value ?? 0}，包来源 {c.initial_mythos_sources?.find(s => s.source === 'experience:mythos')?.value ?? 0}。</p>
    <p>初始 SAN：POW {effect?.san_before ?? c.effective_attributes.pow} − 包代价 {effect?.san_loss ?? 0}，再限制上限 {effect?.san_max ?? 99 - c.initial_mythos} → {c.derived_values.san}。</p>
    {roll && <p>初始神话原骰（保留）：{roll.formula} → [{roll.dice.join(', ')}] + {roll.modifier} = {roll.total}；{roll.source} · {roll.rolled_at}</p>}
    {c.experience.mythos?.backgrounds.map((b, i) => <p key={i}>神话背景 {i + 1}（{({ scar: '伤疤', phobia: '恐惧症', mania: '躁狂症', encounter: '怪异存在' })[b.kind]}）：{b.detail}</p>)}
    <p>法术：仅作已知记录，尚不支持施放。</p>
    {!c.experience.mythos?.spells.length && <p>无初始法术。</p>}
    {c.experience.mythos?.spells.map(s => <p key={s.id}>{s.name} · {c.known_spells?.some(k => k.id === s.id) ? '已知记录已核准' : '候选待核准'} · 取得方式：神话经历包 · 来源：{s.source} · 标识：{s.id}</p>)}
    <p>不信者照常承受遭遇损失；批准的神话直接证据及零损失例外由 SAN 服务处理。来源：{effect?.source}</p>
  </div>
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
