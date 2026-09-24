"""Small generation grammars; full public/persisted contracts stay authoritative.

The per-call subclasses keep the same contract names and validation, but bind
server metadata as defaults. These fields never enter the generation grammar.
"""

import re
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter, create_model, model_validator

from app.agents.adjudication_schemas import (
    AnswerCoverage,
    KeeperNarration,
    KeeperPlan,
    NPCAnswer,
    NPCSpeech,
    PlayerIntent,
    TeammateDecision,
    TurnFocus,
)
from app.agents.check_policy import CheckProposal


def utterance_clauses(raw):
    pieces = [part for part in re.split(r"(?<=[，。！？；,.!?;\n])|(?<=……)", raw) if part]
    pieces = pieces[:11] + ["".join(pieces[11:])] if len(pieces) > 12 else pieces
    return [{"id": f"u{i}", "text": part} for i, part in enumerate(pieces, 1)]


def bound(base, values, **fields):
    return create_model(
        base.__name__,
        __base__=base,
        **{
            k: (
                base.model_fields[k].annotation,
                Field(
                    default=v,
                    json_schema_extra={
                        "x-server-bound": True,
                    },
                ),
            )
            for k, v in values.items()
        },
        **fields,
    )


def narration_body_field(contract):
    """Only the dynamic contract can designate a public prose string."""
    for name in ("observed_detail", "public_narration"):
        field = contract.model_fields.get(name)
        if field and field.annotation is str and not (field.json_schema_extra or {}).get(
            "x-server-bound"
        ):
            return name
    return None


def answer_coverage_contract(requirements, source_ids):
    """A missing source is not another requirement's selectable evidence.

    Keep the ordinary compact grammar. Mixed known/unknown answers additionally
    bind each row's source/status to its own requirement before generation.
    The public prose still needs the same independent coverage validation.
    """
    if all(r.get("source_ids") for r in requirements):
        return create_model(
            "AnswerCoverage", __base__=AnswerCoverage,
            requirement_id=(Literal[tuple(r["id"] for r in requirements)], ...),
            source_id=(Literal[(*source_ids, None)], Field(
                default=None, json_schema_extra={"x-explicit-output": True},
            )),
        )
    variants = []
    for index, requirement in enumerate(requirements):
        allowed = tuple(requirement.get("source_ids", []))
        fields = {
            "requirement_id": (Literal[requirement["id"]], Field(
                description=requirement["text"],
            )),
            "source_id": (Literal[allowed] if allowed else type(None), Field(
                default=None, json_schema_extra={"x-explicit-output": True},
            )),
        }
        if not allowed:
            fields.update({
                "status": (Literal["unknown"], Field(
                    default="unknown", json_schema_extra={"x-explicit-output": True},
                )),
                "source_quote": (Literal[""], Field(
                    default="", json_schema_extra={"x-explicit-output": True},
                )),
                "body_quote": (str, Field(
                    min_length=1, max_length=2000,
                    description="从正文摘录对“" + requirement["text"]
                    + "”尚未确认的说明；本轮没有这项事实的来源，不能给肯定或否定结论。",
                )),
            })
        variants.append(create_model(f"AnswerCoverage{index + 1}",
                                     __base__=AnswerCoverage, **fields))
    return variants[0] if len(variants) == 1 else Annotated[
        Union[tuple(variants)], Field(discriminator="requirement_id"),
    ]


