"""MUNGE authentication."""

from pathlib import Path

import pytest

from stashd.auth.fake import FakeAuthProvider
from stashd.auth.munge import MungeAuthProvider
from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.identity import Principal


class FakeLookup:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def lookup(self, uid: int, gid: int) -> tuple[str, tuple[str, ...], tuple[int, ...]]:
        self.calls.append((uid, gid))
        return ("mmustermann", ("users", "hpc-admin"), (1000, 4000))


def provider(decoder: object) -> MungeAuthProvider:
    return MungeAuthProvider(
        socket_path=Path("/run/munge/munge.socket.2"),
        lookup=FakeLookup(),
        decoder=decoder,  # type: ignore[arg-type]
    )


def test_a_valid_credential_yields_the_verified_uid_and_gid() -> None:
    auth = provider(lambda credential, socket: (1000, 1000))

    principal = auth.authenticate("MUNGE:abc")

    assert principal == Principal(
        uid=1000,
        gid=1000,
        username="mmustermann",
        groups=("users", "hpc-admin"),
        gids=(1000, 4000),
    )


def test_the_socket_from_the_config_is_used() -> None:
    seen: list[Path] = []

    def decoder(credential: str, socket: Path) -> tuple[int, int]:
        seen.append(socket)
        return (1000, 1000)

    provider(decoder).authenticate("MUNGE:abc")

    assert seen == [Path("/run/munge/munge.socket.2")]


def test_a_rejected_credential_is_unauthenticated() -> None:
    def decoder(credential: str, socket: Path) -> tuple[int, int]:
        raise RuntimeError("expired credential")

    with pytest.raises(StashError) as excinfo:
        provider(decoder).authenticate("MUNGE:stale")

    assert excinfo.value.code is ErrorCode.UNAUTHENTICATED
    assert "expired credential" not in excinfo.value.message, "never echo the credential detail"


def test_the_fake_provider_answers_for_known_credentials() -> None:
    known = Principal(uid=1000, gid=1000, username="mmustermann")
    auth = FakeAuthProvider({"token-mmustermann": known})

    assert auth.authenticate("token-mmustermann") == known
    with pytest.raises(StashError):
        auth.authenticate("nobody")


def test_the_primary_group_from_the_credential_is_part_of_the_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user whose admin group is their primary group is not listed in /etc/group."""
    import grp

    from stashd.auth.system import SystemIdentityLookup

    class Group:
        def __init__(self, name: str, gid: int, members: list[str]) -> None:
            self.gr_name, self.gr_gid, self.gr_mem = name, gid, members

    monkeypatch.setattr("pwd.getpwuid", lambda uid: type("P", (), {"pw_name": "mmustermann"}))
    monkeypatch.setattr(grp, "getgrall", lambda: [Group("users", 100, ["mmustermann"])])
    monkeypatch.setattr(grp, "getgrgid", lambda gid: Group("hpc-admin", 4000, []))

    username, groups, gids = SystemIdentityLookup().lookup(1000, 4000)

    assert username == "mmustermann"
    assert set(groups) == {"users", "hpc-admin"}
    assert set(gids) == {100, 4000}
