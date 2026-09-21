# 第40批：实际结果反馈、队友调整与《追书人》真实试跑

本批已完成代码修正、全文准备和一次从正常建卡到原文结局的真实调试局。**结局为“道格拉斯离去，盗书案结束”，seq 1937 `module.completed`。不是全程固定版本通过，也未达到自然交互质量全面验收。** 剩余问题主要是 NPC 无依据发挥、队友贡献弱、承诺不落实、过度澄清及结尾台词贫乏；特殊战斗等未自动化分支仍有准备缺项。

| 核对项 | 实际值 |
|---|---|
| 模组／原件 | `data/modules/追书人/追书人 1-2.pdf`，19物理页 |
| 原文件 SHA256 | `8088809cac6e1196f67c925e81c486cdd287892e689d20aa27efad98412ebfc0` |
| 索引来源 hash | `6c2941c8c4e87c1303c5bde5af32a757fd92aa7be736ea8694af87af249a892d` |
| 准备包文件 SHA256 | `f0e0f1c33e8bf2499c3f6af949c6aa88c6a3523ba6ba70f9a30ef37fdd6afce7` |
| 准备包规范化摘要 | `81c20b71d292a167a3b43bede908bd4b2767c26a8c192a9e27c95f663ae62945` |
| 主局实际绑定 | `8abad664-5eac-49d3-b4f3-d361280fd75f`，version 1；开局前核对标题、来源与包 hash |
| 主局运行目录 | `data/prepared/zhuishuren/batch-40/run-20260921/` |
| 房间 | `e6dde3a5-bcf1-4c8b-a4ed-615757f1b00d` |
| 应用基线／主局结束时加载补丁 | `main`／`d33dc4fcce8b23759c785cd9d0cdfa802fe86ed7`；v13 diff SHA256 `ac6afa507a47ddfcbbbba5360ed79a10e6c6c140c88ad5a1d226c0f7c4e1e18a` |
| 最终交付代码 | v19；`final-version/code-version.json`，diff SHA256 `0ead6f55ba2c1e7508426db3bd1392fe4f3b8e206158956443b2a7ce7c4f45dc`；主局结束后只在副本定向复验 |
| 最终状态 | 模组 completed=true；当前回合 completed；房间容器 running，结局后已保存、服务已停止，未调用强制结束 |

## 复现、续看与导出

