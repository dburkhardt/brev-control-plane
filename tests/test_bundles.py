import tarfile

import pytest

from brev_control_plane.bundles import BundleError, create_bundle_archive


def test_create_bundle_archive_adds_source_files_relative_to_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")
    package = source / "package"
    package.mkdir()
    (package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    archive = create_bundle_archive(source, tmp_path / "out" / "bundle.tar.gz")

    assert archive == tmp_path / "out" / "bundle.tar.gz"
    with tarfile.open(archive, "r:gz") as tar:
        assert sorted(tar.getnames()) == ["package/module.py", "pyproject.toml"]


def test_create_bundle_archive_excludes_any_matching_path_component(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "keep.txt").write_text("keep", encoding="utf-8")
    ignored = source / ".git"
    ignored.mkdir()
    (ignored / "config").write_text("ignore", encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    nested_ignored = nested / "runs"
    nested_ignored.mkdir()
    (nested_ignored / "result.json").write_text("ignore", encoding="utf-8")

    archive = create_bundle_archive(
        source,
        tmp_path / "bundle.tar.gz",
        exclude_names={".git", "runs"},
    )

    with tarfile.open(archive, "r:gz") as tar:
        assert tar.getnames() == ["keep.txt"]


def test_create_bundle_archive_rejects_non_directory_source(tmp_path):
    with pytest.raises(BundleError, match="source_dir must be a directory"):
        create_bundle_archive(tmp_path / "missing", tmp_path / "bundle.tar.gz")
