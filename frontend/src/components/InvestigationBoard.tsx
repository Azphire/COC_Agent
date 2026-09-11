import type { PublicEntity } from '../api/preparation'

export default function InvestigationBoard({ entities, onSelect }: { entities: PublicEntity[]; onSelect?: (entity: PublicEntity) => void }) {
  const groups = [{ type: 'npc', title: '已知人物' }, { type: 'location', title: '已知地点' }, { type: 'clue', title: '已知线索' }, { type: 'item', title: '已知物品' }]
  return <section data-testid="investigation-board"><h2>调查板</h2><p>这里记录已经公开的发现，所有调查员共享。</p><div className="board-grid">{groups.map(group => {
    const visible = entities.filter(e => e.type === group.type || (group.type === 'location' && e.type === 'scene'))
    return <div key={group.type}><h3>{group.title}</h3>{visible.length === 0 && <p>尚无发现</p>}{visible.map(entity => <article className="entity-card" key={entity.id} data-public-entity={entity.id}><h4>{onSelect ? <button type="button" onClick={() => onSelect(entity)}>{entity.title}</button> : entity.title} {entity.origin === 'host_authored_test' && <span>主机测试人物</span>} {entity.state === 'corrected' && <span>已修正</span>}</h4><p>{entity.scope_label || '位置未确认'}</p><p className="preserve-lines">{entity.public_summary}</p><small>首次发现：{entity.revealed_time ? new Date(entity.revealed_time).toLocaleString() : '—'}</small><p>{entity.source_references.map((ref, i) => <small key={i}>{ref.source_title} {ref.physical_page ? `p.${ref.physical_page}` : '文本'}；</small>)}</p></article>)}</div>
  })}</div></section>
}
