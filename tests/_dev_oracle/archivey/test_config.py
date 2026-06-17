import dataclasses

from archivey.config import (
    ArchiveyConfig,
    OverwriteMode,
    archivey_config,
    get_archivey_config,
)
from archivey.types import ArchiveMember, ExtractionFilter


def custom_filter(member: ArchiveMember) -> ArchiveMember | None:
    return member.replace(filename="custom_filename")


def test_archivey_config_fields_apply():
    """
    Check that each ArchiveyConfig field is accepted as a keyword argument
    to archivey_config() and is correctly applied to the default config.
    """
    # With the new **overrides signature, we can't check individual parameters
    # but we can still test that each field works correctly when passed as kwargs

    for field_def in dataclasses.fields(ArchiveyConfig):
        field_name = field_def.name
        param_type = field_def.type

        # define all values to test for this field
        if param_type == "bool":
            possible_values = [True, False]
        elif param_type == "OverwriteMode":
            possible_values = list(OverwriteMode)
        elif "ExtractionFilter" in str(param_type):
            possible_values = [
                ExtractionFilter.DATA,
                ExtractionFilter.TAR,
                ExtractionFilter.FULLY_TRUSTED,
                custom_filter,
            ]
        else:
            raise TypeError(f"Add test value logic for type {param_type} please")

        for test_value in possible_values:
            with archivey_config(**{field_name: test_value}):  # type: ignore
                active_config = get_archivey_config()
                actual = getattr(active_config, field_name)
                assert actual == test_value, (
                    f"{field_name} not applied correctly — expected {test_value}, got {actual}"
                )
