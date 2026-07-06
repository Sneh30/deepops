from datetime import datetime


class CostOptimizer:
    def __init__(self):
        self.audits = []
        self.optimizations = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "audit":
            return self.audit(params.get("scope", "all"))
        if action == "optimize":
            return self.optimize(params.get("scope", "all"))
        if action == "savings_report":
            return self.savings_report()
        return {"error": f"Unknown action: {action}"}

    def audit(self, scope):
        findings = self._find_waste(scope)
        audit_report = {
            "id": f"audit-{len(self.audits) + 1}",
            "scope": scope,
            "timestamp": datetime.utcnow().isoformat(),
            "total_findings": len(findings),
            "monthly_waste": "$2,847.50",
            "findings": findings,
        }
        self.audits.append(audit_report)
        return audit_report

    def optimize(self, scope):
        actions = self._generate_actions(scope)
        optimization = {
            "id": f"opt-{len(self.optimizations) + 1}",
            "scope": scope,
            "actions": actions,
            "estimated_monthly_savings": "$2,150.00",
            "requires_approval": True,
        }
        self.optimizations.append(optimization)
        return optimization

    def savings_report(self):
        total_savings = sum(o.get("estimated_monthly_savings", 0) for o in self.optimizations)
        return {
            "total_optimizations": len(self.optimizations),
            "total_audits": len(self.audits),
            "total_monthly_savings": total_savings,
            "annual_projected": total_savings * 12,
        }

    def _find_waste(self, scope):
        return [
            {
                "resource": "i-0abcd1234 (web-server-01)",
                "type": "idle_instance",
                "utilization": "3% CPU over 30 days",
                "monthly_cost": "$86.40",
                "recommendation": "Stop or downsize to t3.nano",
            },
            {
                "resource": "i-0efgh5678 (db-replica-02)",
                "type": "oversized_instance",
                "utilization": "12% CPU, 8% memory",
                "monthly_cost": "$432.00",
                "recommendation": "Downsize from r5.xlarge to r5.large",
            },
            {
                "resource": "vol-0ijkl9012",
                "type": "unattached_volume",
                "size": "500 GiB gp3",
                "monthly_cost": "$50.00",
                "recommendation": "Delete or snapshot then delete",
            },
            {
                "resource": "eks-node-group-1",
                "type": "overprovisioned_node",
                "nodes": 8,
                "recommended": 4,
                "monthly_cost": "$1,200.00",
                "recommendation": "Reduce to 4 nodes or use spot instances",
            },
            {
                "resource": "s3://logs-archive",
                "type": "storage_class",
                "size": "2.3 TB",
                "monthly_cost": "$53.00",
                "recommendation": "Transition to Glacier Deep Archive ($8/month)",
            },
            {
                "resource": "ELB: prod-alb",
                "type": "unused_load_balancer",
                "monthly_cost": "$26.00",
                "recommendation": "Delete if not serving traffic",
            },
        ]

    def _generate_actions(self, scope):
        return [
            {"resource": "web-server-01", "action": "downsize", "from": "t3.medium", "to": "t3.nano", "savings": "$72/month"},
            {"resource": "db-replica-02", "action": "downsize", "from": "r5.xlarge", "to": "r5.large", "savings": "$216/month"},
            {"resource": "unattached-volumes", "action": "delete", "count": 3, "savings": "$150/month"},
            {"resource": "eks-node-group", "action": "scale_in", "from": 8, "to": 4, "savings": "$600/month"},
            {"resource": "s3-logs", "action": "transition_to_glacier", "savings": "$45/month"},
            {"resource": "reserved_instances", "action": "purchase_3yr", "coverage": "80% of baseline", "savings": "$1,067/month"},
        ]
