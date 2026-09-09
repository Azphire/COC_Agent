# 第四批开发与验收报告

日期：2026-09-09。起始 HEAD：`76365f8a67771aad605aa6d73c30e69d2513617c`。开始时工作树干净，仓库及父目录未发现 AGENTS.md；未创建分支/worktree，未 commit 或 push。已按补充要求覆盖原要求：实际模组为《常暗之厢》，并按后续确认扩展 DOC/DOCX，优先于同名 PDF。

## 用户复核后的验收结论更正

本次记录验证了真实 qwen3:8b 调用、RAG 来源隔离、规则引用、一次真人席位检定、日志权限和恢复链路。**《常暗之厢》的剧情推进尚未验收通过。** 下文“通过”对应脚本中这些技术断言，不应解读为 AI 已能实际主持该模组开场；先前结论的表述范围过大。

“真人A”席位由浏览器验收脚本驱动，三段固定输入分别是规则问答、便签附近侦查和无答案拒答负例。第三段“超光速量子跃迁引擎热寂修正”由测试配置人为加入，与模组无关；PUBLIC 仅提交一次，HOST_DEBUG 因 KP 决策、公开叙事、队友三个阶段分别预检索而记录三次相同 query，不是三次新的玩家行动。

实际只有一轮模组调查，未公开新线索，也未切换场景；队友两次公开输出均为气氛发言，提出调查行动为零。原始 DOC 绑定仅初始化一个公开开场，未提供可揭示的线索实体；保守的抽取式输出因此主要复述原场景。现有计数 `kp_actions=3` / `teammate_actions=2` 是公开回应/发言数，不能作为剧情推进次数。拒答负例应单独验收，后续开场验收需要以自然调查行动、可追溯的新公开信息和队友实际调查行为衡量推进。

## 基线和实现

基线 Ruff、前端 lint/build 通过；原有 pytest **283 passed**。Windows 默认临时目录最初不可写，改用仓库 `.cache/batch-4/` 内的隔离 basetemp 后跑通，没有修改测试业务来绕过失败。

新增 `backend/app/knowledge/` 的 schemas、提取/Word helper、规范化/切块、indexer、repository、retriever、context/grounding service 和 CLI；新增知识 API、五张增量持久化表、两组 RAG 测试、检索评测及浏览器脚本。修改 agents 的 model/runtime/service/tools/schemas/modules、memory.build_context、database/config/main；前端新增知识页、来源绑定和时间线事件组件，接入 App、RoomsPage、AgentGamePanel 和 API 类型。文档为 README、本报告、`rag-architecture.md`，另更新 `.env.example` 和 `.gitignore`。

知识库默认 `data/knowledge/knowledge.db`，本次真实隔离索引是 `.cache/batch-4/knowledge-v2.db`；游戏验收库分别在 `.cache/agent-*-smoke-<uuid>/rag-game.db`。知识库含 sources、source_heads、chunks、FTS5；游戏库新增 `room_knowledge_bindings`、`agent_retrieval_records`、`agent_grounded_claims`、`knowledge_save_states`、`agent_model_calls`，不改旧表列。真实用户游戏库没有被服务启动或写入。

检索采用 NFKC、中文 2/3-gram、Latin/数字和通用 CoC 术语 token；约 800 字符页内切块、100 重叠、确定性重复页眉页脚处理。排序结合 FTS5 BM25、覆盖率、术语、精确短语、通用章节标题、场景/实体匹配及相邻块，单页最多两个结果、总数最多六个。没有 query→页码硬编码、embedding、OCR 或模型 reranker。

KP 可以 search_rules/search_module/get_evidence_excerpt；调查员 Agent 只能查询公开规则和当前 run 有权限的 excerpt。来源、精确 hash、edition 和权限先过滤，scene/entity 已知冲突标签也过滤。`build_context` 在现有分层上下文中加入独立 RULE_EVIDENCE/MODULE_EVIDENCE，摘录预算最多 30%，优先保证规则/模组类型及来源多样性，过长时缩短摘录。模组预检索使用已公开当前场景的短描述，工具仍可补充查询；返回 ID 与实际注入 ID 分别审计。

