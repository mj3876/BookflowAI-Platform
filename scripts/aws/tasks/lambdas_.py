"""task-lambdas · Tier 99-serverless (8 Lambdas + EventBridge + Kinesis ESM + API Gateway).

SAM template auto-fetches RDS/Redis/SF/Secret/Bucket params from existing stacks.
GCS transfer is handled by the Glue features_build job, not by a Lambda.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..lib import Stack, log
from ..lib.config import Config


def _package_sam(artifact_bucket: str) -> Path:
    """SAM 로컬 CodeUri → S3 업로드 후 패키징된 템플릿 경로 반환."""
    sam_dir   = Config.INFRA_ROOT / "99-serverless"
    build_dir = sam_dir / ".aws-sam"
    built     = build_dir / "build" / "template.yaml"

    # stale build 재사용 방지 — 매번 .aws-sam 삭제 후 fresh build.
    # (sam-template / lambda 소스 변경이 항상 반영 · 옛 ReservedConcurrentExecutions 등 잔재 제거)
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)
    log.info("  sam build 실행 중 (fresh)...")
    # Windows 에서 PATH 에 sam 없으면 SAM_CMD env 또는 default 위치 fallback
    sam_cmd = os.environ.get("SAM_CMD")
    if not sam_cmd:
        sam_cmd = shutil.which("sam") or shutil.which("sam.cmd") or r"C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd"
    # Python 3.13 을 PATH 에서 동적으로 탐색 (Windows 기본 설치 경로 fallback 포함).
    # SAM build 가 runtime=python3.13 과 로컬 python 버전 일치를 검증하므로 3.13 이 필수.
    py313_candidates = [
        shutil.which("python3.13"),
        shutil.which("py") and "py -3.13",  # Windows py launcher
    ]
    import glob as _glob
    py313_candidates += _glob.glob(r"C:\Users\*\AppData\Local\Programs\Python\Python313")
    py313_candidates += _glob.glob(r"C:\Python313")
    py313_dir = next((p for p in py313_candidates if p and os.path.isdir(str(p).split()[0]) or
                      (p and os.path.isfile(str(p)))), None)
    # 경로가 디렉터리인 경우 (설치 폴더) → PATH 프리픽스로 사용
    if py313_dir and os.path.isdir(str(py313_dir)):
        path_prefix = str(py313_dir)
    elif py313_dir:
        path_prefix = os.path.dirname(str(py313_dir))
    else:
        log.warn("python3.13 을 찾을 수 없음 · 시스템 PATH 로 sam build 시도")
        path_prefix = ""
    build_env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PATH": (path_prefix + os.pathsep if path_prefix else "") + os.environ.get("PATH", ""),
    }
    subprocess.run(
        [sam_cmd, "build", "--template-file", str(sam_dir / "sam-template.yaml")],
        cwd=str(sam_dir), check=True,
        env=build_env,
    )

    tmp_dir   = Path(tempfile.mkdtemp())
    packaged  = tmp_dir / "packaged.yaml"
    log.info(f"  Lambda 코드 S3 패키징 → s3://{artifact_bucket}/lambda-packages/")
    subprocess.run([
        "aws", "cloudformation", "package",
        "--template-file", str(built),
        "--s3-bucket", artifact_bucket,
        "--s3-prefix", "lambda-packages",
        "--output-template-file", str(packaged),
        "--region", Config.REGION,
    ], check=True,
       env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    return tmp_dir


def deploy() -> None:
    log.step("=== task-lambdas · 8 Lambdas SAM ===")
    if not Stack(tier="10", name="vpc-bookflow-ai", template="").exists():
        log.err("vpc-bookflow-ai 없음"); raise SystemExit(1)
    if not Stack(tier="20", name="kinesis", template="").exists():
        log.err("kinesis 없음 · task-data 먼저"); raise SystemExit(1)
    if not Stack(tier="20", name="rds", template="").exists():
        log.warn("RDS 없음 · pos-ingestor / spike-detect 비활성")
    if not Stack(tier="10", name="peering-bookflow-ai-data", template="").exists():
        log.warn("peering bookflow-ai-data 없음 · pos-ingestor → RDS 불가")

    params = {}

    sf_arn = Stack(tier="99", name="step-functions", template="").outputs().get("Etl3StateMachineArn", "")
    if sf_arn:
        params["StepFunctionsArn"] = sf_arn

    log.info(f"  SAM params: {sorted(params)}")

    artifact_bucket = f"{Config.PROJECT_NAME}-cp-artifacts-{Config.account_id()}"
    tmp_dir = _package_sam(artifact_bucket)

    Stack(tier="99", name="lambdas",
          template="packaged.yaml",
          template_root=tmp_dir,
          parameters=params,
          capabilities=["CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND", "CAPABILITY_IAM"],
          ).deploy()

    out = Stack(tier="99", name="lambdas", template="").outputs()
    log.info(f"secret-forwarder API: {out.get('SecretForwarderApiUrl', '?')}")
    log.step("=== task-lambdas 완료 ===")


def destroy() -> None:
    log.step("=== task-lambdas-down ===")
    Stack(tier="99", name="lambdas", template="").destroy()
    log.step("=== task-lambdas-down 완료 ===")
