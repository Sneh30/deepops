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
        task_lower = task.lower()
        matches = []

        if "deploy" in task_lower:
            matches.append((10, [
                {"agent": "provisioner", "action": "provision", "params": {"spec": task}},
                {"agent": "monitor", "action": "watch", "params": {"target": "deployment"}},
            ]))

        if any(w in task_lower for w in ["fix", "repair", "heal", "restart"]):
            matches.append((30, [
                {"agent": "remediator", "action": "remediate", "params": {"issue": task}},
            ]))

        if any(w in task_lower for w in ["latency", "slow", "timeout", "error"]):
            matches.append((25, [
                {"agent": "detector", "action": "detect", "params": {"signal": task}},
                {"agent": "root_cause", "action": "analyze", "params": {"signal": task}},
            ]))

        if any(w in task_lower for w in ["scale", "traffic", "load"]):
            matches.append((20, [
                {"agent": "scaler", "action": "analyze_and_scale", "params": {"context": task}},
            ]))

        if any(w in task_lower for w in ["bill", "cost", "spend", "optimize"]):
            matches.append((15, [
                {"agent": "optimizer", "action": "audit", "params": {"scope": "all"}},
                {"agent": "optimizer", "action": "optimize", "params": {"scope": "all"}},
            ]))

        if any(w in task_lower for w in ["update", "patch", "upgrade"]):
            matches.append((12, [
                {"agent": "patcher", "action": "update", "params": {"scope": "all"}},
            ]))

        if any(w in task_lower for w in ["backup", "snapshot", "disaster"]):
            matches.append((10, [
                {"agent": "manager", "action": "backup", "params": {"scope": "all"}},
            ]))

        if any(w in task_lower for w in ["secure", "vuln", "scan"]):
            matches.append((8, [
                {"agent": "scanner", "action": "scan", "params": {"scope": "all"}},
            ]))

        if matches:
            matches.sort(key=lambda x: -x[0])
            steps = matches[0][1]
        else:
            steps = [{"agent": "provisioner", "action": "analyze", "params": {"input": task}}]

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
        if any(w in task_lower for w in ["fix", "repair", "heal"]):
            return "remediation"
        if "deploy" in task_lower:
            return "deployment"
        if "scale" in task_lower:
            return "scaling"
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
