"""Release assets must contain the tagged integration and the matching manifest."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from script.build_release import build_release


@pytest.mark.parametrize("tag", ["v2026.11.0", "2026.11.0"])
def test_release_contains_only_integration_files_and_preserves_manifest(tmp_path, tag):
    source = tmp_path / "custom_components" / "smappee_ev"
    source.mkdir(parents=True)
    manifest = source / "manifest.json"
    manifest.write_text(json.dumps({"domain": "smappee_ev", "version": "2026.11.0"}))
    before = manifest.read_bytes()
    (source / "__init__.py").write_text('"""Tagged revision."""')
    (source / "translations").mkdir()
    (source / "translations" / "en.json").write_text("{}")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "module.pyc").write_bytes(b"bytecode")
    output = tmp_path / "dist" / "smappee_ev.zip"
    build_release(source, output, tag)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {"manifest.json", "__init__.py", "translations/en.json"}
        assert archive.read("manifest.json") == before
        assert b"Tagged revision" in archive.read("__init__.py")
    assert manifest.read_bytes() == before


def test_release_refuses_version_mismatch_before_creating_archive(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"version": "2026.10.0"}))
    output = tmp_path / "release.zip"
    with pytest.raises(ValueError, match="before creating the tag"):
        build_release(tmp_path, output, "v2026.11.0")
    assert not output.exists()


def test_release_workflow_checks_out_the_release_tag():
    workflow = Path(__file__).parents[1] / ".github" / "workflows" / "release.yaml"
    steps = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]["release"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "refs/tags/${{ github.event.release.tag_name }}"
    assert not any("git push" in step.get("run", "") for step in steps)