rule claim 必须引用当前 run、当前绑定版本的公开 CoC 7 规则证据，并通过短句包含校验；module_fact 必须有当前模组证据或合法结构化实体，公开时只能引用已公开实体文字。公开叙事使用独立公开上下文，小模型可以选择从证据生成的短句选项，但服务器仍重新验证全部 ID/文本。无规则依据转为 needs_host_ruling，不允许用场景叙述回避。flavor 限于中性短句，不进入 canonical observation；调查员推测保留 belief。只有经验证事实或确定性场景/线索事件成为权威记忆，摘要不创建新 evidence ID。

房间与存档固定 source ID/hash，新索引不静默替换旧房间。缺少同版本 chunks/FTS 时显示 knowledge_missing，保留旧日志引用，阻止新 Agent cycle。清理仅显式删除无引用的旧派生版本，原文不改。

API/界面清单见 [RAG 架构](rag-architecture.md)。本批保留每 run 四工具、每 cycle 六次模型调用的限制，真实调用串行；每次调用的耗时与重试可在重启后核对。

## 实际目录与来源检查

实际约定是 `data/modules/<一级模组文件夹>/`，内部递归；没有假定固定文件名。每个文件夹是独立来源，默认标题为文件夹名，manifest 优先，无 manifest 的 ID 来自稳定相对路径。模组 hash 由排序后的相对路径与文件 SHA-256 计算，含原始附件；缓存/输出/锁文件除外。所有模组原始内容默认 keeper_only，公开仍经场景、reveal_clue 和房间事件。原创结构化《停摆的钟楼》未删除或修改。

实际发现《常暗之厢》只有 **`常暗之厢 2-3.doc`**，类型 `.doc`。用户明确允许扩展 Word 并优先于 PDF 后，使用本机 Microsoft Word 只读提取。**17 个 Word 物理页，17 页提取成功，0 页失败，19 chunks**；没有 PDF，因而“PDF 文本层”不适用；DOC 有可提取文字，未触发 OCR。没有跳过或失败文件。整个目录 source hash **`a72e88d5a4cb`**，原文件 SHA-256 **`af1033fc931f85b53109856e7bbb9695313a3adb90008e6615cc2c091c85c1d8`**，只读提取前后相同。

最终索引批次时间约 11:40:51–11:41:09 UTC，元数据报告 `.cache/batch-4/index-final.json`：

| 来源 | 文件类型 | 物理页/成功页 | chunks | source hash 前 12 位 |
|---|---|---:|---:|---|
| 第七版规则书1907 | PDF | 401 / 398 | 979 | bc8455d443ec |
| 第七版调查员手册1.20 | PDF | 162 / 157 | 419 | 6a2e961d2cd1 |
| 常暗之厢 | DOC | 17 / 17 | 19 | a72e88d5a4cb |
| 吱乎鲁的呼唤 | DOCX、PDF、PNG、TXT | 不适用 | 86 | 9b63dcfbcc35 |
| 妖灵会馆守则-禁止接触 | DOCX、JPG、PDF | 不适用 | 39 | 07f3a1ab6d77 |
| 希普拉 | DOCX、PDF、TXT | 不适用 | 11 | 51723b3d9c99 |
| 木星噩梦 | PDF | 85 / 83 | 139 | 538633b37880 |
| 被煮沸的钢琴 | PDF | 34 / 34 | 50 | 40b854ca586d |
| 追书人 | PDF | 19 / 18 | 36 | 6c2941c8c4e8 |
| 长明灯 | PDF | 8 / 1 | 1 | 13c4d0c48760 |

DOCX 以文本单元提取，没有虚构 Word/PDF 页数；同名 PDF 被标为 skipped_preferred_word。PNG/JPG 等只记录 unsupported。规则目录另有 XLSX 角色卡，unsupported，零 chunks。模组目录中的原始文件在开发期间有用户侧新增 Word 版本，索引按实际最终结构扫描；本次代码没有转换后写回、移动或改名用户文件。

规则书无可用文本的 physical pages 为 1、111、391；手册为 10、36、60、78、88。其他模组失败/无文本页：木星噩梦 1、3；追书人 1；长明灯 2–8。部分成功来源保持 partial，不能把无文本页算作成功，也没有新增 OCR。其他模组只做来源扫描和隔离验证，没有作为《常暗之厢》开场的检索范围。

## 真实 PDF 检索评测与页码抽查

