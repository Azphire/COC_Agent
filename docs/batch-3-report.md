# 第三批开发报告：最小可玩的 Agent 回合闭环

> 后续状态：第十／十一批已实现 SAN、疯狂状态、批准遭遇自动触发及普通检定幸运／孤注后处理。本文验证数字与范围为当批历史记录；当前接口见 [第十一批说明](check-settlement-and-encounters.md)。

验收日期：2026-09-09。本批已实现并验证真人行动、AI KP、服务端检定、LangGraph 人工中断／恢复、AI 队友、事件／记忆、存档重启续玩。没有创建分支或 worktree，没有 commit、push，没有修改用户 `.env` 或游戏数据库。

## 1. 起始 HEAD

`b7ef90f1586dcde38ce2a6fd284a3dc3f5842195`。起始工作树干净；仓库及父目录未发现适用的 AGENTS.md。实现期间没有回退用户文件。

## 2. 基线检查

Ruff check、前端 lint/build 通过，原有 **215 项 pytest 全部通过**，耗时 41.82 秒。已阅读第二批报告、多人协议、规则来源、模型适配器、房间事务／广播／权限／存档及角色快照实现。既有依赖含 LangGraph 与 SQLite checkpointer；本批没有安装新模型或增加框架依赖。

Windows 默认 pytest 临时目录和缓存曾受沙箱权限限制，随后统一使用仓库 `.cache` 下的独立 basetemp/cache_dir。Chrome 调试需要沙箱外启动，使用已获自动批准的隔离浏览器验收命令；没有更改浏览器全局配置。

## 3. 主要文件

| 文件／目录 | 作用 |
| --- | --- |
| `backend/app/agents/{schemas,modules,service,tools,runtime}.py` | 档案／模组／检定模型，领域命令，封闭工具，LangGraph 图 |
| `backend/app/agents/{model,security}.py`、`models/ollama.py` | 串行模型网关、一次格式修复、脱敏、原生 Ollama 适配 |
| `backend/app/agents/definitions/stopped_clock.yaml` | 原创模组快照来源 |
| `backend/app/rules/checks.py`、`rules/definitions/SOURCES.md` | 确定性 CoC 7 判定及本地来源 |
| `backend/app/memory/service.py` | 三层记忆与按权限构建上下文 |
| `backend/app/persistence/agent_models.py`、`api/agents.py` | 九张增量表和 REST API |
| `backend/app/{main,config}.py`、`persistence/database.py`、`rooms/service.py`、`models/base.py` | 生命周期、配置、既有事务／存档接入、模型结果类型 |
| `frontend/src/pages/AgentProfilesPage.tsx`、`components/AgentGamePanel.tsx`、`api/agents.ts` | 档案、模组绑定、真人行动、检定卡片、状态、主机调试 |
| 前端 App、RoomsPage、rooms API、App.css | 导航、房间投影及事件展示 |
| `backend/tests/test_agent_*.py` 四个文件 | 68 项新增测试，保留全部原测试 |
| `backend/scripts/check_agent_cycle.py`、`check_multiplayer.py` | Fake／真实模型三浏览器验收，修复长调用期间 CDP ping 超时 |
| `.env.example`、`README.md`、`docs/agent-runtime.md`、本报告 | 配置、玩法、机制和证据 |

## 4. 数据库

新增 `agent_profiles`、`module_snapshots`、`room_agent_bindings`、`agent_cycles`、`pending_checks`、`agent_runs`、`agent_memories`、`agent_tool_receipts`、`agent_save_states` 共九张表。保留原八张表，不改旧列。SQLite checkpointer 使用独立文件；默认跟随游戏数据库目录，显式配置可覆盖。全部测试数据库位于 `.cache` 或 pytest 独立临时目录。

## 5. 检定子集与来源

支持角色快照中的属性／技能、普通／困难／极难、01 大成功、大失败、成功等级与目标达成分别记录、0–2 枚奖励／惩罚骰及抵消、三种可见性、真人或主机触发、AI 自动检定和重复解决幂等。

本地《克苏鲁的呼唤第七版规则书1907.pdf》PDF **72–73** 页核对难度，**77** 页核对大成功／大失败，**79** 页核对奖励／惩罚骰；《第七版调查员手册1.20.pdf》PDF **9** 页核对 00+0=100。已查看关键页图像。按本地译本，以本次难度的成功上限是否达到 50 决定大失败范围；此选择在 SOURCES 明确记录。只记录必要公式与位置，没有复制大段规则书。

## 6. 原创模组

