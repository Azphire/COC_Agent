# 玩家行动裁决与失败恢复（第七批）

真人提交的 action event 是行动依据。模型先生成私有 `KeeperPlan`，服务端裁决并执行，最后才请求公开的 `KeeperNarration`。玩家的输入、计划中的愿望和队友的建议都不等于已经发生的结果。

## 数据契约

新契约位于 `backend/app/agents/adjudication_schemas.py`，继承禁止额外字段的 Pydantic `DomainModel`，持久化记录使用 `schema_version=1`。

| 契约 | 职责 |
| --- | --- |
| `PlayerIntent` | type、行动者 member/character slot、可选 target kind/id/text、原文 evidence quote、requested outcome、confidence、ambiguity reason、澄清标记和问题 |
| `KeeperPlan` | plan/cycle/current scene、目标和来源 ID、required context、至多四项原始工具、可选 check/reveal/transition、下一阶段、主机审阅、短依据摘要、expected revision |
| `ValidatedActionPlan` | approved/partially approved/clarification required/host review required/rejected、允许与拒绝的动作及原因、唯一等待原因、权威 revision |
| `KeeperNarration` | 公开叙事、可选 NPC speech、grounded claims、公开实体引用、真实 check/transition 引用、needs host ruling |
| `ContextGap` / `RecoveryDecision` | 缺失内容、受限范围、错误类别、唯一一次恢复决策及结果 |
| `TeammateDecision` | act/speak/assist/pass、目标与文本、短理由、相关真人 action seq、公开实体、novelty keys、confidence、可选短期目标 |
| `BehaviorState` | 短期目标、上次动作与目标、公开文本 hash、novelty keys、cooldowns、上次 cycle、连续 pass 数、更新时间 |
| `SummaryRecoveryState` | 成功覆盖 seq、pending 起止、stale、失败数、安全错误、下次 cycle 序号、上次尝试 cycle/time、是否停止自动恢复 |

意图类型限于 `observe`、`investigate`、`converse`、`move`、`interact`、`use_item`、`assist`、`wait`、`out_of_character`、`unknown`。所有类型由模型解析；服务端词表仅辅助核实显式移动。

短 `rationale_summary` 只记录授权和条件等决策依据，不请求或存储思维链。模型网关继续拒绝 reasoning/chain-of-thought/think 字段。

`proposed_check` 与 `proposed_transition_id` 标注 `x-explicit-output`：Ollama 生成语法要求明确输出对象/ID 或 `null`，避免小模型悄悄省略关键决策。该注解在发送语法前移除，Pydantic 默认值继续兼容旧计划；它不强迫产生检定或转场。计划模型只列出读取与提议工具，状态变化使用高层字段，由服务端展开与验证。本次玩家原文放在资料最后，降低其他场景文字对意图分类的干扰。

## 服务端裁决

`ActionAdjudicationService` 从当前房间的成员、角色快照、触发事件、批准结构快照、已公开实体和导航状态构造 `ActionFacts`。`ActionPolicyValidator` 独立于 prompt 执行：

1. 核实真人角色席位与 member 的控制权，cycle 归属及原文子串。
2. 核实目标属于当前可见范围；同名候选不唯一时要求澄清。玩家显式选择的 NPC ID 不能被模型替换。
3. `unknown` 结束本轮并澄清，`out_of_character` 不执行角色工具或检定。
4. 核实当前 scene、navigation revision、来源节点和实体的批准范围、实际读取过的 evidence ID。
   当前 scene node 可以作为观察目标；显式移动可引用批准出口的目的地元数据，但不能将目的地正文当作已读取来源。
5. 核实工具注册、KP 角色权限和 Pydantic 参数；合并高层 check/reveal/transition 后仍不得超过四项。
6. 核实检定技能/属性存在于真实角色快照；揭示条件与当前 cycle 的真实检定相匹配。
7. 状态工具执行前再次验证；模型不能修改验证结论。

允许的动作分成只读、提议和状态变更。检定请求及主机审阅先于状态变化；未掷骰时不会运行结果叙事。审阅拒绝后最多请求一次安全叙事，失败则使用确定性的主机裁定提示。

澄清事件 `action.clarification_requested` 是公开问题。当前 cycle 正常结束，不保留长期 interrupt，也不调用队友。新 action 使用 `clarification_event_seq` 关联同一真人先前收到的问题。

## 转场保护

