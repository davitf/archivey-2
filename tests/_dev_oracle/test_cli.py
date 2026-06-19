import subprocess

from tests.archivey.sample_archives import BASIC_ARCHIVES
from tests.archivey.testing_utils import skip_if_package_missing

SAMPLE = BASIC_ARCHIVES[0]


def _archive_path(tmpdir):
    return SAMPLE.get_archive_path()


def test_cli_list(capsys):
    archive = _archive_path(None)
    skip_if_package_missing(SAMPLE.creation_info.format, None)
    result = subprocess.run(
        ["archivey", "--list", "--hide-progress", archive],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert SAMPLE.contents.files[0].name.split("/")[0] in result.stdout


def test_cli_extract(tmp_path):
    archive = _archive_path(None)
    skip_if_package_missing(SAMPLE.creation_info.format, None)
    dest = tmp_path / "out"
    result = subprocess.run(
        [
            "archivey",
            "--extract",
            "--dest",
            str(dest),
            "--hide-progress",
            archive,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0
    expected = SAMPLE.contents.files[0].name.rstrip("/")
    assert (dest / expected).exists()
