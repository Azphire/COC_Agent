"""Batch 20 regression fixtures, not a natural game or a timed acceptance."""

import json

import pytest
from pydantic import ValidationError
from test_action_adjudication import plan_for
from test_agent_runtime import game, submit, wait_cycle  # noqa: F401
from test_batch12 import conversational
from test_rooms import lobby, ok  # noqa: F401

from app.agents.adjudication_schemas import KeeperNarration, KeeperPlan, TeammateDecision, TurnFocus
from app.agents.generation_contracts import generation_contract, restore_output
from app.agents.model import FakeModelAdapter
from app.memory.facts import (
    fact_records,
    readonly_recall,
    recall_question,
    render_facts,
    select_facts,
    summary_sources,
)
from app.preparation.dialogue import dialogue_target, repair_dialogue_focus
from app.preparation.inventory import bind_item_prose
from app.rooms.service import RoomError


def test_memory_budget_counts_full_auxiliary_envelope_and_actual_plan_view():
    from copy import deepcopy

    from app.memory.service import prompt_context_size

    context = {
        "role": "keeper",
        "module_context_audit": {"navigation_revision": 9, "source_hash": "a" * 3000},
        "inventory_state": {"holders": [{"instance_id": "lamp:1", "holder_id": "peer"}]},
        "characters": [{"skill_values": {"spot_hidden": 60}}],
        "fact_evidence": [{"id": "event:7", "text": "原始便签"}],
    }
    original = deepcopy(context)
    assert prompt_context_size(context) == len(json.dumps(context, ensure_ascii=False))
    assert context == original
    enlarged = {**context, "fact_evidence": [{"text": "原始证据" * 2000}]}
    assert prompt_context_size(enlarged) > 6092
    peer = {**context, "role": "investigator"}
    assert prompt_context_size(peer) == len(json.dumps(peer, ensure_ascii=False))
    from app.agents.action_runtime import generation_prompt

    planning = {
        **context,
        "phase": "plan_keeper_action",
        "triggering_action": {"payload": {"text": "进入前厅"}},
    }
    actual = generation_prompt(planning, KeeperPlan)
    assert prompt_context_size(planning) == len(json.dumps(actual, ensure_ascii=False))
    assert actual["characters"] == planning["characters"]
    assert actual["inventory_state"] == planning["inventory_state"]
    assert actual["fact_evidence"] == planning["fact_evidence"]
    teammate = {**planning, "phase": "decide_teammates", "role": "investigator"}
    teammate_prompt = generation_prompt(teammate, TeammateDecision)
    assert prompt_context_size(teammate) == len(json.dumps(teammate_prompt, ensure_ascii=False))
    assert teammate_prompt["inventory_state"] == teammate["inventory_state"]
    assert teammate_prompt["characters"] == teammate["characters"]


def test_compact_prompt_omits_routing_metadata_but_keeps_pending_check_and_inventory():
    from app.agents.action_runtime import planning_prompt

    context = {
        "omit_bound_prompt_metadata": True,
        "phase": "plan_keeper_action",
        "role": "keeper",
        "knowledge_enabled": False,
        "structure_incomplete": False,
        "current_check": True,
        "inventory_state": {"complete": True, "holders": []},
        "fact_evidence": [{"id": "event:3", "text": "只管前进"}],
        "module": {
            "current_scene": {"node_id": "node3", "title": "3号", "heading_path": ["车", "3号"]}
        },
    }
    prompt = planning_prompt(context)
    assert prompt["current_check"] is True
    assert prompt["inventory_state"] == context["inventory_state"]
    assert prompt["fact_evidence"] == context["fact_evidence"]
    assert not {"phase", "role", "knowledge_enabled", "structure_incomplete"} & prompt.keys()
    assert prompt["module"]["current_scene"] == {"node_id": "node3", "title": "3号"}
    assert context["module"]["current_scene"]["heading_path"] == ["车", "3号"]


def event(seq, kind, **payload):
    return dict(seq=seq, type=kind, visibility="public", actor_member_id="player", payload=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,wrong",
    [
        ("被撕裂的人类肢体散落在车厢里。", "地面散落着被撕裂的人类肢体，空气中有血腥味。"),
        ("墙角放着一只刻有银色羽毛的木匣。", "眼前的桌上放着刻有银色羽毛的木匣。"),
        ("北侧墙上刻着三枚相连的紫色螺旋。", "这里的门上刻着三枚相连的紫色螺旋。"),
    ],
)
async def test_correct_current_scene_reference_cannot_relocate_a_known_clue(source, wrong):
    from types import SimpleNamespace as N

    from app.agents.action_runtime import ActionRuntimeMixin
    from app.module_ir.facts import nonlocal_source_claims

    entities = [
        {"id": "west", "type": "scene", "fact_scope": "current_scene",
         "public_summary": "这里有普通座椅和窗帘。"},
        {"id": "old-clue", "type": "clue", "fact_scope": "historical",
         "public_summary": source},
    ]

    async def public(*args):
        return entities

    runtime = N(service=N(entities=N(public=public)))
    output = KeeperNarration(public_narration=wrong, current_scene_reference="west",
                             public_entity_references=["west"])
    with pytest.raises(RoomError, match="先前场景"):
        await ActionRuntimeMixin.validate_narration_output(
            runtime, None, N(id="room"), N(state={}), N(context={}), output
        )
    assert not nonlocal_source_claims("你想起先前的发现：" + source, entities)
    assert not nonlocal_source_claims("这里没有" + source, entities)
    assert not nonlocal_source_claims("窗帘轻轻晃动，空气有些潮湿。", entities)
    entities[1]["fact_scope"] = "current_scene"
    assert not nonlocal_source_claims(wrong, entities)


@pytest.mark.parametrize(
    "source,detail,subject",
    [
        ("被撕裂的人类肢体散落在车厢里。", "地面散落着被撕裂的人类肢体", "人类肢体"),
        ("墙角放着一只刻有银色羽毛的木匣。", "桌上放着刻有银色羽毛的木匣", "木匣"),
    ],
)
def test_scene_improvisation_projection_rebuilds_from_original_located_clues(
    source, detail, subject
):
    from copy import deepcopy

    from app.memory.events import relevant_incidental_memories

    history = [
        event(1, "action.submitted", text="我查看北厅。"),
        event(2, "entity.revealed", id="clue", type="clue", title=subject,
              public_summary=source, scene_id="north"),
        event(3, "keeper.narration", text=detail, scene_id="north",
              incidental_source="kp_improvisation", incidental_details=[detail]),
        event(4, "scene.updated", scene_id="south", scene_title="南厅"),
        event(5, "keeper.narration", text=detail + "空气潮湿。", scene_id="south",
              incidental_source="kp_improvisation", incidental_details=[detail, "空气潮湿。"]),
        event(6, "keeper.narration", text="窗帘轻轻晃动。", scene_id="south",
              incidental_source="kp_improvisation", incidental_details=["窗帘轻轻晃动。"]),
        event(7, "snapshot.loaded", source_seq=6),
    ]
    original = deepcopy(history)
    current = relevant_incidental_memories(history, "我靠近门边，观察地面和桌面。", "south")
    assert [r["source_event_seq"] for r in current] == [6]
    recalled = relevant_incidental_memories(history, f"先前的{subject}在哪里？", "south")
    assert 3 in [r["source_event_seq"] for r in recalled]
    assert 5 not in [r["source_event_seq"] for r in recalled]
    assert history == original


@pytest.mark.parametrize(
    "note,npc,item,scene",
    [
        ("只管前进吧，已经没有退路了。", "乘务员", "鞋", "2号车厢"),
        ("钟响三次后沿东廊前进。", "林女士", "围巾", "旧大厅"),
    ],
)
def test_original_quotes_and_action_results_survive_false_summaries_and_restore(
    note, npc, item, scene
):
    events = [
        event(1, "action.submitted", text="开始调查"),
        event(2, "entity.revealed", public_summary="便签写着：" + note),
        event(
            3,
            "npc.spoke",
            actor_name=npc,
            entity_id="npc",
            text="我只看到了前面的门。",
            scene_id=scene,
        ),
        event(4, "action.submitted", text=f"我把{item}扔到远处，用声响引开怪物。"),
        event(
            5, "module.interaction", source_event_seq=4, text="声响吸引了怪物，前门出现通行机会。"
        ),
        event(6, "keeper.narration", text="我们用纸条号码引开怪物，便签写着注意脚下。"),
        event(7, "snapshot.loaded", source_seq=5),
        event(8, "action.submitted", text="继续游戏"),
    ]
    for _ in range(3):
        # Round-trip storage and repeatedly derive from originals, never prior prose.
        events = json.loads(json.dumps(events))
        route = summary_sources(events, [1, 2, 3, 4, 5, 6])
        assert "纸条号码" not in json.dumps(route, ensure_ascii=False)
    answer = render_facts(
        select_facts(events, [], "便签原文是什么？之前用了什么办法引开怪物？他说过什么？")
    )
    assert note in answer and item in answer and "前门出现通行机会" in answer
    assert npc + "当时说" in answer and "纸条号码" not in answer
    assert not any(r["source_event_seq"] == 6 for r in fact_records(events))


def test_correct_reference_cannot_publish_wrong_body_for_keeper_or_peer():
    record = dict(
        id="entity:note", source_event_seq=2, kind="source_text", title="便签", text="向东前进。"
    )
    context = dict(
        readonly_recall=True,
        fact_evidence=[record],
        current_scene_reference="hall",
        triggering_action={"seq": 12, "payload": {"text": "便签原文是什么？"}},
        PUBLIC_CLAIM_OPTIONS=[
            dict(
                claim_id="note",
                category="module_fact",
                statement="向东前进。",
                entity_ids=["note"],
                evidence_ids=[],
                visibility="public",
            )
        ],
    )
    schema = generation_contract(KeeperNarration, context)
    bad = schema(public_narration="便签写着向西走，乘务员说用手机照亮。", claim_ids=["note"])
    fixed = restore_output(bad, KeeperNarration, context)
    assert fixed.public_narration == "便签：向东前进。"
    peer = TeammateDecision(
        mode="act",
        action_type="investigate",
        action_text="我检查背包。",
        speech_text="便签写着向西走。",
        related_player_action_seq=12,
        confidence=1,
    )
    fixed = restore_output(peer, TeammateDecision, context)
    assert (
        fixed.mode == "speak"
        and fixed.action_text is None
        and fixed.speech_text == "便签：向东前进。"
    )


def test_many_npc_turns_cannot_displace_original_note_question():
    events = [
        event(1, "entity.revealed", public_summary="便签写着：钟响三次后沿东廊前进。", cycle_id="c")
    ]
    events += [
        event(i, "npc.spoke", actor_name="林女士", entity_id="npc", text=f"第{i}件普通见闻。")
        for i in range(2, 15)
    ]
    answer = render_facts(select_facts(events, [], "最初便签的原话是什么？"))
    assert "钟响三次后沿东廊前进" in answer
    assert "普通见闻" not in answer


@pytest.mark.parametrize(
    "document,npc,item", [("便签", "乘务员", "钥匙"), ("信件", "林女士", "铜灯")]
)
def test_two_sided_source_and_npc_question_do_not_reserve_unasked_inventory(document, npc, item):
    from app.memory.facts import bounded_facts
    from app.preparation.inventory import recalled_inventory

    events = [
        event(0, "action.submitted", text=f"我查看{document}。"),
        event(
            1, "entity.revealed", title=document + "正面", public_summary="正面写着：沿着北廊前进。"
        ),
        event(
            2,
            "entity.revealed",
            title=document + "背面",
            public_summary=f"背面写着：{item}藏在第三只箱子里。",
        ),
        event(3, "npc.spoke", actor_name=npc, text=f"我把{item}遗落在前门附近。"),
    ]
    events += [
        event(i, "npc.spoke", actor_name=npc, text=f"{item}的第{i}件普通见闻。")
        for i in range(4, 14)
    ]
    view = dict(
        known_items=[dict(id="item", names=[item])],
        holders=[],
        members=[dict(id="p", name="顾遥", starting_items=[], source_event_seq=15)],
    )
    question = f"开场{document}正反面到底写了什么？{npc}关于{item}实际说过什么？"
    assert recalled_inventory(view, question) == []
    selected = bounded_facts(select_facts(events, [], question))
    answer = render_facts(selected)
    assert "沿着北廊前进" in answer and "第三只箱子" in answer
    assert npc + "当时说" in answer
    context = dict(readonly_recall=True, fact_evidence=selected)
    output = restore_output(
        KeeperNarration(public_narration="只有正面有字。"), KeeperNarration, context
    )
    assert "沿着北廊前进" in output.public_narration and "第三只箱子" in output.public_narration


def test_opening_note_and_current_holder_do_not_request_initial_inventory():
    from app.preparation.inventory import recalled_inventory

    view = dict(
        known_items=[dict(id="key", names=["钥匙"])],
        holders=[],
        members=[dict(id="p", name="顾遥", starting_items=[], source_event_seq=15)],
    )
    records = recalled_inventory(view, "开场便签写了什么？钥匙现在由谁保管？")
    assert [r["id"] for r in records] == ["state:inventory"]


def test_loading_later_original_snapshot_restores_its_event_branch():
    from app.memory.events import story_events

    events = [
        event(1, "action.submitted", text="开始"),
        event(2, "module.interaction", text="取得围巾"),
        event(3, "action.submitted", text="扔出围巾引开怪物"),
        event(4, "module.interaction", source_event_seq=3, text="声响引开怪物，前门可以通过。"),
        event(5, "snapshot.loaded", source_seq=2),
        event(6, "keeper.narration", text="这条分支没有诱导"),
        event(7, "snapshot.loaded", source_seq=4),
    ]
    branch, _ = story_events(events)
    assert [e["seq"] for e in branch] == [1, 2, 3, 4]
    answer = render_facts(select_facts(events, [], "之前怎么引开怪物，结果是什么？"))
    assert "围巾" in answer and "前门可以通过" in answer and "没有诱导" not in answer


@pytest.mark.parametrize(
    "text",
    [
        "刚才用了什么办法引开它？",
        "沈砚，开场便签说什么？",
        "你记错了，我想回顾便签原文。",
        "之前检查背包的结果是什么？",
        "林女士实际说过什么关于铜钥匙的话？",
        "手机和钥匙有没有真正交接过？分别是谁交给了谁，实际交接结果是什么？",
        "这盏铜灯是否转交过？",
        "蓝色罗盘交接过吗？",
        "那个开关有没有打开过？",
        "我们是否进入过西厅？",
    ],
)
def test_recall_operations_are_past_tense(text):
    assert readonly_recall(text)


def test_recall_with_independent_present_action_is_not_readonly():
    assert not readonly_recall("回顾刚才的线索，然后我检查桌子。")
    assert not readonly_recall("便签原文是什么？。我打开门。")
    assert not readonly_recall("铜灯交接过吗？现在我进入西厅。")
    assert not readonly_recall("我们有没有真正交接过钥匙？然后我检查桌子。")


@pytest.mark.parametrize("raw", [
    "包扎的真实结果是什么？我们现在分别持有什么，手机的照明是否开启？",
    "修理的实际结果如何？",
    "我们目前各自拿着哪些物品？",
    "铜灯的灯光是否打开？",
])
def test_current_result_and_possession_questions_ignore_previous_npc_focus(raw):
    assert readonly_recall(raw)
    npc = dict(id="npc", title="守夜人", aliases=[], public_summary="一名守夜人。")
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id="npc")
    assert repair_dialogue_focus(plan, raw, [npc], previous="npc") is None
    restored = restore_output(
        KeeperNarration(public_narration="守夜人说：我不知道。"), KeeperNarration,
        {"readonly_recall": True,
         "fact_evidence": [dict(id="event:12", kind="result", text="治疗成功；铜灯已开启。")]},
    )
    assert "治疗成功；铜灯已开启。" in restored.public_narration
    assert "我不知道" not in restored.public_narration
    assert not readonly_recall(raw + "现在我打开东门。")


@pytest.mark.parametrize("action,summary", [
    ("我用衣服给守夜人包扎，并问他钥匙在哪里？", "守夜人的急救成功。"),
    ("我清理船夫的伤口并问他潮汐时间。", "船夫的医学治疗失败。"),
])
def test_treatment_result_is_linked_to_original_action_across_summary_and_load(action, summary):
    history = [
        event(1, "game.started"),
        event(2, "action.submitted", cycle_id="aid", text=action),
        event(3, "combat.resolved", cycle_id="aid", summary=summary),
        event(4, "npc.spoke", actor_name="守夜人", text="这件事我现在还说不清楚。"),
        event(5, "snapshot.loaded", source_seq=4),
    ]
    query = action[1:7] + "的真实结果是什么？我们现在分别持有什么，铜灯是否开启？"
    records = select_facts(history, [], query)
    receipt = next(r for r in records if r["source_event_seq"] == 3)
    assert receipt["action_event_seq"] == 2 and receipt["action_quote"] == action
    body = render_facts(records)
    assert summary in body
    history.insert(2, event(6, "action.submitted", cycle_id="aid", text="另一个不确定的动作"))
    # An ambiguous cycle must not invent which submitted action produced a receipt.
    ambiguous = next(r for r in fact_records(history[:-1]) if r["source_event_seq"] == 3)
    assert "action_quote" not in ambiguous


@pytest.mark.parametrize("subject,action,result", [
    ("包扎", "我用自己的衣服给乘务员包扎，并问他钥匙在哪里？", "乘务员的急救成功。"),
    ("修理门闩", "我修理门闩，并问船夫何时启航。", "门闩修复失败，仍然卡住。"),
])
@pytest.mark.parametrize("wording", ["的真实结果是什么", "的实际结果如何", "实际的结果是什么"])
def test_named_result_survives_unrelated_checks_in_compound_recall(
    subject, action, result, wording,
):
    from app.memory.facts import bounded_facts

    history = [
        event(1, "game.started"),
        event(2, "action.submitted", cycle_id="task", text=action),
        event(3, "combat.resolved", cycle_id="task", summary=result),
        event(4, "check.resolved", reason="我查看报纸的日期，确认它是什么时候刊出的。",
              display_text="图书馆使用检定：44/20，失败。"),
        event(5, "npc.spoke", actor_name="乘务员", text="这件事我现在还说不清楚。"),
        event(6, "action.submitted", cycle_id="light", text="我用手机照亮前面的通道。"),
        event(7, "module.interaction", source_event_seq=6, text="手机照亮脚下有限范围。"),
        event(8, "agent.summary_rebuilt", text="玩家处理好了所有事情。", covered_until_seq=7),
        event(9, "snapshot.loaded", source_seq=8),
    ]
    query = subject + wording + "？我们现在分别持有什么，手机的照明是否开启？"
    selected = select_facts(history, [], query)
    assert selected[0]["source_event_seq"] == 3
    state = dict(id="state:inventory", kind="current_state",
                 text="颜舟没有持有物；姜宁持有手机，照明开启。")
    selected = bounded_facts([state, *selected])
    assert any(r.get("source_event_seq") == 3 for r in selected)
    restored = restore_output(
        KeeperNarration(public_narration="报纸日期没有查明，所以刚才的动作失败了。",
                        fact_ids=[r["id"] for r in selected]),
        KeeperNarration, {"readonly_recall": True, "fact_evidence": selected},
    )
    assert result in restored.public_narration
    assert "所以刚才的动作失败" not in restored.public_narration


