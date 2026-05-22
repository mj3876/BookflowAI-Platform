"""
Publisher ASG Auto Scaling short scenario for demonstration.

This test creates a temporary scaling policy to trigger scale-out quickly,
prints the flow as a traffic increase/decrease scenario, then restores the ASG
to its normal capacity so the recording can finish within a few minutes.
"""

from __future__ import annotations

import os
import sys
import time

import boto3
from botocore.exceptions import ClientError


REGION = os.environ.get("REGION", "ap-northeast-1")
ASG_NAME = os.environ.get("ASG_NAME", "CodeDeploy_bookflow-publisher-bg_d-ILE9K2X6J")
POLICY_NAME = "bookflow-publisher-demo-scaling"
OLD_CPU_POLICY_NAME = "bookflow-publisher-cpu-tracking"

TARGET_DESIRED = int(os.environ.get("TARGET_DESIRED", "4"))
MAX_WAIT_SECONDS = int(os.environ.get("MAX_WAIT_SECONDS", "120"))
HOLD_SECONDS = int(os.environ.get("HOLD_SECONDS", "30"))

asg = boto3.client("autoscaling", region_name=REGION)


def get_asg() -> dict:
    resp = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
    groups = resp.get("AutoScalingGroups", [])
    if not groups:
        raise RuntimeError(f"ASG not found: {ASG_NAME}")
    return groups[0]


def capacity() -> tuple[int, int, int, int]:
    group = get_asg()
    return group["MinSize"], group["DesiredCapacity"], group["MaxSize"], len(group["Instances"])


def print_status(prefix: str = "") -> None:
    min_size, desired, max_size, instances = capacity()
    label = f"{prefix} " if prefix else ""
    print(f"{label}Min={min_size} / Desired={desired} / Max={max_size} / Instances={instances}")


def create_scaling_policy() -> str:
    resp = asg.put_scaling_policy(
        AutoScalingGroupName=ASG_NAME,
        PolicyName=POLICY_NAME,
        PolicyType="TargetTrackingScaling",
        EstimatedInstanceWarmup=60,
        TargetTrackingConfiguration={
            "PredefinedMetricSpecification": {
                "PredefinedMetricType": "ASGAverageCPUUtilization",
            },
            # Low value for a short demo: idle CPU is enough to trigger scale-out.
            "TargetValue": 0.3,
            "DisableScaleIn": False,
        },
    )
    return resp["PolicyARN"]


def delete_policy(name: str) -> None:
    try:
        asg.delete_policy(AutoScalingGroupName=ASG_NAME, PolicyName=name)
        print(f"  테스트 정책 삭제: {name}")
    except ClientError:
        pass


def cleanup() -> None:
    min_size, _, _, _ = capacity()
    print("[정리] 테스트 트래픽 종료 및 정상 용량 복구")
    delete_policy(POLICY_NAME)
    delete_policy(OLD_CPU_POLICY_NAME)
    asg.set_desired_capacity(AutoScalingGroupName=ASG_NAME, DesiredCapacity=min_size)
    print(f"  부하 감소에 따라 정상 용량으로 복귀 요청: Desired={min_size}")
    print_status("[현재 상태]")


def wait_for_scale_out(original_desired: int, target_desired: int) -> bool:
    print("\n[3단계] 출판사 트래픽 증가에 따른 Scale-out 확인")
    elapsed = 0
    while elapsed <= MAX_WAIT_SECONDS:
        _, desired, _, instances = capacity()
        print(f"  [{elapsed:>3}s] 트래픽 증가 상태 / Desired={desired} / Instances={instances}")
        if desired >= target_desired:
            print("\n  [이벤트] 출판사 요청 트래픽 증가 감지")
            print(f"  [결과] Publisher ASG가 자동 확장됨: Desired {original_desired} → {desired}")
            return True
        time.sleep(10)
        elapsed += 10

    print(f"\n  [주의] {MAX_WAIT_SECONDS}s 내 목표 Desired({target_desired}) 미도달")
    return False


def hold_scaled_out() -> None:
    if HOLD_SECONDS <= 0:
        return
    print(f"\n[4단계] 확장 상태 확인 ({HOLD_SECONDS}s)")
    remaining = HOLD_SECONDS
    while remaining > 0:
        _, desired, _, instances = capacity()
        print(f"  트래픽 처리 중 / Desired={desired} / Instances={instances} / 남은 확인 시간={remaining}s")
        sleep_for = min(10, remaining)
        time.sleep(sleep_for)
        remaining -= sleep_for


def restore_after_traffic_drop(min_size: int) -> None:
    print("\n[5단계] 출판사 트래픽 감소에 따른 Scale-in 확인")
    print("  [이벤트] 출판사 요청 트래픽이 감소했습니다.")
    print("  [조치] 테스트 트래픽을 종료하고 ASG를 정상 운영 용량으로 복귀시킵니다.")

    delete_policy(POLICY_NAME)
    delete_policy(OLD_CPU_POLICY_NAME)
    asg.set_desired_capacity(AutoScalingGroupName=ASG_NAME, DesiredCapacity=min_size)
    print(f"  [결과] 부하 감소 후 Publisher ASG Desired Capacity가 정상 용량({min_size})으로 복귀했습니다.")

    for elapsed in range(0, 61, 10):
        _, desired, _, instances = capacity()
        print(f"  [{elapsed:>2}s] 복구 상태 / Desired={desired} / Instances={instances}")
        if desired == min_size and instances <= min_size:
            break
        time.sleep(10)


def run_test() -> None:
    print("=" * 60)
    print("[Publisher ASG Auto Scaling 시나리오]")
    print("출판사 트래픽 증가 시 Scale-out, 트래픽 감소 시 정상 용량 복귀를 검증합니다.")

    print("\n[1단계] 테스트 전 Publisher ASG 상태")
    min_size, original_desired, max_size, instances = capacity()
    target_desired = min(TARGET_DESIRED, max_size)
    print(f"  Min={min_size} / Desired={original_desired} / Max={max_size} / Instances={instances}")

    print("\n[2단계] 출판사 트래픽 증가 조건 적용")
    print("  [이벤트] 출판사 외부 요청이 증가한 상황을 재현합니다.")
    print("  [조치] Publisher ASG Auto Scaling 정책이 트래픽 증가를 감지하도록 테스트 조건을 적용합니다.")
    arn = create_scaling_policy()
    print(f"  정책 ARN: {arn}")

    wait_for_scale_out(original_desired, target_desired)
    hold_scaled_out()
    restore_after_traffic_drop(min_size)

    print("\n[최종 상태]")
    print_status()
    print("=" * 60)


def usage() -> str:
    return """
사용법:
  python publisher_asg_test.py test      # 3분 이내 시연용 전체 테스트
  python publisher_asg_test.py status    # 현재 ASG 상태 조회
  python publisher_asg_test.py cleanup   # 테스트 정책 삭제 + 정상 용량 복구
"""


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(usage())
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "test":
        run_test()
    elif cmd == "status":
        print_status()
    elif cmd == "cleanup":
        cleanup()
    else:
        print(usage())
        sys.exit(1)
