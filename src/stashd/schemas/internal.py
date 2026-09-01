"""The controller-to-daemon wire. Peer token only; never a MUNGE credential."""

from stashd.schemas.base import Wire


class Owner(Wire):
    """Whose data this is. The controller sends what the credential proved."""

    user: str
    uid: int
    gid: int


class CreateFileset(Wire):
    storage_id: str
    name: str
    owner: Owner
    allocation_bytes: int


class DeleteFileset(Wire):
    storage_id: str
    name: str
    owner: Owner
    path: str


class FilesetLocation(Wire):
    storage_id: str
    name: str
    path: str
