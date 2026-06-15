import os
import pathlib
import random
import shutil


def truncate_archive(
    original_path: pathlib.Path,
    output_path: pathlib.Path,
    truncate_fraction: float = 0.5,
    truncate_bytes: int | None = None,
):
    """Copies the original_path to output_path and truncates the last truncate_fraction of its bytes."""
    shutil.copyfile(original_path, output_path)
    with open(output_path, "rb+") as f:
        size = os.path.getsize(output_path)
        if truncate_bytes is not None:
            truncate_at = truncate_bytes
        else:
            truncate_at = int(size * (1 - truncate_fraction))
        f.truncate(truncate_at)


def corrupt_archive(
    original_path: pathlib.Path,
    output_path: pathlib.Path,
    corruption_type: str = "single",
):
    """Copies the original_path to output_path and corrupts it based on the corruption type.

    Args:
        original_path: Path to the original archive
        output_path: Path where the corrupted archive will be written
        corruption_type: Type of corruption to apply:
        position_fraction: Where to corrupt the file (0.0 to 1.0), used for "default" type
    """
    shutil.copyfile(original_path, output_path)
    with open(output_path, "rb+") as f:
        content = bytearray(f.read())
        size = len(content)

        if size == 0:  # Cannot corrupt an empty file
            return

        if corruption_type == "single":
            position_fraction = 0.5
            num_bytes = 1
        elif (
            corruption_type == "random"
            or corruption_type == "zeroes"
            or corruption_type == "ffs"
        ):
            position_fraction = 0.5
            num_bytes = 128
        else:
            raise ValueError(f"Invalid corruption type: {corruption_type}")

        corruption_position = int(size * position_fraction)
        f.seek(corruption_position)

        current_data = f.read(num_bytes)
        if corruption_type == "single":
            corrupted_data = bytes([current_data[0] ^ 0xFF])
        elif corruption_type == "random":
            r = random.Random(current_data)
            corrupted_data = r.randbytes(num_bytes)
        elif corruption_type == "zeroes":
            corrupted_data = bytes([0] * num_bytes)
        elif corruption_type == "ffs":
            corrupted_data = bytes([0xFF] * num_bytes)
        else:
            raise ValueError(
                f"Invalid corruption type: {corruption_type}"
            )  # pragma: no cover

        f.seek(corruption_position)
        f.write(corrupted_data)
