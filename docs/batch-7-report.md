# 第七批：玩家行动裁决、两阶段 KP、队友去重与失败恢复

## 起点与保护范围

起始 HEAD：`b003340f391b18cfaf4103f8b3b4f8e3531e8344`。开始时工作树干净；没有适用的 `AGENTS.md`。已读取 README、第五/六批报告、准备、ModuleIR、运行时、RAG 文档及第六批真实会话。未创建分支或 worktree，未 commit、push。

基线 Ruff、前端 lint/build 通过；pytest **415 passed, 1 skipped**，450.22 秒。跳过项为原有 Windows 符号链接用例；另有 Starlette 弃用与 pytest 缓存权限警告。所有后续测试使用 `.cache/batch-7` 的临时库或 `.cache/batch7-*-smoke-*` 的隔离副本。

保护检查包括 `.env`、实际配置的用户游戏库与知识库路径、《常暗之厢》DOC、第五批准备库、第六批知识库和第四批规则知识库。只读备份既有准备数据，没有修改原模组、用户库或 `.env`。最终 hash 核对见本报告末尾。

## 实现

新增 `adjudication_schemas.py`、`action_policy.py`、`adjudication.py`、`action_runtime.py`、`behavior.py`、`memory/recovery.py` 和 `persistence/adjudication_models.py`。调整 graph、Agent service、工具执行、导航、公开实体视图、API、配置和上下文窗口。新增行为验收脚本与两组自动测试；旧 Fake 场景通过测试专用 `adjudication_helpers.py` 迁移到新输出契约，生产适配器不接受旧版 KP 输出。

新增表只有 `agent_action_plans`、`agent_behavior_states`、`summary_recovery_states`。validation 合并在 plan document，并用主机事件记录验证历史；没有修改旧表列。新增 JSON 记录使用版本化 Pydantic schema。

`PlayerIntent.type` 限定为 observe、investigate、converse、move、interact、use_item、assist、wait、out_of_character、unknown。其余字段为 schema_version=1、actor_member_id、actor_character_slot_id、可空 target_kind/id/text、evidence_quote、requested_outcome、0–1 confidence、可空 ambiguity_reason、requires_clarification、可空 clarification_question。服务器检查引句确为原文子串、角色控制权、目标属于当前房间批准快照与可见范围；同名目标不能由模型自行选定。

`KeeperPlan` 只制定私有计划，包含目标、所需资料、工具、检定、揭示、转换与 revision。工具和真实检定执行后才生成 `KeeperNarration`，后者只使用公开结果与经过验证的 grounded claims。公开叙事、NPC 对话都没有提前执行计划中未发生的结果。字段范围错误仅允许一次格式修复；修复提示只包含 schema 中的字段名、错误类型和数值约束，不包含失败输出或思维内容。

独立 `ActionPolicyValidator` 验证 actor、quote、当前目标、来源节点/证据、KP 工具权限、参数、真实技能/属性、实体批准与揭示条件、可用转换、明确移动和 revision。未知意图请求澄清；场外讨论不执行角色工具。澄清结束当前 cycle，新 action 关联公开问题，不建立长期 interrupt，不调用队友。

转场同时要求：真人明确 move、原文及引句均确认移动、目标唯一匹配当前 approved transition、来源场景正确、条件或对应审阅完成、revision 一致。检查入口、询问去向、可用出口或队友建议均不提供转场授权。最终导航写入处再次验证；主机直接导航写入 `host.action`。

补读只限当前 scene、其子节点、已批准的关联非后续场景节点、active NPC 和当前出口。每计划一次，随后确定性复核，不重生成整份计划，不扩大 source hash，不改变场景或公开状态，不启动全库检索。

工具错误统一为 `invalid_arguments`、`permission_denied`、`precondition_failed`、`context_missing`、`entity_not_found`、`revision_conflict`、`already_applied`、`model_schema_error`、`model_timeout`、`internal_error`。参数错误只修复一次且不能发明目标 ID；context/revision 只允许对应的一次补读或同场景复核；权限和条件错误不盲目重试。内部错误使用安全提示。

