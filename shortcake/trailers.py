"""Helpers for parsing and updating git commit trailers."""

from __future__ import annotations

import re
from typing import Iterable

TRAILER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)(\s*:\s*)(.*)$")
CONTINUATION_RE = re.compile(r"^\s+\S.*$")

SHORTCAKE_PARENT_TRAILER = "Shortcake-Parent"
SHORTCAKE_STACK_TRAILER = "Shortcake-Stack"


def parse_message(message: str) -> tuple[str, str, str]:
    """Split a commit message into subject, body, and trailer block."""
    if not message:
        return "", "", ""

    lines = message.splitlines()
    subject = lines[0] if lines else ""
    if len(lines) <= 1:
        return subject, "", ""

    message_lines = lines[1:]
    trailer_start = _find_trailer_block_start(message_lines)
    if trailer_start == -1:
        body = "\n".join(message_lines).strip()
        return subject, body, ""

    body = "\n".join(message_lines[:trailer_start]).strip()
    trailers = "\n".join(message_lines[trailer_start:]).strip()
    return subject, body, trailers


def _find_trailer_block_start(lines: list[str]) -> int:
    """Find the start index of a trailer block or return -1."""
    trimmed_lines = list(reversed([line for line in reversed(lines) if line.strip()]))
    if not trimmed_lines:
        return -1

    block_indices = [-1] + [i for i, line in enumerate(lines) if not line.strip()]
    for i in range(len(block_indices) - 1, -1, -1):
        start_idx = block_indices[i] + 1
        if i == 0 or start_idx == 0:
            return start_idx if _is_trailer_block(lines[start_idx:]) else -1

        end_idx = block_indices[i + 1] if i + 1 < len(block_indices) else len(lines)
        if _is_trailer_block(lines[start_idx:end_idx]):
            return start_idx

    return -1


def _is_trailer_block(lines: Iterable[str]) -> bool:
    """Return True if lines form a trailer block."""
    content_lines = [line for line in lines if line.strip()]
    if not content_lines:
        return False

    trailer_lines = 0
    non_trailer_lines = 0
    i = 0
    while i < len(content_lines):
        line = content_lines[i]
        if CONTINUATION_RE.match(line):
            i += 1
            continue

        if TRAILER_RE.match(line):
            trailer_lines += 1
        else:
            non_trailer_lines += 1
        i += 1

    return trailer_lines > 0 and non_trailer_lines == 0


def get_trailer_value(message: str, key: str) -> str | None:
    """Get a trailer value from a commit message."""
    _subject, _body, trailers = parse_message(message)
    if not trailers:
        return None

    value = None
    for line in trailers.splitlines():
        match = TRAILER_RE.match(line)
        if not match:
            continue
        trailer_key, _sep, trailer_value = match.groups()
        if trailer_key == key:
            value = trailer_value.strip()
    return value


def update_trailers(message: str, updates: dict[str, str | None]) -> str:
    """Add or replace trailer keys in a commit message."""
    subject, body, trailers = parse_message(message)
    existing_lines = trailers.splitlines() if trailers else []
    filtered_lines: list[str] = []

    for line in existing_lines:
        match = TRAILER_RE.match(line)
        if not match:
            filtered_lines.append(line)
            continue
        trailer_key = match.group(1)
        if trailer_key in updates:
            continue
        filtered_lines.append(line)

    for key, value in updates.items():
        if value is None:
            continue
        filtered_lines.append(f"{key}: {value}")

    new_message = subject
    if body:
        new_message += "\n\n" + body
    if filtered_lines:
        new_message += "\n\n" + "\n".join(filtered_lines)

    return new_message
