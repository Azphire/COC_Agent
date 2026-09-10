# 第八批开发报告

## 1. 基线与执行边界

开始 HEAD：`7cb05c69d59e311ab682c20aa08925c57005589a`。开始工作树干净，没有重叠用户修改。已阅读 README、第七批报告、行动裁决协议和第七批真实日志，并检查计划、检定、检索、叙事、队友和摘要实现。

基线 Ruff、前端 lint/build 通过；pytest **480 passed, 1 skipped**，四个独立测试进程，墙钟169.25秒。没有创建分支、worktree、commit 或 push。未使用子 Agent；pytest 分片是普通本地测试进程。

## 2. 修改范围和存储

新增 CheckPolicyEvaluator、规则集显示解析、RuleTopicRegistry、NarrationValidator、TeammateEligibilityPolicy；接入计划裁决与检定服务、叙事网关、知识选择、摘要阈值、模型计时和前端工作台／调试面板。新增两份文档、Fake 测试、完整浏览器验收及本地非思考比较脚本。具体文件见末尾 Git 清单。

**没有新增表，也没有修改旧表列。** 访问策略复用 reveal_conditions JSON；必要性决策和叙事验证复用 ActionPlanRecord.document；重复指纹和显示信息复用 CheckRecord.document；调用阶段和耗时复用 cycle.state 与 AgentModelCall。旧持久化 JSON 可读；旧缺少必要性字段的模型提案不能授权新检定。

## 3. CheckPolicyEvaluator

创建检定需同时满足真实角色技能、当前可见目标、实体检定配置或明确风险与已实现规则、不确定因素、不同成败效果、同状态未完成过相同检定。普通观察、公开信息、普通交谈、等待和无障碍移动默认免检定。不能因为附近有 requires_check 线索就把“环顾环境”变成调查该线索。

提案包含角色、属性／技能、目标、必要性、不确定因素、成败结果、难度、实体／规则依据、风险原文、重复声明。确定性代码自行计算重复状态，不相信模型的重复声明。指纹覆盖场景、revision、模组状态、公开实体与角色卡；聊天和叙事不会单独解除重复限制。

实际创建检定时再次验证，并校对执行参数与已裁决提案完全一致。目标及依据均匹配已批准检定实体时，可补齐旧 clue_id 别名。检定配置、风险及后果不会凭空补造。部分动作拒绝后继续合法动作，测试确认不会重建完整 KeeperPlan。

## 4. Entity access policy

`automatic` 直接访问批准的公开信息；`requires_check` 要求配置并通过实际检定；`requires_condition` 要求满足可验证条件；`host_review` 要求目标匹配的主机审阅。沿用旧条件推导默认策略。工作台可编辑策略；自相矛盾或缺少必要条件的显式配置不能批准。

## 5. RuleTopicRegistry 和本地来源

Registry 对应技能／属性检定（PDF72–73）、难度（73）、大成功／大失败（77）、奖惩骰（79）。本次只读核对本地1907版规则书，查看72、73、77、79页面图像，另核对74–75失败／孤注一掷说明；没有下载资料。来源 SHA-256 `bc8455d443ec4c64ae86ec2d6e6637889eff2cd59a4cc4686076e95b130d08dc`。印刷页比 PDF 页小1。

topic 保存 mechanic、精确版本来源、页码、章节、条件、释义；仅匹配房间绑定的精确 hash 才绕过 FTS。未知问题或不同来源继续 RAG。计划中的具体概念分别检索，过滤无关证据、去重、裁剪预算，记录返回／注入／排除及原因。详见 [SOURCES.md](../backend/app/rules/definitions/SOURCES.md)。六项机器门禁、状态指纹和四种访问策略明确标为项目主持策略，不冒充官方逐条规则。

## 6. 叙事验证和 fallback

公开正文由本 run 验证过的 claim 构成，服务端加自然行动引句和不可修改的真实检定显示文本。验证公开实体、当前场景、真实检定／转场、成败一致、内部标识及私密来源。先前已公开的场景也不能冒充当前场景。中文紧贴 snake_case 的情况同样拦截。

格式与语义共享一次修复预算；无效或超时后，按实际行动、骰点、公开状态输出简短中文 fallback，不再调用模型或重复执行工具。OOM 上抛停止验收。现有精确 claim 约束限制了自由措辞，长场景复述仍可能出现；报告不把“通过事实校验”当成文学质量完美。

