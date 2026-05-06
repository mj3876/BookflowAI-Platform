#!/usr/bin/env python
"""BookFlow AWS deploy CLI.

Usage:
  python scripts/aws/bookflow.py phase0 [--down]
  python scripts/aws/bookflow.py base-up
  python scripts/aws/bookflow.py base-down
  python scripts/aws/bookflow.py task <name> [--down]
  python scripts/aws/bookflow.py task --all [--down]
  python scripts/aws/bookflow.py scenario ha [--revert]
  python scripts/aws/bookflow.py wipe-all
  python scripts/aws/bookflow.py status
"""
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.aws.lib import Config, log
from scripts.aws.tasks import base, cicd_ecs, cicd_eks, cross_cloud, foundation, full_stack, scenario_ha, wipe_all

TASK_MODULES = {
    "data":           "data",
    "msa-pods":       "msa_pods",
    "eks-addons":     "eks_addons",
    "mocks":          "mocks",
    "etl-streaming":  "etl_streaming",
    "publisher":      "publisher",
    "auth-pod":       "auth_pod",
    "forecast":       "forecast",
    "lambdas":        "lambdas_",
    "glue":           "glue",
    "client-vpn":     "client_vpn",
    "rds-seed":       "rds_seed",
}


def cmd_phase0(args):
    if args.down:
        foundation.destroy()
    else:
        foundation.deploy()


def cmd_base_up(args):
    base.deploy()


def cmd_base_down(args):
    base.destroy()


def cmd_task(args):
    if args.all:
        if args.down:
            full_stack.destroy()
        else:
            full_stack.deploy()
        return
    if args.name not in TASK_MODULES:
        log.err(f"Unknown task: {args.name} · available: {list(TASK_MODULES.keys())}")
        raise SystemExit(2)
    mod = importlib.import_module(f"scripts.aws.tasks.{TASK_MODULES[args.name]}")
    if args.down:
        mod.destroy()
    else:
        mod.deploy()


def cmd_scenario(args):
    if args.scenario == "ha":
        if args.revert:
            scenario_ha.destroy()
        else:
            scenario_ha.deploy()
    else:
        log.err(f"Unknown scenario: {args.scenario}")
        raise SystemExit(2)


def cmd_wipe_all(args):
    wipe_all.destroy()


def cmd_status(args):
    from scripts.aws.lib.stack import list_bookflow_stacks
    stacks = list_bookflow_stacks()
    if not stacks:
        log.info("No bookflow stacks")
        return
    for s in sorted(stacks, key=lambda x: x["StackName"]):
        log.info(f"  {s['StackName']:50} {s['StackStatus']}")


def main():
    p = argparse.ArgumentParser(prog="bookflow")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("phase0", help="Tier 00 foundation ()")
    sp.add_argument("--down", action="store_true")
    sp.set_defaults(func=cmd_phase0)

    sub.add_parser("base-up", help="base-up (Tier 10 + Tier 30 base)").set_defaults(func=cmd_base_up)
    sub.add_parser("base-down", help="base-down (Tier 10-99 )").set_defaults(func=cmd_base_down)

    sub.add_parser("cross-cloud-up", help="cross-cloud minimum (4 VPC + CGW + TGW + VPN)").set_defaults(
        func=lambda a: cross_cloud.deploy())
    sub.add_parser("cross-cloud-down", help="cross-cloud minimum destroy").set_defaults(
        func=lambda a: cross_cloud.destroy())

    sub.add_parser("cicd-eks-up", help="CICD pipeline for EKS pods (CodePipeline + CodeBuild)").set_defaults(
        func=lambda a: cicd_eks.deploy())
    sub.add_parser("cicd-eks-down", help="CICD pipeline for EKS destroy").set_defaults(
        func=lambda a: cicd_eks.destroy())

    sub.add_parser("cicd-ecs-up", help="CICD pipeline for ECS sims (CodePipeline + CodeBuild)").set_defaults(
        func=lambda a: cicd_ecs.deploy())
    sub.add_parser("cicd-ecs-down", help="CICD pipeline for ECS destroy").set_defaults(
        func=lambda a: cicd_ecs.destroy())

    sp = sub.add_parser("task", help="task <name> [--down] | --all")
    sp.add_argument("name", nargs="?", help=f"task name: {', '.join(TASK_MODULES.keys())}")
    sp.add_argument("--all", action="store_true", help="full-stack (data + msa-pods + etl-streaming + publisher)")
    sp.add_argument("--down", action="store_true")
    sp.set_defaults(func=cmd_task)

    sp = sub.add_parser("scenario", help="scenario <name> [--revert]")
    sp.add_argument("scenario", choices=["ha"])
    sp.add_argument("--revert", action="store_true")
    sp.set_defaults(func=cmd_scenario)

    sub.add_parser("wipe-all", help="  destroy (Tier 00 )").set_defaults(func=cmd_wipe_all)
    sub.add_parser("status", help="list bookflow stacks").set_defaults(func=cmd_status)

    args = p.parse_args()
    log.info(f"Region={Config.REGION} · Account={Config.account_id()} · Project={Config.PROJECT_NAME}")
    args.func(args)


if __name__ == "__main__":
    main()
