#!/usr/bin/env bash
# Build + upload Lambda zip packages to S3 (CodeStar 우회 · 임시 직접 deploy).
#
# Usage:
#   AWS_PROFILE=bookflow-admin AWS_REGION=ap-northeast-1 ./build.sh                    # all lambdas
#   AWS_PROFILE=bookflow-admin AWS_REGION=ap-northeast-1 ./build.sh pos-ingestor       # specific
#
# Output: s3://bookflow-cp-artifacts-${ACCOUNT}/lambda/<name>.zip
#
# Linux x86_64 wheel 자동 선택 (manylinux2014) — host OS 무관 zip 가능.
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="bookflow-cp-artifacts-${ACCOUNT_ID}"
KEY_PREFIX="lambda"

cd "$(dirname "$0")"

if [[ $# -gt 0 ]]; then
  LAMBDAS=("$@")
else
  # find Lambdas with either lambda_function.py or index.py (handler entrypoint)
  mapfile -t LAMBDAS < <(find . -mindepth 2 -maxdepth 2 \( -name lambda_function.py -o -name index.py \) -exec dirname {} \; | sed 's|^\./||' | sort -u)
fi

# Verify bucket exists
aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null

WORK=$(mktemp -d)
trap "rm -rf $WORK" EXIT

for fn in "${LAMBDAS[@]}"; do
  echo "=== build $fn ==="
  STAGE="$WORK/$fn"
  mkdir -p "$STAGE"

  # Copy source
  cp -r "$fn"/*.py "$STAGE/"
  [[ -f "$fn/requirements.txt" ]] && cp "$fn/requirements.txt" "$STAGE/requirements.txt" || true

  # Install Linux wheels (Lambda is Amazon Linux 2023 x86_64 / python 3.12)
  if [[ -f "$STAGE/requirements.txt" ]]; then
    # Linux Lambda runtime - force manylinux wheels for binary deps
    pip install --target "$STAGE" --upgrade --quiet \
      --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
      --only-binary=:all: \
      -r "$STAGE/requirements.txt"
  fi

  ZIP="$WORK/$fn.zip"
  # Python zipfile (Windows 호환 · zip 명령 없어도 동작)
  py -c "
import os, zipfile, sys
stage = sys.argv[1]; out = sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(stage):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith('.pyc'):
                continue
            full = os.path.join(root, f)
            arc = os.path.relpath(full, stage)
            z.write(full, arc)
print(f'zipped {os.path.getsize(out)} bytes')
" "$STAGE" "$ZIP"
  SIZE=$(du -h "$ZIP" | cut -f1)
  echo "  zip: $ZIP ($SIZE)"

  aws s3 cp "$ZIP" "s3://${BUCKET}/${KEY_PREFIX}/${fn}.zip" --region "$REGION"
done

echo
echo "Uploaded: s3://${BUCKET}/${KEY_PREFIX}/<name>.zip"
