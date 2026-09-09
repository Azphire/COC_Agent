# 第五批开发报告

本批实现模组准备工作台、主机审阅中断与玩家公开调查板，并完成限定的两轮词法优化。最终完整回归为 **371 passed、1 skipped**；Fake 三浏览器和本地 qwen3:8b 真实流程均通过。下文保留实际失败与语义表现边界。

## 基线与范围

- 起始 HEAD：`eb13061595dd4b293bbbb6430d694b310978ef90`。开始时工作树干净，仓库及上级没有适用的 AGENTS.md。
- 基线：Ruff、前端 lint/build 通过，pytest **327 passed、1 skipped**，184.54 秒。skip 为 Windows 无符号链接创建权限，保留其语义；既有 Starlette deprecation warning 未改变。
- 不建分支或 worktree，不 commit/push。测试游戏库、checkpoint、浏览器配置和开发记录均在 `.cache`；真实模组索引复用第四批 `.cache/batch-4/knowledge-v2.db`。
- 开场使用《常暗之厢》原 DOC 提取所得 Word 物理页 **2–3**，不重新转换原文件，不处理全部 17 页。不使用 OCR、图片理解、embedding、reranker、外部模型 API 或新模型下载。

## 主要实现

新增 `backend/app/preparation/{schemas,service,room_service}.py`、`app/api/preparation.py` 和 `app/persistence/preparation_models.py`。修改 Agent 模型网关、状态、服务、工具和 LangGraph，接入知识上下文、记忆、房间存档与应用生命周期。

前端新增 `ModulePreparationPage`、`HostEntityPanel`、`InvestigationBoard` 和准备 API 类型，更新导航、游戏面板、日志角色标记与样式。新增 `check_preparation.py`、`evaluate_batch5.py` 及准备、审阅、词法测试。操作说明见[模组准备](module-preparation.md)，README、Agent/RAG 架构同步更新。

新增八张表：`module_preparations`、`module_generation_runs`、`module_entities`、`module_entity_relations`、`room_module_preparation_bindings`、`room_entity_states`、`host_review_requests`、`preparation_save_states`。独立状态列、外键和唯一索引约束实体与审阅；没有改旧表列、删除或重建用户数据库。增加覆盖 `waiting_for_review` 的活动回合唯一索引。

准备任务绑定精确 source ID/hash、范围、模型预设、版本、初始场景和必要实体，状态为 created/extracting/review_ready/approved/stale/failed。五类实体保存公开／私密摘要、来源、检定、条件、生成来源及主机编辑标记；六类关系使用简单审批列表。

生成独立于游戏回合，每次最多三批、六次请求，每批最多六实体、八关系，格式最多修复一次，与游戏共用并发一的模型网关。证据 ID 由服务端生成并先记录在实际 generation run；校验 run、source/hash、页码。空或伪造证据保留验证错误，不能批准。规范化标题、类型和 chunk 证据重叠构成保守去重条件。真实模型曾省略可选字段，因此生成 schema 现在要求显式公开摘要和证据数组；允许有意留空，但不能绕过公开和审批校验。

主机可查看最长 420 字证据摘录、编辑／批准／拒绝／返回草稿、指定初始场景与必要实体。未保存修改禁止批准；检定和公开条件有最小 JSON 编辑器。工作台显示最近 30 条准备和审阅操作。

`approved` 表示主机允许使用；`revealed` 表示条件验证后已写公开权威事件；`corrected` 表示追加修正，旧事件不删除。房间只复制批准实体及关系，不随全局任务后续编辑改变。准备模组的 `RoomEntityState` 是实体状态唯一来源，旧 ModuleSnapshot 只保留当前公开场景的兼容投影；原创《停摆的钟楼》保留原流程。

KP 可读取批准实体、私密摘要、批准关系和本 run 证据。真人和调查员读取同一公开实体集合，草稿、拒绝项及未公开实体不进入公开上下文。为维持 8192 上下文预算，KP 保留当前行动者完整角色数值，其他角色按需 `inspect_character`；省略空条件、重复来源元数据和检定存储字段。

