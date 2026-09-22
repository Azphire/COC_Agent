# 第41批：本机全部待备团模组交付

交付日期：2026-09-22（Europe/Paris）。基线及结束HEAD均为 `302001691ae667349e77d0db7e58d1769979f8e6`，当前分支 `main`。开始时工作区干净，`git pull --ff-only` 返回已是最新；已读第40批报告及备团说明。全程单执行者，没有子Agent、分支/worktree、commit/push、reset/stash。

盘点8个来源目录、51个原始文件，确认8个完整独立模组。保留2个已有有效成果，新完成6个来源校对包（245物理PDF页），均通过包审计、隔离导入/绑定及正式库导入。真实模型短局3个通过；《吱乎鲁》仅公开调查探针通过，完整HO建卡仍受限；另2个被原生规则/角色材料阻断。特殊机制未全部自动化不等于没有备团，短局也不等于全部分支实跑。

[完整机器清单](../data/prepared/batch-41/preparation-inventory.json)记录每个实际文件的SHA256、来源聚合hash、旧产物、处理原因、覆盖、缺项与运行限制。`data/modules/.gitkeep`仅占位，不是模组。没有消失的待办。

## 逐模组去向

| 名称 | 本次前状态 | 全文范围 | 本次结果 | 可用准备ID / 版本 |
|---|---|---|---|---|
| 吱乎鲁的呼唤 | 尚未备团 | 54页 | 全文包完成；公开短局通过；HO建卡加值仍需裁定 | `7954368e-b024-4303-944d-35e706a247e6` / v1 |
| 妖灵会馆守则-禁止接触 | 尚未备团 | 51页 | 全文包完成；原生规则/角色材料阻断真实短局 | `f3d3262b-bc80-4b83-a89f-47226349946a` / v1 |
| 希普拉 | 尚未备团 | 13页 | 全文包完成；真实模型短局及保存恢复通过 | `1dfdbe8c-405d-4296-a2ea-c73e0b36b321` / v2 |
| 常暗之厢 | 已有有效全文包与实跑 | 17页 | 核验后跳过，原绑定保留 | `51119e5b-9b63-4429-860f-b5a622690b70` / v1（原库） |
| 木星噩梦 | 尚未备团 | 85页 | 全文包完成；原生规则/角色材料阻断真实短局 | `b72c6150-e8a6-4e66-877d-203925215f7d` / v1 |
| 被煮沸的钢琴 | 尚未备团 | 34页 | 全文包完成；真实模型短局及保存恢复通过 | `9a7bd497-3f69-4467-9c91-ad47aa9b501f` / v1 |
| 追书人 | 已有有效全文包与实跑 | 19页 | 核验后跳过，原绑定保留 | `160fa7e4-c7f4-474f-acbe-972aec60c2a1` / v1（原库） |
| 长明灯 | 尚未备团 | 8页 | 全文包完成；真实模型短局及保存恢复通过 | `060c6417-6f0b-42f6-a004-dee676287594` / v2 |

准备ID属于各自数据库，不能跨库直接绑定。上表6个新包在正常配置的 `data/game.db` 中；旧两包继续使用原数据库与房间。正式选择接口复核见 [publication-verification.json](../data/prepared/batch-41/publication-verification.json)。

### 吱乎鲁的呼唤

来源：`吱乎鲁的呼唤.pdf`；目录聚合SHA256 `9b63dcfbcc35ce7e667dc86b5000760627b820790ef908106e2335df87ffedd1`。文件级hash见来源清单。

54页全文、3份私人HO、全部楼层/地下室、3盘内容与5类结局；15场景/39实体。PDF与Word比较，2张插图/标识及说明文本均审阅。

产物目录：`data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/`。

[可导入包](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/package-reviewed.json) · [逐文件清单](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/source-manifest.json) · [全文校对](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/source-review.md) · [审计](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/package-audit.json) · [分支条件核对](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/branch-analysis.json)

正式库准备 `7954368e-b024-4303-944d-35e706a247e6` v1；隔离导入 `719ea9d8-41c4-418f-8513-c228aaa4eae5` v2、房间 `67ec7f0f-782c-4e17-adf9-56062c987ac7`。重复导入同一内容复用同一ID/版本。

