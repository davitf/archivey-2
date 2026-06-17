import logging
import pathlib

import pytest

from archivey.config import ArchiveyConfig
from archivey.exceptions import PackageNotInstalledError
from archivey.internal.dependency_checker import (
    format_dependency_versions,
    get_dependency_versions,
)
from tests.archivey.create_archives import create_archive
from tests.archivey.sample_archives import (
    ALTERNATIVE_CONFIG,
    SAMPLE_ARCHIVES,
    SampleArchive,
    filter_archives,
)
from tests.archivey.testing_utils import skip_if_package_missing

logger = logging.getLogger(__name__)

# Named config variants for @pytest.mark.sample_archives(configs=[...])
_CONFIG_VARIANTS: dict[str, ArchiveyConfig | None] = {
    "default": None,
    "altlibs": ALTERNATIVE_CONFIG,
    "rarstream": ArchiveyConfig(use_rar_stream=True),
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "sample_archives(extensions=None, prefixes=None, container=None, "
        "custom=None, archives=None, configs=None): "
        "declarative parametrization over sample archives and config variants",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    marker = metafunc.definition.get_closest_marker("sample_archives")
    if marker is None:
        return

    kw = marker.kwargs
    raw_archives = kw.get("archives")
    extensions = kw.get("extensions")
    prefixes = kw.get("prefixes")
    container = kw.get("container")
    custom = kw.get("custom")
    configs = kw.get("configs")

    if "sample_archive" in metafunc.fixturenames:
        if raw_archives is not None:
            archives = list(raw_archives)
        else:
            filters = []
            if container is not None:
                _c = container
                filters.append(lambda a, c=_c: a.creation_info.format.container == c)
            if custom is not None:
                filters.append(custom)
            combined = (lambda a: all(f(a) for f in filters)) if filters else None
            archives = filter_archives(
                SAMPLE_ARCHIVES,
                extensions=extensions,
                prefixes=prefixes,
                custom_filter=combined,
            )
        metafunc.parametrize("sample_archive", archives, ids=lambda a: a.filename)

    if "archivey_config" in metafunc.fixturenames and configs is not None:
        config_list = [_CONFIG_VARIANTS[name] for name in configs]
        metafunc.parametrize("archivey_config", config_list, ids=list(configs))


@pytest.fixture
def archivey_config() -> ArchiveyConfig | None:
    """Default config fixture — overridden by parametrization from @pytest.mark.sample_archives."""
    return None


@pytest.fixture
def sample_archive_path(
    sample_archive: SampleArchive,
    archivey_config: ArchiveyConfig | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    """Return path to the sample archive, creating it if needed."""
    skip_if_package_missing(sample_archive.creation_info.format, archivey_config)

    path = pathlib.Path(sample_archive.get_archive_path())
    if path.exists():
        return str(path)

    output_dir = tmp_path_factory.mktemp("generated_archives")
    try:
        return create_archive(sample_archive, str(output_dir))
    except PackageNotInstalledError as e:
        pytest.skip(
            f"Required library for {sample_archive.filename} is not installed: {e}"
        )


@pytest.fixture(autouse=True, scope="session")
def print_dependency_versions_on_failure(request):
    yield
    logger.warning(
        "\n"
        + "=" * 30
        + " Dependency Versions "
        + "=" * 30
        + "\n"
        + format_dependency_versions(get_dependency_versions())
        + "\n"
        + "=" * 80
    )
