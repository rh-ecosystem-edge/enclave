#!/usr/bin/env python3
"""
Merge oc-mirror generated IDMS/ITMS manifests with existing cluster resources.

The merge strategy is keyed by `source` and preserves existing mirror order:
- Existing mirrors remain first.
- New mirrors from the current manifest are appended if missing.
"""

import argparse
import json
import subprocess
import sys
import time

DEFAULT_SUBPROCESS_TIMEOUT = 30


def _format_cmd(argv):
    return " ".join(argv)


def _runtime_error_from_process(argv, exc=None, result=None, timeout=None):
    if exc is not None and isinstance(exc, subprocess.TimeoutExpired):
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        return RuntimeError(
            "command timed out: "
            f"cmd={_format_cmd(argv)!r}, timeout={timeout}s, "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )
    if exc is not None and isinstance(exc, subprocess.CalledProcessError):
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        return RuntimeError(
            "command failed: "
            f"cmd={_format_cmd(argv)!r}, exit={exc.returncode}, "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )
    if result is not None:
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        return RuntimeError(
            "command failed: "
            f"cmd={_format_cmd(argv)!r}, exit={result.returncode}, "
            f"stdout={stdout!r}, stderr={stderr!r}"
        )
    return RuntimeError(f"command failed: cmd={_format_cmd(argv)!r}")


def run_command(argv, check=False, timeout=DEFAULT_SUBPROCESS_TIMEOUT):
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _runtime_error_from_process(argv, exc=exc, timeout=timeout) from exc
    except subprocess.CalledProcessError as exc:
        raise _runtime_error_from_process(argv, exc=exc) from exc


def run_json(argv, check=False):
    result = run_command(argv, check=check)
    if not result.stdout:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "failed to decode JSON output: "
            f"cmd={_format_cmd(argv)!r}, error={exc!r}, stdout={result.stdout!r}"
        ) from exc


def merge_by_source(existing_items, new_items):
    merged = {}
    order = []

    for item in existing_items or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if not source:
            continue
        if source not in merged:
            merged[source] = []
            order.append(source)
        for mirror in item.get("mirrors") or []:
            if mirror not in merged[source]:
                merged[source].append(mirror)

    for item in new_items or []:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        if not source:
            continue
        if source not in merged:
            merged[source] = []
            order.append(source)
        for mirror in item.get("mirrors") or []:
            if mirror not in merged[source]:
                merged[source].append(mirror)

    return [{"source": source, "mirrors": merged[source]} for source in order]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--oc-bin", required=True)
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-manifest", required=True)
    parser.add_argument("--api-kind", required=True)
    parser.add_argument("--oc-resource", required=True)
    parser.add_argument("--spec-key", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    return parser.parse_args()


def get_existing_resource(args, name):
    current = run_command(
        [args.oc_bin, "get", args.oc_resource, name, "-o", "json"],
        check=False,
    )
    if current.returncode != 0:
        error_output = f"{current.stderr or ''}\n{current.stdout or ''}".lower()
        if "notfound" in error_output or "not found" in error_output:
            return {}
        raise _runtime_error_from_process(
            [args.oc_bin, "get", args.oc_resource, name, "-o", "json"],
            result=current,
        )
    return json.loads(current.stdout) if current.stdout else {}


def build_merged_docs(args, docs):
    merged_docs = []
    expected_specs = {}

    for doc in docs:
        if not isinstance(doc, dict):
            merged_docs.append(doc)
            continue
        if doc.get("kind") != args.api_kind:
            merged_docs.append(doc)
            continue

        name = (doc.get("metadata") or {}).get("name")
        if not name:
            merged_docs.append(doc)
            continue

        existing = get_existing_resource(args, name)
        existing_list = (existing.get("spec") or {}).get(args.spec_key) or []
        new_list = (doc.get("spec") or {}).get(args.spec_key) or []
        merged_spec = merge_by_source(existing_list, new_list)
        doc.setdefault("spec", {})[args.spec_key] = merged_spec
        merged_docs.append(doc)
        expected_specs[name] = merged_spec

    return merged_docs, expected_specs


def apply_payload(args):
    run_command(
        [args.oc_bin, "apply", "-f", args.output_manifest],
        check=True,
    )


def live_matches_expected(args, expected_specs):
    mismatches = []
    for name, expected_list in expected_specs.items():
        current = get_existing_resource(args, name)
        live_list = (current.get("spec") or {}).get(args.spec_key) or []
        if live_list != expected_list:
            mismatches.append(name)
    return mismatches


def main():
    args = parse_args()
    if args.max_attempts < 1:
        raise RuntimeError("--max-attempts must be at least 1")

    # Render the input manifest to a list of objects
    rendered = run_json(
        [
            args.oc_bin,
            "create",
            "--dry-run=client",
            "-f",
            args.input_manifest,
            "-o",
            "json",
        ],
        check=True,
    )
    docs = rendered.get("items", []) if rendered.get("kind") == "List" else [rendered]

    unmatched_resources = []
    for attempt in range(1, args.max_attempts + 1):
        merged_docs, expected_specs = build_merged_docs(args, docs)
        payload = {"apiVersion": "v1", "kind": "List", "items": merged_docs}
        with open(args.output_manifest, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        if not args.apply:
            return

        apply_payload(args)
        unmatched_resources = live_matches_expected(args, expected_specs)
        if not unmatched_resources:
            return

        if attempt < args.max_attempts:
            time.sleep(args.retry_delay * attempt)

    raise RuntimeError(
        "concurrent update detected while applying merged mirrors for "
        f"{args.oc_resource}: {', '.join(unmatched_resources)} "
        f"(exhausted {args.max_attempts} attempts)"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"merge_mirror_sets.py failed: {exc}", file=sys.stderr)
        sys.exit(1)