短验证：公开短局通过；HO建卡加值仍需裁定。
隔离房间 `4f0307d2-181d-4025-9498-42257154f6d6`；模型调用6次；保存、暂停、加载、恢复后session_state完全一致。
[实际公开记录](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/short-run-01/session-full.md) · [验收依据](../data/prepared/zhihulu/batch-41/9b63dcfbcc35ce7e/short-validation.json)

- 事件49实际揭示宴会安排，50完成询问，57/87为真实模型公开叙述且非安全回退。
- 初始只公开生日宴和庭院，未泄露HO身份、幕后真相或后续结局。
- 三名19岁学生基础卡通过正式创建，但HO专属属性/技能/信用加值未进入原生卡状态；源数值及派生有效表另存。仅验公开调查，不宣称原文完整角色验收通过。

限制及恢复：物理5–11页HO信用/心理学/潜行及属性+30，本机普通学生卡无法原生承载。已保存含年龄修正的source-effective-character-sheets.json，未冒称已写入卡状态。私密单线/天眼、特殊魔法、战斗缺护甲、时钟及结局前提见unsupported-mechanisms.json；需实际私密通道及有来源的加值适配或KP手动卡。

### 妖灵会馆守则-禁止接触

来源：`【妖灵会馆】禁止接触-慢慢.pdf`；目录聚合SHA256 `07f3a1ab6d7718464b734eea69b17c3f1ae53f18df069d7e7cd855abb3c33d22`。文件级hash见来源清单。

51页全文、城镇/森林/地洞/盗猎路线、日期互斥分支、NPC证词及结局；14场景/37实体。Word及36张图逐件分类；正文预设与附件卡差异独立登记。

产物目录：`data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/`。

[可导入包](../data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/package-reviewed.json) · [逐文件清单](../data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/source-manifest.json) · [全文校对](../data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/source-review.md) · [审计](../data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/package-audit.json) · [分支条件核对](../data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/branch-analysis.json)

正式库准备 `f3d3262b-bc80-4b83-a89f-47226349946a` v1；隔离导入 `58f4a880-4405-4de2-9535-879dcf952357` v1、房间 `a77ecb76-6ab1-4b50-ace7-ee09be8a0fcc`。重复导入同一内容复用同一ID/版本。

短验证：原生规则/角色材料阻断真实短局。
[正常规则接口阻断回执](../data/prepared/jinzhijiechu/batch-41/07f3a1ab6d771846/short-validation.json)。仍建立了上列隔离绑定验证房间，未使用错误规则角色冒充真实短局。

限制及恢复：物理7/12/27/31/40/44/50–51页使用妖灵D6池、伤害/成长；本地正文未提供完整成功阈值、初始HP规则。正常规则接口拒绝yaoling-d6（404），不可换成CoC卡宣称通过。恢复需补对应独立规则/完整原生卡及规则适配，再建隔离短局。第50页正文卡与附件赤焰/夭夭卡数值或态度不同，版本证据不混用。

### 希普拉

来源：`希普拉.pdf`；目录聚合SHA256 `51723b3d9c99809bf5335f048ad4caaa1c49412f9b7a144f4b3abf6db0ac7fd1`。文件级hash见来源清单。

13页全部正文和后半本，包括画室、诊所、地下室、群体求助与火灾收尾；7场景/27实体。Word正文实质相同，作者联系说明非新模组。

产物目录：`data/prepared/xipula/batch-41/51723b3d9c99809b/`。

[可导入包](../data/prepared/xipula/batch-41/51723b3d9c99809b/package-reviewed.json) · [逐文件清单](../data/prepared/xipula/batch-41/51723b3d9c99809b/source-manifest.json) · [全文校对](../data/prepared/xipula/batch-41/51723b3d9c99809b/source-review.md) · [审计](../data/prepared/xipula/batch-41/51723b3d9c99809b/package-audit.json) · [分支条件核对](../data/prepared/xipula/batch-41/51723b3d9c99809b/branch-analysis.json)

