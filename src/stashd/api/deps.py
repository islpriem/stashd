"""Request dependencies. Everything is read off the app the request arrived at."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from stashd.auth.provider import AuthProvider, Unauthenticated
from stashd.config.cluster import ClusterConfig
from stashd.domain.identity import Principal, is_admin


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


async def session(request: Request) -> AsyncIterator[AsyncSession]:
    sessions = request.app.state.sessions
    if sessions is None:  # pragma: no cover - wired at startup
        raise RuntimeError("the controller has no database session factory")
    async with sessions() as open_session:
        yield open_session


Cluster = Annotated[ClusterConfig, Depends(cluster_config)]
Caller = Annotated[Principal, Depends(principal)]
Session = Annotated[AsyncSession, Depends(session)]


def caller_is_admin(caller: Principal, config: ClusterConfig) -> bool:
    return is_admin(
        caller, admin_uids=config.auth.admin_uids, admin_gids=config.auth.admin_gids
    )
