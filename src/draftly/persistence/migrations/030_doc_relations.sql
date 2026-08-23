CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    node_type TEXT NOT NULL CHECK (node_type IN ('code','concept','doc','eval')),
    key TEXT NOT NULL,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, node_type, key)
);

CREATE TABLE IF NOT EXISTS doc_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id TEXT REFERENCES organizations(clerk_org_id) ON DELETE CASCADE,
    source_node_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    target_node_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN
        ('IMPLEMENTS','DOCUMENTED_BY','AFFECTS','DERIVED_FROM')),
    evidence JSONB NOT NULL DEFAULT '[]'::JSONB,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_node_id, target_node_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_nodes_key
ON knowledge_nodes (org_id, node_type, key);

CREATE INDEX IF NOT EXISTS idx_doc_edges_source ON doc_edges (source_node_id);
CREATE INDEX IF NOT EXISTS idx_doc_edges_target ON doc_edges (target_node_id);