正式库准备 `1dfdbe8c-405d-4296-a2ea-c73e0b36b321` v2；隔离导入 `54c53542-c11f-473c-83ed-3cc4385fd369` v3、房间 `7a50d042-b48f-496b-af62-1753c8779126`。重复导入同一内容复用同一ID/版本。

短验证：真实模型短局及保存恢复通过。
隔离房间 `db9608b7-a77d-4891-8855-ba26e8c06884`；模型调用6次；保存、暂停、加载、恢复后session_state完全一致。
[实际公开记录](../data/prepared/xipula/batch-41/51723b3d9c99809b/short-run-03/session-full.md) · [验收依据](../data/prepared/xipula/batch-41/51723b3d9c99809b/short-validation.json)

- 2024警察角色通过正式建卡，事件35揭示罗布巷报案内容、36完成有来源的询问。
- 公开开场未泄露人类之外身份、隐藏整体意识、地下室遭遇或火灾结局。
- 短局03修复条件字段后，事件66真实执行分别留联系方式，并持久化olivine_contact_received；73 NPC正常回应。此前仅对话未写状态的短局02另存保留。
- 保存恢复一致；这只是警局短调查，地下室和火灾分支未实跑。

限制及恢复：原文未明定CoC版次，短局明示CoC7建卡适配；原稿8月20/21日冲突保留。分裂再生、输血/移植、火灾和怪物伤害缺数值需KP；侦查失败HP-1与离场普通成功未写结果单列。开场通过不代表地下室/火灾都已运行。

### 常暗之厢

来源：`常暗之厢 2-3.doc`，17物理页；聚合SHA256 `a72e88d5a4cb08831bb4ab6719094447b6c995932b4760945a1abb871cad57c8`。

沿用 [package-approved.json](../data/prepared/changan/batch-21/package-approved.json)；准备记录来自 `data/prepared/changan/batch-39/acceptance-run5-20260919/game.db`。通过只读SQLite备份上的正常准备/结构API确认 approved、bindable、结构approved且非stale；没有重建、导入覆盖或替换旧房间。

运行限制：第39批固定代码完整新局到达结局B（seq1130），第40批另有结果反馈等定向修复；本批未重跑。 A/C全分支未在本批验收；自然反馈、无依据细节、失败后队友自主调整及取消变体存在历史质量限制，不因此重做全文包。 第17批主机补充值与原文值须继续区分；现有准备包审核身份不是用户数值批准。

[本次核验依据](../data/prepared/changan/batch-41/a72e88d5a4cb0883/existing-verification.json)

### 木星噩梦

来源：`克苏鲁-星升 木星噩梦.pdf`；目录聚合SHA256 `538633b37880d1424cdced323bfd9f12f19ec47cd8e476f10fd83ff097c672df`。文件级hash见来源清单。

85页全部核对；完整冒险《逃逸速度》为48–76页，57编号区域及21 NPC另有逐项索引；8场景/89实体。地图、世界资料、小说、7种子、术语、空白角色卡和封底均入覆盖。

产物目录：`data/prepared/muxingemeng/batch-41/538633b37880d142/`。

[可导入包](../data/prepared/muxingemeng/batch-41/538633b37880d142/package-reviewed.json) · [逐文件清单](../data/prepared/muxingemeng/batch-41/538633b37880d142/source-manifest.json) · [全文校对](../data/prepared/muxingemeng/batch-41/538633b37880d142/source-review.md) · [审计](../data/prepared/muxingemeng/batch-41/538633b37880d142/package-audit.json) · [分支条件核对](../data/prepared/muxingemeng/batch-41/538633b37880d142/branch-analysis.json)

正式库准备 `b72c6150-e8a6-4e66-877d-203925215f7d` v1；隔离导入 `4057ff6c-dce7-44d4-bb5b-8f541c4141bd` v1、房间 `db023fbd-3ae4-431d-8369-540f35f3844d`。重复导入同一内容复用同一ID/版本。

短验证：原生规则/角色材料阻断真实短局。
[正常规则接口阻断回执](../data/prepared/muxingemeng/batch-41/538633b37880d142/short-validation.json)。仍建立了上列隔离绑定验证房间，未使用错误规则角色冒充真实短局。