公共检定显示中文技能／属性名，并提供骰点、目标、难度、成功等级、奖惩骰和服务端文本。内部目标依据及重复指纹不进入玩家检定 JSON。

## 7. 队友、摘要与调用

无新线索、转场、直接交谈或短期目标触发时直接内部 pass，不建立队友模型 run。每轮最多给一个合格队友机会；保留第七批重复行为的一次修复，异常情况下会多一次请求，计入总数。摘要按事件／上下文阈值触发，每轮至多一次，保留原退避、失败上限和手工恢复。

`MODEL_THINK` 暴露原先硬编码 false 的参数，默认 false 延续基线行为；不是把原思考模式关闭后取得的性能提升。不安装或升级 Ollama，不记录思考正文。每个 cycle 显示实际模型调用次数、阶段和累计模型耗时。

## 8. 自动测试

全量回归：**525 passed, 1 skipped**，四分片全部退出0，墙钟170.17秒。日志 `.cache/batch-8/completed-shards-result.json` 及 `completed-shard-*.txt`。之后收紧规则问题入口，政策／规则相关 **61 passed**；摘要提示修正后，恢复／记忆相关 **38 passed**。全部自动测试模型为 Fake／MockTransport。

新政策文件45项，覆盖普通行为免检定、配置目标、风险、缺失后果、重复与状态变化、技能、host_review、实际结果、越界实体／场景、内部ID、修复与无副作用fallback、topic精确来源／版本降级、RAG分概念与排除无关证据、队友门控、普通cycle两次调用、部分计划保留合法动作，以及导航审计外围字段增长后的完整上下文预算。旧存读档、权限、规则引用、房间通信与恢复回归继续保留。

一次中间全量运行的原有 WebSocket 关闭测试出现 `concurrent.futures.CancelledError`；该文件单独重跑56项通过，后续完整分片也通过。未通过修改或吞掉异常来掩盖它。Ruff、前端 lint/build、32个本批 Python 文件格式检查通过；原有两个未修改文件的格式差异未做无关修正。

## 9. 真实验收及必要性

主验收目录：`.cache/batch8-real-smoke-681f6992381e45dc9707bdf46a3c708c`。前七轮完整执行；第八轮在保存—停止后端—重启—读档后继续。只完成两次必要检定：侦查25，骰点20，通过；敏捷60，骰点21，困难成功并达到普通目标。普通查看、复核公开资料、普通NPC交谈、重复调查和移动均没有实际检定。

服务端拒绝3个检定提案：普通交谈 `routine_action`、公开目标 `already_public`、移动目标不匹配 `intent_target_mismatch`。本次重复调查发生在成功公开之后，走 already_public 门禁；相同状态失败后重掷与状态变化重新允许由 Fake 策略测试覆盖。没有为了验收控制或修改真实骰点。

原模组未改动。隔离准备快照公开说明地图可见，并给现有批准地图实体显式 requires_check。NPC 是标记 `host_authored_test` 的合成乘客，非原模组人物；其批准公开回答只涉及自己刚醒来，私密哨兵未公开。原开场“没有其他乘客”与此测试人物的并置仅为权限／普通交谈测试，不把它当作原剧情改写。

第八轮第一次在发出任何模型请求前触发预算保护。只读副本诊断确认：局部模组选段在预算内，但合并导航审计等外围字段后整体超过5892字符预算。修复为按完整 envelope 计算剩余空间，再确定性缩小当前场景选段；不增加模型上下文配置、不走模组全库检索。通过既有 retry API 重试同一cycle，3次请求后完成，未重做前七轮副作用。原失败保留在 `initial-report.json`、`real-browser6.txt`、`post_load_recovery` 和 `real-continuation.txt`。

最终 Fake 三浏览器目录 `.cache/batch8-fake-smoke-299236ce54a9418cb39bc88935371824`：8轮、2次检定、22次Fake调用、0回退，完整存档／重启／读档和权限回归通过。Fake 来源为原创规则，不匹配真实PDF hash，因此规则请求走RAG，不能伪造 structured 验收数据。

## 10. 公开边界

主验收：不必要实际检定0、重复实际检定0、内部技能ID公开泄漏0、提前转场0、叙事与实际检定冲突0、NPC私密哨兵泄漏0、队友逐字及0.65阈值近重复均0、安全fallback 0。共8条KP回应、1条NPC回答、2条队友公开输出、1次实际转场。真假检定均由服务端事件与叙事中的显示文本交叉核对；不能从本次小样本推断所有自然语言输入都正确。

