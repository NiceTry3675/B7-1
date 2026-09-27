"""Check or apply the bundled Vulkan patch to a separate llama.cpp checkout."""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

PATCH = (
    Path(__file__).resolve().parents[1]
    / "patches/llama.cpp/0001-ggml-add-broadcom-architecture.patch"
)
SHA256 = "7f4ae91a0f5e296c1e954ffadfb5bd8dcdafa919f230d6b7ef1b90730557479d"
TARGET = "ggml/src/ggml-vulkan/ggml-vulkan.cpp"


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="apply after the dry-run checks")
    args = parser.parse_args(argv)
    root = args.llama_dir.resolve()
    if hashlib.sha256(PATCH.read_bytes()).hexdigest() != SHA256:
        parser.error("bundled patch checksum mismatch")
    result = git(root, "rev-parse", "--show-toplevel")
    if result.returncode or Path(result.stdout.strip()).resolve() != root:
        parser.error("--llama-dir must be the root of a llama.cpp Git checkout")
    if not (root / TARGET).is_file():
        parser.error("target Vulkan source is missing")
    if git(root, "apply", "--reverse", "--check", str(PATCH)).returncode == 0:
        print("already applied")
        return 0
    if git(root, "diff", "--quiet", "--", TARGET).returncode:
        parser.error("target Vulkan source has unstaged changes")
    if git(root, "diff", "--cached", "--quiet", "--", TARGET).returncode:
        parser.error("target Vulkan source has staged changes")
    check = git(root, "apply", "--check", str(PATCH))
    if check.returncode:
        parser.error("patch does not apply cleanly to this checkout")
    if args.apply:
        applied = git(root, "apply", str(PATCH))
        if applied.returncode:
            parser.error("patch application failed")
        print("applied to " + str(root))
    else:
        print("patch applies cleanly; pass --apply to write it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
