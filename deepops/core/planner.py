import re


class PlannerAgent:
    def __init__(self):
        self.capabilities = {
            "deploy": ["provisioner", "scaler"],
            "monitor": ["monitor", "pipeline"],
            "investigate": ["detector", "root_cause"],
            "repair": ["remediator", "responder"],
            "secure": ["scanner"],
            "optimize": ["optimizer"],
            "backup": ["manager"],
            "update": ["patcher"],
        }

    def plan(self, task):
        steps = []
        task_lower = task.lower()

        if "deploy" in task_lower:
            steps.append({"agent": "provisioner", "action": "provision", "params": {"spec": task}})
            steps.append({"agent": "monitor", "action": "watch", "params": {"target": "deployment"}})

        elif any(w in task_lower for w in ["scale", "traffic", "load"]):
            steps.append({"agent": "scaler", "action": "analyze_and_scale", "params": {"context": task}})

        elif any(w in task_lower for w in ["latency", "slow", "timeout", "error"]):
            steps.append({"agent": "detector", "action": "detect", "params": {"signal": task}})
            steps.append({"agent": "root_cause", "action": "analyze", "params": {"signal": task}})

        elif any(w in task_lower for w in ["fix", "repair", "heal", "restart"]):
            steps.append({"agent": "remediator", "action": "remediate", "params": {"issue": task}})

        elif any(w in task_lower for w in ["bill", "cost", "spend", "optimize"]):
            steps.append({"agent": "optimizer", "action": "audit", "params": {"scope": "all"}})
            steps.append({"agent": "optimizer", "action": "optimize", "params": {"scope": "all"}})

        elif any(w in task_lower for w in ["backup", "snapshot", "disaster"]):
            steps.append({"agent": "manager", "action": "backup", "params": {"scope": "all"}})

        elif any(w in task_lower for w in ["update", "patch", "upgrade"]):
            steps.append({"agent": "patcher", "action": "update", "params": {"scope": "all"}})

        elif any(w in task_lower for w in ["secure", "vuln", "scan"]):
            steps.append({"agent": "scanner", "action": "scan", "params": {"scope": "all"}})

        else:
            steps.append({"agent": "provisioner", "action": "analyze", "params": {"input": task}})

        return {"task": task, "steps": steps, "requires_approval": True}

    def analyze_task(self, task):
        return {
            "task": task,
            "detected_intent": self._classify_intent(task),
            "confidence": "medium",
            "requires_approval": True,
        }

    def _classify_intent(self, task):
        task_lower = task.lower()
        if "deploy" in task_lower:
            return "deployment"
        if "scale" in task_lower:
            return "scaling"
        if any(w in task_lower for w in ["fix", "repair", "heal"]):
            return "remediation"
        if any(w in task_lower for w in ["latency", "slow", "error"]):
            return "investigation"
        if "cost" in task_lower or "bill" in task_lower:
            return "cost_optimization"
        if "backup" in task_lower:
            return "backup"
        if "update" in task_lower or "patch" in task_lower:
            return "update"
        if "secure" in task_lower or "scan" in task_lower:
            return "security"
        return "unknown"
