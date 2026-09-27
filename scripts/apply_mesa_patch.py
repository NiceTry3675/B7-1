"""Check or apply the V3D register-pressure diagnostic to a Mesa checkout."""

import argparse
import sys
from pathlib import Path

from scripts.patch_utils import check_or_apply

ROOT = Path(__file__).resolve().parents[1]
PATCHES = (
    (
        ROOT / "patches/mesa/0001-v3d-ra-pre-spill-pressure.patch",
        "7c176afffe24ce04e91663ab922ae9e93edbe05ca2ca1df06d91ce11239bae2b",
    ),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesa-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        return check_or_apply(
            args.mesa_dir,
            "src/broadcom/compiler/vir_register_allocate.c",
            PATCHES,
            apply=args.apply,
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