def generation_contract(schema, context):
    if schema is KeeperPlan:
        ids = context["action_identifiers"]
        people = [t["id"] for t in context.get("current_targets", []) if t["type"] == "npc"]
        people += [
            member_id
            for member_id in context.get("current_participants", {}).get("members", {})
            if member_id != ids["actor_member_id"]
            and (
                not context.get("characters")
                or member_id in {c.get("member_id") for c in context["characters"]}
            )
        ]
        targets = [t["id"] for t in context.get("current_targets", [])]
        # The KP must be able to select a local undiscovered search result.
        # Its access conditions still apply; players need not know its name.
        targets += [t["entity_id"] for t in context.get("check_requirements", [])]
        targets += [t["target_scene_node_id"] for t in context.get("approved_exits", [])]
        focus_base = create_model(
            "TurnFocus",
            __base__=TurnFocus,
            addressee_id=(
                Literal[tuple(dict.fromkeys([*people, None]))],
                Field(
                    default=None,
                    json_schema_extra={"x-explicit-output": True},
                ),
            ),
            action_target_id=(
                Literal[tuple(dict.fromkeys([*targets, ids["current_scene_id"], None]))],
                Field(default=None, json_schema_extra={"x-explicit-output": True}),
            ),
        )
        clauses = utterance_clauses(context["triggering_action"]["payload"]["text"])
        clause_list = list[Literal[tuple(c["id"] for c in clauses)]]
        request = create_model(
            "TurnRequest",
            kind=(Literal["question", "delegate", "suggestion", "hypothesis", "cancel"], ...),
            addressee_id=(Literal[tuple(dict.fromkeys(people))] if people else str, ...),
            clause_ids=(clause_list, Field(default_factory=list, max_length=12)),
            target_id=(Literal[tuple(dict.fromkeys([*targets, *people, None]))], None),
            continuity=(Literal["scene", "ongoing"], "scene"),
            replaces_prior=(bool, False),
        )
        focus_base = bound(
            focus_base,
            {k: "" for k in ("action", "question", "suggestion", "hypothesis")},
            requests=(
                list[request],
                Field(
                    default_factory=list,
                    max_length=12,
                    json_schema_extra={"x-explicit-output": True},
                ),
            ),
        )
        focus = create_model(
            "TurnFocus",
            __base__=TurnFocus.__bases__[0],
            **{
                name + "_clause_ids": (
                    clause_list,
                    Field(
                        default_factory=list,
                        max_length=12,
                        json_schema_extra={"x-explicit-output": True},
                    ),
                )
                for name in ("action", "question", "suggestion", "hypothesis")
            },
            **{k: (f.annotation, f) for k, f in focus_base.model_fields.items()},
        )

        def preserve_mixed_question(value):
            selected = [c["text"] for c in clauses if c["id"] in value.action_clause_ids]
            questions = {c["id"] for c in clauses if re.search(r"[?？]$", c["text"].strip())}
            if any(
                re.search(r"[?？]$", c.strip()) for c in selected
            ) and not questions.intersection(value.question_clause_ids):
                if value.addressee_id in people and re.search(
                    r"问|请教", context["triggering_action"]["payload"]["text"],
                ):
                    # The plan already chose a present interlocutor. An explicit
                    # question is speech even if the model labelled all clauses
                    # as actions. Preserve independent action clauses for the
                    # existing attribution/authority pass; no new model call.
                    value.question_clause_ids = [c["id"] for c in clauses
                                                 if c["id"] in questions]
                    value.action_clause_ids = [
                        c["id"] for c in clauses if c["id"] in value.action_clause_ids
                        and c["id"] not in questions
                        and not re.search(r"问|请教|你|您", c["text"])
                    ]
                    return value
                from app.models.ollama import ModelFormatError

                raise ModelFormatError(
                    "混合句漏掉提问",
                    [
                        {
                            "field": "focus.question_clause_ids",
                            "code": (
                                "把原话中的提问片段填入question_clause_ids；"
                                "本人实际动作仍保留在action_clause_ids"
                            ),
                        }
                    ],
                )
            return value

        focus = create_model(
            "TurnFocus",
            __base__=focus,
            __validators__={
                "preserve_mixed_question": model_validator(mode="after")(preserve_mixed_question)
            },
        )
        trigger = context["triggering_action"]
        from app.preparation.action_authority import (
            action_kinds,
            declared_action,
            speaker_action,
            teammate_request,
        )

        original = trigger.get("payload", {}).get("text", "")
        own_transfers = (
            {
                c["id"]: c["text"]
                for c in clauses
                if speaker_action(c["text"]) and "give" in action_kinds(c["text"])
            }
            if teammate_request(
                original,
                context.get("current_participants", {}).get("members", {}),
                trigger.get("actor_member_id"),
            )
            else {}
        )
        if not context.get("readonly_recall") and (
            trigger.get("type") == "agent.action_proposed"
            and re.search(r"检查|搜索|寻找|拿起|使用|交给", original)
            or declared_action(original)
            and action_kinds(original)
            and (not teammate_request(
                original, context.get("current_participants", {}).get("members", {}),
                trigger.get("actor_member_id"),
            ) or speaker_action(original))
        ):

            def keep_proposed_operation(value):
                if own_transfers and not (
                    set(value.action_clause_ids) & own_transfers.keys()
                    or value.action
                    and any(t in value.action for t in own_transfers.values())
                ):
                    raise ValueError(
                        "本人交出物品与队友请求是两段意图；"
                        "action_clause_ids须保留本人实际交出段落。"
                    )
                if not value.action_clause_ids and not (value.action and value.action in original):
                    raise ValueError(
                        "原话包含实际检查或物品操作；action_clause_ids必须保留原动作。检查有没有文字不是只向人提问。检定仍由KP按条件决定。"
                    )
                return value

            focus = create_model(
                "TurnFocus",
                __base__=focus,
                __validators__={
                    "keep_proposed_operation": model_validator(mode="after")(
                        keep_proposed_operation
                    )
                },
            )
        intent = bound(
            PlayerIntent,
            {
                "schema_version": 1,
                "actor_member_id": ids["actor_member_id"],
                "actor_character_slot_id": ids["actor_character_slot_id"],
                "evidence_quote": context["triggering_action"]["payload"]["text"],
                "confidence": 1,
                "target_kind": None,
                "target_id": None,
                "target_text": None,
                "requested_outcome": "",
                "ambiguity_reason": None,
            },
        )
        check = bound(
            CheckProposal,
            {
                "target_member_id": ids["actor_member_id"],
                "rule_topic_id": None,
                "risk_quote": "",
                "necessity": "unnecessary",
                "purpose": "",
                "method": "",
                "reason": "",
                "uncertainty": "",
            },
        )
        from app.agents.compound_generation import proposal_contract

        check = proposal_contract(check, context)
        return bound(
            KeeperPlan,
            {
                **{
                    k: ids[k]
                    for k in (
                        "plan_id",
                        "cycle_id",
                        "current_scene_id",
                        "expected_navigation_revision",
                    )
                },
                "schema_version": 1,
                "addressed_member_id": None,
                "action_authority": {},
                "target_entity_ids": [],
                "target_node_ids": [],
                "source_entity_ids": [],
                "source_node_ids": [],
                "source_evidence_ids": [],
                "expected_next_phase": "narration",
                "rationale_summary": "",
                **(
                    {"pending_action": "independent"}
                    if not context.get("conversation_parent")
                    else {}
                ),
            },
            parsed_intent=(intent, ...),
            focus=(
                focus,
                Field(
                    # Generation requires an object via x-explicit-output;
                    # legacy providers that omit the field retain their default.
                    default=None,
                    json_schema_extra={"x-explicit-output": True},
                ),
            ),
            proposed_check=(
                check | None,
                Field(
                    default=None,
                    json_schema_extra={
                        "x-explicit-output": True,
                    },
                ),
            ),
            **(
                {
                    "proposed_transition_id": (
                        Literal[
                            tuple([e["transition_id"] for e in context["approved_exits"]] + [None])
                        ],
                        Field(default=None, json_schema_extra={"x-explicit-output": True}),
                    )
                }
                if "approved_exits" in context
                else {}
            ),
        )
    if schema is KeeperNarration:
        from app.agents.answer_parts import (
            CONTRACT_VERSION,
            parts_field,
            project_answer_parts,
            uses_answer_parts,
        )

        parts_mode = uses_answer_parts(context)
        responder = context.get("response_brief", {}).get("responder", {})
        fields = {}
        if responder.get("kind") == "npc":
            speech = bound(NPCSpeech, {"entity_id": responder["id"]})
            questions = context.get("response_brief", {}).get("questions", [])
            if questions:
                brief = context["response_brief"]
                answer_sources = {
                    f["id"]: f["text"] for f in brief.get("allowed_facts", [])
                }
                answer_sources.update({
                    f["entity_id"]: f["text"] for f in brief.get("testimony", [])
                })
                answer_sources["portrayal"] = responder.get("portrayal", "")
                answer_schema = bound(
                    NPCAnswer, {"evidence_quote": ""},
                    question_index=(
                        int, Field(default=0, ge=0, lt=len(questions),
                                   json_schema_extra={"x-explicit-output": True}),
                    ),
                    evidence_id=(
                        Literal[tuple([*answer_sources, None])],
                        Field(default=None, json_schema_extra={"x-explicit-output": True}),
                    ),
                )

                def bind_social_answers(value):
                    from app.preparation.dialogue import social_question

                    if isinstance(value, dict):
                        value = {**value}
                        answers = []
                        for position, item in enumerate(value.get("answers", [])):
                            item = dict(item)
                            item.setdefault("question_index", position)
                            if item.get("evidence_id") in answer_sources:
                                item["evidence_quote"] = answer_sources[item["evidence_id"]]
                            index = item.get("question_index")
                            if isinstance(index, int) and 0 <= index < len(questions):
                                if social_question(questions[index], brief) and not item.get(
                                    "evidence_quote"
                                ):
                                    item.update(certainty="social", evidence_quote="")
                            answers.append(item)
                        value["answers"] = answers
                    return value

                speech = bound(
                    NPCSpeech,
                    {"entity_id": responder["id"]},
                    text=(str, Field(default="", json_schema_extra={"x-server-bound": True})),
                    answers=(
                        list[answer_schema],
                        Field(
                            default_factory=list,
                            max_length=12,
                            json_schema_extra={"x-explicit-output": True},
                        ),
                    ),
                )

                def answers_current_questions(value):
                    from app.models.base import ModelFormatError

                    if value.text and not value.answers:
                        return value  # Older persisted outputs remain readable.
                    brief = context["response_brief"]
                    material = [f["text"] for f in brief.get("allowed_facts", [])]
                    material += [f["text"] for f in brief.get("testimony", [])]
                    material += [brief["responder"].get("portrayal", "")]
                    if sorted(a.question_index for a in value.answers) != list(
                        range(len(questions))
                    ):
                        raise ModelFormatError(
                            "问题未逐项回答",
                            [
                                {
                                    "field": "npc_speech.answers",
                                    "code": "每个questions索引恰好回答一次，从0起；"
                                    "无依据的问题写具体未知",
                                }
                            ],
                        )
                    for answer in value.answers:
                        from app.agents.behavior import normalized
                        from app.preparation.dialogue import item_history_evidence

                        if normalized(answer.text) == normalized(questions[answer.question_index]):
                            raise ModelFormatError(
                                "回答只是重复问题",
                                [{"field": f"npc_speech.answers[{answer.question_index}]",
                                  "code": "直接回答这项问题，不把问句重复给玩家；"
                                  "保留其他有效项。"}],
                            )
                        history = item_history_evidence(
                            answer.evidence_quote,
                            context.get("inventory_state", {}).get("known_items", []),
                        )
                        for item in history:
                            if item["past_possession"] and not any(
                                h["item_id"] == item["item_id"] and h["last_location"]
                                for h in history
                            ) and any(
                                item["name"] in part and re.search(r"掉在|掉落|丢在|遗落", part)
                                and not re.search(r"不确定|不知道|不清楚|是否|说不准", part)
                                for part in re.split(r"[，,；;。\n]", answer.text)
                            ):
                                raise ModelFormatError(
                                    "证词中的物件与最后位置被混淆",
                                    [{"field": f"npc_speech.answers[{answer.question_index}]",
                                      "code": item["name"] + "的依据仅为：" + item["source"]
                                      + "。其他物件的掉落地点不能当作它的位置；保留已知内容。"}],
                                )
                        if (
                            answer.evidence_quote
                            and not any(answer.evidence_quote in s for s in material)
                        ) or (
                            answer.certainty in {"sourced", "inference"}
                            and not answer.evidence_quote
                        ):
                            raise ModelFormatError(
                                "回答缺少对应原文",
                                [
                                    {
                                        "field": "npc_speech.answers",
                                        "code": "evidence_id须选择支持本问题的依据ID；"
                                        "没有该问题的依据请用unknown，明确哪点不清楚",
                                    }
                                ],
                            )
                        from app.preparation.dialogue import social_question

                        if answer.certainty == "social" and not social_question(
                            questions[answer.question_index], brief
                        ):
                            raise ModelFormatError(
                                "关键问题不能作为寒暄补全",
                                [{"field": f"npc_speech.answers[{answer.question_index}]",
                                  "code": "本项当前问题是："
                                  + questions[answer.question_index]
                                  + " 请回答这一项，不复播旧寒暄。其他有效答案保持原样；"
                                  "按本问题选证词，关键未知具体说明不确定哪一点。"}],
                            )
                        name = brief["responder"].get("name", "")
                        if (
                            name and re.match(re.escape(name) + r"(?:说|曾|在|的)", answer.text)
                        ) or (
                            re.search(r"他曾经?保管", answer.text)
                            and re.search(r"曾.{0,3}保管", answer.evidence_quote)
                        ):
                            raise ModelFormatError(
                                "NPC把自己说成第三人称",
                                [{"field": f"npc_speech.answers[{answer.question_index}]",
                                  "code": "用我表达自己的经历，不抄第三人称人物资料。"
                                  "保留其他有效项及其question_index。"}],
                            )
                        if (
                            re.search(r"(?:曾|原先|以前|过去|之前).{0,5}(?:保管|持有|拿着|带着)",
                                      answer.evidence_quote)
                            and any(
                                re.search(
                                    r"在我(?:这里|这儿|身上|手里|手上)|"
                                    r"(?:还在|仍在|正在)保管|保管着|"
                                    r"(?:现在|目前|如今).{0,6}(?:不在|没在|没有|不持有)", clause,
                                )
                                and not re.search(
                                    r"不确定|不知道|说不准|是否|原先|以前|过去|之前|曾", clause
                                )
                                for clause in re.split(r"[，。；,;]", answer.text)
                            )
                        ):
                            raise ModelFormatError(
                                "过去持有不能证明当前持有",
                                [{"field": f"npc_speech.answers[{answer.question_index}]",
                                  "code": "证词只说明过去保管，不证明现在在或不在身上。"
                                  "请保留已知的最后位置；当前持有未知就明确不确定，"
                                  "不要先断言在身上再说不知道。当前问题："
                                  + questions[answer.question_index]}],
                            )
                        if answer.certainty == "unknown" and not re.search(
                            r"不知|不清|不明|不了解|不记|记不|想不|说不|"
                            r"不(?:能|敢)?(?:确定|确认|肯定)|无法|难以|"
                            r"没(?:有)?(?:见|看|听|注意|印象|答案)|未(?:见|听|确认)",
                            answer.text,
                        ):
                            raise ModelFormatError(
                                "未知被说成事实",
                                [
                                    {
                                        "field": f"npc_speech.answers[{answer.question_index}]",
                                        "code": "unknown的答话须自然承认当前问题中"
                                        "具体不知道的内容，不补编经过",
                                    }
                                ],
                            )
                    value.text = "\n".join(
                        a.text for a in sorted(value.answers, key=lambda a: a.question_index)
                    )
                    return value

                speech = create_model(
                    "NPCSpeech",
                    __base__=speech,
                    __validators__={
                        "bind_social_answers": model_validator(mode="before")(bind_social_answers),
                        "answers_current_questions": model_validator(mode="after")(
                            answers_current_questions
                        )
                    },
                )
            fields["npc_speech"] = (
                speech,
                Field(
                    ...,
                    json_schema_extra={"x-explicit-output": True},
                ),
            )
        if "PUBLIC_CLAIM_OPTIONS" in context:
            from app.agents.narration_coverage import narration_claim_options

            claim_ids = tuple(c["claim_id"] for c in narration_claim_options(context))
            fields["claim_ids"] = (
                list[Literal[claim_ids]] if claim_ids else list[str],
                Field(
                    default_factory=list, max_length=5 if claim_ids else 0,
                    description="仅选本轮 allowed_facts.id；无对应依据则空列表。",
                    json_schema_extra={"x-explicit-output": True},
                ),
            )
        fields["public_narration"] = (
            str,
            Field(
                default="",
                max_length=2000,
                description="KP公开回答正文。responder为keeper时，完整回应本轮动作目标与"
                "questions中的各个问题，保留player_statement的答复格式要求；"
                "历史证词标明来源与历史性质，当前结果只依据实际回执。"
                "先在本字段实际写出answer_requirements各项答复，再从已写正文摘取body_quote填写映射。"
                "responder为npc时这里只写相关动作或环境，问答留在npc_speech。",
                json_schema_extra={"x-explicit-output": True},
            ),
        )
        ordinary_observation = context.get("response_brief", {}).get("ordinary_observation")
        answer_requirements = context.get("response_brief", {}).get("answer_requirements", [])
        observation_body = ordinary_observation and not any(
            r.get("kind") in {"question", "result"} for r in answer_requirements
        )
        unknown_demands = [r["text"] for r in answer_requirements if not r.get("source_ids")]
        unknown_instruction = (
            "本轮须明确答出尚未确认的项目：" + "；".join(unknown_demands)
            + "。在正文具体说哪项尚未确认，不把没有记载写成不存在；同时报告已发生的实际结果。"
            if unknown_demands else ""
        )
        if (not observation_body and responder.get("kind") == "keeper"
                and context.get("response_brief", {}).get("current_action_results")
                and any(r.get("kind") == "result" for r in answer_requirements)):
            fields["public_narration"] = (
                str,
                Field(
                    default="", max_length=2000,
                    description=fields["public_narration"][1].description
                    + "本轮包含已执行的实际结果，完整正文须同时报告结果内容和其余检查需求。"
                    + unknown_instruction,
                    json_schema_extra={"x-explicit-output": True},
                ),
            )
        if answer_requirements:
            source_ids = tuple(s["id"] for s in context["response_brief"].get("answer_sources", []))
            coverage = answer_coverage_contract(answer_requirements, source_ids)
            coverage_validator = TypeAdapter(coverage)
            fields["answer_coverage"] = (list[coverage], Field(
                default_factory=list, max_length=12,
                description="每个answer_requirements.id恰好映射一次，不重复同一需求。"
                "body_quote须原样出现在本次KP正文，"
                "source_id/source_quote取本轮answer_sources；保留数值、证词归属和估计。"
                "无依据则unknown并具体说明未知项。ID、附属细节或NPC台词均不能代替正文回答。",
                json_schema_extra={"x-explicit-output": True},
            ))
        else:
            fields["answer_coverage"] = (list[AnswerCoverage], Field(
                default_factory=list, json_schema_extra={"x-server-bound": True},
            ))
        if observation_body:
            appearance_instruction = (
                "本次检查结论仅依据answer_sources，尚未确认的外观不能即兴填成事实。"
                if context.get("response_brief", {}).get("current_action_results")
                else "普通杂物可即兴外观，不产生可获得资源、核心线索或治疗效果。"
            )
            fields["observed_detail"] = (
                str,
                Field(
                    default="", min_length=8, max_length=1000,
                    description="给玩家的完整公开回答正文，本字段原样成为public_narration。"
                    "在同一正文中逐项描述本轮关注的多个公开对象，并回答response_brief.questions"
                    "中的每个问题，包括有公开来源的历史问题；遵守player_statement的答复格式。"
                    "眼前观察、他人过去的估计或证词、当前实际结果分别说明，不把旧说法写成新发现。"
                    "只依据已公开来源与实际回执，缺少依据时具体说明尚未确认的部分。"
                    "current_action_results是本次已经发生的结果，须在正文报告；"
                    "answer_requirements.source_ids为空的检查细节尚未确认，不能编造肯定或否定结论。"
                    "直接描述物件/伤口/环境的外观细节，不描述你试图观察的动作。"
                    + appearance_instruction + unknown_instruction,
                    json_schema_extra={"x-explicit-output": True},
                ),
            )
            fields["public_narration"] = (
                str, Field(default="", json_schema_extra={"x-server-bound": True})
            )
        if parts_mode:
            fields["answer_parts"] = parts_field(context)
            fields["answer_contract_version"] = (
                Literal[CONTRACT_VERSION],
                Field(default=CONTRACT_VERSION, json_schema_extra={"x-server-bound": True}),
            )
            for name, annotation, default in (
                ("public_narration", str, ""),
                ("answer_coverage", list[AnswerCoverage], []),
                ("claim_ids", list[str], []),
                ("incidental_details", list[str], []),
            ):
                fields[name] = (annotation, Field(
                    default=default, json_schema_extra={"x-server-bound": True},
                ))
        result = bound(
            KeeperNarration,
            {
                "schema_version": 1,
                "grounded_claims": [],
                "current_scene_reference": context["current_scene_reference"],
                "check_result_reference": None,
                "transition_result_reference": None,
                "public_entity_references": [],
                **({"npc_speech": None} if responder.get("kind") != "npc" else {}),
            },
            **fields,
        )
        if not context.get("readonly_recall"):

            def responds_to_new_turn(value):
                from app.agents.behavior import bigram_jaccard

                if parts_mode:
                    from app.models.base import ModelFormatError

                    projection = project_answer_parts(value.answer_parts, context)
                    if (
                        value.public_narration
                        and value.public_narration != projection.public_narration
                        or value.answer_coverage
                        and value.answer_coverage != projection.answer_coverage
                        or value.incidental_details or value.claim_ids or value.grounded_claims
                    ):
                        raise ModelFormatError("逐段契约不能另外生成正文或引用副本", [{
                            "field": "answer_parts", "code": "只在每项text中填写实际正文。",
                        }])
                    value.public_narration = projection.public_narration
                    value.answer_coverage = projection.answer_coverage
                    value.check_result_reference = projection.check_result_reference
                    value.transition_result_reference = projection.transition_result_reference

                if observation_body and value.observed_detail:
                    from app.models.base import ModelFormatError

                    if not answer_requirements and re.match(
                        r"(?:你|我)(?:们)?(?:正|试图|尝试|仔细|蹲|开始)", value.observed_detail
                    ):
                        raise ModelFormatError("观察只有动作，没有内容", [{
                            "field": "observed_detail",
                            "code": "直接说明目标可见的具体状况或什么仍无法确认；不要再复述动作。",
                        }])
                    value.public_narration = value.observed_detail

                brief = context.get("response_brief", {})
                if answer_requirements and not parts_mode:
                    from app.agents.narration_coverage import (
                        coverage_audit,
                        coverage_repair_message,
                        normalize_coverage_spans,
                    )
                    from app.models.base import ModelFormatError

                    effective, _ = normalize_coverage_spans(value, brief)
                    value.answer_coverage = [coverage_validator.validate_python(row)
                                             for row in effective.get("answer_coverage", [])]
                    audit = coverage_audit(value, brief)
                    if not audit["complete"]:
                        raise ModelFormatError("KP正文需求覆盖未通过", [{
                            "field": "answer_coverage", "code": coverage_repair_message(audit),
                        }])
                old = [d.get("text", "") for d in brief.get("incidental_memories", [])]
                old += [
                    d.get("text", "")
                    for d in brief.get("recent_dialogue", [])
                    if d.get("type") in {"npc.spoke", "keeper.narration"}
                ]
                current = value.npc_speech.text if value.npc_speech else value.public_narration
                if (
                    ordinary_observation and brief.get("observation_subject")
                    and bigram_jaccard(
                        current, brief.get("current_scene", {}).get("public_description", "")
                    ) >= 0.8
                ):
                    from app.models.base import ModelFormatError

                    raise ModelFormatError("物件观察只重播场景", [{
                        "field": "observed_detail",
                        "code": "请直接描述attempt中所检查物件的可见情况或尚不能确认的部分；"
                        "observation_subject提供已知对象，不能用场景简介代替查看结果。",
                    }])
                if not current.strip() and (brief.get("question") or brief.get("attempt")):
                    from app.models.ollama import ModelFormatError

                    raise ModelFormatError(
                        "缺少本轮回应",
                        [
                            {
                                "field": "public_narration",
                                "code": (
                                    "在public_narration或npc_speech回应本轮任务；不能只选择旧事实ID"
                                ),
                            }
                        ],
                    )
                if (
                    current
                    and (value.npc_speech or brief.get("question") or brief.get("attempt"))
                    and any(bigram_jaccard(current, text) >= 0.8 for text in old)
                ):
                    from app.models.ollama import ModelFormatError

                    raise ModelFormatError(
                        "重复旧叙述",
                        [
                            {
                                "field": "npc_speech" if value.npc_speech else "public_narration",
                                "code": (
                                    "先直接回答current_task的新问题，不复播旧台词；"
                                    "根据公开依据说明知道什么、不确定什么"
                                ),
                            }
                        ],
                    )
                return value

            result = create_model(
                "KeeperNarration",
                __base__=result,
                __validators__={
                    "responds_to_new_turn": model_validator(mode="after")(responds_to_new_turn)
                },
            )
        return result
    if schema is TeammateDecision:
        from app.agents.task_receipts import teammate_target_options

        entity_ids, instance_ids = teammate_target_options(context)
        fields = {
            name: (
                str | None,
                Field(default=None, max_length=700, json_schema_extra={"x-explicit-output": True}),
            )
            for name in ("action_text", "speech_text")
        }
        fields["target_id"] = (
            Literal[tuple([*entity_ids, None])],
            Field(default=None, description="当前可操作实体或在场人物ID；讨论或目标不明确时为空。",
                  json_schema_extra={"x-explicit-output": True}),
        )
        fields["item_instance_ids"] = (
            list[Literal[tuple(instance_ids)]] if instance_ids else list[str],
            Field(default_factory=list, max_length=8 if instance_ids else 0,
                  description="实际可用的物品实例ID。线索等场景实体ID填target_id；不用道具填空数组。",
                  json_schema_extra={"x-explicit-output": True}),
        )
        requests = context.get("addressed_requests", [])
        if requests and all(r["kind"] in {"question", "cancel"} for r in requests):
            # Constrain generation using the shared, validated request. The
            # broader persisted schema and runtime authority checks remain.
            fields["mode"] = (
                TeammateDecision.model_fields["mode"].annotation,
                Field(..., json_schema_extra={"enum": ["speak"], "x-explicit-output": True}),
            )
        return bound(
            TeammateDecision,
            {
                "schema_version": 1,
                "related_player_action_seq": context["triggering_action"]["seq"],
            },
            **fields,
        )
    return schema


