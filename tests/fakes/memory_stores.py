"""In-memory doubles for agentic-memory stores."""

from __future__ import annotations


class FakeEpisodesStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, *, fields: dict) -> dict:
        fields = dict(fields)
        fields["id"] = f"ep-{len(self.rows) + 1}"
        self.rows.append(fields)
        return {"id": fields["id"], **fields}

    async def search(self, *, embedding, org_id=None, limit=5):
        return [
            {k: v for k, v in r.items() if k != "embedding"}
            for r in self.rows
            if org_id in (None, r.get("org_id"))
        ][:limit]


class FakeProceduresStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self._n = 0

    async def insert(self, *, fields: dict) -> dict:
        self._n += 1
        row = {
            "id": f"proc-{self._n}",
            "status": "active",
            "success_count": 0,
            "failure_count": 0,
            "confidence": 0.5,
            **fields,
        }
        self.rows[row["id"]] = row
        return dict(row)

    async def get(self, procedure_id: str) -> dict | None:
        row = self.rows.get(procedure_id)
        return dict(row) if row else None

    async def update(self, procedure_id: str, **fields) -> dict:
        self.rows[procedure_id].update(fields)
        return dict(self.rows[procedure_id])

    async def search(self, *, embedding, org_id=None, limit=3):
        active = [
            dict(r)
            for r in self.rows.values()
            if r.get("status") == "active" and org_id in (None, r.get("org_id"))
        ]
        return active[:limit]


class FakeDocGraphStore:
    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.edges: list[dict] = []

    def _node(self, node_type, key, org_id):
        for n in self.nodes:
            if n["node_type"] == node_type and n["key"] == key and n.get("org_id") == org_id:
                return n
        n = {"id": f"n{len(self.nodes) + 1}", "node_type": node_type,
             "key": key, "org_id": org_id}
        self.nodes.append(n)
        return n

    async def ensure_node(self, *, node_type, key, org_id=None, title=None) -> dict:
        return dict(self._node(node_type, key, org_id))

    async def upsert_edge(self, *, source_node_id, target_node_id,
                          relation_type, org_id=None, evidence=None) -> dict:
        for e in self.edges:
            if (e["source_node_id"] == source_node_id
                    and e["target_node_id"] == target_node_id
                    and e["relation_type"] == relation_type):
                e["evidence"] = evidence or e["evidence"]
                e["last_confirmed_at"] = "now"
                return dict(e)
        e = {"id": f"e{len(self.edges) + 1}", "source_node_id": source_node_id,
             "target_node_id": target_node_id, "relation_type": relation_type,
             "evidence": evidence or [], "last_confirmed_at": "now"}
        self.edges.append(e)
        return dict(e)

    async def docs_for_code(self, *, code_keys, org_id=None) -> list[dict]:
        by_id = {n["id"]: n for n in self.nodes}
        code_nodes = {n["id"] for n in self.nodes if n["key"] in code_keys}
        docs, seen = [], set()
        frontier = set(code_nodes)
        changed = True
        while changed:
            changed = False
            for e in self.edges:
                if e["source_node_id"] in frontier and e["target_node_id"] not in seen:
                    seen.add(e["target_node_id"])
                    frontier.add(e["target_node_id"])
                    changed = True
                    node = by_id.get(e["target_node_id"], {})
                    if node.get("node_type") == "doc":
                        docs.append(dict(node))
        return docs


class FakeCandidatesStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, *, fields: dict) -> dict:
        fields = {"id": f"cand-{len(self.rows) + 1}", "status": "pending",
                  "decision_reason": None, **fields}
        self.rows.append(fields)
        return dict(fields)

    async def claim_pending(self, *, limit: int) -> list[dict]:
        claimed = []
        for row in self.rows:
            if row["status"] == "pending" and len(claimed) < limit:
                row["status"] = "processing"
                claimed.append(dict(row))
        return claimed

    async def set_status(self, candidate_id: str, status: str, reason: str) -> None:
        for row in self.rows:
            if row["id"] == candidate_id:
                row["status"] = status
                row["decision_reason"] = reason

    async def list_by_status(self, status: str, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.rows if r["status"] == status][:limit]


class FakeSemanticRepo:
    """Duck-types DomainMemoryRepository for supersede tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.sources: dict[str, list[dict]] = {}

    def seed(self, memory_id: str, *, content: str) -> None:
        self.rows[memory_id] = {
            "id": memory_id, "content": content,
            "namespace": "knowledge", "memory_type": "fact",
            "importance": 0.5, "confidence": 0.5,
            "metadata": {}, "org_id": None, "status": "active",
        }

    async def get(self, memory_id: str):
        return dict(self.rows[memory_id]) if memory_id in self.rows else None

    async def store(self, item):
        mid = f"new-{len(self.rows) + 1}"
        self.rows[mid] = {
            "id": mid, "content": item.content,
            "namespace": item.namespace, "memory_type": item.memory_type,
            "importance": item.importance, "confidence": item.confidence,
            "metadata": dict(item.metadata), "org_id": item.org_id,
            "status": "active",
        }
        self.sources[mid] = []
        return dict(self.rows[mid])

    async def set_status(self, *, memory_id: str, status: str):
        self.rows[memory_id]["status"] = status
        return True

    async def record_provenance(self, *, memory_id, source_type,
                                source_id=None, source_url=None, evidence=None,
                                org_id=None):
        self.sources.setdefault(memory_id, []).append({
            "source_type": source_type, "source_id": source_id,
            "source_url": source_url, "evidence": evidence or [],
        })
