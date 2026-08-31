"""Admin recognition."""

from stashd.domain.identity import Principal, is_admin


def principal(**overrides: object) -> Principal:
    values: dict[str, object] = {
        "uid": 1000,
        "gid": 1000,
        "username": "mmustermann",
        "groups": ("users", "hpc-admin"),
        "gids": (1000, 4000),
    }
    return Principal(**{**values, **overrides})  # type: ignore[arg-type]


def test_root_is_always_an_admin() -> None:
    assert is_admin(principal(uid=0), admin_uids=[], admin_gids=[])


def test_a_listed_uid_is_an_admin() -> None:
    assert is_admin(principal(), admin_uids=[0, 1000], admin_gids=[])


def test_a_listed_group_name_makes_an_admin() -> None:
    assert is_admin(principal(), admin_uids=[0], admin_gids=["hpc-admin"])


def test_a_listed_numeric_gid_makes_an_admin() -> None:
    assert is_admin(principal(), admin_uids=[0], admin_gids=[4000])
    assert is_admin(principal(groups=(), gids=()), admin_uids=[0], admin_gids=[1000])


def test_an_ordinary_user_is_not_an_admin() -> None:
    assert not is_admin(principal(), admin_uids=[0], admin_gids=["other-admins"])


def test_a_username_never_grants_admin() -> None:
    assert not is_admin(principal(username="root"), admin_uids=[0], admin_gids=[])