《停摆的钟楼》包含广场、维修间、齿轮平台三场景，两名 NPC，四条线索及确定性结束条件。一条线索需要真实侦查成功，一条 keeper_only 信息永不公开。YAML 经 Pydantic 引用验证，规范 JSON 计算 SHA-256，绑定时冻结完整快照。运行中不读取后来修改的 YAML。

## 7. Agent Profile 与绑定

主机可手动创建、编辑档案，或生成可编辑草稿后确认保存。未确认草稿不能绑定。KP 绑定主机管理身份，调查员绑定已有角色快照的 AI 席位；每席位和同房间每档案只有一个启用绑定。档案只选择后端 `default` 模型预设，不保存 API Key。档案启用期间禁止编辑。

## 8. 工具及权限

KP：inspect_public_state、inspect_character、request_skill_check、reveal_clue、update_scene、send_narration、write_memory。调查员：inspect_public_state、inspect_own_character、speak、propose_action、write_private_memory。

工具使用严格 Pydantic 参数和角色白名单，检查房间／run／绑定／角色，线索检查场景、前置线索和关联真实检定。每个 run:index 回执持久化参数哈希，重复执行复用结果。没有模型写骰点、HP/MP/SAN/Luck、SQL、文件或网络的工具。调查员 speak/propose_action 合计每轮最多一次。

KP 私密决策不能直接发布自由文本：send_narration 只申请叙事，后续公开叙事节点使用独立公开上下文；检定原因由服务端生成；公开 observation 从权威事件重建。隐藏线索关联 ID 不进入玩家检定投影。

## 9. 三层记忆

先过滤权限，再取最近 25 条可见事件；每个 Agent 单独维护带覆盖序号的滚动摘要；结构化记忆保存 observation、belief、goal、relationship、summary，按 public／agent_private／keeper_only 隔离。记忆按目标／摘要、重要性、关键词和时间排序进入预算。supersedes 使旧记忆失活而不删除。belief 不提升为世界事实，正式公开线索自动生成 observation。摘要每轮最多一次，失败保留旧摘要并继续游戏。

## 10. LangGraph 与 interrupt/resume

节点顺序：collect_context → keeper_decide → validate_keeper_actions → execute_keeper_tools → wait_for_human_roll → resolve_keeper_response → narrate_publicly → run_teammates → update_memories → finish_cycle。

真人 pending check 触发 interrupt；服务端读取真实角色数值、复用 DiceService 掷骰并提交事件后，使用同一 cycle thread 的 Command(resume) 恢复。图只存控制字段／记录引用，房间数据库是事实来源。成功模型输出和工具回执可重放；重复 roll/resume 不新增骰点或队友行动。

## 11. 调用与循环上限

全进程单事件循环模型并发数 **1**；默认每轮最多 **6 次实际模型请求**，包含格式修复、一次前置条件计划修正和摘要；每 run 最多 **4 个工具**；每轮最多 **1 次检定**；每队友最多一次行动。Agent 消息不启动新回合，主机重试不重置预算。一般一队友无检定 3 次、有检定 4 次；计划修正再用 1 次。

KP 计划漏切换场景、或为无需检定的线索申请检定时，先记录 rejected run，不执行原工具，允许一次有明确错误反馈的替代计划。上下文使用完整 JSON 长度计算，包含列表分隔符及修正反馈，超预算明确失败。

## 12. 存档与重启

与既有房间存档同事务保存模组快照／状态、绑定与档案配置、消费序号、活动 cycle 控制字段、检定、活动记忆、摘要和 checkpoint thread 引用。读档不会回滚已解决的骰子、重跑已完成回合、删除事件或复活已离开成员。修改过的共享档案必须恢复原配置后才能读档，避免影响其他房间。

后端重启将正在调用模型的回合置为可重试失败，已持久化的真人中断仍可掷骰；存档里的未完成状态按当前权威结果继续或明确要求重试／取消。

## 13. REST API 与 WebSocket

