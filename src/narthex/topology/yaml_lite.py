"""A deliberately small YAML subset parser.

Narthex ships without third-party dependencies.  Topology files need only a
safe, predictable subset of YAML: nested mappings, lists, quoted/plain scalars,
inline lists, and literal/folded block strings.  This module implements that
subset and raises precise line-numbered errors instead of guessing.

If a file is JSON, ``load`` delegates to :mod:`json`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class YamlLiteError(ValueError):
    """Raised when a document is outside the supported YAML subset."""


_INT_RE = re.compile(r"^[+-]?[0-9]+$")
_FLOAT_RE = re.compile(
    r"^[+-]?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    depth = 0
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char in "[{(":
            depth += 1
            continue
        if char in "]})":
            depth = max(0, depth - 1)
            continue
        if char == "#" and depth == 0 and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    quote: str | None = None
    escaped = False
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char in "[{(":
            depth += 1
            continue
        if char in "]})":
            depth -= 1
            continue
        if char == delimiter and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def _split_key(value: str) -> tuple[str, str]:
    quote: str | None = None
    escaped = False
    depth = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char in "[{(":
            depth += 1
            continue
        if char in "]})":
            depth -= 1
            continue
        if char == ":" and depth == 0:
            if index + 1 == len(value) or value[index + 1].isspace():
                return value[:index].strip(), value[index + 1 :].strip()
    raise YamlLiteError(f"expected a 'key: value' mapping entry, got {value!r}")


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return None
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise YamlLiteError(f"invalid double-quoted string: {value!r}") from exc
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    lowered = value.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_top_level(inner)]
    if value.startswith("{") and value.endswith("}"):
        inner = value[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, Any] = {}
        for part in _split_top_level(inner):
            key, raw = _split_key(part)
            result[str(_parse_scalar(key))] = _parse_scalar(raw)
        return result
    if _INT_RE.match(value):
        return int(value)
    if _FLOAT_RE.match(value):
        return float(value)
    return value


class _Line:
    __slots__ = ("indent", "content", "number")

    def __init__(self, indent: int, content: str, number: int) -> None:
        self.indent = indent
        self.content = content
        self.number = number

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_Line({self.indent}, {self.content!r}, {self.number})"


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines: list[_Line] = []
        self.index = 0
        block_parent_indent: int | None = None
        for number, raw in enumerate(text.splitlines(), start=1):
            leading = raw[: len(raw) - len(raw.lstrip())]
            if "\t" in leading:
                raise YamlLiteError(f"line {number}: tabs are not allowed for indentation")
            if block_parent_indent is not None:
                if not raw.strip():
                    self.lines.append(_Line(-1, "", number))
                    continue
                indent = len(raw) - len(raw.lstrip(" "))
                if indent > block_parent_indent:
                    self.lines.append(_Line(indent, raw.lstrip(" ").rstrip(), number))
                    continue
                block_parent_indent = None
            cleaned = _strip_comment(raw).rstrip()
            if not cleaned.strip():
                continue
            stripped = cleaned.lstrip(" ")
            if stripped in ("---", "..."):
                continue
            indent = len(cleaned) - len(stripped)
            self.lines.append(_Line(indent, stripped, number))
            if re.search(r"(?:^|:\s*|-\s*)[|>][+-]?\s*$", stripped):
                block_parent_indent = indent

    def parse(self) -> Any:
        if not self.lines:
            return None
        if self.lines[0].indent != 0:
            raise YamlLiteError(
                f"line {self.lines[0].number}: document must start at indentation 0"
            )
        result = self._parse_block(0)
        if self.index != len(self.lines):
            line = self.lines[self.index]
            raise YamlLiteError(f"line {line.number}: unexpected indentation or content")
        return result

    def _parse_block(self, indent: int) -> Any:
        if self.index >= len(self.lines):
            return None
        line = self.lines[self.index]
        if line.indent < indent:
            return None
        if line.content.startswith("- ") or line.content == "-":
            return self._parse_list(indent)
        return self._parse_map(indent)

    def _parse_map(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent < indent:
                break
            if line.indent > indent:
                raise YamlLiteError(
                    f"line {line.number}: unexpected indentation inside mapping"
                )
            if line.content.startswith("- ") or line.content == "-":
                break
            key, raw_value = _split_key(line.content)
            key = str(_parse_scalar(key))
            self.index += 1
            result[key] = self._parse_value(raw_value, indent, line.number)
        return result

    def _parse_value(self, raw_value: str, parent_indent: int, line_number: int) -> Any:
        if raw_value in ("|", "|-", "|+", ">", ">-", ">+"):
            return self._parse_block_scalar(raw_value, parent_indent)
        if raw_value != "":
            return _parse_scalar(raw_value)
        if self.index >= len(self.lines):
            return None
        next_line = self.lines[self.index]
        if next_line.indent <= parent_indent:
            return None
        return self._parse_block(next_line.indent)

    def _parse_block_scalar(self, marker: str, parent_indent: int) -> str:
        folded = marker.startswith(">")
        chomping = marker[1:] if len(marker) > 1 else ""
        collected: list[str] = []
        block_indent: int | None = None
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent == -1 and line.content == "":
                collected.append("")
                self.index += 1
                continue
            if line.indent <= parent_indent:
                break
            if block_indent is None:
                block_indent = line.indent
            if line.indent < block_indent:
                break
            collected.append(" " * (line.indent - block_indent) + line.content)
            self.index += 1
        if block_indent is None:
            return ""
        if folded:
            text = ""
            for part in collected:
                if text and part:
                    text += " "
                text += part
        else:
            text = "\n".join(collected)
        if chomping == "-":
            return text.rstrip("\n")
        if chomping == "+":
            return text + "\n"
        return text + "\n" if text else ""

    def _parse_list(self, indent: int) -> list[Any]:
        result: list[Any] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent < indent:
                break
            if line.indent > indent:
                raise YamlLiteError(
                    f"line {line.number}: unexpected indentation inside list"
                )
            if not (line.content.startswith("- ") or line.content == "-"):
                break
            rest = line.content[1:].strip()
            self.index += 1
            if rest == "":
                if self.index < len(self.lines) and self.lines[self.index].indent > indent:
                    result.append(self._parse_block(self.lines[self.index].indent))
                else:
                    result.append(None)
                continue
            if self._looks_like_mapping(rest):
                result.append(self._parse_mapping_item(rest, indent, line.number))
            else:
                result.append(_parse_scalar(rest))
        return result

    @staticmethod
    def _looks_like_mapping(value: str) -> bool:
        try:
            _split_key(value)
        except YamlLiteError:
            return False
        return True

    def _parse_mapping_item(
        self,
        first_entry: str,
        list_indent: int,
        line_number: int,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        item_indent = list_indent + 2

        def consume_entry(content: str, number: int, entry_indent: int) -> None:
            key, raw_value = _split_key(content)
            key = str(_parse_scalar(key))
            if raw_value in ("|", "|-", "|+", ">", ">-", ">+"):
                result[key] = self._parse_block_scalar(raw_value, entry_indent)
            elif raw_value != "":
                result[key] = _parse_scalar(raw_value)
            elif self.index < len(self.lines) and self.lines[self.index].indent > entry_indent:
                result[key] = self._parse_block(self.lines[self.index].indent)
            else:
                result[key] = None

        consume_entry(first_entry, line_number, item_indent)
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent <= list_indent:
                break
            if line.indent != item_indent:
                raise YamlLiteError(
                    f"line {line.number}: expected mapping indentation {item_indent}"
                )
            if line.content.startswith("- ") or line.content == "-":
                break
            self.index += 1
            consume_entry(line.content, line.number, item_indent)
        return result


def loads(text: str) -> Any:
    """Parse a YAML document using the supported subset."""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    return _Parser(text).parse()


def load(path: str | Path) -> Any:
    """Load JSON or the supported YAML subset from ``path``."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    return loads(text)
