# 第六批报告：ModuleIR 与确定性场景导航

工作直接在原仓库完成，没有创建分支/worktree，没有 commit 或 push。商业模组、数据库、浏览器配置、模型运行记录和原文派生数据均留在 Git 忽略的 `.cache` 中。

## 前置检查与基线

- 起始 HEAD：`638a51903d36c8db4418ec3bca2596c5e09a8c78`（`workbench and clue board`）。起始工作树干净，第五批已提交；指定的第五批文档均存在。
- 读取 README、第四/五批报告、RAG、模组准备、Agent runtime、多人协议，以及提取器、实体/关系/审阅、上下文、检索、grounding、存档和事件恢复代码。仓库与适用父目录未发现 AGENTS.md。
- 基线：Ruff、前端 lint/build 通过；pytest **371 passed, 1 skipped**，709.44 秒。跳过为原有 Windows 符号链接测试；保留现有 Starlette 弃用警告。
- 首次在 backend cwd 运行时，沙箱不允许测试写父目录 `.cache`，产生权限错误；改为仓库根 cwd 和隔离 basetemp 后基线通过，没有修改用户数据库来绕过权限。

## 逐项交付记录

| # | 要求 | 结果 |
| --- | --- | --- |
| 1 | 起始 HEAD | 见上面的完整 hash |
| 2 | 第五批基线 | 371 passed / 1 skipped，Ruff、前端 lint/build 通过 |
| 3 | 主要文件 | 新增 `app/module_ir/{schemas,parser,repository,service,navigation,context}.py`、API/关联表、结构/导航前端与验收脚本；集成原 Agent、memory、knowledge、preparation、rooms；详见下方清单 |
| 4 | 新数据库表 | 知识库 3 张，游戏库 6 张，均为新增表；没有破坏性修改旧表 |
| 5 | DOC 提取方式 | `word_com_readonly_structure`；复用已有本地 Word COM，只读、禁宏和链接更新、无 DOCX 中间转换 |
| 6 | 真实结构数量 | 11 nodes、243 blocks、10 heading blocks、0 tables、9 warnings；1 个显式 outline、9 个启发式标题 |
| 7 | 稳定 ID/版本 | source ID/hash、文件、原始位置和内容 hash 决定 ID；`ir1_…` 版本随来源 hash 变化，approved snapshot 独立冻结 |
| 8 | 开场校正数量 | 2 个 scene；根节点保留，其余 8 个节点排除出本次运行结构，未完成整个模组语义校正 |
| 9 | 实体绑定 | 6：复用第五批 5 个 approved 实体，另加 1 个主机核对的相邻场景实体；NPC 0，原因见限制 |
| 10 | 批准转换 | 1 条开场范围内批准关系，关联第五批 leads_to |
| 11 | 导航状态 | 固定 snapshot/version/hash、current/previous/visited、active NPC/location、revealed、available transitions、revision、selected references 和 pending 状态 |
| 12 | 上下文与预算 | 当前 scene/subtree 精确读取，祖先只有元数据；行动相关片段/重要区块优先，实体与转换预留空间，长段落受限前缀；记录省略与字符预算 |
| 13 | 结构/RAG 边界 | 普通回合不全模组检索；current_scene/linked_nodes 局部补充；主机或批准 incomplete 才可 global；规则 RAG 不变 |
| 14 | Agent 工具 | 当前场景、内容索引、受限节点、实体索引、转换；调查员仅 public scene/entities；结构模型只看到新转场参数 schema |
| 15 | node grounding | 校验 snapshot/hash、授权范围及本轮实际观察内容；node claim 仅限 KP 私有依据，公开 canonical state 继续使用批准实体 |
| 16 | LangGraph | collect_context 确定性选节点；合法转换直接执行，不合法进入原主机 interrupt/resume；每个图节点检查 revision，禁止并行检定/审阅 |
| 17 | 存档 | 保存结构引用、位置与 pending；重启读档校验匹配版本；missing 保留历史/调查板并停止新 cycle，主机恢复匹配版本后 reload |
| 18 | API/前端 | 树形校正、绑定/转换管理、快照批准；主机当前导航、实际 cycle 审计和一次性转场；玩家公开投影；reload API/按钮 |
| 19 | 自动测试 | 415 passed / 1 skipped，较基线新增 44 个通过用例；Ruff、31 个 Python 文件格式、前端 lint/build 全通过 |
| 20 | Fake Model | 三浏览器流程通过，4 次 KP、4 次 AI 行动、1 次检定、1 次转换、保存重启恢复；普通全模组检索 0 |
| 21 | 三浏览器 | 主机树/debug 可读；玩家相关端点 403、公开场景与调查板一致、WS 无 node/keeper 字段；真实模式与最终 Fake 模式均通过，保存后重启/刷新一致 |
| 22 | 真实场景/转换 | 2 个场景、1 次模型工具执行的 approved 转换；revision 0→1；第 3/4 轮选块只来自新场景 |
| 23 | 真实 KP/队友/检定 | 3 次基础真人行动 + 读档后 1 次继续；KP 4、AI 队友公开输出 4、检定 2、公开实体 4 |
| 24 | 普通模组全库 RAG | 0 次；4 个 KP 决策上下文均为 structure navigation 的 local_fallback（当前子树预算选择） |
| 25 | 主机 fallback | 显式主机 global 查询 1 次，返回 4 条；规则书 RAG 7 次，未改变原行为 |
| 26 | 模型次数/延迟/显存 | 18 次本地调用；总 133.681 秒，均值 7.427 秒，中位 6.031 秒，范围 2.047–28.483 秒；全卡手工采样最高 6158 / 8188 MiB，无 OOM |
| 27 | 开发记录 | `.cache/batch-6/常暗之厢-session.md`，分 PUBLIC 与 HOST_DEBUG |
| 28 | 无剧透 PUBLIC 示例 | [KP]「你们进入前方的车厢。」；[AI队友]「四周安静下来。」；侦查骰点 6 / 目标 25，困难成功 |
| 29 | 日志导出 | 保留原 JSONL/Markdown；Fake 与实际浏览器验收检查接口成功和有效内容；未增加正式日志导出格式 |
| 30 | 保护文件 hash | 全部保护文件未变化，原不存在的默认知识库仍不存在；6 项 Git 忽略检查通过 |
| 31 | 外部 API/下载 | 未调用外部/付费模型 API，未下载模型或安装软件，仅访问本地 Ollama；Chrome/Vite/FastAPI 为本地验收服务 |
| 32 | 限制/下一批 | 见下方限制，不宣称完成全模组自动理解 |
| 33 | git status | 见末尾实际清单；没有暂存、提交或推送 |
| 34 | git diff --stat | 见末尾实测统计；标准 diff 不包含未跟踪新文件，另列新增文件统计 |

