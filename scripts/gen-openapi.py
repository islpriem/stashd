#!/usr/bin/env python
"""Write contracts/openapi.json from the public API. Commit the result."""

from pathlib import Path

from stashd.api.contract import openapi_json

TARGET = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"

if __name__ == "__main__":
    TARGET.write_text(openapi_json())
    print(f"wrote {TARGET}")
