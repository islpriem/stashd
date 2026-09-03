"""Serving the cluster config to the daemons."""

import pytest
from fastapi.testclient import TestClient

from stashd.api.app import create_app
from stashd.auth.token import TokenAuthProvider
from stashd.config.bootstrap import BootstrapConfig
from stashd.config.cluster import ConfigDocument, parse_cluster_document

TOKEN = "peer-s3cret"


@pytest.fixture
def document(write_cluster: object, valid_cluster: dict[str, object]) -> ConfigDocument:
    from stashd.config.cluster import load_cluster_document

    return load_cluster_document(write_cluster(valid_cluster))  # type: ignore[operator]


@pytest.fixture
def controller(controller_bootstrap: BootstrapConfig, document: ConfigDocument) -> TestClient:
    app = create_app(
        controller_bootstrap,
        document.config,
        peer_auth=TokenAuthProvider(TOKEN),
        document=document,
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    return client


def test_the_config_is_served_with_its_revision_and_hash(
    controller: TestClient, document: ConfigDocument
) -> None:
    response = controller.get("/internal/v1/cluster-config")

    assert response.status_code == 200
    body = response.json()
    assert body["revision"] == 42
    assert body["content_hash"] == document.content_hash
    assert parse_cluster_document(body["text"]).config == document.config


def test_a_peer_token_is_required(controller: TestClient) -> None:
    response = controller.get("/internal/v1/cluster-config", headers={"Authorization": ""})

    assert response.status_code == 401


def test_a_daemon_has_no_config_to_serve(storage_bootstrap: BootstrapConfig) -> None:
    client = TestClient(create_app(storage_bootstrap, peer_auth=TokenAuthProvider(TOKEN)))

    response = client.get(
        "/internal/v1/cluster-config", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 404
