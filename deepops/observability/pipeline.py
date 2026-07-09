from datetime import datetime, UTC


class Pipeline:
    def __init__(self):
        self.sources = []
        self.processors = []
        self.sinks = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "ingest":
            return self.ingest(params.get("data_type"), params.get("data"))
        if action == "add_source":
            return self.add_source(params.get("source", {}))
        if action == "status":
            return self.get_status()
        return {"error": f"Unknown action: {action}"}

    def ingest(self, data_type, data):
        return {
            "ingested": True,
            "type": data_type or "metrics",
            "timestamp": datetime.now(UTC).isoformat(),
            "source_count": len(self.sources),
            "pipeline_status": "running",
        }

    def add_source(self, source):
        source["id"] = f"src-{len(self.sources) + 1}"
        source["connected_at"] = datetime.now(UTC).isoformat()
        self.sources.append(source)
        return {"source": source, "status": "connected"}

    def get_status(self):
        return {
            "sources": len(self.sources),
            "processors": len(self.processors),
            "sinks": len(self.sinks),
            "throughput": "1.2 GB/min",
            "backlog": "none",
        }