复用工具 `run_id:index` receipt 与请求 hash，仅已失败的 context/revision 操作有一次 `:recovery` 键。完成的副作用优先读取 receipt；换参复用原键被拒绝。导航继续使用 room/request receipt，检定返回原 check 的骰点。叙事、NPC 和队友公开事件使用稳定 request ID；叙事失败后的 retry 不重放已完成工具。

`TeammateDecision` 支持 act/speak/assist/pass。pass 不生成公开台词。重复比较最近三次自身输出、本轮其他队友、真人行动和目标/动作冷却，使用 NFKC、去标点、bigram Jaccard（默认 0.65）及三句空话黑名单。最多一次修复，仍不合格即 pass。冷却默认两个 cycle，公开状态变化后解除；`BehaviorState` 保存短期目标、上次动作/目标、hash、novelty keys、冷却、cycle 和 pass 数，并纳入存档。

摘要失败保持旧 active summary 与 pending 范围，每 cycle 合计最多尝试一次，后续 cycle 重试；连续三次失败停止自动尝试，等待主机手动重建。pending 与重要公开状态事件优先进入预算内上下文。成功时原子替换摘要，连续覆盖可见事件范围，拒绝未提供的实体、证据和事件 ID。队友和摘要失败都不使已完成的 KP cycle 失败。

图共十四个节点：collect_context、plan_keeper_action、validate_player_intent、validate_keeper_plan、supplement_context、execute_read_tools、wait_for_host_review、create_checks、wait_for_human_roll、execute_state_tools、generate_keeper_narration、decide_teammates、update_summary、finish_cycle。模型调用继续串行，默认总上限 12；阶段状态与安全错误进入 cycle document。

接口增加 actions/clarifications、主机专用 plan/validation、behavior/reset、summary-status/rebuild。前端显示规划、等待、结果和澄清，提供 NPC 目标选择；主机面板展示计划、验证、恢复、receipt、队友候选拒绝和摘要状态，玩家无权读取这些内部记录。具体字段、接口和边界见 [action-adjudication.md](action-adjudication.md)。

## NPC 缺失诊断

第五批 preparation `dd349b49-3857-4827-8f5e-3003dd7577c8` 实际生成了一个 NPC，并有 evidence 绑定；该人物属于未来范围，被主机拒绝。因此不是模型完全未生成、类型丢失或 ModuleIR 丢失绑定，而是开场真实验收没有可批准使用的该人物。

没有重新批准未来人物。隔离真实验收通过通用主机创建流程添加“同行乘客（测试人物）”，保存独立 keeper/public summary、批准后绑定开场 scene node，使用 `host_authored_test` 标签。准备工作台增加人物计数和测试标记入口，调查板明确显示测试来源。NPC 仍由 KP 扮演，无独立 NPC Agent。

第六批最终四个 cycle 的只读复核确认两次工具失败分别是把图节点 `resolve_keeper_response` 当作工具、同一队友再次 `propose_action`；另有一次 `summary_failed`。本批分别用工具白名单、声明式单次队友候选和独立摘要恢复状态处理。

## 验证记录

基线与各次开发检查日志位于 `.cache/batch-7/`。开发中的第一轮全量回归为 464 passed / 7 failed / 1 skipped；旧隐式转场、阶段次数、摘要窗口及 grounded-claim 字段断言已迁移，同时修复无规则依据时被场景叙事掩盖、重要公开事件被预算挤出的运行时问题。第二轮全量回归为 **476 passed / 1 skipped**，1372.69 秒。

第一轮成功 Fake 三浏览器验收：8 cycle，1 次澄清、1 次实际转场、7 次队友 pass、14 个重复候选拦截，提前转场与公开重复均为 0。局部补读、同场景 revision 恢复、NPC、权限隔离、原 JSONL/Markdown 导出、存档重启与读档后继续行动通过。加入单次摘要故障注入后的最终结果单独记录。

