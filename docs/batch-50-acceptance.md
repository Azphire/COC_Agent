# 第50批冻结与真实验收操作记录

基线实际检出为 `main` / `42401ab511e8823e8a0082b5e0ca91385f660648`；用户计划将该基线标注为 `repair`。没有切分支、commit 或 push。

## 原失败冻结

`backend/tests/fixtures/batch50/` 保存第49批 real-09、real-10 的原 KP run/context、各两次完整模型调用 document 及正式回退事件。原输出、覆盖字段和实际调用审计均未改写，未从旧 coverage 生成任何新正文。`manifest.json` 分别记录源文件、冻结文件、context 和每个调用 document 的 SHA256。

| 原文件 | 原文件 SHA256 |
| --- | --- |
| `data/prepared/batch-49/real-09/delegate-case.json` | `1de3e6bf63a1f55cb22a97889ea5468d8a96656d4d9b63a5117822dd96cac3eb` |
| `data/prepared/batch-49/real-10/delegate-case.json` | `5c79ea046ce7a6a9eb7bf8c6c5fe7b8b712113e09b67354f509090e4f9b207cb` |

冻结工具为 `backend/scripts/freeze_batch50_failures.py`，默认拒绝覆盖已冻结目录。首次普通沙箱创建目录被 Windows 权限拒绝，导致随后 8 项测试缺 fixture 的 setup error；允许的提权执行完成冻结后，同一测试 8 passed / 0.20 秒，0 模型调用。测试直接对原稿运行 coverage 语义检查，证明原稿未知需求没有被实际正文回答；新契约的结构拒绝另行验证，不能把缺 `answer_parts` 当作语义拒绝的全部证据。

主线新契约接入后，旧第49批当前结果覆盖测试与冻结组合初次 9 failed / 35 passed：失败均为旧生成字段或旧提示假设。将原正确 prose 用例保留在 `KeeperNarration` 存储/API 格式上验证，wire 改检查 `answer_parts` 与服务端绑定，未把旧 coverage 搬成新片段。迁移后同组 44 passed / 2.66 秒。观察汇总器与冻结组 16 passed / 0.44 秒；两组有 8 项重叠，不相加计唯一总数。上述改动脚本及测试 Ruff 通过。

## 真实复验入口

真实模型运行须在受影响离线回归通过后启动，本说明完成时尚未提交真实回合。仅执行原委派，原请求逐字校验为：

> 林修远·猎人，请用手检查门上便签正面的边缘，看看纸张有没有夹层或折叠，再把实际发现告诉大家。

从 `backend/` 工作目录使用 `.venv/Scripts/python.exe -X utf8`：

1. `scripts/validate_batch50_delegate.py --directory ../data/prepared/batch-50/real-01 --prepare`
2. 后台启动相同命令的 `--serve`，后端端口 `8150`；Windows `Start-Process` 必须带 `-WindowStyle Hidden`，或使用已带 `CREATE_NO_WINDOW` 的 subprocess。
3. `scripts/validate_batch50_delegate.py --directory ../data/prepared/batch-50/real-01 --setup`
4. 后台启动 `scripts/observe_batch50_browser.py ../data/prepared/batch-50/real-01`，前端端口 `5250`。它创建两个真实 Chrome 玩家窗口，在 `browser-observer/browser-observer-ready.json` 落盘后才允许下一步。
5. `scripts/validate_batch50_delegate.py --directory ../data/prepared/batch-50/real-01 --case`
6. 等正式落盘与双窗 DOM 完成后创建该运行目录的 `browser-observer-stop`；观察脚本保存截图并退出自己启动的 Chrome/Vite。停止隔离后端，再执行 `--export` 核对停止后的源保护。
7. `scripts/summarize_batch50_browser.py ../data/prepared/batch-50/real-01`

准备仍复用第49批正常 `mode=ro` SQLite backup → HTTP 新建房间／挂批准准备／旧卡与档案／正常 start 路径。新入口只提交 `delegate`，不暴露 `read` 选项。准备和开场不生成新卡、不重备团；旧重叠姓名、3 张完整旧卡与原人格、批准准备、来源实体快照均由预检逐项比较。提交前重新确认便签背面仍为 hidden。

