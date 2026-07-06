import json
import os
from datetime import datetime


MEMORY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "memory")
INCIDENTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "incidents")


class LearningEngine:
    def __init__(self):
        os.makedirs(MEMORY_DIR, exist_ok=True)
        os.makedirs(INCIDENTS_DIR, exist_ok=True)
        self._load_index()

    def _load_index(self):
        index_path = os.path.join(MEMORY_DIR, "index.json")
        if os.path.exists(index_path):
            with open(index_path) as f:
                self.index = json.load(f)
        else:
            self.index = {"incidents": [], "patterns": [], "learned_actions": []}
            self._save_index()

    def _save_index(self):
        with open(os.path.join(MEMORY_DIR, "index.json"), "w") as f:
            json.dump(self.index, f, indent=2)

    def record_incident(self, incident):
        incident["learned_at"] = datetime.utcnow().isoformat()
        incident_id = f"inc-{len(self.index['incidents']) + 1}"
        incident["id"] = incident_id

        path = os.path.join(INCIDENTS_DIR, f"{incident_id}.json")
        with open(path, "w") as f:
            json.dump(incident, f, indent=2)

        self.index["incidents"].append({
            "id": incident_id,
            "type": incident.get("type", "unknown"),
            "summary": incident.get("summary", ""),
            "timestamp": incident["learned_at"],
        })
        self._save_index()
        return incident_id

    def recall_similar(self, query):
        matches = []
        for entry in self.index["incidents"]:
            if query.lower() in entry.get("summary", "").lower():
                path = os.path.join(INCIDENTS_DIR, f"{entry['id']}.json")
                if os.path.exists(path):
                    with open(path) as f:
                        matches.append(json.load(f))
        return matches

    def learn(self, pattern, action, outcome):
        self.index["patterns"].append({
            "pattern": pattern,
            "action": action,
            "outcome": outcome,
            "learned_at": datetime.utcnow().isoformat(),
            "confidence": outcome.get("success", False),
        })
        self._save_index()

    def get_stats(self):
        return {
            "total_incidents": len(self.index["incidents"]),
            "total_patterns": len(self.index["patterns"]),
            "learned_actions": len(self.index["learned_actions"]),
        }
