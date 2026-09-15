# 第二十六批开发与验证清单

基线 `6b415db2fc6b747370f434e80b24baad582425b7`。本批未自动提交，结果见 [报告](batch-26-report.md)。

## 本批已完成

- [x] 免检定实体进入明确的规划候选；已选观察目标补齐揭示操作，保留条件、范围及库存门禁。
- [x] 原正式包、同一隔离房间 Ollama 自然观察示意图成功；保留先前失败，隐藏细节没有泄漏。
- [x] 首批自定义外语、艺术／手艺、科学；职业分组、兴趣投入、重算、改名、删除、导入导出、角色快照与检定显示。
- [x] 第 25 批 OpenAI 审计按实际 provider、目的地、调用与场景证据判定，取消无条件 `blocked`。
- [x] 相关回归、Chrome 操作、前端 build/lint；更新 README 与规则来源记录。

## 清单状态更新与后续

| 原条目 | 第 26 批后状态 |
| --- | --- |
| 免检定示意图运行缺陷 | 已修复本次链路，并有一次真实自然成功；更广措辞／模组仍为验证边界 |
| 自定义技能专业 | 首批三类已开发；格斗、射击、驾驶、生存、学问等其他类别仍只支持目录 |
| 完整车卡数据 | 仍为部分完成；31 职业、103 目录 ID，加本卡自定义项；全手册职业、跨专业可选加值、90+、装备特例仍缺 |
| 新搜索修复的成功骰分支 | 确定性回归已有，本批无新自然成功骰；此次免检定发现不替代该验收 |
| 正式包 OpenAI 短测 | API 接入已开发；具体必要模组上下文／隔离房间状态／测试行动外发授权仍待取得，本批未执行 |
| A/C 全路线 | 数据已准备，完整自然验收仍待完成；历史 B 主线结论沿用 |
| 60–90 分钟长测 | 已完成的第 20／21 批结论保留，本批不重复 |
| 公网联机及远程车卡上传 | 仍待开发／评估 |
| 图像资料展示、OCR、独立 NPC Agent、语音、向量召回与重排 | 本批未开发，沿用原优先级 |

后续可按有来源的职业条目继续补充；90+ 须取得完整调整条款再实现，不外推数值。专项自然验收与“功能尚未编写”分别记账。

## 定向验证命令

从仓库根目录执行。pytest 使用**新名字**，避免覆盖原始证据或复用自动清理目录。

```powershell
$env:PYTHONPATH='backend'
$env:PYTHONUTF8='1'
New-Item -ItemType Directory -Force data/prepared/changan/batch-26
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_batch26.py backend/tests/test_batch25.py backend/tests/test_batch24_search.py backend/tests/test_batch21.py backend/tests/test_coc7_creation.py backend/tests/test_characters_api.py -q -p no:cacheprovider --basetemp=data/prepared/changan/batch-26/pytest-new
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch26_ui.py
```

浏览器脚本使用隔离数据库及临时 Chrome 配置，需端口 8000／5173 空闲；模型目录 mock，零推理。前端在 `frontend/` 下运行 `npm run build` 和 `npm run lint`。

## 本机自然发现证据

只读复核，不启动应用或调用模型：

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch26.py ollama-c1 --audit-only
```

已有完成态的续跑会保留原请求和步骤；本批已验证新增模型调用为 0：

```powershell
backend/.venv/Scripts/python.exe -X utf8 backend/scripts/check_batch26.py ollama-c1 --resume --live
```

首次需新运行名及 `--live`，新房间只做一次自然观察，不执行摘要／恢复长测。`--after-fix` 仅供已有完成但发现失败的观察，在修复后用同一房间追加独立步骤，须同时指定 `--resume --live`；它保留旧步骤，并要求房间没有检定，不能用来重掷骰。原 cycle 执行失败时使用已有 `--retry-failed` 续原 cycle。

## API 判定边界

`check_batch25_evidence.py` 的 `openai_formal_retest`：本轮 provider 非 OpenAI 为 `not_run`；OpenAI 无调用为 `not_run`，明确记录阻塞则为 `blocked`；存在调用时核对官方目的地、模型元数据和正式场景各项结果，才可为 `passed`。单纯选中 OpenAI 或成功发出请求不构成验收。审计不会授予外发权限，历史报告中的当时授权状态保留。
