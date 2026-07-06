from datetime import datetime


class Patcher:
    def __init__(self):
        self.updates = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "update":
            return self.update(params.get("scope", "all"))
        if action == "check":
            return self.check_updates()
        if action == "history":
            return {"updates": self.updates}
        return {"error": f"Unknown action: {action}"}

    def update(self, scope):
        updates = self._plan_updates(scope)
        update_job = {
            "id": f"upd-{len(self.updates) + 1}",
            "scope": scope,
            "updates": updates,
            "status": "planned",
            "requires_approval": True,
            "rollback_plan": self._rollback_plan(updates),
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.updates.append(update_job)
        return update_job

    def check_updates(self):
        return {
            "os_patches": 4,
            "kubernetes_upgrades": ["v1.28.4", "v1.29.0"],
            "docker_updates": ["24.0.7"],
            "library_updates": 12,
            "last_checked": datetime.utcnow().isoformat(),
        }

    def _plan_updates(self, scope):
        return [
            {"component": "OS", "from": "Amazon Linux 2", "to": "Amazon Linux 2023", "type": "major", "risk": "high"},
            {"component": "Kubernetes", "from": "v1.28.3", "to": "v1.28.4", "type": "patch", "risk": "low"},
            {"component": "Docker", "from": "24.0.6", "to": "24.0.7", "type": "patch", "risk": "low"},
            {"component": "nginx-ingress", "from": "1.9.3", "to": "1.9.5", "type": "patch", "risk": "low"},
        ]

    def _rollback_plan(self, updates):
        return [
            {"step": 1, "action": "Revert OS to previous AMI", "condition": "If node fails to join cluster"},
            {"step": 2, "action": "Rollback Kubernetes version", "condition": "If workloads fail to schedule"},
            {"step": 3, "action": "Restore previous deployment manifests", "condition": "If applications report errors"},
        ]
