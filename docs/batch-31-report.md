# 第三十一批：初始SAN归零与自主行动限制

日期：2026-09-16。基线和交付时HEAD：`39661b42d0308ad8e62eb043e727c96d0a65a765`，分支`main`。开始时无已有修改，已核对第30批报告、开发清单、理智与基础战斗说明；没有后续提交可跳过。单执行者、原分支，无worktree、commit或push。第30批两项职业特例不重复开发，正式规则保持 **1.3.0**，历史YAML不变。

原规则PDF、正式批准包、原存档及`.env`未写入，没有读取／输出环境密钥。运行材料仅在新建的`data/prepared/changan/batch-31/`（Git忽略）；HTTP自动测试使用临时SQLite和确定性夹具，禁止外部HTTP。浏览器单独数据库／检查点／模型设置，不加载`.env`，清空子进程模型密钥环境值，明确拒绝模型HTTP传输。

## 本次发现与规则边界

本轮先用正常HTTP接口补齐两项已定位P0的服务链证据。此前无数据库的轻量复现只作为线索，没有计为浏览器或HTTP通过。

1. **初始SAN0未初始化永久疯狂**：神秘学家申请初始神话99、主机核准、最终确认后，派生SAN／上限为0。原发布及提交接受链得到`sanity.kind=none, phase=none`，普通行动被接受。
2. **直接战斗入口绕过理智限制**：永久疯狂和临时／不定性疯狂的等待症状／发作阶段，普通入口阻止，`/combat/action`却可以建立行动。
3. 补充核验发现同源差异：物品入口只查发作阶段，未查永久疯狂／SAN0；队友只查标记；战斗AI排队／执行未查当前理智；SAN更正为0后当前顺序可能仍停在受限者。以上均纳入本批修复，没有扩职业或装备规则。

来源沿用[已核对理智条款](sanity-and-insanity.md)：规则书PDF131–133／印刷130–132的SAN归零及类型／阶段、发作处理。没有重新审读整本PDF。初始神话仍经本地主机许可，范围1–99，**“建议不超过10”没有改成硬上限**；初始SAN仍为`min(POW,99−神话)`。新运行时补齐不是新遭遇，不增加神话、不再扣SAN、不掷骰。主机对受限等待的固定软件策略见下文，未将它描述为所有疯狂症状的完整规则实现。

## 修复前正常接口复现

`backend/tests/test_batch31.py`在修改生产代码前运行；原日志／XML保留在`baseline-c1`、`baseline-c2`、`baseline-waits`。

| 场景 | 旧实现实际结果 |
| --- | --- |
| 神话99→核准→确认→直接发布→分配→开局→普通行动 | SAN0、上限0、none/none，`/actions` **200** |
| 同卡导出→玩家预览／提交→房间主机重新核准接受→开局→普通行动 | 同为SAN0、none/none，`/actions` **200** |
| 旧运行时SAN0／none | `/actions` **200**；随后的战斗请求409是“先完成当前事项”，不是理智限制，报告未将它算作正确拦截 |
| 正SAN、永久疯狂 | 普通行动 **409**；直接战斗 **200** |
| 临时／不定性，各自awaiting_symptom或bout | 四组均为普通行动 **409**、直接战斗 **200** |
| SAN0战斗AI | 原队列仍为该角色创建自动决策周期 |
| 受限等待 | 原API不支持固定主机处理；扩展入口契约用例失败，随后补齐受限阶段处理 |

修正导出时间戳断言后的`baseline-c2`为 **15 failed、4 passed**（35.54秒）；4项正常／潜在疯狂保护对照通过。`baseline-waits`为 **4 failed**（35.65秒）。其中装填／治疗的最初夹具有其他不满足条件的问题，后续已配置合法武器、备弹和受伤目标，并在旧实现副本重验。

补充用例使用`git archive HEAD`仅导出app、tests、scripts及pyproject到新证据目录；不切分支、不创建worktree、不替换当前源码或原数据。`baseline-extra-c2`确认旧档类型、固定枪械恢复、SAN0／永久疯狂物品与SAN0队友的 **6项失败**；另有2个旧夹具清理错误，单独保留。`baseline-extra-c3`确认合法装填／治疗等操作、SAN更正顺序和已排队AI的 **7项失败**。这些都是修复前对照，不计为通过或浏览器证据。

## 实现与恢复语义

