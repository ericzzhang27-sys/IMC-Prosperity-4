from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _make_json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(inner) for inner in value]
    return str(value)


def load_trader_data(raw: Any) -> Dict[str, Any]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}
    return payload


def dump_trader_data(payload: Mapping[str, Any]) -> str:
    safe_payload = _make_json_safe(dict(payload))
    try:
        return json.dumps(safe_payload, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return "{}"