新增主机 Profile CRUD／generate-draft、model-presets、modules；房间 agent-config、module、agent-bindings、actions、agent-cycle／cancel／retry、checks／roll／cancel、agent-runs、memories。详细路径见 [运行机制](agent-runtime.md#api-与事件)。

复用原 room.event／room.snapshot／room.synced 协议。事件包括 module.bound/completed、action.submitted、check.requested/resolved/cancelled、keeper.narration、clue.revealed、scene.updated、agent.bound/unbound/configured/cycle_changed/spoke/action_proposed；run_failed 和 memory_written 只给主机。玩家不能读取运行上下文调试 API。

## 14. 最终静态检查与自动测试

- Ruff check：通过。
- 本批全部 **23 个 Python 文件** Ruff format --check：通过。
- 最终全套 pytest：**283 passed，1 warning，336.58 秒**；原有 215 项全部保留，新增 68 项。唯一警告来自 Starlette 使用的 AnyIO BlockingPortal 弃用别名。
- 前端 `npm run lint`：通过；`npm run build`：通过，TypeScript 与 Vite 构建成功，主 JS 247.29 kB。
- 新增测试覆盖属性／技能、奖励惩罚、真实快照、可见性、工具拒绝、并发、幂等、上下文、记忆、摘要、interrupt／恢复、重启、取消、重试、模型超时／修复／上限、凭据过滤和隐藏推理拒绝。

## 15. Fake Model 完整验收

最终成功证据：[report.json](../.cache/agent-fake-smoke-6426f69d8197468684983ac854181c0f/report.json)。真实 HTTP、SQLite、WebSocket、三浏览器、存档、实际关闭／重启后端、读档续玩、双方玩家与主机导出均通过，SQLite integrity_check=ok。第一轮真实服务端骰点 65、技能 25、失败；后续仍能揭示无需检定的交接簿并生成记忆。两个回合各 4 次 Fake 调用，第二轮包含摘要；草稿确认流程也通过。Fake 传输显式禁止访问真实模型网络。

## 16. 三浏览器隔离

主机、真人、另一玩家使用三个独立 Chrome user-data-dir。验证独立 token、另一玩家不能代掷、公开结果一致、KP 隐藏信息不出现在玩家页面／WebSocket／日志、调查员 prompt 无 keeper brief。三方分别下载 JSONL／Markdown，无密钥、token、完整 prompt 或思考内容。验收检查浏览器异常，并保留截图。已人工查看真实模型的 [Agent 面板](../.cache/agent-ollama-smoke-aaf59c40c3024f7798572e7c0b1863f1/player/agent-visual-review.png)：状态、行动表单、失败骰点和已公开线索显示正常。

## 17. 真实 qwen3:8b 验收

最终成功证据：[report.json](../.cache/agent-ollama-smoke-aaf59c40c3024f7798572e7c0b1863f1/report.json)。使用已安装 qwen3:8b（8.2B，Q4_K_M），无模型下载。三轮分别为首次检查、补充阅读交接簿、重启读档后继续行动；调用数 **5 + 4 + 5 = 14**，均串行且各轮不超过 6。第一次侦查实际 **34/25 失败**，未把失败当成功；通过后续公开交接簿验证线索和记忆；续玩再次需要检定时由真人按钮完成。全部临时进程关闭，8000/5173 释放。

| 顺序 | 模型阶段 | 耗时 ms |
| --- | --- | ---: |
| 1 | KP 初始计划（前置条件拒绝） | 5954 |
| 2 | KP 一次计划修正 | 6016 |
| 3 | 检定后决策 | 3389 |
| 4 | 公开叙事 | 3108 |
| 5 | 队友 | 9843 |
| 6–9 | 阅读交接簿：KP／叙事／队友／摘要 | 3922 / 3577 / 4375 / 1593 |
| 10–14 | 读档续玩：KP／检定后决策／叙事／队友／摘要 | 6969 / 2952 / 3438 / 3187 / 16750 |

成功验收开始时模型已经驻留；本批首次冷启动请求为 **18,608 ms**。最终成功流程首请求 5954 ms，后续范围 1593–16750 ms；耗时包含服务端网关等待／记账，不是纯生成速度。成功报告中两次摘要 token_usage 尚为 null，其余实际输入最大 4227 tokens；Ollama 日志补充确认摘要输入 181／1374、输出约 37／661，均未截断。随后已补齐摘要 token_usage 持久化。

Ollama `/api/ps` 显示 context_length=8192、size=size_vram=6,186,378,198 字节（约 5.76 GiB）。RTX 4060 Laptop 8GB 的 nvidia-smi 观察约 6035–6141 MiB，最终 6046/8188 MiB；这是观察值，不是连续采样的峰值。日志显示 37/37 层 GPU offload，成功流程各请求 truncated=0；**未发现 OOM、CPU 降级或上下文截断**。

没有把前期失败隐藏成一次成功：六次调试验收共 **34 次实际请求**，加最终 14 次，本批真实模型合计 **48 次**。调试暴露并修复了 Ollama 较大 maxLength 语法编译失败、模型漏场景前置条件、公开叙事资料隔离、上下文长度边界，以及浏览器等待旧 cycle 的竞态。未完成验收均使用隔离数据并清理进程；其中一次仅为停止旧脚本等待，修改了那次 `.cache` 数据库的完成状态，未用该结果作为通过证据。最终成功验收没有人工写入数据库或伪造骰点。

## 18. 外部 API

**外部模型 API 调用 0，付费 API 调用 0，模型下载 0。** 模型仅连接 `127.0.0.1:11434`；技术核对阅读了官方 LangGraph／Ollama 文档，不属于模型 API 调用。现有 Ollama 服务由用户管理，验收没有停止该服务。

## 19. 用户数据库哈希

用户 `data/game.db` 起始为空文件。验收前后 SHA-256 均为：

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

最终再次检查相同；`.env` 未更改。没有对用户数据库启动迁移或写入。

## 20. 当前限制与第四批入口

单主机、单 Uvicorn worker；固定字符预算不等同精确 tokenizer；8GB 显存串行运行，一轮可能需要几十秒。qwen3:8b 仍可能申请不必要的检定、提出被拒绝的第二次队友行动、编写未证实的叙事细节；数据隔离和确定性工具保护事实，但不保证语言质量。模型语义表现没有作为 pytest 的随机硬断言。摘要可能较简略，后续可改进相关性与覆盖选择。

第三批当时仅原创短模组和核对过的最小检定。后续已接入规则 RAG、SAN／疯狂，以及普通检定幸运消耗和孤注一掷；战斗、追逐、成长仍未实现。正式游戏以检定卡片、场景事件、已揭示线索为准。已掷普通检定必须完成结果选择或后果处理，不能通过取消、换回合或无限重试绕过。

第四批把规则书／模组检索接到 `memory.service.build_context` 的权限过滤后、预算选择前；返回来源页码及可见性，沿用相同脱敏与事实工具，先做小范围章节／关键词检索。不要把整本 PDF 或 KP 私密段落直接注入调查员，不用检索结果替代服务端判定。

## 21. git status --short

```text
 M .env.example
 M README.md
 M backend/app/config.py
 M backend/app/main.py
 M backend/app/models/base.py
 M backend/app/persistence/database.py
 M backend/app/rooms/service.py
 M backend/app/rules/definitions/SOURCES.md
 M backend/scripts/check_multiplayer.py
 M frontend/src/App.css
 M frontend/src/App.tsx
 M frontend/src/api/rooms.ts
 M frontend/src/pages/RoomsPage.tsx
?? backend/app/agents/definitions/
?? backend/app/agents/model.py
?? backend/app/agents/modules.py
?? backend/app/agents/runtime.py
?? backend/app/agents/schemas.py
?? backend/app/agents/security.py
?? backend/app/agents/service.py
?? backend/app/agents/tools.py
?? backend/app/api/agents.py
?? backend/app/memory/service.py
?? backend/app/models/ollama.py
?? backend/app/persistence/agent_models.py
?? backend/app/rules/checks.py
?? backend/scripts/check_agent_cycle.py
?? backend/tests/test_agent_boundaries.py
?? backend/tests/test_agent_memory_tools.py
?? backend/tests/test_agent_model_adapter.py
?? backend/tests/test_agent_runtime.py
?? docs/agent-runtime.md
?? docs/batch-3-report.md
?? frontend/src/api/agents.ts
?? frontend/src/components/AgentGamePanel.tsx
?? frontend/src/pages/AgentProfilesPage.tsx
```

## 22. git diff --stat

```text
 .env.example                             |  8 +++++++
 README.md                                | 36 +++++++++++++++++++++++++++-----
 backend/app/config.py                    | 19 ++++++++++++++---
 backend/app/main.py                      | 16 +++++++++++++-
 backend/app/models/base.py               |  3 ++-
 backend/app/persistence/database.py      |  2 +-
 backend/app/rooms/service.py             | 32 ++++++++++++++++++++++++++++
 backend/app/rules/definitions/SOURCES.md | 12 +++++++++++
 backend/scripts/check_multiplayer.py     |  6 +++++-
 frontend/src/App.css                     |  3 +++
 frontend/src/App.tsx                     |  3 +++
 frontend/src/api/rooms.ts                |  3 ++-
 frontend/src/pages/RoomsPage.tsx         | 10 +++++----
 13 files changed, 136 insertions(+), 17 deletions(-)
```

`git diff --stat` 默认不计未跟踪新文件；上面的 status 同时列出本批全部新文件。没有暂存、提交或推送。
