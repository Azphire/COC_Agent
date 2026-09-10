# 普通检定后处理与 SAN 自动遭遇

第十一批，基线 `167c62cbfb0bcd7e4912f68d641fa16e7ed5efe8`。沿用现有 cycle、CheckRecord、房间事务和 LangGraph interrupt，不增加模型工具或模型选择调用。对抗、组合、战斗及全模组准备不在本批范围。

## 本地规则核对

来源：`data/rules/克苏鲁的呼唤第七版规则书1907.pdf`，SHA-256 `bc8455d443ec4c64ae86ec2d6e6637889eff2cd59a4cc4686076e95b130d08dc`。下列 PDF 页均为从 1 开始的文件页，印刷页小 1；已提取相关全文并查看 PDF74、85 页面。

| 规则 | PDF／书页 | 本批执行 |
| --- | --- | --- |
| 幸运消耗（可选规则，§5.16） | 85／84 | 普通技能或属性掷出后，由本人选择，按 1:1 降低结果；总花费不得超过当前 Luck。默认关闭，由主机显式启用。 |
| 花费与成功等级 | 73、85／72、84 | 服务端对每个合法花费重新计算普通、困难、极难等级与本次是否通过。允许只改善点数；最低可购买到 02，不能购买大成功。 |
| 幸运排除项 | 85／84 | 幸运、伤害、SAN、理智损失骰及孤注结果不可修改；原大成功、大失败、枪械故障不能用幸运改变。花费幸运不获得技能成长标记（成长系统未在本批实现）。 |
| 孤注资格与一次限制（§5.3） | 74／73 | 仅适用的技能／属性失败可申请；玩家须说明额外努力或时间，目标仍可达成，由主机核准。第二次也是最后一次尝试。 |
| 后果与成败 | 75–76／74–75 | 成功达成原目标、不执行失败后果；失败适用更严重后果。本项目按本批要求，强制主机在掷骰前确认后果，再由玩家确认掷骰。书中预告后果本身是“可”采用的主持方式。 |
| 孤注排除项 | 74、77–78、88／73、76–77、87 | 幸运、SAN、战斗、伤害／损失骰及对抗不可孤注；大失败不能借孤注抵消。本批保守地不提供大失败的孤注选项。不能再孤注，不能花幸运改孤注结果。 |

限制由服务端判定，模型不能通过文字声明开放选项。当前普通检定路径不接受战斗、对抗或组合操作；规则函数也对这些上下文关闭本批后处理入口。

## 阶段、回执与权限

顺序为：普通检定原骰 → 可用时等待结果选择 → 最终裁决 → 必要揭示 → SAN 发现与串行结算 → 叙事。一个 cycle 同时只有一个检定等待项或遭遇确认项。原四项模型工具限制保留；系统发现只能处理批准的 SAN 配置。

`PendingCheck.settlement` 记录 `original_dice/original_result`、`stage`、`luck_spent/luck_before/luck_after`、`choice`、`effort/review`、`push_dice/push_result`、`consequence_status`、`final_result`。未最终裁决时 `status=pending`、`result=null`；卡片显示原骰点。最终裁决后才发布 `check.resolved`。顶层 `dice` 保持原骰，顶层 `result` 为最终裁决，二者可能不同。

普通真人检定有合法选项时等待选择；AI 默认接受原结果，不增加模型调用。远程真人只能选择自己的角色；主机可代管本地真人和 AI 席位。主机核准孤注资格与后果，但不能代替远程真人选择幸运或确认孤注掷骰。

所有更改在既有房间锁和 SQLite 事务内完成。重复原掷骰、重复同一幸运选择及重复孤注掷骰返回既有结果，不重复扣点或重掷；不同的最终选择被拒绝。已掷出的普通检定须接受或完成结算，不能取消以绕过选择。孤注绑定原 check ID，保留原失败；重新提交同一目标的普通检定、换技能或改变重复指纹不能绕过已申请的孤注。

批准后果当前支持 `condition`（增加角色状态）、`time`（增加游戏分钟）、`host_manual`（等待主机处理）。前两种由服务端执行一次，容量上限在掷骰前核对，避免拒绝后果时丢弃已掷骰；后一种保持 `pending`，直到主机记录实际处理结果，记录为 `host_handled`。伤害、物品、位置等没有自动结算；确认记录不隐含自动扣 HP 或移动实体。

## SAN 自动发现

准备效果增加：

