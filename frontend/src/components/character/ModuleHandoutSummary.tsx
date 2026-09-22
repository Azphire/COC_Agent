import type { Character, RuleSet } from '../../api/characters'
import HandoutSources from '../HandoutSources'

export default function ModuleHandoutSummary({ character, rules }: { character: Character; rules?: RuleSet | null }) {
  const handout = character.module_handout
  if (!handout) return null
  return <div data-testid="module-handout-summary"><h4>{handout.definition.title} · 服务端保存结果</h4>
    <p>{character.module_handout_approval ? '当前方案已由 KP 核准。' : '当前数值为候选效果，待 KP 核准后方可最终确认。'}下表保留调整前数值、HO 加值与最终结果。</p>
    <p>{handout.definition.adjustments?.order_note}</p>
    <div className="table-scroll"><table><thead><tr><th>属性／技能</th><th>调整前</th><th>HO 调整</th><th>最终值</th><th>困难／极难</th><th>来源页</th></tr></thead>
      <tbody>{character.module_handout_effects?.map(effect => <tr key={`${effect.target}:${effect.key}`}>
        <th scope="row">{effect.target === 'attribute' ? rules?.attributes.find(item => item.key === effect.key)?.display_name ?? effect.key.toUpperCase() : rules?.skills.find(item => item.key === effect.key)?.display_name ?? effect.key}</th>
        <td>{effect.base_value}</td><td>{effect.adjustment >= 0 ? '+' : ''}{effect.adjustment}</td><td>{effect.final_value}</td>
        <td>{Math.floor(effect.final_value / 2)}／{Math.floor(effect.final_value / 5)}</td><td>{effect.source_pages.join('、')}</td>
      </tr>)}</tbody>
    </table></div>
    <HandoutSources handout={handout.definition} />
  </div>
}
