#!/usr/bin/env python3
"""Verify the data release asset and run the supplementary reproduction against it.

Offline: no network, no API key, no spend.

Given the data-asset zip (``apart_data_2026-10-25.zip``), this script:

1. checks every entry listed in the zip's ``SHA256SUMS.txt`` (and reports any file that
   is present but unlisted, or listed but missing);
2. extracts the zip to a fresh temp directory;
3. copies this repository's ``supplementary/analysis/`` scripts alongside the extracted
   data, so the reproduction reads the *asset's* data rather than the checkout's; and
4. runs ``supplementary/analysis/run_all.py`` (35 checks) against that data and returns
   its exit status.

Usage::

    python scripts/verify_data_asset.py --zip ~/Desktop/apart_data_2026-10-25.zip
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ANALYSIS = REPO / "supplementary" / "analysis"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="verify the data asset zip offline")
    parser.add_argument("--zip", required=True, help="path to apart_data_2026-10-25.zip")
    args = parser.parse_args(argv)

    zpath = Path(args.zip).expanduser()
    if not zpath.exists():
        print(f"ERROR: zip not found: {zpath}", file=sys.stderr)
        return 2

    checksum_ok = True
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
        sums_text = zf.read("SHA256SUMS.txt").decode("utf-8")
        listed: dict[str, str] = {}
        for line in sums_text.splitlines():
            if not line.strip():
                continue
            digest, _, path = line.partition("  ")
            listed[path] = digest

        for path, want in sorted(listed.items()):
            if path not in names:
                print(f"MISSING  {path} (listed in SHA256SUMS.txt but not in the zip)")
                checksum_ok = False
                continue
            got = hashlib.sha256(zf.read(path)).hexdigest()
            if got != want:
                print(f"BADHASH  {path}")
                checksum_ok = False
            else:
                print(f"OK       {path}")

        unlisted = sorted(p for p in names
                          if p not in listed and not p.endswith("/") and p != "SHA256SUMS.txt")
        if unlisted:
            print("Unlisted files in the zip (no checksum entry):")
            for p in unlisted:
                print(f"UNLISTED {p}")

        tmp = Path(tempfile.mkdtemp(prefix="apart-data-verify-"))
        zf.extractall(tmp)

    print(f"Checksums: {'all match' if checksum_ok and not unlisted else 'see above'}")
    if not checksum_ok:
        return 1

    # Copy the repository's analysis scripts in so run_all.py reads the extracted data.
    shutil.copytree(ANALYSIS, tmp / "supplementary" / "analysis")
    result = subprocess.run([sys.executable, "run_all.py"],
                            cwd=tmp / "supplementary" / "analysis")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
