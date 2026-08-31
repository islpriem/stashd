"""The committed wire contract must match the app."""

from pathlib import Path

from stashd.api.contract import openapi_document, openapi_json

CONTRACT = Path(__file__).parents[3] / "contracts" / "openapi.json"


def test_the_committed_contract_is_up_to_date() -> None:
    assert CONTRACT.read_text() == openapi_json(), "run scripts/gen-openapi.py and commit"


def test_the_error_envelope_is_part_of_the_contract() -> None:
    document = openapi_document()

    assert "ErrorEnvelope" in document["components"]["schemas"]
    responses = document["paths"]["/api/v1/filesets"]["get"]["responses"]
    assert responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "ErrorEnvelope"
    )


def test_the_contract_covers_the_read_api() -> None:
    paths = openapi_document()["paths"]

    assert set(paths) >= {
        "/api/v1/whoami",
        "/api/v1/locations",
        "/api/v1/storages",
        "/api/v1/filesets",
        "/api/v1/filesets/{fileset_id}",
        "/api/v1/transfers",
        "/api/v1/transfers/{transfer_id}",
        "/api/v1/allocations",
        "/api/v1/limits",
    }
