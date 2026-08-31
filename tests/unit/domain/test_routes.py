"""Routes, channel selection and the best-effort ETA."""

from datetime import timedelta

from stashd.domain.routes import Channel, Route, channel_for, estimate, throughput_for

MB = 1000**2


def test_a_route_renders_as_source_to_target() -> None:
    assert str(Route("HOT1", "LOC2HOT")) == "HOT1->LOC2HOT"


def test_one_daemon_for_both_storages_means_a_local_channel() -> None:
    assert channel_for("hot1", "hot1") is Channel.LOCAL


def test_two_daemons_mean_an_ssh_channel() -> None:
    assert channel_for("hot1", "loc2hot") is Channel.SSH


def test_a_route_specific_throughput_wins_over_the_default() -> None:
    routes = {"HOT1->LOC2HOT": 120 * MB}

    assert throughput_for(Route("HOT1", "LOC2HOT"), default=200 * MB, routes=routes) == 120 * MB
    assert throughput_for(Route("HOT1", "OTHER"), default=200 * MB, routes=routes) == 200 * MB


class TestEstimate:
    def test_duration_is_the_size_over_the_throughput(self) -> None:
        assert estimate(bytes_total=200 * MB, queued_ahead=0, throughput=100 * MB) == (
            timedelta(0),
            timedelta(seconds=2),
        )

    def test_the_start_waits_for_the_bytes_queued_ahead_on_the_route(self) -> None:
        start, duration = estimate(
            bytes_total=100 * MB, queued_ahead=400 * MB, throughput=100 * MB
        )

        assert start == timedelta(seconds=4)
        assert duration == timedelta(seconds=1)

    def test_an_empty_transfer_takes_no_time(self) -> None:
        assert estimate(bytes_total=0, queued_ahead=0, throughput=100 * MB) == (
            timedelta(0),
            timedelta(0),
        )
