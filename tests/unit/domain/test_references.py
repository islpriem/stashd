"""Reference syntax and fileset naming."""

import pytest

from stashd.domain.errors import ErrorCode, StashError
from stashd.domain.references import (
    FilesetRef,
    PathRef,
    fileset_path,
    parse_reference,
    validate_fileset_name,
    validate_storage_path,
)


class TestParsing:
    def test_a_leading_slash_means_a_path(self) -> None:
        assert parse_reference("HOT1:/myuser/mydirectory") == PathRef(
            "HOT1", "/myuser/mydirectory"
        )

    def test_no_leading_slash_means_a_fileset(self) -> None:
        assert parse_reference("LOC2HOT:mydir") == FilesetRef("LOC2HOT", "mydir")

    def test_the_storage_root_is_a_path(self) -> None:
        assert parse_reference("HOT1:/") == PathRef("HOT1", "/")

    def test_references_render_back_to_their_text(self) -> None:
        for text in ("HOT1:/myuser/mydirectory", "LOC2HOT:mydir", "HOT1:/"):
            assert str(parse_reference(text)) == text

    @pytest.mark.parametrize("text", ["HOT1", "", ":mydir", "HOT1:", "hot1:mydir", "1HOT:/x"])
    def test_malformed_references_are_rejected(self, text: str) -> None:
        with pytest.raises(StashError) as excinfo:
            parse_reference(text)
        assert excinfo.value.code in {ErrorCode.INVALID_NAME, ErrorCode.INVALID_PATH}

    @pytest.mark.parametrize(
        "text", ["HOT1:/../etc", "HOT1:/a/../../b", "HOT1://a", "HOT1:/a//b"]
    )
    def test_paths_that_could_escape_the_storage_are_rejected(self, text: str) -> None:
        with pytest.raises(StashError) as excinfo:
            parse_reference(text)
        assert excinfo.value.code is ErrorCode.INVALID_PATH

    def test_a_trailing_slash_is_dropped(self) -> None:
        assert parse_reference("HOT1:/myuser/dir/") == PathRef("HOT1", "/myuser/dir")


class TestStoragePaths:
    def test_a_path_from_the_api_must_be_storage_relative(self) -> None:
        with pytest.raises(StashError) as excinfo:
            validate_storage_path("HOT1", "myuser/mydirectory")

        assert excinfo.value.code is ErrorCode.INVALID_PATH


class TestFilesetNames:
    @pytest.mark.parametrize("name", ["a", "mydir", "my.dir-1_2", "A" * 64, "0abc"])
    def test_valid_names_are_accepted(self, name: str) -> None:
        assert validate_fileset_name(name) == name

    @pytest.mark.parametrize(
        "name", ["", ".hidden", "-dash", "_under", "a" * 65, "with/slash", "with space", "wïth"]
    )
    def test_invalid_names_are_rejected(self, name: str) -> None:
        with pytest.raises(StashError) as excinfo:
            validate_fileset_name(name)
        assert excinfo.value.code is ErrorCode.INVALID_NAME
        assert name in excinfo.value.details["name"]


class TestFilesetPath:
    def test_the_path_is_prefix_user_name(self) -> None:
        assert (
            fileset_path("/cache/loc2", "mmustermann", "mydir")
            == "/cache/loc2/mmustermann/mydir"
        )

    def test_the_name_is_validated(self) -> None:
        with pytest.raises(StashError):
            fileset_path("/cache/loc2", "mmustermann", "../escape")
