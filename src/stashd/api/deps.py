"""Request dependencies. Everything is read off the app the request arrived at."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.auth.owners import OwnerLookup, owner_for
from stashd.auth.provider import AuthProvider, Unauthenticated
from stashd.auth.token import TokenAuthProvider
from stashd.clients.filesets import FilesetStore
from stashd.clients.transfers import TransferDispatcher
from stashd.config.cluster import ClusterConfig
from stashd.domain.clock import Clock
from stashd.domain.identity import Principal, is_admin
from stashd.domain.storage import Owner
from stashd.services.filesets import Forbidden


def cluster_config(request: Request) -> ClusterConfig:
    config: ClusterConfig | None = request.app.state.cluster
    if config is None:  # pragma: no cover - a controller cannot start without one
        raise RuntimeError("the controller has no cluster config")
    return config


def auth_provider(request: Request) -> AuthProvider:
    provider: AuthProvider | None = request.app.state.auth
    if provider is None:  # pragma: no cover - wired at startup
        raise RuntimeError("no auth provider is configured")
    return provider


def principal(request: Request) -> Principal:
    """Authorization comes only from the verified credential."""
    provider = auth_provider(request)
    header = request.headers.get("Authorization", "")
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != provider.scheme.lower() or not credential.strip():
        raise Unauthenticated(f"expected a {provider.scheme} credential in Authorization")
    verified = provider.authenticate(credential.strip())
    request.state.principal = verified
    return verified


def peer(request: Request) -> None:
    """Internal endpoints take a peer token and nothing else."""
    provider: TokenAuthProvider | None = request.app.state.peer_auth
    if provider is None:
        raise Unauthenticated("this daemon has no peer token configured")
    scheme, _, credential = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != provider.scheme.lower():
        raise Unauthenticated(f"expected a {provider.scheme} token in Authorization")
    provider.verify(credential.strip())


async def session(request: Request) -> AsyncIterator[AsyncSession]:
    sessions = request.app.state.sessions
    if sessions is None:  # pragma: no cover - wired at startup
        raise RuntimeError("the controller has no database session factory")
    async with sessions() as open_session:
        yield open_session


def clock(request: Request) -> Clock:
    injected: Clock = request.app.state.clock
    return injected


def fileset_store(request: Request) -> FilesetStore:
    store: FilesetStore | None = request.app.state.store
    if store is None:  # pragma: no cover - wired at startup
        raise RuntimeError("the controller has no way to reach its daemons")
    return store


def transfer_dispatcher(request: Request) -> TransferDispatcher:
    dispatcher: TransferDispatcher | None = request.app.state.dispatcher
    if dispatcher is None:  # pragma: no cover - wired at startup
        raise RuntimeError("the controller has no way to dispatch transfers")
    return dispatcher


def owner_lookup(request: Request) -> OwnerLookup:
    injected: OwnerLookup = request.app.state.owners
    return injected


def subject_owner(request: Request, caller: "Principal", subject: str | None) -> Owner:
    """Acting for another user is admin-only."""
    wanted = subject or caller.username
    if wanted != caller.username and not caller_is_admin(caller, cluster_config(request)):
        raise Forbidden(f"only an admin may act for {wanted}", user=wanted)
    return owner_for(caller, wanted, owner_lookup(request))


Cluster = Annotated[ClusterConfig, Depends(cluster_config)]
Ticking = Annotated[Clock, Depends(clock)]
Store = Annotated[FilesetStore, Depends(fileset_store)]
Caller = Annotated[Principal, Depends(principal)]
Session = Annotated[AsyncSession, Depends(session)]


def caller_is_admin(caller: Principal, config: ClusterConfig) -> bool:
    return is_admin(
        caller, admin_uids=config.auth.admin_uids, admin_gids=config.auth.admin_gids
    )
