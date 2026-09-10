# 第十一批报告：SAN 自动触发、幸运消耗与孤注一掷

执行日期：2026-09-10。根目录执行时 HEAD 为 `167c62cbfb0bcd7e4912f68d641fa16e7ed5efe8`，与实际 pull 后指定基线一致；`main` 分支，开始工作树干净。已核对第十批报告，复用其 SAN、疯狂、资源现值与恢复机制。单执行者，没有 worktree、commit 或 push；没有下载模型或调用外部生成 API。

## 完成内容

- 从本地1907版第七版规则书核对幸运可选规则、资格／成功等级／排除项，以及孤注额外努力、一次限制和失败后果。PDF／印刷页对应关系、来源 hash 和接口见 [规则与接口说明](check-settlement-and-encounters.md)。
- SAN 在行动最终生效和必要实体揭示之后，由服务端匹配已批准效果。显式目标经过行动裁决；自然语言只自动采用当前场景可见范围中的唯一标题匹配。多个对象、文字条件或多候选效果进入主机核实。回顾、规则提问、等待、单纯重试和已有遭遇不会新建 SAN。公开实体不会自动影响全员，冻结实际成员与角色席位。
- 复用 cycle、CheckRecord、房间事务及 interrupt，顺序为普通检定最终结算→必要揭示→串行 SAN→叙事。每次一个等待项，保留原四项模型工具上限。模型 SAN 工具只记录提案，自动／模型／主机入口共用遭遇回执。
- 普通检定有合法后处理选项时保留原骰，`pending/result=null` 等待本人选择。幸运默认关闭，主机显式启用；服务端枚举花费及结果，玩家选择后原子扣除。原骰、花费和最终结果分别保存；重复点击不重复扣点。远程真人自己选择，主机只可代管本地真人或 AI；AI 默认接受原结果，没有增加选择模型调用。
- 孤注绑定原 check ID：玩家说明额外努力，主机核准资格并在掷骰前确认更严重后果，玩家确认后由服务端掷唯一一次后续骰。保留原失败，不能花幸运改孤注结果、再次孤注或换普通行动／技能绕过。状态与时间后果由服务端执行；伤害等尚未实现后果保持等待主机处理，记录实际处理依据后才完成。
- 扩展检定卡、主机审阅、Luck／SAN 现值和来源展示。存档恢复待选择、已扣 Luck、待核准／待孤注骰、待后果和后续 SAN；资源随存档回退，原／孤注／SAN 已掷骰点固定。沿用第十批公开事实单调语义，`story_v1` 排除被读档放弃的剧情；叙事仅使用最终裁决。

## 验证

最终完整 pytest：**605 passed、1 skipped、1 warning，2181.77 秒**，实际退出码 **0**，见 [完整日志](../.cache/batch-11/final-pytest-5.txt) 和 [退出码](../.cache/batch-11/pytest-result.json)。跳过项为 Windows 符号链接权限不可用，warning 为既有 Starlette／AnyIO 弃用提示。第十批 **561 passed／1 skipped** 仅为历史结果，没有借用。

本批及受影响行动断言的定向回归：**41 passed、1 warning，312.93 秒**，见 [定向日志](../.cache/batch-11/pytest-boundaries.txt)。随后旧档兼容补充验证 **1 passed，3.00 秒**，见 [旧档日志](../.cache/batch-11/pytest-legacy-save.txt)：移除旧档中不存在的新字段，使用旧版 `check.resolved` 原骰事件恢复，没有再次调用随机数。覆盖：

