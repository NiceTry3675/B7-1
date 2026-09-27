# Packed 4×8 integer dot on V3DV

Here `4×8` means four packed 8-bit input pairs in one 32-bit operand. It is
not an 8-by-4 matrix tile. The Vulkan shader name is `dotPacked4x8EXT`; Mesa
lowers the corresponding signed, unsigned, or mixed NIR operation to a V3D
7.1 `V8DOT` with a signedness mode. This is the source interpretation used
for the `8x4dot` research request.

## End-to-end source path

| Layer | Checkable source fact |
| --- | --- |
| V3DV device | `v3dv_device.c` advertises accelerated packed 4×8 signed, unsigned, and mixed dot for V3D 7.1+; nonpacked 8-bit variants remain false. |
| llama.cpp capability | `ggml-vulkan.cpp` enables `integer_dot_product` only with the Vulkan extension, accelerated signed packed property, and shader feature. `GGML_VK_DISABLE_INTEGER_DOT_PRODUCT` can turn it off. |
| Q4_0 decode | `mul_mat_vecq_funcs.glsl` unpacks low/high nibbles and calls `dotPacked4x8EXT` against Q8_1. The prior `b13d13e` patch changes only its Broadcom one-token row count. |
| Quantized MMQ | `mul_mmq_funcs.glsl` uses `dotPacked4x8EXT` for Q4_0 and other packed quant types. `mul_mmq.comp` has a distinct small tile and accumulator array. |
| Mesa compiler | `nir_to_vir.c` maps `udot_4x8_uadd`, `sdot_4x8_iadd`, and `sudot_4x8_iadd` to `SETNNMODE_UU/SS/SU`, `V8DOT`, then `ADD`. |
| QPU scheduling | `qpu_schedule.c` keeps `SETNNMODE` before the dependent `V8DOT`. `US` mode exists in the QPU scheduler even though the listed NIR cases use UU, SS, and SU. |

The previous local Q4_0 shader trace includes
`setnnmode_ss` immediately before `v8dot` in the VIR dump and machine-code
disassembly lines such as `v8dot rf51, rf50, rf58`. Its original
`baseline-native-dot.log` had SHA-256
`c3e1ed3c4cd4be51e367650b5ee8620e8597e02c112175624826f47b1adbb081`.
The full trace is outside this Codyssey checkout. The source chain above and
the trace excerpt establish that the prior case reached the native opcode;
they do not validate the new tile patch.

## Tile patches and exceptions

`0002-v3dv-small-scalar-tile.patch` changes nonquant small scalar matmul.
`0003-v3dv-packed-dot-small-tile.patch` also changes these six small tile
vectors when the device is VC7, subgroup 16, and has no cooperative matrix:

```text
s_warptile_mmq          s_warptile_mmq_int
s_warptile_mmq_int_k    s_warptile_mmqid
s_warptile_mmqid_int    s_warptile_mmqid_int_k
```

All six have `BLOCK_SIZE=32`, `WARP=16`, `BM=WM=BN=32`: two warps, both in
the same output-row group. Their old `WN=32` covers 64 columns and reaches
shared B row 63 despite the 32-row shared allocation. Setting `WN=16`
partitions the workgroup's 32 columns. For packed non-K MMQ, `TN=1` and
`WMITER=2`, so `WNITER` changes 16→8 and the scalar accumulator array from
64 to 32 entries per lane. K-quant uses `WMITER=1`; `WNITER` changes 32→16
and likewise 64→32 accumulator entries. The offline enumerator covers both
shapes and the nonpacked MMQ shape.

`s_warptile_id` is deliberately untouched: its `BLOCK_SIZE=16` and
`WARP=16` give one warp, so `WN=32` already covers `BN=32`. Changing it to
16 would leave half the tile unwritten. Medium and large matrix pipelines
have different warp geometry and need separate evidence. `flash_attn.comp`
also has a packed 4×8 dot use, but its tiling is independent of these
small MMQ vectors. FP16 `DOT2_F16` is a separate floating-point extension,
not the V3D `V8DOT` path. No patch changes those paths.

The Mesa patch adds a `V3D_DEBUG=ra` line with pre-spill live temps, RF
budget, number of VIR `V8DOT` instructions, and counts of UU/SS/SU/US mode
settings. It does not alter dot lowering, QPU scheduling, or code generation.
The counts are compiler-stage diagnostics, not final machine instruction
counts or a performance measurement.

## Later checks to explain to reviewers

1. With a patched GPU build, run exact backend operations for representative
   Q2_0, Q4_0, Q4_1, Q5_0, Q5_1, Q8_0, and K quant small matrix cases.
   Include `n=1,16,31,32,33`, `m<=32`, aligned and unaligned inputs, and
   matmul-id cases if supported. Compare all outputs against CPU reference.
2. Use `V3D_DEBUG=ra` and QPU disassembly to compare pre-spill pressure,
   spills/fills, NN modes, and final `V8DOT` instructions for the same cases.
   The NN mode dependency must remain ordered after scheduling.
3. Only then measure quiet-headless latency and counters. A lower source
   accumulator count is a hypothesis about fewer spills, not a measured
   speedup. The chatbot's actual CPU run remains a separate gate.