## 实现范围与数据库

知识库表：`module_structure_versions`、`module_nodes`、`module_blocks`。

游戏库表：`module_structure_overrides`、`module_structure_snapshots`、`module_entity_node_bindings`、`room_module_navigation_states`、`module_navigation_receipts`、`module_navigation_save_states`。

新增后端 API：结构 build/get/node preview/patch/approve，实体绑定 get/post/delete，转换 get/post/patch/delete，房间 current-scene/module-navigation/module-context-debug/scene-transition/module-search/reload。均按主机/玩家身份隔离，原文预览 1200 字、context debug 3000 字预算。所有旧测试与内置练习模组兼容路径保留；新第五批准备用房间要求批准结构。

新增前端：`ModuleStructurePanel.tsx`、`ModuleNavigationPanel.tsx`、`api/moduleStructure.ts`；接入模组准备页和主机游戏界面。使用普通树和表单，无拖拽流程图。主机填写的公开标题/摘要与原始目录标题分离。

新增测试文件：`test_module_ir.py`、`test_module_ir_edges.py`、`test_module_navigation.py`、`test_module_navigation_edges.py`、`test_module_navigation_runtime.py`，以及 fixture helper。原准备/审阅测试补齐新结构批准流程；原第五批证据审阅专用 fixture 明确批准 incomplete 以继续验证旧证据路径，没有删除旧断言。

## 实际 DOC 结构检查

原 DOC 只读解析保留段落顺序、style、outline、列表标签、表格边界、可靠物理页、字符位置和书签信息。此前提取只按页给纯文本，第六批增加可选 structure 分支，原页文本索引分支保留。没有 DOC → DOCX 转换，所以没有中间 DOCX/派生 DOCX hash。

抽查全部 10 个非根节点：标题与其原始 heading block 一致，顺序和父节点符合抽取出的层级；9 个为根下标题，1 个显式标题为子节点。抽查不表示人工认可整个剧情语义。无剧透的位置记录如下，完整 ID 审计在 `.cache/batch-6/structure-inspection.json`。

| 检查序号 | source order | 原始段落（0 起） | 物理页 | 父节点 |
| --- | --- | --- | --- | --- |
| 1 | 36 | 47 | 2 | 文档根 |
| 2 | 46 | 61 | 3 | 文档根 |
| 3 | 56 | 75 | 3 | 文档根 |
| 4 | 77 | 107 | 5 | 文档根 |
| 5 | 97 | 145 | 6 | 文档根 |
| 6 | 114 | 171 | 7 | 文档根 |
| 7 | 148 | 225 | 11 | 文档根 |
| 8 | 160 | 245 | 12 | 第 7 个标题 |
| 9 | 188 | 280 | 14 | 文档根 |
| 10 | 219 | 316 | 15 | 文档根 |