在仓库根目录 PowerShell：

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONIOENCODING='utf-8'
# 启动已完成的原房间；另一终端可 observe/poll，保留原存档和认证文件
backend/.venv/Scripts/python.exe -m scripts.play_batch40 serve run-20260921
backend/.venv/Scripts/python.exe -m scripts.play_batch40 observe run-20260921
backend/.venv/Scripts/python.exe -m scripts.play_batch40 poll run-20260921
# 离线重新导出公共事件、团录和脱敏审计
backend/.venv/Scripts/python.exe -m scripts.play_batch40 export run-20260921
```

当前已到结局，无待续阻断。若在其他未完副本中继续，使用 `act <目录> '普通玩家行动'`；`restore <目录>` 会正常保存、暂停、加载、恢复并核对检定及回执，不是回退重投。不要对主局再运行 init。

要复现准备导入或另开新局，使用新的空目录，不覆盖交付材料：

```powershell
$env:DATABASE_URL='sqlite+aiosqlite:///D:/Codes/COC_Agent/data/prepared/zhuishuren/batch-40/bootstrap.db'
$env:CHECKPOINT_DB_PATH='D:/Codes/COC_Agent/data/prepared/zhuishuren/batch-40/bootstrap-checkpoint.db'
$env:KNOWLEDGE_DB_PATH='D:/Codes/COC_Agent/data/prepared/zhuishuren/batch-40/bootstrap-knowledge.db'
backend/.venv/Scripts/python.exe -m scripts.module_package load --bundle data/prepared/zhuishuren/batch-40/package-reviewed.json --directory data/prepared/zhuishuren/batch-40/reimport-new
# 先在一个终端 serve run-new，再在另一终端正常 init
backend/.venv/Scripts/python.exe -m scripts.play_batch40 serve run-new
backend/.venv/Scripts/python.exe -m scripts.play_batch40 init run-new data/prepared/zhuishuren/batch-40/investigators.json
```

配置中的 package_path 为本机绝对路径；迁移机器时仅修改新配置副本的路径。准备构建入口为 `backend/scripts/prepare_batch40.py`，本地全文、索引和 IR 已保存。它会重写本批输出包，核对时优先复用现成包及独立导入命令。

## 基线与保护

执行前已阅读第39批报告、检查仓库指令与 HEAD；工作区干净后 `git pull --ff-only` 返回 Already up to date。单执行者，无子 Agent、分支、worktree、commit、push、reset、stash。新材料全部在独立 batch-40 目录。没有重跑《常暗之厢》全程。

核对了本机第39批 acceptance-run5、short26/final-failed-followups、short27/final-light-operations。上传 `session-full(4).md` 对应 short27，仅四次输入、恢复后的短复测；第39批已有固定版本结局 B 的成果未改写。原问题 1147 找开关只说手机光、1172 重复急救、1241 关灯却说更易观察，以及工具箱追问被旧钥匙结果替代，作为本批针对性依据，见 `baseline-evidence.json`。

首尾按 `protected-files-initial.json` 的111条键、110个唯一文件逐一比对，**0变化、0缺失**。包括 `.env`、原 PDF、用户数据库、旧批准包、指定旧运行与旧存档；未递归扫描全部历史目录。最终仍为原 HEAD 和 main，修改留在工作区。证据：`protected-files-final.json`。

## 代码结果与边界

1. `public_results()` 先从权限过滤后的原事件生成完整结果，再投影给旁白和队友；保留 cycle、执行者、实际操作、目标／物品实例、状态、效果、来源。模块交互仅记实际授权的操作交集，另存原动作目标和原话，避免把一个方法的所有备选动词都算作已做。
2. 结果按动作周期、执行者、操作和物品分别匹配；失败目标保留原动作，不沿用模型误绑名称。当前周期不会由已压缩的旧 `action.result` 反推真实发生了什么。旧通路仅在同次实际通过与同一目的地到达均存在时消除相反失败结论。
3. `action.result` 接入 `fact_records()` 与检索预算，结果追问优先读对应回执；明确历史问题保留过去，原话复述与现状回答分开。只读不能绕过真实性或持有权校验，也不能把新 NPC 问话误当旧结果追问。观察回退围绕当前对象，未执行不能说已成功。
4. 取消先识别否定作用域，再按既有任务 key 与操作匹配；取消医疗与新安置分开。候选生成前提供最新真实结果与治疗可执行条件，相同伤势、方法无新条件时不主动排队规则禁止的急救。技术失败恢复及合法新条件沿用既有机制，没有新增常驻评审模型或调度框架。
5. 新模组现场补齐可选 NPC 字段，压缩重复模型投影而保留退出条件、目标及物品所有权。唯一姓名前段可定位队友，重名不猜；建议问句不再强制带一个玩家行动。推拉动作边界不误命中人名，方向推拉仍有效。
6. 原文无额外条件、非视觉、自动公开触发的 SAN 不再让第二次情境判断否认已公开事实；未结算遭遇可从真实来源恢复，当前队列严格核对来源、效果和角色。视觉与有条件效果仍走原审查，重复检查仍受防重规则约束。
7. 社交方法含 converse 备选操作即可进入既有裁决；不再要求操作列表恰好只有 converse。保留物品、使用、遭遇、时间和终幕的额外权限检查。

## 准备覆盖

| 层次 | 核对结果 | 不能据此声称的内容 |
|---|---|---|
| 文字与图像 | 19页全文提取、全部渲染检查；891原始块保留；物理13页地图逐项转录 | 文件名1-2不是页码范围；封底印刷48不表示本 PDF 有48页 |
| 实体与结构 | Codex 对照原稿校对12场景、37实体、23转场及前后半程；私密真相不直接公开 | 不是用户人工批准，也不是严格盲测 |
| 可执行机制 | 询问、资料查询、书房、陵墓、隧道、守夜、身份／真相SAN、离去与结案均有既有裁决入口；和平线实际跑通 | 静态覆盖不等于每个分支已实跑 |

逐页范围、数值和缺项见 `source-review.md`、`source-manifest.json`、`package-audit.json`。全文含完整《追书人》（剧情物理8–18页）；其他模组只在目录提及，未杜撰后续章节或结局。封面1、插画7、封底19均人工式视觉核对，无需 OCR。自动索引报告18/19、封面低文本量 partial；人工逐页核对补足19/19，未把 partial 静默当作完整自动提取。

最新独立导入验证为 `preparation-v5/`，准备 ID `160fa7e4-c7f4-474f-acbe-972aec60c2a1`；54条来源引用验证为 PDF 物理页。主局绑定的旧 version 1 保留了导入器修正前的 `page_kind=text` 元数据；来源文件、hash、实际页码和内容一致，没有改写冻结绑定。所有审阅者标记明确为 `Codex source review, batch-40 (not human/user approval)`。

原文数值与适配分开记录：NPC 未给护甲等值留空。真相固定1D4损失借现有 SAN 服务两分支同值实现，保留了原文没有的百分骰步骤；神话+3在结案时兑现。买酒EDU／2美元／幸运96–100拘留、未屏息昏迷、部分孤注时间和扣酬、熟睡被唤醒、枪伤减半／累计6伤击晕／群鬼结局链尚未完整自动化，影响对应支线。酬金为叙述支付，无货币账本；守夜到次日场景的文字时间与数值时钟也未完全同步。永久地下失踪已配置，特殊战斗三后果只作私密来源分支，未伪称可自动跑通全部结局。

## 实际运行、版本和交互质量

1922年，两名原文推荐职业的搭档：Codex 玩家丹尼尔·里德（29岁记者），AI 艾琳·沃克（31岁私家侦探）。正常随机建卡、技能分配、提交／接受、就绪与冻结；保留 original-rolls、draft、frozen。新笔记本、钢笔和记者相机，无旧局手机、钥匙或记忆。模型调用244次，KP、NPC及队友均由本机 Ollama `qwen3:8b` 完成，8192上下文、900输出、think=false，未接单测替身。

实际路径：委托→书房窗锁→邻居（APP 60/35失败）→警方职业资料→公墓守墓人（说服26/70困难成功）→墓碑／陵墓→屋外守夜（幸运20/70困难成功）→跟随抱书人→会面→道别封门→返回托马斯家结案。没有读私有攻略驱动玩家动作，但准备者已读全文，明确不是盲测。

关键证据：

| 事件 | 发生内容与评价 |
|---|---|
| 626–628、1819–1821 | 两次正常保存恢复；检定、工具回执、队友状态相同，重放已结算投骰不改变结果 |
| 972、976–977 | 玩家说服守墓人，实际取得地点指引，失败邻居路线没有被偷偷改成成功 |
| 1103 | 调试期错误把“艾琳检查”给玩家投侦查52/65；保留骰值，不能算队友贡献；v9修复简称归属，1308后不再错投玩家 |
| 1379、1383–1384 | 玩家守夜成功，实际出现人影；不靠错误的蹄迹归属或即兴碑文推进 |
| 1502、1507、1512 | 通过并到达会面，却附带相反未执行；v11真实副本复算已消除矛盾，旧团录不覆盖 |
| 1540、1565、1619–1654 | 身份和真相公开；修复阻断后两名在场调查员均完成原骰 SAN |
| 1804、1809 | 实际离去封门，原事件→结果事实→公开结果→记忆→保存恢复一致 |
| 1933–1937、1941 | 系统奖励原骰6与4，正常结案，神话各+3；两人最终SAN均53 |

版本边界完整见 `run-20260921/version-boundaries.json` 与 start/v2–v13代码归档。v2–v7处理可选字段、预算、动作误识别和问话；v8–v10处理结果回退、简称和建议归属；v11–v12处理通路事实和SAN续接；v13处理社交备选操作。主局结束时 backend/app 文件与已加载v13逐个hash相同。之后 v14–v19 在独立短测中修正完成问句、搜索对象和旧NPC绑定覆盖结果的问题；最终代码不是主局从头加载的版本。最终差异及原始结束时核对均保留于 application-version-verification.json。修复前全部错误保留，不能将52次输入的混合版本团录称为固定版本验收。

队友实际贡献有限：艾琳询问文件、向守墓人打招呼、提供谨慎建议、辨认后接话，并在结案时追问委托人。8次 action_proposed 不是8次成功；两次试图带队转场被禁止，若干次观察仅承诺或生成失败，没有独立取得经规则结算的新线索。失败后曾改为提问或建议、沉默，但也重复建议找守墓人、复述“我和艾琳”、守夜只承诺。不得把1103玩家的错误归属成功记成她的贡献。医疗取消后具体扶靠的改道证据主要来自旧现场短测，而非这场没有伤员的模组。

剩余自然交互缺陷：NPC出现无依据咖啡馆、月份、碑名和新委托；1877把一年说成多年，1660对隐瞒真相的说法自相矛盾；6次行动澄清中多次没有必要。道别经多次重述才落实，向NPC汇报本身没结案，随后普通“就此结束调查”才由现有源规则结算；最终KP只说场景标题。普通细节允许即兴，但这些会改线索／事实的内容仍不合格。详见 `interaction-review-notes.json`，不以到达结局掩盖这些问题。

## 定向验证和统计口径

旧现场 short01–05 为调试过程，目录中的 final/fixed 字样不代表最终通过。short06 的5次输入验证未执行照明追问与手机灯关／开，原事件1412／1446到事实、公开结果、记忆和恢复一致；short07 的2次输入验证工具箱失败追问未借钥匙成功，以及取消包扎后实际扶靠1856／1883。是否取消了仍活动的旧医疗key由行为回归验证，不把已完成任务的现场说成成功取消活动队列。

最终定向短测的结论与增量指标见下表及 `delivery-metrics.json`。只统计 fixture-provenance.initial_max_seq 之后的新事件，继承团录、旧骰和重叠调试不累计。

| 短测 | 本次输入／模型调用／失败调用 | 实际结论 |
|---|---|---|
| short08，v13 | 5／37／12 | 1325真实关手机灯，1369追问正确；1399安置提案→1434扶靠，无重复急救。1463找开关反馈围绕开关；1499追问仍误排否定句为动作，后续修复。 |
| short09，v13 | 1／7／3 | 1826明确工具箱取物未执行，不借旧钥匙成功；没有新增取物。 |
| short10–14 | 各自日志 | 调试反例：开关追问曾被手机灯、便签或伤员描述替代；离去后旧NPC还答话。均不记为通过。 |
| short15，v17 | 1／4／1 | 从主局正常快照恢复；1974正确说明道格拉斯离去、巨石板封门，无新动作／SAN／转场。 |
| short16，v18 | 2／9／3 | 手机灯关闭追问正确；开关搜索问句仍解释其他照明操作，保留为失败例。 |
| short17，v19 | 1／4／2 | 1588按实际证据回退“还没有确认找到开关。”；无队友新动作、无搜索检定、无治疗或物品副作用。 |

short17 依赖一次定向修正后的确定性安全回退，不能冒称模型原稿一次答对。short15 的后续代码改动只涉及搜索问句分支；最终同一结果／记忆管线复核与行为回归保留原离去答案。short08 还正常补结了历史 source seq 415 的未完成SAN，复用了原始日期的骰33，未改骰；紧接只读追问没有新增SAN。

回归：`regression-final.log` **221通过**，覆盖第39/40、37/36、结果回忆、模块包17/24、记忆工具和SAN/存档恢复10及18指定用例。最后两处社交判断修改后，`regression-v13.log` **130通过**；二者有重叠，不相加。结束后追加修复的 `regression-v16.log` 142通过、`regression-v18-final.log` 92通过（含NPC绑定下的真实记忆构建回归），最后回答范围修正的 `regression-delivery.log` 84通过；同样不累加。扩大跑第18批时发现3项旧断言仍失败（观察动作的旧意图预期、明确请求重试、初始物品搜索范围），用HEAD版本相关函数在隔离进程复核可重现，记录在 `baseline-three-legacy-tests.log`；未声称全仓库测试通过。新增字段测试的一处kind断言、方向推拉和旧mock接口问题已修正复验。修改文件 Ruff 通过，`git diff --check` 通过。

主局单独统计：52玩家输入、60含队友回合、244模型调用、45失败调用；最终未完成／失败回合0，但历史发生6条agent.run_failed并正常续接。失败调用包含后来定向修正成功者，不等于45个未解决回合；队友 generation_failed 事件19，终态决策失败23按原审计口径分别保留。行动澄清6，加通用SAN补问1，共7。旁白安全回退事件15、发生回退的回合16，其中全段14、保留有效部分2，口径差异由私有验证记录与公开事件决定。

主局实测模型耗时2170.119秒，活动回合区间并集3323.339秒，差额1153.220秒包括应用、校验、检定等待、队列与同轮调试停顿，不是CPU时间。嵌套父子回合只计一次，不把整场人工停顿或其他短测加进模型耗时。

## 交付与审计

完整团录 `run-20260921/session-full.md`；全员公开事件 `public-events.jsonl`；玩家本人视角（含自己的SAN）`public-events.json`；脱敏模型输入输出、计划、检定、回执、任务状态与建卡证据在 `private-audit/`。只以 `audit-manifest.json` 所列文件作为脱敏交付，数据库、private-auth、session认证和完整存档只供本机续跑，不应整体外传。最终清单的hash及敏感值扫描另有验证结果。


主局与 short15、short17 的关键证据采用正常API和本地真实模型；单测才使用替身。准备覆盖完整、和平线运行完成，但自然交互仍部分不合格，尤其队友没有独立实质线索贡献、部分NPC内容失真；本报告不宣称所有完成标准已无缺陷满足。

定向复现（使用新的空目录；命令会从源副本通过正常API加载存档，只统计新事件）：

```powershell
backend/.venv/Scripts/python.exe -m scripts.play_batch40 probe verify-module-new data/prepared/zhuishuren/batch-40/run-20260921 natural-save.json '刚才道格拉斯真的离开了吗？现在入口怎么样？'
backend/.venv/Scripts/python.exe -m scripts.play_batch40 probe verify-switch-new data/prepared/zhuishuren/batch-40/short08-v13-light-cancel - '刚才找到开关了吗？'
```

最终交付审计核对18份清单、212个列入清单的文件：hash有效，已知本地凭据匹配0；原始数据库及认证文件未列入脱敏交付。`delivery-verification.json`保存范围与结果。最后Ruff换行不改变行为；最终代码与short17的生产代码AST一致，区别仅格式。