真实开发验收保留了以下失败记录：新增目标标识挤占计划上下文预算；NPC 计划 confidence 使用百分数导致 schema 拒绝；一次真实检定完成后，规则依据与公开实体元数据挤占叙事预算；移动计划提供了出口但遗漏 parsed_intent.target_id，因此被要求澄清。前三类分别通过压缩重复元数据、裁剪正文尾部、安全格式反馈和公开实体视图压缩修复；移动提示改为明确的完整字段路径。已完成检定的 receipt 保留，没有重复掷骰。各次运行均无提前转场，普通观察被模型错判为 move 的尝试也被服务器拦截。

另有小模型将 NPC 回复放入已验证 grounded claim、却省略可选 npc_speech 的情况。服务端现在将本次 converse 目标的同一条公开依据标注为 NPC 台词，不生成额外内容。遗漏的可选检定/转场引用也从本 cycle 的真实公开结果补齐；规则示例加“规则参考”前缀，避免与本局服务端骰点混淆。相关三项针对性回归通过。

开发中的延续验收保留了模型误选隐藏目标、把调查误判为 move、将 entity_id 误填为 evidence_id 的被拒计划，并在同一隔离房间、原三个浏览器身份下继续明确行动。只读诊断发现小模型还会省略可空的 proposed_check；精简计划提示并要求生成格式显式输出 proposed_check/proposed_transition_id（允许 null）后，模型返回了正确的 investigate 与 spot_hidden 请求。旧持久化计划仍可读取；没有放宽原文、可见性或转场校验。生成格式适配另有 MockTransport 回归测试。阈值校准后的最终验收另开隔离副本，结果如下。

最终 Ruff check 通过；本批 **32 个 Python 文件**格式检查通过；前端 lint/build 通过。最终全量 pytest：**480 passed, 1 skipped；四个隔离进程覆盖全部 25 个测试文件，墙钟 180.28 秒**。新增用例相对基线净增 65 个，覆盖意图/策略、转场、两阶段叙事、NPC、重复/冷却、恢复与摘要；ModuleIR、规则 RAG、局部补读/global fallback 权限、主机审阅、真人检定、存档和原导出回归均在全量套件内。最终 NPC 台词归属、真实骰点呈现和规则依据修正另有 **3 passed，48.48 秒**的针对性验证。格式检查按要求限本批文件；仓库原有两个未修改 Python 文件仍不符合当前 formatter，未作无关改动。

| 指标 | Fake 完整流程 | qwen3:8b 真实流程 |
| --- | --- | --- |
| cycle 数 | `8` | `7` |
| 意图分布 | `{"unknown": 1, "observe": 4, "converse": 1, "investigate": 1, "move": 1}` | `{"observe": 3, "move": 2, "converse": 1, "investigate": 1}` |
| 澄清 | `1` | `1` |
| 含拒绝或部分批准裁决的计划 | `1` | `3` |
| 拒绝的唯一计划动作 | `1` | `4` |
| 转场申请 | `1` | `5` |
| 实际转场 | `1` | `1` |
| 提前转场 | `0` | `0` |
| 服务端完成的检定 | `1` | `5` |
| KP 回应 | `7` | `6` |
| 其中安全请求主机裁定 | `0` | `1` |
| NPC 对话 | `1` | `1` |
| 队友公开输出 | `0` | `5` |
| 队友 mode | `{"pass": 7}` | `{"speak": 5, "pass": 1}` |
| 重复候选／拦截 | `14` | `3` |
| 公开逐字重复 | `0` | `0` |
| 公开近重复（0.65阈值、最近三次） | `0` | `0` |
| 执行时工具错误 | `{"context_missing": 2}` | `{}` |
| 受限补读 | `2` | `0` |
| 摘要失败 | `1` | `1` |
| 摘要重建 | `5` | `5` |
| 普通回合全模组 RAG | `0` | `0` |
| 网关调用（含故障注入） | `35` | `29` |
| 实际适配器调用 | `34` | `28` |
| 模型调用累计延迟 ms | `32` | `189755` |
| 单次模型最长延迟 ms | `16` | `14000` |
| execution receipt 数 | `8` | `7` |

