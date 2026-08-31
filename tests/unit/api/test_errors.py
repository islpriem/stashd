"""The error envelope and the request id that goes with it."""

from fastapi.testclient import TestClient

from stashd.domain.errors import ErrorCode


def envelope(response: object) -> dict[str, object]:
    body = response.json()["error"]  # type: ignore[attr-defined]
    assert isinstance(body, dict)
    return body


class TestAuthentication:
    def test_a_request_without_a_credential_is_unauthenticated(
        self, controller_app: TestClient
    ) -> None:
        response = controller_app.get("/api/v1/whoami", headers={"Authorization": ""})

        assert response.status_code == 401
        assert envelope(response)["code"] == ErrorCode.UNAUTHENTICATED

    def test_a_credential_in_the_wrong_scheme_is_unauthenticated(
        self, controller_app: TestClient
    ) -> None:
        response = controller_app.get("/api/v1/whoami", headers={"Authorization": "Bearer abc"})

        assert response.status_code == 401
        assert envelope(response)["code"] == ErrorCode.UNAUTHENTICATED

    def test_an_unknown_credential_is_unauthenticated(self, controller_app: TestClient) -> None:
        response = controller_app.get("/api/v1/whoami", headers={"Authorization": "Munge nope"})

        assert response.status_code == 401

    def test_the_credential_is_never_echoed(self, controller_app: TestClient) -> None:
        response = controller_app.get(
            "/api/v1/whoami", headers={"Authorization": "Munge secret"}
        )

        assert "secret" not in response.text


class TestEnvelope:
    def test_every_error_carries_a_code_message_and_request_id(
        self, controller_app: TestClient
    ) -> None:
        response = controller_app.get("/api/v1/nothing-here")

        assert response.status_code == 404
        body = envelope(response)
        assert body["code"] == ErrorCode.NOT_FOUND
        assert body["message"]
        assert body["request_id"] == response.headers["X-Request-Id"]

    def test_a_successful_response_also_carries_the_request_id(
        self, controller_app: TestClient
    ) -> None:
        response = controller_app.get("/api/v1/whoami")

        assert response.status_code == 200
        assert response.headers["X-Request-Id"]

    def test_two_requests_get_different_ids(self, controller_app: TestClient) -> None:
        first = controller_app.get("/api/v1/whoami").headers["X-Request-Id"]
        second = controller_app.get("/api/v1/whoami").headers["X-Request-Id"]

        assert first != second


def test_an_unexpected_failure_becomes_an_internal_envelope_without_detail(
    controller_bootstrap: object, cluster_config: object, auth: object
) -> None:
    from stashd.api.app import create_app

    app = create_app(controller_bootstrap, cluster_config, auth=auth)  # type: ignore[arg-type]

    async def boom() -> None:
        raise RuntimeError("a detail the client must never see")

    app.add_api_route("/api/v1/boom", boom)
    client = TestClient(app, raise_server_exceptions=False)
    client.headers["Authorization"] = "Munge cred-user"

    response = client.get("/api/v1/boom")

    assert response.status_code == 500
    assert envelope(response)["code"] == ErrorCode.INTERNAL
    assert "a detail the client must never see" not in response.text
    assert "Traceback" not in response.text
