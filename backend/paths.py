"""Lokasi folder proyek yang dipakai backend dan alat build."""
from pathlib import Path

FRONTEND = "frontend"
TEMPLATES = "templates"
STATIC = "static"
INSTANCE = "instance"
ENV_FILE = ".env"
PUBLIC = "public"


def find_root(start=None):
    current = Path(start or __file__).resolve()
    base = current if current.is_dir() else current.parent
    for candidate in (base, *base.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return base.parent


ROOT = find_root()
