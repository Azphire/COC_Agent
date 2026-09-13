import type { Room } from '../api/rooms'

const injuryLabels: Record<string, string> = {
  major_wound: '重伤', unconscious: '昏迷', dying: '濒死', dead: '死亡', stabilized: '伤势已稳定',
}

export default function RuntimeCards({ room }: { room: Room }) {
  return <div className="runtime-cards">
    {Object.entries(room.session_state.characters).map(([id, runtime]) => {
      const slot = room.character_slots.find(s => s.id === id)
      const items = (room.inventory ?? []).filter(i => i.holder_id === slot?.member_id)
      const combatant = Object.values(room.combat?.participants ?? {}).find(p => p.member_id === slot?.member_id)
      const weapons = combatant?.weapons ?? runtime.weapons ?? []
      const injury = combatant?.injury ?? runtime.injury ?? {}
      const conditions = [...Object.entries(injuryLabels).filter(([key]) => injury[key]).map(([, label]) => label), ...runtime.conditions]
      return <article key={id} className="runtime-card" data-runtime-id={id}>
        <h3>{slot?.public_summary.name}</h3>
        <p>HP {runtime.hp ?? '—'} / {runtime.hp_max ?? '—'} · MP {runtime.mp ?? '—'} / {runtime.mp_max ?? '—'}</p>
        <p>SAN {runtime.san ?? '—'} / {runtime.san_max ?? '—'} · Luck {runtime.luck ?? '—'}</p>
        {runtime.mp != null && runtime.mp_max != null && runtime.mp < runtime.mp_max && <small>MP 自然恢复进度：{runtime.mp_recovery_progress ?? 0} / 60 游戏分钟</small>}
        <p>{conditions.join('、') || '无已记录伤势'}{injury.con_pending ? ' · 待体质检定' : ''}</p>
        <p>武器：{weapons.filter(w => w.quantity > 0).map(w => `${w.name}${w.kind === 'firearm' ? `（弹药 ${w.ammo}，备用 ${w.reserve}${w.jammed ? '，卡壳' : ''}）` : ''}`).join('、') || '无已配置武器'}</p>
        <p>实际持有物：{items.length ? '' : '无'}</p>
        {items.length > 0 && <ul>{items.map(item => <li key={item.instance_id}>
          {item.title}{item.remaining_uses != null ? ` · 剩余 ${item.remaining_uses} 次` : ''}
          <small>（实例：{item.instance_id}）</small>
        </li>)}</ul>}
      </article>
    })}
    <p className="muted">在行动输入中描述使用物品和目标；同名物品可附上实例编号。使用结果见下方行动记录。</p>
  </div>
}
