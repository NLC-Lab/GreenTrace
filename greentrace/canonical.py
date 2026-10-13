"""RFC 8785 风格的确定性 JSON 编码。"""
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_hash(value: Any) -> str:
    import hashlib
    return hashlib.sha256(canonical_json(value)).hexdigest()