新事实用 `propose_module_fact`／`request_host_review`，缺证据返回 needs_host_ruling。直接揭示仅接受当前房间批准 ID；转场优先批准的 leads_to，无对应关系则审阅。原 reveal_clue/update_scene 在准备房间兼容到新工具。原始证据 claim 不直接写 canonical memory。

LangGraph 增加主机审阅等待、延后工具及检定后审阅门。每 cycle 最多一次审阅，房间最多一条 pending，请求检定与审阅同时出现时先审阅。wait_reason 区分 host_review/human_roll；主机决定先落库，再恢复原 cycle/checkpoint。拒绝后仅一次安全改写，失败即待裁定并结束。批准 proposal 形成房间专属 approved 快照，不改共用准备版本。

存档保存准备版本/hash、完整房间实体快照、当前场景、公开／修正状态及审阅，既有 Agent 存档保留等待原因和 cycle 引用。重启和读档可处理仍 pending 的同一审阅；已解决审阅、已公开事实不会回退或重复公开。修正追加 public observation 并停用旧 observation；滚动摘要保留公开实体 ID。认证和 WebSocket 临时状态不入存档。

准备、完整实体、关系和证据 API 仅主机可访问；房间公开实体接口使用字段白名单；审阅正文仅 host_only，玩家只见通用等待／结果。前端调查板显示人物、地点、线索、物品、发现时间、有限来源与修正标记，继续使用原 WebSocket 快照与 JSONL/Markdown 导出。API 清单见[接口说明](module-preparation.md#api)。

## 两轮词法优化

修改前冻结原 12 条有答案、1 条无答案查询；另冻结 8 条独立表述和 1 条无答案 holdout。原脚本 SHA-256 为 `ce91a1ada41818b10101aaaaf2f28e64c893d369a40bce8e8963f8f3fcec02dc`，holdout 数据 SHA-256 为 `4b7acd3f183824e6fff6c797cefe60eaabeed50eee2ca1fa52a6336fb0b55a48`。实现不包含 query→页码映射，自动测试不读取真实模组或调用 Ollama。

失败分类与处理：

| 类型 | 观察与处理 |
|---|---|
| 多概念 | 属性、技能点、难度并列词分散；确定性拆分概念，给遗漏概念保留候选。属性生成仍失败。 |
| 跨书重叠 | 两本规则来源有相近解释；来源意图仅增加权重，保留另一来源候选。百分骰两组查询改善。 |
| 术语不一致 | “普通成功”与“常规难度”、技能 key、骰式表达不同；使用有限通用术语保留和映射。 |
| 切块边界 | 属性公式跨段、相邻页面与块分散；本批未重建切块器，仍是主要未解决项。 |
| 同页重复 | 多块占据 top-k；按 source/file/物理页去重。候选多样性提高，也有首位退化。 |
| 无答案阈值 | RRF 只融合已有有词法支持候选；两条独立无答案继续拒答。 |
| 来源／标题权重 | 标题或定义行可提高明确规则命中，也可能把相邻概念排在前面；两轮后停止调整。 |

实际采用多路 2/3-gram、短语、标题／定义行、概念候选，保留原词法通道，使用 weighted RRF；按页去重和有限跨来源／概念覆盖。没有 LLM 查询改写或额外模型调用。准备模组的场景标题＋当前行动查询构造属于实体上下文接入；检索排名只调整了两轮。

| 评测集 | 阶段 | Hit@1 | Hit@3 | Hit@5 | MRR | 无答案 |
|---|---|---:|---:|---:|---:|---:|
| 原集 | 修改前 | 58.33% | 75.00% | 75.00% | 0.6528 | 1/1 |
| 原集 | 两轮后 | 58.33% | 75.00% | 91.67% | 0.6903 | 1/1 |
| holdout | 修改前 | 37.50% | 50.00% | 62.50% | 0.4688 | 1/1 |
| holdout | 两轮后 | 50.00% | 75.00% | 87.50% | 0.6292 | 1/1 |
| 合并 | 修改前 | 50.00% | 65.00% | 70.00% | 0.5792 | 2/2 |
| 合并 | 两轮后 | 55.00% | 75.00% | 90.00% | 0.6658 | 2/2 |

第一轮合并 Hit@1/3/5=55%/75%/80%，MRR=0.6542；第二轮达到原集不退化及合并 Hit@5≥85% 目标。**合并 Hit@3≥80% 和 MRR≥0.70 未达到**，按要求停止调参。

逐条变化保存在 `.cache/batch-5/retrieval-before.json`、`retrieval-round1.json`、`retrieval-round2.json` 和 `retrieval-final.json`。主要改善：原集百分骰无命中→第2，普通／困难／极难成功无命中→第5；holdout 技能点第4→第1、跨书百分骰无命中→第2。退化：原集职业／兴趣点第2→第4、奖励骰如何掷第1→第3；holdout 大成功／大失败第1→第3。两组属性生成均仍无 top-5 命中。

第二轮可比测量的合并平均检索耗时为 **72.68 ms**，修改前 **40.53 ms**。最终并行运行模型和回归测试时复验指标完全相同，平均耗时升至 **387.81 ms**，这是有资源争用的测量，不能作为独立性能基准。

## 自动化与浏览器

新增 **44** 个参数展开后的测试，最终完整回归 **371 passed、1 skipped**，693.09 秒；此前两次完整回归均为370/1，补充证据裁剪用例后为371/1；覆盖准备／页码／hash／证据／去重、主机创作、空公开摘要、关系与快照、跨来源权限、公开幂等与修正、审阅三种结果及幂等、一次上限、拒绝改写失败、先审阅后检定、取消、重启和存档恢复、原始 claim 不提前写记忆及词法通道。另有99项运行时定向回归与16项RAG回归通过。最后收紧验证入口：丢弃未注入候选，验证与证据读取只使用真正发送到该 run 的裁剪摘录；原始候选数保留用于主机审计。

Fake Model 最终三浏览器验收通过：4 次 KP 公开回应、4 次队友动作、1 次真实引擎检定、1 次主机编辑后批准审阅、3 项公开实体（含初始场景），存档／重启／读档／续玩及两种日志导出通过。没有连接模型 API。结果在 `.cache/batch5-fake-smoke-82df65880a764e61b538b805d2e442a2/report.json`。

浏览器验收实际修复过：任务选项加载／按钮禁用等待、旧回合与新行动的竞争、读取全部 WS 记录超过 CDP 消息大小、Windows 端口释放延迟，以及续玩意外产生检定时的等待处理。权限检查改为在浏览器内扫描 WS，再返回布尔结果。早期未真正发生审阅的流程按失败保存，没有计入通过结果。

## 真实准备与人工审阅

三个独立草稿尝试均使用本地现有 qwen3:8b、并发1、8192 context，没有 OOM。每次一批、一次调用、零格式修复：38.891 秒、16.296 秒、22.062 秒，共 **3 次／77.249 秒模型时间**。

第一尝试缺少场景且摘要为空；第二尝试有正确类型数量但遗漏 evidence，被验证器阻止批准。第三尝试生成 **scene 1、location 1、item 1、clue 2、npc 1**，证据形式校验通过。人工证据核对仍发现一条草稿引用与具体描述不匹配，以及过早透露后续信息；将其限定为证据支持的公开内容，拒绝后续 NPC，编辑并批准开场 **5** 项。技术证据校验不能替代语义核对。

批准任务 `dd349b49-3857-4827-8f5e-3003dd7577c8`，source hash `a72e88d5a4cb08831bb4ab6719094447b6c995932b4760945a1abb871cad57c8`。真实游戏库位于 `.cache/batch5-real-smoke-518442cd9c0646c1bc5825b15f18f8f4/`。原文、完整 prompt、隐藏线索全文、密钥和推理均不写入本报告。

真实游玩共进行五个独立房间尝试。第一、第二、第四次没有实际审阅，明确判为未通过。第三次触发了编辑批准，但人工审计发现提议标题与观察不匹配，未把它作为最终语义验收。第五次使用准备模组专用 KP 提示、较小工具清单和严格证据入口，完成最终流程。主机拒绝未经核准的提议；此前的错误记录保留在隔离库中，没有删除历史或把调试尝试混入最终统计。

## 当前限制与第六批建议

本地 8B 模型会遗漏字段、混合前后文、要求多余检定或返回不存在的工具；字段／工具校验能阻止写入，公开叙事受独立公开上下文与 grounded claim 限制，仍不能证明任意语义正确。不要依靠一次生成就自动批准整个模组。

范围仍是小段文本准备、简单列表审阅、冻结公开卡片和单次审阅回合。没有富文本／图编辑器、完整模组结构化、战斗／追逐／疯狂／成长或公网内容分享。上下文不足仍会报错，不能无限增加实体和队友。

第六批建议优先做开场小范围语义回归、证据片段与实体描述的人工核对体验、可观测的上下文预算、角色按需读取，以及属性公式跨块检索诊断。新增评测与方案须重新明确范围，不在本批继续调参。

## 最终验收数据

最终房间 `476189fe-a423-4a29-bc46-99fcd4f77511`：真人先提交3次行动，存档、暂停、重启后端、读档后继续1次。**KP回应4次、AI队友发言2次、真实检定1次、主机审阅2次**。两次审阅均被拒绝且恢复原 cycle；第二次发生在读档续玩中。公开集合为2项：初始场景及一条已批准线索。没有公开被拒绝的提议。

主机、玩家A、玩家B三个真实 Chrome 会话验收通过：主机可查看隐藏批准实体，玩家私密接口403；玩家之间公开集合一致，WebSocket没有私密审阅正文或 keeper_summary，同一事件序号没有重复渲染；刷新、重连、后端重启、读档后恢复。审计6份公开叙事／队友上下文，均不含私密摘要或 MODULE_EVIDENCE。JSONL／Markdown导出通过。

最终游戏调用 **12次**，平均 **5908.42 ms**、中位5718.5 ms、范围3327–10547 ms、P95 10547 ms。9次检索平均 **78.11 ms**、中位62 ms、最大250 ms。五个游戏调试房间累计85次模型请求，平均7028.4 ms；另有3次准备请求，总计88次本地请求，没有付费调用。所有模型请求串行。

GPU为RTX 4060 Laptop，显存8188 MiB；运行中GPU总占用约6096–6099 MiB。Ollama报告 qwen3:8b、8192 context 的模型显存占用为6,186,378,198 bytes（约5900 MiB）。这是实测驻留值，不代表所有机器的容量保证；本次没有OOM。

**语义表现仍弱**：KP的四次回应重复了同一条已批准线索，AI队友两次仅给出安全气氛句；读档续玩还提出了与本次行动不匹配的审阅。流程和权限通过，不能据此宣称自然叙事或推理质量已通过。相比第四批，本批确实完成了一次正式线索公开，但没有验证更长剧情推进。

开发记录为 `.cache/batch-5/常暗之厢-session.md`，分PUBLIC与HOST_DEBUG；聚合审计为 `.cache/batch-5/real-final-audit.json`。少量公开示例：

- 初始场景：“你们从末班电车上醒来；车厢里没有其他乘客，列车仍在行驶。”
- 主机审阅：“等待主机确认调查内容。”“已拒绝”。
- 实际检定：failure，骰点95；以确定性引擎和公开检定卡为准。

可见区域截图已经人工检查：`.cache/batch-5/real-public-board.png`、`real-preparation-workbench.png`。文本可读，调查板布局没有溢出；准备工作台显示已索引DOC、范围、调用数、批准／拒绝数及实际实体卡片。

## 保护文件、检查和进程

最终 Ruff check、25个本批Python文件格式检查、前端lint/build、git diff --check通过。检索最终复验与第二轮指标相同。完整pytest保留既有skip语义和一项Starlette deprecation warning。

| 文件 | 前后SHA-256（相同） |
|---|---|
| `.env` | `a998c032b29c3970dd1ec8b435ed3c4bf3b3791946e154716f037dec63606b40` |
| `data/game.db` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 原始DOC | `af1033fc931f85b53109856e7bbb9695313a3adb90008e6615cc2c091c85c1d8` |

`.cache`、`.env`、游戏数据库和原始模组均由现有.gitignore忽略，未修改.gitignore。测试未写用户数据库或.env，未转换／编辑原DOC，未下载模型或调用外部模型API。临时Chrome、后端与Vite均关闭，8000／5173端口释放；用户原有Ollama服务保留。

## Git清单

未commit、push、创建分支或worktree。以下为最终状态；18个新增文件保持untracked，未暂存。

```text
 M README.md
 M backend/app/agents/model.py
 M backend/app/agents/runtime.py
 M backend/app/agents/schemas.py
 M backend/app/agents/service.py
 M backend/app/agents/tools.py
 M backend/app/knowledge/retriever.py
 M backend/app/knowledge/service.py
 M backend/app/main.py
 M backend/app/memory/service.py
 M backend/app/persistence/agent_models.py
 M backend/app/persistence/database.py
 M backend/app/rooms/service.py
 M backend/tests/test_knowledge_runtime.py
 M docs/agent-runtime.md
 M docs/rag-architecture.md
 M frontend/src/App.css
 M frontend/src/App.tsx
 M frontend/src/api/agents.ts
 M frontend/src/components/AgentGamePanel.tsx
 M frontend/src/components/RoomTimelineEvent.tsx
?? backend/app/api/preparation.py
?? backend/app/knowledge/lexical.py
?? backend/app/persistence/preparation_models.py
?? backend/app/preparation/
?? backend/scripts/check_preparation.py
?? backend/scripts/evaluate_batch5.py
?? backend/tests/test_host_review.py
?? backend/tests/test_lexical_batch5.py
?? backend/tests/test_module_preparation.py
?? docs/batch-5-report.md
?? docs/module-preparation.md
?? frontend/src/api/preparation.ts
?? frontend/src/components/HostEntityPanel.tsx
?? frontend/src/components/InvestigationBoard.tsx
?? frontend/src/pages/ModulePreparationPage.tsx
```

`git diff --stat`只统计21个已跟踪的修改文件，不包含上面的新增文件：

```text
 README.md                                     |  12 +-
 backend/app/agents/model.py                   |  10 +-
 backend/app/agents/runtime.py                 | 236 ++++++++++++++++++++++++--
 backend/app/agents/schemas.py                 |  10 ++
 backend/app/agents/service.py                 |  60 ++++++-
 backend/app/agents/tools.py                   |  34 +++-
 backend/app/knowledge/retriever.py            | 123 +++++++++++++-
 backend/app/knowledge/service.py              |  71 +++++++-
 backend/app/main.py                           |   5 +-
 backend/app/memory/service.py                 |  98 ++++++++++-
 backend/app/persistence/agent_models.py       |   8 +
 backend/app/persistence/database.py           |   7 +-
 backend/app/rooms/service.py                  |   4 +-
 backend/tests/test_knowledge_runtime.py       |  62 ++++++-
 docs/agent-runtime.md                         |  10 ++
 docs/rag-architecture.md                      |   8 +
 frontend/src/App.css                          |   5 +
 frontend/src/App.tsx                          |   3 +
 frontend/src/api/agents.ts                    |   5 +-
 frontend/src/components/AgentGamePanel.tsx    |   6 +-
 frontend/src/components/RoomTimelineEvent.tsx |   4 +-
 21 files changed, 748 insertions(+), 33 deletions(-)
```