@pytest.mark.parametrize("scene,item,names", [
    ("3号车厢", "黑包", ["陈砚", "林夏"]),
    ("北楼走廊", "蓝色信箱", ["周棠", "沈川"]),
])
@pytest.mark.parametrize("followup", [
    "两人的搜索结果分别是什么", "各自尝试的结果如何", "每次搜索结果怎样",
])
def test_scoped_plural_results_keep_both_actors_and_current_inventory(scene, item, names, followup):
    from app.memory.facts import bounded_facts, recalled_location
    from app.preparation.inventory import recalled_inventory

    history = [
        event(1, "member.joined", member_id="p", display_name=names[0]),
        event(2, "member.joined", member_id="a", display_name=names[1]),
        event(3, "game.started"),
        event(4, "scene.updated", scene_title=scene, scene_summary="行李散落。"),
        event(5, "entity.revealed", type="clue", title="失落的" + item,
              public_summary=f"工作人员曾经保管钥匙，装钥匙的{item}掉在{scene}前门附近。"),
        {**event(6, "check.resolved", reason=f"我仔细搜索{scene}前门附近，寻找掉落的{item}。",
                 display_text="侦查99/70，失败。"), "actor_member_id": "p"},
        {**event(7, "check.resolved", reason=f"我搜索{scene}前门附近，找找工作人员说的{item}。",
                 display_text="侦查90/70，失败。"), "actor_member_id": "a"},
        event(8, "action.submitted", text="我使用刚接到的手机照亮脚下。"),
        event(9, "module.interaction", source_event_seq=8, text="手机只照亮有限范围。"),
        event(10, "entity.revealed", type="location", title=scene + "通往前方的通道",
              public_summary="前方的门通向其他地方。"),
        event(11, "scene.updated", scene_title="前厅", scene_summary="门仍然上锁。"),
        event(12, "agent.summary_rebuilt", text="大家已经找到了钥匙。", covered_until_seq=11),
        event(13, "snapshot.loaded", source_seq=12),
    ]
    view = dict(known_items=[], holders=[dict(title="手机", holder_id="p")],
                members=[dict(id="p", name=names[0]), dict(id="a", name=names[1])])
    query = f"回顾{scene}寻找{item}的经过，{followup}？我们是否已经拿到钥匙？"
    selected = bounded_facts(recalled_location(dict(scene_title="前厅"), query)
        + recalled_inventory(view, query) + select_facts(history, [], query))
    assert {6, 7} <= {r.get("source_event_seq") for r in selected}
    body = render_facts(selected)
    assert names[0] + "：该次检定针对" in body and names[1] + "：该次检定针对" in body
    assert "侦查99/70，失败。" in body and "侦查90/70，失败。" in body
    assert names[0] + "当前持有：手机" in body
    assert names[1] + "当前没有登记的持有物" in body
    restored = restore_output(KeeperNarration(public_narration="两人找到钥匙，直接开门。",
        fact_ids=[r["id"] for r in selected]), KeeperNarration,
        {"readonly_recall": True, "fact_evidence": selected})
    assert "两人找到钥匙" not in restored.public_narration
    assert "侦查90/70，失败。" in restored.public_narration
    assert not recalled_inventory(view, "工作人员说我们是否已经拿到钥匙？")


@pytest.mark.parametrize("title", ["驾驶室门", "北楼铁栅门"])
@pytest.mark.parametrize("opened", [False, True])
def test_current_visible_door_state_uses_existing_projection_before_related_history(title, opened):
    from app.memory.facts import bounded_facts, recalled_entity_states

    entity = dict(id="door", type="location", title=title, fact_scope="current_scene",
                  revealed_event_seq=8, public_summary=title + "仍然上锁。")
    if opened:
        entity.update(initial_public_summary=entity["public_summary"],
                      public_summary=title + "已经由周棠用钥匙打开。",
                      current_state_receipts=[dict(source_event_seq=27, entity_id="door")])
    query = f"我们现在在哪里？{title}的实际状态是什么，依据哪些已经完成的动作？"
    current = recalled_entity_states([entity], query)
    selected = bounded_facts(current + [
        dict(id=f"event:{i}", kind="npc_statement", text="钥匙可能放在黑包里。" * 20)
        for i in range(5)
    ])
    assert current[0]["source_event_seq"] == (27 if opened else 8)
    assert current[0]["source"] == "current_public_entity_projection"
    restored = restore_output(KeeperNarration(public_narration="这里很安静。",
        fact_ids=[r["id"] for r in selected]), KeeperNarration,
        {"readonly_recall": True, "fact_evidence": selected})
    assert entity["public_summary"] in restored.public_narration
    if opened:
        assert "仍然上锁" not in restored.public_narration
    assert not recalled_entity_states([{**entity, "fact_scope": "historical"}], query)
    assert not recalled_entity_states([entity], f"当时{title}的状态是什么？")
    assert not recalled_entity_states([entity], f"船夫说过{title}是什么状态？")
    assert not recalled_entity_states([entity], "我们现在在哪里？")


@pytest.mark.parametrize("raw", [
    "我先观察醒来的地方，拿起身旁的便签，仔细查看正面和背面分别写了什么。",
    "我仔细翻看铜牌，看看背面写了什么。",
    "我们先取下墙上的布告，检查背面写着什么。",
    "我把封条翻过来，查看原文。",
    "我认真观察门上的信封，再翻看背面的文字。原文是什么？",
])
def test_source_reading_with_actual_inspection_is_not_readonly(raw):
    assert recall_question(raw)
    assert not readonly_recall(raw)


@pytest.mark.parametrize("raw", [
    "回顾我们刚才翻看铜牌时，背面写了什么？",
    "我记得之前取下了布告，它的原文是什么？",
    "之前我把封条翻过来，看到的原文是什么？",
])
def test_past_source_inspection_remains_readonly(raw):
    assert readonly_recall(raw)


def test_bounded_current_facts_keep_current_location_and_full_quotes():
    from app.memory.facts import bounded_facts, recalled_location

    assert readonly_recall("我们现在在哪一节车厢？")
    location = recalled_location({"scene_title": "旧大厅"}, "回顾路线和当前位置。")
    records = [dict(id=str(i), kind="source_text", text="线索" * 100) for i in range(8)]
    chosen = bounded_facts(records + location)
    assert chosen[0] == location[0] and len(chosen) <= 6
    assert len(render_facts(chosen)) <= 680


def test_move_destination_reply_is_bound_once_to_current_navigation():
    import asyncio
    from types import SimpleNamespace as N

    from app.agents.conversation import movement_reply
    from app.persistence.adjudication_models import ActionPlanRecord
    from app.persistence.agent_models import AgentCycle
    from app.persistence.room_models import RoomEvent

    async def verify():
        original = N(seq=12, payload={"text": "我潜行过去。"})
        clarification = N(payload={"actor_member_id": "p", "cycle_id": "c"}, seq=18)
        plan = N(document={"plan": {"current_scene_id": "hall", "expected_navigation_revision": 3}})
        cycle = N(id="c", state={"triggering_event_seq": 12})
        nav = N(current_scene_node_id="hall", navigation_revision=3)

        class Session:
            async def get(self, model, key):
                return (
                    {AgentCycle: cycle, ActionPlanRecord: plan}.get(model)
                    if model != RoomEvent
                    else clarification
                    if key[1] == 18
                    else original
                )

            async def scalar(self, statement):
                return original

        async def state(*args):
            return nav

        service = N(navigation=N(state=state))
        reply = await movement_reply(service, Session(), N(id="room"), "p", "大厅另一端", 18)
        assert reply["text"] == "我潜行前往大厅另一端"
        assert reply["submitted_text"] == "大厅另一端" and reply["clarification_source_seq"] == 12
        nav.navigation_revision = 4
        assert await movement_reply(service, Session(), N(id="room"), "p", "大厅另一端", 18) is None

    asyncio.run(verify())


@pytest.mark.parametrize("name", ["手电筒", "铜灯"])
def test_empty_inventory_discussion_and_concrete_use_are_distinct(name):
    view = {
        "known_items": [{"id": "lamp", "names": [name]}],
        "holders": [],
        "members": [{"id": "peer", "name": "许岚"}],
    }
    for text in [
        f"我用{name}照明。",
        f"许岚举着{name}。",
        f"我带了{name}。",
        f"{name}的光束照亮房间。",
        f"{name}还在口袋里，不过我得确认一下。",
        f"我检查了一下口袋，{name}还在。",
        f"我用{name}寻找线索。",
        f"我没有手机，但是{name}在背包里。",
        f"我有一盏{name}。",
    ]:
        with pytest.raises(RoomError):
            bind_item_prose(text, view, "peer")
    for text in [
        f"我们没有{name}。",
        f"如果找到{name}就能照明。",
        f"我寻找{name}。",
        f"有没有{name}？",
        f"我检查口袋，看看{name}是否还在。",
        f"{name}还在桌上。",
        f"我的{name}不见了。",
    ]:
        assert bind_item_prose(text, view, "peer") == []
    view["holders"] = [dict(instance_id="lamp:player", item_id="lamp", holder_id="player")]
    with pytest.raises(RoomError):
        bind_item_prose(f"我用{name}照明。", view, "peer")
    view["holders"][0]["holder_id"] = "peer"
    assert bind_item_prose(f"我用{name}照明。", view, "peer") == ["lamp:player"]


@pytest.mark.parametrize("npc,item", [("乘务员", "手机"), ("林女士", "铜灯")])
def test_npc_possession_cannot_borrow_the_player_inventory(npc, item):
    view = {
        "members": [{"id": "player", "name": "周岚"}],
        "other_actors": [{"id": "npc", "names": [npc]}],
        "known_items": [{"id": "lamp", "names": [item]}],
        "holders": [dict(instance_id="lamp:p", item_id="lamp", holder_id="player")],
    }
    for text in [
        f"{npc}的{item}灯光照亮周围。", f"{npc}举着{item}。",
        f"{npc}的{item}屏幕微弱地亮着，照亮了脚边的一小片区域。",
        f"你看到{npc}的{item}外壳上有一道划痕。",
    ]:
        with pytest.raises(RoomError):
            bind_item_prose(text, view, "player")
    with pytest.raises(RoomError):
        bind_item_prose(f"我拿着{item}。", view, "npc")
    assert bind_item_prose(f"周岚举着{item}。", view, "player") == ["lamp:p"]
    assert bind_item_prose(f"{npc}问有没有{item}。", view, "player") == []
    assert bind_item_prose(f"周岚的{item}屏幕微弱地亮着。", view, "player") == ["lamp:p"]
    for text in [
        f"{npc}的{item}亮着吗？", f"{npc}的{item}在哪里？",
        f"我们讨论{npc}的{item}。", f"我想问{npc}的{item}。",
        f"关于{npc}的{item}，我还有问题。",
    ]:
        assert bind_item_prose(text, view, "player") == []


@pytest.mark.parametrize(
    "name,raw",
    [("乘务员", "我向乘务员询问：怎么离开？"), ("许岚", "许岚女士，你好，知道发生了什么吗？")],
)
def test_wrong_action_focus_recovers_addressed_question(name, raw):
    plan = plan_for(raw, "interact", "wrong")
    plan.focus = TurnFocus(action=raw, action_target_id="wrong")
    npc = {"id": "npc", "title": name, "type": "npc"}
    assert repair_dialogue_focus(plan, raw, [npc]) == npc
    assert plan.focus.addressee_id == "npc" and plan.focus.question
    assert not plan.focus.action and plan.parsed_intent.type == "converse"
    assert dialogue_target("那你听见什么了吗？", [npc], previous="npc") == npc
    assert dialogue_target("那你听见什么了吗？", [], previous="npc") is None


def test_bandage_and_question_keeps_both_intents():
    raw = "我给乘务员包扎并问他：钥匙在哪里？"
    plan = plan_for(raw, "interact", "npc")
    plan.focus = TurnFocus(action=raw, action_target_id="npc")
    repair_dialogue_focus(plan, raw, [dict(id="npc", title="乘务员")])
    assert plan.focus.action == raw and "钥匙在哪里" in plan.focus.question
    assert plan.focus.addressee_id == "npc"


@pytest.mark.parametrize("title,salutation", [
    ("乘务员", "师傅"), ("图书管理员", "先生"),
    ("女守门人", "大姐"), ("摆渡人", "您好"),
])
@pytest.mark.parametrize("wrong_addressee", ["actor", "peer"])
def test_first_salutation_reaches_the_only_accessible_npc(title, salutation, wrong_addressee):
    raw = f"{salutation}，能听见我说话吗？这里出了什么事，你现在感觉怎么样？"
    npc = dict(id="npc", title=title, type="npc")
    plan = plan_for(raw, "converse")
    plan.focus = TurnFocus(question=raw, addressee_id=wrong_addressee, answer_basis="social")
    assert repair_dialogue_focus(plan, raw, [npc], member_names=["林青", "沈川"]) == npc
    assert plan.focus.addressee_id == "npc"
    assert "这里出了什么事" in plan.focus.question and "感觉怎么样" in plan.focus.question
    assert plan.parsed_intent.target_id == "npc" and not plan.focus.action


def test_generic_npc_salutation_does_not_steal_named_peers_or_resolve_ambiguity():
    npc = dict(id="npc", title="乘务员", type="npc")
    raw = "师傅，能听见我说话吗？"
    assert dialogue_target(raw, []) is None
    assert dialogue_target(raw, [npc, {**npc, "id": "second"}]) is None
    assert dialogue_target(raw, [npc], member_names=["师傅"]) is None
    assert dialogue_target("林青，你好，听见了吗？", [npc], member_names=["林青"]) is None
    assert dialogue_target("你好，林青，你听见了吗？", [npc], member_names=["林青"]) is None
    assert dialogue_target("主持人，这里发生了什么？", [npc]) is None
    recall = "回顾师傅之前说过的话，钥匙线索是什么？"
    plan = plan_for(recall, "recall")
    assert repair_dialogue_focus(plan, recall, [npc]) is None


@pytest.mark.parametrize("actor,other", [("player", "peer"), ("peer", "player")])
def test_dialogue_generation_cannot_address_its_own_speaker(actor, other):
    context = dict(
        action_identifiers=dict(plan_id="p", cycle_id="p", actor_member_id=actor,
            actor_character_slot_id="slot", current_scene_id="scene",
            expected_navigation_revision=0),
        triggering_action=dict(seq=1, actor_member_id=actor, payload=dict(text="师傅，你好。")),
        current_participants=dict(members={actor: "沈川", other: "林青"}),
        current_targets=[dict(id="npc", title="乘务员", type="npc")],
    )
    schema = generation_contract(KeeperPlan, context)
    with pytest.raises(ValidationError, match="addressee_id"):
        schema(parsed_intent={"type": "converse"}, focus={"addressee_id": actor})
    for addressee in [other, "npc", None]:
        assert schema(parsed_intent={"type": "converse"},
            focus={"addressee_id": addressee}).focus.addressee_id == addressee


@pytest.mark.parametrize("name,item", [("叶宁", "手机"), ("林女士", "旧铜灯")])
@pytest.mark.parametrize("tail", ["拿着", "打开它照明"])
def test_own_handover_and_teammate_request_keep_separate_actors(name, item, tail):
    from app.preparation.action_authority import (
        freeze_action,
        requested_action_kinds,
        teammate_request,
    )
    from app.preparation.dialogue import repair_teammate_focus
    from app.rooms.schemas import SessionStateV1

    raw = f"我把自己保留下来的{item}交给{name}，请{name}{tail}。"
    own = raw.split("，")[0] + "，"
    members = {"p": "宋衡", "peer": name}
    assert teammate_request(raw, members, "p") == "peer"
    assert "give" not in requested_action_kinds(raw)
    for selected in [own, raw]:
        plan = plan_for(raw, "interact", "item")
        plan.focus = TurnFocus(action=selected, action_target_id="item")
        repair_teammate_focus(plan, raw, members, "p")
        assert plan.parsed_intent.type == "interact" and plan.focus.action == own
        assert plan.focus.action_target_id == "item" and plan.focus.addressee_id == "peer"
        assert plan.focus.question == f"请{name}{tail}。"
        authority = freeze_action(plan, raw, "p", 20, "hall", {},
                                  SessionStateV1().module_runtime, members)
        assert authority["request_member_id"] is None
        assert authority["kinds"] == ["give"]
    request = f"{name}，能不能把{item}交给我？"
    plan = plan_for(request, "interact", "item")
    plan.focus = TurnFocus(action=request, action_target_id="item")
    repair_teammate_focus(plan, request, members, "p")
    assert plan.parsed_intent.type == "converse" and not plan.focus.action

    context = dict(action_identifiers=dict(plan_id="p", cycle_id="p", actor_member_id="p",
        actor_character_slot_id="slot", current_scene_id="hall", expected_navigation_revision=0),
        current_participants={"members":members},
        triggering_action={"seq":20,"actor_member_id":"p", "type":"action.submitted",
                           "payload":{"text":raw}})
    schema = generation_contract(KeeperPlan, context)
    with pytest.raises(ValidationError, match="action_clause_ids"):
        schema(parsed_intent={"type":"converse"},focus={"action_clause_ids":["u2"]})


@pytest.mark.parametrize("name,target", [("程岚", "控制面板"), ("林女士", "北厅保险柜")])
@pytest.mark.parametrize("timing", ["现在", "这就", "马上", "接着"])
def test_timed_direct_request_keeps_its_addressee_and_no_requester_operation(name, target, timing):
    from app.preparation.action_authority import freeze_action, requested_action_kinds
    from app.preparation.dialogue import repair_teammate_focus
    from app.rooms.schemas import SessionStateV1

    raw = f"{name}，{timing}请实际用钥匙解锁眼前的{target}。"
    members = {"p": "许衡", "peer": name}
    plan = plan_for(raw, "use_item", "panel")
    plan.focus = TurnFocus(action=raw.split("，")[1], action_target_id="panel")
    repair_teammate_focus(plan, raw, members, "p")
    assert plan.focus.addressee_id == "peer" and plan.focus.question == raw
    assert not plan.focus.action and plan.parsed_intent.type == "converse"
    assert requested_action_kinds(raw) == ["open"]
    authority = freeze_action(plan, raw, "p", 18, "hall", {},
                              SessionStateV1().module_runtime, members)
    assert authority["request_member_id"] == "peer" and authority["kinds"] == []


def test_teammate_prompt_preserves_unique_receipts_and_full_server_provenance():
    from copy import deepcopy

    from app.agents.action_runtime import generation_prompt

    receipt = dict(entity_id="door", source_event_seq=27, actor_member_id="peer", text="门已打开。")
    unique = dict(entity_id="panel", source_event_seq=31,
                  actor_member_id="peer", text="面板已解锁。")
    context = dict(
        public_state={"completed_interactions": [receipt]},
        public_entities=[dict(id="door", title="北厅门", type="location",
            public_summary="门已打开。",
            state="revealed", origin="host", revealed_event_seq=11, scope_label="当前场景",
            fact_scope="current_scene", current_state_receipts=[receipt]),
            dict(id="panel", title="保险柜", type="location", public_summary="柜门已开。",
                 fact_scope="historical", current_state_receipts=[unique])],
        triggering_action={"seq":40,"actor_member_id":"p","payload":{"text":"林女士，现在请查看保险柜。"}},
        requested_operations=["observe"],
        inventory_state={"complete":True,"holders":[dict(instance_id="key:1",item_id="key",holder_id="peer")],
                         "members":[dict(id="p",name="许衡",held=[],starting_belongings="not_retained")]},
    )
    original = deepcopy(context)
    prompt = generation_prompt(context, TeammateDecision)
    assert context == original
    assert prompt["public_state"]["completed_interactions"] == [receipt]
    assert "current_state_receipts" not in prompt["public_entities"][0]
    assert prompt["public_entities"][1]["current_state_receipts"] == [unique]
    assert prompt["public_entities"][1]["fact_scope"] == "historical"
    assert prompt["requested_operations"] == ["observe"]
    assert prompt["inventory_state"] == context["inventory_state"]
    assert prompt["triggering_action"]["actor_member_id"] == "p"


def test_npc_contract_requires_actual_speech_and_rejects_null():
    schema = generation_contract(
        KeeperNarration,
        dict(current_scene_reference="s", response_brief={"responder": {"kind": "npc", "id": "n"}}),
    )
    for value in [{}, {"npc_speech": None}, {"npc_speech": {"text": ""}}]:
        with pytest.raises(ValidationError):
            schema.model_validate(value)


