export type NodeType = 'document' | 'chapter' | 'section' | 'scene' | 'appendix' | 'block_group'
export type StructureNode = {
  node_id: string; parent_node_id: string | null; child_ids: string[]; title: string
  depth: number; original_depth: number; heading_path: string[]; detected_type: NodeType
  approved_type: NodeType | null; detection_source: string; confidence: number | null
  included: boolean; block_count: number; entity_count: number; public_title: string
  public_summary: string; keeper_summary: string; linked_node_ids: string[]
  source_position: { physical_page: number | null; page_end: number | null; paragraph_start: number }
}
export type NodeBinding = { binding_id: string; entity_id: string; node_id: string; source_hash: string }
export type SceneTransition = {
  transition_id: string; source_scene_node_id: string; target_scene_node_id: string
  transition_type: 'normal' | 'conditional' | 'host_only'; condition_summary: string
  required_revealed_entity_ids: string[]; required_event_types: string[]; approved: boolean
  source_evidence: string[]; relation_id: string | null
}
export type ModuleStructure = {
  source_hash: string; structure_version: string; root_node_id: string; nodes: StructureNode[]
  warnings: string[]; extraction_method: string; initial_scene_node_id: string | null
  entity_bindings: NodeBinding[]; transitions: SceneTransition[]; approved_snapshot_id: string | null
  incomplete: boolean; stale: boolean; node_count: number; block_count: number
}
export const nodeTypes: Record<NodeType, string> = {
  document: '文档', chapter: '章节', section: '小节', scene: '场景', appendix: '附录', block_group: '普通区块',
}
