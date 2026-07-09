import sys
import json
import argparse

from .core import Orchestrator, PlannerAgent, LearningEngine
from .infrastructure import Provisioner, AutoScaler
from .observability import Monitor, Pipeline
from .incidents import AnomalyDetector, RootCauseAnalyzer, Remediator, IncidentResponder
from .security import SecurityScanner
from .cost import CostOptimizer
from .backup import BackupManager
from .updates import Patcher


def init_orchestrator():
    orch = Orchestrator()
    orch.planner = PlannerAgent()
    orch.memory = LearningEngine()

    orch.register_agent("provisioner", Provisioner())
    orch.register_agent("scaler", AutoScaler())
    orch.register_agent("monitor", Monitor())
    orch.register_agent("pipeline", Pipeline())
    orch.register_agent("detector", AnomalyDetector())
    orch.register_agent("root_cause", RootCauseAnalyzer())
    orch.register_agent("remediator", Remediator())
    orch.register_agent("responder", IncidentResponder())
    orch.register_agent("scanner", SecurityScanner())
    orch.register_agent("optimizer", CostOptimizer())
    orch.register_agent("manager", BackupManager())
    orch.register_agent("patcher", Patcher())

    return orch


def cmd_interactive(args):
    print("\n  DeepOps v0.1.0 - Autonomous SRE Platform")
    print("  Type 'exit' to quit, 'help' for commands\n")
    orch = init_orchestrator()
    orch.start()

    while True:
        try:
            inp = input("deepops> ").strip()
            if not inp:
                continue
            if inp.lower() in ("exit", "quit"):
                orch.stop()
                break
            if inp.lower() == "help":
                print_help()
                continue
            if inp.lower() == "status":
                print(json.dumps(orch.status(), indent=2))
                continue

            if len(inp) > 10000:
                print("Error: Input too long")
                continue

            try:
                result = orch.dispatch(inp)
                print(json.dumps(result, indent=2))
            except Exception as e:
                print(json.dumps({"error": str(e)}, indent=2))

        except KeyboardInterrupt:
            orch.stop()
            break
        except EOFError:
            orch.stop()
            break


def cmd_execute(args):
    orch = init_orchestrator()
    orch.start()
    input_text = " ".join(args.input)
    if len(input_text) > 10000:
        print("Error: Input too long")
        return
    result = orch.dispatch(input_text)
    print(json.dumps(result, indent=2))
    orch.stop()


def cmd_status(args):
    orch = init_orchestrator()
    orch.start()
    print(json.dumps(orch.status(), indent=2))
    orch.stop()


def print_help():
    print("""
Commands:
  <natural language>   Describe what you want (deploy, fix, scale, etc.)
  status               Show orchestrator status
  help                 This help
  exit/quit            Exit

Examples:
  Deploy my MERN app
  Why is latency increasing?
  Fix production
  Optimize my AWS bill
  Scale the API
  Scan for vulnerabilities
""")


def main():
    parser = argparse.ArgumentParser(
        description="DeepOps - Autonomous SRE Platform (v0.1.0)"
    )
    parser.add_argument("input", nargs="*", help="Task description")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")

    args = parser.parse_args()

    if args.interactive or (not args.input):
        cmd_interactive(args)
    else:
        cmd_execute(args)


if __name__ == "__main__":
    main()
