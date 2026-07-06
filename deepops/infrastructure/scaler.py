from datetime import datetime


class AutoScaler:
    def __init__(self):
        self.policies = {}
        self.scaling_events = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "analyze_and_scale":
            return self.analyze_and_scale(params.get("context", ""))
        if action == "set_policy":
            return self.set_policy(params.get("name"), params.get("config", {}))
        if action == "get_history":
            return self.get_history()
        return {"error": f"Unknown action: {action}"}

    def analyze_and_scale(self, context):
        prediction = self._predict_traffic(context)
        decision = "scale_up" if prediction.get("load_increasing") else "maintain"

        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "context": context,
            "prediction": prediction,
            "decision": decision,
        }
        self.scaling_events.append(event)

        return {
            "analysis": prediction,
            "decision": decision,
            "recommended_replicas": prediction.get("recommended_replicas", 3),
            "reasoning": prediction.get("reasoning", ""),
            "requires_approval": True,
        }

    def set_policy(self, name, config):
        self.policies[name] = config
        return {"policy": name, "config": config, "status": "active"}

    def get_history(self):
        return {"scaling_events": self.scaling_events}

    def _predict_traffic(self, context):
        context_lower = context.lower()
        load_increasing = any(w in context_lower for w in ["traffic", "scale", "load", "growing", "spike"])
        return {
            "load_increasing": load_increasing,
            "current_load": "medium",
            "predicted_load": "high" if load_increasing else "medium",
            "recommended_replicas": 5 if load_increasing else 3,
            "confidence": "medium",
            "reasoning": "Predictive analysis based on traffic patterns. " +
                        "Scaling proactively to handle anticipated demand." if load_increasing
                        else "Load is stable. Maintaining current capacity.",
        }
