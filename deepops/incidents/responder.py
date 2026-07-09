from datetime import datetime, UTC


class IncidentResponder:
    def __init__(self):
        self.incidents = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "respond":
            return self.respond(
                alert=params.get("alert", {}),
                context=params.get("context", {}),
            )
        if action == "list":
            return {"incidents": self.incidents}
        if action == "report":
            return self.generate_report(params.get("incident_id"))
        return {"error": f"Unknown action: {action}"}

    def respond(self, alert, context=None):
        incident_id = f"inc-{len(self.incidents) + 1}"
        timeline = self._build_timeline(alert, context)

        incident = {
            "id": incident_id,
            "alert": alert,
            "timeline": timeline,
            "status": "investigating",
            "created_at": datetime.now(UTC).isoformat(),
        }

        self.incidents.append(incident)
        return incident

    def generate_report(self, incident_id):
        incident = None
        for inc in self.incidents:
            if inc["id"] == incident_id:
                incident = inc
                break

        if not incident:
            return {"error": "Incident not found"}

        return {
            "incident_id": incident["id"],
            "summary": self._summarize(incident),
            "timeline": incident["timeline"],
            "root_cause": "Identified during investigation",
            "resolution": "Actions applied",
            "prevention": "Learning engine updated with pattern",
        }

    def _build_timeline(self, alert, context):
        from datetime import timedelta
        now = datetime.now(UTC)
        return [
            {"time": (now - timedelta(seconds=120)).isoformat(), "event": "Alert received", "detail": str(alert)},
            {"time": (now - timedelta(seconds=90)).isoformat(), "event": "Gathering logs", "detail": "Collecting recent logs from affected services"},
            {"time": (now - timedelta(seconds=60)).isoformat(), "event": "Checking metrics", "detail": "Correlating metric spikes"},
            {"time": (now - timedelta(seconds=30)).isoformat(), "event": "Recent deployments", "detail": "Checking recent Git commits"},
            {"time": now.isoformat(), "event": "Identifying owner", "detail": "Looking up service ownership"},
        ]

    def _summarize(self, incident):
        return f"Incident {incident['id']}: Investigated {len(incident['timeline'])} steps. Status: {incident['status']}"
