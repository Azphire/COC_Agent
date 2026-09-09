# 第四批本地 RAG 架构

本批在现有 `build_context`、封闭工具、事件权限与 LangGraph 上增加词法证据层。检定仍由确定性引擎执行，原有未绑定知识库的房间保持原流程。

## 来源、格式和版本

`data/rules/` 的文件按规则书或调查员手册注册；`data/modules/` 每个一级文件夹是一个独立 module source boundary。只在各自边界内递归发现文件，拒绝绝对引用、`..`、盘符和解析后越界的路径。图片、压缩包、未知格式及缓存/输出目录都有跳过记录。原始模组始终 `keeper_only`，manifest 无法提升可见性。

支持 `.pdf/.md/.txt/.docx/.doc`。同路径同 stem 的版本优先 DOCX、DOC、PDF，避免重复内容；优先文件失败会报告失败，不静默换到低优先版本。DOCX 读取 `word/document.xml`，其文本单元不冒充物理页。DOC 在 Windows 使用 Word COM 的只读、禁止宏、禁止更新链接模式，隐藏启动，提取分页文本，finally 不保存退出。DOC 的分页取决于本机 Word 排版；缺少 Word 或转换失败有明确状态。没有 OCR 或图片理解。

manifest 依次读取 `manifest.json`、`manifest.yaml`、`manifest.yml`，支持 `source_id/module_id/title/edition/language/version`。默认 edition 为 coc7。默认标题是文件夹名，内部 ID 是类型及稳定相对路径的 SHA-256 前缀，扫描顺序不参与 ID。重复 ID 或把旧 ID 改用于另一边界会被拒绝。

规则文件 hash 是文件 SHA-256。模组整体 hash：对排序后的每个原始文件相对路径及 SHA-256 编码为紧凑 JSON 数组，末尾加换行，累积 SHA-256。manifest、图片等原始附件也参与；缓存、输出和锁文件不参与。文件在提取期间变化时拒绝发布混合版本。

默认知识库 `data/knowledge/knowledge.db` 独立于游戏库。`knowledge_sources` 保存经 Pydantic 校验的完整来源元数据，以 `(source_id, source_hash)` 为主键；`source_heads` 指向最新扫描版本。`knowledge_chunks` 保存文件 hash、页码、label、offset、显示/规范化文本、可见性、模组/场景/实体 ID 和相邻块 ID。`knowledge_fts` 保存预分词文本及 chunk ID。初始化先检测 FTS5，不可用则停止索引。

source hash 不变则跳过已完成提取；索引条目缺失时可重建同版本。新 hash 产生新版本，旧版本保留。`verify` 检查 SQLite 完整性及 FTS/chunk 对应关系。`cleanup` 默认 dry-run，`--apply --game-database ...` 只删除非 head 且没有房间、存档、claim 引用的派生记录，不触及原文。当前清理只检查指定的一个游戏数据库；共享知识库的多个游戏库须先分别核查引用，不能直接清理。

## 提取、检索与预算

PDF 使用现有 PyMuPDF，保留 physical page（1 开始）和原 label。按 PDF 内容流读取；本地双栏规则书若使用视觉逐行排序，会把左右栏交错，验收时已发现并纠正。空白/无文本页列入 failed_pages；完全无文本 PDF 为 `ocr_required`，部分可用为 `partial`。

切块约 800 字符、重叠约 100，优先段落/行边界，每块停留在同一文件同一页；不需要跨页范围。页眉/页脚清理使用至少三页及 60% 重复比例，不硬编码书名。NFKC、连续空白、中文断行、Latin 小写及词内断字符处理保留数字、百分号与骰式。中文生成 2/3 字 n-gram，Latin/数字保持完整 token，补充通用 CoC 术语 token；查询和文档共用规范化器。

SQL 先按来源 ID/hash、kind、edition、visibility 限定，再取 FTS5 BM25 候选。已标注场景/实体的冲突块被过滤，未结构化的空标签仍可检索；当前原文不会自动识别场景或 NPC。排序组合饱和 BM25、查询覆盖率、术语/精确短语、通用数字章节标题匹配、场景/实体匹配，补充同页相邻块，按文本去重，每页最多两个结果，最多返回六个。实现没有 query→页码映射，评测页码标签只在独立评测脚本。

