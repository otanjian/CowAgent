"""Read the per-scene catalog without changing the original definitions."""
import json
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent


def read_catalog(path=None):
    index = json.loads(Path(path or ROOT / "catalog.json").read_text(encoding="utf-8"))
    # Keep compatibility with explicit catalogs supplied by tests/integrators.
    if all(isinstance(item, dict) for item in index.get("scenes", [])):
        return index
    return {"categories": index["categories"], "scenes": [
        json.loads((ROOT / scene_id / "scene.json").read_text(encoding="utf-8"))
        for scene_id in index["scenes"]
    ]}


def skill_directories():
    return sorted(ROOT.glob("*/skills"))


def skill_path(name):
    for directory in skill_directories():
        candidate = directory / name
        if candidate.is_dir():
            return candidate
    raise KeyError(name)


def resolve_demo_urls(value):
    """Resolve skill:// links only in the response; source JSON stays untouched."""
    if isinstance(value, dict):
        return {k: resolve_demo_urls(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_demo_urls(v) for v in value]
    if isinstance(value, str) and value.startswith("skill://"):
        name, _, rest = value[8:].partition("/")
        try:
            file = skill_path(name) / rest
            file.resolve().relative_to(skill_path(name).resolve())
            if file.is_file():
                return "/scene-assets/" + quote(file.relative_to(ROOT).as_posix())
        except (KeyError, ValueError):
            pass
    return value