def restore_output(output, schema, context):
    value = output.model_dump(mode="json")
    if schema is KeeperNarration:
        if "answer_parts" in value:
            from app.agents.answer_parts import project_answer_parts

            projection = project_answer_parts(value.pop("answer_parts"), context)
            value.update(projection.model_dump(mode="json"))
        value.pop("answer_contract_version", None)
        value.pop("observed_detail", None)
    if schema is KeeperNarration and value.get("npc_speech"):
        for answer in value["npc_speech"].get("answers", []):
            answer.pop("evidence_id", None)
    if schema is KeeperPlan:
        from app.agents.action_policy import READ_TOOLS, explicit_movement, named_move_exits

        if value.get("proposed_transition_id") in {"null", "None", ""}:
            value["proposed_transition_id"] = None

        raw = context["triggering_action"]["payload"]["text"]
        named = named_move_exits(raw, context.get("approved_exits", []))
        from app.preparation.action_authority import action_kinds

        if (
            len(named) == 1
            and explicit_movement(raw)
            and value["parsed_intent"]["type"]
            in {"observe", "investigate", "wait", "unknown", "converse", "interact"}
            and set(action_kinds(raw)) <= {"observe", "search", "pass"}
            and not re.search(r"潜行|悄悄", raw)
        ):
            # "Enter the hall and look around" cannot lose its explicit move
            # merely because the model called it investigation. The named exit
            # still goes through the normal availability/authority checks.
            focus = value.get("focus") or TurnFocus().model_dump(mode="json")
            focus.update(action=raw, action_target_id=named[0]["target_scene_node_id"])
            if focus.get("question") == raw:
                focus.update(question="", addressee_id=None)
            value["focus"] = focus
            value["parsed_intent"].update(
                type="move", evidence_quote=raw, requires_clarification=False
            )
            value["proposed_transition_id"] = named[0]["transition_id"]
            value["needs_clarification"] = False
            value["proposed_check"] = None
            value["proposed_reveal_entity_ids"] = []
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] in READ_TOOLS
            ]
        local = named_move_exits(
            raw,
            [
                {**t, "target_public_title": t["title"]}
                for t in context.get("current_targets", [])
                if t.get("type") != "scene"
            ],
        )
        if (
            value["parsed_intent"]["type"] in {"move", "unknown"}
            and value.get("proposed_transition_id")
            and not named
            and len(local) == 1
            and explicit_movement(raw)
        ):
            value["focus"] = TurnFocus(action=raw, action_target_id=local[0]["id"]).model_dump(
                mode="json"
            )
            value["parsed_intent"].update(
                type="interact", evidence_quote=raw, requires_clarification=False
            )
            value["proposed_transition_id"] = None
            value["proposed_check"] = None
            value["needs_clarification"] = False
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] in READ_TOOLS
            ]
            if local[0]["type"] == "npc" and not any(
                r["entity_id"] == local[0]["id"] for r in context.get("check_requirements", [])
            ):
                value["proposed_reveal_entity_ids"] = [local[0]["id"]]
        if (
            value["parsed_intent"]["type"] in {"move", "unknown"}
            and (not value.get("focus") or value["parsed_intent"]["type"] == "unknown")
            and len(named) == 1
            and value.get("proposed_transition_id") == named[0]["transition_id"]
            and explicit_movement(raw)
        ):
            # Repair an incomplete representation of the model's selected move,
            # corroborated by the existing verb/destination guards. No inferred exit.
            value["focus"] = TurnFocus(
                action=raw, action_target_id=named[0]["target_scene_node_id"]
            ).model_dump(mode="json")
            value["parsed_intent"].update(
                type="move", evidence_quote=raw, requires_clarification=False
            )
            value["needs_clarification"] = False
            value["proposed_check"] = None
            value["proposed_tool_calls"] = [
                tool for tool in value["proposed_tool_calls"] if tool["name"] in READ_TOOLS
            ]
    if schema is KeeperPlan and value.get("focus"):
        focus = value["focus"]
        if (
            value["parsed_intent"]["type"] in {"observe", "investigate"}
            and focus.get("action")
            and not explicit_movement(focus["action"])
            and set(action_kinds(focus["action"])) & {"observe", "search"}
        ):
            # Looking toward a neighbouring room never authorizes crossing into
            # it. Drop the stray transition before it triggers a plan repair.
            value["proposed_transition_id"] = None
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]
        if focus.get("answer_basis") in {"improvise", "unrecorded"}:
            from app.agents.action_policy import READ_TOOLS

            if (
                not value.get("proposed_check")
                and not value.get("proposed_reveal_entity_ids")
                and not value.get("proposed_transition_id")
                and all(t["name"] in READ_TOOLS for t in value["proposed_tool_calls"])
            ):
                value["needs_host_review"] = False
        clauses = utterance_clauses(context["triggering_action"]["payload"]["text"])
        requests = []
        for request in focus.get("requests", []):
            if "clause_ids" not in request:
                requests.append(request)
                continue
            chosen = request.pop("clause_ids")
            positions = [i for i, c in enumerate(clauses) if c["id"] in chosen]
            # Only contiguous original spans are accepted. Missing middle IDs
            # cannot absorb another addressee's intervening task.
            if not positions or positions != list(range(min(positions), max(positions) + 1)):
                continue
            start = sum(len(c["text"]) for c in clauses[: min(positions)])
            text = "".join(c["text"] for c in clauses[min(positions) : max(positions) + 1])
            requests.append(
                {**request, "text": text, "source_start": start, "source_end": start + len(text)}
            )
        focus["requests"] = requests
        for name in ("action", "question", "suggestion", "hypothesis"):
            key = name + "_clause_ids"
            chosen = focus.pop(key, [])
            if output.focus is not None and key in output.focus.model_fields_set:
                positions = [i for i, c in enumerate(clauses) if c["id"] in chosen]
                focus[name] = (
                    "".join(c["text"] for c in clauses[min(positions) : max(positions) + 1])
                    if positions
                    else ""
                )
        from app.agents.action_policy import named_move_exits
        from app.preparation.action_authority import NON_ACTION, action_kinds, declared_action

        kinds = set(action_kinds(focus.get("action", "")))
        if (
            (
                value["parsed_intent"]["type"] in {"converse", "wait", "unknown", "assist"}
                or value["parsed_intent"]["type"] == "observe"
                and (
                    kinds & {"take", "give", "open", "close", "place", "control"}
                    or "search" in kinds
                    and (
                        re.search(r"翻看|翻过|翻转|揭下", focus.get("action", ""))
                        or any(
                            entry.get("entity_id") == focus.get("action_target_id")
                            and any(
                                "search" in rule.get("action_kinds", [])
                                for rule in entry.get("interactions", [])
                            )
                            for entry in context.get("module_interactions", [])
                        )
                    )
                )
            )
            and kinds
            and declared_action(focus["action"])
        ):
            value["parsed_intent"]["type"] = (
                "observe" if kinds <= {"observe", "light"} else "interact"
            )

        if (
            value["parsed_intent"]["type"] in {"observe", "converse", "wait", "unknown", "move"}
            and "throw" in action_kinds(focus.get("action", ""))
            and (value["parsed_intent"]["type"] != "move" or not explicit_movement(raw))
            and re.search(
                r"(?:^|[，。；])\s*我?(?:(?:把|将|脱下|摘下|取下).{1,24})?(?:扔|抛|掷|投向)",
                focus["action"],
            )
        ):
            # An explicit current throw cannot be downgraded to observation.
            # Keep the source clauses/target; approved methods still decide
            # whether a worn or actually held object is legal and its result.
            value["parsed_intent"]["type"] = "interact"
            value["proposed_transition_id"] = None
            value["needs_clarification"] = False
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]

        if (
            value["parsed_intent"]["type"] == "move"
            and not explicit_movement(raw)
            and set(action_kinds(focus.get("action", "")))
            & {"control", "open", "close", "give", "place", "take", "light", "sound_start"}
            and any(
                t["id"] == focus.get("action_target_id")
                and t["type"] in {"item", "location", "clue"}
                for t in context.get("current_targets", [])
            )
        ):
            # Moving a lever/item is not an investigator scene transition.
            # Keep the selected target and raw clauses, then use normal method
            # adjudication; this repair cannot grant any world operation itself.
            value["parsed_intent"]["type"] = "interact"
            value["proposed_transition_id"] = None
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]

        if (
            focus.get("action")
            and all(
                NON_ACTION.search(c["text"])
                for c in utterance_clauses(focus["action"])
                if c["text"].strip()
            )
            and not action_kinds(focus["action"])
            and not explicit_movement(focus["action"])
            and not (value["parsed_intent"]["type"] == "move" and explicit_movement(raw))
        ):
            focus["question"] = focus.get("question") or focus["action"]
            focus["action"] = ""
            focus["action_target_id"] = None
            value["parsed_intent"]["type"] = "converse" if focus.get("addressee_id") else "wait"
            value["proposed_check"] = None
            value["proposed_tool_calls"] = []
            value["proposed_reveal_entity_ids"] = []
            value["proposed_transition_id"] = None

        named = named_move_exits(
            context["triggering_action"]["payload"]["text"], context.get("approved_exits", [])
        )
        # A named place can be a rejected direction, not the destination.
        exits = context.get("approved_exits", [])
        excluded = [
            t
            for t in exits
            if t.get("target_public_title")
            and re.search(
                r"(?:远离|背对|背离|避开|离开)[^，。！？；,.!?;\n]{0,12}"
                + re.escape(t["target_public_title"]),
                raw,
            )
        ]
        remaining = [t for t in exits if t not in excluded]
        if value["parsed_intent"]["type"] == "move" and excluded and len(remaining) == 1:
            named = remaining
        if value["parsed_intent"]["type"] == "move" and len(named) == 1:
            value["proposed_transition_id"] = named[0]["transition_id"]
            # A uniquely corroborated destination is not an ambiguous action.
            # Availability is still checked against live navigation conditions.
            value["needs_clarification"] = False
            value["parsed_intent"].update(
                requires_clarification=False, clarification_question=None
            )
            # Discard a model-authored movement call with a different destination.
            # The approved transition proposal is normalized by the existing planner.
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]
        transition = next(
            (
                t
                for t in context.get("approved_exits", [])
                if t["transition_id"] == value.get("proposed_transition_id")
            ),
            None,
        )
        local_target = next(
            (
                target
                for target in context.get("current_targets", [])
                if target.get("id") == focus.get("action_target_id")
                and target.get("fact_scope", "current_scene") == "current_scene"
            ),
            None,
        )
        local_named = local_target and any(
            name and name in raw
            for name in [local_target.get("title", ""), *local_target.get("aliases", [])]
        )
        if transition and not named and local_named and value["parsed_intent"]["type"] == "move":
            # A person/object actually named by the player can be a local goal.
            # A model-selected obstacle alone must not erase the separately
            # selected destination just because its public title is a synonym.
            value["parsed_intent"]["type"] = "interact"
            value["proposed_transition_id"] = None
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
            ]
            transition = None
        if transition and value["parsed_intent"]["type"] == "move":
            # A selected move has one destination. Restore it from the approved
            # candidate; the ordinary intent/exit/ownership guards still apply.
            focus["action_target_id"] = transition["target_scene_node_id"]
            # Preserve the actual movement clause when the model selected only
            # its preceding door-opening clause. The destination still comes
            # from the approved exit and the complete utterance must authorize it.
            if len(named) == 1 and explicit_movement(raw):
                movement_positions = [
                    i
                    for i, clause in enumerate(clauses)
                    if explicit_movement(clause["text"])
                    and named_move_exits(clause["text"], [transition])
                ]
                if movement_positions and not explicit_movement(focus.get("action", "")):
                    focus["action"] = "".join(
                        c["text"]
                        for c in clauses[min(movement_positions) : max(movement_positions) + 1]
                    )
                if "converse" not in action_kinds(raw):
                    focus.update(question="", addressee_id=None)
            # Scene nodes are routing metadata, never revealable entity IDs.
            # Do not let a redundant model reveal derail a valid transition.
            node_ids = {
                context.get("action_identifiers", {}).get(
                    "current_scene_id", value["current_scene_id"]
                )
            } | {
                e["target_scene_node_id"] for e in context.get("approved_exits", [])
            }
            # Arrival itself publishes this scene entity. A separate reveal
            # before moving is outside the current scene and masks valid prose.
            node_ids.add(transition.get("target_entity_id"))
            value["proposed_reveal_entity_ids"] = [
                eid for eid in value["proposed_reveal_entity_ids"] if eid not in node_ids
            ]
        proposal = value.get("proposed_check")
        from app.preparation.search import (
            named_check_requirement,
            repair_belongings_action,
            repair_control_target,
            repair_observation_target,
            repair_search_target,
        )

        repair_belongings_action(value, context)
        repair_observation_target(value, context)
        repair_search_target(value, context)
        repair_control_target(value, context)
        from app.preparation.action_authority import check_operation_error

        # Approved conditions still belong to an operation. A search of a door
        # cannot select an unrelated treatment-gated clue in the same scene.
        requirements = [
            r for r in context.get("check_requirements", [])
            if not check_operation_error(
                (r.get("successful_check") or {}).get("name"), focus.get("action", "")
            )
        ]
        excluded = {r["entity_id"] for r in context.get("check_requirements", [])
                    if r not in requirements}
        if focus.get("action_target_id") in excluded:
            focus["action_target_id"] = value["current_scene_id"]
        value["proposed_reveal_entity_ids"] = [
            eid for eid in value.get("proposed_reveal_entity_ids", []) if eid not in excluded
        ]
        if proposal and (proposal.get("clue_id") in excluded or check_operation_error(
            proposal.get("name"), focus.get("action", "")
        ) and proposal.get("clue_id")):
            value["proposed_check"] = proposal = None
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] != "request_skill_check"
            ]
        named_requirements = [
            r
            for r in requirements
            if r.get("access_policy") == "requires_check"
            and r.get("successful_check")
            and named_check_requirement(
                r,
                focus.get("action", ""),
                value.get("proposed_reveal_entity_ids", []),
                parent=next(
                    (
                        t
                        for t in context.get("current_targets", [])
                        if t["id"] == focus.get("action_target_id")
                    ),
                    None,
                ),
                proposal=proposal,
            )
        ]
        from app.preparation.search import search_instrument, unrelated_item_focus

        if (
            not named_requirements
            and (
                focus.get("action_target_id")
                == context.get("action_identifiers", {}).get("current_scene_id")
                or any(
                    t.get("type") == "scene" and t["id"] == focus.get("action_target_id")
                    for t in context.get("current_targets", [])
                )
                or unrelated_item_focus(
                    focus.get("action", ""), focus.get("action_target_id"), context
                )
                or search_instrument(
                    focus.get("action", ""),
                    focus.get("action_target_id"),
                    context.get("inventory_state", {}),
                    context.get("triggering_action", {}).get("actor_member_id"),
                )
                or any(
                    t.get("type") == "clue"
                    and t["id"] == focus.get("action_target_id")
                    and not any(
                        name and name in focus.get("action", "")
                        for name in [t.get("title", ""), *t.get("aliases", [])]
                    )
                    for t in context.get("current_targets", [])
                )
            )
            and (
                "search" in action_kinds(focus.get("action", ""))
                or "observe" in action_kinds(focus.get("action", ""))
                and focus.get("obstacle")
                and proposal
                and proposal.get("necessity") in {"required", "optional"}
            )
            and value["parsed_intent"]["type"] in {"investigate", "observe"}
        ):
            # A scene search need not name the object it has not discovered yet.
            # The KP already chose a single gated discovery; bind that attempt
            # to its approved real check, retaining all normal prerequisites.
            # An ordinary public observation cannot acquire an unrelated hidden
            # discovery merely from an unnecessary model check. Explicitly named
            # gated details were handled above; concrete risky observations retain
            # their existing adjudication here.
            named_requirements = [
                r
                for r in requirements
                if (
                    r["entity_id"] in value.get("proposed_reveal_entity_ids", [])
                    or r["entity_id"] == (proposal or {}).get("target_entity_id")
                )
                and r.get("access_policy") == "requires_check"
                and r.get("successful_check")
            ]
        if len(named_requirements) == 1 and value["parsed_intent"]["type"] in {
            "investigate",
            "observe",
            "interact",
        }:
            # Resolve an explicitly named gated detail instead of its parent object.
            previous_target = focus.get("action_target_id")
            focus["action_target_id"] = named_requirements[0]["entity_id"]
            if previous_target != focus["action_target_id"]:
                # A discarded focus cannot separately publish an unrelated event
                # while the actual search still awaits its original check.
                value["proposed_reveal_entity_ids"] = [
                    eid for eid in value["proposed_reveal_entity_ids"]
                    if eid != previous_target
                ]
        required = next(
            (
                r
                for r in requirements
                if r["entity_id"] == focus.get("action_target_id")
                and r.get("access_policy") == "requires_check"
                and r.get("successful_check")
            ),
            None,
        )
        if (
            required
            and focus.get("action")
            and not focus.get("obstacle")
            and value["parsed_intent"]["type"] in {"investigate", "observe", "interact"}
        ):
            focus["obstacle"] = "模组已批准条件要求先完成检定，目标尚未确认。"
        if (
            required
            and focus.get("action")
            and value["parsed_intent"]["type"] in {"investigate", "observe", "interact"}
            and (
                not proposal
                or proposal.get("target_entity_id") != required["entity_id"]
                or not proposal.get("alternative_basis")
                and any(
                    proposal.get(k) != required["successful_check"][k]
                    for k in ("kind", "name", "difficulty")
                )
            )
        ):
            # A source-approved mandatory search is a rule, not optional model
            # judgement. This requests a real roll; it never supplies its result.
            focus["obstacle"] = "模组已批准条件要求先完成检定，目标尚未确认。"
            proposal = CheckProposal(
                **required["successful_check"],
                target_member_id=context["action_identifiers"]["actor_member_id"],
                reason=focus["action"],
                clue_id=required["entity_id"],
                target_entity_id=required["entity_id"],
                basis_entity_id=required["entity_id"],
                success_effect="完成模组配置的调查目标。",
                failure_consequence="本次未取得模组配置的发现，不自动扣除资源。",
            ).model_dump(mode="json")
            value["proposed_check"] = proposal
            value["rationale_summary"] = "按已批准的目标条件补齐必需检定；使用真实骰。"
        if not focus.get("action") or not focus.get("obstacle"):
            value["proposed_check"] = None
            value["proposed_tool_calls"] = [
                t for t in value["proposed_tool_calls"] if t["name"] != "request_skill_check"
            ]
        elif proposal:
            if required and proposal.get("target_entity_id") == required["entity_id"]:
                # A model may propose the correct check but omit its discovery
                # binding. Bind the existing proposal before method candidates
                # and deferred acquisition are collected, just as for a new one.
                proposal.update(
                    clue_id=required["entity_id"],
                    basis_entity_id=required["entity_id"],
                    target_member_id=context["action_identifiers"]["actor_member_id"],
                )
            actor = next(
                (
                    c
                    for c in context.get("characters", [])
                    if c.get("member_id") == context["action_identifiers"]["actor_member_id"]
                ),
                {},
            )
            # Resolve an unambiguous attribute name, e.g. luck, without letting
            # the model choose numeric values or invent a new skill.
            if proposal.get("name") in actor.get("effective_attributes", {}) and proposal.get(
                "name"
            ) not in actor.get("skill_values", {}):
                proposal["kind"] = "attribute"
            proposal.update(
                rule_topic_id="coc7.skill_check",
                necessity="required",
                purpose=(focus.get("purpose") or focus["action"])[:240],
                method=focus["action"][:240],
                reason=focus["action"],
                uncertainty=focus["obstacle"],
            )
        # Run after unnecessary model checks have been removed. A discarded
        # check must not suppress the selected object's ordinary discovery.
        from app.preparation.search import complete_automatic_discovery

        complete_automatic_discovery(value, context)
    acknowledgement = False
    if schema is KeeperPlan:
        from app.preparation.inventory import held_item_acknowledgement

        trigger = context["triggering_action"]
        acknowledgement = trigger.get(
            "type"
        ) == "agent.action_proposed" and held_item_acknowledgement(
            raw, context.get("inventory_state", {}), trigger.get("actor_member_id")
        )
    if schema is KeeperPlan and (context.get("readonly_recall") or acknowledgement):
        recipient = (value.get("focus") or {}).get("addressee_id")
        people = context.get("current_participants", {}).get("members", {})
        if not any(name and name in raw for name in people.values()):
            recipient = None
        value["parsed_intent"].update(
            type="converse" if acknowledgement else "recall",
            target_id=None,
            evidence_quote=context["triggering_action"]["payload"]["text"],
            requires_clarification=False,
        )
        value.update(
            proposed_check=None,
            proposed_tool_calls=[],
            proposed_transition_id=None,
            proposed_reveal_entity_ids=[],
            needs_clarification=False,
            addressed_member_id=recipient,
        )
        value["focus"] = TurnFocus(
            question=context["triggering_action"]["payload"]["text"],
            addressee_id=recipient,
            answer_basis="facts",
        ).model_dump()
    elif schema is KeeperPlan:
        from app.agents.action_policy import local_scene_movement

        if local_scene_movement(
            raw, context.get("current_targets", []), context.get("approved_exits", [])
        ):
            scene_id = context["action_identifiers"]["current_scene_id"]
            focus = value.get("focus") or TurnFocus().model_dump()
            focus.update(action=raw, action_target_id=scene_id)
            value.update(focus=focus, proposed_transition_id=None, needs_clarification=False)
            # The navigation node is already the current scene. It is not an
            # unrevealed prepared entity that a local move needs to discover.
            value["proposed_reveal_entity_ids"] = [
                eid for eid in value["proposed_reveal_entity_ids"] if eid != scene_id
            ]
            value["parsed_intent"].update(
                type="interact",
                target_id=scene_id,
                evidence_quote=raw,
                requires_clarification=False,
            )
            value["proposed_tool_calls"] = [
                t
                for t in value["proposed_tool_calls"]
                if t["name"] not in {"transition_scene", "update_scene"}
                and not (
                    t["name"] == "reveal_entity" and t["arguments"].get("entity_id") == scene_id
                )
            ]
            if value.get("proposed_check"):
                value["proposed_check"].update(target_entity_id=scene_id, clue_id=None)
    if schema is KeeperNarration and value.get("claim_ids"):
        options = {c["claim_id"]: c for c in context["PUBLIC_CLAIM_OPTIONS"]}
        if not set(value["claim_ids"]) <= options.keys():
            from app.models.ollama import ModelFormatError

            raise ModelFormatError("未知公开依据", [{"field": "claim_ids", "code": "unknown_id"}])
        value["grounded_claims"] = [options[k] for k in dict.fromkeys(value["claim_ids"])]
    from app.agents.results import quote_request, search_question_subject
    from app.memory.facts import answer_query, selected_answer_facts

    if schema in {KeeperNarration, TeammateDecision} and context.get("readonly_recall") and (
        quote_request(answer_query(context))
        or not any(r.get("result_fact") for r in selected_answer_facts(context))
        and not search_question_subject(answer_query(context))
    ):
        from app.memory.facts import render_facts

        evidence = selected_answer_facts(context)
        ids = value.get("fact_ids", [])
        # The model may order retrieved excerpts. The original text is always
        # rendered by the server, including evidence the model forgot to select.
        known = {r["id"]: r for r in evidence}
        ids = list(dict.fromkeys([i for i in ids if i in known] + list(known)))
        content = render_facts([known[i] for i in ids])
        if schema is KeeperNarration:
            value.update(
                public_narration=content,
                incidental_details=[],
                npc_speech=None,
                grounded_claims=[],
                claim_ids=[],
                fact_ids=ids,
            )
        else:
            value.update(
                mode="speak",
                action_type="recall",
                action_text=None,
                speech_text=content,
                target_id=None,
                related_public_entity_ids=[],
                fact_ids=[r["id"] for r in evidence],
            )
    elif schema is KeeperNarration:
        # Receipts constrain the narrator; they are not a replacement paragraph.
        # The publication validator checks real state, references and disclosure.
        # Only failed validation invokes the bounded fallback in the runtime.
        results = context.get("public_tool_results", {}).get("events", [])
        for event in results:
            if event["type"] == "check.resolved":
                value["check_result_reference"] = event["payload"].get(
                    "id", event["payload"].get("check_id")
                )
            elif event["type"] == "scene.updated":
                value["transition_result_reference"] = str(event["seq"])
    restored = schema.model_validate(value)
    if schema is KeeperPlan:
        from app.agents.action_policy import local_scene_movement
        from app.preparation.turn_focus import repair_attribution

        people = {
            **context.get("current_participants", {}).get("members", {}),
            **{
                t["id"]: t["title"]
                for t in context.get("current_targets", [])
                if t["type"] == "npc"
            },
        }
        repair_attribution(
            restored,
            context["triggering_action"]["payload"]["text"],
            people,
            restored.parsed_intent.actor_member_id,
            npc_ids={t["id"] for t in context.get("current_targets", []) if t["type"] == "npc"},
        )
        actual = set(action_kinds(restored.focus.action)) if restored.focus else set()
        from app.preparation.action_authority import operative_fragments
        from app.preparation.search import unrelated_item_focus

        if "light" in actual and unrelated_item_focus(
            "".join(f["text"] for f in operative_fragments(restored.focus.action)
                    if "light" in action_kinds(f["text"])),
            restored.focus.action_target_id, context,
        ):
            # An inferred carried item is not the stated environmental target.
            # Keep the actual attempt in this scene; legal methods must bind
            # its device themselves, and final authority checks the same noun.
            restored.focus.action_target_id = restored.current_scene_id
            restored.parsed_intent.target_id = restored.current_scene_id
            restored.parsed_intent.type = "interact"
            restored.proposed_tool_calls = [t for t in restored.proposed_tool_calls
                                           if t.name != "apply_module_action"]
        if restored.parsed_intent.type == "move" and actual - {"converse", "observe"} \
                and not explicit_movement(restored.focus.action):
            restored.parsed_intent.type = (
                "investigate" if actual <= {"search", "observe"} else "interact"
            )
            restored.proposed_transition_id = None
            restored.needs_clarification = restored.parsed_intent.requires_clarification = False
            restored.parsed_intent.clarification_question = None
            restored.proposed_tool_calls = [t for t in restored.proposed_tool_calls
                                           if t.name not in {"transition_scene", "update_scene"}]
        if restored.parsed_intent.type == "observe" and actual - {"observe", "converse"}:
            restored.parsed_intent.type = (
                "investigate" if actual <= {"search", "observe"} else "interact"
            )
        if restored.proposed_check and restored.proposed_check.opposed and restored.focus:
            # Asking a peer for advice is not a contest, even alongside a search.
            opponent = restored.proposed_check.opposed.opponent_member_id
            if opponent and any(r.addressee_id == opponent and r.kind == "question"
                                for r in restored.focus.requests):
                restored.proposed_check = None
        if (
            restored.parsed_intent.type == "move"
            and restored.focus and restored.focus.action
            and set(action_kinds(restored.focus.action)) == {"observe"}
            and re.search(r"(?:过去|走近|凑近|靠近).{0,12}(?:看看|查看|观察|打量)",
                          restored.focus.action)
            and not named_move_exits(restored.focus.action, context.get("approved_exits", []))
            and not re.search(r"进入|走进|穿过|跨入|离开|返回|退回", restored.focus.action)
        ):
            # Approaching an unnamed ordinary object to inspect it supplies a
            # local viewpoint, not permission to select a neighbouring scene.
            restored.parsed_intent.type = "observe"
            local_ids = {t["id"] for t in context.get("current_targets", [])}
            if restored.focus.action_target_id not in local_ids:
                restored.focus.action_target_id = restored.current_scene_id
            restored.parsed_intent.target_id = restored.focus.action_target_id
            restored.needs_clarification = restored.parsed_intent.requires_clarification = False
            restored.parsed_intent.clarification_question = None
            if not restored.proposed_check:
                restored.proposed_reveal_entity_ids = []
        if (
            restored.parsed_intent.type in {"observe", "investigate"}
            and restored.focus and restored.focus.action
            and set(action_kinds(restored.focus.action)) <= {"observe", "search"}
            and not (
                explicit_movement(restored.focus.action)
                and named_move_exits(restored.focus.action, context.get("approved_exits", []))
            )
        ):
            # Attribution can recover a local inspection after the earlier
            # movement pass. Discard its leftover exit and destination reveal
            # together; a local "go over and look" is not a party transition.
            restored.proposed_transition_id = None
            restored.proposed_tool_calls = [
                t for t in restored.proposed_tool_calls
                if t.name not in {"transition_scene", "update_scene"}
            ]
            route_ids = {restored.current_scene_id} | {
                e.get(k) for e in context.get("approved_exits", [])
                for k in ("target_scene_node_id", "target_entity_id")
            }
            restored.proposed_reveal_entity_ids = [
                eid for eid in restored.proposed_reveal_entity_ids if eid not in route_ids
            ]
        # The speaker's local viewpoint must be checked after attribution: a
        # teammate's polite question cannot turn this into a party transition.
        if (
            restored.focus
            and restored.focus.action
            and local_scene_movement(
                restored.focus.action,
                context.get("current_targets", []),
                context.get("approved_exits", []),
            )
        ):
            scene_id = restored.current_scene_id
            restored.focus.action_target_id = scene_id
            restored.parsed_intent.type = "interact"
            restored.parsed_intent.target_id = scene_id
            restored.proposed_transition_id = None
            restored.proposed_tool_calls = [
                t
                for t in restored.proposed_tool_calls
                if t.name not in {"transition_scene", "update_scene"}
            ]
            restored.proposed_reveal_entity_ids = [
                eid for eid in restored.proposed_reveal_entity_ids if eid != scene_id
            ]
    return restored