`RetrievedEvidence` 的短摘录最多 420 字符，注入默认裁为 280；evidence ID 由 run ID 和 chunk ID 计算，在同一 run 内稳定。ID 存在还不够，claim 必须在该 run 实际检索记录中找到该证据。

`build_context` 保持身份/角色、公开状态、可见模组、记忆/摘要、近期事件、证据、当前行动的顺序。先按 actor 和公开叙事角色过滤，再裁剪；规则问题使用术语预检索，KP 另有模组预检索，不新增意图模型调用。`RULE_EVIDENCE` 与 `MODULE_EVIDENCE` 分开，检索摘录预算不超过可用上下文 30%，按分数与来源多样性选取。公开叙事额外提供少量直接从可见短摘录/场景抽取的 `PUBLIC_CLAIM_OPTIONS`，计入总上下文预算，帮助小模型复制正确 ID 和短句；选项仍通过相同服务端验证。模型提示将输入/证据明确视为参考数据。

## 工具与有据输出

| 工具 | KP | AI 调查员 |
|---|---|---|
| search_rules | 当前绑定公开规则 | 当前绑定公开规则 |
| search_module | 唯一绑定模组及精确 hash | 拒绝 |
| get_evidence_excerpt | 当前 run 已取得的证据 | 当前 run 已取得的公开规则证据 |

工具结果保存 run 审计，检索不增加模型调用数；每 run 最多四个工具，每 cycle 最多六次真实模型调用（含修复/摘要），所有模型调用串行。补充检索工具在当前 run 审计中可查；每个后续阶段会按自身权限重新预检索，以该阶段的 evidence ID 为准，不把私密 KP 工具输出直接转给公开叙事员。

`GroundedClaim` 包含 claim ID、category、statement、evidence IDs、entity IDs 和 visibility：

- rule：至少一条当前 run 的公开 CoC 7 规则书/调查员手册证据，source ID/hash 必须仍是绑定版本。statement 至少 12 字符，且为规范化后摘录中的连续短句；不能给合法 ID 随意附一条新规则或数字。
- module_fact：当前模组检索证据或当前快照内的合法 scene/NPC/clue 实体。公开 claim 只能引用已公开实体的原有文字；原始 keeper-only 摘录不能直接发布。
- flavor：只允许少量中性气氛短句，不引用来源，不产生 observation；新增地点、NPC、线索、物品或结果均被拒绝。

公开发布前验证全部 claim。非法证据/实体转为 `needs_host_ruling`；规则问题没有检索证据时也强制转为裁定，不能用公开场景句回避。拒绝原因只写主机事件，不发布被拒的原文。KP 的私密 `send_narration` 文本不直接传播，由独立公开上下文 run 生成正式叙事。状态改变只能通过原有封闭工具，模型不能覆盖实际骰点或判定。

通过验证的 rule/module_fact 保存 canonical observation、ClaimRecord 与来源/实体引用。调查员的推测写 belief；工具不能任意写 observation。场景与 reveal_clue 的确定性既有事件仍是权威来源。摘要保留早期引用，拒绝新编造的 evidence ID，摘要不会升级为 canonical world fact。

## 持久化、接口与界面

游戏库仅新增 `room_knowledge_bindings`、`agent_retrieval_records`、`agent_grounded_claims`、`knowledge_save_states`、`agent_model_calls` 五张表，通过原有 `create_all` 增量初始化，不改已有列。检索记录保存脱敏 query/hash、过滤条件、短 evidence、注入 ID 和耗时。模型调用表只存实际调用元数据、耗时/错误，用于重启后开发记录；不含 prompt。

绑定仅允许主机在大厅/暂停且无活动 cycle 时变更；不静默跨模组替换既有快照。原始模组只初始化主机填写的公开开场，不自动生成完整结构。存档保存绑定版本与 grounded claim 引用，旧日志/记忆保持可读；精确版本缺失时 `knowledge_missing` 阻止新 cycle，重新索引同版本或主机明确重绑后继续。