def test_teammate_actual_search_cannot_become_only_a_question():
    context = dict(
        action_identifiers=dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="a",
            actor_character_slot_id="s",
            current_scene_id="hall",
            expected_navigation_revision=0,
        ),
        triggering_action={
            "seq": 2,
            "type": "agent.action_proposed",
            "payload": {"text": "我检查盒子里是否有铜灯。"},
        },
    )
    schema = generation_contract(KeeperPlan, context)
    with pytest.raises(ValidationError, match="action_clause_ids"):
        schema(parsed_intent={"type": "converse"}, focus={"question_clause_ids": ["u1"]})
    assert schema(
        parsed_intent={"type": "investigate"}, focus={"action_clause_ids": ["u1"]}
    ).focus.action_clause_ids == ["u1"]


@pytest.mark.parametrize(
    "raw",
    [
        "现在就潜行过去，到2号车厢内部另一端。",
        "我放轻脚步，尝试潜行穿过2号车厢，避开喘息声，前往车头。",
        "我穿过2号车厢，去前面的门。",
    ],
)
def test_local_stealth_cannot_take_wrong_exit_and_arrival_uses_receipt(raw):
    context = dict(
        action_identifiers=dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="a",
            actor_character_slot_id="s",
            current_scene_id="node2",
            expected_navigation_revision=0,
        ),
        triggering_action={"seq": 1, "payload": {"text": raw}},
        current_targets=[dict(id="scene2", type="scene", title="2号车厢")],
        approved_exits=[
            dict(transition_id="back", target_scene_node_id="node3", target_public_title="3号车厢")
        ],
    )
    schema = generation_contract(KeeperPlan, context)
    repaired = restore_output(
        schema(
            parsed_intent={"type": "move"},
            focus={"action": raw, "action_target_id": "node3"},
            proposed_transition_id="back",
        ),
        KeeperPlan,
        context,
    )
    assert repaired.parsed_intent.type == "interact" and repaired.focus.action_target_id == "node2"
    assert repaired.proposed_transition_id is None
    context.update(
        current_scene_reference="scene3",
        public_tool_results={
            "events": [
                event(8, "scene.updated", scene_title="3号车厢", scene_summary="行李散落满地。")
            ]
        },
    )
    fixed = restore_output(
        KeeperNarration(public_narration="你悄无声息地穿过2号车厢。"), KeeperNarration, context
    )
    assert fixed.public_narration == "你已抵达3号车厢。行李散落满地。"


def test_npc_question_publishes_speech_even_when_model_only_narrates(client, game):  # noqa: F811
    def response(messages, kwargs):
        result = conversational(messages, kwargs)
        if kwargs["response_schema"].__name__ == "KeeperPlan":
            raw = json.loads(messages[-1]["content"])["triggering_action"]["payload"]["text"]
            result["focus"] = {"action": raw, "action_target_id": "caretaker"}
        if kwargs["response_schema"].__name__ == "TeammateDecision":
            return dict(
                mode="pass",
                related_player_action_seq=json.loads(messages[-1]["content"])["triggering_action"][
                    "seq"
                ],
                confidence=1,
            )
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "管理员，你好，听得见我吗？"))
    assert wait_cycle(client, game)["status"] == "completed"
    speeches = [
        e for e in ok(client.get(game["prefix"] + "/events"))["events"] if e["type"] == "npc.spoke"
    ]
    assert speeches and "问题" in speeches[-1]["payload"]["text"]


def test_local_batch19_failed_room_original_evidence():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "data/prepared/changan/batch-19/long-c1/HOST_DEBUG-events.json"
    )
    if not path.exists():
        pytest.skip("Local historical room is not included in Git")
    events = json.loads(path.read_text("utf8"))
    # This is a read-only replay of the old failed room, not a new real-model run.
    public = [e for e in events if e["visibility"] == "public"]
    entities = [e["payload"] for e in public if e["type"] == "entity.revealed"]
    note = render_facts(
        select_facts(public, entities, "开场便签的原文是什么？乘务员当时说过什么？")
    )
    assert "只管前进吧，已经没有退路了。" in note
    assert "没有NPC实际答话" in note
    lure = select_facts(public, entities, "刚才用了什么办法引开循声者？")
    assert any(r.get("action_event_seq") == 1270 for r in lure)
    assert "鞋" in render_facts(lure) and "纸条号码" not in render_facts(lure)


def test_movement_clarification_keeps_original_authority(client, game):  # noqa: F811
    # The natural destination matching itself is exercised through the navigation
    # fixtures; a stale or unrelated answer must never resurrect an old movement.
    from app.agents.conversation import movement_reply

    svc = client.app.state.agent_service

    async def verify():
        async with svc.rooms.database.sessions() as session:
            room = await svc.rooms.room(session, game["room"]["id"])
            assert await movement_reply(svc, session, room, room.host_member_id, "另一端") is None

    client.portal.call(verify)


def test_treatment_followup_is_a_question_and_preserves_source_target():
    from app.preparation.dialogue import can_speak, question_after_treatment

    assert (
        question_after_treatment("我给许岚包扎并问她：灯塔钥匙在哪里？", "许岚")
        == "许岚，灯塔钥匙在哪里？"
    )
    assert question_after_treatment("我给许岚包扎。", "许岚") is None
    npc = {"id": "n", "combat_template": {"injury": {"unconscious": True}}}
    assert not can_speak(npc, {})
    assert can_speak(npc, {"instance": {"npc_id": "n", "injury": {"unconscious": False}}})
    assert not can_speak(npc, {"instance": {"npc_id": "n", "injury": {"dead": True}}})


def test_move_prompt_keeps_exit_conditions_and_item_receipt_fallback():
    from app.agents.action_runtime import planning_prompt
    from app.agents.narration import fallback_narration

    context = {
        "triggering_action": {"payload": {"text": "我进入前方车厢。"}},
        "check_requirements": [{"entity_id": "hidden_lamp", "successful_check": "luck"}],
        "approved_exits": [
            {"transition_id": "ahead", "available": False, "condition_summary": "需先解除阻挡"}
        ],
    }
    prompt = planning_prompt(context)
    assert (
        "check_requirements" not in prompt and prompt["approved_exits"] == context["approved_exits"]
    )
    assert (
        fallback_narration(
            "interact",
            {"events": [event(3, "module.interaction", text="物品已交给指定队友。")]},
            "旧场景",
        )
        == "物品已交给指定队友。"
    )


def test_compound_treatment_keeps_actual_operation_and_question():
    from app.agents.combat_runtime import human_decision_contract
    from app.preparation.dialogue import compound_treatment

    raw = "我给检票员包扎并问他：工具在哪里？"
    assert compound_treatment(raw) == "first_aid"
    assert compound_treatment("如果我给检票员包扎并问他，他会答应吗？") is None
    assert compound_treatment("我不要给检票员包扎并问他。") is None
    schema = human_decision_contract(
        {
            "input": raw,
            "combat": {"participants": {"n": {"label": "检票员"}}},
            "treatment_targets": [{"id": "n"}],
        }
    )
    assert schema.model_json_schema()["properties"]["operation"]["const"] == "first_aid"
    assert schema.model_json_schema()["properties"]["target_id"]["const"] == "n"


@pytest.mark.parametrize("npc", ["乘务员", "看守"])
def test_pronoun_treatment_keeps_the_actual_previous_npc(npc):
    from app.agents.combat_runtime import human_decision_contract

    context = {
        "input": "我用衣服布条包扎他的腿，并问他：钥匙在哪里？",
        "combat": {"participants": {"n": {"label": npc}, "p": {"label": "许宁"}}},
        "treatment_targets": [{"id": "n"}, {"id": "p"}],
        "previous_dialogue_target": "n",
    }
    schema = human_decision_contract(context)
    assert schema.model_json_schema()["properties"]["target_id"]["const"] == "n"
    with pytest.raises(ValidationError):
        schema.model_validate({"operation": "first_aid", "target_id": "p", "reason": "错误的人"})
    context["input"] = "我给许宁包扎并问他：钥匙在哪里？"
    assert (
        human_decision_contract(context).model_json_schema()["properties"]["target_id"]["const"]
        == "p"
    )


@pytest.mark.parametrize("rejected", [False, True])
def test_treatment_question_survives_both_settlement_and_rule_rejection(rejected):
    import asyncio
    from types import SimpleNamespace as N

    from app.preparation.dialogue import continue_treatment_question

    async def verify():
        rows, public = [], []
        original = N(
            seq=12, actor_member_id="player", payload={"text": "我包扎他的腿，并问他：钥匙在哪里？"}
        )

        class Session:
            async def get(self, *args):
                return original

            def add(self, value):
                rows.append(value)

        def append(*args, **kwargs):
            public.append(args[4])
            return N(seq=20)

        attempt = {"operation": "first_aid", "target_id": "n"}
        cycle = N(
            id="cycle",
            state={
                "triggering_event_seq": 12,
                "combat_decision": attempt,
                "combat_rejection": "目标不需要治疗" if rejected else None,
            },
        )
        room = N(
            id="room",
            session_state={
                "combat": {"participants": {"n": {"npc_id": "source-npc", "label": "乘务员"}}}
            },
        )
        await continue_treatment_question(
            N(rooms=N(append=append)), Session(), room, cycle, None if rejected else attempt
        )
        assert len(rows) == 1 and rows[0].status == "queued"
        assert public[0]["text"] == "乘务员，钥匙在哪里？"
        assert public[0]["continuation_of"] == 12
        await continue_treatment_question(
            N(rooms=N(append=append)), Session(), room, cycle, None if rejected else attempt
        )
        assert len(rows) == 1

    asyncio.run(verify())


@pytest.mark.parametrize(
    "scene,next_scene,item", [("3号车厢", "2号车厢", "黑包"), ("旧大厅", "东塔", "信件")]
)
def test_historical_failure_does_not_negate_independent_move_or_local_edge_stealth(
    scene, next_scene, item
):
    from app.agents.action_policy import explicit_movement, local_scene_movement

    raw = f"我们暂时没有找到{item}。我先进入{next_scene}，留意前方动静。"
    assert explicit_movement(raw)
    assert not explicit_movement(f"如果找到{item}。我再进入{next_scene}。")
    assert not explicit_movement(f"我没有进入{next_scene}。")
    local = f"我放轻脚步，沿{scene}边缘潜行，试着靠近前门。"
    assert local_scene_movement(local, [{"type": "scene", "title": scene}])
    assert not local_scene_movement("如果安全，" + local, [{"type": "scene", "title": scene}])


@pytest.mark.parametrize("tool", ["工具", "撬棍", "铁钳"])
def test_unregistered_instrument_requires_an_actual_named_instance(tool):
    view = {"holders": [], "known_items": []}
    with pytest.raises(RoomError):
        bind_item_prose(f"我尝试用{tool}撬开门，看看是否能打开。", view, "p")
    assert bind_item_prose(f"我寻找能撬门的{tool}。", view, "p") == []
    assert bind_item_prose(f"如果找到{tool}，再想办法。", view, "p") == []
    assert bind_item_prose("我用手撬一下门。", view, "p") == []
    view["known_items"] = [{"id": "tool", "names": [tool]}]
    view["holders"] = [{"item_id": "tool", "holder_id": "p", "instance_id": "actual"}]
    assert bind_item_prose(f"我用{tool}撬门。", view, "p") == ["actual"]


@pytest.mark.parametrize("item", ["黑包", "木匣"])
def test_sourced_npc_reply_acknowledges_unanswered_followup_parts(item):
    from app.preparation.dialogue import sourced_dialogue_reply

    source = f"{item}的提带已经断裂，掉在仓库前门附近，里面装着钥匙。"
    question = f"你说的{item}是什么样子？前面还有什么需要我们小心的？"
    reply = sourced_dialogue_reply(question, [source])
    assert source in reply
    assert "前面还有什么需要我们小心的" in reply and "不能确定" in reply
    assert "里面装着钥匙" in reply


@pytest.mark.parametrize(
    "raw",
    [
        "我在旧大厅内部潜行到另一端。",
        "我放轻脚步，潜行穿过旧大厅，前往东塔。",
    ],
)
def test_local_stealth_finds_approved_passage_without_its_full_title(raw):
    from types import SimpleNamespace as N

    from app.preparation.runtime_schemas import ModuleInteraction
    from app.preparation.search import named_local_interaction_ids, repair_local_interaction_target

    facts = N(
        raw_text=raw,
        actor_member_id="player",
        scene_id="hall",
        local_entity_ids={"scene", "passage"},
        revealed_entity_ids={"scene"},
        reveal_errors={},
        approved_entities={
            "scene": dict(id="scene", type="scene", title="旧大厅"),
            "passage": dict(
                id="passage",
                type="location",
                title="石柱旁窄道",
                reveal_conditions={"access_policy": "automatic"},
                interactions=[
                    ModuleInteraction(
                        id="cross",
                        source_block_ids=["fixture-pass-source"],
                        instruction="通过真实潜行检定。",
                        public_result="到达门前。",
                        kp_enabled=True,
                        check_name="stealth",
                        action_kinds=["pass"],
                    ).model_dump()
                ],
            ),
        },
    )
    assert named_local_interaction_ids(facts) == {"passage"}
    plan = plan_for(raw, "interact", "hall")
    plan.focus = TurnFocus(action=raw, action_target_id="hall")
    repair_local_interaction_target(plan, facts, {})
    assert plan.focus.action_target_id == "passage" and plan.proposed_reveal_entity_ids == [
        "passage"
    ]
    facts.reveal_errors["passage"] = "隐藏路径条件未满足"
    assert named_local_interaction_ids(facts) == set()


@pytest.mark.parametrize("raw", ["如果穿过旧大厅会怎么样？", "我并未穿过旧大厅。", "我返回门厅。"])
def test_hypothetical_or_other_destination_is_not_local_crossing(raw):
    from app.agents.action_policy import local_scene_movement

    assert not local_scene_movement(raw, [{"type": "scene", "title": "旧大厅"}])


def test_checked_passage_uses_original_action_not_rephrased_environment_quote():
    from types import SimpleNamespace as N

    from app.preparation.adjudication import action_evidence, state_verified_method

    raw = "我潜行到通道另一端。"
    rule = {"action_kinds": ["pass"], "check_name": "stealth", "required_facts": []}
    decision = N(action_clause_ids=["u1"], evidence_quotes=["我潜行到通道另一端。", "大厅里很暗。"])
    assert state_verified_method(rule)
    result = action_evidence(decision, [{"id": "u1", "text": raw}], raw, [raw], state_verified=True)
    assert result == (raw, [raw])
    assert not state_verified_method({**rule, "required_facts": ["unknown_distance"]})


@pytest.mark.parametrize("name", ["手机", "铜灯"])
def test_lighting_state_follows_named_device_across_clauses_and_in_recall(name):
    from types import SimpleNamespace as N

    from app.preparation.inventory import light_state, recalled_inventory
    from app.preparation.observation import validate_lighting_prose
    from app.rooms.service import RoomError

    entity = {"id": "device", "type": "item", "title": name, "interactions": [
        {"action_kinds": ["light"], "set_flags": {"device_light": True}}
    ]}
    runtime = N(inventory={"instance": "p"}, item_instances={"instance": "device"},
                dropped_items={}, flags={})
    bad = f"你拿起{name}，它亮起微弱的光，照亮了房间。"
    with pytest.raises(RoomError, match="尚未实际"):
        validate_lighting_prose(bad, [entity], runtime, "room")
    validate_lighting_prose(f"如果打开{name}，它可能亮起光。", [entity], runtime, "room")
    validate_lighting_prose(f"你准备点亮{name}，看看能否照亮房间。", [entity], runtime, "room")
    holder = {"title": name, "holder_id": "p", **light_state(entity, runtime.flags)}
    view = {"holders": [holder], "members": [{"id": "p", "name": "林川"}],
            "known_items": [{"names": [name]}]}
    result = recalled_inventory(view, f"{name}现在由谁拿着，是否已经打开？")
    assert "林川当前持有" in result[0]["text"]
    assert "照明未开启" in result[0]["text"]
    runtime.flags["device_light"] = True
    assert light_state(entity, runtime.flags) == {"light_active": True}
    validate_lighting_prose(bad, [entity], runtime, "room")
    runtime.inventory.clear()
    with pytest.raises(RoomError, match="尚未实际"):
        validate_lighting_prose(bad, [entity], runtime, "room")


def test_phone_screen_claim_does_not_escape_via_omitted_name_or_network_negation():
    from types import SimpleNamespace as N

    from app.preparation.observation import validate_lighting_prose
    from app.rooms.service import RoomError

    entity = {"id": "phone", "type": "item", "title": "手机", "interactions": [
        {"action_kinds": ["light"], "set_flags": {"light": True}}
    ]}
    runtime = N(inventory={"phone": "p"}, item_instances={}, dropped_items={}, flags={})
    for text in [
        "你打开手机，屏幕亮起微弱的光。", "手机没有网络，但屏幕亮起了。", "屏幕亮起微光。"
    ]:
        with pytest.raises(RoomError, match="尚未实际"):
            validate_lighting_prose(text, [entity], runtime, "room")


def test_resource_fallback_cannot_republish_old_false_lighting_improvisation():
    from app.agents.narration import fallback_narration

    brief = {"attempt": "但可以尝试打开看看能不能提供一点光。",
             "incidental_memories": [{"speaker": "KP", "text": "手机屏幕已经亮起。"}],
             "allowed_facts": [{"text": "你的手机仍在身上。"}]}
    view = {"holders": [{"title": "手机", "holder_id": "peer", "light_active": False}],
            "members": [{"id": "p", "name": "林川"}, {"id": "peer", "name": "沈砚"}],
            "known_items": [{"names": ["手机"]}]}
    text = fallback_narration("interact", {"events": []}, "大厅", brief=brief, inventory_state=view)
    assert "没有完成新的物品操作" in text
    assert "沈砚当前持有：手机（照明未开启）" in text
    assert "林川当前没有登记的持有物" in text
    assert "屏幕已经亮起" not in text


def test_shared_light_flag_does_not_activate_another_held_instance():
    from app.preparation.inventory import instance_light_state

    definitions = {"phone": {"interactions": [
        {"id": "on", "action_kinds": ["light"], "set_flags": {"phone_light": True}},
        {"id": "off", "action_kinds": ["light"], "set_flags": {"phone_light": False}},
    ]}}
    runtime = {"flags": {"phone_light": True}, "inventory": {"p1": "a", "p2": "b"},
               "item_instances": {"p1": "phone", "p2": "phone"}, "receipts": {}}
    assert instance_light_state("phone", "p2", runtime, definitions) == {"light_active": None}
    for seq, actor, operation in [(1, "a", "on"), (2, "a", "off"), (3, "b", "on")]:
        runtime["receipts"][str(seq)] = {"source_event_seq": seq, "actor_member_id": actor,
            "entity_id": "phone", "interaction_id": operation,
            "inventory": dict(runtime["inventory"])}
    assert instance_light_state("phone", "p1", runtime, definitions) == {"light_active": False}
    assert instance_light_state("phone", "p2", runtime, definitions) == {"light_active": True}
    runtime["inventory"]["p2"] = "a"
    assert instance_light_state("phone", "p2", runtime, definitions) == {"light_active": True}
    runtime["flags"]["phone_light"] = False
    assert instance_light_state("phone", "p2", runtime, definitions) == {"light_active": False}