12 个有答案 query 加 1 个明确无答案 query，评测标签只保存在 `backend/scripts/evaluate_knowledge.py`，未提交原书文本。

| 阶段 | Hit@1 | Hit@3 | Hit@5 | MRR | 平均耗时 |
|---|---:|---:|---:|---:|---:|
| 初始基线 | 0.5000 | 0.5833 | 0.5833 | 0.5417 | 30.12 ms |
| 最终通用排序/阅读顺序调整 | 0.5833 | 0.7500 | 0.7500 | 0.6528 | 36.13 ms |

无答案 query 正确返回空集。结果分别保存于 `.cache/batch-4/retrieval-baseline.json` 和 `retrieval-final-v2.json`。调整来自通用术语/覆盖率、章节标题权重、BM25 饱和及双栏阅读顺序；没有针对某题指定页数。

仍有三类严格标签失败：多属性生成问题被教育/其他属性内容分散；同时列普通/困难/极难的查询命中讨论而非定义页；“百分骰”跨规则书/手册重叠时未把期望的手册页排进前五。职业点 query 命中概述页后，目标页在后续结果出现。这里没有用“页码能打开”冒充“答案正确”，也没有修改期望标签掩盖失败。

人工抽查 10 个检索结果（8 个不同物理页），渲染并核对短摘录确实来自对应 PDF 页：**27、27、39、38、72、98、57、77、79、79**，10/10 来源页匹配。PDF printed page 与 physical page 相差一页，界面以 physical page 为准。发现视觉逐行排序会交错双栏，已改为 PDF 阅读内容流并重建隔离索引。图片与简短检查记录位于 `.cache/batch-4/page-review/`，不提交 Git。该抽查验证来源与阅读顺序，检索相关性仍按上面的严格指标报告。

## 自动测试及浏览器流程

最终全量检查已得到 **327 passed、1 skipped**，保留原有 283 项。跳过项为当前 Windows 环境无权限创建符号链接；其他路径越界测试通过。一个既有 Starlette/AnyIO 弃用警告。Ruff、全部本批 Python 格式检查、前端 lint/build 均通过。最后一次证据裁剪/场景查询修复的聚焦 RAG 回归为 **41 passed、1 skipped**；随后将 FTS 完整性检查改为两次线性 ID 扫描，避免无索引列连接的重复扫描，两项缺失/清理回归通过。真实索引 verify：**1779 chunks / 1779 FTS rows，integrity=ok**。

覆盖 PDF page/label、无文本、TXT/MD/DOCX、Word 优先、hash/增量/旧版本、路径/格式、清洗切块、中文/英文/数字、FTS5、source/edition/module/scene、ID 稳定与权限、excerpt/budget、假证据/假实体、公开/私密 grounding、无依据拒答、摘要/记忆引用、存档缺失版本、封闭工具、真人 interrupt/resume、队友、日志导出和旧房间兼容。

合成流程继续使用原创《停摆的钟楼》及短原创知识文本。Fake Model 浏览器脚本以真实 HTTP、WebSocket、SQLite、Chrome 运行，不访问模型 API；首次通过记录为 `.cache/agent-fake-smoke-07e9f234ce9d4018832f4a94817dfb70/report.json`，2 个 KP 公开回应、2 次队友行动/发言、1 次检定、8 次 Fake 调用。后续还执行加强版权限/引用浏览器回归。

真实验收经过多次调试：早期曾有无效摘录改写/实体 ID、未明确调用 search_module、中文技能键被拒绝、用场景句回避未知规则以及检索命中未注入的问题。对应记录保留在 `.cache/batch-4/常暗之厢-attempt-1.md` 至 `attempt-5.md`；部分旧脚本的 passed 仅表示当时较弱的断言通过，**不作为最终完整验收依据**。后续断言明确要求合法规则引用、公开实体叙事、真实掷骰及同 cycle 恢复、未知规则裁定、KP 决策前模组证据注入和正确 source hash。

最终真实测试仅覆盖主机已公开的开场车厢及便签周围调查，不完成全模组，不公开隐藏内容。真人提交三次行动，AI KP 与一名 AI 调查员均使用本地 qwen3:8b。最终报告：`.cache/agent-ollama-smoke-e3d610be426842758258b3efee309336/report.json`；开始时间 **12:12:28 UTC**，房间短 ID **9d0a372d**。

