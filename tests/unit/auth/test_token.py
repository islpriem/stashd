"""Peer tokens for the internal API."""

from pathlib import Path

import pytest

from stashd.auth.provider import Unauthenticated
from stashd.auth.token import TokenAuthProvider


def token_file(tmp_path: Path, body: str = "s3cret\n", mode: int = 0o600) -> Path:
    path = tmp_path / "peer-token"
    path.write_text(body)
    path.chmod(mode)
    return path


def test_a_matching_token_is_accepted(tmp_path: Path) -> None:
    provider = TokenAuthProvider.from_file(token_file(tmp_path))

    provider.verify("s3cret")


def test_the_token_is_stripped_of_its_newline(tmp_path: Path) -> None:
    TokenAuthProvider.from_file(token_file(tmp_path, "s3cret\n")).verify("s3cret")


def test_a_wrong_token_is_refused(tmp_path: Path) -> None:
    provider = TokenAuthProvider.from_file(token_file(tmp_path))

    with pytest.raises(Unauthenticated):
        provider.verify("guess")


def test_an_empty_credential_is_refused(tmp_path: Path) -> None:
    provider = TokenAuthProvider.from_file(token_file(tmp_path))

    with pytest.raises(Unauthenticated):
        provider.verify("")


def test_a_readable_by_others_token_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="0600"):
        TokenAuthProvider.from_file(token_file(tmp_path, mode=0o644))


def test_an_empty_token_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="empty"):
        TokenAuthProvider.from_file(token_file(tmp_path, "\n"))


def test_a_missing_token_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="does not exist"):
        TokenAuthProvider.from_file(tmp_path / "absent")


def test_the_token_is_never_in_the_error(tmp_path: Path) -> None:
    provider = TokenAuthProvider.from_file(token_file(tmp_path))

    with pytest.raises(Unauthenticated) as excinfo:
        provider.verify("guess")

    assert "s3cret" not in str(excinfo.value)
    assert "guess" not in str(excinfo.value)


def test_a_daemon_without_a_peer_token_accepts_no_internal_request(
    storage_bootstrap: object,
) -> None:
    """No token configured is not "no authentication needed"."""
    from fastapi.testclient import TestClient

    from stashd.api.app import create_app

    client = TestClient(create_app(storage_bootstrap))  # type: ignore[arg-type]

    response = client.post(
        "/internal/v1/filesets",
        json={},
        headers={"Authorization": "Bearer anything"},
    )

    assert response.status_code == 401