原有三个浏览器独立身份的事件同步、主机内部API对玩家403、公开调查板、JSONL／Markdown导出均通过。读档修复继续复用了玩家A／B的原Chrome身份，再完成同样的权限检查。所有临时8000／5173服务及Chrome进程均已停止；预先存在的Ollama服务保留。

## 11. 规则选段的实际记录与修复

主8轮实际记录 **9次structured、10次规则RAG、0次模组全库RAG**。这10次不是应保留的最终行为：审计发现模型把规则概念误填为 `coc7.move` 等内部标识；旧入口把非规则行动也当成查询，两个 `coc` 片段通过了过宽词汇相关性筛选。它们未被公开叙事采用，但确实进入过模型上下文；报告不将历史记录改为0。

最终修复：过滤含数字的内部机制标识，仅认可登记的mechanic；普通行动不因模型填了rule_concepts就启动规则检索。必须是实际规则解释问题或当前真实检定。对本房间 **19份真实计划／叙事／队友上下文**以最终代码离线重放，得到 **8次structured、0次RAG**；无模型请求，证据来自相同绑定知识库，事务回滚，详见 `.cache/batch-8/routing-replay.json`。

另追加第9个真实诊断cycle询问奖惩骰：计划上下文产生2次structured、0次RAG；小模型将意图判为需澄清，未生成规则解释，1次模型调用。它不计入前8轮行为目标和中位延迟，保留在 PUBLIC／HOST_DEBUG。包含这一诊断的原始总报告为 **9 cycle、24次模型调用、11次structured、10次历史RAG、1次澄清**。已知规则选段验证通过，规则问答意图的可靠性仍有限。

## 12. 调用数、延迟与非思考对照

| 主验收cycle | 行为 | 模型请求 | 墙钟秒 |
| --- | --- | ---: | ---: |
| 1 | 明显环境 | 2 | 31.00 |
| 2 | 公开信息 | 2 | 29.33 |
| 3 | NPC普通交谈 | 2 | 39.22 |
| 4 | requires_check调查 | 4 | 53.58 |
| 5 | 同状态重复调查 | 3 | 43.52 |
| 6 | 原地自身风险动作 | 3 | 47.77 |
| 7 | 明确移动 | 4 | 54.20 |
| 8 | 读档后继续，预算修复后重试 | 3 | 47.41 |

主8轮 **23次，平均2.875次/cycle**，达到目标。构成：计划8、叙事8、队友2、摘要5；队友确定性跳过6次。第4／7轮因新线索或转场触发队友，且达到摘要阈值，所以是4次。摘要失败0、重建5次；初始化与角色事件也占预算，是摘要仍较频繁的原因。

第七批真实7轮28次请求，平均4次；本批平均请求减少28.1%。第七批6个主要计时cycle中位48.42秒，本批前7轮中位 **43.52秒**，降低10.1%，范围29.33–54.20秒。包含第8轮重试执行段时中位45.465秒；该执行段不包含停机诊断时间。主8轮累计模型请求耗时207337ms，第9诊断再增加10500ms。硬件未变，但行动集合、骰点、缓存及测试时系统负载不同，这是观察数据，不是受控性能基准，也不将非思考配置当成本批新获得的加速。

本地 Ollama `/api/version` 为 **0.33.3**。使用相同真实上下文、8192上下文、1100输出预算、temperature0.3串行对照：

| 阶段 | think=false 秒／结果 | think=true 秒／结果 |
| --- | --- | --- |
| KeeperPlan | 17.89；格式、目标、技能及无转场检查通过 | 24.83；同样通过 |
| KeeperNarration | 5.45；公开claim和场景通过 | 10.94；公开claim和场景通过 |
| Summary（明确事件边界后） | 3.56；正确区分发布和分配 | 24.55；1100 token截断，格式失败 |

false三项另经人工复核：原文引用与目标匹配、叙事依据准确、摘要只将有明确分配事件的角色说成已分配。native接口支持布尔think；思考正文未保存，只记录长度。默认false延续原适配器，现变为可配置。

对照过程保留两项问题：第一组直接用了SQLite中转义JSON，和运行时中文输入不同，6次请求仅作诊断，不能作模式质量结论；第二组恢复UTF-8后发现两种模式都把角色发布推断为分配。已补充摘要提示明确事件边界，并只重测两次摘要。诊断文件为 `nonthinking-escaped-input-diagnostic.json`、`nonthinking-before-summary-grounding.json`；最终样本为真实目录的 `nonthinking-comparison.json`。有限样本不构成普遍正确保证。

