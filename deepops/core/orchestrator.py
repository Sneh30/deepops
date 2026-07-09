import json
import os
from datetime import datetime, UTC


class Orchestrator:
    def __init__(self):
        self.agents = {}
        self.planner = None
        self.memory = None
        self._running = False

    def register_agent(self, name, agent):
        self.agents[name] = agent

    def start(self):
        self._running = True
        return {"status": "started", "timestamp": datetime.now(UTC).isoformat()}

    def stop(self):
        self._running = False
        return {"status": "stopped", "timestamp": datetime.now(UTC).isoformat()}

    def dispatch(self, task):
        if not self._running:
            return {"error": "Orchestrator not running"}

        if not self.planner:
            return {"error": "Planner not configured", "task": task}

        plan = self.planner.plan(task)
        results = []

        for step in plan.get("steps", []):
            agent_name = step.get("agent")
            action = step.get("action")
            params = step.get("params", {})

            if agent_name in self.agents:
                try:
                    result = self.agents[agent_name].execute(action, params)
                except Exception as e:
                    result = {"error": str(e), "action": action, "agent": agent_name}

                if isinstance(result, dict) and result.get("requires_approval"):
                    result["approved"] = False
                    result["message"] = "Requires manual approval — skipped"

                results.append({
                    "agent": agent_name,
                    "action": action,
                    "result": result,
                })

        return {
            "task": task,
            "plan": plan,
            "results": results,
            "completed_at": datetime.now(UTC).isoformat(),
        }

    def status(self):
        return {
            "running": self._running,
            "agents": list(self.agents.keys()),
            "agent_count": len(self.agents),
        }
