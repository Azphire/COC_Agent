from test_rooms import ok


def approve_structure(client, preparation_id, *, incomplete=False):
    prefix = f"/api/module-preparations/{preparation_id}/structure"
    structure = ok(client.post(prefix + "/build"))
    entities = ok(client.get(f"/api/module-preparations/{preparation_id}/entities"))
    prep = ok(client.get(f"/api/module-preparations/{preparation_id}"))
    scenes = [e for e in entities if e["type"] == "scene" and e["status"] == "approved"]
    scenes.sort(key=lambda e: e["id"] != prep["initial_scene_entity_id"])
    headings = [n for n in structure["nodes"] if n["node_id"] != structure["root_node_id"]]
    if not headings:
        headings = structure["nodes"]
    assert len(headings) >= len(scenes)
    scene_nodes = {}
    for index, entity in enumerate(scenes):
        node_id = headings[index]["node_id"]
        scene_nodes[entity["id"]] = node_id
        ok(
            client.patch(
                prefix + f"/nodes/{node_id}",
                json={
                    "approved_type": "scene",
                    "public_title": entity["title"],
                    "public_summary": entity["public_summary"],
                    "initial_scene": index == 0,
                },
            )
        )
    initial = scene_nodes[scenes[0]["id"]]
    for entity in entities:
        if entity["status"] != "approved":
            continue
        node_id = scene_nodes.get(entity["id"], initial)
        ok(
            client.post(
                prefix + "/entity-bindings",
                json={
                    "entity_id": entity["id"],
                    "node_id": node_id,
                    "source_hash": structure["source_hash"],
                },
            )
        )
    approved = ok(client.post(prefix + "/approve", json={"incomplete": incomplete}))
    return {"snapshot": approved, "scenes": scene_nodes, "initial": initial, "prefix": prefix}
