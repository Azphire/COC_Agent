import type { ValidationIssue } from '../../api/characters'

export default function ValidationSummary({ issues, dirty }: { issues: ValidationIssue[]; dirty: boolean }) {
  return <section aria-live="polite">
    <h2>{dirty ? '上次保存的后端校验' : '后端校验结果'}</h2>
    {dirty && <p className="hint">有未保存修改，保存草稿后更新派生值、剩余点数和完整校验。</p>}
    {issues.length ? <ul>{issues.map((issue, index) =>
      <li className="field-error" key={`${issue.field}-${index}`}>{issue.message} <small>（{issue.field}）</small></li>,
    )}</ul> : <p>已保存的数据校验通过。</p>}
  </section>
}