| 项目 | 最终真实结果 |
|---|---|
| 真人行动 | 3 次 |
| KP 公开回应 | 3 次：2 次有据叙事，1 次无依据裁定 |
| AI 同伴公开行动 | 2 次发言；另有公开状态查询，不计为公开行动 |
| 真实检定 | 1 次侦查，1D100=68，failure，未通过 |
| cycle | 4d0e64bf、114549bf、3bacd92b，全部 completed；第二轮 interrupt/resume ID 相同 |
| 公开规则引用 | 2 条，规则书 physical p.79、p.67；三方一致 |
| 检索 | 总计 18 次；模组 7 次，其中 5 次非空，累计 12 个候选、4 次 evidence 注入 |
| 模组来源 | 全部为常暗之厢 / a72e88d5a4cb，KP 决策前实际注入；无其他模组来源 |
| 模型调用 | 11 次串行调用，全部 attempt=1；每 cycle 为 3 / 4 / 4 次（含一次摘要） |
| 单次模型耗时 | 3.483–7.531 秒，平均 5.565 秒，累计 61.212 秒 |
| 检索服务耗时 | 31–327 ms，平均 168.11 ms；含服务端处理，并非纯排序基准 |
| 显存 | Ollama /api/ps：6,186,378,198 bytes，约 5.76 GiB；Q4_K_M，context=8192 |
| 工具拒绝 | KP 恢复阶段再请求检定被拒；队友越权请求检定被拒，未产生第二次骰点 |
| 存档/重启 | 同一来源 hash 恢复，公开历史重载，旧公开 evidence 仍可读取 |

模组候选 physical Word 页包括 2、3、4、5、7、16。检索边界是整个绑定模组，行为范围仅开场；未自动把检索页映射成场景或公开其隐藏内容。只读的原始模组证据保留在 KP/主机权限内，公开叙事依据主机已公开实体，不直接复制私密摘录。

最终三浏览器真实验收明确验证：主机展开后能看到检索 query、source_filters、injected_ids 和运行/工具状态；玩家 A 实时收到 KP/队友事件；玩家 B 刷新恢复相同 seq；三方规则引用 ID 一致；玩家获取已发布规则证据成功，猜测隐藏模组 evidence 返回 403/404，读取检索审计返回 403；玩家无 HOST_DEBUG/私密记忆；actor 名称与 agent controller 正确；事件 seq 排序且无 REST/WS 重复；后端重启恢复原日志及绑定 hash；其他模组标题/内容未进入上下文或日志。

加强后的 Fake 三浏览器回归同样通过：`.cache/agent-fake-smoke-59d20096e0d14ffa915c1c7bab803205/report.json`。脚本可不带配置自动生成合成知识库；它包含原创场景短文，以便真实检验场景预检索，而不是只让工具返回空集也算通过。

开发记录中的少量无剧透示例（UTC）：

```text
PUBLIC #31 [12:13:55] [4d0e64bf] [AI队友：谨慎的同伴] 调查仍在继续。
PUBLIC #44 [12:14:58] [114549bf] [检定] 1D100=68, failure, passed=False
PUBLIC #65 [12:17:26] [3bacd92b] [KP：本地KP] 需要主持人裁定：缺少已验证的依据。
PUBLIC #67 [12:17:42] [3bacd92b] [AI队友：谨慎的同伴] 四周安静下来。
```

规则引用与第二轮公开叙事的完整短记录见 `.cache/batch-4/常暗之厢-session.md` 的 PUBLIC 段，HOST_DEBUG 记录每轮所有调用耗时和检索 ID。最终两个流程均实际下载原有 JSONL、Markdown 日志并检查引用及可见性，日志导出回归通过。

## 日志、安全与交付

游戏界面复用房间事件：时间、cycle 短 ID、actor 名称与 controller、KP 回应、AI 发言/行动、检定请求、实际骰点/等级、公开线索、cycle 启动/等待/恢复/完成/失败和引用均可见。REST 与 WebSocket 按 seq 合并，一次事件只有一条；刷新和后端重启重新加载相同公开历史。主机调试显示工具、检索、score/rank、注入 ID 和错误；玩家无 HOST_DEBUG、隐藏 evidence 或 KP 私密记忆。

