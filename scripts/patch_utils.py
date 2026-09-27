"""Apply reviewed patch bundles without touching other repository changes."""

import hashlib
import shutil
import subprocess
import tempfile
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
    if git(root, "ls-files", "--error-unmatch", "--", target).returncode:
        raise ValueError("patch target is not tracked by Git")
    for patch, digest in patches:
        if hashlib.sha256(patch.read_bytes()).hexdigest() != digest:
            raise ValueError("bundled patch checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="b7-patch-check-") as scratch:
        target_copy = Path(scratch) / target
        target_copy.parent.mkdir(parents=True)
        shutil.copy2(root / target, target_copy)
        present = set()
        for patch, _ in reversed(patches):
            reverse = subprocess.run(
                ["git", "apply", "--reverse", "--check", str(patch)],
                cwd=scratch,
                capture_output=True,
            )
            if reverse.returncode == 0:
                subprocess.run(
                    ["git", "apply", "--reverse", str(patch)],
                    cwd=scratch,
                    capture_output=True,
                    check=True,
                )
                present.add(patch)
        needed = [patch for patch, _ in patches if patch not in present]
        for patch, _ in patches:
            if patch in present:
                print(f"already applied: {patch.name}")
    if not needed:
        return 0
    if git(root, "diff", "--quiet", "--", target).returncode:
        raise ValueError("patch target has unstaged changes")
    if git(root, "diff", "--cached", "--quiet", "--", target).returncode:
        raise ValueError("patch target has staged changes")
    with tempfile.TemporaryDirectory(prefix="b7-patch-check-") as scratch:
        target_copy = Path(scratch) / target
        target_copy.parent.mkdir(parents=True)
        shutil.copy2(root / target, target_copy)
        for patch in needed:
            check = subprocess.run(
                ["git", "apply", "--check", str(patch)], cwd=scratch, capture_output=True
            )
            if check.returncode:
                raise ValueError(f"patch does not apply cleanly: {patch.name}")
            applied = subprocess.run(["git", "apply", str(patch)], cwd=scratch, capture_output=True)
            if applied.returncode:
                raise ValueError(f"patch simulation failed: {patch.name}")
    if apply:
        for patch in needed:
            if git(root, "apply", str(patch)).returncode:
                raise ValueError(f"patch application failed: {patch.name}")
            print(f"applied: {patch.name}")
    else:
        print("bundle applies cleanly; pass --apply to write it")
    return 0