实际 Word 大部分段落为正文 style/outline=9。纯样式识别只有一个显式标题；通用括号编号和保守排版规则补出其余 9 个，不硬编码本模组标题。未识别的短导入/说明标题仍留在原始区块，不伪造 Word style，也不自动变成场景。

## 验收过程与已修复问题

- Fake 三浏览器初次在沙箱中 Chrome 连接失败；验证并清理本次临时 PID 后，在允许本地浏览器的环境重跑通过。
- 本地实战第一次完成 DOC 构建与主机/玩家页面准备，规则来源集合包含无文本索引来源，绑定被 422 拒绝。启动器改为只选有 chunks 的规则来源。
- 第二次重复 Word 提取超过原 120 秒上限。增加安全超时错误和完整 hash 校验后的结构缓存复用；随后复用第一次成功提取的隔离 IR，原知识库和 DOC 未变。
- 第三次模型已完成首轮与真实检定，但拒绝工具时 savepoint 回滚导致 ORM 对象失效，出现 MissingGreenlet。显式刷新相关对象并增加非法节点/参数与未来实体公开的回归测试。
- 实战发现低预算下实体挤占全部原文，改为对当前节点的超长段落保留受限前缀，记录截断；不扩大到未来 scene。
- 第四次完成 4 个公开回合、3 次 AI 行动、2 次检定、主机检索和重启读档，普通全库模组检索 0、规则检索 7、主机查询返回 4 条；但没有实际转场，不能作为通过。模型混填新旧参数，服务端正确拒绝；结构房间改为只向模型提供 `TransitionRequest` schema。
- 第五次完整实战通过：专用 schema 消除了混填参数；模型执行 1 次批准转换，4 个回合均完成，重启后 current/visited/revision 保持一致。未额外由主机代执行转场。

模型语义失误不会作为纯离线单测的硬断言；实战以状态、工具回执、节点选择、权限响应和事件为依据。失败尝试保留，不计入最终成功会话指标。

## 测试与运行产物

- 基线：`.cache/batch-6/baseline-pytest.txt`。
- 第一次完整回归：413 passed / 1 skipped，719.67 秒，`.cache/batch-6/final-pytest.txt`。
- 第二次全量：414 passed / 1 failed / 1 skipped，968.77 秒；原有 WebSocket presence 测试出现断线清理竞态，单独重跑 1 passed。修复为取消任务前同步移除在线连接，未改动原测试断言。
- 最终串行完整回归：**415 passed / 1 skipped**，1083.64 秒，`.cache/batch-6/final-pytest-3.txt`；保留 1 条原有 Starlette 弃用警告。
- 最终专用 schema、非法工具与节点依据/导航专项：19 passed，127.41 秒，`.cache/batch-6/navigation-final.txt`。
- 非法工具与节点依据专项：7 passed，75.99 秒，`.cache/batch-6/runtime-final.txt`。
- 合成表格/围栏/分页/完整来源清单：4 passed，`.cache/batch-6/edge-tests.txt`。
- 缺失结构读档/reload、hash、stale、路径权限、自然语言位置、pending review：6 passed，`.cache/batch-6/nav-edge-tests.txt`。
- Fake：`.cache/batch6-fake-smoke-11e7521a05804773869f0faae771305e/report.json`；结构与玩家调查板截图已查看，文字可读且无隐藏调试内容。
- 实际会话：`.cache/batch6-real-smoke-258a002b65794b93a4b9910712f1337c/report.json`；最终精简开发记录在 `.cache/batch-6/常暗之厢-session.md`，位置审计在 `.cache/batch-6/real-metrics.json`。
- Ruff check 通过；31 个本批 Python 文件 format --check 通过；前端 lint/build 通过；按仓库原 CRLF 配置的 git diff --check 通过。
- 临时服务清理：验收启动的后端、Vite、三个浏览器和 Word 全部退出，8000/5173 无监听；`.cache/batch-6/process-cleanup.json` 留存实际进程核验。

## 保护文件

开始时记录于 `.cache/batch-6/protected-before.json`。最终按同一清单比较，另检查 Git 忽略规则。

| 文件 | 开始 SHA-256 |
| --- | --- |
| `.env` | `a998c032b29c3970dd1ec8b435ed3c4bf3b3791946e154716f037dec63606b40` |
| `data/game.db` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `.cache/batch-4/knowledge-v2.db` | `b5e7b5eaf43a6ebc5b6545240f7209ea1d1669255559d8aabfc9be8358172ff1` |
| 原模组 DOC | `af1033fc931f85b53109856e7bbb9695313a3adb90008e6615cc2c091c85c1d8` |

`data/knowledge/knowledge.db` 开始时不存在。实际原始模组来源 hash 为 `a72e88d5a4cb08831bb4ab6719094447b6c995932b4760945a1abb871cad57c8`；结构版本为 `ir1_4689d06283bb5adb9c04422fab7530b6`。

