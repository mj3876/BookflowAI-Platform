#!/usr/bin/env python
"""Compatibility wrapper for old AWS CLI EKS token output.

Some Windows images still ship an AWS CLI that returns ExecCredential
client.authentication.k8s.io/v1alpha1. Modern kubectl/helm reject that
version, so this wrapper rewrites only the apiVersion field.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--cluster-name", required=True)
    args = parser.parse_args()

    result = subprocess.run(
        [
            "aws",
            "eks",
            "get-token",
            "--region",
            args.region,
            "--cluster-name",
            args.cluster_name,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode

    payload = json.loads(result.stdout)
    payload["apiVersion"] = "client.authentication.k8s.io/v1beta1"
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