@pytest.mark.parametrize("container", ["口袋", "衣袋"])
@pytest.mark.parametrize("verb", ["检查", "看看"])
def test_clipped_belongings_purpose_retains_actual_self_inspection(container, verb):
    from app.preparation.search import repair_belongings_action

    action = f"我{verb}自己的{container}，"
    raw = f"先留在大厅里。{action}看看醒来后还保留着哪些随身物品。"
    value = {"focus": {"action": "看看醒来后还保留着哪些随身物品。", "action_target_id": "item"},
             "parsed_intent": {"type": "investigate"}}
    context = {"triggering_action": {"actor_member_id": "p", "payload": {"text": raw}}}
    repair_belongings_action(value, context)
    assert value["focus"]["action"] == action
    assert value["focus"]["action_target_id"] == "item"
    for unrelated in ["我进入前面的大厅。", "我询问乘务员。"]:
        value["focus"]["action"] = unrelated
        repair_belongings_action(value, context)
        assert value["focus"]["action"] == unrelated
    value["focus"]["action"] = "看看还保留着哪些随身物品。"
    context["readonly_recall"] = True
    repair_belongings_action(value, context)
    assert value["focus"]["action"] == "看看还保留着哪些随身物品。"


@pytest.mark.parametrize(
    "raw",
    [
        "我放轻脚步，沿2号车厢边缘潜行，试着靠近通往先头的前门。",
        "我压低身体，沿旧大厅侧边潜行，试着靠近东侧的门。",
    ],
)
def test_prepared_method_retries_missing_operative_clause_instead_of_silent_noop(raw):
    from pydantic import ValidationError

    from app.agents.generation_contracts import utterance_clauses
    from app.preparation.adjudication import decision_contract

    clauses = utterance_clauses(raw)
    context = {
        "actor": "p", "items": [], "members": {"p": "林川"}, "npc_instances": [],
        "clauses": clauses,
        "action_authority": {"utterance": raw, "action": raw, "kinds": ["pass"]},
    }
    rule = {"id": "sneak", "instruction": "通过实际潜行检定。", "check_name": "stealth",
            "action_kinds": ["pass"], "source_block_ids": ["source"],
            "public_result": "按实际检定结算。"}
    schema = decision_contract(context, [{"option": "1", "rule": rule}])
    bad = {"option": "1", "applicable": True, "action_clause_ids": [clauses[-1]["id"]]}
    with pytest.raises(ValidationError, match="action_clause_ids"):
        schema.model_validate(bad)
    good = {**bad, "action_clause_ids": [clauses[1]["id"], clauses[2]["id"]]}
    assert schema.model_validate(good).applicable
    assert not schema.model_validate({**bad, "applicable": False}).applicable
    context["action_authority"]["kinds"] = ["observe"]
    with pytest.raises(ValidationError, match="action_clause_ids"):
        decision_contract(context, [{"option": "1", "rule": rule}]).model_validate(good)


@pytest.mark.parametrize(
    "raw",
    ["我打开这部手机的屏幕，用它照明。", "我用铜灯照亮脚下。", "我开启平板屏幕。"],
)
def test_explicit_screen_and_lighting_clauses_require_real_use(raw):
    from app.preparation.action_authority import action_kinds

    assert "light" in action_kinds(raw)
    assert not action_kinds("如果" + raw)
    assert "light" not in action_kinds("不要用它照明。")
    assert "light" not in action_kinds("你能用它照明吗？")


def test_worn_throw_cannot_select_an_unrelated_held_key():
    from app.preparation.adjudication import decision_contract

    context = {
        "actor": "p",
        "items": [{"item_id": "key", "instance_id": "key:1", "holder_id": "p"}],
        "members": {"p": "周岚"},
        "npc_instances": [],
        "clauses": [{"id": "u1"}],
        "action_authority": {"item_instances": {}},
    }
    schema = decision_contract(context, [])
    assert schema.model_json_schema()["properties"]["used_item_id"]["type"] == "null"
    context["action_authority"]["item_instances"] = {"key": "key:1"}
    assert (
        "key"
        in decision_contract(context, []).model_json_schema()["properties"]["used_item_id"]["enum"]
    )


@pytest.mark.parametrize("intent", ["observe", "move", "unknown"])
@pytest.mark.parametrize(
    "raw",
    [
        "我把脱下的围巾抛向大厅另一端，用响声引开它。",
        "潜行没有成功。我脱下一只鞋，扔向后方远离前门的位置，用落地声把喘息声的主人引开。",
        "我留在原地，摘下帽子抛向远处，用响声引开它。",
    ],
)
def test_actual_throw_misclassified_as_observation_retains_operation(raw, intent):
    context = dict(
        action_identifiers=dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="a",
            actor_character_slot_id="s",
            current_scene_id="hall",
            expected_navigation_revision=0,
        ),
        triggering_action={"seq": 2, "payload": {"text": raw}},
        current_targets=[],
        approved_exits=[],
    )
    schema = generation_contract(KeeperPlan, context)
    result = restore_output(
        schema(parsed_intent={"type": intent}, focus={"action": raw}), KeeperPlan, context
    )
    assert result.parsed_intent.type == "interact" and result.focus.action == raw
    assert not result.proposed_tool_calls  # No inferred success or new method.


def test_simple_move_prompt_drops_old_checks_but_keeps_exit_and_inventory():
    from app.agents.action_runtime import planning_prompt

    context = {
        "triggering_action": {"payload": {"text": "我进入前面的大厅。"}},
        "previous_attempts": [{"result": {"success": False}}],
        "inventory_state": {"members": [{"held": []}]},
        "approved_exits": [{"available": False, "condition_summary": "门尚未打开"}],
    }
    result = planning_prompt(context)
    assert "previous_attempts" not in result
    assert result["inventory_state"] == context["inventory_state"]
    assert result["approved_exits"] == context["approved_exits"]


@pytest.mark.parametrize(
    "raw,initial_target",
    [
        ("我用林女士交给我的铜钥匙打开仓库门。", "door"),
        ("我这里有铜钥匙，可以尝试打开仓库的门。", "hall"),
    ],
)
def test_named_tool_does_not_hide_the_already_selected_legal_gate(raw, initial_target):
    from types import SimpleNamespace as N

    from app.preparation.runtime_schemas import ModuleInteraction
    from app.preparation.search import repair_local_interaction_target

    def rule(**kw):
        return ModuleInteraction(source_block_ids=["fixture-source"], **kw).model_dump()

    facts = N(
        raw_text=raw,
        actor_member_id="p",
        scene_id="hall",
        local_entity_ids={"key", "door"},
        revealed_entity_ids={"key"},
        reveal_errors={},
        approved_entities={
            "key": dict(
                type="item",
                title="铜钥匙",
                interactions=[
                    rule(
                        id="give",
                        instruction="交给对方",
                        public_result="已交出",
                        kp_enabled=True,
                        inventory_operation="give",
                        item_id="key",
                    )
                ],
            ),
            "door": dict(
                type="location",
                title="仓库门",
                reveal_conditions={"access_policy": "automatic"},
                interactions=[
                    rule(
                        id="unlock",
                        instruction="用铜钥匙开门",
                        public_result="门开了",
                        kp_enabled=True,
                        encounter_operation="open_door",
                        door_id="door",
                        required_item_ids=["key"],
                    )
                ],
            ),
        },
    )
    plan = plan_for(raw, "use_item", initial_target)
    plan.focus = TurnFocus(action=raw, action_target_id=initial_target)
    repair_local_interaction_target(plan, facts, {})
    assert plan.proposed_reveal_entity_ids == ["door"]
    assert plan.focus.action_target_id == "door" and not plan.proposed_tool_calls


def test_gate_status_requires_actual_receipt_even_with_valid_entity_reference():
    from types import SimpleNamespace as N

    from app.preparation.current_state import validate_access_prose

    entities = [
        dict(
            id="gate",
            title="仓库门",
            interactions=[
                dict(id="unlock", encounter_operation="open_door", required_item_ids=["key"])
            ],
        )
    ]
    runtime = N(receipts={})
    for text in [
        "门把手生锈，但没有上锁。",
        "仓库门的门锁似乎被钥匙成功开启。",
        "仓库门已经打开。",
    ]:
        with pytest.raises(RoomError, match="实际开门回执"):
            validate_access_prose(text, "查看仓库的门", entities, runtime)
    validate_access_prose("我尝试打开门锁。", "查看仓库门", entities, runtime)
    runtime.receipts["r"] = dict(entity_id="gate", interaction_id="unlock")
    validate_access_prose("仓库门已经打开。", "查看仓库门", entities, runtime)


def test_rejected_possession_has_a_truthful_reply_without_a_new_operation():
    from app.preparation.inventory import inventory_reply

    view = {"holders": [], "members": [{"id": "p", "starting_belongings": "undetermined"}]}
    assert "还没有确认" in inventory_reply(view, "p")
    view["members"][0]["starting_belongings"] = "not_retained"
    assert "没有已确认" in inventory_reply(view, "p")
    view["holders"] = [{"holder_id": "p", "title": "铜钥匙"}]
    assert "持有：铜钥匙" in inventory_reply(view, "p")
    assert "铜钥匙" not in inventory_reply(view, "peer")


def test_possession_repair_pass_answers_direct_question_without_acting(client, game, monkeypatch):  # noqa: F811
    from app.preparation import inventory

    async def view(*args, **kwargs):
        return {
            "holders": [],
            "known_items": [{"id": "lamp", "names": ["铜灯"]}],
            "members": [
                {
                    "id": game["agent"],
                    "name": "investigator",
                    "starting_belongings": "undetermined",
                    "held": [],
                }
            ],
        }

    monkeypatch.setattr(inventory, "inventory_context", view)
    attempts = []

    def response(messages, kwargs):
        result = conversational(messages, kwargs)
        context = json.loads(messages[-1]["content"])
        schema = kwargs["response_schema"].__name__
        if schema == "KeeperPlan":
            result["focus"] = {
                "question": context["triggering_action"]["payload"]["text"],
                "addressee_id": game["agent"],
                "answer_basis": "teammate",
            }
        if schema == "TeammateDecision":
            attempts.append(schema)
            return dict(
                mode="act" if len(attempts) == 1 else "pass",
                action_type="observe",
                action_text="铜灯还在口袋里。" if len(attempts) == 1 else None,
                related_player_action_seq=context["triggering_action"]["seq"],
                confidence=1,
            )
        return result

    client.app.state.agent_service.model.adapter = FakeModelAdapter(responder=response)
    ok(submit(client, game, "investigator，你有铜灯吗？"))
    assert wait_cycle(client, game)["status"] == "completed"
    events = ok(client.get(game["prefix"] + "/events"))["events"]
    speech = [e["payload"]["text"] for e in events if e["type"] == "agent.spoke"]
    assert len(attempts) == 2 and any("还没有确认" in t for t in speech)
    assert not any(e["type"] == "agent.action_proposed" for e in events)


def test_inspection_question_keeps_actual_action_without_borrowing_question_verbs():
    from app.preparation.action_authority import action_kinds, declared_action

    raw = "我把封条翻过来，仔细检查背面有没有字。"
    assert declared_action(raw) and action_kinds(raw) == ["search"]
    assert action_kinds("我观察门有没有打开。") == ["observe"]
    assert action_kinds("我检查盒子里是否有铜灯。") == ["search"]
    for question in [
        "有没有铜灯？",
        "能否翻看封条？",
        "我没有检查封条。",
        "如果有光，我检查封条。",
        "如果安全，我靠近检查封条。",
    ]:
        assert not declared_action(question) and not action_kinds(question)
    assert not declared_action("我不靠近检查封条。")
    assert not declared_action("我之前靠近检查过封条。")


@pytest.mark.parametrize(
    "raw",
    [
        "我把封条翻过来，仔细检查背面有没有字。",
        "我走近门上的便签，读清正面的字，再翻过来看看背面有没有文字。",
        "我靠近布告，接着翻看背面有没有字。",
        "我先观察醒来的地方，拿起身旁的便签，仔细查看正面和背面分别写了什么。",
        "我取下铜牌，仔细查看背面写了什么。",
    ],
)
def test_actual_player_inspection_cannot_have_only_question_clauses(raw):
    context = dict(
        action_identifiers=dict(
            plan_id="p",
            cycle_id="p",
            actor_member_id="a",
            actor_character_slot_id="s",
            current_scene_id="hall",
            expected_navigation_revision=0,
        ),
        triggering_action={"seq": 2, "type": "action.submitted", "payload": {"text": raw}},
    )
    schema = generation_contract(KeeperPlan, context)
    with pytest.raises(ValidationError, match="action_clause_ids"):
        schema(parsed_intent={"type": "converse"}, focus={"question_clause_ids": ["u1", "u2"]})
    from app.agents.generation_contracts import utterance_clauses

    for mistaken_type in ["converse", "observe"]:
        plan = restore_output(
            schema(
                parsed_intent={"type": mistaken_type},
                focus={"action_clause_ids": [c["id"] for c in utterance_clauses(raw)]},
            ),
            KeeperPlan,
            context,
        )
        assert plan.focus.action == raw and plan.parsed_intent.type == "interact"


@pytest.mark.parametrize("raw", [
    "我靠近检查肢体和血迹，用医学常识判断死亡时间。",
    "我走近木匣仔细查看纹饰，判断它是哪一年制作的。",
    "我凑近铜牌检查背面有没有文字。",
])
def test_approaching_an_object_to_inspect_cannot_be_only_social_prose(raw):
    from app.agents.generation_contracts import utterance_clauses

    context = dict(
        action_identifiers=dict(plan_id="p", cycle_id="p", actor_member_id="a",
            actor_character_slot_id="s", current_scene_id="hall", expected_navigation_revision=0),
        triggering_action={"seq": 2, "type": "action.submitted", "payload": {"text": raw}},
    )
    schema = generation_contract(KeeperPlan, context)
    clauses = [c["id"] for c in utterance_clauses(raw)]
    with pytest.raises(ValidationError, match="action_clause_ids"):
        schema(parsed_intent={"type": "wait"}, focus={"question_clause_ids": clauses})
    fixed = restore_output(schema(parsed_intent={"type": "investigate"},
        focus={"action_clause_ids": clauses}), KeeperPlan, context)
    assert fixed.focus.action == raw and fixed.parsed_intent.type == "investigate"
    assert not fixed.proposed_transition_id  # Nearby inspection does not cross a scene.


def test_selected_interaction_receipt_cannot_be_rewritten_as_no_clue():
    context = dict(
        public_tool_results={
            "events": [event(20, "module.interaction", text="封条背面写着：向东门前进。")]
        }
    )
    result = restore_output(
        KeeperNarration(public_narration="封条背面没有任何文字。"), KeeperNarration, context
    )
    assert result.public_narration == "封条背面写着：向东门前进。"


@pytest.mark.parametrize("document", ["便签", "木牌"])
@pytest.mark.parametrize("selected_clauses", [["u3"], ["u2", "u3"]])
def test_inspection_clause_subset_uses_selected_targets_approved_search(document, selected_clauses):
    raw = f"我先观察醒来的地方，拿起身旁的{document}，仔细查看正面和背面分别写了什么。"
    context = dict(
        action_identifiers=dict(plan_id="p", cycle_id="p", actor_member_id="a",
            actor_character_slot_id="s", current_scene_id="hall", expected_navigation_revision=0),
        triggering_action={"seq": 2, "type": "action.submitted", "payload": {"text": raw}},
        current_targets=[dict(id="front", title=document + "正面", type="clue")],
        module_interactions=[dict(entity_id="front", interactions=[dict(action_kinds=["search"])])],
    )
    schema = generation_contract(KeeperPlan, context)
    output = schema(parsed_intent={"type": "observe"}, focus={
        "action_clause_ids": selected_clauses, "action_target_id": "front"},
        proposed_reveal_entity_ids=["front"])
    plan = restore_output(output, KeeperPlan, context)
    assert plan.parsed_intent.type == "interact"
    assert plan.focus.action_target_id == "front"
    assert plan.focus.action in raw and "背面" in plan.focus.action
    assert plan.proposed_check is None  # The prepared method still decides whether a check applies.
    assert plan.proposed_reveal_entity_ids == ["front"]  # No new hidden reveal is manufactured.
    if selected_clauses == ["u3"]:
        # A search capability belonging to an unrelated target is not authority.
        context["module_interactions"][0]["entity_id"] = "other"
        assert restore_output(output, KeeperPlan, context).parsed_intent.type == "observe"


def test_repeated_observation_uses_public_writing_not_old_improvised_absence():
    from app.agents.narration import response_brief

    raw = "我凑近封条背面，仔细观察有没有隐藏的文字。"
    plan = plan_for(raw, "observe", "note")
    plan.focus = TurnFocus(action=raw, action_target_id="note")
    context = dict(
        triggering_action={"seq": 21, "payload": {"text": raw}},
        public_entities=[
            dict(
                id="note",
                type="clue",
                title="封条背面",
                public_summary="封条背面写着：向东门前进。",
                fact_scope="current_scene",
            )
        ],
    )
    brief, _ = response_brief(plan, context, {"events": []})
    assert brief["source_quotes"] == ["封条背面写着：向东门前进。"]
    value = restore_output(
        KeeperNarration(public_narration="封条背面没有文字。"),
        KeeperNarration,
        {"response_brief": brief},
    )
    assert value.public_narration == "封条背面写着：向东门前进。"
    context["public_entities"][0]["fact_scope"] = "historical"
    assert response_brief(plan, context, {"events": []})[0]["source_quotes"] == []


def test_named_writing_alias_survives_wrong_teammate_focus():
    from app.agents.narration import response_brief

    raw = "我走近布告，看看背面有没有文字。"
    plan = plan_for(raw, "converse", None)
    plan.focus = TurnFocus(question=raw, addressee_id="peer")
    context = dict(
        triggering_action={"seq": 21, "payload": {"text": raw}},
        current_participants={"members": {"peer": "乔安"}},
        public_entities=[
            dict(
                id="note",
                type="clue",
                title="墙上布告正面",
                aliases=["布告"],
                public_summary="布告写着：沿东廊前进。",
                fact_scope="current_scene",
            )
        ],
    )
    brief, _ = response_brief(plan, context, {"events": []})
    result = restore_output(
        KeeperNarration(public_narration="布告背面写着：你还有三分钟。"),
        KeeperNarration,
        {"response_brief": brief},
    )
    assert result.public_narration == "布告写着：沿东廊前进。"


def test_compact_prompt_keeps_server_bindings_and_real_prerequisites():
    import json
    from copy import deepcopy

    from app.agents.action_runtime import compact_planning_prose, generation_prompt

    ids = dict(
        plan_id="plan",
        cycle_id="cycle",
        actor_member_id="actor",
        actor_character_slot_id="slot",
        current_scene_id="hall",
        expected_navigation_revision=7,
    )
    context = dict(
        action_identifiers=ids,
        triggering_action={
            "seq": 20,
            "actor_member_id": "actor",
            "payload": {"text": "我进入东厅。"},
        },
        approved_exits=[
            {
                "transition_id": "exit",
                "target_scene_node_id": "east",
                "target_public_title": "东厅",
                "available": False,
                "condition_summary": "门尚未打开",
            }
        ],
        inventory_state={
            "holders": [],
            "members": [{"id": "actor", "held": [], "starting_belongings": "not_retained"}],
        },
    )
    before = deepcopy(context)

    def size(c):
        return len(
            json.dumps(generation_prompt(c, KeeperPlan), ensure_ascii=False, separators=(",", ":"))
        )

    compact = compact_planning_prose(context, size(context) - 1)
    prompt = generation_prompt(compact, KeeperPlan)
    assert context == before and compact["action_identifiers"] == ids
    assert "action_identifiers" not in prompt and "omit_bound_prompt_metadata" not in prompt
    assert prompt["approved_exits"] == context["approved_exits"]
    assert prompt["inventory_state"]["members"][0]["held"] == []
    assert size(compact) < size(context)
    schema = generation_contract(KeeperPlan, compact)
    result = schema(parsed_intent={"type": "move"}, focus={"action": "我进入东厅。"})
    assert result.plan_id == "plan" and result.cycle_id == "cycle"
    assert result.parsed_intent.actor_character_slot_id == "slot"
    assert result.current_scene_id == "hall" and result.expected_navigation_revision == 7


