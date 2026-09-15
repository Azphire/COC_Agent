"""Bounded public state attached to summaries, derived from existing projections."""


def summary_state_facts(state, entities, inventory, events, budget=1800):
    """Keep exact recent outcomes/known writing when prose compression omits them.

    Only pass permission-filtered events and the public entity/inventory views.
    This is disposable summary context, never a source of new world mutations.
    """
    lines = ["当前位置：" + state.get("scene_title", "") + "。"]
    lines += [
        f"{i['holder_name']}当前持有：{i['title']}（实例 {i['instance_id']}）。" for i in inventory
    ]
    if not inventory:
        lines.append("当前没有登记的持有物。")
    lines += [
        e["payload"]["display_text"]
        for e in events
        if e["type"] == "check.resolved" and e["payload"].get("display_text")
    ][-3:]
    known = sorted(
        (e for e in entities if e.get("type") in {"clue", "item"}),
        key=lambda e: e.get("revealed_event_seq") or 0,
        reverse=True,
    )
    lines += [
        e["title"] + "：" + e["public_summary"]
        for e in known[:6]
        if e.get("revealed_event_seq") and e.get("public_summary")
    ]
    output = "当前公开状态核对（独立于分段历史摘要，后续以实际状态为准）："
    for line in lines:
        if len(output) + len(line) + 1 > budget:
            output += "\n其余公开记录见调查板与库存。"
            break
        output += "\n" + line
    return output
