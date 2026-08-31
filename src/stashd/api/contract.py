"""The OpenAPI document of the public API, built without a database or a credential.

`scripts/gen-openapi.py` writes it to `contracts/openapi.json`; a test compares the two,
so a wire change that is not committed fails CI.
"""

import json
from typing import Any

from fastapi import FastAPI

from stashd import __version__
from stashd.api.app import public_router


def contract_app() -> FastAPI:
    app = FastAPI(title="stashd", version=__version__)
    app.include_router(public_router())
    return app


def openapi_document() -> dict[str, Any]:
    return contract_app().openapi()


def openapi_json() -> str:
    return json.dumps(openapi_document(), indent=2, sort_keys=True) + "\n"
