"""List every packed 4x8 dot call site in a llama.cpp shader checkout."""

import argparse
from pathlib import Path

KNOWN = {"mul_mat_vecq_funcs.glsl", "mul_mmq_funcs.glsl", "flash_attn.comp"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llama-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.llama_dir / "ggml/src/ggml-vulkan/vulkan-shaders"
    if not root.is_dir():
        parser.error("llama.cpp Vulkan shader directory is missing")
    counts = {}
    for path in root.rglob("*"):
        if path.suffix not in {".glsl", ".comp"}:
            continue
        count = path.read_text(encoding="utf-8").count("dotPacked4x8EXT")
        if count:
            counts[str(path.relative_to(root))] = count
    for name, count in sorted(counts.items()):
        print(f"{name}: {count} source call sites")
    if set(counts) != KNOWN:
        parser.error("packed-dot source inventory changed; review new or missing paths")


if __name__ == "__main__":
    main()