@pytest.mark.parametrize("item", ["随身手电筒", "蓝色罗盘"])
def test_initial_item_history_is_distinct_from_current_holder_after_transfer(item):
    from app.preparation.inventory import recalled_inventory

    view = dict(
        holders=[{"title": item, "holder_id": "p"}],
        members=[
            {"id": "p", "name": "林舟", "starting_items": [], "source_event_seq": 2},
            {"id": "peer", "name": "周岚", "starting_items": [item], "source_event_seq": 4},
        ],
    )
    records = recalled_inventory(view, "我们醒来时分别保留了什么物品，现在由谁拿着？")
    assert "林舟当前持有：" + item in records[0]["text"]
    assert "林舟的起始随身物检查已结算，没有保留下" in records[1]["text"]
    assert "周岚的起始随身物检查已结算，实际保留：" + item in records[2]["text"]
    assert records[2]["scope"] == "historical" and records[2]["action_event_seq"] == 4


@pytest.mark.parametrize("unknown", ["手电", "信号枪", "铜哨"])
@pytest.mark.parametrize("opening", ["开场各有", "起初带着", "最初各有", "一开始带着"])
def test_false_initial_possession_question_uses_settled_inventory_without_known_item(
    unknown, opening
):
    from app.preparation.inventory import recalled_inventory

    view = dict(
        holders=[], known_items=[],
        members=[dict(id="p", name="林舟", starting_items=[]),
                 dict(id="a", name="周岚", starting_items=["罗盘"])],
    )
    raw = f"我记得我们{opening}一个{unknown}，是这样吗？请核对原记录。"
    assert readonly_recall(raw)
    records = recalled_inventory(view, raw)
    body = render_facts(records)
    assert "林舟的起始随身物检查已结算，没有保留下随身物品" in body
    assert "周岚的起始随身物检查已结算，实际保留：罗盘" in body
    assert unknown not in body
    assert not recalled_inventory(view, "开场的两张便签各有什么文字？")
    assert not recalled_inventory(view, "乘务员开场说两人都有工具，他原话是什么？")


@pytest.mark.parametrize("item_a,item_b", [("手机", "钥匙"), ("铜灯", "蓝色罗盘")])
@pytest.mark.parametrize("verb", ["交接", "流转"])
def test_identical_receipt_text_keeps_distinct_handover_actions_and_speakers(item_a, item_b, verb):
    from copy import deepcopy

    history = [
        event(1, "member.joined", member_id="p", display_name="林舟"),
        event(2, "member.joined", member_id="a", display_name="周岚"),
        {**event(3, "agent.action_proposed", text=f"我把{item_a}交给林舟。"),
         "actor_member_id": "a"},
        event(4, "module.interaction", text="物品已交给指定队友。", source_event_seq=3),
        {**event(5, "action.submitted", text=f"我把刚拿到的{item_b}交给周岚，请她保管。"),
         "actor_member_id": "p"},
        event(6, "module.interaction", text="物品已交给指定队友。", source_event_seq=5),
        event(7, "agent.action_proposed", text=f"我用{item_b}打开面板。"),
        event(8, "module.interaction", text="面板已打开。", source_event_seq=7),
        event(9, "snapshot.loaded", source_seq=8),
    ]
    original = deepcopy(history)
    query = f"开场我们各自保留了什么？后来{item_a}和{item_b}怎样{verb}，现在分别由谁持有？"
    records = select_facts(history, [], query)
    assert {4, 6} <= {r["source_event_seq"] for r in records}
    body = render_facts(records)
    assert f"周岚当时提交的尝试（不代表结果）：我把{item_a}交给林舟。" in body
    assert f"林舟当时提交的尝试（不代表结果）：我把刚拿到的{item_b}交给周岚" in body
    assert body.count("实际记录：物品已交给指定队友。") == 2
    from app.memory.events import story_events

    branch, _ = story_events(history)
    assert not any(e["type"] == "member.joined" for e in branch)
    assert render_facts(select_facts(
        branch, [], query, members={"p": "林舟", "a": "周岚"}
    )) == body
    assert len(body) <= 680 and len(records) <= 6
    assert history == original


@pytest.mark.parametrize("opening", ["刚醒来时", "一开始", "起初"])
def test_natural_opening_recall_cannot_become_a_new_pocket_action(opening):
    raw = f"我们{opening}分别留下了什么物品？现在由谁拿着，又是怎么到他手里的？"
    assert readonly_recall(raw)
    assert not readonly_recall(raw + "现在我进入5号车厢。")
    context = {
        "readonly_recall": readonly_recall(raw),
        "fact_evidence": [
            dict(
                id="state:initial:peer",
                kind="result",
                scope="historical",
                source_event_seq=None,
                text="周岚的起始检查保留了铜灯。",
            )
        ],
        "triggering_action": {"seq": 2, "payload": {"text": raw}},
    }
    result = restore_output(
        KeeperNarration(public_narration="周岚正在重新检查口袋，拿出手机。"),
        KeeperNarration,
        context,
    )
    assert "铜灯" in result.public_narration and "手机" not in result.public_narration


@pytest.mark.parametrize(
    "raw",
    [
        "我们刚才是怎样通过2号车厢的？钥匙现在由谁保管，驾驶室门究竟开了没有？",
        "此前我们如何越过庭院？",
        "铜灯现在由谁保管？",
        "钥匙在谁手里？",
    ],
)
def test_natural_how_and_holder_questions_use_readonly_original_facts(raw):
    assert readonly_recall(raw)
    context = {
        "readonly_recall": readonly_recall(raw),
        "fact_evidence": [dict(id="event:8", kind="result", text="潜行检定成功，通过通道。")],
        "triggering_action": {"seq": 20, "payload": {"text": raw}},
    }
    result = restore_output(
        KeeperNarration(public_narration="灯光在墙上摇晃。"), KeeperNarration, context
    )
    assert "潜行检定成功" in result.public_narration and "摇晃" not in result.public_narration


@pytest.mark.parametrize(
    "raw",
    [
        "刚才如何通过庭院？然后我去东厅。",
        "钥匙在谁手里？现在我返回大厅。",
        "回顾一下便签，我试着打开门。",
        "我检查柜子，然后回顾先前的线索。",
    ],
)
def test_independent_current_action_is_preserved_beside_a_recall(raw):
    assert not readonly_recall(raw)


def test_memory_recall_budget_keeps_originals_and_inventory_before_planning():
    from copy import deepcopy

    from app.memory.facts import recall_context_view

    holders = [
        dict(instance_id=f"item:{i}", item_id=str(i), holder_id="p", title="铜灯", remaining_uses=2)
        for i in range(4)
    ]
    context = dict(
        readonly_recall=True,
        fact_evidence=[dict(id="event:9", source_event_seq=9, text="沿东廊前进。")],
        inventory_state=dict(
            holders=holders, members=[dict(id="peer", held=[], starting_belongings="not_retained")]
        ),
        triggering_action={"seq": 20, "payload": {"text": "我们现在在哪，谁拿着什么？"}},
        characters=[dict(skill_values={str(i): 50 for i in range(29)})],
        module=dict(
            current_scene={"id": "east", "title": "东廊"},
            blocks=["旧叙述" * 1600],
            approved_entities=[{"id": "hidden", "keeper_summary": "隐藏内容" * 200}],
            outgoing_transitions=[{"id": "west"}],
            interaction_state=dict(
                held_items=holders, flags={"light_on": True}, doors={"east": "open"}
            ),
        ),
        module_context_audit={"navigation_revision": 3, "selected_node_ids": ["east"]},
    )
    before = deepcopy(context)
    result = recall_context_view(context)
    assert len(json.dumps(context, ensure_ascii=False)) > 6092
    assert len(json.dumps(result, ensure_ascii=False)) < 6092
    for key in ("fact_evidence", "inventory_state", "triggering_action", "module_context_audit"):
        assert result[key] == before[key]
    assert result["module"]["current_scene"] == before["module"]["current_scene"]
    assert result["module"]["interaction_state"] == dict(
        flags={"light_on": True}, doors={"east": "open"}
    )
    assert context == before
    context["readonly_recall"] = False
    ordinary = recall_context_view(context)
    assert ordinary["characters"] == context["characters"]
    assert ordinary["module"]["blocks"] == context["module"]["blocks"]
    assert ordinary["module"]["approved_entities"] == context["module"]["approved_entities"]
    assert ordinary["module"]["outgoing_transitions"] == context["module"]["outgoing_transitions"]
    assert ordinary["inventory_state"] == context["inventory_state"]
    assert "held_items" not in ordinary["module"]["interaction_state"]
    context["triggering_action"]["payload"]["text"] = "我把铜灯交给周岚，请她保管。"
    transfer = recall_context_view(context)
    assert "skill_values" not in transfer["characters"][0]
    assert transfer["inventory_state"] == context["inventory_state"]
    assert transfer["module"]["approved_entities"] == context["module"]["approved_entities"]
    context["module"]["interaction_state"]["held_items"] = []
    assert recall_context_view(context)["module"]["interaction_state"]["held_items"] == []
    context["item_holders"] = context["inventory_state"]["holders"]
    context["current_participants"] = {"members": {"p": "陆衡", "peer": "苏宁"}}
    context["triggering_action"]["actor_member_id"] = "p"
    context["triggering_action"]["payload"]["text"] = "苏宁，你先把铜灯交回给我，我来看看。"
    peer_context = recall_context_view(context, "peer")
    assert "item_holders" not in peer_context
    assert "skill_values" not in peer_context["characters"][0]
    assert peer_context["inventory_state"] == context["inventory_state"]
    assert recall_context_view(context, "keeper")["characters"] == context["characters"]


@pytest.mark.parametrize("item,recipient", [("手电筒", "陆衡"), ("铜灯", "林舟")])
def test_initial_recall_includes_actual_handover_without_repeating_legacy_template(item, recipient):
    from app.memory.facts import bounded_facts
    from app.preparation.inventory import recalled_inventory

    events = [
        event(1, "action.submitted", text="我检查自己的口袋与随身物，确认实际保留下来的物品。"),
        event(2, "module.interaction", source_event_seq=1, text="随身物已登记；手机没有信号。"),
        event(3, "agent.action_proposed", text=f"我把{item}交给{recipient}。"),
        event(4, "module.interaction", source_event_seq=3, text="物品已交给指定队友。"),
        event(5, "check.resolved", reason="查看线路图", display_text="智力检定成功。"),
    ]
    question = "我们刚醒来时分别留下了什么物品？现在由谁拿着，又是怎么到他手里的？"
    view = dict(
        holders=[dict(title=item, holder_id="p")],
        members=[
            dict(id="p", name=recipient, starting_items=[], source_event_seq=0),
            dict(id="peer", name="周岚", starting_items=[item], source_event_seq=1),
        ],
    )
    records = bounded_facts(recalled_inventory(view, question) + select_facts(events, [], question))
    text = render_facts(records)
    assert f"我把{item}交给{recipient}" in text and "物品已交给指定队友" in text
    assert "周岚的起始随身物检查已结算，实际保留：" + item in text
    assert recipient + "当前持有：" + item in text
    assert "手机没有信号" not in text
    assert events[1]["payload"]["text"] == "随身物已登记；手机没有信号。"


def test_old_false_written_fact_is_not_recompressed_as_incidental_memory():
    from app.memory.events import incidental_records

    old = event(
        12,
        "keeper.narration",
        text="背面没有任何文字或记号，纸张边缘微微泛黄。",
        incidental_source="kp_improvisation",
        incidental_details=["背面没有任何文字或记号", "纸张边缘微微泛黄"],
    )
    assert [r["text"] for r in incidental_records(old)] == ["纸张边缘微微泛黄"]
    assert old["payload"]["incidental_details"][0] == "背面没有任何文字或记号"


@pytest.mark.parametrize("passed", [False, True])
def test_actual_roll_and_reveals_constrain_body_even_with_correct_reference(passed):
    events = [
        event(
            30,
            "check.resolved",
            id="roll",
            result={"passed": passed},
            display_text="侦查检定：实际结果。",
        )
    ]
    if passed:
        events.append(event(31, "entity.revealed", public_summary="航海日志日期：六月五日。"))
    output = restore_output(
        KeeperNarration(
            public_narration="你读到日志日期为七月一日，还有一宗失踪案。",
            check_result_reference="roll",
            incidental_details=["新闻写着失踪案"],
        ),
        KeeperNarration,
        {"public_tool_results": {"events": events}},
    )
    assert "七月" not in output.public_narration and "失踪案" not in output.public_narration
    assert not output.incidental_details
    assert ("六月五日" in output.public_narration) == passed
    assert ("检定失败" in output.public_narration) != passed


def test_unconfirmed_written_target_cannot_acquire_content_from_prose():
    output = restore_output(
        KeeperNarration(public_narration="你翻开信封，看见约定地点是钟楼。"),
        KeeperNarration,
        {"response_brief": {"unconfirmed_target": True}},
    )
    assert "尚未实际确认" in output.public_narration and "钟楼" not in output.public_narration


def test_focus_evidence_does_not_replace_a_different_observation_with_old_text():
    from app.agents.narration import response_brief

    raw = "我查看墙上的地图。"
    plan = plan_for(raw, "observe", "map")
    plan.focus = TurnFocus(action=raw, action_target_id="map", public_fact_ids=["note"])
    context = dict(
        triggering_action={"seq": 21, "payload": {"text": raw}},
        public_entities=[
            dict(id="note", type="clue", title="便条", public_summary="便条写着：向东。"),
            dict(id="map", type="clue", title="地图", public_summary="西面地图已被撕掉。"),
        ],
    )
    assert response_brief(plan, context, {"events": []})[0]["source_quotes"] == [
        "西面地图已被撕掉。"
    ]


@pytest.mark.parametrize("schema", [KeeperPlan, KeeperNarration, TeammateDecision])
def test_prompt_deduplicates_state_without_losing_empty_inventory_or_actual_instances(schema):
    from copy import deepcopy

    from app.agents.action_runtime import generation_prompt

    holders = [
        dict(
            instance_id="lamp:p",
            item_id="lamp",
            holder_id="p",
            holder_name="顾遥",
            title="铜灯",
            remaining_uses=2,
        )
    ]
    context = dict(
        triggering_action={"payload": {"text": "我进入前厅并观察周围。"}},
        inventory_state=dict(
            complete=True,
            holders=holders,
            members=[
                dict(id="p", name="顾遥", held=["lamp:p"], starting_belongings="settled"),
                dict(id="a", name="许宁", held=[], starting_belongings="not_retained"),
            ],
            dropped_items=[dict(instance_id="key", scene_node_id="hall")],
        ),
        item_holders=holders,
        module={
            "interaction_state": {
                "held_items": holders,
                "flags": {"lamp_on": True},
                "doors": {"west": "open"},
            }
        },
        public_tool_results={"events": []},
        response_brief=dict(completed_results={"events": []}, current_inventory=holders),
    )
    before = deepcopy(context)
    result = generation_prompt(context, schema)
    assert context == before
    state = result["inventory_state"]
    assert state["members"][1]["held"] == []
    assert state["members"][1]["starting_belongings"] == "not_retained"
    assert state["holders"][0]["instance_id"] == "lamp:p"
    assert state["holders"][0]["remaining_uses"] == 2
    assert state["dropped_items"] == context["inventory_state"]["dropped_items"]
    assert "item_holders" not in result
    assert "completed_results" not in result["response_brief"]
    if schema is not KeeperNarration:
        assert result["module"]["interaction_state"] == {
            "flags": {"lamp_on": True},
            "doors": {"west": "open"},
        }


def test_rejected_detail_cannot_be_invented_under_a_visible_parent():
    from app.agents.narration import fallback_narration

    results = {"events": [], "failed_tools": [], "blocked_discovery": True}
    output = restore_output(
        KeeperNarration(public_narration="档案的签名是林某。", public_entity_references=["file"]),
        KeeperNarration,
        {"public_tool_results": results},
    )
    assert "尚未确认" in output.public_narration and "林某" not in output.public_narration
    assert "尚未确认" in fallback_narration("investigate", results, "档案室")


def test_actual_new_reveal_supplies_content_even_without_a_check_this_turn():
    results = {
        "events": [event(32, "entity.revealed", public_summary="档案签名：季青。")],
        "blocked_discovery": True,
    }
    output = restore_output(
        KeeperNarration(public_narration="档案签名是林某。"),
        KeeperNarration,
        {"public_tool_results": results},
    )
    assert output.public_narration == "档案签名：季青。"


def test_named_child_detail_accepts_natural_modifiers_without_revealing_all_details():
    from app.preparation.search import named_check_requirement

    r = dict(entity_id="date", title="报纸日期", aliases=[])
    assert named_check_requirement(r, "我看这份报纸的报头，确认印刷日期。", ["date"])
    assert not named_check_requirement(r, "我看这份报纸。", ["date"])
    assert not named_check_requirement(r, "我看这份报纸的报头，确认印刷日期。", [])
    r = dict(entity_id="signature", title="档案签名", aliases=["署名"])
    assert named_check_requirement(r, "我仔细检查档案封面的签名。", ["signature"])
    assert named_check_requirement(r, "我检查署名。")
    assert not named_check_requirement(r, "我检查旁边的柜子。", ["signature"])


@pytest.mark.parametrize(
    "parent_title,detail_title,raw,skill",
    [
        ("7号车厢残骸", "残骸死亡时间",
         "我查看这些遗骸的伤痕，试着判断死亡时间和可能的死因。", "medicine"),
        ("西厅木匣", "木匣制作年代",
         "我仔细查看这个箱子的纹饰，判断制作年代。", "history"),
    ],
)
@pytest.mark.parametrize("selected", [True, False])
def test_selected_parent_and_requested_detail_share_the_approved_check_target(
    parent_title, detail_title, raw, skill, selected
):
    from app.preparation.search import named_check_requirement

    actor = "00000000-0000-0000-0000-000000000001"
    parent = dict(id="parent", type="clue", title=parent_title)
    requirement = dict(
        entity_id="detail", title=detail_title, access_policy="requires_check",
        successful_check=dict(kind="skill", name=skill, difficulty="regular"),
    )
    context = dict(
        action_identifiers=dict(
            plan_id="plan", cycle_id="cycle", actor_member_id=actor,
            actor_character_slot_id="slot", current_scene_id="scene",
            expected_navigation_revision=0,
        ),
        triggering_action=dict(seq=1, payload=dict(text=raw)),
        current_targets=[parent], check_requirements=[requirement],
    )
    plan = plan_for(raw, "investigate", "parent")
    plan.focus = TurnFocus(action=raw, action_target_id="parent")
    plan.proposed_reveal_entity_ids = ["detail"] if selected else []
    fixed = restore_output(plan, KeeperPlan, context)
    if selected:
        assert fixed.focus.action_target_id == "detail"
        assert fixed.proposed_check.name == skill and fixed.proposed_check.clue_id == "detail"
        assert fixed.focus.action == raw
    else:
        assert fixed.focus.action_target_id == "parent" and fixed.proposed_check is None
    assert not named_check_requirement(requirement, "我查看眼前的东西。", ["detail"], parent=parent)
    assert not named_check_requirement(
        requirement, raw, ["detail"], parent=dict(type="clue", title="不相关文件")
    )


