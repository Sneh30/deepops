from datetime import datetime, UTC


class SecurityScanner:
    def __init__(self):
        self.scans = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "scan":
            return self.scan(params.get("scope", "all"))
        if action == "history":
            return {"scans": self.scans}
        return {"error": f"Unknown action: {action}"}

    def scan(self, scope):
        findings = self._run_checks(scope)
        scan_report = {
            "id": f"scan-{len(self.scans) + 1}",
            "scope": scope,
            "timestamp": datetime.now(UTC).isoformat(),
            "total_findings": len(findings),
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "medium": sum(1 for f in findings if f["severity"] == "medium"),
            "low": sum(1 for f in findings if f["severity"] == "low"),
            "findings": findings,
        }
        self.scans.append(scan_report)
        return scan_report

    def _run_checks(self, scope):
        return [
            {
                "type": "vulnerability",
                "severity": "critical",
                "package": "openssl",
                "installed": "1.1.1k",
                "fixed_in": "1.1.1l",
                "cve": "CVE-2024-1234",
            },
            {
                "type": "misconfiguration",
                "severity": "high",
                "resource": "S3 bucket: prod-assets",
                "issue": "Bucket publicly accessible",
                "recommendation": "Apply bucket policy to restrict access",
            },
            {
                "type": "secret_detection",
                "severity": "critical",
                "file": ".env.production",
                "finding": "Hardcoded AWS secret key detected",
                "recommendation": "Rotate key immediately, use Secrets Manager",
            },
            {
                "type": "iam_analysis",
                "severity": "medium",
                "role": "ci-deploy-role",
                "issue": "Overprivileged: has AdministratorAccess",
                "recommendation": "Scope down to least privilege",
            },
            {
                "type": "dependency",
                "severity": "high",
                "package": "lodash",
                "installed": "4.17.20",
                "fixed_in": "4.17.21",
                "cve": "CVE-2024-5678",
            },
            {
                "type": "network",
                "severity": "medium",
                "resource": "security-group: sg-eks-nodes",
                "issue": "Port 22 open to 0.0.0.0/0",
                "recommendation": "Restrict SSH access to bastion only",
            },
        ]