- `rooms/autonomy.py`集中读取当前运行时：SAN0、永久疯狂、awaiting_symptom和bout受限；正SAN的underlying保持可行动；None不等于0。普通入口、队友、新战斗动作、战斗自主选择及物品入口共用判断。AI排队及真正调用模型前再次核对，避免恢复／排队期间状态改变后继续执行。
- `CharacterRuntimeV1`初始化与解析补齐SAN0的永久疯狂，覆盖`publish_character()`及提交接受共用链；既有`complete_runtime`／保存恢复事务固化派生状态。`sanity.history`留下单条`runtime_zero_san_initialized`及旧类型／阶段、说明，重复解析不重复追加房间事件。冻结卡、原存档文档、历史损失、日累计、原骰、神话增量不变。正SAN不自动解除已有永久疯狂。
- `capable()`仍只负责身体条件，避免误拦目标／顺序／伤势。战斗参与者只按slot引用当前运行时，不复制SAN。受限但身体符合原规则的角色仍可受伤或被治疗；无SAN模型NPC保持正常。新行动在模型调用、掷骰、弹药和时间效果前拒绝；旧动作相同请求先返回原回执。
- 战斗顺序跳过受限执行者但保留目标；SAN更正、恢复及调度重新核验，跨轮CON先结算。已创建但未执行的AI动作在调用模型前终止，并唤醒下一次正常调度。
- `CombatStep.resolve_restricted`仅主机、仅当前受限参与者的等待阶段、须非空原因，不接受武器或幸运数值。固定规则为：无新自主防御；停止从未掷骰的攻击／治疗；接受已有原结果；复用全局固定骰账本及当时资源回执；继续强制CON和伤害／治疗结算。记录`restricted_resolutions`与`combat.restricted_resolved`。健康真人不能使用此入口，远程真人也不能由主机用普通战斗入口代打。
- 已有本人接受原结果及强制CON确认仍可用；受限者无幸运选项，强制CON不会多出无意义的确认等待。对其他尚需主机处理的受限等待，UI显示原因与固定操作。重复请求／恢复不会重新掷骰、重复消耗或重复施加效果。
- RuntimeCards、接受预览、普通行动输入和CombatPanel同步显示原因／禁用不合法操作；服务端仍为最终依据。规则提问、房间聊天、查看日志／角色、暂停、保存保持可用。

## 本批自动测试

新增最终轮`final-tests/pytest.log/xml`：**34 passed**，298.17秒。不是第30批252项的延用计数。

覆盖直接发布与提交接受、99边界与冻结／导出稳定性、SAN0／永久／临时／不定性阶段矩阵、None及潜在疯狂对照、合法装填／主动治疗拒绝且无资源变化、受限伤员被治疗、等待攻击／防御／幸运／CON、主机权限及健康角色不可使用捷径、已掷枪械与已耗弹药跨两次恢复、旧档派生说明不重复、正SAN不解除永久、队友与已排队AI不调用模型、规则答复／聊天／查看／存档。

受影响回归选择7个文件，共 **212个不同用例通过**：

```text
tests/test_batch30.py
tests/test_batch10.py
tests/test_combat.py
tests/test_batch27.py
tests/test_characters_api.py
tests/test_action_adjudication.py
tests/test_batch19.py
```

初轮`regression-c1`为205通过、7失败（279.47秒）。修复实施期间引入的两个问题后，`regression-c2`将7个失败及3个直接受影响用例复验为 **10 passed**（54.15秒）。计数为205＋7＝212，重跑的3项不重复计数。新增34与回归212合计 **246个不同用例**；没有把前期重跑或历史批次累加进去。

前端lint／build通过，日志`static-c1/lint.log`、`build.log`。11个新增／修改Python文件的Ruff与AST解析检查通过，`git diff --check`通过，规则定义目录无改动；记录在`static-final/summary.json`、`ruff.log`和`diff-check.log`。未重跑全装备、全模组随机分支或77分钟长团。