@pytest.mark.parametrize(
    "parent_title,detail_title,raw,skill",
    [
        ("7号车厢残骸", "残骸死亡时间",
         "我观察这些肢体的情况，试着判断死亡大概发生在多久以前。", "medicine"),
        ("西厅木匣", "木匣制作年代",
         "我查看这个箱子的纹饰，试着判断它大约是哪一年做出来的。", "history"),
    ],
)
@pytest.mark.parametrize(
    "case", ["selected", "wrong-basis", "wrong-skill", "unrelated", "no-check"]
)
def test_paraphrased_detail_keeps_kp_selected_check_and_actual_parent_basis(
    parent_title, detail_title, raw, skill, case
):
    from app.agents.check_policy import CheckProposal

    actor = "00000000-0000-0000-0000-000000000001"
    parent = dict(id="parent", type="clue", title=parent_title)
    if case == "unrelated":
        parent["title"] = "另一份文件"
    context = dict(
        action_identifiers=dict(
            plan_id="p", cycle_id="p", actor_member_id=actor,
            actor_character_slot_id="slot", current_scene_id="scene",
            expected_navigation_revision=0,
        ),
        triggering_action=dict(seq=1, payload=dict(text=raw)),
        current_targets=[parent],
        check_requirements=[dict(entity_id="detail", title=detail_title,
            access_policy="requires_check",
            successful_check=dict(kind="skill", name=skill, difficulty="regular"))],
    )
    plan = plan_for(raw, "investigate", "parent")
    plan.focus = TurnFocus(action=raw, action_target_id="parent")
    if case != "no-check":
        plan.proposed_check = CheckProposal(
            kind="skill", name="listen" if case == "wrong-skill" else skill,
            target_entity_id="detail", target_member_id=actor,
            basis_entity_id="other" if case == "wrong-basis" else "parent",
            reason=raw, purpose=raw, method=raw, necessity="required",
        )
    fixed = restore_output(plan, KeeperPlan, context)
    assert fixed.focus.action_target_id == ("detail" if case == "selected" else "parent")
    assert fixed.focus.action == raw
    if case == "selected":
        assert fixed.proposed_check.name == skill
        assert fixed.proposed_check.target_entity_id == "detail"
    assert fixed.proposed_reveal_entity_ids == []  # Binding does not grant a discovery.


@pytest.mark.parametrize("subject,detail,kind,name", [
    ("线路示意图", "示意图涂抹痕迹", "attribute", "int"),
    ("墙上星象图", "星象图修订痕迹", "skill", "astronomy"),
    ("铜雕像", "铜雕像年代特征", "skill", "history"),
])
@pytest.mark.parametrize("case", ["unique", "ambiguous", "unrelated", "obstacle"])
def test_explicit_observation_subject_does_not_inherit_an_old_clue_target(
    subject, detail, kind, name, case
):
    actor = "00000000-0000-0000-0000-000000000001"
    raw = f"我仔细观察{subject}，看看标记有没有异常。"
    requirement = dict(entity_id="detail", title=detail, access_policy="requires_check",
        successful_check=dict(kind=kind, name=name, difficulty="regular"))
    requirements = [requirement]
    if case == "ambiguous":
        requirements.append({**requirement, "entity_id": "other-detail"})
    if case == "unrelated":
        requirements = [{**requirement, "title": "旧便签背面"}]
    context = dict(
        action_identifiers=dict(plan_id="p", cycle_id="p", actor_member_id=actor,
            actor_character_slot_id="slot", current_scene_id="scene",
            expected_navigation_revision=0),
        triggering_action=dict(seq=1, payload=dict(text=raw)),
        current_targets=[dict(id="old-note", title="旧便签正面", type="clue")],
        check_requirements=requirements,
    )
    plan = plan_for(raw, "observe", "old-note")
    plan.focus = TurnFocus(action=raw, action_target_id="old-note",
        obstacle="物体被挡住" if case == "obstacle" else "")
    fixed = restore_output(plan, KeeperPlan, context)
    assert fixed.focus.action == raw
    if case == "unique":
        assert fixed.focus.action_target_id == "detail"
        assert fixed.proposed_check.name == name
        assert fixed.proposed_check.target_entity_id == "detail"
        assert fixed.proposed_check.reason == raw
    else:
        assert fixed.focus.action_target_id == "old-note" and fixed.proposed_check is None
    assert fixed.proposed_reveal_entity_ids == []


@pytest.mark.parametrize("item", ["手机", "铜灯"])
def test_unrelated_item_focus_never_overrides_an_explicit_item_inspection(item):
    from app.preparation.search import unrelated_item_focus

    context = dict(
        current_targets=[dict(id="tool", type="item", title="随身" + item)],
        inventory_state=dict(holders=[], known_items=[dict(id="tool", names=[item])]),
    )
    assert unrelated_item_focus("我仔细查看线路图。", "tool", context)
    assert not unrelated_item_focus(f"我仔细查看{item}。", "tool", context)
    assert not unrelated_item_focus("我仔细查看线路图。", "missing", context)


@pytest.mark.parametrize(
    "raw,item",
    [
        ("我仔细搜索座位上和座椅下面遗留的东西。", "遗留报纸"),
        ("我仔细观察地板上气味的来源。", "污损档案"),
        ("我仔细查看线路示意图上的编号和文字。", "示意图涂抹痕迹"),
    ],
)
@pytest.mark.parametrize("selection", ["single", "ambiguous", "none"])
@pytest.mark.parametrize("scene_target", ["scene", "scene-entity", "unrelated-tool"])
def test_scene_search_binds_only_the_kp_selected_single_discovery(
    raw, item, selection, scene_target
):
    actor = "00000000-0000-0000-0000-000000000001"
    context = dict(
        action_identifiers=dict(
            plan_id="plan",
            cycle_id="cycle",
            actor_member_id=actor,
            actor_character_slot_id="slot",
            current_scene_id="scene",
            expected_navigation_revision=0,
        ),
        triggering_action={"seq": 1, "payload": {"text": raw}},
        current_targets=[
            dict(id="scene-entity", type="scene", title="当前场景"),
            dict(id="unrelated-tool", type="item", title="旧铜灯"),
        ],
        check_requirements=[
            dict(
                entity_id=eid,
                title=title,
                access_policy="requires_check",
                successful_check=dict(kind="skill", name="spot_hidden", difficulty="regular"),
            )
            for eid, title in [("found", item), ("other", "封存记录")]
        ],
    )
    plan = plan_for(raw, "investigate", scene_target)
    plan.focus = TurnFocus(action=raw, action_target_id=scene_target)
    plan.proposed_reveal_entity_ids = {
        "single": ["found"],
        "ambiguous": ["found", "other"],
        "none": [],
    }[selection]
    result = restore_output(plan, KeeperPlan, context)
    if selection == "single":
        assert result.proposed_check.name == "spot_hidden"
        assert result.proposed_check.clue_id == "found"
        assert str(result.proposed_check.target_member_id) == actor
        assert result.focus.action == raw and result.focus.action_target_id == "found"
    else:
        assert result.proposed_check is None and result.focus.action_target_id == scene_target


@pytest.mark.parametrize("instrument", ["手机", "铜灯"])
@pytest.mark.parametrize("case", ["held", "unheld", "self_inspection", "hypothetical"])
@pytest.mark.parametrize("phrasing", ["inspect", "light_then_search"])
@pytest.mark.parametrize("selection", ["reveal", "check"])
def test_search_with_held_instrument_keeps_the_selected_discovery_check(
    instrument, case, phrasing, selection
):
    actor = "00000000-0000-0000-0000-000000000001"
    raw = f"我用{instrument}的光束检查座位和通道，看看有没有被忽视的痕迹。"
    if phrasing == "light_then_search":
        raw = f"我用{instrument}照着座椅下面和过道，仔细寻找遗留的东西。"
    if case == "self_inspection":
        raw = f"我检查{instrument}，看看有什么问题。"
    elif case == "hypothetical":
        raw = "如果安全，" + raw
    context = dict(
        action_identifiers=dict(
            plan_id="plan",
            cycle_id="cycle",
            actor_member_id=actor,
            actor_character_slot_id="slot",
            current_scene_id="scene",
            expected_navigation_revision=0,
        ),
        triggering_action=dict(
            seq=1, type="agent.action_proposed", actor_member_id=actor, payload=dict(text=raw)
        ),
        inventory_state=dict(
            holders=[]
            if case == "unheld"
            else [dict(item_id="tool", holder_id=actor, instance_id="actual")],
            known_items=[dict(id="tool", names=[instrument])],
        ),
        check_requirements=[
            dict(
                entity_id="found",
                title="遗留信件",
                access_policy="requires_check",
                successful_check=dict(kind="skill", name="spot_hidden", difficulty="regular"),
            )
        ],
    )
    plan = plan_for(raw, "investigate", "tool")
    plan.focus = TurnFocus(action=raw, action_target_id="tool")
    if selection == "reveal":
        plan.proposed_reveal_entity_ids = ["found"]
    else:
        from app.agents.check_policy import CheckProposal

        plan.proposed_check = CheckProposal(
            kind="skill", name="spot_hidden", target_member_id=actor,
            target_entity_id="found", basis_entity_id="found", reason=raw,
        )
    fixed = restore_output(plan, KeeperPlan, context)
    if case == "held":
        assert fixed.proposed_check.name == "spot_hidden"
        assert fixed.proposed_check.clue_id == "found" and fixed.focus.action_target_id == "found"
        assert str(fixed.proposed_check.target_member_id) == actor
    else:
        assert fixed.proposed_check is None


@pytest.mark.parametrize("target", ["车厢座位", "库房货架"])
def test_empty_search_uses_existing_repair_without_choosing_a_hidden_result(target):
    from app.preparation.search import unresolved_search_plan

    raw = f"我仔细搜索{target}，寻找遗留的东西。"
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    context = {
        "check_requirements": [
            {"access_policy": "requires_check", "successful_check": {"name": "spot_hidden"}}
        ]
    }
    assert unresolved_search_plan(plan, context)
    assert not plan.proposed_check and not plan.proposed_reveal_entity_ids
    assert not unresolved_search_plan(plan, {})
    assert not unresolved_search_plan(plan, {**context, "readonly_recall": True})
    plan.focus.obstacle = "通道被封死，当前无法接近货架。"
    assert not unresolved_search_plan(plan, context)
    plan.focus.obstacle = ""
    plan.needs_clarification = True
    assert not unresolved_search_plan(plan, context)
    plan.needs_clarification = False
    plan.proposed_reveal_entity_ids = ["kp-selected"]
    assert not unresolved_search_plan(plan, context)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["check", "obstacle"])
async def test_search_repair_transmits_actionable_instruction_in_existing_retry(resolution):
    from app.agents.model import AgentModelClient
    from app.config import Settings
    from app.preparation.search import validate_search_plan

    raw = "我用铜灯照着货架，仔细寻找遗留的东西。"
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    context = {"check_requirements": [{
        "access_policy": "requires_check", "successful_check": {"name": "spot_hidden"},
    }]}
    fixed = plan.model_copy(deep=True)
    if resolution == "obstacle":
        fixed.focus.obstacle = "货架被倒下的横梁挡住，暂时无法接近。"
    else:
        # Only the KP's second output chooses a target; the repair grants none.
        fixed.proposed_reveal_entity_ids = ["kp-selected"]
    adapter = FakeModelAdapter([plan, fixed])
    client = AgentModelClient(Settings(_env_file=None), adapter)

    async def validate(output):
        validate_search_plan(output, context)

    result, _ = await client.generate([], KeeperPlan, validate_output=validate)
    assert len(adapter.prompts) == 2 and len(client.calls) == 2
    repair = adapter.prompts[1][-1]["content"]
    assert "check_requirements" in repair and "focus.obstacle" in repair
    assert "不得直接宣布发现或成功" in repair
    assert result.structured == fixed
    assert not plan.proposed_check and not plan.proposed_reveal_entity_ids


@pytest.mark.parametrize("place,item", [("车厢座位", "遗留报纸"), ("库房货架", "残缺账册")])
def test_generic_search_recall_retains_the_actual_discovery_after_rehydration(place, item):
    raw = f"我仔细搜索{place}，寻找遗留的东西。"
    events = [
        event(1, "action.submitted", text=raw, cycle_id="actual-search"),
        event(
            2,
            "check.resolved",
            reason=raw,
            display_text="侦查43/60，成功。",
            cycle_id="actual-search",
        ),
        event(
            3,
            "entity.revealed",
            id="found",
            type="item",
            title=item,
            public_summary=f"{item}记载了失踪人员的情况。",
            cycle_id="actual-search",
        ),
        event(
            4, "keeper.narration", text="发现了一张打开所有门的通行证。", cycle_id="actual-search"
        ),
        event(5, "agent.action_proposed", text="我再检查其他地方。", cycle_id="actual-search"),
        event(6, "action.submitted", text="我检查后门血迹。", cycle_id="other"),
        event(
            7,
            "check.resolved",
            reason="我检查后门血迹。",
            display_text="医学56/1，失败。",
            cycle_id="other",
        ),
    ]
    for _ in range(3):
        selected = select_facts(
            events, [], f"回顾一下，刚才搜索{place}实际发现了什么？我们现在在哪里？"
        )
        text = render_facts(selected)
        assert item + "记载了失踪人员的情况。" in text
        assert "通行证" not in text
        found = next(r for r in selected if r["source_event_seq"] == 3)
        assert found["action_event_seq"] == 1 and found["action_quote"] == raw
        events = json.loads(json.dumps(events))
    events[2]["payload"]["cycle_id"] = "unlinked"
    found = next(r for r in fact_records(events) if r["source_event_seq"] == 3)
    assert "action_quote" not in found


@pytest.mark.parametrize("map_name,paper", [("线路图", "报纸"), ("平面图", "账册")])
def test_coordinated_recall_reserves_each_subject_before_duplicate_checks(map_name, paper):
    events = [
        event(1, "entity.revealed", type="clue", title="便签正面", public_summary="只管前进。"),
        event(
            2, "entity.revealed", type="clue", title="便签背面", public_summary="第三个箱子有钥匙。"
        ),
        event(3, "action.submitted", text=f"我查看{map_name}。", cycle_id="map"),
        event(
            4,
            "entity.revealed",
            type="clue",
            title="涂抹痕迹",
            public_summary="后半部分编号被涂掉。",
            cycle_id="map",
        ),
        event(5, "action.submitted", text="我搜索座位。", cycle_id="search"),
        event(
            6,
            "entity.revealed",
            type="item",
            title=paper,
            public_summary=f"{paper}记载了失踪案件。",
            cycle_id="search",
        ),
        event(7, "check.resolved", reason=f"检查{paper}日期", display_text="日期检定69/20失败。"),
        event(8, "check.resolved", reason="我搜索座位。", display_text="侦查4/60成功。"),
    ]
    query = (
        f"回顾一下，开场便签正反面各写了什么？{map_name}和后来搜索座位实际发现了什么，"
        f"{paper}日期确认了吗？"
    )
    selected = select_facts(events, [], query)
    assert {1, 2, 4, 6, 7} <= {r["source_event_seq"] for r in selected}
    assert len(render_facts(selected)) <= 680


def test_recall_matches_original_check_purpose_not_generic_inventory_noise():
    events = [
        event(1, "action.submitted", text="我检查口袋，确认有没有物品。"),
        event(2, "module.interaction", source_event_seq=1, text="你没有找到原先携带的物品。"),
        event(3, "entity.revealed", public_summary="一份报纸报道了列车上的事故。"),
        event(4, "keeper.narration", text="报纸日期是三天前。"),
        event(
            5,
            "check.resolved",
            reason="我仔细看报纸的报头，确认印刷日期。",
            display_text="图书馆使用检定：62/20，失败。",
        ),
    ]
    for _ in range(3):
        selected = select_facts(
            events, [], "刚才对报纸日期的说法有实际证据吗？请核对原记录，说明我们究竟确认了什么。"
        )
        text = render_facts(selected)
        assert "印刷日期" in text and "62/20，失败" in text
        assert "三天前" not in text and "口袋" not in text
        assert selected[0]["source_event_seq"] == 5
        events = json.loads(json.dumps(events))


@pytest.mark.parametrize("kind", ["observe", "investigate", "wait", "unknown", "converse"])
@pytest.mark.parametrize("destination", ["5号车厢", "东侧大厅"])
@pytest.mark.parametrize(
    "inspection", ["观察座位和地上的东西", "查看有没有人需要帮助", "留意周围是否有异常"]
)
def test_explicit_move_with_observation_survives_wrong_intent_without_item_actions(
    kind, destination, inspection
):
    raw = f"我进入{destination}，{inspection}。"
    context = dict(
        triggering_action={"seq": 1, "payload": {"text": raw}},
        current_participants={"members": {"peer": "周岚"}},
        current_targets=[dict(id="old-note", title="旧便条", type="clue")],
        action_identifiers=dict(
            plan_id="plan",
            cycle_id="cycle",
            actor_member_id="actor",
            actor_character_slot_id="slot",
            current_scene_id="scene",
            expected_navigation_revision=0,
        ),
        approved_exits=[
            dict(
                transition_id="next",
                target_scene_node_id="hall",
                target_public_title=destination,
                available=False,
            )
        ],
    )
    schema = generation_contract(KeeperPlan, context)
    result = restore_output(
        schema(
            parsed_intent={"type": kind},
            focus={"action_clause_ids": ["u2"], "action_target_id": "old-note"}
            if kind != "converse" or inspection.startswith(("观察", "查看"))
            else {
                "action_clause_ids": [],
                "question_clause_ids": ["u1", "u2"],
                "addressee_id": "peer",
            },
            proposed_reveal_entity_ids=["missing-phone"],
        ),
        KeeperPlan,
        context,
    )
    assert result.parsed_intent.type == "move"
    assert result.proposed_transition_id == "next" and result.focus.action_target_id == "hall"
    assert "进入" in result.focus.action
    assert not result.focus.question and result.focus.addressee_id is None
    assert result.proposed_check is None and result.proposed_reveal_entity_ids == []
    assert context["approved_exits"][0]["available"] is False


@pytest.mark.parametrize(
    "raw",
    [
        "我观察进入大厅的门。",
        "如果安全我就进入大厅。",
        "我没有进入大厅。",
        "我没有进入大厅，只是查看有没有人在。",
        "我问是否可以进入大厅。",
    ],
)
def test_observation_or_hypothetical_destination_does_not_become_movement(raw):
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    context = dict(
        triggering_action={"seq": 1, "payload": {"text": raw}},
        approved_exits=[
            dict(transition_id="next", target_scene_node_id="hall", target_public_title="大厅")
        ],
    )
    result = restore_output(plan, KeeperPlan, context)
    assert result.parsed_intent.type != "move" and result.proposed_transition_id is None


@pytest.mark.parametrize(
    "raw,keeps_discovery",
    [
        ("我把刚收好的钥匙交给许宁，请他保管。", False),
        ("我先搜索钥匙，再交给许宁。", True),
    ],
)
def test_transfer_prompt_keeps_instances_and_operation_rules_not_unrelated_discovery(
    raw, keeps_discovery
):
    from app.agents.action_runtime import planning_prompt

    context = dict(
        triggering_action={"payload": {"text": raw}},
        characters=[
            {"member_id": "recipient", "name": "队友", "skill_values": {"spot_hidden": 50}}
        ],
        inventory_state={"complete": True, "holders": [], "members": [{"held": []}]},
        check_requirements=[{"entity_id": "hidden-map", "successful_check": "int"}],
        module_interactions=[
            {"entity_id": "key", "interactions": [{"id": "give", "host_review": False}]}
        ],
    )
    prompt = planning_prompt(context)
    assert ("check_requirements" in prompt) == keeps_discovery
    assert prompt["inventory_state"] == context["inventory_state"]
    assert prompt["module_interactions"] == context["module_interactions"]
    assert prompt["characters"][0]["member_id"] == "recipient"
    assert ("skill_values" in prompt["characters"][0]) == keeps_discovery


