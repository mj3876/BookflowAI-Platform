"""
Publisher ASG Auto Scaling 장애 시나리오 테스트 스크립트
대상 ASG: CodeDeploy_bookflow-publisher-bg_d-TOW7F0I5J

테스트 흐름 (python publisher_asg_test.py test):
  1. 테스트 전 ASG 상태 스냅샷 저장
  2. TargetTracking 정책 생성 (CPU 0.3% 목표 → 유휴 CPU ~0.4%가 즉시 초과)
  3. Desired가 증가할 때까지 모니터링 (최대 10분)
  4. 스케일 아웃 확인 후 자동 원복 (정책 삭제 + Desired 원래값 복구)
"""
import time
import boto3

REGION = "ap-northeast-1"
ASG_NAME = "CodeDeploy_bookflow-publisher-bg_d-TOW7F0I5J"
POLICY_NAME = "bookflow-publisher-cpu-tracking"

asg = boto3.client("autoscaling", region_name=REGION)


def get_asg():
    resp = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
    return resp["AutoScalingGroups"][0]


def get_status():
    g = get_asg()
    print(f"Min={g['MinSize']} / Desired={g['DesiredCapacity']} / Max={g['MaxSize']} / Instances={len(g['Instances'])}")


def create_scaling_policy(target_cpu: float):
    resp = asg.put_scaling_policy(
        AutoScalingGroupName=ASG_NAME,
        PolicyName=POLICY_NAME,
        PolicyType="TargetTrackingScaling",
        TargetTrackingConfiguration={
            "PredefinedMetricSpecification": {
                "PredefinedMetricType": "ASGAverageCPUUtilization"
            },
            "TargetValue": target_cpu,
        },
    )
    return resp["PolicyARN"]


def delete_scaling_policy():
    try:
        asg.delete_policy(AutoScalingGroupName=ASG_NAME, PolicyName=POLICY_NAME)
        print("  정책 삭제 완료")
    except asg.exceptions.ClientError:
        pass  # 이미 없으면 무시


def restore(original_desired: int):
    """테스트 전 상태로 원복: 정책 삭제 + Desired 복구."""
    print("\n[원복 시작]")
    delete_scaling_policy()
    asg.set_desired_capacity(AutoScalingGroupName=ASG_NAME, DesiredCapacity=original_desired)
    print(f"  Desired → {original_desired}")
    print("[원복 완료] 테스트 전 상태로 돌아왔습니다.")


def run_test():
    """전체 테스트 자동 실행: 정책 생성 → 스케일 아웃 확인 → 원복."""
    print("=" * 50)
    print("[1단계] 테스트 전 상태 확인")
    g = get_asg()
    original_desired = g["DesiredCapacity"]
    print(f"  Desired={original_desired} / Instances={len(g['Instances'])}")

    print("\n[2단계] 스케일링 정책 생성 (CPU 목표: 0.3%)")
    arn = create_scaling_policy(target_cpu=0.3)
    print(f"  정책 ARN: {arn}")

    print("\n[3단계] 스케일 아웃 모니터링 (최대 10분)")
    timeout = 600
    interval = 15
    elapsed = 0
    success = False

    while elapsed < timeout:
        g = get_asg()
        current = g["DesiredCapacity"]
        instances = len(g["Instances"])
        print(f"  [{elapsed:>3}s] Desired={current} / Instances={instances}")

        if current > original_desired:
            print(f"\n  ✅ 스케일 아웃 확인! Desired {original_desired} → {current}")
            success = True
            break

        time.sleep(interval)
        elapsed += interval

    if not success:
        print("\n  ❌ 10분 내 스케일 아웃 미발생")

    restore(original_desired)
    print("=" * 50)


if __name__ == "__main__":
    import sys

    usage = """
사용법:
  python publisher_asg_test.py test      # 전체 자동 테스트 (생성→확인→원복)
  python publisher_asg_test.py status    # 현재 ASG 상태만 조회
  python publisher_asg_test.py cleanup   # 수동 원복 (테스트 중단 시)
"""
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "test":
        run_test()
    elif cmd == "status":
        get_status()
    elif cmd == "cleanup":
        g = get_asg()
        restore(g["MinSize"])
    else:
        print(usage)