| 范围 | Fake／服务端验收 |
| --- | --- |
| 自动 SAN | 模型省略工具、误提多余普通检定、显式／自然语言目标、调查揭示后自动 SAN、回顾／规则／等待不触发、歧义交主机、旧信息不再触发、主机／自动回执合并、首次配置拒绝重复。 |
| 串行顺序 | 普通原失败不揭示；花 Luck 买通过后才揭示并创建 SAN；SAN 等待及选择阶段读档后保持队列。回到原选择改为接受失败时不根据旧公开揭示再扣 SAN。 |
| 幸运 | 当前 Luck 为未定义／0／边界、1:1 花费、普通／困难／极难等级、大成功／大失败及排除项、非法整数、越权、重复提交、扣点／资源回退一致性。 |
| 孤注 | 额外尝试必填、未经批准不得掷、远程权限、拒绝、成功、状态／时间／主机处理失败后果、重复掷骰、禁止幸运和二次孤注、改变重复指纹不能绕过。 |
| 恢复 | 四个普通等待阶段分别关闭并重建 TestClient／应用／数据库连接；恢复固定原骰。另覆盖待选择／已扣 Luck／待孤注审批和骰／待后果／待 SAN／主机遭遇确认的资源与回执恢复。第十批 SAN／疯狂恢复回归继续运行。 |

Ruff、前端 lint 和 TypeScript／Vite build 通过，构建 **46 个模块**。日志：[Ruff](../.cache/batch-11/final-ruff.txt)、[lint](../.cache/batch-11/final-frontend-lint.txt)、[build](../.cache/batch-11/final-frontend-build-2.txt)、[构建退出码](../.cache/batch-11/frontend-build-result.json)。构建产物隔离在 `.cache/batch-11/frontend-dist/`；首次构建成功产出，但 Vite 的 outDir 提示被 PowerShell 当作 stderr 错误显示，随后核对目标目录并明确使用 `--emptyOutDir`，实际退出码 0。

