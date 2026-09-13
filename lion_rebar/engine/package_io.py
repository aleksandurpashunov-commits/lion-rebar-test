from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Iterable

from lion_rebar.config.constants import ENGINE_BUNDLE_SUFFIX
from lion_rebar.engine.models import EnginePackage
from lion_rebar.utils.files import canonical_json_bytes, ensure_directory, safe_filename, sha256_bytes, sha256_file


class EngineBundleError(ValueError):
    pass


def export_engine_bundle(
    engine: EnginePackage,
    destination: Path,
    asset_paths: Iterable[Path] = (),
) -> Path:
    if destination.suffix.lower() != ENGINE_BUNDLE_SUFFIX:
        destination = destination.with_suffix(ENGINE_BUNDLE_SUFFIX)
    ensure_directory(destination.parent)

    engine_data = engine.to_dict()
    manifest = {
        "bundle_schema": "lion-rebar-engine-bundle/v1",
        "engine_file": "engine.json",
        "engine_id": engine.engine_id,
        "engine_version": engine.version,
        "engine_sha256": sha256_bytes(canonical_json_bytes(engine_data)),
        "assets": [],
    }
    unique_assets: dict[str, Path] = {}
    for asset in asset_paths:
        if asset.exists() and asset.is_file():
            name = safe_filename(asset.name)
            if name in unique_assets:
                stem, suffix = Path(name).stem, Path(name).suffix
                counter = 2
                while f"{stem}_{counter}{suffix}" in unique_assets:
                    counter += 1
                name = f"{stem}_{counter}{suffix}"
            unique_assets[name] = asset
            manifest["assets"].append(
                {"archive_path": f"assets/{name}", "sha256": sha256_file(asset)}
            )

    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("engine.json", json.dumps(engine_data, ensure_ascii=False, indent=2, sort_keys=True))
        for name, asset in unique_assets.items():
            archive.write(asset, arcname=f"assets/{name}")
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return destination


def inspect_engine_bundle(path: Path) -> tuple[EnginePackage, dict, list[str]]:
    try:
        archive = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise EngineBundleError("Невалиден .lionengine ZIP пакет.") from exc
    with archive:
        names = set(archive.namelist())
        if "manifest.json" not in names or "engine.json" not in names:
            raise EngineBundleError("Пакетът няма manifest.json или engine.json.")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
        engine_data = json.loads(archive.read("engine.json").decode("utf-8-sig"))
        expected = str(manifest.get("engine_sha256", ""))
        actual = sha256_bytes(canonical_json_bytes(engine_data))
        if expected and expected != actual:
            raise EngineBundleError("Checksum на engine.json не съвпада.")
        for asset in manifest.get("assets", []):
            archive_path = str(asset.get("archive_path", ""))
            if archive_path not in names:
                raise EngineBundleError(f"Липсва asset: {archive_path}")
            asset_actual = sha256_bytes(archive.read(archive_path))
            if asset.get("sha256") and asset_actual != asset.get("sha256"):
                raise EngineBundleError(f"Checksum не съвпада за {archive_path}")
        return EnginePackage.from_dict(engine_data), manifest, sorted(names)


def extract_engine_bundle_assets(path: Path, destination: Path) -> list[Path]:
    ensure_directory(destination)
    extracted: list[Path] = []
    with zipfile.ZipFile(path, "r") as archive:
        for member in archive.namelist():
            if not member.startswith("assets/") or member.endswith("/"):
                continue
            filename = safe_filename(Path(member).name)
            target = destination / filename
            target.write_bytes(archive.read(member))
            extracted.append(target)
    return extracted
