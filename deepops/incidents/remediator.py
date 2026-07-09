from datetime import datetime, UTC


class Remediator:
    def __init__(self):
        self.remediations = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "remediate":
            return self.remediate(params.get("issue", ""))
        if action == "status":
            return self.get_status()
        return {"error": f"Unknown action: {action}"}

    def remediate(self, issue):
        actions = self._plan_remediation(issue)
        remediation = {
            "issue": issue,
            "actions": actions,
            "requires_approval": any(a.get("risk", "low") == "high" for a in actions),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        self.remediations.append(remediation)
        return remediation

    def get_status(self):
        return {"total_remediations": len(self.remediations)}

    def _plan_remediation(self, issue):
        issue_lower = issue.lower()

        if "memory" in issue_lower or "oom" in issue_lower or "leak" in issue_lower:
            return [
                {"action": "restart_container", "target": "affected_service", "risk": "medium"},
                {"action": "increase_memory_limit", "target": "deployment", "from": "512Mi", "to": "1Gi", "risk": "low"},
                {"action": "profile_heap", "target": "application", "risk": "low"},
                {"action": "redeploy_with_fix", "target": "application", "risk": "medium"},
            ]

        if "disk" in issue_lower or "full" in issue_lower or "storage" in issue_lower:
            return [
                {"action": "cleanup_temp_files", "target": "volume", "risk": "low"},
                {"action": "expand_volume", "target": "EBS volume", "from": "100Gi", "to": "200Gi", "risk": "low"},
                {"action": "setup_log_rotation", "target": "all_services", "risk": "low"},
            ]

        if "latency" in issue_lower or "slow" in issue_lower or "timeout" in issue_lower:
            return [
                {"action": "restart_cache_service", "target": "Redis", "risk": "medium"},
                {"action": "scale_read_replicas", "target": "database", "count": 2, "risk": "low"},
                {"action": "optimize_connection_pool", "target": "application", "risk": "low"},
            ]

        if "crash" in issue_lower or "error" in issue_lower or "fail" in issue_lower:
            return [
                {"action": "rollback_deployment", "target": "application", "from": "v2.3.1", "to": "v2.3.0", "risk": "medium"},
                {"action": "restart_affected_pods", "target": "deployment", "risk": "low"},
                {"action": "run_db_migration_repair", "target": "database", "risk": "high"},
            ]

        if "cert" in issue_lower or "tls" in issue_lower or "ssl" in issue_lower:
            return [
                {"action": "renew_certificate", "target": "ingress", "risk": "low"},
                {"action": "verify_certificate_chain", "target": "load_balancer", "risk": "low"},
            ]

        return [
            {"action": "investigate_further", "target": "unknown", "risk": "low"},
            {"action": "collect_diagnostics", "target": "all_services", "risk": "low"},
        ]