```json
{
  "automation": "automatic",
  "repeat": "first_only",
  "action_types": ["observe", "investigate", "interact"],
  "condition": ""
}
```

原有来源、损失公式、`trigger` 和 `visibility` 继续沿用。旧配置默认 `automation=host_review`、`repeat=first_only`。`trigger=action_target` 在服务端核对行动意图、当前可见范围、已满足的访问条件及必要检定最终结果后匹配；选择一个实体本身不构成遭遇。自然语言只自动采用当前可见且当前场景中唯一的标题匹配；多个匹配中的批准恐怖效果进入主机核实，不采纳模型任选的目标直接扣 SAN。回顾、规则问题、等待、拒绝／不明确行动不自动触发。模型误提多余普通检定时，对该工具的拒绝不否定已经核准的普通目睹行动。

`trigger=entity_revealed` 使用本 cycle 实际揭示事件，支持其既有 `id` 字段，在揭示后的阶段发现。只冻结本次行动者及其角色席位；公开给所有玩家的实体不代表所有人都遭遇。`condition` 非空、旧配置或多个候选效果会进入主机确认；主机明确实际遭遇者和采用效果。自动／模型／主机路径共用同一角色、实体、效果、来源事件的遭遇回执；模型工具仅记录提案，不直接创建 SAN 检定。

新的行动事件不会自动成为新的恐怖遭遇。已有该角色／实体／效果回执时，自动路径不会重复触发；`first_only` 禁止另起重复遭遇，`host_confirmed` 必须由主机另行确认真实重复遭遇并说明依据。主机也必须明确确认实际遭遇，不能只发送一个选中实体 ID。

## HTTP 接口

路径均位于 `/api/rooms/{room_id}`；使用原有身份凭据。

| 方法与路径 | 请求／职责 |
| --- | --- |
| `PATCH /check-rules` | 主机：`expected_revision, luck_spending`；无活动 cycle 时更改可选规则。 |
| `POST /checks/{id}/roll` | 原骰；无选项直接结算，有选项进入 `choice`。重复点击不跨阶段掷骰。 |
| `POST /checks/{id}/choice` | 本人／授权代管：`operation=accept/luck/push`；幸运传整数 `spend`，孤注传非空 `effort`。 |
| `POST /checks/{id}/push-review` | 主机：`approve, reason, consequence`。批准时必须传后果类型和描述；`condition` 传状态文本，`time` 传 `minutes`。 |
| `POST /checks/{id}/push-roll` | 本人／代管：`{}`；只能掷已批准的一次孤注。 |
| `POST /checks/{id}/consequence` | 主机：`reason`；记录尚未实现的后果已由主机处理。 |
| `POST /sanity/encounter-review` | 主机：`source_event_seq, entity_id, effect_id, approve, target_member_ids, reason`；核实等待项。 |
| `POST /sanity/encounters` | 主机入口：原四项参数外，必须 `encounter_confirmed=true, reason`；重复需 `repeat_confirmed=true` 和对应配置。 |

`GET /checks` 与房间快照提供卡片和服务端计算的 `options`；他人的选项不返回给普通玩家。SAN 的阶段确认接口保持第十批形式。

## 恢复与叙事

存档保存普通后处理和 SAN 队列；旧档缺失字段采用默认值，旧普通待掷检定也能恢复。Luck 扣点已提交、图还没来得及继续的即时存档，也会接着执行后续揭示／SAN／叙事。资源与处理阶段一同回退；原骰和孤注骰保留在只追加的 `check.dice_fixed` 事件，SAN 沿用 `sanity.dice_fixed`。早于本批的普通骰从既有 `check.resolved` 事件恢复，并补记固定骰回执，不重新取随机数。重放同一阶段固定骰点，但按载入后的阶段重新执行尚未扣除的资源事务；不能把旧扣点回执直接套到已回退的资源上。

既有公开事实仍遵守第十批的单调语义：已经发给玩家的公开事实不会被撤回；`story_v1` 排除存档之后放弃的剧情事件。旧揭示的必要检定现在未通过时不会据此重新结算 SAN。恢复等待中的 SAN 则重放同一遭遇阶段与固定骰点。

新增 `check.rolled/luck_spent/push_requested/push_reviewed/consequence_pending/consequence_applied` 按原检定可见性进入 `story_v1`。发现、模型提案、主机遭遇确认保留主机审计；SAN 卡片标明来源。公开叙事只取已最终结算的检定与当前剧情事件，移除原骰、未结算选择及孤注申请文档，避免依据原失败编写最终结果。