@pytest.mark.parametrize("item", ["钥匙", "铜牌"])
@pytest.mark.parametrize("confirmation", ["received", "wrong_recipient", "stored"])
def test_receiving_already_transferred_item_is_speech_not_another_action(item, confirmation):
    from app.agents.action_runtime import planning_prompt
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy
    from app.preparation.inventory import held_item_acknowledgement

    view = {
        "holders": [
            {"instance_id": "instance", "item_id": "item", "holder_id": "peer", "title": item}
        ],
        "known_items": [{"id": "item", "names": [item]}],
    }
    raw = (
        f"我接过{item}，确认保管好。现在我们可以继续寻找其他线索了。"
        if confirmation == "received"
        else f"我确认{item}已交给你保管，需要我帮忙查看{item}的用途吗？"
    )
    if confirmation == "stored":
        raw = f"我确认{item}已安全保管，可以随时取用。\n{item}已经交给你了，需要的时候随时叫我。"
    assert held_item_acknowledgement(raw, view, "peer")
    assert not held_item_acknowledgement(raw, view, "other")
    assert not held_item_acknowledgement(f"我接过{item}，打开门。", view, "peer")
    assert not held_item_acknowledgement(f"我已收到{item}并打开门。", view, "peer")
    assert not held_item_acknowledgement(f"我尚未保管好{item}。", view, "peer")
    decision = TeammateDecision(
        mode="assist", action_text=raw, related_player_action_seq=12, confidence=1
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text=f"我将{item}交给队友。",
        player_intent="interact",
        public_ids=set(),
        action_seq=12,
        fingerprint="current",
        inventory_state=view,
        actor_id="peer",
    )
    assert result.accepted and decision.mode == "speak" and decision.action_text is None
    assert decision.item_instance_ids == ["instance"]
    assert "我目前持有：" + item in decision.speech_text and "交给你" not in decision.speech_text
    context = dict(
        triggering_action={
            "type": "agent.action_proposed",
            "actor_member_id": "peer",
            "payload": {"text": raw},
        },
        inventory_state=view,
        check_requirements=[{"entity_id": "hidden"}],
        module_interactions=[{"entity_id": "item"}],
    )
    prompt = planning_prompt(context)
    assert prompt["completed_item_acknowledgement"]
    assert "check_requirements" not in prompt and "module_interactions" not in prompt
    assert prompt["inventory_state"] == view


@pytest.mark.parametrize("verb", ["交给", "交还给", "交回给", "归还给"])
def test_handover_request_does_not_assign_the_requester_observation_to_the_peer(verb):
    from app.preparation.action_authority import requested_action_kinds
    from app.preparation.inventory import requested_handover

    raw = f"苏宁，你先把铜牌{verb}我，我来看看。"
    view = dict(
        members=[dict(id="p", name="陆衡"), dict(id="peer", name="苏宁")],
        known_items=[dict(id="token", names=["铜牌"])],
        holders=[dict(instance_id="token:1", item_id="token", holder_id="peer", title="铜牌")],
    )
    assert requested_action_kinds(raw) == ["give"]
    assert requested_action_kinds("苏宁，请告诉我你的看法。") == []
    assert requested_handover(raw, view, "peer", "p")["action_text"] == "我把铜牌交给陆衡。"
    assert requested_action_kinds(f"苏宁，你不要把铜牌{verb}我。") == []
    assert set(requested_action_kinds(f"苏宁，你观察房间，再把铜牌{verb}我。")) == {
        "give",
        "observe",
    }


def test_explicit_peer_action_request_requires_response_but_does_not_force_agreement():
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy

    kwargs = dict(
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text="苏宁，请把铜牌交给我。",
        player_intent="converse",
        public_ids=set(),
        action_seq=12,
        fingerprint="current",
        explicit_action_request=True,
    )
    policy = TeammateBehaviorPolicy()
    passed = TeammateDecision(mode="pass", related_player_action_seq=12, confidence=1)
    assert policy.validate(passed, **kwargs).reason == "action_request_requires_response"
    refusal = TeammateDecision(
        mode="speak",
        speech_text="我现在不想交出这件东西。",
        related_player_action_seq=12,
        confidence=1,
    )
    assert policy.validate(refusal, **kwargs).accepted
    kwargs["explicit_action_request"] = False
    assert policy.validate(passed, **kwargs).accepted


@pytest.mark.parametrize(
    "scene,raw",
    [
        ("2号车厢", "我放轻脚步，沿车厢内的通道潜行到前门，尽量避开喘息声。"),
        ("2号车厢", "现在去这节车厢内的前门，仍然放轻脚步潜行。"),
        ("西侧大厅", "我沿大厅内的小道潜行到西门。"),
        ("档案室", "我走到这间房间内的另一端。"),
    ],
)
def test_local_deictic_destination_preserves_attempt_without_selecting_an_exit(scene, raw):
    from app.agents.action_policy import local_scene_movement

    targets = [dict(id="scene", type="scene", title=scene)]
    assert local_scene_movement(raw, targets)
    assert not local_scene_movement("如果安全，" + raw, targets)
    assert not local_scene_movement("我进入3号车厢内。", targets)
    assert not local_scene_movement(raw, [])
    plan = plan_for(raw, "move", "wrong-exit")
    plan.focus = TurnFocus(action=raw, action_target_id="wrong-exit")
    plan.proposed_transition_id = "wrong"
    context = dict(
        triggering_action={"seq": 1, "payload": {"text": raw}},
        current_targets=targets,
        action_identifiers={"current_scene_id": "scene"},
        approved_exits=[],
    )
    result = restore_output(plan, KeeperPlan, context)
    assert result.parsed_intent.type == "interact" and result.focus.action == raw
    assert result.focus.action_target_id == "scene" and result.proposed_transition_id is None
    assert not result.needs_clarification


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("我观察周围，确认声响吸引了它。现在我们可以尝试过去。", False),
        ("我留意到黑包可能就在前门附近，我们一起去查看一下。", False),
        ("我检查前门附近的地板。", True),
    ],
)
def test_group_suggestion_must_not_be_published_as_a_teammate_action(text, accepted):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy

    decision = TeammateDecision(
        mode="assist", action_text=text, related_player_action_seq=12, confidence=1
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text="我投出帽子制造声响。",
        player_intent="interact",
        public_ids=set(),
        action_seq=12,
        fingerprint="current",
    )
    assert result.accepted is accepted
    if not accepted:
        assert result.reason == "suggestion_requires_speak_mode"


@pytest.mark.parametrize(
    "scene,item,next_scene", [("2号车厢", "鞋", "先头车厢"), ("旧大厅", "帽子", "东塔")]
)
def test_compound_recall_reserves_route_receipt_beside_key_and_door_question(
    scene, item, next_scene
):
    from app.memory.facts import bounded_facts

    history = [
        event(
            0, "entity.revealed", id="scene-id", type="scene", title=scene, public_summary="入口。"
        ),
        event(1, "scene.updated", scene_title=scene),
        event(
            2, "action.submitted", scene_id="scene-id", text=f"我留在原地，将{item}扔向远处引开它。"
        ),
        event(
            3, "module.interaction", source_event_seq=2, text="响声吸引了它，前门出现通过的机会。"
        ),
        event(4, "scene.updated", scene_title=next_scene),
        event(
            5,
            "entity.revealed",
            id="door",
            type="location",
            title="驾驶室门",
            public_summary="驾驶室门上锁。",
        ),
        event(6, "action.submitted", text="我使用钥匙打开驾驶室的门。"),
        event(7, "module.interaction", source_event_seq=6, text="驾驶室门已用钥匙打开。"),
    ]
    history.extend(
        event(i, "npc.spoke", actor_name="乘务员", text="钥匙原来由我保管，驾驶室门需要钥匙。")
        for i in range(8, 15)
    )
    facts = select_facts(
        history, [], f"我们刚才是怎样通过{scene}的？钥匙现在由谁保管，驾驶室门到底有没有打开？"
    )
    current = [
        dict(id="state:inventory", kind="current_state", text="同伴持有钥匙。"),
        dict(id="state:location", kind="current_state", text=f"当前位置：{next_scene}。"),
    ]
    answer = render_facts(bounded_facts([*current, *facts]))
    assert item in answer and "前门出现通过的机会" in answer
    assert "驾驶室门已用钥匙打开" in answer and "同伴持有钥匙" in answer
    old_description = render_facts([r for r in fact_records(history) if r["source_event_seq"] == 5])
    assert "当时公开的描述" in old_description
    projection = dict(
        id="door",
        type="location",
        title="驾驶室门",
        revealed_event_seq=5,
        public_summary="投影不能覆盖原事件。",
    )
    assert "投影不能覆盖" not in render_facts(fact_records(history, [projection]))


@pytest.mark.parametrize("invented", ["一枚橡皮擦和一张名片", "一只蓝色罗盘", "三张写有号码的纸鹤"])
@pytest.mark.parametrize(
    "mode", ["act", "assist_with_suggestion", "speak", "possession_question", "possession_action"]
)
def test_inventory_probe_cannot_publish_unregistered_discovery(invented, mode):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy, output_text

    text = f"我检查了一下口袋，发现只有{invented}。"
    is_action = mode in {"act", "assist_with_suggestion", "possession_action"}
    if mode == "assist_with_suggestion":
        text += "不过这些或许能帮上忙。"
    asked_question = mode.startswith("possession")
    view = {
        "complete": True,
        "holders": [],
        "known_items": [],
        "members": [
            {"id": "peer", "name": "林舟", "held": [], "starting_belongings": "undetermined"}
        ],
    }
    decision = TeammateDecision(
        mode="assist" if mode == "assist_with_suggestion" else "act" if is_action else "speak",
        action_text=text if is_action else None,
        speech_text=(f"我有{invented}。" if asked_question else text) if not is_action else None,
        related_player_action_seq=12,
        confidence=1,
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text="林舟，你有什么能用的东西？"
        if asked_question
        else "林舟，请检查你的口袋，确认还有哪些随身物能用。",
        player_intent="converse",
        public_ids=set(),
        action_seq=12,
        fingerprint="empty",
        explicit_action_request=not asked_question,
        requested_operations=[] if asked_question else ["search"],
        inventory_state=view,
        actor_id="peer",
    )
    assert result.accepted and invented not in output_text(decision)
    assert (
        "检查自己的口袋" in output_text(decision)
        if mode in {"act", "assist_with_suggestion", "speak"}
        else "还没有确认" in output_text(decision)
    )
    assert not decision.item_instance_ids
    if asked_question:
        assert decision.mode == "speak" and decision.action_text is None


@pytest.mark.parametrize("verb", ["看看", "观察"])
@pytest.mark.parametrize("invented", ["除了手机外，还有一支随身手电筒", "一台电台和一枚红色罗盘"])
@pytest.mark.parametrize("mode", ["act", "assist"])
def test_autonomous_observation_of_own_pockets_cannot_publish_its_invented_result(
    verb, invented, mode
):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy, output_text

    view = dict(complete=True,
        holders=[dict(instance_id="phone:p",item_id="phone",holder_id="p",title="随身手机")],
        known_items=[dict(id="phone",names=["手机","随身手机"])],
        members=[dict(id="p",name="沈川",held=["phone:p"],starting_belongings="settled"),
                 dict(id="peer",name="顾遥",held=[],starting_belongings="undetermined")])
    decision = TeammateDecision(mode=mode, action_type="observe",
        action_text=f"我{verb}自己的口袋，发现{invented}。这可能对探索有帮助。",
        related_player_action_seq=12,confidence=1, fact_ids=["event:98"],
        related_public_entity_ids=["phone", "unregistered-item"],
        item_instance_ids=["phone:p", "unregistered-item"])
    result = TeammateBehaviorPolicy().validate(decision,state=BehaviorState(),
        recent_outputs=[],other_outputs=[],player_text="我检查自己的口袋，看看保留了什么。",
        player_intent="investigate",public_ids={"phone"},action_seq=12,fingerprint="p-has-phone",
        explicit_action_request=False,requested_operations=[],inventory_state=view,
        actor_id="peer",requester_id="p")
    assert result.accepted
    assert decision.action_text == "我检查自己的口袋与随身物，确认实际保留下来的物品。"
    assert invented not in output_text(decision) and not decision.item_instance_ids
    assert not decision.fact_ids and not decision.related_public_entity_ids
    assert view["holders"][0]["holder_id"] == "p"


@pytest.mark.parametrize("verb", ["看看", "观察"])
def test_requested_pocket_observation_allows_its_normalized_actual_search(verb):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy
    from app.preparation.action_authority import requested_action_kinds

    raw = f"顾遥，你也{verb}自己的口袋和随身物，确认实际保留下了什么。"
    requested = requested_action_kinds(raw)
    assert "search" in requested
    decision = TeammateDecision(mode="assist",action_type="investigate",
        action_text="我检查自己的口袋与随身物，确认实际保留下来的物品。",
        related_player_action_seq=12,confidence=1)
    result = TeammateBehaviorPolicy().validate(decision,state=BehaviorState(),
        recent_outputs=[],other_outputs=[],player_text=raw,player_intent="converse",
        public_ids=set(),action_seq=12,fingerprint="empty",explicit_action_request=True,
        requested_operations=requested,inventory_state={"complete":True,"holders":[],
            "members":[dict(id="peer",name="顾遥",held=[],starting_belongings="undetermined")]},
        actor_id="peer",requester_id="p")
    assert result.accepted and decision.mode == "assist"
    assert not decision.item_instance_ids


def test_unsettled_inventory_narration_uses_state_and_keeps_actual_pending_check():
    view = {
        "holders": [],
        "members": [{"id": "peer", "name": "林舟", "starting_belongings": "undetermined"}],
    }
    context = dict(
        triggering_action={"actor_member_id": "peer", "payload": {"text": "我检查口袋。"}},
        inventory_state=view,
        response_brief={"inventory_probe": True, "responder": {"kind": "keeper"}},
        public_tool_results={"events": []},
    )
    draft = KeeperNarration(public_narration="你拿着刚找到的蓝色罗盘。")
    fixed = restore_output(draft, KeeperNarration, context)
    assert (
        "罗盘" not in fixed.public_narration and "林舟的随身物还没有确认" in fixed.public_narration
    )
    context["public_tool_results"]["events"] = [
        event(1, "entity.revealed", public_summary="一只蓝色罗盘。")
    ]
    assert "还没有确认" in restore_output(draft, KeeperNarration, context).public_narration
    context["public_tool_results"]["events"] = [event(1, "check.requested")]
    assert "请先完成" in restore_output(draft, KeeperNarration, context).public_narration


@pytest.mark.parametrize("target", ["hall", "hall_entity"])
def test_own_inventory_attempt_can_select_source_check_without_model_proposing_luck(target):
    from app.preparation.adjudication import matches_action_focus
    from app.preparation.runtime_schemas import ModuleInteraction

    raw = "我检查自己的口袋与随身物，确认实际保留下来的物品。"
    plan = plan_for(raw, "investigate", target)
    plan.focus = TurnFocus(action=raw, action_target_id=target)
    rule = ModuleInteraction(
        id="initial_compass",
        instruction="检查起始随身物。",
        source_block_ids=["fixture-source"],
        public_result="按实际幸运结果登记。",
        inventory_operation="initial",
        item_id="compass",
        check_name="luck",
        kp_enabled=True,
    )
    assert plan.proposed_check is None
    assert matches_action_focus(plan, "hall", "hall_entity", rule)
    plan.focus.action = "我检查墙上的布告。"
    assert not matches_action_focus(plan, "hall", "hall_entity", rule)
    plan.focus.action = "如果我检查自己的口袋，会有什么？"
    assert not matches_action_focus(plan, "hall", "hall_entity", rule)


def test_inventory_prose_is_not_recycled_as_improvisation_but_other_details_remain():
    from copy import deepcopy

    from app.memory.events import relevant_incidental_memories, story_events

    history = [
        event(1, "action.submitted", text="我观察墙壁。"),
        event(
            2,
            "keeper.narration",
            text="墙漆有裂纹。",
            incidental_source="kp_improvisation",
            incidental_details=["墙漆有裂纹。"],
            scene_id="hall",
        ),
        event(3, "agent.action_proposed", text="我检查了一下口袋，发现只有一张陌生名片。"),
        event(
            4,
            "keeper.narration",
            text="名片在你手里。",
            incidental_source="kp_improvisation",
            incidental_details=["名片在你手里。"],
            scene_id="hall",
        ),
    ]
    original = deepcopy(history)
    projected, _ = story_events(history)
    assert 4 not in [e["seq"] for e in projected] and history == original
    memories = relevant_incidental_memories(history, "口袋里的名片", "hall")
    assert [r["text"] for r in memories] == ["墙漆有裂纹。"]


@pytest.mark.parametrize("item", ["随身手机", "镶有蓝色玻璃的旧铜钥匙"])
def test_agreed_handover_binds_actual_instance_and_requester_instead_of_repeating_possession(item):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy
    from app.preparation.inventory import requested_handover

    view = {
        "holders": [
            {
                "instance_id": "actual-instance",
                "item_id": "item",
                "title": item,
                "holder_id": "peer",
            }
        ],
        "known_items": [{"id": "item", "names": [item]}],
        "members": [{"id": "peer", "name": "林舟"}, {"id": "player", "name": "乔安"}],
    }
    request = f"林舟，请把{item}交给我。"
    decision = TeammateDecision(
        mode="assist",
        action_text=f"我目前持有{item}，如果需要可以用它。",
        related_player_action_seq=12,
        confidence=1,
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text=request,
        player_intent="converse",
        public_ids={"item"},
        action_seq=12,
        fingerprint="held",
        explicit_action_request=True,
        requested_operations=["give"],
        inventory_state=view,
        actor_id="peer",
        requester_id="player",
    )
    assert result.accepted and decision.action_text == f"我把{item}交给乔安。"
    assert decision.item_instance_ids == ["actual-instance"]
    assert requested_handover(request, {**view, "holders": []}, "peer", "player") is None
    assert (
        requested_handover(request, {**view, "holders": view["holders"] * 2}, "peer", "player")
        is None
    )
    assert requested_handover(f"林舟，不要把{item}交给我。", view, "peer", "player") is None


@pytest.mark.parametrize("name", ["苏宁", "林女士"])
@pytest.mark.parametrize(
    "request_text", ["我是请你现在实际检查自己的随身物", "我想请你查看口袋", "麻烦你检查随身物"]
)
def test_clarified_teammate_request_cannot_reselect_requesters_starting_item(name, request_text):
    from types import SimpleNamespace

    from app.preparation.action_authority import freeze_action, teammate_request
    from app.preparation.search import guard_initial_reselection
    from app.rooms.schemas import SessionStateV1

    raw = f"{name}，{request_text}。"
    members = {"p": "陆衡", "peer": name}
    assert teammate_request(raw, members, "p") == "peer"
    assert teammate_request(f"{name}，我检查自己的口袋。", members, "p") is None
    plan = plan_for(raw, "investigate", "torch")
    plan.focus = TurnFocus(action=raw, action_target_id="torch")
    runtime = SessionStateV1().module_runtime
    runtime.initial_belongings["p"] = dict(chosen_item_id="phone", item_ids=[], check_ids=["fixed"])
    before = runtime.model_dump()
    plan.action_authority = freeze_action(plan, raw, "p", 20, "scene", {}, runtime, members)
    assert plan.action_authority["kinds"] == []
    assert not guard_initial_reselection(plan, SimpleNamespace(actor_member_id="p"), runtime)
    assert runtime.model_dump() == before