限制及恢复：物理7–8/48/52/63/68–74/82–83页为Cthulhu Rising/BRP原尺度，含POT、部位HP和EVA；六人背景不是完整数值卡，82–83为空白卡，引用的预设下载包本机无文件。正常接口拒绝cthulhu-rising-brp（404）；不擅自属性×5。恢复需完整规则/数值卡及原生适配；供氧/夹钳/货船钥匙缺解法和50分钟时钟另需KP实证。

### 被煮沸的钢琴

来源：`被煮沸的钢琴 4（推理）.pdf`；目录聚合SHA256 `40b854ca586d70e4813e1eef0abedd04fd1015806d3a2978bf2d44c81a4fc00e`。文件级hash见来源清单。

34页全文、3轮时间线、4私人HO、33条记忆、14×4记忆矩阵、9×9关系矩阵、全部结局；17场景/34实体。关系图及附录数值视觉复核，原文时刻/称谓冲突保留。

产物目录：`data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/`。

[可导入包](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/package-reviewed.json) · [逐文件清单](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/source-manifest.json) · [全文校对](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/source-review.md) · [审计](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/package-audit.json) · [分支条件核对](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/branch-analysis.json)

正式库准备 `9a7bd497-3f69-4467-9c91-ad47aa9b501f` v1；隔离导入 `187bad81-ac7a-470e-a5c2-2b2a62491ef1` v1、房间 `c8747a40-277b-4b9d-b4c5-24edce3b7566`。重复导入同一内容复用同一ID/版本。

短验证：真实模型短局及保存恢复通过。
隔离房间 `30dcd64d-9c74-49d5-91f4-902ce8e8d548`；模型调用3次；保存、暂停、加载、恢复后session_state完全一致。
[实际公开记录](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/short-run-01/session-full.md) · [验收依据](../data/prepared/zhufeigangqin/batch-41/40b854ca586d70e4/short-validation.json)

- 四名调查员按四种HO职业、1973背景和原文数值上限通过正式角色接口。
- 事件56实际完成观察演出介绍；63真实模型叙述且非安全回退；公开开场未泄露失火、身体灵魂交换、女巫及结局。

限制及恢复：物理7–12/20–25页涉及灵魂/身体与时间回退；当前短局未进入该机制。12/22/24–25页时刻和分支优先级存在原稿矛盾，需KP开团前定。定时私人记忆、固定SAN、反向暗骰及特殊法术不能用旁白冒充状态结算。

### 追书人

来源：`追书人 1-2.pdf`，19物理页；聚合SHA256 `6c2941c8c4e87c1303c5bde5af32a757fd92aa7be736ea8694af87af249a892d`。

沿用 [package-reviewed.json](../data/prepared/zhuishuren/batch-40/package-reviewed.json)；准备记录来自 `data/prepared/zhuishuren/batch-40/preparation-v5/game.db`。通过只读SQLite备份上的正常准备/结构API确认 approved、bindable、结构approved且非stale；没有重建、导入覆盖或替换旧房间。

运行限制：第40批和平离去线已真实完局（主局seq1937）；不能采用导入审计中not_run_by_import旧标签否认既有实跑。 物理8–18页剧情：买酒EDU/2美元/幸运96–100拘留、屏息昏迷、部分孤注时间与扣酬、熟睡唤醒、枪伤减半/累计6伤击晕及群鬼结局链仍需KP。 酬金无货币账本、守夜文字时间与数值时钟未完全同步；NPC依据、队友贡献及澄清质量仍有第40批已记录限制。

[本次核验依据](../data/prepared/zhuishuren/batch-41/6c2941c8c4e87c13/existing-verification.json)

### 长明灯

来源：`长明灯 1-2（近代）.pdf`；目录聚合SHA256 `13c4d0c48760ac7bf0847abca861c1ce9c04ca6eeca2c44bae0ea12ff16efaba`。文件级hash见来源清单。

8页全部为扫描页；Windows OCR后逐页看图校正，保留原OCR和改动表。8场景/30实体，茶馆/祠堂/郭和/放火/仿灯/替代照明/离村/奖励均覆盖。

