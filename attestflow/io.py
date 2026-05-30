from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


def load_data(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a mapping at the top level")
        return data
    lines = _clean_lines(path.read_text(encoding="utf-8"))
    if not lines:
        return {}
    data, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"could not parse all lines in {path}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data


def dump_data(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".json":
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    path.write_text(_dump_value(data, 0).rstrip() + "\n", encoding="utf-8")


def _clean_lines(text: str) -> list[tuple[int, str]]:
    cleaned: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        cleaned.append((indent, raw.strip()))
    return cleaned


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return None, index
    current_indent, content = lines[index]
    if current_indent != indent:
        raise ValueError("invalid indentation")
    if content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_dict(lines, index, indent)


def _parse_dict(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"unexpected indentation before {content!r}")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError(f"expected key/value line, got {content!r}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            result[key] = _parse_scalar(raw_value)
            index += 1
            continue
        if index + 1 >= len(lines) or lines[index + 1][0] <= indent:
            result[key] = None
            index += 1
            continue
        child, index = _parse_block(lines, index + 1, lines[index + 1][0])
        result[key] = child
    return result, index


def _parse_list(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("- "):
            break
        item = content[2:].strip()
        if item:
            result.append(_parse_scalar(item))
            index += 1
            continue
        if index + 1 >= len(lines) or lines[index + 1][0] <= indent:
            result.append(None)
            index += 1
            continue
        child, index = _parse_block(lines, index + 1, lines[index + 1][0])
        result.append(child)
    return result, index


def _parse_scalar(raw: str) -> Any:
    if raw == "[]":
        return []
    if raw == "{}":
        return {}
    if raw in {"null", "None", "~"}:
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if raw.startswith(("'", '"')):
        return ast.literal_eval(raw)
    try:
        return int(raw)
    except ValueError:
        return raw


def _dump_value(value: Any, indent: int) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, dict):
                if child:
                    lines.append(f"{prefix}{key}:")
                    lines.append(_dump_value(child, indent + 2))
                else:
                    lines.append(f"{prefix}{key}: {{}}")
            elif isinstance(child, list):
                if child:
                    lines.append(f"{prefix}{key}:")
                    lines.append(_dump_value(child, indent + 2))
                else:
                    lines.append(f"{prefix}{key}: []")
            else:
                lines.append(f"{prefix}{key}: {_dump_scalar(child)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_dump_value(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_dump_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_dump_scalar(value)}"


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if (
        text == ""
        or text.strip() != text
        or text in {"true", "false", "null", "None", "~", "[]", "{}"}
        or text.startswith(("-", "{", "[", "#", "!", "&", "*"))
        or ": " in text
        or "\n" in text
    ):
        return repr(text)
    return text
