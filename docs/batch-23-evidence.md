# 第二十三批证据索引

以下路径均相对 `data/prepared/changan/batch-23/`，为本机忽略目录。仓库只提交说明与脚本；原始会话、完整调试上下文不属于可公开附件。

| 路径 | 用途／判读 |
| --- | --- |
| `live-c1/verified-evidence.json` | 最终只读审计；17 次玩家输入、45 次 API 请求、205351 tokens、schema、原骰、回执、回顾及恢复 |
| `live-c1/game.db` | 原始事件和 18 个已完成 cycle；使用只读 SQLite 查看，不改状态 |
| `live-c1/model-calls.json` | 43 次游戏／摘要调用的 schema、attempt、request_id、response_model、耗时、usage 及私有输入上下文 |
| `live-c1/agent-debug.json` | 计划、校验、工具回执、队友和摘要的完整主机审计 |
| `live-c1/all-checks.json` | 唯一侦查检定、原骰、settlement、最终 10/60 及不可用的幸运／孤注选项 |
| `live-c1/initial-room.json`、`final-room.json` | 起始与最终角色资源、库存、实例、武器、场景等 |
| `live-c1/turn-12.json` | 实际放下，检查 after 中 dropped_items、inventory 和 weapons |
| `live-c1/turn-13.json` | 同一实例实际拾回；不是首次取得新实例 |
| `live-c1/turn-14.json`、`recovery-4.json` | 重复拾取的上下文失败及正常恢复，不产生新交互 |
| `live-c1/turn-15.json`、`turn-16.json` | 只回顾；后者在摘要／保存／停机／正常载入之后 |
| `live-c1/restore-comparison*.json` | 两次正常恢复的 9 项状态比较 |
| `live-c1/trial-openai-minimal.json`、`trial-openai-switch.json` | 两次额外 OpenAI 试运行，合计 1538 tokens；后者通过实际设置页按钮 |
| `live-c1/trial-ollama-before.json`、`trial-ollama-after.json` | 两份成功本地生成结果，不计入 OpenAI 请求数 |
| `live-c1/settings-verified.png` | 实际设置页：环境来源、空密钥框、官方地址、型号、JSON 模式 |
| `live-c1/result.json` | 累积启动、试运行、切换 409、恢复及回合记录；旧 pending_review 字段以最终 verified-evidence 为准 |
| `live-c1/result-before-resume-*.json`、`before-resume-*-*.json` | 修复前现场，禁止覆盖为“通过”；包括初轮无检定、超限和本地切换运行器竞态 |
| `live-c1/launcher-*.log` | 各次 start.cmd／launch.py 控制台输出；仅第 3–11 次有完整停止标记 |
| `startup-c1/` | 沙箱 Chrome 失败候选，不算成功 |
| `startup-c2/result.json`、`launcher-1.log`、`launcher-2.log` | 原生 Windows 两次 API 配置启动、HTTP/WS、Ctrl+C、释放端口、exit 0；调用表均 0 |
| `baseline-models.xml` | 原模型入口基线 42 passed |
| `models-final.xml`、`models-review.xml` | 凭据及模型入口先后 56／57 passed；后者包含端口 0 边界，轮次不累加 |
| `final-regression.xml` | 综合受影响确定性回归；代码最后的裁定变化另在 affected-final 复验 |
| `affected-final.xml` | 最后裁定／压缩／物品修复后的回归 |
| `compound-recovery.xml` | 对抗／联合检定与单次扣点、原骰恢复，工具和旁白重试、摘要恢复 |
| `baseline-runtime.xml`、`wait60.xml` | 基线 15 秒等待超时对照及 60 秒等待的同例通过 |
| `credentials-models.xml`、`directed.xml`、`runtime-native.xml` | 开发中失败与环境等待问题记录，不计为最终通过 |
| `frontend-checks.json`、`frontend-lint.log`、`frontend-build.log` | 前端验证退出码与输出 |
| `static-checks.json` | 最终受影响文件 ruff 与仓库默认换行设置下 `git diff --check` 均 exit 0 |
| `secret-audit.json` | 25 个交付文件、93 个证据／日志文件密钥扫描通过；原 provider/model 仍为 Ollama qwen3:8b |
| `cleanup.json` | 本批 Chrome／pytest 进程结束，8026／5175 无监听 |
| `final-source-manifest.json` | 交付源码及文档的 SHA256 清单，用于定位这次未提交工作区版本 |

## 事件定位

房间 `80080d70-9875-4099-8459-782b72cafa3b`；事件 seq 来自该房间数据库，不是全局序号。

| seq | 证据 |
| ---: | --- |
| 37 / 64 / 93 | 公开观察／NPC 实际台词／队友行动提案 |
| 275 / 292 / 293 | 新调查原话／唯一原骰固定／唯一结算 10/60 |
| 361 / 378 | 自己持有的小刀放下原话／drop 回执 |
| 390 / 407 | 同一实例拾回原话／pickup 回执 |
| 419 | 重复拾取请求；之后无第三条物品交互回执 |
| 465 / 497 | 检定与持有事实回顾；后者为正常恢复后 |
| 305 / 332 / 387 / 416 / 469 / 472 / 501 | 七条实际摘要重建，coverage 不等于全部历史 |

原骰 ID `f6af368e-21e3-45e1-80c1-61168583a319`；检定 ID `ff17e14c-d793-43c6-b22f-bed6c4eae700`；周衡小刀实例 `320529cd-ff2e-504a-ad1f-29f7fe871481`。

`session.json`、`host-model-settings.json`、浏览器 profile 和任何原始请求内容仅本地保留，不复制到报告或公开附件。API 密钥扫描只记录通过／失败，不展示密钥或其片段。
