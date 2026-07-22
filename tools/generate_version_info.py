#!/usr/bin/env python3
"""PyInstaller 用の Windows バージョン情報ファイルを生成する。"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def parse_version(version: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in re.findall(r"\d+", version)]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])  # type: ignore[return-value]


def main() -> None:
    version = sys.argv[1] if len(sys.argv) > 1 else "0.0.0"
    version = version.lstrip("vV")
    filevers = prodvers = parse_version(version)
    root = Path(__file__).resolve().parent.parent
    out = root / "client" / "version_info.txt"
    out.write_text(
        f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={filevers},
    prodvers={prodvers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'stanak'),
        StringStruct(u'FileDescription', u'asobby - Hisoutensoku lobby client'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'InternalName', u'asobby'),
        StringStruct(u'LegalCopyright', u'Copyright (c) 2026 stanak'),
        StringStruct(u'OriginalFilename', u'asobby.exe'),
        StringStruct(u'ProductName', u'asobby'),
        StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    print(f"Wrote {out} for version {version}")


if __name__ == "__main__":
    main()