最终命令均从仓库根目录执行：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests -v --basetemp=.cache/batch-11/final-pytest-5 -o cache_dir=D:/Codes/COC_Agent/.cache/batch-11/pytest-cache
backend/.venv/Scripts/ruff.exe check backend
npm.cmd --prefix frontend run lint
npm.cmd --prefix frontend run build -- --outDir ../.cache/batch-11/frontend-dist --emptyOutDir
git diff --check
```

早期失败日志全部保留在 `.cache/batch-11/`。开发中修复了新选择操作没有唤醒原 cycle、恢复已结算检查后仍残留等待状态、揭示事件实际使用 `id` 字段、主机拒绝遭遇后的叙事续行等问题；测试夹具也改为遵守开始前批准准备、当前场景可见范围及最终结果选择。第一次完整回归为 **593 passed、1 failed、1 skipped，1512.68 秒**，见 [原日志](../.cache/batch-11/final-pytest.txt)。失败的旧断言把富展示结果与原始接口结果整体比较，现改为比较最终裁决字段及固定骰点，并已定向通过；没有降低权限、放宽行动阈值或修改真实随机数。

收尾补充了孤注后果容量验证：角色状态已满或游戏时间将超限时，在取随机数之前拒绝掷骰，避免失败后事务回滚导致重复点击重新取骰。新增两项边界测试 **2 passed，21.20 秒**，见 [日志](../.cache/batch-11/pytest-push-capacity.txt)。另覆盖“Luck 已扣除、图尚未继续”的即时存档时点：普通续算 **1 passed，12.97 秒**，继续揭示／SAN 并再次载入复用原骰 **1 passed，46.18 秒**，见 [扣点时点](../.cache/batch-11/pytest-paid-window.txt)、[串联恢复](../.cache/batch-11/pytest-paid-san-chain.txt)。第二、三次全量在后续边界修正时主动停止，部分日志不计为完整通过。

第四次完整运行结果为 **603 passed、2 failed、1 skipped，2101.04 秒**。两项均为测试助手的 15 秒等待超时，分别停在正常的 `generate_keeper_narration` 和 `discover_encounters`，没有运行时错误；相关三项随后单独重跑 **3 passed，113.94 秒**。见 [完整失败栈](../.cache/batch-11/final-pytest-4.txt) 与 [定向复验](../.cache/batch-11/pytest-auto-regression.txt)。仅给本批串联测试设置 30 秒等待余量，仍要求任务结束及原有状态／资源／权限断言成立；旧测试默认值和生产超时均未更改。最终采用第五次完整日志。

## 本机短会话

沿用已有本机 Ollama `qwen3:8b`，串行调用，context 8192、output 900、think=false，单请求超时 120 秒。复用第十批验收助手，新增 [check_batch11.py](../backend/scripts/check_batch11.py)；原助手保留默认第十批行为，只增加批次隔离参数和完成阶段等待。真实数据库、准备／知识库副本和浏览器资料均位于 `.cache/batch-11/`。

使用原《常暗之厢》Word 第10页 Clicker 的 SAN 1/1d6，来源 SHA-256 `a72e88d5a4cb08831bb4ab6719094447b6c995932b4760945a1abb871cad57c8`。这是主机选定的 **2号车厢局部起点**，已确认光源、此前见过7号车厢尸体；没有声称从开场连续跑到这里或完成全模组准备。声音细节检定是主机为本批交互验收批准的条件。

| 实际步骤 | 原骰与最终裁决 | 资源／触发 |
| --- | --- | --- |
| 普通聆听 | 原骰 **86**，目标 **20**；待选择存档后停止后端、重启载入，玩家选择花 **1** 点 Luck，最终 **85，失败**。重复点击同一选择成功返回原回执。 | Luck **50→49**，仅 1 个扣点事件；没有提前执行听清细节的成功后果。已扣点存档载入仍为 49。 |
| 首次 Clicker 行动（修复前） | 模型误提多余侦查，服务端拒绝；当时自动发现连带被跳过。 | 未创建 SAN、未扣点；该次不计为自动触发成功，原日志保留。 |
| 同一房间修复后的 Clicker 行动 | 行动 **#92** 有效目睹，系统匹配批准 `corpse-san`，`origin=automatic`；模型仍没有提出 SAN 工具。待 SAN 存档后再次停止后端、重启载入。实际 SAN **76／50，失败**，损失骰 **1d6=4**。 | SAN **50→46**，本日累计 4，无疯狂。只有实际行动者受影响。**没有主机补发 SAN 请求**。 |

同一房间保留原角色、原普通骰和已扣 Luck 继续验证，没有重建或重掷去挑结果。主机介入是事先批准配置／可见外形、启用可选规则、存读档与恢复；验收助手禁止调用 `/sanity/encounters`。普通检定与 SAN 由本人权限接口确认，主机和玩家最终界面都显示 Luck 49、SAN 46。

实际生成调用总计 **8 次**：准备批准前失败副本 0；声音前置副本 1；最终保留房间 7（普通聆听 2、修复前 Clicker 2、成功自动 SAN 回合 3，最后一轮含一次叙事修复）。逐条调用见 [model-calls.json](../.cache/batch-11/model-calls.json)。末次叙事采用确定结算文本、`needs_host_ruling=true`；自动 SAN 与资源结算已完成，未将其计为流畅的模型叙事。没有新增 AI 选择调用或多模型测试。

真实会话没有幸运买成功、孤注成败或疯狂，相关分支仅计 Fake 验证。伤害、战斗、对抗／组合检定、全模组准备仍留后续批次。

## 交付与保护

- [PUBLIC 可读会话](../.cache/batch-11/常暗之厢-session.md)：从真实玩家权限日志应用 `story_v1`，保留实际原／最终结果。
- [HOST_DEBUG 会话与调用／介入／存档记录](../.cache/batch-11/常暗之厢-HOST_DEBUG.md)，内含原始双权限日志、事件、模型运行、报告及双端截图链接。
- [规则与接口说明](check-settlement-and-encounters.md)，同步更新 README、SAN 说明及相关历史报告的后续状态说明。
- [保护核对](../.cache/batch-11/protection-result.json)：**66 个受保护文件前后 SHA-256 完全一致**，包括原资料、`.env`、用户库和既有准备库。
- [服务回收](../.cache/batch-11/services-closed.json)：验收后端、前端和浏览器已停止，8000／5173 可独占绑定。修改留在当前工作树，未 commit／push。

`git diff --check` 退出码 **0**，见 [退出码记录](../.cache/batch-11/diff-check-result.json)；Git 的 CRLF 转换提示不计作差异错误。
