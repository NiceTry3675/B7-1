# V3DV tile and register-pressure analysis

This is the GPU research track for the B7-1 submission. The chatbot's planned
real Gemma run uses CPU. Neither new GPU patch is enabled by that CPU run.
`patches/llama.cpp/0001-*` is the previously measured Broadcom Q4_0 matvec
change; `0002-*`, `0003-*`, and `patches/mesa/0001-*` are separate research
patches. The packed integer-dot family is traced in
[V3DV packed 4×8 dot](V3DV_DOT4X8.md).

## Where the small tile is selected

In llama.cpp `ggml_vk_guess_matmul_pipeline`, a device without cooperative
matrices selects the small matrix pipeline when `m <= 32` or `n <= 32`. The
RPi5 V3DV report records subgroup size 16 and no matrix cores. The scalar
small tile in `ggml_vk_load_shaders` has the fields
`{BLOCK_SIZE, BM, BN, BK, WM, WN, WMITER, TM, TN, TK, WARP}`:

| Variant | BLOCK_SIZE | BM | BN | WM | WN | WMITER | TM | TN | WARP |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Before | 32 | 32 | 32 | 32 | 32 | 2 | 2 | 2 | 16 |
| `0002` on VC7/subgroup16/scalar | 32 | 32 | 32 | 32 | 16 | 2 | 2 | 2 | 16 |

`mul_mm.comp` sets `NUM_WARPS = BLOCK_SIZE / WARP = 2`,
`warp_r = warp_i % (BM / WM)`, and `warp_c = warp_i / (BM / WM)`.
Here `BM / WM = 1`, so the two warps are column warps 0 and 1. Before the
patch they covered 32 columns each, a total of 64 despite `BN=32` and the
host dispatch denominator of 32 columns. Warp 1's `buf_b` row index can reach
63 while the shared array is sized for 32 rows. A bounds check protects final
global writes, not those shared reads. This is a static out-of-range risk, not
an observed GPU fault. For `m <= 32, n > 32`, adjacent workgroups can also
overlap output columns. With `WN=16`, the warps cover `[0,16)` and `[16,32)`;
`BN` and dispatch denominators remain 32. The second warp's highest B row is
31. No output region is omitted by this mapping.

The shader also computes:

```text
WNITER = WM * WN / (WARP * TM * TN * WMITER)
before: 32 * 32 / (16 * 2 * 2 * 2) = 8
after:  32 * 16 / (16 * 2 * 2 * 2) = 4
sums:   WMITER * TM * WNITER * TN / 2 vec2 values
before: 32 vec2 = 64 FP32 accumulator values per lane
after:  16 vec2 = 32 FP32 accumulator values per lane
```

`python -m scripts.analyze_v3dv_tile` enumerates every lane, warp, and output
coordinate using these shader formulas. It confirms 1,024 unique cells in the
patched 32×32 scalar and MMQ tiles, each written once, and B row indices at
most 31. It also checks the one-warp `matmul-id` exception. It is a source-level
arithmetic check, not a shader execution.

The V3D 7.x two-thread physical RF budget in Mesa is 64 registers. The old
accumulator count alone reaches that number; addresses, caches, and loop state
need registers too. Halving the source accumulator count creates headroom,
but LLVM/SPIR-V/NIR/VIR transformations determine actual live temps and
spills. A lower register count or faster runtime is still unmeasured.

The scalar patch changes only non-cooperative small matrix pipelines on
detected Broadcom VC7 with subgroup size 16. The packed-dot patch extends
the same two-warp correction to six small MMQ and MMQ-id vectors. It leaves
the one-warp nonquant `matmul-id`, medium/large tiles, and every other GPU
unchanged. It does not establish faster Gemma Q4_0 prefill: the common large
MMQ pipeline is outside this change. The earlier `b13d13e` patch affects
the single-token Q4_0 integer-dot matvec path, a different shader family.

## Mesa V3D patch

`patches/mesa/0001-v3d-ra-pre-spill-pressure.patch` applies against local Mesa
`origin/main` at `f6f72d4b21c`. It adds a `V3D_DEBUG=ra` diagnostic just
after live intervals and thread count are set, before graph coloring or
spilling. The line reports shader stage and ID, maximum simultaneously live
VIR temps, physical RF budget, thread count, `V8DOT` count, and NN mode
counts. It runs only on the
accumulator-free V3D path when RA debug logging is enabled. It changes no
register allocation decision or generated shader. The logged live-temp count
is a pressure indicator, not a direct measurement of physical registers.

An earlier local Board note (outside this repository) recorded 210.951 ms/run, 1.0738 billion
TMU accesses, and 30.1 million QPU stalls in an isolated run. It also found
that one-at-a-time spill selection cost far more compile time for only a 1.3%
runtime change, and that doubling LDTMU spill cost made runtime worse. Those
observations motivate changing the source tile and measuring pre-spill
pressure; they do not prove the new tile's speed or correctness. The raw
counter captures are not bundled here, so those numbers are motivation only.

## What is verified and what comes next

- The two llama patches apply in order to a clean `1f368f3` checkout. The
  result matches the isolated `v3dv-opt` source byte for byte.
- The Mesa patch applies to clean `f6f72d4b21c` and matches its isolated
  worktree. Both patch bundles pass `git diff --check` after application.
- The patched llama Vulkan translation unit compiled with the existing
  `build-vulkan` command at `-O0`. The Mesa V3D allocator translation unit
  compiled with the existing origin-main build command. These are compile
  checks, not a linked GPU build or a shader execution.
- The B7-1 mock suite checks CTS, queue lifetime, adapter contracts, failure
  storage, and patch application without a model or network connection.

Later hardware checks should compare old/new small F32/F16 matrix cases at
`n=1,16,31,32,33,64`, including `m<=32` and non-multiple dimensions, against
the CPU reference. Check aligned/unaligned and split-K paths, shader-db spill
counts, `V3D_DEBUG=ra` pre-spill pressure, and isolated QPU/TMU counters.
Only after correctness should timing be compared under the same clock,
driver, shader cache, and desktop-idle conditions. The CPU service test and
external HTTPS UI test are separate acceptance gates.

## Points to explain during review

1. The production chatbot can use CPU inference while the GPU patches remain
   reviewable research artifacts. There is no hidden GPU dependency in the
   B7-1 API adapter.
2. `b13d13e` is a measured Broadcom Q4_0 matvec specialization. The new
   `WN=16` patch addresses small scalar matrix tile coverage and pressure.
   Their test scope and performance claims must stay separate.
3. `BN` is a workgroup output width; `WN` is a per-warp width. Two 16-lane
   warps with `WN=16` cover `BN=32` exactly in this mapping.
4. Mesa owns register allocation; llama.cpp owns shader tile selection.
   Reducing pressure at the source is a specific hypothesis. The Mesa patch
   makes that hypothesis observable and does not itself optimize execution.
5. Mock passes and translation-unit compiles establish API behavior and
   syntactic validity. They do not establish real Gemma quality, V3DV shader
   correctness, or throughput. Those require the later hardware gates above.
