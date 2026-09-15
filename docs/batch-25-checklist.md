# 第二十五批运行清单

从仓库根目录运行；保留已有目录、原骰、原请求及失败证据。不自动提交。

## 本机 Ollama

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONUTF8='1'
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch25.py ollama-c1 --resume --live --provider ollama
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch25_evidence.py ollama-c1
```

已有运行用 `--resume`，第一次执行使用新名称并省略该参数。不要新建房间重掷已有检定。若原 cycle 失败，先核对该步原始记录，修复原因后加 `--retry-failed` 沿原 cycle 重试。等待检定和中断的日志按原 `client_request_id`、check ID 继续。已完成阶段必须有对应的提交事件与 KP 实际回应。

恢复后继续阶段要求实际移动到相邻车厢的计划、转场事件和回应；普通回顾不计为继续。当前运行的最终证据为 `step-continue_move.json`。转场会给历史摘要正文附加导航 JSON 包装，审计核对同一摘要 ID、原正文和存档；不把导航包装当成正文丢失。

摘要按实际覆盖序号推进，每次最多补齐六个窗口。若需要修复并重新验收摘要，使用 `--resume --live --refresh-summary`：先归档上一轮摘要、恢复和回应证据，再只重做这些阶段，保留原调查骰、揭示和拿取回执。原始文字摘要与从公开状态生成的核对段分别保留。

模型由现有 `Settings`／`ModelSettings` 选择；可显式给出 `--model`、`--base-url`、`--output-mode`。续跑必须保持已记录配置。Ollama 只允许本机回环地址；正常应用继续执行自己的地址校验、配置服务与调用预算。原生 Agent 传输实际是 `/api/chat` 和 JSON Schema，保存的输出模式与实际传输方式分别记录。

查看 `data/prepared/changan/batch-25/ollama-c1/`：

- `result.json`：配置、阶段、调用起点、失败历史及恢复状态。
- `step-*.json`：原输入／请求、cycle、check、roll ID、事件范围、状态和工具回执。
- `before-roll-*.json`／`roll-receipt-*.json`：骰前绑定、批准动作与实际骰回执。
- `public-entities.json`／`player-inventory.json`：玩家权限下的调查板和库存。
- `summary.json`／`restore.json`：实际摘要、正常停机载入与九项比较。
- `verified-evidence.json`：逐项 `passed / failed / not_run / blocked`，没有发生的骰分支保持未覆盖。

只读审计不启动模型、不修复状态、不重掷骰；验收未通过时退出码为 2。运行器正常退出本身不构成通过。`session.json` 为本地测试凭据，不纳入 Git。

## 定向回归

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_batch25.py backend/tests/test_batch24_search.py -q -p no:cacheprovider --basetemp=data/prepared/changan/batch-25/pytest-new-review
```

使用新的 `--basetemp` 名称，保留每轮测试结果。没有修改前端或导入服务，不需为本批重复浏览器导入、前端 lint/build 或整套导入回归。

## 正式包 OpenAI 复验

本批没有取得具体外发授权，也没有执行该复验。须先获得用户明确允许把**必要模组上下文、隔离房间状态、测试行动**发送至 **`https://api.openai.com/v1/`**，才可使用：

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch25.py openai-continuation --live --provider openai --source-batch24 --authorize-openai-formal-context
```

该入口核对并沿用 `live-c4` 原房间 `d11069af-bc06-4c80-836f-4497be6eb0b8`、准备版本 `1b38f4b7-3287-457e-9ae6-f906906cf1f4`，在第 25 批目录保存续跑前数据库快照和调用边界。后续继续须同时保留 `--resume --source-batch24`。参数不能替代用户授权。

具体结果及未覆盖范围见[第 25 批报告](batch-25-report.md)。
