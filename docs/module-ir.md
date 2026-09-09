# ModuleIR、结构校正与场景导航

第六批将已准备模组的运行入口改为批准的文档结构和房间场景位置。第四批知识索引、第五批实体批准、主机审阅、公开调查板和规则书 RAG 继续使用。解析、定位和上下文选择均不调用模型。

## 操作流程

1. 索引本地模组，完成第五批开场实体和初始场景实体的核对与批准。
2. 在准备页「文档结构」区域点击「构建 / 校验 ModuleIR」。基础结构写入独立知识库，原文不变。
3. 展开目录并选择节点，核对原层级、来源、置信度、页码、段落位置。修改显示标题、父节点、批准类型、included，以及公开标题、公开摘要和主机短摘要。
4. 标记 scene 和 initial scene。绑定 approved 实体，来源匹配建议需要主机确认。NPC、地点可绑定多个场景，scene 实体只绑定一个 scene 节点。
5. 添加并批准转换，选择起点、终点、类型、公开实体前提和所需事件类型。反向移动需要单独批准的反向转换。
6. 批准结构快照，再在新房间绑定准备任务。此时同时固定准备版本、结构快照和来源 hash；未批准或过期结构不能绑定。
7. 游戏中主机查看「当前场景导航」和实际 cycle 审计。玩家读取公开场景、调查板和公开日志。

校正只作用于 override；不能编辑原 block、删除原节点、自由重排正文或修改 DOC。修改草稿会撤销供新房间使用的批准标记；已有房间继续引用不可变旧快照。

## Schema 与版本

核心对象继承 `DomainModel`，Pydantic 拒绝额外字段。`ModuleDocumentIR.schema_version = 1` 包含 module/source ID、source hash、structure version、标题、格式、提取方式、根节点、节点/区块计数、警告和时间。

| 对象 | 主要字段 |
| --- | --- |
| ModuleNode | ID、parent、有序 children、source order、depth、原/规范化标题、heading path、检测/批准类型、检测来源、confidence、来源位置、included、visibility、时间 |
| ModuleBlock | ID、所属节点、原始顺序、类型、text、表格单元格、style、outline、numbering、页码/来源位置、visibility、内容 SHA-256；另保存分页标记、书签名 |
| Position | 来源相对文件名、从 0 开始的段落范围、可靠物理页/页标签、字符范围 |
| StructureSnapshot | schema/准备版本、来源与结构版本、批准状态、校正树、绑定、转换、initial scene、incomplete |
| RoomModuleNavigationState | 房间、snapshot/version/hash、current/previous/visited、active NPC/location、revealed、可用转换、revision、事件序号、选中引用、pending transition/review、missing |

节点类型：`document / chapter / section / scene / appendix / block_group`。

区块类型：`paragraph / heading / list / table / read_aloud / keeper_note / stat_block / unknown`。不通过模型自动猜测语义；DOCX 明确同名 style 可提供后三种语义类型。

树校验检查重复 ID、环、失联节点、父子关系、depth/heading path 和孩子原始顺序。IR 检查计数、block 归属、唯一顺序及实际文本 hash。snapshot 还校验 linked nodes、绑定、转换两端和初始场景。公开场景必须由主机填写公开标题与摘要。

ID 使用 SHA-256：node 包含 source ID/hash、文件和原始位置；block 再包含内容 hash。同名标题不会碰撞。`ir1_…` 是当前 schema 与 source ID/hash 的结构版本。构建先核验整个原始文件清单和 hash，再复用完整合法缓存；缺失时重建同一版本。生成时间不参与 ID。source hash 变化创建新版本，旧房间不跟随 source head。

## 解析路径

| 格式 | 保留内容 | 限制 |
| --- | --- | --- |
| DOC | 本地 Word COM，只读打开、禁用宏/链接更新；Paragraphs 顺序、style、outline、列表标签、表格单元格、物理页、书签名、字符范围 | 需要已安装 Word；不可用/超时返回错误；不安装软件 |
| DOCX | 标准库直接解析 body XML，段落/表格原序、style 继承、显式 outline、numPr、书签、分页标记 | 无可靠物理页码；outline=9 正文覆盖标题 style |
| PDF | PyMuPDF 内容流、outline/bookmark、物理页/页标签、可检测文本表格 | 不做 OCR、图片或复杂版面推理 |
| Markdown | ATX 标题、列表、管道表格；代码围栏不产生章节 | 非完整 CommonMark AST，不支持所有 Setext/HTML 语法 |
| TXT | 按行顺序、通用编号标题；无可靠标题时单节点 fallback | 不推断完整故事语义 |

