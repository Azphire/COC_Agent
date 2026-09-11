# 对抗与组合检定：规则和接口

范围：非战斗双方对抗；同一调查员的两个技能组合。沿用普通行动、检定卡、房间事务与存档，不增加普通主机审批。

## 本地规则依据

来源为 `data/rules/克苏鲁的呼唤第七版规则书1907.pdf`；下列先列 PDF 物理页，括号内为印刷页。

| 内容 | 来源与实现 |
|---|---|
| 对抗 | PDF 78–80（77–79），5.7–5.8。双方选择各自属性或技能，可以不同；比较大成功、极难、困难、普通、失败、大失败六级。同级比较完整技能／属性值，仍相同可僵局或重骰。本版选择**僵局**，不追加骰。对抗不设置难度，以奖惩骰调整；禁止孤注。 |
| 双失败 | 第五章没有给非战斗双失败单列例外，本版仍按上述等级、数值比较，结果另外记录 `both_failed`。同为失败且数值相同则僵局；数值不同则较高者取得相对胜利，不把其单项结果改成成功。普通失败高于大失败按六级顺序解释。PDF 107（106）的“双失败无伤害”是**战斗**规定，本批不套用。 |
| 组合 | PDF 80（79），5.9。只掷一次百分骰，与两项技能分别比较。KP 必须在骰前决定任一成功 `any` 或全部成功 `all`，不能在看到部分成功后更换条件。各分项等级、阈值、大失败与部分成功均保留。 |
| 孤注 | PDF 74–75（73–74），5.3；PDF 77（76），5.5。组合总体失败、无分项大失败且未花幸运时，可说明额外努力申请一次；KP 先确认资格和更严重后果，真人再确认掷骰。两项共用第二次骰，后果只执行一次。 |
| 幸运 | PDF 85（84），5.16。开启房间可选规则后可按 1:1 降低骰点，仅消费本人幸运。大成功、大失败、故障及孤注结果不能修改；SAN／幸运／伤害／理智损失不适用，不能买出 01。花幸运后不能孤注，不获得技能成长标记。组合只扣一次并重算两个分项；任一分项大失败即禁止幸运和孤注。 |

官方[对抗规则摘要](https://cthulhuwiki.chaosium.com/rules/opposed-skill-rolls.html)也说明同等级比较技能值；完全平局的处理以本地完整规则书为准。

PDF 78（77）建议非战斗调查员对 NPC 通常不用双方对抗，但允许熟悉规则的 KP 为增强戏剧性采用。本版提供这项能力，是否采用仍由 KP 针对行动决定。

**项目约定**：本地规则未规定双方花幸运的出价顺序。本版等双方原骰齐备后，由行动者先选、对手后选；每方只能确认一次，不能反复追加。确认幸运时，在同一事务中检查余额、扣点、保存选择；全部确认后才产生统一 `check.resolved`。AI 调查员与 NPC 自动接受原骰，不自动消费资源。NPC 没有调查员幸运余额。组合的幸运与孤注资格按整体尝试及全部分项约束判定，这是对通用规则的实现解释。

首版对抗使用公开卡；远程真人由本人操作，主机只可代管本地真人席位。AI／NPC 由服务端自动掷骰。拒绝战斗标记及已知格斗、闪避、枪械技能；本批不结算战斗、伤害或 HP。

## 提案与等待项

`request_skill_check` 继续接收 `CheckRequest`。保留原字段，新增互斥且默认 `null` 的 `opposed`、`combined`；`combat` 默认 `false`。旧提案、普通检定及旧存档无需填新字段。

```json
{
  "target_member_id": "行动者成员 UUID",
  "kind": "attribute", "name": "str", "difficulty": "regular",
  "reason": "双方争取互斥结果",
  "opposed": {
    "opponent_member_id": "对手成员 UUID",
    "kind": "attribute", "name": "siz",
    "bonus_dice": 0, "penalty_dice": 0
  }
}
```

NPC 对手改填 `opponent_npc_id`，与 `opponent_member_id` 二选一。必须是当前场景可见、有已批准准备数值的 NPC。`CheckRequest` 不能提交数值或骰点。

```json
{
  "target_member_id": "行动者成员 UUID",
  "kind": "skill", "name": "spot_hidden", "reason": "结合观察与异响定位故障",
  "combined": {
    "name": "listen", "difficulty": "regular", "requirement": "all"
  }
}
```

KP 的 `CheckProposal` 仍须填写目的、方法、不确定因素、成功效果及失败后果，由现有行动裁决检查。自然行动规划使用同一字段，不另设手工建检定接口。

既有线索配置的单技能门槛不会被隐式放宽：关联这类线索的对抗或 `any` 组合须由 KP 在掷骰前说明 `alternative_basis`，按已有替代路径规则验证；`all` 组合在第一技能及难度仍匹配原要求时可以沿用门槛。线索只在整个检定最终通过后进入原揭示流程。

`PendingCheck.compound` 保存冻结参与方或技能分项。对抗阶段为 `rolling → choice → final`，包含 `participants`、`choice_index`、固定 `choice_order` 和 `tie_policy=stalemate`。顶层 `result` 只在最终结算时出现；分方骰点与选择不是胜负公告。结果含 `winner`（0／1／null）、`winner_id`、`comparison`、`both_failed`、`side_results`，顶层 `passed` 表示**行动者的相对胜利**。

组合继续使用原 `settlement` 阶段；`result.components` 保存两个独立判定，`partial_success` 标识部分成功，顶层 `passed` 按预定条件汇总。顶层等级仅用于摘要，不能替代分项判定。

## 玩家操作、NPC 准备与恢复

- `POST /api/rooms/{room_id}/checks/{id}/roll`：普通／组合传 `{}`；对抗可传 `{"participant_id":"本人 UUID"}`，省略时取调用者本人。不能指定骰点，也不能替远程真人或 AI／NPC 掷骰。
- `POST .../choice`：沿用 `operation=accept|luck|push`、`spend`、`effort`，对抗可附 `participant_id`。对抗拒绝 `push` 与抢先选择；组合的 `push-review`、`push-roll`、`consequence` 接口沿用原流程。
- 准备工作台 NPC 编辑区或主机 `POST /api/module-entities`、`PATCH /api/module-entities/{id}` 支持 `check_stats`：`{"attributes":{"str":60},"skills":{"listen":45},"source":"人物表","page":12}`。数值为 0–999 的严格整数，来源必填；只能由主机准备，模型生成草稿不能填写数值。重新批准并绑定后冻结，后续准备编辑不影响已绑定房间。传 `null` 清除草稿数值。
- 一个 `CheckRecord` 与一个 `pending_check_id` 管整次尝试。固定骰账本键为检定 ID 加阶段：对抗 `opposed:0/1`，组合 `original/push`。读旧阶段只恢复投影，后续操作复用已经固定的骰；重复确认同一选择返回原结果，不重复扣点。改选、越权或操作非当前等待项被拒绝。
- 已掷一方、等待选择、已扣一方幸运、等待孤注及最终结算均随现有存档保存。恢复房间后沿原阶段继续，保持串行调度；线索只使用最终服务端结果。完整骰账本和准备来源留在服务端，公开卡不暴露 NPC 来源说明。
