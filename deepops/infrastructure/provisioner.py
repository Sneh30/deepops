class Provisioner:
    def __init__(self):
        self.deployments = []

    def execute(self, action, params=None):
        params = params or {}
        if action == "provision":
            return self.provision(params.get("spec", ""))
        if action == "analyze":
            return self.analyze(params.get("input", ""))
        if action == "list":
            return self.list_deployments()
        return {"error": f"Unknown action: {action}"}

    def provision(self, spec):
        deployment = {
            "id": f"dep-{len(self.deployments) + 1}",
            "spec": spec,
            "status": "provisioning",
            "provider": "aws",
            "orchestration": "kubernetes",
        }

        self.deployments.append(deployment)
        return {
            "status": "provisioning",
            "deployment": deployment,
            "message": "Analyzing requirements...",
            "steps": [
                "Resolved cloud provider: AWS",
                "Selected region: us-east-1",
                "Orchestration: Kubernetes (EKS)",
                "Creating VPC and networking...",
                "Provisioning node group...",
                "Setting up load balancer...",
            ],
        }

    def analyze(self, input_text):
        stack = self._detect_stack(input_text)
        return {
            "input": input_text,
            "detected_stack": stack,
            "recommended_provider": "aws",
            "recommended_instance": self._recommend_instance(stack),
            "estimated_monthly_cost": "$150-300",
        }

    def list_deployments(self):
        return {"deployments": self.deployments}

    def _detect_stack(self, text):
        text = text.lower()
        if "mern" in text or "react" in text or "node" in text or "mongodb" in text:
            return "MERN Stack"
        if "django" in text or "python" in text:
            return "Django + PostgreSQL"
        if "spring" in text or "java" in text:
            return "Spring Boot + MySQL"
        if "laravel" in text or "php" in text:
            return "Laravel + MySQL"
        if "next" in text or "nuxt" in text:
            return "Next.js + Vercel-compatible"
        if "go" in text or "golang" in text:
            return "Go + Redis + PostgreSQL"
        return "Generic web application"

    def _recommend_instance(self, stack):
        base = {"cpu": 2, "memory": "4GB", "type": "t3.medium"}
        if "MERN" in stack:
            return {"cpu": 4, "memory": "8GB", "type": "t3.large"}
        if "Django" in stack:
            return {"cpu": 2, "memory": "4GB", "type": "t3.medium"}
        if "Spring" in stack:
            return {"cpu": 4, "memory": "16GB", "type": "t3.xlarge"}
        return base
