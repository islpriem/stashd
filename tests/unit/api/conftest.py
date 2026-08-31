"""A controller app with a fake auth provider and no database."""

import pytest
from fastapi.testclient import TestClient

from stashd.api.app import create_app
from stashd.auth.fake import FakeAuthProvider
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ClusterConfig
from stashd.domain.identity import Principal

USER = Principal(uid=1000, gid=1000, username="mmustermann", groups=("users",), gids=(1000,))
ADMIN = Principal(uid=0, gid=0, username="root")
CREDENTIALS = {"cred-user": USER, "cred-admin": ADMIN}


@pytest.fixture
def auth() -> FakeAuthProvider:
    return FakeAuthProvider(CREDENTIALS)


@pytest.fixture
def controller_app(
    controller_bootstrap: BootstrapConfig, cluster_config: ClusterConfig, auth: FakeAuthProvider
) -> TestClient:
    client = TestClient(create_app(controller_bootstrap, cluster_config, auth=auth))
    client.headers["Authorization"] = "Munge cred-user"
    return client
