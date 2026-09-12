# 第十五批 HOST_DEBUG 实际验收记录

本文件包含 NPC 隐藏数值，仅供主机／开发复核。正文从隔离 HTTP 服务事件和模型调用导出生成；不是 Fake 测试。原始事件、规划、检查点、错误日志留在 `.cache/batch-15-combat/`，没有复制访问令牌或 `.env`。

## 环境与数量

原有 Ollama `qwen3:8b`，上下文8192，输出上限900；端口8016，独立数据库。13次玩家自由输入，57次记录在案的模型调用，累计模型延迟258.714秒（不是会话墙钟时间）。首轮输入2026-09-11T23:40:15.419988+00:00，末轮输入2026-09-12T00:33:34.352414+00:00。

原创维修工坊经正常模组绑定与角色 API 创建；守卫数值由主机在掷骰前明确配置。没有写原模组、规则、用户库或模型配置。

| 调用契约 | 数量 |
| --- | ---: |
| ArgumentRepair | 1 |
| CombatDecision | 11 |
| CombatNarration | 10 |
| ExtendedDecision | 11 |
| KeeperNarration | 7 |
| KeeperPlan | 11 |
| SummaryOutput | 3 |
| TeammateDecision | 3 |

## 复合检定实际骰

### #124

```json
{
  "id": "9a644869-60e5-4834-9cd6-66c8546a6c9a",
  "name": "周岚·力量 / 沈砚·力量",
  "result": {
    "total": 20,
    "threshold": 60,
    "level": "hard",
    "passed": true,
    "outcome": "success",
    "winner": 0,
    "winner_id": "4396a2d6-41b8-42dc-8aec-0d0f537f4747",
    "both_failed": false,
    "comparison": "level",
    "side_results": [
      {
        "total": 20,
        "threshold": 60,
        "level": "hard",
        "passed": true,
        "outcome": "success"
      },
      {
        "total": 46,
        "threshold": 60,
        "level": "regular",
        "passed": true,
        "outcome": "success"
      }
    ],
    "display_text": "非战斗对抗：周岚·力量 / 沈砚·力量。周岚在对抗中获胜。"
  }
}
```

### #167

```json
{
  "id": "25212f5e-c528-46de-b6e1-b3adef53d6e7",
  "name": "周岚·侦查 / 沈砚·聆听",
  "result": {
    "total": 77,
    "threshold": 60,
    "level": "failure",
    "passed": true,
    "outcome": "success",
    "winner": 0,
    "winner_id": "4396a2d6-41b8-42dc-8aec-0d0f537f4747",
    "both_failed": true,
    "comparison": "value",
    "side_results": [
      {
        "total": 77,
        "threshold": 60,
        "level": "failure",
        "passed": false,
        "outcome": "failure"
      },
      {
        "total": 74,
        "threshold": 55,
        "level": "failure",
        "passed": false,
        "outcome": "failure"
      }
    ],
    "display_text": "非战斗对抗：周岚·侦查 / 沈砚·聆听。周岚在对抗中获胜。双方单项检定均未成功；非战斗对抗仍按等级、数值比较。"
  }
}
```

### #285

```json
{
  "id": "39366862-b6fb-44c4-8fda-496779994023",
  "name": "侦查 + 聆听",
  "result": {
    "total": 63,
    "threshold": 60,
    "level": "failure",
    "outcome": "failure",
    "passed": false,
    "components": [
      {
        "name": "spot_hidden",
        "display_name": "侦查",
        "value": 60,
        "difficulty": "regular",
        "total": 63,
        "threshold": 60,
        "level": "failure",
        "passed": false,
        "outcome": "failure"
      },
      {
        "name": "listen",
        "display_name": "聆听",
        "value": 55,
        "difficulty": "regular",
        "total": 63,
        "threshold": 55,
        "level": "failure",
        "passed": false,
        "outcome": "failure"
      }
    ],
    "requirement": "all",
    "partial_success": false,
    "difficulty": "regular",
    "bonus_dice": 0,
    "penalty_dice": 0,
    "display_text": "侦查 + 聆听检定（全部成功）：骰点 63，目标 60，失败，未通过。"
  }
}
```

## 战斗完整结算摘要

表中原骰包含奖励／惩罚选骰结果；原始十位候选保留在括号中。DB0的常数记录不计为随机骰。守卫HP8、护甲1、DEX70、CON50、斗殴45；调查员HP12、DEX60、斗殴25、闪避30、DB0。数值在第一次战斗掷骰前固定。

