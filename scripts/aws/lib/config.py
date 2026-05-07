import os
from pathlib import Path

import boto3

#  load scripts/aws/config/.env.local (gitignored ·   )
_env_path = Path(__file__).resolve().parent.parent / "config" / ".env.local"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())


class Config:
    REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
    PROJECT_NAME = os.environ.get("BOOKFLOW_PROJECT", "bookflow")
    STACK_PREFIX = os.environ.get("BOOKFLOW_STACK_PREFIX", "bookflow")
    GITHUB_ORG = os.environ.get("BOOKFLOW_GITHUB_ORG", "MyosoonHwang")
    GITHUB_REPO = os.environ.get("BOOKFLOW_GITHUB_REPO", "BookFlowAI-Platform")

    REPO_ROOT = Path(__file__).resolve().parents[3]
    INFRA_ROOT = REPO_ROOT / "infra" / "aws"
    EXPORTS_DIR = REPO_ROOT / "exports"

    _account_id = None

    @classmethod
    def account_id(cls) -> str:
        if cls._account_id is None:
            cls._account_id = boto3.client("sts", region_name=cls.REGION).get_caller_identity()["Account"]
        return cls._account_id

    @classmethod
    def stack_name(cls, tier: str, name: str) -> str:
        return f"{cls.STACK_PREFIX}-{tier}-{name}"

    @classmethod
    def template_path(cls, relative: str) -> Path:
        return cls.INFRA_ROOT / relative
