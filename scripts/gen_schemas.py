"""Generate JSON Schemas for IPC protocol v1 from Pydantic models.

Usage:
    python scripts/gen_schemas.py          # write schemas/ipc/**
    python scripts/gen_schemas.py --check  # exit 1 if committed files are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.worker.ipc.method_catalog import (  # noqa: E402
    FORBIDDEN_METHOD_PREFIXES,
    M0_METHODS,
    PROTOCOL_VERSION,
    EnvelopeSchema,
    WorkerReadyEvent,
)

SCHEMA_ROOT = ROOT / "schemas" / "ipc"


def pascal(value: str) -> str:
    return "".join(part.capitalize() for part in value.replace("-", "_").split("_"))


def method_type_name(method: str, suffix: str) -> str:
    group, _, name = method.partition(".")
    return f"{pascal(group)}{pascal(name)}{suffix}"


def namespace_defs(schema: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Prefix nested ``$defs`` so repeated models stay unique across files."""
    defs = schema.pop("$defs", {})
    mapping = {name: f"{prefix}{name}" for name in defs}

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                    out[key] = "#/$defs/" + mapping.get(value[len("#/$defs/") :], value)
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    schema = walk(schema)
    if defs:
        schema["$defs"] = {mapping[name]: walk(value) for name, value in defs.items()}
    return schema


def strip_nested_titles(node: Any, *, is_root: bool = True) -> Any:
    """Keep only the root ``title``; Pydantic adds titles to every field.

    json-schema-to-typescript turns titled primitives into aliases, which
    collide across files (``Url``, ``V`` ...). Nested names are unnecessary
    because nested object ``$defs`` already carry unique prefixed keys.
    """
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "title" and not is_root:
                continue
            out[key] = strip_nested_titles(value, is_root=False)
        return out
    if isinstance(node, list):
        return [strip_nested_titles(item, is_root=False) for item in node]
    return node


def render(model: Any, title: str) -> str:
    schema = model.model_json_schema(ref_template="#/$defs/{model}")
    schema["title"] = title
    schema = namespace_defs(schema, title)
    schema = strip_nested_titles(schema)
    return json.dumps(schema, indent=2, sort_keys=False) + "\n"


def generate() -> dict[Path, str]:
    files: dict[Path, str] = {}
    methods: list[dict[str, Any]] = []
    for spec in M0_METHODS:
        group = spec.method.split(".", 1)[0]
        snake = spec.method.replace(".", "_")
        request_name = method_type_name(spec.method, "Request")
        response_name = method_type_name(spec.method, "Response")
        request_path = SCHEMA_ROOT / group / f"{snake}_request.json"
        response_path = SCHEMA_ROOT / group / f"{snake}_response.json"
        files[request_path] = render(spec.params, request_name)
        files[response_path] = render(spec.result, response_name)
        methods.append(
            {
                "method": spec.method,
                "long_running": spec.long_running,
                "request_schema": request_path.relative_to(ROOT).as_posix(),
                "response_schema": response_path.relative_to(ROOT).as_posix(),
            }
        )
    files[SCHEMA_ROOT / "envelope.json"] = render(EnvelopeSchema, "Envelope")
    files[SCHEMA_ROOT / "event" / "worker_ready.json"] = render(
        WorkerReadyEvent, "WorkerReadyEvent"
    )
    files[SCHEMA_ROOT / "methods.json"] = (
        json.dumps(
            {
                "protocol_version": PROTOCOL_VERSION,
                "methods": methods,
                "forbidden_prefixes": list(FORBIDDEN_METHOD_PREFIXES),
            },
            indent=2,
        )
        + "\n"
    )
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    files = generate()
    stale: list[Path] = []
    if args.check:
        for path, content in files.items():
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(path)
        if stale:
            print("Stale IPC schemas:")
            for path in stale:
                print(f"  {path.relative_to(ROOT)}")
            print("Run: python scripts/gen_schemas.py")
            return 1
        return 0

    # Remove previously generated JSON so deleted methods do not linger.
    if SCHEMA_ROOT.exists():
        for path in SCHEMA_ROOT.rglob("*.json"):
            path.unlink()
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {len(files)} IPC schema files to {SCHEMA_ROOT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
