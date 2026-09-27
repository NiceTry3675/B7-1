"""Apply reviewed patch bundles without touching other repository changes."""

import hashlib
import subprocess
from pathlib import Path


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )


def check_or_apply(root, target, patches, *, apply=False):
    root = root.resolve()
    result = git(root, "rev-parse", "--show-toplevel")
    if result.returncode or Path(result.stdout.strip()).resolve() != root:
        raise ValueError("directory must be the root of a Git checkout")
    if not (root / target).is_file():
        raise ValueError("patch target source is missing")
    needed = []
    for patch, digest in patches:
        if hashlib.sha256(patch.read_bytes()).hexdigest() != digest:
            raise ValueError("bundled patch checksum mismatch")
        if git(root, "apply", "--reverse", "--check", str(patch)).returncode:
            needed.append(patch)
        else:
            print(f"already applied: {patch.name}")
    if not needed:
        return 0
    if git(root, "diff", "--quiet", "--", target).returncode:
        raise ValueError("patch target has unstaged changes")
    if git(root, "diff", "--cached", "--quiet", "--", target).returncode:
        raise ValueError("patch target has staged changes")
    for patch in needed:
        if git(root, "apply", "--check", str(patch)).returncode:
            raise ValueError(f"patch does not apply cleanly: {patch.name}")
    if apply:
        for patch in needed:
            if git(root, "apply", str(patch)).returncode:
                raise ValueError(f"patch application failed: {patch.name}")
            print(f"applied: {patch.name}")
    else:
        print("bundle applies cleanly; pass --apply to write it")
    return 0
