"""Compile Domly's simple UTF-8 gettext catalogs without external tools.

The production server may use Django's regular ``compilemessages`` command.
This helper keeps Windows development reproducible when GNU gettext is absent.
"""

from __future__ import annotations

import ast
import struct
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def read_catalog(path: Path) -> dict[str, str]:
    messages: dict[str, str] = {}
    msgid: list[str] | None = None
    msgstr: list[str] | None = None
    active: list[str] | None = None

    def store() -> None:
        if msgid is not None and msgstr is not None:
            messages["".join(msgid)] = "".join(msgstr)

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            store()
            msgid = [ast.literal_eval(line[6:])]
            msgstr = None
            active = msgid
        elif line.startswith("msgstr "):
            msgstr = [ast.literal_eval(line[7:])]
            active = msgstr
        elif line.startswith('"') and active is not None:
            active.append(ast.literal_eval(line))
        else:
            raise ValueError(f"Unsupported PO line in {path}: {raw_line}")
    store()
    return messages


def write_mo(messages: dict[str, str], path: Path) -> None:
    items = sorted(messages.items())
    original = b""
    translated = b""
    original_table: list[tuple[int, int]] = []
    translated_table: list[tuple[int, int]] = []

    for msgid, msgstr in items:
        encoded = msgid.encode("utf-8")
        original_table.append((len(encoded), len(original)))
        original += encoded + b"\0"
        encoded = msgstr.encode("utf-8")
        translated_table.append((len(encoded), len(translated)))
        translated += encoded + b"\0"

    count = len(items)
    original_table_offset = 28
    translated_table_offset = original_table_offset + count * 8
    original_offset = translated_table_offset + count * 8
    translated_offset = original_offset + len(original)

    output = [
        struct.pack(
            "<7I",
            0x950412DE,
            0,
            count,
            original_table_offset,
            translated_table_offset,
            0,
            0,
        )
    ]
    output.extend(
        struct.pack("<2I", length, original_offset + offset)
        for length, offset in original_table
    )
    output.extend(
        struct.pack("<2I", length, translated_offset + offset)
        for length, offset in translated_table
    )
    output.extend((original, translated))
    path.write_bytes(b"".join(output))


def main() -> None:
    for po_path in sorted((BASE_DIR / "locale").glob("*/LC_MESSAGES/django.po")):
        mo_path = po_path.with_suffix(".mo")
        write_mo(read_catalog(po_path), mo_path)
        print(f"Compiled {po_path.relative_to(BASE_DIR)} -> {mo_path.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
