"""B 帧金字塔参考规划的 15 条验收用例。

用例只调用 ``bpyramid/layers.py`` 的公开接口。初始状态下 8 个函数都是
``NotImplementedError`` 桩，因此本文件得到 15 failed（不是 error）。
"""

import math

from bpyramid.layers import (
    assign_layers,
    decode_order,
    encode_order,
    layer_count,
    pyramid_batch,
    references,
    simulate_refs,
    validate_pyramid,
)

CONFIG = {"validate": True, "decode_check": True}


def test_layer_count_uses_log2_depth():
    """缺陷 1：层数不能按 gop_len // 2 估算，必须是对数深度。"""
    assert layer_count(8) != 8 // 2
    assert layer_count(16) == int(math.log2(16))
    assert layer_count(32) == int(math.log2(32))


def test_assign_layers_starts_anchors_at_layer_zero():
    """缺陷 2：层号从 1 开始时 I 帧层级会错，锚点必须落在第 0 层。"""
    layers = assign_layers(8)
    assert layers[0] == 0
    assert layers[-1] == 0
    assert min(layers) == 0


def test_references_never_point_past_decode_position():
    """缺陷 3：后向参考若指向编码序之外的未来帧，就会引用未解码帧。"""
    layers = assign_layers(8)
    order = encode_order(layers)
    position = {frame: pos for pos, frame in enumerate(order)}
    for frame in range(8):
        for ref in references(frame, layers):
            assert position[ref] < position[frame], (
                f"帧 {frame} 的参考 {ref} 在编码顺序中不早于它"
            )


def test_references_stay_inside_one_gop():
    """缺陷 4：参考不得跨 GOP 借用下一组的帧。"""
    for gop_len in (8, 16):
        layers = assign_layers(gop_len)
        for frame in range(gop_len):
            for ref in references(frame, layers):
                assert 0 <= ref < gop_len, (
                    f"GOP={gop_len} 的帧 {frame} 引用了越界参考 {ref}"
                )


def test_encode_order_puts_reference_frames_first():
    """缺陷 5：按显示时间编码会让 B 帧排到参考帧前面。"""
    layers = assign_layers(8)
    order = encode_order(layers)
    assert order != list(range(8)), "编码顺序不应等于显示顺序"
    position = {frame: pos for pos, frame in enumerate(order)}
    for frame in range(8):
        for ref in references(frame, layers):
            assert position[ref] < position[frame], (
                f"帧 {frame} 的参考 {ref} 没有先编码"
            )


def test_decode_order_matches_encode_order():
    """缺陷 6：解码顺序与编码顺序不一致（交换层）会直接解码失败。"""
    layers = assign_layers(8)
    assert decode_order(layers) == encode_order(layers)


def test_simulate_refs_reports_undecoded_reference_use():
    """缺陷 7：模拟器不查已解码集合时，任何顺序都会返回成功。"""
    layers = assign_layers(8)
    good_order = encode_order(layers)
    assert simulate_refs(good_order, layers) == []
    failed = simulate_refs(list(range(8)), layers)
    assert failed, "按显示顺序解码时应当报出参考不可用的帧"
    assert 1 in failed


def test_validate_pyramid_flags_orphan_frames():
    """缺陷 8：低层帧若无人引用（孤儿帧），校验必须报错。"""
    assert validate_pyramid(assign_layers(8), 8) == []
    orphaned = [0, 1, 1, 1, 1, 2, 3, 0]
    messages = validate_pyramid(orphaned, 8)
    assert messages, "含孤儿帧的层数组必须报错"
    assert any("孤儿" in message for message in messages)


def test_pyramid_batch_keeps_gops_independent():
    """缺陷 9：批量规划不能把第一个 GOP 的层数组复用到后面的 GOP。"""
    plan = pyramid_batch([8, 16], CONFIG)
    assert plan["gop_count"] == 2
    assert plan["total_frames"] == 24
    assert plan["gops"][0]["gop_len"] == 8
    assert plan["gops"][1]["gop_len"] == 16
    assert plan["gops"][0]["layers"] == assign_layers(8)
    assert plan["gops"][1]["layers"] == assign_layers(16)


def test_assign_layers_respects_maximum_depth():
    """缺陷 10：采样过密会让最大层号超过 log2(gop_len)。"""
    layers = assign_layers(8)
    assert max(layers) <= layer_count(8)
    assert max(layers) == int(math.log2(8))
    assert set(layers) == {0, 1, 2, 3}


def test_gop8_has_three_pyramid_levels():
    """GOP=8 时金字塔层数为 3。"""
    assert layer_count(8) == 3


def test_i_frame_is_in_temporal_layer_zero():
    """I 帧（以及最后一帧 P）必须位于第 0 层。"""
    layers = assign_layers(8)
    assert layers[0] == 0
    assert all(layers[frame] > 0 for frame in range(1, 7))
    assert layers[7] == 0


def test_every_reference_precedes_its_frame_in_encode_order():
    """每个 GOP 中，任意帧的所有参考都必须先于它编码（可解码）。"""
    for gop_len in (8, 16):
        layers = assign_layers(gop_len)
        order = encode_order(layers)
        assert sorted(order) == list(range(gop_len))
        position = {frame: pos for pos, frame in enumerate(order)}
        for frame in range(gop_len):
            for ref in references(frame, layers):
                assert position[ref] < position[frame]


def test_encode_and_decode_order_are_identical():
    """编码顺序与解码顺序逐项一致，且都是完整排列。"""
    for gop_len in (4, 8, 16):
        layers = assign_layers(gop_len)
        order = encode_order(layers)
        assert decode_order(layers) == order
        assert sorted(order) == list(range(gop_len))


def test_two_gops_planned_independently_match_single_planning():
    """两个 GOP 一起规划，结果必须与各自单独规划完全相同。"""
    combined = pyramid_batch([8, 16], CONFIG)
    single_8 = pyramid_batch([8], CONFIG)["gops"][0]
    single_16 = pyramid_batch([16], CONFIG)["gops"][0]

    def planning(entry):
        return (
            entry["layers"],
            entry["encode_order"],
            entry["decode_order"],
            entry["failed"],
            entry["errors"],
        )

    assert planning(combined["gops"][0]) == planning(single_8)
    assert planning(combined["gops"][1]) == planning(single_16)
    assert combined["gops"][0]["layers"] != combined["gops"][1]["layers"]