有效配置必须逐键等于第49批 real-10：`ollama/qwen3:8b`、窗口 `16384`、输出上限 `900`、`agent_max_calls=12`、`agent_context_chars=12000`、超时 `120s`。脚本不改变一次修复实现预算。源库/WAL、旧准备包、模型文件的保护哈希和复制卡内容继续由原独立 integrity 方法核对。

## 本次证据分层

- `delegate-case.json` / `delegate-frames.json`：实际原请求、当前父子 cycle、完整实际调用、正式事件与客户端帧。
- `model-originals.json`：仅本次 KP 调用原模型 document，包括实际发送契约、生成稿、服务端审计和时间；不以派生正文替换原稿。
- `derived-formal-bodies.json`：本次正式事件的正文与 origin，失败回退照实保留。
- `server-narration-audit.json`：本次 KP run、验证及结果事件、任务状态；不得由片段或临时流提前结算。
- `memory-and-receipts.json`：只读导出本房间记忆、工具回执、揭示状态，供正文／事件／历史一致性核验。
- `batch50-metrics.json` / `batch50-source-integrity.json`：仅本题父子调用和 usage、逐次耗时、源保护与完整旧卡／HO保留核对。
- `browser-observer/*/observation.json`：两个真实玩家投影逐字正文 DOM、当前 stream/attempt 帧及断线时间。
- `browser-observer/batch50-browser-summary.json`：同一个最终成功 cycle/stream/attempt 的严格合取，不能借失败稿或之前读便签流。

汇总要求服务端首个已验证片段和首 DOM 均早于该次模型完成、至少两次实际 delta、DOM 后续只追加、第二窗在模型完成前主动重连且获得 responding 快照、最终每窗只有一个正式气泡、正文节点与 history 逐字相等且无剩余草稿。只收到终稿后切帧、没有 `model_finished_at`、回退、不同 attempt、仅一次文本或晚重连均不通过。独立 8 项汇总回归覆盖这些误报边界。

首次真实失败必须先检查实际 output_contract 是否含新结构及逐项语义错误；保留全部原稿和失败流。只有明确代码改动及同题离线复现通过后才使用新的 real-NN 目录复验。相同代码无新原因时停止；不自动重采样、不换原题、人物或模型配置。

## 定向回归边界

主线应包含新增 batch50 片段／修复／增量解码组、第49批当前结果与自然覆盖／读便签／原请求绑定／结果结算，以及第46批 coverage、normalization、wire、runtime、第43批 narration stream 与 model stream、第47批 coverage 与任务旧模式保护。附修仅装备并列与 HO 扩充／恢复边界。不要为本批重跑完整随机卡矩阵、备团或全部开团流程。

## 唯一一次真实复验结果

离线通过后执行了 `real-01`，只提交原委派一次；新契约实际送达，但两次无来源项目分别输出“没有发现纸张有夹层或折叠。”和“目前尚未发现纸张有夹层或折叠。”，均被拒。有效背面结果被独立保留，修复仅请求错误项；没有明确代码原因，不再采样。完整结果见[本次双窗报告](../data/prepared/batch-50/real-01/browser-observer/report.md)。

本次 6 调用，27974 input／1588 output，调用 latency 合计108.170秒，整体墙钟261.854秒；正式失败 e76 落盘249.861秒，两窗首正式 DOM250.644／249.971秒。首验证片段未产生；两稿各0delta及interrupt，0成功end／生成中重连。两窗最终各一个回退气泡且与history逐字一致，这一发布一致性不计成功流式。observer、Vite、两个Chrome已停止。

本次新协议原稿、完整context和调用审计另冻结于 `fixtures/batch50/real-01-current-failure.json`，源及各子树SHA256在 `real-01-current-manifest.json`。新增3项真实失败回归证明两稿仍拒、唯一有效结果保留且正式答复仍不完整；与汇总8项合跑11 passed／0.61秒。无新增模型调用。

最终真实结果和全部最终回归由 [batch-50-report.md](batch-50-report.md) 记录，本页保留冻结依据、工具判定、运行前检查和本次独立验收。
