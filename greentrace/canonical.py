"""RFC 8785 风格的确定性 JSON 编码。"""
import json
from typing import Any

# 嵌套深度上限，防止恶意构造深层 payload 触发 RecursionError / 堆栈耗尽。
_MAX_DEPTH = 64


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("value nested too deeply")
    if isinstance(value, dict):
        for key, item in value.items():
            _check_depth(key, depth + 1)
            _check_depth(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_depth(item, depth + 1)


def canonical_json(value: Any) -> bytes:
    _check_depth(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_hash(value: Any) -> str:
    import hashlib
    return hashlib.sha256(canonical_json(value)).hexdigest()