产物目录：`data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/`。

[可导入包](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/package-reviewed.json) · [逐文件清单](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/source-manifest.json) · [全文校对](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/source-review.md) · [审计](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/package-audit.json) · [分支条件核对](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/branch-analysis.json)

正式库准备 `060c6417-6f0b-42f6-a004-dee676287594` v2；隔离导入 `2bb223fa-0828-4565-9ecc-f85b1326ff4f` v4、房间 `866ce2fd-b042-4364-875f-cfe694c46cf6`。重复导入同一内容复用同一ID/版本。

短验证：真实模型短局及保存恢复通过。
隔离房间 `80154321-be7a-4eb6-9ac9-3c02011742ca`；模型调用5次；保存、暂停、加载、恢复后session_state完全一致。
[实际公开记录](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/short-run-04/session-full.md) · [验收依据](../data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/short-validation.json)

- 1925记者卡通过正式创建，现代物品未混入；41实际转场到茶馆。
- 53实际玩家豪爽销账并询问，71揭示原文茶客消息，72执行无检定合法信息互动；79真实模型叙述非回退。
- 开场只给任务和可见茶棚，未泄露两重诅咒真相、郭和召唤物或日落结局。
- 80 NPC茶钱由谁付的措辞不准，未形成资金扣款回执；线索内容与原文一致。此处不声称有完整金钱账本。
- 失败01、歧义02、条件字段不适用03均保留；修正版04保存恢复一致，不冒称追逐、战斗或全部结局通过。

限制及恢复：物理2/6页日落与失去照明为两种不同毁村条件；3–6/8页增援、追逐、2d6分钟和炎之精特殊攻击/免疫需KP。7–8页未给护甲/部分DB保持缺项。保留假灯、调包、替代照明和救人路线，不宣称全部自动通关。

## 来源范围、覆盖与审批

《木星噩梦》1–47页为世界资料与两篇小说；完整模组《逃逸速度》48–76页单独登记；77–80页含“无法自拔、极点、出卖、木卫五十六、冰川、盖尼米得劫案、打捞”七个种子，各仅提案/变体，缺完整场景和数值，不算七个现成模组。81页术语、82–83页空白卡、84–85页标志/封底也有明确去向。详见该目录scope-register.json、map-review.json、timeline.json及source-npc-statblocks.json。

全部正文通过现有extraction、索引与Module IR提取。扫描《长明灯》8页逐页渲染，用Windows OCR并逐项视觉校正；其他低文本封面、地图、图像线索和关系图均记录视觉复核。PDF物理页码与Word文本单位分开，原稿未改写，纠正文本和附件检查只写独立交付目录。相同题名的Word/PDF、预设卡不同版保留差异，未拼凑当作一个无冲突版本。

分段生成继续复用PreparationService的串行窗口、稳定证据与恢复机制，单窗口3批/最多6调用。完成量：吱乎鲁43/43、禁止接触20/20、希普拉6/6、木星噩梦83/83、钢琴34/34、长明灯8/8。生成草稿独立保存；审批包由Codex对照全部来源后构建并经正式导入审批，不把模型草稿当校对。reviewed_by明确为“Codex source review, batch-41 (not human/user approval)”，没有伪造用户审核。

全部245页逐页入覆盖表；封面/背景用context，已配置内容用prepared，特殊规则及数值未给处用host_ruling并附具体缺项。source_accounting_complete只证明覆盖有去向；branch-analysis逐条检查主要线索/道具前提、多解及结局，仍无法证明的原稿冲突或特殊机制如实标注，all_branches_played均false。

## 实际修复、短验证边界与保护

生产修复仅两处：混合PDF/Word包的实体来源引用按实际block保留文件名与物理页类型；静态前置条件审计补识别pickup/initial/recover为物品来源。生成提示收紧证据数量以避免输出截断，并要求复用实体ID时保持目录title/type；仍保留严格证据/数值审批门禁。

