"""Build a release archive from a checked-out tag without changing tracked files."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build_release(source: Path, output: Path, tag: str) -> None:
    """Require the tagged manifest to match and package only integration contents."""
    version = tag.lower().removeprefix("v")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != version:
        raise ValueError(
            f"Release tag {tag} does not match manifest version {manifest.get('version')}. "
            f"Set manifest.json version to {version} before creating the tag."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if (
                path.is_file()
                and path.resolve() != output.resolve()
                and "__pycache__" not in path.relative_to(source).parts
                and path.suffix != ".pyc"
            ):
                archive.write(path, path.relative_to(source))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=Path("dist/smappee_ev.zip"))
    args = parser.parse_args()
    build_release(Path("custom_components/smappee_ev"), args.output, args.tag)