显式 outline 优先于 heading style，随后使用可用目录信息、格式自身标题/书签、通用编号和保守排版启发式。Word 书签只保留位置元数据，不把任意书签名伪装成标题。普通加粗正文不自动成章；字体启发式还要求短段落、正文标点检查和排版信号。低置信度节点产生主机复核警告。

一个模组文件夹是来源边界；多文件建立各自文件根，按文件与文件内部顺序组织。父节点校正不跨文件。旧 DOC 直接获得结构，不需要 DOC → DOCX 副本。原文件不保存、不覆盖、不重命名；调试派生文件只放 `.cache`。

## 数据与实体

知识库新增 `module_structure_versions / module_nodes / module_blocks`，按 `(structure_version, node_id, source_order)` 索引区块。读取时重建和验证完整 IR，缺行或 hash 损坏视为不可用。FTS5 表继续存在。

游戏库新增六张表：`module_structure_overrides / module_structure_snapshots / module_entity_node_bindings / room_module_navigation_states / module_navigation_receipts / module_navigation_save_states`。只增表，不破坏性修改旧表；核心 JSON 通过明确 schema 校验。

绑定沿用第五批 entity ID/evidence IDs，只允许同任务、同 source hash 的 approved 实体。NPC/location 支持多场景，item 可带 approved NPC 持有人。建议基于来源页和精确标题匹配，不自动批准。转换两端有 scene 实体时关联第五批 `ModuleEntityRelation(leads_to)`，执行条件来自冻结的结构快照。

## 权威位置与转换

绑定时初始化位置；开始/恢复房间前校验结构可用。current scene 只由 navigation state 决定，自然语言、记忆、旧 scene 文本或普通 session-state 编辑不能覆盖。

`transition_scene` 输入 `target_scene_node_id / expected_revision / request_id`。服务端检查来源、snapshot、included scene、revision、批准关系、公开实体和事件类型条件。`host_only` 不列为 KP 可执行转换。旧 scene_id 工具参数在结构房间中映射到节点并经过相同检查。

合法转换在房间事务中原子更新 current、previous、visited 和 revision，通过第五批实体机制公开到达场景，再更新公开投影。公开 `scene.updated` 与主机专用 `module.scene_transition` 分开发送。回执保证同 request_id 不重复执行，不同请求内容冲突。

缺少关系或条件未满足时复用 `HostReviewRequest` 和 `wait_for_host_review`，主机批准一次性转场或拒绝后恢复同一 cycle。已有真人检定时不建立并行审阅，继续遵守原工具/审阅上限。每个图节点重新检查导航 revision，冲突使旧 cycle 失败并提示取消或重试。工具 savepoint 拒绝后显式刷新 ORM 状态，避免过期属性触发异步数据库访问错误。

## ModuleContextResolver 与预算

输入为房间、角色、权威场景、批准结构/实体、已公开实体、最近行动和字符预算。

固定组织顺序：公开导入 → 当前标题/heading path/摘要 → 当前直属 blocks 和必要子节点 → 关联 approved NPC/location/clue/item 与公开实体 → outgoing transitions → 祖先元数据 → 最近访问场景公开摘要。现有 `build_context` 随后在总预算内加入事件和分层记忆。祖先不给整章正文；子树遇到另一 scene 立即停止。

长场景按行动匹配实体 block、read-aloud/主机重要 block、最近使用 block、当前子树词法匹配选择，为实体和转换预留预算。实体优先匹配当前行动，再考虑 NPC/clue。超长段落可给带 truncated 标记的受限前缀，原 IR 不变；表格作为整体。选择后仍按原 source order 输出，不全库回退。

审计保存 current scene、heading path、snapshot/hash/revision、selected node/block IDs、omitted/truncated block 数、omitted entity 数、预算和 fallback 原因。预算按 JSON 字符数衡量，受既有模型上下文限制；不是精确 token 计数。预算可能省略部分实体/转换，可使用实体索引和节点工具补充。

调查员和公开叙述器只获得当前公开场景、公开实体/调查板、公开事件及自己的私有记忆。原始 block、keeper summary、隐藏转换、后续 scene 正文不进入它们的上下文。公开日志按字段白名单移除 node debug。

## 工具、检索与 grounded claim