常规复现（从backend运行；每轮用新临时／证据目录）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_batch31.py -q -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_batch30.py tests/test_batch10.py tests/test_combat.py tests/test_batch27.py tests/test_characters_api.py tests/test_action_adjudication.py tests/test_batch19.py -q -p no:cacheprovider
```

## 真实浏览器与模型调用

脚本：[check_batch31_ui.py](../backend/scripts/check_batch31_ui.py)。完整活动战斗验收目录 **ui-c3**，房间`7a16e00f-0c34-4d32-9ec0-dab2aea52b2f`。Chrome **152.0.7977.83**，两个隔离身份，本机回环5188→8028，独立SQLite与检查点。

1. 主机经正常HTTP创建神秘学家、核准神话99、确认、导出；玩家在Chrome上传导出文件、看到“初始SAN0／永久疯狂”预览并提交。主机在Chrome重新核准、接受和分配；运行时SAN0、上限0、permanent/bout、mythos_gain=0。
2. 开局后RuntimeCards清楚标记永久疯狂，普通行动发送禁用。为正常角色提供隔离伤势作为合法急救目标；SAN0玩家在战斗治疗面板不能急救或医学治疗。其浏览器携自身成员身份直接发攻击请求得到 **409**，主机冒充远程角色的普通攻击请求得到 **403**，无额外状态变化。
3. 正常调查员在Chrome发送侦查行动并确认掷骰：**原骰97、目标25、大失败**，接受原结果完成；没有为获取成功而重掷。
4. 正常角色发起隔离攻击，SAN0角色仍在合法目标／顺序中，界面不提供自主闪避／反击。主机填写原因确认固定处理，正常角色确认攻击骰：**原骰13、目标25、常规成功**；伤害骰1d3=2，受限目标HP **12→10**，无待处理残留。

`ui-c3/report.json`保留数值、拒绝结果、战斗回执；`fixture-calls.jsonl`记录 **2次确定性KP响应**，**0次真实模型调用**。截图包括`player/zero-preview.png`、`zero-runtime.png`、`zero-combat-disabled.png`、`zero-active-combat.png`和`host/combat-settled.png`，已核看SAN0预览／禁用状态及活动战斗画面。正常行动的确切原骰／结果以report.json为准。浏览器代码异常列表为空，Chrome、Vite、后端均关闭，端口释放。

`ui-c2`已通过基本限制及正常行动短链；`ui-c3`补了真实受伤目标、活动战斗与主机受限处理，不是重掷取更好结果。两轮独立材料均保留，不累加为多次自然模型或异地验收。

从仓库根目录执行，传入从未使用的新运行名：

```powershell
$env:PYTHONPATH = (Resolve-Path backend).Path
backend/.venv/Scripts/python.exe backend/scripts/check_batch31_ui.py ui-new-run
```

## 失败记录与已修正问题

- 首次普通沙箱在data及.cache新日志目录创建时被文件权限拒绝，pytest未启动。随后在明确限定的新工作区测试目录以获准执行方式运行；未更改原数据权限。
- `baseline-c1`的两条初始SAN0链先误比较了变化的`exported_at`；改为比较原角色内容。`baseline-c2`保留了真正到达发布／接受／开局／普通行动的失败结果。
- `baseline-extra`首次只导出app／tests，缺少旧夹具依赖的scripts，收集失败；新目录补齐。`baseline-extra-c2`有两个旧夹具清理错误及SQLite线程警告；不将它们算成产品缺陷复现或通过。
- `fixed-c1`：16通过、7失败。6项使用了不存在的adapter.calls属性，改查持久模型运行记录和模型客户端调用表；1项错误禁止了健康NPC对已有攻击继续防御掷骰，改为验证原攻击不重掷、合法后续结算继续。
- `fixed-c2`：30通过、1失败。将JSONL日志当作单JSON解析，改为验证正确的下载响应。
- `fixed-c3`：32通过、1失败。规则问答已完成且无模型／资源变化，断言写成`rules.answer`；改为真实事件名`rules.answered`。
- `regression-c1`：6项失败来自新增资格检查覆盖了`current`回合变量，已更名为`character`；1项来自受限强制CON结果被多停在choice阶段，已沿原流程接受强制结果。上述两个问题属于本批实施期间的回归，均在`regression-c2`复验通过，没有修改旧测试来掩盖它们。
- `ui-c1`已完成提交接受、SAN0标记和开局，但通用脚本click只找button，无法点击details的summary。修正DOM点击选择器后`ui-c2`通过，原失败截图与页面文本保留。
- 最终静态检查全部完成后，附带的状态命令使用`core.excludesfile=NUL`被Git拒绝（退出128）；随后普通`git status --short`成功（退出0），核对仅本批预期修改与新增文件。该命令错误不涉及代码、测试或仓库配置写入。

## 后续顺序与待验范围

两项P0已闭环。下一功能批推进P1经历包：先核对手册PDF34–35／印刷61–62，优先战场、警务、罪犯、医务四种非神话包的资格、额外点池、初始SAN与原文限定免疫，复用导入／本地主机许可／冻结链。神话经历包和法术单列；动物特例、专业替换、战斗扩展分别排期。

真实异地、A/C完整自然路线、新搜索自然成功骰、正式包OpenAI复验仍单列待验；本批没有执行或用确定性KP替代。90+等历史缺口继续保留。这些不作为本批修复或下一功能批的前置。
