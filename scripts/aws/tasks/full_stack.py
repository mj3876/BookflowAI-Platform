"""task-full-stack ·  task  (data → msa-pods → etl-streaming → publisher).

cross-cloud (auth-pod / forecast / client-vpn)  rds-seed  .
"""
from ..lib import log
from . import data, etl_streaming, msa_pods, publisher


def deploy() -> None:
    log.step("=== task-full-stack ·  deploy ===")
    data.deploy()
    msa_pods.deploy()
    etl_streaming.deploy()
    publisher.deploy()
    log.step("=== task-full-stack  ===")


def destroy() -> None:
    """ task  destroy (base  ). base-down  base  ."""
    log.step("=== task-full-stack-down ·  task  destroy ===")
    from . import auth_pod, client_vpn, forecast, glue, lambdas_, rds_seed
    glue.destroy()
    lambdas_.destroy()
    publisher.destroy()
    etl_streaming.destroy()
    rds_seed.destroy()
    msa_pods.destroy()
    data.destroy()
    client_vpn.destroy()
    forecast.destroy()
    auth_pod.destroy()
    log.step("=== task-full-stack-down  ===")