@pytest.mark.parametrize(
    "speech,expected",
    [
        ("我检查了一下自己的口袋，目前没有发现物品。不过我们可以找箱子。", "act"),
        ("我这就检查自己的随身物。", "act"),
        ("我不想现在检查自己的口袋。", "speak"),
        ("我刚才检查过自己的口袋。", "speak"),
    ],
)
def test_explicit_probe_agreement_submits_attempt_without_inventing_its_result(speech, expected):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy

    view = dict(
        holders=[],
        known_items=[],
        members=[dict(id="peer", name="苏宁", starting_belongings="undetermined")],
    )
    decision = TeammateDecision(
        mode="speak", speech_text=speech, related_player_action_seq=12, confidence=1
    )
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text="苏宁，你也检查一下自己的口袋和随身物，看看实际留下了什么。",
        player_intent="converse",
        public_ids=set(),
        action_seq=12,
        fingerprint="empty",
        explicit_action_request=True,
        requested_operations=["search", "observe"],
        inventory_state=view,
        actor_id="peer",
        requester_id="p",
    )
    assert result.accepted and decision.mode == expected
    if expected == "act":
        assert decision.action_text == "我检查自己的口袋与随身物，确认实际保留下来的物品。"
        assert decision.speech_text is None and not decision.item_instance_ids
    assert view["holders"] == []


@pytest.mark.parametrize("item,name", [("手机", "苏宁"), ("旧铜灯", "林女士")])
@pytest.mark.parametrize("polite", ["能", "能不能", "可否", "可不可以"])
def test_polite_handover_question_binds_agreed_actual_instance(item, name, polite):
    from app.preparation.action_authority import requested_action_kinds, teammate_request
    from app.preparation.inventory import requested_handover

    raw = f"{name}，{polite}把{item}先交给我吗？我来照明，你留意周围的动静。"
    view = dict(
        holders=[dict(instance_id="actual", item_id="item", title=item, holder_id="peer")],
        known_items=[dict(id="item", names=[item])],
        members=[dict(id="p", name="陆衡"), dict(id="peer", name=name)],
    )
    assert teammate_request(raw, {"p": "陆衡", "peer": name}, "p") == "peer"
    assert requested_action_kinds(raw) == ["give"]
    transfer = requested_handover(raw, view, "peer", "p")
    assert (
        transfer["instance_id"] == "actual" and transfer["action_text"] == f"我把{item}交给陆衡。"
    )
    assert requested_handover(raw, {**view, "holders": []}, "peer", "p") is None
    assert requested_handover(f"{name}，不要把{item}交给我。", view, "peer", "p") is None


def test_rejected_handover_cannot_be_narrated_as_received_even_with_correct_claim():
    from app.agents.narration import fallback_narration

    brief = dict(
        attempt="我将铜灯交给你。", allowed_facts=[dict(id="lamp", text="你的铜灯仍在身上。")]
    )
    context = dict(
        response_brief=brief, public_tool_results=dict(events=[], blocked_operations=True)
    )
    result = restore_output(
        KeeperNarration(public_narration="你接过铜灯，握在手里。"), KeeperNarration, context
    )
    assert result.public_narration == "这次动作没有完成，当前状态未因这次尝试改变。"
    assert (
        fallback_narration("interact", context["public_tool_results"], "大厅", brief=brief)
        == result.public_narration
    )
    brief.update(attempt="", responder=dict(kind="teammate", name="林女士"))
    assert (
        fallback_narration("converse", {"events": []}, "大厅", brief=brief)
        == "你向林女士说出了这番话。"
    )


def test_actual_teammate_handover_label_is_not_only_assistance():
    raw = "我将铜灯交给你，你负责照明。"
    plan = plan_for(raw, "assist", "lamp")
    plan.focus = TurnFocus(action="我将铜灯交给你，", action_target_id="lamp")
    result = restore_output(
        plan,
        KeeperPlan,
        dict(
            triggering_action=dict(type="agent.action_proposed", payload=dict(text=raw)),
            current_targets=[],
        ),
    )
    assert result.parsed_intent.type == "interact" and result.focus.action == "我将铜灯交给你，"


@pytest.mark.parametrize(
    "scene,local", [("7号车厢", "本节车厢的后门"), ("旧大厅", "这间大厅的另一端")]
)
@pytest.mark.parametrize("question", ["有没有文字", "是否有痕迹", "是不是有刻痕"])
def test_local_movement_with_inspection_question_discards_unrelated_exit(scene, local, question):
    from app.agents.action_policy import local_scene_movement

    raw = f"我走到{local}，近距离观察门面和周围{question}。"
    targets = [dict(id="scene", type="scene", title=scene)]
    assert local_scene_movement(raw, targets)
    assert not local_scene_movement("如果安全，" + raw, targets)
    assert not local_scene_movement("我不要走到" + local + "。", targets)
    plan = plan_for(raw, "investigate", "scene")
    plan.focus = TurnFocus(action=raw, action_target_id="scene")
    plan.proposed_transition_id = "wrong-exit"
    plan.proposed_reveal_entity_ids = ["scene"]
    fixed = restore_output(
        plan,
        KeeperPlan,
        dict(
            triggering_action=dict(payload=dict(text=raw)),
            current_targets=targets,
            action_identifiers=dict(current_scene_id="scene"),
        ),
    )
    assert fixed.proposed_transition_id is None and fixed.focus.action_target_id == "scene"
    assert fixed.proposed_reveal_entity_ids == []
    assert not fixed.needs_clarification and fixed.focus.action == raw


@pytest.mark.parametrize("scene,destination", [("6号车厢", "5号车厢"), ("西大厅", "南楼")])
@pytest.mark.parametrize("connector", ["打开前门", "推开门后", ""])
def test_authorization_keeps_named_exit_after_crossing_current_room(scene, destination, connector):
    from test_action_adjudication import facts_for

    from app.agents.action_policy import ActionPolicyValidator

    raw = f"我穿过{scene}，{connector}进入{destination}。"
    facts = facts_for(raw)
    facts.local_entity_ids = {"scene"}
    facts.approved_entities = {"scene": dict(id="scene", type="scene", title=scene)}
    facts.transitions = {"forward": dict(transition_id="forward", target_scene_node_id="next",
        target_public_title=destination, approved=True)}
    plan = plan_for(raw, "move", "next")
    validator = ActionPolicyValidator()
    assert validator.validate_intent(plan.parsed_intent, plan, facts) is None
    facts.raw_text = f"我穿过{scene}，走到本车厢的前门。"
    plan.parsed_intent.evidence_quote = facts.raw_text
    assert validator.validate_intent(plan.parsed_intent, plan, facts)[0] == "clarification_required"
    facts.raw_text = plan.parsed_intent.evidence_quote = raw
    facts.transitions["forward"]["approved"] = False
    assert validator.validate_intent(plan.parsed_intent, plan, facts)[0] == "clarification_required"


@pytest.mark.parametrize("scene,destination", [("2号车厢", "先头车厢"), ("旧大厅", "东厅")])
def test_crossing_current_scene_then_entering_named_exit_is_a_transition(scene, destination):
    from app.agents.action_policy import local_scene_movement
    from app.agents.narration import fallback_narration, response_brief

    raw = f"趁声音引开它们，我穿过{scene}的前门，进入{destination}。"
    targets = [dict(id="current", type="scene", title=scene)]
    exits = [
        dict(transition_id="forward", target_scene_node_id="next", target_public_title=destination)
    ]
    assert not local_scene_movement(raw, targets, exits)
    plan = plan_for(raw, "interact", "passage")
    plan.focus = TurnFocus(action=raw, action_target_id="passage")
    context = dict(
        triggering_action=dict(seq=1, payload=dict(text=raw)),
        current_targets=targets,
        action_identifiers=dict(current_scene_id="current"),
        approved_exits=exits,
        module=dict(current_scene=dict(node_id="current", title=scene)),
    )
    fixed = restore_output(plan, KeeperPlan, context)
    assert fixed.parsed_intent.type == "move"
    assert fixed.proposed_transition_id == "forward" and fixed.focus.action_target_id == "next"
    # Even the old wrong intent cannot turn a missing receipt into an arrival.
    brief, _ = response_brief(plan, context, {"events": []})
    assert brief["movement_requested"]
    output = restore_output(
        KeeperNarration(public_narration=f"你成功进入{destination}。"),
        KeeperNarration,
        dict(response_brief=brief, public_tool_results={"events": []}),
    )
    assert output.public_narration == f"本次没有完成转场；当前位置仍是{scene}。"
    assert (
        fallback_narration("interact", {"events": []}, scene, brief=brief)
        == output.public_narration
    )


@pytest.mark.parametrize(
    "raw",
    [
        "刚才真的完成转场了吗？请核对我们目前的位置。",
        "之前真的进入东厅了没有？",
        "请说明当前的位置。",
        "先前把铜灯交给她了吗？",
    ],
)
def test_completed_operation_confirmation_is_readonly_without_a_new_action(raw):
    assert readonly_recall(raw)
    assert not readonly_recall(raw + "现在我进入东厅。")
    assert not readonly_recall(raw + "现在我查看墙上的报纸。")


@pytest.mark.parametrize("item", ["便签", "木牌"])
def test_initial_written_front_remains_available_after_start_and_snapshot_selection(item):
    from app.memory.events import story_events

    history = [
        event(
            1,
            "entity.revealed",
            id="front",
            type="clue",
            title=item + "正面",
            public_summary="沿东廊前进。",
        ),
        event(2, "game.started"),
        event(3, "action.submitted", text=f"我读清{item}正面的字，再翻过来查看背面。"),
        event(
            4,
            "entity.revealed",
            id="back",
            type="clue",
            title=item + "背面",
            public_summary="第三个箱子藏着铜牌。",
        ),
        event(5, "module.interaction", source_event_seq=3, text="第三个箱子藏着铜牌。"),
        event(6, "npc.spoke", actor_name="守门人", text="铜牌曾经由我保管。"),
        event(7, "scene.updated", initialization=True, scene_title="恢复位置"),
    ]
    assert 1 not in {e["seq"] for e in story_events(history)[0]}
    records = fact_records(history)
    assert any(r["id"] == "event:1" and r["text"] == "沿东廊前进。" for r in records)
    assert not any(r["id"] == "event:7" for r in records)
    selected = select_facts(
        history, [], f"开场{item}的正反面分别写了什么？守门人关于铜牌实际说过哪些话？"
    )
    answer = render_facts(selected)
    assert "沿东廊前进。" in answer and "第三个箱子藏着铜牌。" in answer
    assert "铜牌曾经由我保管。" in answer
    assert fact_records(history + [event(8, "snapshot.loaded", source_seq=0)]) == []


@pytest.mark.parametrize(
    "document,npc,item", [("便签", "乘务员", "钥匙"), ("木牌", "门卫", "铜牌")]
)
@pytest.mark.parametrize("wording", ["正反面分别写了什么", "正面和背面到底写了什么"])
def test_requested_print_survives_long_related_action_and_repeated_testimony(
    document, npc, item, wording
):
    from app.memory.facts import bounded_facts

    history = [
        event(1, "entity.revealed", type="clue", title=document + "正面",
              public_summary="沿东廊前进。"),
        event(2, "game.started"),
        event(3, "action.submitted", cycle_id="inspect", text=(
            f"我先观察醒来的地方，拿起身旁的{document}，仔细查看正面和背面分别写了什么，"
            "正面和背面到底写了什么。")),
        event(4, "entity.revealed", cycle_id="inspect", type="clue", title=document + "背面",
              public_summary=f"第三个箱子藏着{item}。"),
    ]
    history += [event(i, "npc.spoke", actor_name=npc,
        text=f"{item}曾经由我保管。至于你问的第{i}件事，我不能确定。") for i in range(5, 10)]
    history += [event(10, "snapshot.loaded", source_seq=9)]
    query = f"开场{document}的{wording}？{npc}关于{item}实际说过哪些话？"
    selected = bounded_facts(select_facts(history, [], query))
    assert {1, 4} <= {r["source_event_seq"] for r in selected}
    restored = restore_output(
        KeeperNarration(public_narration="正面是空白。", fact_ids=[selected[0]["id"]]),
        KeeperNarration, {"readonly_recall": True, "fact_evidence": selected},
    )
    assert "沿东廊前进。" in restored.public_narration
    assert f"第三个箱子藏着{item}。" in restored.public_narration
    assert f"{npc}当时说：" in restored.public_narration
    assert "正面是空白" not in restored.public_narration


@pytest.mark.parametrize(
    "door,panel,npc",
    [("驾驶室门", "控制面板", "乘务员"), ("北厅木门", "墙上保险柜", "守夜人")],
)
def test_compound_current_state_selects_actual_receipts_before_related_testimony(door, panel, npc):
    from app.memory.facts import bounded_facts, recalled_location

    history = [
        event(1, "action.submitted", text=f"我用钥匙打开{door}。"),
        event(2, "module.interaction", source_event_seq=1, text=f"{door}已用钥匙打开。"),
        event(3, "action.submitted", text=f"我把第二把钥匙插进{panel}的锁孔。"),
        event(4, "module.interaction", source_event_seq=3, text=f"你用第二把钥匙解锁了{panel}。"),
    ]
    history += [event(i, "npc.spoke", actor_name=npc, text=(
        f"我曾保管{door}钥匙和{panel}钥匙。至于现在在哪里、分别是什么实际状态，"
        f"以及第{i}件已经完成的动作，我不清楚。")) for i in range(5, 10)]
    query = f"我们现在在哪里？{door}和{panel}分别是什么实际状态，依据哪些已经完成的动作？"
    selected = bounded_facts(recalled_location({"scene_title": "前厅"}, query)
                             + select_facts(history, [], query))
    body = render_facts(selected)
    assert {2, 4} <= {r["source_event_seq"] for r in selected}
    assert f"{door}已用钥匙打开。" in body
    assert f"你用第二把钥匙解锁了{panel}。" in body
    assert "当前位置：前厅。" in body


@pytest.mark.parametrize("map_name,document,npc", [
    ("线路示意图", "报纸", "乘务员"), ("仓库平面图", "货运清单", "管理员"),
])
@pytest.mark.parametrize("wording", [
    "回顾{map_name}和{document}的实际调查。哪些内容查明了，哪些没有，依据是什么？",
    "核对之前对{map_name}与{document}的调查，分别有哪些原文和检定结果？",
])
def test_investigation_recall_keeps_revealed_content_and_failed_checks(
    map_name, document, npc, wording
):
    from app.memory.facts import bounded_facts

    history = [
        event(1, "game.started"),
        event(2, "check.resolved", reason=f"我仔细辨认{map_name}的编号和涂抹痕迹。",
              text="智力检定：79/60，失败，未通过。"),
        event(3, "agent.action_proposed", cycle_id="search",
              text="我观察东厅的环境，看看有没有异常或隐藏的线索。"),
        event(4, "entity.revealed", cycle_id="search", type="item", title="遗留的" + document,
              public_summary="记载：夜班失联，原因未明。"),
        event(5, "check.resolved", reason=f"我仔细查看{document}的报头和日期，确认记录时间。",
              text="图书馆使用检定：86/20，失败，未通过。"),
    ]
    history += [event(i, "npc.spoke", actor_name=npc, text=(
        f"至于你问的第{i}件事，哪些内容查明了，哪些没有，实际调查依据是什么，"
        "我不能确定更多细节。")) for i in range(6, 11)]
    history += [event(11, "snapshot.loaded", source_seq=10)]
    query = wording.format(map_name=map_name, document=document)
    selected = bounded_facts(select_facts(history, [], query))
    assert {2, 4, 5} <= {r["source_event_seq"] for r in selected}
    restored = restore_output(
        KeeperNarration(public_narration="什么都没查明，日期已经确定。",
                        fact_ids=[selected[0]["id"]]),
        KeeperNarration, {"readonly_recall": True, "fact_evidence": selected},
    )
    assert "夜班失联，原因未明。" in restored.public_narration
    assert "79/60，失败" in restored.public_narration
    assert "86/20，失败" in restored.public_narration
    assert "日期已经确定" not in restored.public_narration


@pytest.mark.parametrize(
    "name,object_name", [("苏宁", "前门附近的黑色包"), ("林女士", "壁炉后面的红盒子")]
)
def test_agreed_search_retains_requested_target_instead_of_generic_lighting(name, object_name):
    from app.agents.adjudication_schemas import BehaviorState
    from app.agents.behavior import TeammateBehaviorPolicy
    from app.preparation.action_authority import requested_search_attempt

    request_text = f"{name}，你也独立找一下{object_name}，留意里面的信件。我来照明。"
    decision = TeammateDecision(
        mode="assist",
        action_type="assist",
        action_text="我用手机的光束检查地面。",
        speech_text="我用手机检查地面。",
        related_player_action_seq=12,
        confidence=1,
    )
    view = dict(holders=[], known_items=[], members=[dict(id="peer", name=name)])
    result = TeammateBehaviorPolicy().validate(
        decision,
        state=BehaviorState(),
        recent_outputs=[],
        other_outputs=[],
        player_text=request_text,
        player_intent="converse",
        public_ids=set(),
        action_seq=12,
        fingerprint="empty",
        explicit_action_request=True,
        requested_operations=["search"],
        inventory_state=view,
        actor_id="peer",
        requester_id="p",
    )
    assert result.accepted and decision.action_text == f"我搜索一下{object_name}，留意里面的信件。"
    assert not decision.speech_text and not decision.item_instance_ids
    assert "手机" not in decision.action_text and "我来照明" not in decision.action_text
    assert requested_search_attempt(f"{name}，不要找{object_name}。") is None
    assert requested_search_attempt(f"如果有空，请{name}找{object_name}。") is None


@pytest.mark.parametrize("scene,destination,item", [
    ("2号车厢", "先头车厢", "鞋"), ("东侧长廊", "南楼门厅", "围巾上的铜扣"),
])
@pytest.mark.parametrize("wording", [
    "回顾{scene}的经过：我们先后做过哪些尝试，各自结果如何，又是怎么到达现在的位置的？",
    "回顾一下，我们在{scene}先后做了什么，各自结果如何？",
    "回想{scene}那段经历，后来怎么抵达{destination}的？",
    "我记得按纸条号码通过{scene}，是这样吗？请核对原始行动和结果。",
])
def test_scene_history_keeps_actual_attempts_and_departure_after_summary_and_load(
    scene, destination, item, wording
):
    from app.memory.facts import bounded_facts, recalled_location

    history = [
        event(0, "game.started"),
        event(1, "scene.updated", scene_title="旧候车室", scene_summary="堆着行李。"),
        event(2, "check.resolved", reason="先后搜索座位，各自尝试的结果如何，目前仍不清楚。",
              text="无关搜索77/60失败。"),
        event(3, "npc.spoke", actor_name="门卫", text="你的问题，现在我说不清楚。"),
        event(4, "scene.updated", scene_title=scene, scene_summary="传来喘息声。"),
        event(5, "check.resolved", reason=f"沿{scene}边缘潜行，靠近前门。",
              text="潜行60/30失败，没有通过。"),
        event(6, "action.submitted", cycle_id="distract", text=f"我取下{item}，扔向另一侧。"),
        event(7, "module.interaction", source_event_seq=6,
              text="撞击声引开了对方，前门方向出现通行机会。"),
        event(8, "scene.updated", scene_title=destination, scene_summary="面前有一扇木门。"),
        event(9, "agent.summary_rebuilt", coverage_start=1, coverage_end=8),
        event(10, "agent.summary_rebuilt", coverage_start=1, coverage_end=9),
        event(11, "snapshot.loaded", source_seq=10),
    ]
    query = wording.format(scene=scene, destination=destination)
    selected = bounded_facts(recalled_location({"scene_title": destination}, query)
                             + select_facts(history, [], query))
    assert {5, 7, 8} <= {r["source_event_seq"] for r in selected}
    assert next(r for r in fact_records(history) if r["source_event_seq"] == 8)[
        "from_scene_title"
    ] == scene
    output = restore_output(
        KeeperNarration(public_narration="我们按纸条号码直接成功通过。",
                        fact_ids=[r["id"] for r in selected]),
        KeeperNarration, {"readonly_recall": True, "fact_evidence": selected},
    )
    assert "潜行60/30失败" in output.public_narration
    assert f"我取下{item}，扔向另一侧。" in output.public_narration
    assert "撞击声引开了对方" in output.public_narration
    assert f"抵达：{destination}" in output.public_narration
    assert "纸条号码" not in output.public_narration