| 事件 | 行动者／目标 | 攻击原骰 | 防御原骰 | 最终结果／资源 |
| --- | --- | --- | --- | --- |
| #322 | 守卫 → 周岚 | 77 failure（[77]） | 37 failure（[37]） | 此次攻击未造成伤害。 |
| #337 | 周岚 → 守卫 | 61 failure（[61]） | 72 failure（[72]） | 此次攻击未造成伤害。 |
| #349 | 沈砚 → 守卫 | 2 extreme（[52, 2]） | 4 extreme（[4]） | 沈砚命中守卫。 原伤害3−护甲1＝2；守卫 HP 8→6。 |
| #367 | 守卫 → 周岚 | 74 failure（[74]） | 11 hard（[11]） | 周岚命中守卫。 原伤害1−护甲1＝0；守卫 HP 6→6。 |
| #383 | 周岚 → 守卫 | 53 failure（[53]） | 30 regular（[30]） | 守卫命中周岚。 原伤害2−护甲0＝2；周岚 HP 12→10。 |
| #395 | 沈砚 → 守卫 | 51 failure（[51, 51]） | 94 failure（[94]） | 此次攻击未造成伤害。 |
| #412 | 守卫 → 周岚 | 88 failure（[88]） | 61 failure（[61]） | 此次攻击未造成伤害。 |
| #422 | 周岚 → 守卫 | — | — | 周岚脱离战斗。 |
| #432 | 沈砚 → 守卫 | — | — | 沈砚结束本次行动。 |

## 存读档与重复请求

等待守卫第一次攻击的真人防御时存档，停止本批后端进程，重新启动同一隔离库，加载该存档并恢复。下列相等判断直接比较保存的两个房间响应。最终收场后另存了一次档。

```json
{
  "pending_equal": true,
  "combat_equal": true,
  "action_id": "bdf36b1b-a8eb-4b2d-b948-35dfcac4c27d",
  "stage": "defense",
  "waiting_snapshot": "403d1bda-1c77-4ae0-b1d0-45a1196e4e70",
  "damage_replay_status": "200",
  "damage_replay_unchanged": true,
  "final_snapshot": "67ae3d26-0472-47f7-8912-7f559dadfc65",
  "model_calls": 57,
  "model_latency_ms": 258714,
  "input_count": 13,
  "first_input_at": "2026-09-11T23:40:15.419988+00:00",
  "last_input_at": "2026-09-12T00:33:34.352414+00:00",
  "final_combat_active": false
}
```

重复请求是同一个 `ad3d0d74-cbe6-4415-a387-3c6c7138e480` 的 `attack_roll/roll`，HTTP200，周岚HP10、全部参与者伤势和武器资源前后相同。原攻击已经结算出2点伤害，没有重掷或再次扣血。

## 开发失败及主机例外

- #43 及后续尝试出现扩展契约缺字段、错误技能、无检定；逐次读取真实规划后收紧候选与必填原话，未伪造提案。#124 对抗结果固定后叙事曾失败，重试保留20／46原骰。
- #146 错用侦查对聆听的双方对抗，因AI已掷不能撤销；完成并保留77／74的错误事项，没有作为正确组合验收。#210 的普通侦查卡在未掷骰前取消。#181／233 仍漏判。#260 修正必填行动引用后重试同事项，#285 正确共享63，原结果被接受。
- #303 未结算就描述命中／缴械，#327 主机纠正；开始阶段随后改为固定行动顺序提示。#370 把护甲挡住伤害描述成格挡，数值结算无误。结算回执现保证出现在叙事前，文风与语义细节仍有后续空间。
- #439 在旧进程中追击已退出的周岚；无骰，取消后主机停战。补上目标须仍在当前顺序中的校验和回归，不把此次收场记为自动停战成功。
- #466 恢复调查并保留HP10，但仍附带部分行动警告。这段仅验证恢复原流程，不证明完整线索调查或长期主持质量。

## 最终资源（主机视图）

| 角色 | HP | 护甲 | 重伤 | 昏迷 | 濒死 | 死亡 |
| --- | --- | --- | --- | --- | --- | --- |
| 守卫 | 6/8 | 1 | False | False | False | False |
| 周岚 | 10/12 | 0 | False | False | False | False |
| 沈砚 | 12/12 | 0 | False | False | False | False |

## 本地原始证据指纹

```json
{
  "PUBLIC-events.json": "83fbf8f1e34df12605c2485b3486f993d594e1e2869c1cf9b087f4783062736a",
  "HOST_DEBUG-events.json": "c6e74dabf824c5e7d0ec3ed0f008212df0c0c5bc2756d71c5e129b47a3aca58e",
  "model-calls.json": "1093c86c5b7ee76e67a0f2476a9e1e5050837c0486abe39ad5d1b4a3634235aa",
  "waiting-defense-before.json": "8980e5164771f89c8f8feb08ef06f4e1297330a5b2f9117d1b28771ac9b82a61",
  "waiting-defense-restored.json": "9a7c408c76d397da6ea81bf1a26c910304eb1d67ef1968ba4f49ccf7338da805",
  "settled-damage-replay.json": "a03de756747c6f2c60084e5032e669abd6992ff028fa9b11d178a5d9422bf74f"
}
```
