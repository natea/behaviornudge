"""Configuration loading. Settings come from behaviornudge.toml; secrets only from the
environment or a local, git-ignored .env file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("behaviornudge.toml")
DEFAULT_ENV_FILE = Path(".env")


class MissingSecretError(RuntimeError):
    """A required API key is not set."""

    def __init__(self, var: str):
        super().__init__(
            f"Environment variable {var} is not set. Add it to .env (see .env.example) "
            f"or export it in your shell."
        )
        self.var = var


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if val:
            values[key.strip()] = val
    return values


def get_secret(var: str, env_file: Path | None = DEFAULT_ENV_FILE) -> str:
    """Return a secret from the environment, falling back to the .env file."""
    val = os.environ.get(var)
    if val:
        return val
    if env_file is not None:
        val = _parse_env_file(env_file).get(var)
        if val:
            return val
    raise MissingSecretError(var)


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path = field(default_factory=Path.cwd)

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    @property
    def timezone(self) -> str:
        return self.raw.get("timezone", "America/New_York")

    @property
    def data_dir(self) -> Path:
        p = self.root / self.raw.get("data_dir", "data")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def path(self, section: str, key: str) -> Path:
        p = Path(os.path.expanduser(self.section(section)[key]))
        return p if p.is_absolute() else self.root / p


EXAMPLE_CONFIG = Path("behaviornudge.example.toml")


def load_config(path: Path | str = DEFAULT_CONFIG) -> Config:
    """Load behaviornudge.toml, falling back to the committed example when it's absent."""
    path = Path(path)
    if not path.exists() and path.name == DEFAULT_CONFIG.name:
        example = path.with_name(EXAMPLE_CONFIG.name)
        if example.exists():
            path = example
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Config(raw=raw, root=path.resolve().parent)
