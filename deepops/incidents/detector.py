from datetime import datetime


class AnomalyDetector:
    def __init__(self):
        self.detection_rules = []
        self.alerts = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "detect":
            return self.detect(params.get("signal", ""))
        if action == "add_rule":
            return self.add_rule(params.get("rule", {}))
        if action == "get_alerts":
            return {"alerts": self.alerts}
        return {"error": f"Unknown action: {action}"}

    def detect(self, signal):
        anomalies = self._analyze_signal(signal)
        if anomalies:
            alert = {
                "id": f"alt-{len(self.alerts) + 1}",
                "signal": signal,
                "anomalies": anomalies,
                "severity": anomalies[0].get("severity", "info"),
                "detected_at": datetime.utcnow().isoformat(),
            }
            self.alerts.append(alert)
            return alert
        return {"status": "normal", "signal": signal}

    def add_rule(self, rule):
        rule["id"] = f"rule-{len(self.detection_rules) + 1}"
        self.detection_rules.append(rule)
        return rule

    def _analyze_signal(self, signal):
        signal_lower = signal.lower()
        anomalies = []

        if any(w in signal_lower for w in ["latency", "slow", "timeout"]):
            anomalies.append({
                "type": "latency_spike",
                "metric": "p99_latency",
                "value": "280ms (baseline: 45ms)",
                "severity": "warning",
                "description": "P99 latency spike detected",
            })

        if any(w in signal_lower for w in ["error", "fail", "crash"]):
            anomalies.append({
                "type": "error_rate_burst",
                "metric": "error_rate",
                "value": "5.2% (baseline: 0.02%)",
                "severity": "critical",
                "description": "Error rate burst detected",
            })

        if any(w in signal_lower for w in ["memory", "oom", "leak"]):
            anomalies.append({
                "type": "memory_pressure",
                "metric": "memory_usage",
                "value": "94.7% (threshold: 85%)",
                "severity": "critical",
                "description": "Memory usage exceeds threshold",
            })

        if any(w in signal_lower for w in ["cpu", "spike"]):
            anomalies.append({
                "type": "cpu_spike",
                "metric": "cpu_usage",
                "value": "92% (baseline: 45%)",
                "severity": "warning",
                "description": "CPU usage spike detected",
            })

        if any(w in signal_lower for w in ["disk", "full", "storage"]):
            anomalies.append({
                "type": "disk_pressure",
                "metric": "disk_usage",
                "value": "88.6% (threshold: 80%)",
                "severity": "warning",
                "description": "Disk usage approaching capacity",
            })

        return anomalies