自动转场要求全部满足：`intent.type=move`、真实原文引句、原文与引句均明确表达移动、目标唯一匹配当前批准出口、出口来源为当前 scene、条件满足或相应主机审阅已批准、navigation revision 一致。

“查看入口”“听门后的声音”“询问同伴是否过去”、条件句、否定句、第三方建议都不构成移动授权。可用出口本身也不构成授权。目标不明确时请求澄清；禁止把检定成功自行解释为下一场景。

`ModuleNavigationService.transition` 在状态写入处重新检查移动计划，AI 调查员不能直接转场。主机直接操作继续使用主机认证，并额外写入 `host.action`。没有当前 approved transition 的模型请求不能借主机审阅自动发明出口；主机仍可使用已有显式导航操作。

## 错误、补读与 receipt

| 错误 | 自动处理 |
| --- | --- |
| `invalid_arguments` | 每计划一次结构化参数修复；仅 schema、原参数、当前可见目标；不生成新的目标 ID |
| `permission_denied` | 拒绝，不重试 |
| `precondition_failed` | 不重试；符合批准转场审阅语义时请求一次主机处理 |
| `context_missing` | 每计划一次受限补读，随后确定性复核 |
| `entity_not_found` | 当前同名目标歧义时澄清，否则停止，不扩大检索 |
| `revision_conflict` | 重读权威导航；仅同场景允许一次更新 revision 与复核，场景已改变则停止旧计划 |
| `already_applied` | 返回原 receipt，不重放副作用 |
| `model_schema_error` | 网关至多一次格式修复，计入总预算 |
| `model_timeout` | 不自动反复调用；主机可手动 retry |
| `internal_error` | 玩家只得到安全提示，堆栈和本地路径不进入公开事件 |

`ContextSupplementService` 只读取当前 scene、本场景子节点、批准关联的非后续 scene 节点、active NPC 和当前批准 transition。补读不修改 source hash、结构版本、当前场景或公开状态，不调用全库 RAG。正文每节点最多三块、每块最多 420 字；实体摘要也有长度限制。补读失败仍由主机裁定，不重新生成整份 KeeperPlan。

复用 `agent_tool_receipts`：原操作键为 `run_id:index`，请求内容 hash 绑定参数。相同键换参数会被拒绝；成功键返回原结果。仅对已失败的 context/revision 操作允许一个 `:recovery` 键，恢复键同样持久化。状态变化与 receipt 位于同一房间事务，dispatch 使用 savepoint，失败后刷新 ORM 记录。

导航继续复用 `(room_id, request_id)` 的导航 receipt；真人掷骰复用原 check record，重复 roll 返回原骰点。公开叙事以 run ID 为 request ID，NPC 事件以 `run_id:npc` 为键，队友采用结果以 `cycle_id:member_id` 为键。叙事重试复用已完成的计划和工具记录。

## 队友行为

队友只接收公开场景、公开实体、自身角色和自身记忆。`pass` 不发布台词。每名队友每 cycle 一个初始候选；不通过策略时最多一个修复候选，再失败则 pass。

重复检查比较最近三次自身公开输出、本 cycle 其他队友输出、真人刚提交的行动，以及持久化目标/动作冷却。采用 Unicode NFKC、空白与标点归一化、中文 bigram Jaccard；默认阈值 `TEAMMATE_SIMILARITY_THRESHOLD=0.65`。黑名单仅三句明显空话，不扩展为意图分类词库。

相同 action type + target 默认冷却 `TEAMMATE_COOLDOWN_CYCLES=2` 个 cycle。场景、公开实体或真实检定结果变化会改变公开状态指纹，从而解除旧冷却。被拒候选只保留在主机运行与拒绝记录中，不能进入 PUBLIC。

短期目标和 novelty key 只有来自提供给该队友的安全素材时才保存；未验证字符串被丢弃。主机可以查看和 reset，玩家不能读取完整状态。

## NPC

NPC 由原 KP 扮演。玩家目标选择只列出当前导航 active NPC 中已公开的人物。NPC speech 必须引用本次 converse 的目标，并以该 NPC 的已公开摘要为依据；keeper summary 不进入公开叙事模型的上下文。

本批采用保守的逐字公开依据规则，NPC 台词为 public summary 中的真实子串，避免把猜测写成新剧情事实。主机可以批准更丰富的公开摘要。尚不支持独立 NPC Agent 或对话树。