| API | 权限 |
|---|---|
| GET /api/knowledge/sources、/sources/{id}、/index/status | 主机 |
| POST /api/knowledge/index、/query | 主机；模组查询必须指定唯一 source/hash |
| GET /api/rooms/{id}/knowledge | 已认证房间成员 |
| PATCH /api/rooms/{id}/knowledge | 主机 |
| POST /api/rooms/{id}/knowledge/query | 房间成员，只检索绑定公开规则 |
| GET /api/rooms/{id}/agent-runs/{run}/retrievals | 主机 |
| GET /api/rooms/{id}/evidence/{evidence} | 主机审计，或成员已公开的规则引用 |

接口不返回本地绝对路径，不提供下载原书。证据接口重新验证房间身份和 claim 可见性，不依赖不可猜 ID。

“知识库”页显示来源状态、hash、页数、chunks、文件失败摘要和查询；房间面板绑定版本并显示缺失状态。既有事件时间线以 seq 去重排序，REST 与 WebSocket 共用追加入口，刷新从持久化事件恢复。公开日志有时间、cycle 短 ID、actor/type、KP 叙事、队友行动/发言、检定、骰点/等级、线索、cycle 状态及简短引用。HOST_DEBUG 独立折叠面板显示运行、工具、检索和私密记忆，不显示完整 prompt、token、密钥或思考过程。

正式日志导出沿用第二批 JSONL/Markdown 接口；第四批脚本写到 `.cache/batch-4/*-session.md` 的 PUBLIC/HOST_DEBUG 只是一次性开发测试产物，没有新增通用导出 API 或长期归档格式。

## 运行验收与限制

`backend/scripts/evaluate_knowledge.py --database <隔离知识库>` 在已有本地 PDF 上评测，不把原文写入脚本。运行 `uv run --directory backend python scripts/check_rag.py` 会自行生成原创合成知识库并使用三个真实 Chrome 配置目录；加 `--ollama --config <忽略目录内JSON>` 才调用本机 qwen3:8b。配置字段是 `data_dir`、`knowledge_database`、`module_title`、`binding`（rules/module source/hash）、`opening_scene`、`actions` 和 `other_module_titles`。配置中的开场必须是主机授权公开文字。测试数据库、截图、日志及配置留在 `.cache`，临时前后端/Chrome 由脚本清理，用户 Ollama 保留。

当前为保守的抽取式有据输出，不能证明任意自由改写的语义正确，也不能保证词法检索对所有规则问题有答案。无文本页、表格复杂排版、同义表达和跨书重叠会影响召回；没有 OCR、表格/地图视觉理解、自动完整模组解析、向量库或 reranker。DOC 依赖本机 Word，超时异常可能需要检查残留自动化进程。原始模组目前只能围绕主机公开场景推进；更长流程需要事先结构化实体。

后续若增加 embedding，可在 KnowledgeRetriever 接口下增加候选检索实现，继续共用 source/hash/edition/visibility 前置过滤和 GroundedClaim 验证，不让向量结果绕过边界。本批没有下载或启用该能力。

实现参考：[SQLite FTS5 官方文档](https://www.sqlite.org/fts5.html)、[Microsoft Word Documents.Open](https://learn.microsoft.com/en-us/office/vba/api/word.documents.open)。

## 第五批补充：有限词法优化与实体准备

原评测脚本和答案集合保持不变；独立 holdout 在调参前冻结。`scripts/evaluate_batch5.py` 同时运行两组评测，报告各组与合并的 Hit@1/3/5、MRR、延迟和无答案结果。两轮最终结果见[第五批报告](batch-5-report.md)，未达到的目标保留失败案例，没有继续调参。

检索增加确定性概念拆分、技能键／骰式／难度术语保留、短语与标题命中通道，以及按来源限定的 2/3-gram 和概念候选。多路候选使用 weighted reciprocal rank fusion 合并，按物理页去重，并给多概念和其他来源留出候选。来源意图仅加权，不排除另一本规则来源；没有 query 到页码映射、模型改写、embedding 或额外模型请求。

模组准备从同 source hash 下的选定页／章节顺序读取有限 chunk，为实际送入生成 run 的摘录记录证据 ID。模型实体证据必须来自该 run，摘要本身不成为新原文。准备模组的游戏检索以已批准当前场景标题和本次行动构造查询；原始模组证据仍仅 KP 可读，新事实先进入主机审阅。批准实体与公开实体是两种不同权限集合，详见[模组准备](module-preparation.md)。
