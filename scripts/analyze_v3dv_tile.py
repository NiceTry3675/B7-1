"""Enumerate the VC7 small scalar tile mapping from mul_mm.comp constants."""

from collections import Counter


def analyze(wn, *, block_size=32, wmit=2, tn=2):
    bm, bn = 32, 32
    wm, tm, warp = 32, 2, 16
    warps = block_size // warp
    assert block_size % warp == 0 and bm % wm == 0
    numerator = wm * wn
    denominator = warp * tm * tn * wmit
    assert numerator % denominator == 0
    wniter = numerator // denominator
    assert wn % wniter == 0
    wsubm, wsubn = wm // wmit, wn // wniter
    assert wsubm % tm == 0
    outputs = Counter()
    b_rows = set()
    for warp_i in range(warps):
        warp_r = warp_i % (bm // wm)
        warp_c = warp_i // (bm // wm)
        for lane in range(warp):
            tiwr = lane % (wsubm // tm)
            tiwc = lane // (wsubm // tm)
            for wsir in range(wmit):
                row = warp_r * wm + wsir * wsubm + tiwr * tm
                for wsic in range(wniter):
                    for cc in range(tn):
                        col = warp_c * wn + wsic * wsubn + tiwc * tn + cc
                        b_rows.add(col)
                        for scalar_row in range(tm):
                            outputs[row + scalar_row, col] += 1
    expected = {(row, col) for row in range(bm) for col in range(bn)}
    return {
        "block_size": block_size,
        "wn": wn,
        "wmit": wmit,
        "tn": tn,
        "wniter": wniter,
        "accumulator_f32_per_lane": wmit * tm * wniter * tn,
        "highest_shared_b_row": max(b_rows),
        "unique_outputs": len(outputs),
        "exact_32x32_tile": set(outputs) == expected and set(outputs.values()) == {1},
    }


def main():
    for name, options in (
        ("scalar and float MMQ", {}),
        ("packed 4x8 MMQ", {"tn": 1}),
        ("packed 4x8 K-quant MMQ", {"tn": 1, "wmit": 1}),
    ):
        before, after = analyze(32, **options), analyze(16, **options)
        assert before["highest_shared_b_row"] == 63
        assert before["accumulator_f32_per_lane"] == 64
        assert not before["exact_32x32_tile"]
        assert after["highest_shared_b_row"] == 31
        assert after["accumulator_f32_per_lane"] == 32
        assert after["unique_outputs"] == 32 * 32
        assert after["exact_32x32_tile"]
        print(name, "before:", before)
        print(name, "after: ", after)
    one_warp_id = analyze(32, block_size=16)
    assert one_warp_id["exact_32x32_tile"]
    assert one_warp_id["highest_shared_b_row"] == 31
    print("nonquant matmul-id control:", one_warp_id)


if __name__ == "__main__":
    main()