小模型可能将人物回复放进 grounded claim 而省略可选的 npc_speech。对已验证的 converse 目标，服务端可将同一条已验证 NPC claim 标注为人物对话，不补造台词。真实 check/transition 引用也可以从本 cycle 的公开结果自动补齐；规则原文带“规则参考”前缀，避免把规则示例误写成本局结果。

准备工作台显示批准人物计数，并提供合成测试实体标记。`tags=["host_authored_test"]` 在公开实体中映射为 `origin=host_authored_test`，调查板显示“主机测试人物”。这不表示该人物来自原模组。

## 摘要恢复

失败保留最后一份 active summary，记录 pending 连续可见事件范围。后续上下文在总预算内优先保留 pending 中较早事件，再补充最近事件；当前真人行动始终有独立字段。权限过滤先于事件窗口选择。

每 cycle 全部档案合计最多一次自动摘要尝试。失败后的下一 cycle 可重试，连续失败达到 `SUMMARY_MAX_FAILURES=3` 后停止自动调用。主机手动重建为同步操作，不增加后台队列；同一房间的摘要操作串行化。

成功摘要从旧覆盖终点后依次选取一段可见事件，不跳过中间事件后宣称已覆盖；预算不足时缩短尾部。成功事务内原子停用旧 summary 并新增替代记录。实体、evidence、node 和事件 ID 必须来自输入资料，不能产生不存在的标识。

## 图、权限和存档

图顺序：`collect_context → plan_keeper_action → validate_player_intent → validate_keeper_plan → supplement_context → execute_read_tools → wait_for_host_review → create_checks → wait_for_human_roll → execute_state_tools → generate_keeper_narration → decide_teammates → update_summary → finish_cycle`。

数据库里的 cycle JSON 保存每阶段状态和安全错误，LangGraph checkpoint 保存控制流程。检定和审阅继续使用已有 interrupt/resume，任何时刻只有一个等待原因。队友/摘要失败不阻塞已完成的 KP cycle。模型调用串行，默认每 cycle 上限 `AGENT_MAX_CALLS=12`，参数修复、格式修复、队友修复及摘要都计入预算。

新增表：`agent_action_plans`、`agent_behavior_states`、`summary_recovery_states`。完整 validation 合并在 action plan document，历史验证写入主机事件，避免新增同义表。旧表未增加列，继续支持 `create_all` 初始化。

新增等价 API：

| 路由（房间前缀 `/api/rooms/{room_id}`） | 权限 |
| --- | --- |
| `POST /actions`、`POST /clarifications` | 已认证、控制该真人角色的成员；主机可按现有规则代交 |
| `GET /cycles/{cycle_id}/plan`、`GET /cycles/{cycle_id}/validation` | 仅主机 |
| `GET /teammate-behavior`、`POST /teammate-behavior/{member_id}/reset` | 仅主机，reset 要求无活动回合 |
| `GET /summary-status`、`POST /summary-rebuild` | 仅主机，重建要求无活动回合 |

玩家 cycle 返回只包含状态、等待原因、安全错误和澄清信息，完整 state/plan/validation 不下发。主机调试面板显示计划、裁决、恢复、receipt、叙事、队友拒绝和摘要状态，不显示无限制 prompt 或思维链。

存档在现有 `AgentSaveState` 中加入版本化 adjudication 数据，恢复 BehaviorState/cooldown 和摘要恢复状态；加载较旧存档时清理其未包含的对应状态。原有已完成检定、公开事件和导航 receipt 的终态语义保持不变。

## 验证及限制

自动测试全部使用 Fake Model。`backend/scripts/check_action_adjudication.py` 使用隔离副本、三个独立 Chrome profile、真实 UI 提交/掷骰及权限 API 检查。`--real --config .cache/batch-6/real-config.json` 才调用本机已安装的 `qwen3:8b`，不调用外部 API、不下载模型。验收脚本的单次摘要超时注入与 Fake 工具故障注入不属于生产逻辑。

当前限制：移动词表偏保守，复杂复合句可能要求澄清；公开结果和 NPC 台词采用严格依据文本，表现力有限；bigram 重复检测不等同语义理解；较大场景仍需合理准备与上下文预算；摘要防止伪造 ID，但不能证明每个自然语言概括都准确。继续复用旧工具定义和历史测试场景，生产网关不接受旧版 KP 输出。
