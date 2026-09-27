"""Check or apply the bundled llama.cpp Vulkan patches in order."""

import argparse
import sys
from pathlib import Path

from scripts.patch_utils import check_or_apply

ROOT = Path(__file__).resolve().parents[1]
PATCHES = (
    (
        ROOT / "patches/llama.cpp/0001-ggml-add-broadcom-architecture.patch",
        "7f4ae91a0f5e296c1e954ffadfb5bd8dcdafa919f230d6b7ef1b90730557479d",
    ),
    (
        ROOT / "patches/llama.cpp/0002-v3dv-small-scalar-tile.patch",
        "42d76a056bc8382260efe1b3f6a8cdb01960be01bdca1fcf33f36491ccfff7ee",
    ),
    (
        ROOT / "patches/llama.cpp/0003-v3dv-packed-dot-small-tile.patch",
        "636e8270644d36e506d5ee01b111b52c130035304bf68cefc6bcfe45fc5e679e",
    ),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        return check_or_apply(
            args.llama_dir, "ggml/src/ggml-vulkan/ggml-vulkan.cpp", PATCHES, apply=args.apply
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
