"""The boundaries the code is allowed to cross.

Enforced here rather than by review: a violation is a test failure, not a comment.
"""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "stashd"

# Running a command against user data is the drivers', engines' and identity's business.
EXECUTES = frozenset({"subprocess", "shutil"})
MAY_EXECUTE = ("drivers", "engines", "identity")

# The domain is pure: it decides, it never reaches for anything.
FORBIDDEN_IN_DOMAIN = ("fastapi", "sqlalchemy", "httpx", "httpx2", "os", "subprocess", "shutil")


def modules() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize("path", modules(), ids=lambda path: str(path.name))
def test_only_drivers_engines_and_identity_run_commands(path: Path) -> None:
    package = path.relative_to(SOURCE).parts[0]
    if package in MAY_EXECUTE:
        return

    assert not (imports_of(path) & EXECUTES), (
        f"{path.relative_to(SOURCE)} runs commands; storage access goes through a driver"
    )


@pytest.mark.parametrize(
    "path", sorted((SOURCE / "domain").glob("*.py")), ids=lambda path: str(path.name)
)
def test_the_domain_layer_stays_pure(path: Path) -> None:
    assert not (imports_of(path) & set(FORBIDDEN_IN_DOMAIN)), (
        f"{path.name} reaches outside the domain layer"
    )


@pytest.mark.parametrize(
    "path", sorted((SOURCE / "domain").glob("*.py")), ids=lambda path: str(path.name)
)
def test_the_domain_layer_does_not_import_the_layers_above_it(path: Path) -> None:
    text = path.read_text()

    for banned in ("stashd.models", "stashd.api", "stashd.services", "stashd.clients"):
        assert banned not in text, f"{path.name} imports {banned}"
