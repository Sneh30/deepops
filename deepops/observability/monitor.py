from datetime import datetime, UTC


class Monitor:
    def __init__(self):
        self.metrics = {}
        self.checks = []
        self.watches = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "watch":
            return self.watch(params.get("target", "unknown"))
        if action == "get_metrics":
            return self.get_metrics(params.get("name", "all"))
        if action == "health_check":
            return self.health_check()
        if action == "list_watches":
            return {"watches": self.watches}
        return {"error": f"Unknown action: {action}"}

    def watch(self, target):
        watch = {
            "target": target,
            "since": datetime.now(UTC).isoformat(),
            "status": "watching",
            "metrics_collected": ["cpu", "memory", "disk", "network", "latency", "error_rate"],
        }
        self.watches.append(watch)
        return watch

    def get_metrics(self, name="all"):
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "metrics": {
                "cpu": {"avg": 45.2, "p95": 78.3, "p99": 92.1},
                "memory": {"avg": 62.8, "p95": 85.4, "p99": 94.7},
                "disk": {"avg": 55.3, "p95": 72.1, "p99": 88.6},
                "network": {"inbound_mbps": 120, "outbound_mbps": 85},
                "latency": {"avg_ms": 45, "p95_ms": 120, "p99_ms": 280},
                "error_rate": {"avg": 0.02, "recent": 0.05},
            },
        }

    def health_check(self):
        checks = [
            {"service": "api-server", "status": "healthy", "latency_ms": 12},
            {"service": "database", "status": "healthy", "latency_ms": 3},
            {"service": "cache", "status": "healthy", "latency_ms": 1},
            {"service": "queue", "status": "degraded", "latency_ms": 150},
        ]
        self.checks = checks
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "overall": "degraded",
            "checks": checks,
        }