## 限制与第七批建议

- 第五批准备库只有 5 个 approved 开场实体，没有 approved NPC；原 NPC 草稿处于 rejected。实战 NPC 绑定为 0，没有为了凑数批准未来人物。NPC 多场景、地点/线索绑定通过合成测试覆盖。
- 实际只校正 2 个开场 scene，其他节点排除出运行结构。基础 IR 完整不等于剧情校正完整；没有完成整个模组、全部内部链接或复杂表格语义。
- 预算以 JSON 字符数估算，部分区块和实体会被省略；node 工具提供受限补读。第七批可改进按 token 的预算和主机分段校正，但不应重新把全库检索设为默认。
- 本地模型仍可能请求不必要检定、使用未注册工具或生成无法采用的叙述/摘要；服务端拒绝无效命令并保留安全公开依据。最终实战有 2 次工具拒绝和 1 次非致命摘要失败（保留旧摘要）；队友 4 次输出中 3 次为重复安全短句。模型在第二轮检定后提前执行合法转场，第三轮及读档后的第四轮使用新场景；结构位置/选块无混淆，不声称模型正确理解了每次转场意图或叙述位置。
- Word 需要已有安装；超时安全失败，旧 DOC 原文不变。未实现 OCR、图片/地图、嵌入对象提取、通用 Word 编辑器、embedding、向量库、并行模型或完整战斗/追逐。
- 下一批建议优先改善小模型的实体索引补读、失败工具的明确恢复体验、主机细分漏检区块和可视化权限回归；保留版本固定和原始顺序约束。

## 最终 Git 状态

```text
 M README.md
 M backend/app/agents/runtime.py
 M backend/app/agents/schemas.py
 M backend/app/agents/service.py
 M backend/app/agents/tools.py
 M backend/app/knowledge/extraction.py
 M backend/app/knowledge/schemas.py
 M backend/app/knowledge/service.py
 M backend/app/knowledge/word_extract.ps1
 M backend/app/main.py
 M backend/app/memory/service.py
 M backend/app/persistence/database.py
 M backend/app/preparation/room_service.py
 M backend/app/rooms/realtime.py
 M backend/app/rooms/service.py
 M backend/tests/test_host_review.py
 M backend/tests/test_module_preparation.py
 M frontend/src/App.css
 M frontend/src/api/session.ts
 M frontend/src/components/AgentGamePanel.tsx
 M frontend/src/pages/ModulePreparationPage.tsx
?? backend/app/api/module_ir.py
?? backend/app/module_ir/
?? backend/app/persistence/module_ir_models.py
?? backend/scripts/check_module_navigation.py
?? backend/tests/module_ir_helpers.py
?? backend/tests/test_module_ir.py
?? backend/tests/test_module_ir_edges.py
?? backend/tests/test_module_navigation.py
?? backend/tests/test_module_navigation_edges.py
?? backend/tests/test_module_navigation_runtime.py
?? docs/batch-6-report.md
?? docs/module-ir.md
?? frontend/src/api/moduleStructure.ts
?? frontend/src/components/ModuleNavigationPanel.tsx
?? frontend/src/components/ModuleStructurePanel.tsx
```

标准 `git diff --stat`（未跟踪文件不在内）：

```text
 README.md                                    | 10 ++++
 backend/app/agents/runtime.py                | 40 ++++++++++++--
 backend/app/agents/schemas.py                |  9 ++++
 backend/app/agents/service.py                | 10 ++++
 backend/app/agents/tools.py                  | 80 +++++++++++++++++++++++++---
 backend/app/knowledge/extraction.py          | 44 ++++++++-------
 backend/app/knowledge/schemas.py             |  4 ++
 backend/app/knowledge/service.py             | 22 +++++++-
 backend/app/knowledge/word_extract.ps1       | 66 ++++++++++++++++++++++-
 backend/app/main.py                          | 13 ++++-
 backend/app/memory/service.py                | 31 +++++++++++
 backend/app/persistence/database.py          |  1 +
 backend/app/preparation/room_service.py      | 61 +++++++++++++++++++++
 backend/app/rooms/realtime.py                | 16 +++---
 backend/app/rooms/service.py                 |  9 +++-
 backend/tests/test_host_review.py            | 38 +++++++++++++
 backend/tests/test_module_preparation.py     | 15 +++++-
 frontend/src/App.css                         |  5 ++
 frontend/src/api/session.ts                  |  3 +-
 frontend/src/components/AgentGamePanel.tsx   |  2 +
 frontend/src/pages/ModulePreparationPage.tsx |  2 +
 21 files changed, 440 insertions(+), 41 deletions(-)
```

新增未跟踪文件 21 个；完整文件与行数清单在 `.cache/batch-6/final-git.json`。
