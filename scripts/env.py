"""Read the simple KEY=value files produced by configure.py, without executing shell code."""
import re
import sys
from pathlib import Path


def read_env(path):
    values = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Expected plain KEY=value lines; do not source or quote this file")
        values[key] = value
    return values


if __name__ == "__main__":
    values = read_env(sys.argv[1])
    print(values.get(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