**没有新增正式游戏日志导出规范。** 第二批 JSONL/Markdown 导出 API 保留，浏览器实际下载并检查权限/敏感信息。开发记录为 `.cache/batch-4/常暗之厢-session.md`，PUBLIC 仅含普通玩家有权看到的事件；HOST_DEBUG 只有短 query、来源/hash、返回/注入 evidence ID、模型调用耗时、工具拒绝和 cycle 状态。没有写主机密钥、玩家 token、API Key、完整 prompt、思考过程、keeper-only 原文或未公开线索全文。

本批没有外部模型 API 调用、没有模型下载、没有新增依赖。Ollama 保留回环监听，测试前后端和 Chrome 临时进程由脚本关闭；端口 8000/5173 释放检查在最终流程结束时执行。

用户 `.env` 的前后 SHA-256 为 `a998c032b29c3970dd1ec8b435ed3c4bf3b3791946e154716f037dec63606b40`；`data/game.db` 前后为 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（原本为空文件）。未修改这两份文件。

初期两次 Word helper 参数错误留下无界面 WINWORD PID 65040、53488。自动审批曾拒绝强制结束，理由是无法可靠确认仅属于提取、可能损失用户未保存工作，因此当时保留进程。用户随后明确要求关闭进程，两个 Word 及其残留子进程已关闭，并复查 8000/5173 无监听。修正后的提取正常退出。

已知限制：抽取式公开叙事较保守；词法召回尚未覆盖所有中文问法；raw 模组不自动生成完整实体，长流程需结构化场景与线索；DOC 依赖 Word，DOCX 不给物理页；无文本页不支持。第五批建议优先完善主机审阅/公开实体的最小流程、扩展独立检索评测及明确规则问答的相关性验证，再考虑其他检索实现，继续保持来源和可见性约束。

Git 忽略检查命中 `.cache/`、`data/modules/*`、`data/rules/*`、`data/knowledge/`；跟踪的 data 文件只有原有 `.gitkeep`。开发记录、原文和索引均未加入 Git，暂存区为空。最终 `git status --short`：

```text
 M .env.example
 M .gitignore
 M README.md
 M backend/app/agents/model.py
 M backend/app/agents/modules.py
 M backend/app/agents/runtime.py
 M backend/app/agents/schemas.py
 M backend/app/agents/service.py
 M backend/app/agents/tools.py
 M backend/app/config.py
 M backend/app/main.py
 M backend/app/memory/service.py
 M backend/app/persistence/database.py
 M frontend/src/App.tsx
 M frontend/src/api/agents.ts
 M frontend/src/components/AgentGamePanel.tsx
 M frontend/src/pages/RoomsPage.tsx
?? backend/app/api/knowledge.py
?? backend/app/knowledge/
?? backend/app/persistence/knowledge_models.py
?? backend/scripts/check_rag.py
?? backend/scripts/evaluate_knowledge.py
?? backend/tests/test_knowledge.py
?? backend/tests/test_knowledge_runtime.py
?? docs/batch-4-report.md
?? docs/rag-architecture.md
?? frontend/src/api/knowledge.ts
?? frontend/src/components/KnowledgeBindingPanel.tsx
?? frontend/src/components/RoomTimelineEvent.tsx
?? frontend/src/pages/KnowledgePage.tsx
```

`git diff --stat` 对已跟踪修改统计为 **17 files changed, 391 insertions(+), 52 deletions(-)**，不包含上述未跟踪的新文件。本批文件全部留在工作树供审阅，未暂存、commit 或 push。

最终再跑全量 pytest：**327 passed、1 skipped，501.65 秒**。最后的索引完整性性能修复保留相同校验语义，相关两项回归另行通过；Ruff 和 25 个本批 Python 文件的格式检查再次通过。新增文件共 **22 个**，已跟踪修改 **17 个**。

另外只读重载真实房间，在三个隔离浏览器中复查公开 seq、KP/队友 actor/controller、WebSocket 不含隐藏 evidence ID，结果全部通过；模型调用数仍为 11，未追加模型请求。补充报告和最新日志截图：`.cache/agent-fake-smoke-c7b9f167a4094bdf8e85e6d003207299/`（报告 mode 明确标为 real session UI replay，目录沿用了测试工具前缀）。测试前后端/Chrome 已关闭，8000/5173 无监听；Word 残留也已按用户后续明确指令关闭。