| 工具 | 权限与范围 |
| --- | --- |
| get_current_scene、list_scene_contents、list_scene_transitions | KP 的当前结构与内容索引 |
| open_module_node | 当前子树、祖先元数据或明确 linked node；最多 1600 字、单块 600 字 |
| lookup_module_entity | 当前/关联节点实体与已公开实体；同名返回候选及可访问路径 |
| transition_scene | 服务端导航与审阅 |
| search_module | 默认 current_scene；linked_nodes 仅明确关联；global 需主机或已批准 incomplete |
| get_public_scene、list_public_entities、lookup_public_entity | 调查员公开投影 |
| search_rules | 保持原规则书 RAG |

普通完整结构回合不执行全模组 FTS5。长场景或未知专有名词先在当前子树进行局部词法搜索，显式引用可扩展至 linked nodes。主机可主动跨章节搜索，处理 review 时同样可用；准备阶段保留原检索。主机批准 incomplete 后允许全模组预上下文和工具补充并记录 global fallback，该勾选并非仅允许主动工具调用。

结构版本缺失是阻塞，不能用全库 RAG 静默替代另一版本；未知事实继续走第五批审阅。

`GroundedClaim` 增加 `basis_type = module_evidence / approved_entity / module_node / host_authored` 和 `node_ids`。node 依据需匹配当前 source/snapshot、有访问权限且已在本轮上下文或工具结果出现；文本须由实际观察片段支持。原文 node 依据仅限 KP 私有事实，不能直接公开或改变 canonical state；公开事实仍经批准实体/公开事件校验。flavor 不成为 canonical fact。

KP 私有记忆可保留节点引用；转换后更新已有 KP 摘要的位置和 heading path。调查员记忆继续遵守公开事实与自身私有信息范围。

## API、界面与恢复

结构接口前缀 `/api/module-preparations/{id}/structure`，均为主机专用：

- `POST /build`、`GET`、`GET/PATCH /nodes/{node_id}`、`POST /approve`。
- `GET/POST /entity-bindings`、`DELETE /entity-bindings/{binding_id}`。
- `GET/POST /transitions`、`PATCH/DELETE /transitions/{transition_id}`。

房间接口前缀 `/api/rooms/{id}`：

- `GET /current-scene`：玩家只读公开场景，主机额外读导航。
- `GET /module-navigation`、`GET /module-context-debug`：主机受限预览和实际最近 cycle 审计，预览不覆盖回合选择记录。
- `POST /scene-transition`、`POST /module-search`：主机一次性转换/搜索。
- `POST /module-navigation/reload`：校验并重新加载原版本，不选择别的版本。

目录预览上限 1200 字、单块 420 字；context debug 使用 3000 字预算。界面提供树形展开、编辑、实体绑定、转换管理与批准，以及当前/前一位置、visited、NPC、转换、审阅状态和 cycle 节点审计。玩家接口和 WebSocket 不含隐藏节点调试信息。

导航存档保存版本、位置、active IDs、条件、revision、选择引用及 pending transition/review。读档恢复原位置并校验原结构，绝不自动跟随 source head。第五批已公开发现保持单调，玩家不会因读档失去已知线索；条件根据当前合法公开事实重新计算，历史事件追加保留，JSONL/Markdown 导出格式不变。

结构缺失、hash 不符或节点/实体/转换引用损坏时标记 `module_structure_missing`，保留历史和调查板，阻止新 cycle 和恢复运行。主机恢复匹配知识库，或在原 hash 不变时重建原准备任务，再调用 reload 后恢复房间。pending review、真人检定沿用既有恢复语义和 revision 检查。

## 本地验收与限制

`backend/scripts/check_module_navigation.py` 默认使用合成模组、Fake Model、隔离数据库和三套无界面 Chrome。`--real --config .cache/batch-6/real-config.json` 使用本地人工审阅的节点配置，只读备份第五批准备库和知识库，再连接已安装的 `qwen3:8b`。不下载模型、不安装 Word、不调用外部模型 API。过程产物在 `.cache/batch6-*-smoke-*`。

《常暗之厢》只校正开场和一次邻接转换。商业模组正文/配置不提交 Git。开发记录分 PUBLIC 和 HOST_DEBUG，放在 `.cache/batch-6/常暗之厢-session.md`；结果和限制见第六批报告。

- 结构不等于全部剧情语义；漏检标题、低置信度和单节点 fallback 仍需人工审阅。本批没有任意正文切分/新增节点编辑器。
- Word 内部链接未全部解析为转换，复杂表格合并和 PDF 表格检测不保证完整；无图片、OCR、地图识别。
- 保留既有单 worker 广播和串行模型，不扩展战斗、追逐、多模型。
- 内置练习模组及没有 ModuleIR 的已有旧房间保留兼容路径；新第五批准备用房间需要 approved structure。
