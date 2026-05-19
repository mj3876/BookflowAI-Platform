#!/usr/bin/env python3
"""BookFlow 운영 대시보드 빌더.

dashboards/ 의 각 Row 모듈을 import → Grafana Foundation SDK 로 JSON 직렬화 →
dist/*.json 으로 출력한다. provisioning(IaC)이 dist/ 를 dashboard configmap 으로
사용한다.

사용법:
  py build.py                 dist/ 로 전체 빌드
  py build.py --stdout row0_overview   해당 대시보드 JSON 을 stdout 으로
  py build.py --remap UID_JSON         빌드 후 데이터소스 UID 치환본도 함께 출력
                                       (라이브 import 테스트용 · dist/*.live.json)

--remap 인자는 JSON 객체: 코드 고정 UID -> 라이브 UID. 예:
  py build.py --remap '{"prometheus":"PBFA97CFB590B2093"}'
커밋되는 dist/*.json 은 항상 고정 UID 를 유지하고, *.live.json 만 치환된다.
"""

import argparse
import importlib
import json
import sys
from pathlib import Path

from grafana_foundation_sdk.cog.encoder import JSONEncoder

ROOT = Path(__file__).parent.resolve()
DIST = ROOT / "dist"
DASHBOARDS_PKG = "dashboards"

# lib/ 와 dashboards/ import 가능하도록
sys.path.insert(0, str(ROOT))


def discover_modules() -> list[str]:
    """dashboards/row*.py 를 자동 발견해 모듈명을 정렬 반환.

    새 Row 모듈을 추가하면 별도 등록 없이 자동으로 빌드 대상이 된다.
    """
    pkg_dir = ROOT / DASHBOARDS_PKG
    return sorted(p.stem for p in pkg_dir.glob("row*.py"))


# 빌드 대상 — dashboards/row*.py 자동 발견.
DASHBOARD_MODULES = discover_modules()


def build_one(module_name: str) -> dict:
    """dashboards/<module_name>.py 의 dashboard() 를 빌드해 dict 반환."""
    mod = importlib.import_module(f"{DASHBOARDS_PKG}.{module_name}")
    if not hasattr(mod, "dashboard"):
        raise AttributeError(
            f"{module_name}: dashboard() 함수가 없음. Row 모듈은 dashboard() 를 export 해야 함."
        )
    dashboard = mod.dashboard().build()
    # SDK -> JSON -> dict (encoder 가 SDK 직렬화 규칙을 보장)
    return json.loads(JSONEncoder(sort_keys=True, indent=2).encode(dashboard))


def remap_datasources(obj, mapping: dict):
    """대시보드 dict 내 모든 datasource UID 를 mapping 대로 치환 (in-place 재귀)."""
    if isinstance(obj, dict):
        uid = obj.get("uid")
        if isinstance(uid, str) and uid in mapping:
            obj["uid"] = mapping[uid]
        for v in obj.values():
            remap_datasources(v, mapping)
    elif isinstance(obj, list):
        for v in obj:
            remap_datasources(v, mapping)
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(description="BookFlow 운영 대시보드 빌더")
    parser.add_argument(
        "--stdout", metavar="MODULE",
        help="해당 대시보드 JSON 을 파일 대신 stdout 으로 출력",
    )
    parser.add_argument(
        "--remap", metavar="JSON",
        help="데이터소스 UID 치환 맵(JSON). *.live.json 추가 생성 (import 테스트용)",
    )
    args = parser.parse_args()

    mapping = json.loads(args.remap) if args.remap else None

    if args.stdout:
        built = build_one(args.stdout)
        if mapping:
            remap_datasources(built, mapping)
        print(json.dumps(built, indent=2, ensure_ascii=False))
        return 0

    DIST.mkdir(exist_ok=True)
    for module_name in DASHBOARD_MODULES:
        built = build_one(module_name)
        out = DIST / f"{module_name}.json"
        out.write_text(
            json.dumps(built, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[build] {out.relative_to(ROOT)}  (panels={len(built.get('panels', []))})")

        if mapping:
            live = json.loads(json.dumps(built))  # deep copy
            remap_datasources(live, mapping)
            live_out = DIST / f"{module_name}.live.json"
            live_out.write_text(
                json.dumps(live, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"[build] {live_out.relative_to(ROOT)}  (datasource UID remapped)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