Fake 目录：`.cache/batch7-fake-smoke-310ae8c0c36840a1b9c390fa31d86c8a`；真实目录：`.cache/batch7-real-smoke-80974ee2e8ab4c43821ec3a23b8d3bbe`。两者都通过 **三个独立浏览器（主机、玩家 A、玩家 B）**、公开事件同步、主机 API 的玩家 403、调查板、NPC 私密哨兵不泄漏、保存—停止后端—重启—读档—继续行动和原 JSONL/Markdown 日志导出。原导出格式未增加或改名。

Fake 浏览器流程没有产生队友公开台词，随后按最终 0.65 阈值复核仍为零近重复；阈值校准本身由新增用例、最终全量套件和全新真实流程验证。真实第二轮“查看入口、留在原地”被模型误判为 move，服务端要求澄清；因此意图分布中的两个 move 只有一个实际执行。三项重复候选均被拦截，另有两项候选因关联了错误 action seq 被拒绝。

真实 NPC `b7a24114-eed5-424d-bedb-db989df8d36f` 的批准状态为 `approved`，绑定开场 `node_1de7131691918b24dc119191d8054413`，来源 `host_authored_test`；public/keeper summary 均保存，converse 计划的目标与该实体一致。该人物是明确标注的隔离测试扩展，不是对原模组开场“没有其他乘客”的改写。

Fake 恢复记录：`[{"schema_version": 1, "error": "context_missing", "action": "supplement", "tool_index": -1, "attempt": 1, "succeeded": true, "reason": ""}, {"schema_version": 1, "error": "revision_conflict", "action": "revalidate", "tool_index": -1, "attempt": 1, "succeeded": true, "reason": ""}, {"schema_version": 1, "error": "context_missing", "action": "supplement", "tool_index": -1, "attempt": 1, "succeeded": true, "reason": ""}]`。真实恢复记录：`[]`。重复判定原因分别为 Fake `{"empty_template": 14}`、真实 `{"unrelated_player_action": 2, "repeated_output": 3}`。策略拒绝发生在执行前，不能和工具执行错误混算。

每个完整验收仅注入一次摘要超时，该次在适配器调用前抛出，**不请求 Ollama**。因此真实网关记录 29 次、实际 Ollama 请求 28 次；Fake 的适配器调用完全没有网络。摘要失败未阻塞 cycle，随后自动重建。保留旧摘要、连续失败停止、手动重建与原子替换另由 Fake 自动测试验证。

真实模型调用保持串行，`qwen3:8b` 为既有 Q4_K_M 模型，未下载。真实已计时的 6 个主要 cycle 耗时 24.62–56.56 秒（包含数据库、浏览器等待与真人检定自动点击；读档后行动另计为一个 cycle）。GPU 开始 `6089, 8188`、最后 `6075, 8188` MiB（使用量、总量），RTX 4060 Laptop 总显存 8188 MiB；无 OOM。GPU 按 cycle 采样，不声称连续峰值测量。

开发失败尝试单独保留，未混入最终验收指标：

| 开发尝试目录 | cycle（完成） | 实际本地模型调用 | 结果 |
| --- | --- | --- | --- |
| `batch7-real-smoke-18d097b2f89b487aa70b1a6f90181777` | 3（2） | 7 | {"模型结构化输出在一次修复后仍无效": 1} |
| `batch7-real-smoke-2826229b160d4bc3a87474660a222fb3` | 3（2） | 5 | {"当前行动资料超过上下文预算，请主机缩小场景资料": 1} |
| `batch7-real-smoke-361239a2fe634148aeb11d8eced29e40` | 4（3） | 13 | {"当前行动资料超过上下文预算，请主机缩小场景资料": 1} |
| `batch7-real-smoke-6188a78c010f4cbbba2d999e09f5c52b` | 11（11） | 37 | 脚本通过；人工复核发现一对0.66近重复，随后校准阈值 |
| `batch7-real-smoke-9613df3daee449d599938e9be1873147` | 6（6） | 22 | 安全拒绝或澄清，未通过完整移动验收 |