短局发现并修正《长明灯》的入口可见性、集合实体与NPC重名、各NPC证词绑定，以及两包把叙事条件误写成专用位置事实键的问题。使用既有aliases/interactions/situation配置完成，不扩建引擎。原生位置事实字段不能填任意中文条件；现有情境裁定仍需实际动作引用，不允许旁白直接改状态。旧失败短局和修订版本保留。

真实模型沿用本机Ollama qwen3:8b，num_ctx=8192、输出预算900、think=false、单次timeout=240秒，串行运行；生成阶段沿用已验证备团配置。每个短局正常建卡、导入、绑定、启动，经真实模型处理自然行动，公开事件与最终玩家视图逐项校对；没有隐藏真相字段、跨模组来源或开局未来结局泄漏。保存恢复比较真实session_state。角色未支持、未通过的分支不计成功。

已执行回归：相关旧包/备团测试与本批测试合计42项通过；生成相关定向回归9项通过；最后本批9项通过（4.70秒，含盘点重跑保留交付ID/验收状态回归）。曾因系统临时目录权限失败，改用工作区独立basetemp后通过。Ruff检查全部修改Python文件通过，git diff --check通过；pytest缓存权限提示和第三方弃用提示不影响测试结果。

对预先列明的170个保护文件只做首尾hash核对：169个保持不变，包括全部51个原稿、旧准备包/运行数据库、旧存档和.env。唯一变化为原先0字节的data/game.db，经正式API创建并导入新成果；没有测试库覆盖、直接SQL状态写入或旧房间绑定替换。最终HEAD仍是基线。详见protected-files-initial.json与protected-files-final.json。

恢复复验实际运行了全部resume：当时六包的文件hash、隔离准备ID/版本和generation-progress字节均未变化，没有再调模型生成。其后发现的条件配置修正按新内容形成新版本，旧版本保留；相同内容再导入仍复用。

## 批量、恢复、重试与导入命令

在仓库根目录PowerShell执行。来源变更会生成新hash目录，旧版本与旧绑定保留。process/resume为提取→串行分段生成→已校对包构建→审计/隔离导入；若新来源尚无完整来源校对材料，停在awaiting_source_review，不能自动冒充批准。生成失败保留failures.log及模型验证错误，下一个模组继续。

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 inventory
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 process
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 resume
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 retry
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 resume --module muxingemeng
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 validate
backend/.venv/Scripts/python.exe -m scripts.play_batch41 run
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 publish
```

`--module`可重复；retry仅处理清单status=failed，已完成实体/生成批不重复。short-validation独立于source preparation；真实短局完成后仍需读公开日志确认，脚本不会自动把awaiting_transcript_review当通过。原生规则阻断不调用错误规则模型局。

修订包重测须用新的房间目录；旧目录恢复要求包hash与绑定一致。继续已有隔离房间可追加真实自然动作：

```powershell
backend/.venv/Scripts/python.exe -m scripts.play_batch41 run --module changmingdeng --run-name short-run-new
backend/.venv/Scripts/python.exe -m scripts.play_batch41 run --module xipula --run-name short-run-03 --action '我请报案人再说明地点。'
$bundle="data/prepared/changmingdeng/batch-41/13c4d0c48760ac7b/package-reviewed.json"
backend/.venv/Scripts/python.exe -m scripts.module_package audit --bundle $bundle
backend/.venv/Scripts/python.exe -m scripts.module_package load --bundle $bundle --directory data/prepared/batch-41/manual-import
backend/.venv/Scripts/python.exe -m scripts.prepare_batch41 publish --module changmingdeng
```

`module_package load`只导入指定独立目录；publish使用正常Settings与已有主机鉴权，通过POST /api/module-preparations/import导入，不操作房间。新增准备已在正常备团列表可选。新房间可通过正常“选择准备模组”入口绑定；API等价为PATCH /api/rooms/{新房间ID}/module-preparation，body为{"preparation_id":"上表正式库ID"}。切勿给旧在用房间替换绑定。

交付数据按既有.gitignore保存在本机data/prepared，不会因本次代码同步自动进入远程；没有自动commit/push。短局目录中的private-auth.json与数据库仅供本机恢复，公开记录使用session-full.md，不传播鉴权内容。
