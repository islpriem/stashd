"""Reading a YAML file into a mapping, and flattening Pydantic errors into problems."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from stashd.config.errors import ConfigError


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(path, [f"{path} does not exist"])
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(path, [f"not valid YAML: {exc}"]) from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(
            path, [f"expected a mapping at the top level, found {type(data).__name__}"]
        )
    return data


def problems_from(error: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: {item['msg']}"
        for item in error.errors()
    ]
