"""Parallel CFN deploy helpers (ThreadPoolExecutor over Stack.deploy()).

Used by base.py / wave2.py / cicd_verify.py to fire independent stacks concurrently.
boto3-only (no `aws cloudformation deploy` shell-out) so cp949 codepage issues on
Windows do not surface — Stack.deploy() reads template via Python utf-8.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from .log import log
from .stack import Stack


def parallel_deploy(stacks: Iterable[Stack], label: str = "", max_workers: int = 8) -> None:
    """Deploy a batch of stacks concurrently. Raises if any fails."""
    stacks = list(stacks)
    if not stacks:
        return
    if label:
        log.info(f"  [parallel × {len(stacks)}] {label}")
    errors = []
    with ThreadPoolExecutor(max_workers=min(len(stacks), max_workers)) as ex:
        futures = {ex.submit(s.deploy): s for s in stacks}
        for f in as_completed(futures):
            s = futures[f]
            try:
                f.result()
            except Exception as e:
                errors.append((s.full_name, str(e)))
                log.err(f"  parallel deploy fail: {s.full_name} · {e}")
    if errors:
        raise RuntimeError(f"parallel_deploy: {len(errors)} failures: {[n for n, _ in errors]}")


def parallel_destroy(stacks: Iterable[Stack], max_workers: int = 8) -> None:
    """Destroy a batch of stacks concurrently. Errors logged but not raised."""
    stacks = list(stacks)
    if not stacks:
        return
    log.info(f"  [parallel destroy × {len(stacks)}]")
    with ThreadPoolExecutor(max_workers=min(len(stacks), max_workers)) as ex:
        futures = {ex.submit(s.destroy): s for s in stacks}
        for f in as_completed(futures):
            s = futures[f]
            try:
                f.result()
            except Exception as e:
                log.warn(f"  parallel destroy ignore: {s.full_name} · {e}")