## 13. 显存和开发尝试

RTX4060 Laptop，采样显存 **6037／8188 MiB**（开始／最后均相同），无OOM。只按cycle及诊断采样，不声称连续峰值测量。真实生成保持串行，未下载模型或升级Ollama。

| 开发失败尝试目录后缀 | cycle | 实际本地请求 | 原因 |
| --- | ---: | ---: | --- |
| `cb2d51b18cd44624be5701d08730a8c3` | 4 | 9 | 配置检定缺不确定因素，被拒 |
| `bad0460a4c7249d98e27b9641f7b1fa1` | 4 | 9 | 漏填旧clue_id别名，被拒 |
| `fec0682bcd854ff792d00bc01d8178b9` | 1 | 2 | 观察误触发附近配置检定，发现后增加行动／目标门禁 |
| `f87e188dec6d4ff183686f4f3f76bf60` | 6 | 13 | 自身动作误判移动，被转场门禁拒绝 |
| `04ee31b217124df6b8f34d5cf67cbf80` | 6 | 17 | 自身动作仍误指已公开线索，被拒 |

以上目录均位于 `.cache/batch8-real-smoke-*`，合计50次请求，不混入主验收23次。主房间含诊断24次，加模式对照14次，本批全部这些真实生成共 **88次本地请求**。初次受限沙箱Chrome的GPU进程启动失败，后来在获准的本地进程权限下完成；仅清理跟踪到的临时PID，没有停止用户进程。

## 14. PUBLIC示例和日志位置

实际公开片段：

> 真人：我环顾当前明显可见的环境，确认眼前有什么。  
> KP：你环顾四周，确认了眼前的情况。

> 侦查检定（普通）：骰点20，目标25，普通成功，通过。

完整本地日志 `.cache/batch-8/常暗之厢-session.md` 分 PUBLIC／HOST_DEBUG，主机部分保留 CheckProposal、决策、规则来源、修复原因及每轮计时。机器记录另存 report、plans、retrievals、events、模式对照与重放。PUBLIC 不展示隐藏正文、工具报错或内部skill ID；它包括实际公开线索后的剧情信息，不能当作尚未游玩玩家的模组简介。

## 15. 保护文件hash

开始与结束清单逐项一致；默认知识库始终不存在。

| 文件 | SHA-256 |
| --- | --- |
| `.env` | `a998c032b29c3970dd1ec8b435ed3c4bf3b3791946e154716f037dec63606b40` |
| `data/game.db`（0字节） | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 原模组 `data/modules/常暗之厢/常暗之厢 2-3.doc` | `af1033fc931f85b53109856e7bbb9695313a3adb90008e6615cc2c091c85c1d8` |
| 复用准备库 `batch5-real-smoke-518442cd9c0646c1bc5825b15f18f8f4/game.db` | `b8635866f369c16488c6946d96317d0d075ddd2654c2918bee6bd960c8619908` |
| 复用知识库 `batch6-real-smoke-6251250aeba94fad986683e666546477/knowledge.db` | `e1ebbf92b1af0a94049fa1c6115ef02d03fbd745cdc4bbd9733d984c1711ffcd` |
| `.cache/batch-4/knowledge-v2.db` | `b5e7b5eaf43a6ebc5b6545240f7209ea1d1669255559d8aabfc9be8358172ff1` |

后两份复用库路径位于 `.cache/`；完整绝对路径和字节数见 `protected-inventory-before.json` 与 `protected-after.json`。

## 16. 外部服务

没有调用外部生成API、商业API或下载模型。真实生成只访问本机Ollama；其余HTTP／WebSocket为本地测试服务。规则与模组只读来自用户已有文件，游戏修改限新建隔离副本。

## 17. 当前限制和后续建议

风险识别仍是保守中文规则，不能可靠理解所有表述；小模型需要明确当前目标。精确claim保证事实来源，却仍会复述历史地图信息，风险动作和转场后的叙事相关性、语言变化有提升空间。追加规则问题仍被模型请求澄清，建议下一批单独改进场外规则问题的意图分类与答复，并对历史实体的当前／先前位置建立明确叙事标签。

