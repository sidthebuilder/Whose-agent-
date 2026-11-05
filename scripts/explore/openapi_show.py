"""Print the request and response schemas of chosen paths from a saved OpenAPI file, with $refs resolved.

  python scripts/openapi_show.py logs/switch_gateway_openapi.json /agents/register-other /rooms/from-yaml ...
"""
from __future__ import annotations

import json
import sys


def resolve(spec, node, depth=0, seen=()):
    if depth > 12:
        return {"description": "(deeper levels not shown)"}
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                return f"<recursive {ref.rsplit('/', 1)[-1]}>"
            target = spec
            for part in ref.lstrip("#/").split("/"):
                target = target[part]
            return resolve(spec, target, depth + 1, seen + (ref,))
        keep = {k: v for k, v in node.items() if k not in ("examples",)}
        return {k: resolve(spec, v, depth + 1, seen) for k, v in keep.items()}
    if isinstance(node, list):
        return [resolve(spec, v, depth + 1, seen) for v in node]
    return node


def slim(schema, depth=0):
    """Only types, properties, required, enums and descriptions (shortened)."""
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k in ("type", "enum", "required", "default", "format"):
        if k in schema:
            out[k] = schema[k]
    if "description" in schema:
        out["description"] = str(schema["description"])[:160]
    for k in ("anyOf", "oneOf", "allOf"):
        if k in schema:
            out[k] = [slim(s, depth + 1) for s in schema[k]]
    if "items" in schema:
        out["items"] = slim(schema["items"], depth + 1)
    if isinstance(schema.get("properties"), dict) and depth < 5:
        out["properties"] = {k: slim(v, depth + 1) for k, v in schema["properties"].items()}
    if "additionalProperties" in schema and isinstance(schema["additionalProperties"], dict):
        out["additionalProperties"] = slim(schema["additionalProperties"], depth + 1)
    return out


def main() -> None:
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    for want in sys.argv[2:]:
        for path, ops in spec["paths"].items():
            if not path.endswith(want):
                continue
            for method, op in ops.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                print(f"=== {method.upper()} {path}: {op.get('summary', '')}")
                if op.get("description"):
                    print("  ", op["description"][:600].replace("\n", " "))
                for p in op.get("parameters", []):
                    print(f"   param {p.get('in')} {p.get('name')} required={p.get('required', False)}")
                body = op.get("requestBody", {}).get("content", {})
                for ctype, c in body.items():
                    print(f"   body {ctype}:", json.dumps(slim(resolve(spec, c.get("schema", {}))), indent=1)[:3500])
                for code, r in op.get("responses", {}).items():
                    for ctype, c in r.get("content", {}).items():
                        print(f"   {code} {ctype}:", json.dumps(slim(resolve(spec, c.get("schema", {}))))[:1200])


if __name__ == "__main__":
    main()
