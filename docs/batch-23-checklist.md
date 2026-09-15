# 第二十三批运行清单

在 Windows 仓库根目录执行，使用现有根 `.env`／进程环境凭据；不要把密钥放入命令行。需已有后端依赖、前端依赖和 Chrome。8026／5175 必须空闲，脚本会拒绝占用，不结束用户进程。

## 已有证据离线复核（零 API 调用）

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch23_evidence.py data/prepared/changan/batch-23/live-c1
```

输出 `verified-evidence.json`。它从只读 SQLite、原骰、回执及前后状态核对结果，不以“回合 completed”替代执行验收。此次合计 17 次玩家输入、45 次 OpenAI 请求，不能写成首次 9 回合无失败通过。

## 新建隔离验收

```powershell
# 仅 UI 配置、HTTP/WS、Ctrl+C、重启；不触发模型推理
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch23_api.py startup-new

# 会产生真实 OpenAI 费用；新建原创短测和本地 Ollama 切换
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch23_api.py live-new --live
```

默认 9 次玩家输入；KP 可以拒绝不合法动作或要求澄清，检查失败必须保留现场。模型优先沿用本地已保存的官方 OpenAI 型号，否则默认 `gpt-4.1-mini`；输出 `json_object`。状态查询不验证账户权限，最小试运行成功也不能替代游戏四类 schema 验证。

脚本为每个 run 隔离 DATA_DIR、DATABASE_URL、CHECKPOINT_DB_PATH、KNOWLEDGE_DB_PATH、MODEL_SETTINGS_PATH 和浏览器目录，不修改原设置。UI 切换阶段使用本地已安装 qwen3:8b；不可用时记录失败，不悄悄换成别的型号。

## 中断或失败后

```powershell
# 先定位并修复；只恢复原房间未完成回合／补缺失的检定、物品和回顾
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch23_api.py live-new --resume --live --finish

# 只补设置页生成切换，不追加游戏输入；复用已保存的成功试运行
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch23_api.py live-new --resume --switch-only

backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch23_evidence.py data/prepared/changan/batch-23/live-new
```

确认原始骰未重掷、检查数量未增加、真实物品实例／持有者／武器数量对应、回顾无副作用、摘要后还能引用原事实，正常保存／暂停／停机／载入／继续 9 项比较一致。认证或额度失败应结束本次运行并报告，不反复执行命令求成功。

## 交付核对

- [x] 凭据链路、服务隔离、环境轮换、空 key UI；原模型选择保持。
- [x] 实际 KeeperPlan／KeeperNarration／TeammateDecision／SummaryOutput；request ID、返回模型、usage、有限修复记录。
- [x] 原骰、drop／pickup 回执、重复请求无复制、库存和武器同步。
- [x] 摘要后事实回顾；两次正常恢复均 9/9。
- [x] 三段 UI 实际生成切换、生成／等待时 409、状态不变。
- [x] API 零推理启动、HTTP/WS、Ctrl+C、释放端口、重启。
- [x] lint/build 与受影响确定性回归；具体数目按报告分组，不累加。
- [x] 历史失败保留；真实 API 未触发的 SAN／疯狂／战斗／长测单独列明。
- [x] 报告、证据索引、README 链接；未 commit/push。