上述开发尝试实际本地模型请求共 84 次，另有 **5 次**本地只读诊断请求（一次格式错误定位、四次相同输入的计划格式与意图诊断）。没有外部/付费 API、embedding、模型下载或多模型并行。

最终会话已保存为 `.cache/batch-7/常暗之厢-session.md`，分 PUBLIC 与 HOST_DEBUG；它是开发验收记录，不是新的正式日志导出格式。无剧透示例：真人“我查看通向前方的入口，暂时留在原地。”；测试 NPC“我也是刚醒来的乘客，只能确认我们目前还在这里。”；KP“spot_hidden检定：骰点 21，目标 25，通过。”；队友“我将协助你调查入口痕迹。”。具体 KP、真实骰点、队友与策略事件以该会话和隔离库为准。


## 当前限制与后续建议

移动验证偏保守，复杂复合句可能请求澄清。模型仍可能混淆 observe/investigate/interact/converse，服务端权限与状态约束负责限制结果；真实验收会如实记录这种分类。NPC 台词和公开叙事采用严格公开依据文本，表现力有限。bigram 只提供轻量重复检测，不能替代完整语义判断。摘要能拒绝伪造标识，但不能证明每句自然语言概括都准确。

最终七轮出现五次检定，说明小模型仍会主动增加并非玩家明确要求的检定；这些检定各有独立 check ID，并均等待真人掷骰，不是重复执行 receipt。一次转场后的 KP 叙事未通过公开依据校验，使用安全主机裁定提示；实际转场已由公开场景事件确认。规则引用虽然有来源，选段与当前问题的相关性仍需改善。

第八批建议优先验证检定的必要性、改善规则引用相关性和有依据的自然叙事，把 NPC 交谈意图与表单选择绑定得更明确，并优化小模型在复合行动上的澄清。不要以扩大全模组检索或自动推进剧情来掩盖行动意图问题。

## 最终保护与工作树

受保护清单逐项相等，`.env` 字节、用户游戏库、原模组 DOC 和既有知识库均未改变；缺失的默认知识库没有被创建。

| 受保护文件 | SHA-256（前后相同） |
| --- | --- |
| `.env` | `a998c032b29c3970dd1ec8b435ed3c4bf3b3791946e154716f037dec63606b40` |
| `data/game.db` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `data/knowledge/knowledge.db` | `原先不存在，仍不存在` |
| `data/modules/常暗之厢/常暗之厢 2-3.doc` | `af1033fc931f85b53109856e7bbb9695313a3adb90008e6615cc2c091c85c1d8` |
| `.cache/batch5-real-smoke-518442cd9c0646c1bc5825b15f18f8f4/game.db` | `b8635866f369c16488c6946d96317d0d075ddd2654c2918bee6bd960c8619908` |
| `.cache/batch6-real-smoke-6251250aeba94fad986683e666546477/knowledge.db` | `e1ebbf92b1af0a94049fa1c6115ef02d03fbd745cdc4bbd9733d984c1711ffcd` |
| `.cache/batch-4/knowledge-v2.db` | `b5e7b5eaf43a6ebc5b6545240f7209ea1d1669255559d8aabfc9be8358172ff1` |


`.cache/`、数据库、`.env`、原资料、虚拟环境与前端构建产物仍被 Git 忽略；`git diff --check` 通过。临时后端、Vite 与验收 Chrome 已关闭，8000/5173 端口释放；保留任务开始前已有的 Ollama 服务。最终 HEAD 与起始一致，未建分支/worktree，未 stage、commit 或 push。