词汇相关性筛选不能替代完整语义判定，本批修复了实际发现的无关查询入口并做真实上下文重放。摘要不应把发布推断为分配；本批已加强提示，但缺少全面语义摘要验证器。建议将初始化／车卡管理事件从游戏摘要预算中分离，以减少不必要摘要。未实现战斗、伤害、SAN或孤注一掷，不允许模型通过后果文案修改这些状态。





## 18. 最终工作树

HEAD保持起始值；没有提交或推送。`git diff --check` 退出0，无输出。Git关于用户级ignore读取权限的提示及CRLF提示不是文件差异错误。

`git status --short`：

```text
M .env.example
 M README.md
 M backend/app/agents/action_policy.py
 M backend/app/agents/action_runtime.py
 M backend/app/agents/adjudication.py
 M backend/app/agents/adjudication_schemas.py
 M backend/app/agents/model.py
 M backend/app/agents/runtime.py
 M backend/app/agents/schemas.py
 M backend/app/agents/service.py
 M backend/app/config.py
 M backend/app/knowledge/service.py
 M backend/app/memory/recovery.py
 M backend/app/memory/service.py
 M backend/app/models/ollama.py
 M backend/app/preparation/room_service.py
 M backend/app/preparation/schemas.py
 M backend/app/preparation/service.py
 M backend/app/rules/definitions/SOURCES.md
 M backend/tests/adjudication_helpers.py
 M backend/tests/test_action_adjudication.py
 M backend/tests/test_action_recovery.py
 M backend/tests/test_agent_boundaries.py
 M backend/tests/test_agent_memory_tools.py
 M backend/tests/test_agent_runtime.py
 M backend/tests/test_host_review.py
 M backend/tests/test_knowledge_runtime.py
 M frontend/src/api/agents.ts
 M frontend/src/api/preparation.ts
 M frontend/src/components/AgentGamePanel.tsx
 M frontend/src/components/RoomTimelineEvent.tsx
 M frontend/src/pages/ModulePreparationPage.tsx
?? backend/app/agents/check_policy.py
?? backend/app/agents/narration.py
?? backend/app/agents/teammate_eligibility.py
?? backend/app/rules/display.py
?? backend/app/rules/topics.py
?? backend/scripts/check_check_policy.py
?? backend/scripts/check_nonthinking.py
?? backend/tests/test_check_narration_policy.py
?? docs/batch-8-report.md
?? docs/check-and-narration-policy.md
```

`git diff --stat`（不计未跟踪新增文件；新增文件列在上面的`??`项）：

```text
.env.example                                  |   5 +-
 README.md                                     |  19 +++
 backend/app/agents/action_policy.py           |  46 ++++--
 backend/app/agents/action_runtime.py          | 216 ++++++++++++++++++++++++--
 backend/app/agents/adjudication.py            |  52 ++++++-
 backend/app/agents/adjudication_schemas.py    |   9 +-
 backend/app/agents/model.py                   |   8 +-
 backend/app/agents/runtime.py                 |  10 ++
 backend/app/agents/schemas.py                 |   4 +
 backend/app/agents/service.py                 |  37 ++++-
 backend/app/config.py                         |   3 +
 backend/app/knowledge/service.py              | 129 ++++++++++-----
 backend/app/memory/recovery.py                |  25 ++-
 backend/app/memory/service.py                 |  21 +++
 backend/app/models/ollama.py                  |   2 +-
 backend/app/preparation/room_service.py       |  21 +++
 backend/app/preparation/schemas.py            |   3 +
 backend/app/preparation/service.py            |  12 ++
 backend/app/rules/definitions/SOURCES.md      |  16 ++
 backend/tests/adjudication_helpers.py         |  26 +++-
 backend/tests/test_action_adjudication.py     |   5 +-
 backend/tests/test_action_recovery.py         |   8 +-
 backend/tests/test_agent_boundaries.py        |  13 +-
 backend/tests/test_agent_memory_tools.py      |   8 +-
 backend/tests/test_agent_runtime.py           |  16 +-
 backend/tests/test_host_review.py             |   2 +-
 backend/tests/test_knowledge_runtime.py       |   2 +-
 frontend/src/api/agents.ts                    |   2 +-
 frontend/src/api/preparation.ts               |   2 +-
 frontend/src/components/AgentGamePanel.tsx    |  10 +-
 frontend/src/components/RoomTimelineEvent.tsx |   7 +-
 frontend/src/pages/ModulePreparationPage.tsx  |   6 +-
 32 files changed, 622 insertions(+), 123 deletions(-)
```
