import type { PreparedHandout } from '../api/preparation'

export default function HandoutSources({ handout }: { handout: Pick<PreparedHandout, 'source_hash' | 'source_pages' | 'source_block_ids'> }) {
  return <details><summary>原文来源 · {handout.source_pages.length ? `第 ${handout.source_pages.join('、')} 页` : '无物理页码'}</summary>
    <p className="preserve-lines">来源版本：{handout.source_hash}</p>
    <p className="preserve-lines">校对来源块：{handout.source_block_ids.join('、')}</p>
  </details>
}