`git status --short`：

```text
 M README.md
 M backend/app/agents/model.py
 M backend/app/agents/runtime.py
 M backend/app/agents/schemas.py
 M backend/app/agents/service.py
 M backend/app/agents/tools.py
 M backend/app/api/agents.py
 M backend/app/config.py
 M backend/app/memory/service.py
 M backend/app/models/ollama.py
 M backend/app/module_ir/navigation.py
 M backend/app/persistence/database.py
 M backend/app/preparation/room_service.py
 M backend/scripts/check_module_navigation.py
 M backend/scripts/check_multiplayer.py
 M backend/tests/test_agent_boundaries.py
 M backend/tests/test_agent_memory_tools.py
 M backend/tests/test_agent_model_adapter.py
 M backend/tests/test_agent_runtime.py
 M backend/tests/test_host_review.py
 M backend/tests/test_knowledge_runtime.py
 M backend/tests/test_module_navigation_runtime.py
 M frontend/src/api/agents.ts
 M frontend/src/api/preparation.ts
 M frontend/src/components/AgentGamePanel.tsx
 M frontend/src/components/InvestigationBoard.tsx
 M frontend/src/components/RoomTimelineEvent.tsx
 M frontend/src/pages/ModulePreparationPage.tsx
?? backend/app/agents/action_policy.py
?? backend/app/agents/action_runtime.py
?? backend/app/agents/adjudication.py
?? backend/app/agents/adjudication_schemas.py
?? backend/app/agents/behavior.py
?? backend/app/memory/recovery.py
?? backend/app/persistence/adjudication_models.py
?? backend/scripts/check_action_adjudication.py
?? backend/tests/adjudication_helpers.py
?? backend/tests/test_action_adjudication.py
?? backend/tests/test_action_recovery.py
?? docs/action-adjudication.md
?? docs/batch-7-report.md
```

`git diff --stat`（Git 不把未跟踪的新文件计入此统计；新增文件已列在上面的 `??` 行）：

```text
 README.md                                       |  23 +++-
 backend/app/agents/model.py                     |  13 ++-
 backend/app/agents/runtime.py                   |  68 ++++++++---
 backend/app/agents/schemas.py                   |  21 ++++
 backend/app/agents/service.py                   | 143 +++++++++++++++++++++++-
 backend/app/agents/tools.py                     |  67 ++++++++++-
 backend/app/api/agents.py                       |  42 +++++++
 backend/app/config.py                           |   5 +-
 backend/app/memory/service.py                   |  57 +++++++++-
 backend/app/models/ollama.py                    |  58 +++++++++-
 backend/app/module_ir/navigation.py             |  14 +++
 backend/app/persistence/database.py             |   1 +
 backend/app/preparation/room_service.py         |   3 +
 backend/scripts/check_module_navigation.py      |   6 +-
 backend/scripts/check_multiplayer.py            |   6 +-
 backend/tests/test_agent_boundaries.py          |  36 ++++--
 backend/tests/test_agent_memory_tools.py        |   8 +-
 backend/tests/test_agent_model_adapter.py       |  35 ++++++
 backend/tests/test_agent_runtime.py             |  41 ++++---
 backend/tests/test_host_review.py               |  27 ++---
 backend/tests/test_knowledge_runtime.py         |  16 +--
 backend/tests/test_module_navigation_runtime.py |  29 +++--
 frontend/src/api/agents.ts                      |   4 +-
 frontend/src/api/preparation.ts                 |   2 +-
 frontend/src/components/AgentGamePanel.tsx      |  18 ++-
 frontend/src/components/InvestigationBoard.tsx  |   2 +-
 frontend/src/components/RoomTimelineEvent.tsx   |   5 +-
 frontend/src/pages/ModulePreparationPage.tsx    |   6 +-
 28 files changed, 640 insertions(+), 116 deletions(-)
```
