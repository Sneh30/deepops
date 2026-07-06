class RootCauseAnalyzer:
    def __init__(self):
        self.analyses = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "analyze":
            return self.analyze(params.get("signal", ""))
        if action == "get_history":
            return {"analyses": self.analyses}
        return {"error": f"Unknown action: {action}"}

    def analyze(self, signal):
        chain = self._build_causal_chain(signal)
        analysis = {
            "signal": signal,
            "root_cause": chain[-1] if chain else None,
            "causal_chain": chain,
            "confidence": "high" if len(chain) > 2 else "medium",
        }
        self.analyses.append(analysis)
        return analysis

    def _build_causal_chain(self, signal):
        signal_lower = signal.lower()

        if "latency" in signal_lower or "slow" in signal_lower:
            return [
                {"level": 1, "observation": "API latency increased to 280ms p99", "type": "symptom"},
                {"level": 2, "observation": "Redis query timeout at 200ms", "type": "symptom"},
                {"level": 3, "observation": "Node.js event loop blocked", "type": "symptom"},
                {"level": 4, "observation": "High disk IOPS on EBS volume", "type": "indicator"},
                {"level": 5, "observation": "EBS gp2 volume IOPS exhausted (baseline 300, peak 1200)", "type": "cause"},
                {"level": 6, "observation": "Recent deployment v2.3.1 changed cache TTL from 1h to 30s", "type": "root_cause"},
            ]

        if "memory" in signal_lower or "oom" in signal_lower:
            return [
                {"level": 1, "observation": "Container OOMKilled at 04:15 UTC", "type": "symptom"},
                {"level": 2, "observation": "Memory usage grew 15% per hour over 6 hours", "type": "indicator"},
                {"level": 3, "observation": "Heap snapshot shows unclosed database connections", "type": "indicator"},
                {"level": 4, "observation": "Connection pool set to infinite in v2.3.0 config", "type": "root_cause"},
            ]

        if "error" in signal_lower or "crash" in signal_lower:
            return [
                {"level": 1, "observation": "HTTP 502 errors increasing", "type": "symptom"},
                {"level": 2, "observation": "Workers restarting in crash loop", "type": "symptom"},
                {"level": 3, "observation": "Health check failing on /api/health", "type": "indicator"},
                {"level": 4, "observation": "Database connection pool exhausted", "type": "indicator"},
                {"level": 5, "observation": "Connection leak in ORM v2.3.0 migration", "type": "root_cause"},
            ]

        if "cpu" in signal_lower:
            return [
                {"level": 1, "observation": "CPU usage at 99% on node-3", "type": "symptom"},
                {"level": 2, "observation": "Background batch job running during peak hours", "type": "indicator"},
                {"level": 3, "observation": "Cron schedule changed from 02:00 to 14:00 in last deploy", "type": "root_cause"},
            ]

        return [
            {"level": 1, "observation": f"Signal detected: {signal}", "type": "symptom"},
            {"level": 2, "observation": "No known pattern matches in incident history", "type": "indicator"},
            {"level": 3, "observation": "Insufficient data for root cause determination", "type": "unknown"},
        ]
