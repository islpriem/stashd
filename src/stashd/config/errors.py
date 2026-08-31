"""The single error type raised for any unusable configuration file."""

from collections.abc import Sequence
from pathlib import Path


class ConfigError(Exception):
    """A configuration file was rejected. Carries every problem found, not just the first."""

    def __init__(self, path: Path, problems: Sequence[str]) -> None:
        self.path = path
        self.problems = list(problems)
        detail = "\n".join(f"  - {problem}" for problem in self.problems)
        super().__init__(f"invalid configuration in {path}:\n{detail}")
