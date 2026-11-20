"""A small, dependency-free JSON Schema subset validator for model-generated data.

Model output is untrusted input, so this validator is deliberately conservative:
bounded depth, bounded error list, no regular expressions (``pattern`` is ignored
to avoid ReDoS from schemas or data), no ``$ref``. Supported keywords: ``type``
(string or list), ``enum``, ``const``, ``properties``, ``required``,
``additionalProperties`` (bool or schema), ``items``, ``minItems``, ``maxItems``,
``minLength``, ``maxLength``, ``minimum``, ``maximum``, ``anyOf``. Other keywords
are ignored, so a passing result means "satisfies the supported constraints", not
"satisfies the full schema".
"""

from __future__ import annotations

from typing import Any

MAX_DEPTH = 8
MAX_ERRORS = 5


def validate_json(value: Any, schema: Any, *, strict: bool = False) -> list[str]:
    """Return up to ``MAX_ERRORS`` problems (empty list means valid).

    With ``strict`` an object schema that lists ``properties`` rejects unknown
    keys unless it sets ``additionalProperties`` itself.
    """
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be a JSON object"]
    _check(value, schema, "$", 0, strict, errors)
    return errors[:MAX_ERRORS]


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True  # unknown type names are not enforced


def _check(value: Any, schema: dict[str, Any], path: str, depth: int, strict: bool,
           errors: list[str]) -> None:
    if len(errors) >= MAX_ERRORS:
        return
    if depth > MAX_DEPTH:
        errors.append(f"{path}: nesting too deep")
        return

    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(n, str) and _type_matches(value, n) for n in names):
            errors.append(f"{path}: expected {'/'.join(map(str, names))}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal the required constant")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: not one of the allowed values")

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        if not any(
            isinstance(option, dict) and not _sub_errors(value, option, path, depth + 1, strict)
            for option in any_of
        ):
            errors.append(f"{path}: does not match any allowed shape")

    if isinstance(value, str):
        _bounds(len(value), schema.get("minLength"), schema.get("maxLength"), path, "length", errors)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum, maximum = schema.get("minimum"), schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path}: below minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path}: above maximum")
    elif isinstance(value, list):
        _bounds(len(value), schema.get("minItems"), schema.get("maxItems"), path, "item count",
                errors)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, element in enumerate(value[:200]):
                _check(element, items, f"{path}[{index}]", depth + 1, strict, errors)
    elif isinstance(value, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        for key in required if isinstance(required, list) else []:
            if len(errors) >= MAX_ERRORS:
                return
            if isinstance(key, str) and key not in value:
                errors.append(f"{path}: missing required '{key}'")
        additional = schema.get("additionalProperties")
        for key, element in value.items():
            if len(errors) >= MAX_ERRORS:
                return
            if key in properties and isinstance(properties[key], dict):
                _check(element, properties[key], f"{path}.{key}", depth + 1, strict, errors)
            elif additional is False or (additional is None and strict and properties):
                errors.append(f"{path}: unexpected '{str(key)[:40]}'")
            elif isinstance(additional, dict):
                _check(element, additional, f"{path}.{str(key)[:40]}", depth + 1, strict, errors)


def _sub_errors(value: Any, schema: dict[str, Any], path: str, depth: int, strict: bool) -> list[str]:
    errors: list[str] = []
    _check(value, schema, path, depth, strict, errors)
    return errors


def _bounds(size: int, low: Any, high: Any, path: str, label: str, errors: list[str]) -> None:
    if isinstance(low, int) and not isinstance(low, bool) and size < low:
        errors.append(f"{path}: {label} below minimum")
    if isinstance(high, int) and not isinstance(high, bool) and size > high:
        errors.append(f"{path}: {label} above maximum")
