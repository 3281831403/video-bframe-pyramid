"""B 帧金字塔参考规划（纯函数接口）。

所有函数只处理单个 GOP 的显示序号与时间层，不读写文件、不联网、不使用随机数。
统一约定：

- 一个 GOP 的帧按显示顺序编号为 ``0 .. gop_len - 1``，``gop_len`` 必须是 2 的
  幂且不小于 2；帧 0 是 I 帧，帧 ``gop_len - 1`` 是 P 帧，其余帧是 B 帧。
- I 帧与 P 帧的时间层恒为 0；时间层编号范围是 ``0 .. k``，其中
  ``k = layer_count(gop_len)``，层号越大越靠近金字塔叶子。除 P 帧引用同层的
  I 帧外，参考只允许指向层号更小的帧。
- 参考帧索引用显示序号表示，必须落在同一个 GOP 内；所有索引对比容差为 0，
  必须精确相等。
- 编码顺序、解码顺序都是显示序号 ``0 .. gop_len - 1`` 的一个排列。
"""

__all__ = [
    "layer_count",
    "assign_layers",
    "references",
    "encode_order",
    "decode_order",
    "simulate_refs",
    "validate_pyramid",
    "pyramid_batch",
]


def _is_power_of_two(value: int) -> bool:
    """``value`` 是否为正的 2 的幂。"""
    return isinstance(value, int) and value > 0 and (value & (value - 1)) == 0


def _check_gop_len(gop_len: int) -> None:
    """校验 GOP 长度：必须是 2 的幂且不小于 2。"""
    if not _is_power_of_two(gop_len) or gop_len < 2:
        raise ValueError(f"gop_len 必须是 2 的幂且不小于 2，收到 {gop_len!r}")


def _check_layers(layers) -> None:
    """校验层数组的基本形状（非空、每项都是非负整数）。"""
    if not isinstance(layers, (list, tuple)) or not layers:
        raise ValueError("layers 必须是非空列表")
    for value in layers:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"时间层必须是非负整数，收到 {value!r}")


def layer_count(gop_len: int) -> int:
    """返回金字塔层数 ``k``（也就是允许出现的最大时间层号）。

    对 2 的幂 ``gop_len``：``gop_len == 2`` 时返回 0；``gop_len >= 4`` 时返回
    ``log2(gop_len)``（例如 4 -> 2、8 -> 3、16 -> 4）。时间层编号范围是
    ``0 .. k``，因此返回值必须与真实的 log2 深度一致，而不是 ``gop_len // 2``
    之类的近似值；索引容差为 0。

    参数:
        gop_len: 一个 GOP 的帧数，必须是 2 的幂且不小于 2。

    异常:
        ValueError: ``gop_len`` 不是 2 的幂或小于 2。
    """
    _check_gop_len(gop_len)
    if gop_len == 2:
        return 0
    # 2 的幂的以 2 为底的对数恰好是 bit_length - 1。
    return gop_len.bit_length() - 1


def assign_layers(gop_len: int) -> list[int]:
    """返回长度 ``gop_len`` 的列表，第 i 项是显示序号 i 的帧的时间层。

    帧 0（I 帧）和帧 ``gop_len - 1``（P 帧）恒为第 0 层；其余帧由闭区间
    ``[0, gop_len - 1]`` 逐层二分得到：对区间 ``(a, b)`` 取中点
    ``m = (a + b + 1) // 2``，令 ``layers[m] = 父层 + 1``，再递归处理
    ``(a, m)`` 与 ``(m, b)``，直到区间内没有可插入的帧。

    结果必须满足 ``max(layers) == layer_count(gop_len)``，并且 0 到最大层号之间
    每一层都至少出现一次（层号连续、采样不过密）；列表长度与下标容差均为 0。

    参数:
        gop_len: 一个 GOP 的帧数，必须是 2 的幂且不小于 2。

    异常:
        ValueError: ``gop_len`` 非法。
    """
    _check_gop_len(gop_len)

    layers = [0] * gop_len
    if gop_len == 2:
        # 只有 I、P 两个锚点，均在第 0 层。
        return layers

    # 锚点归入第 0 层。
    layers[0] = 0
    layers[gop_len - 1] = 0

    # 用显式栈代替递归，避免大 GOP 触发 Python 递归深度限制。
    stack = [(0, gop_len - 1)]
    while stack:
        left, right = stack.pop()
        if right - left <= 1:
            # 区间内没有可插入的帧。
            continue
        mid = (left + right + 1) // 2
        # 父层取区间两端已确定层号中的较大者，中点比父层深一层。
        layers[mid] = max(layers[left], layers[right]) + 1
        stack.append((left, mid))
        stack.append((mid, right))

    return layers


def references(frame_idx: int, layers: list[int]) -> tuple[int, ...]:
    """返回帧 ``frame_idx`` 的参考帧索引元组，参考帧都在同一个 GOP 内。

    规则：

    - ``frame_idx == 0``（I 帧）：返回 ``()``；
    - 最后一帧且层号为 0（P 帧）：返回 ``(0,)``；
    - 其余帧（B 帧）设 ``d = layers[frame_idx] > 0``，返回左右两侧最近的、层号
      严格小于 d 的帧 ``(left, right)``，其中 ``left < frame_idx < right``。

    返回的每个索引都必须已经出现在 ``encode_order(layers)`` 中且位于
    ``frame_idx`` 之前（即解码该帧时参考一定可用）；不得跨 GOP、不得引用自身、
    不得引用尚未解码的帧。索引容差为 0，必须精确相等。

    参数:
        frame_idx: 当前帧的显示序号。
        layers: 当前 GOP 的完整时间层数组。

    异常:
        ValueError: ``frame_idx`` 越界、层号非法，或找不到合法的左右参考。
    """
    _check_layers(layers)
    gop_len = len(layers)
    if not isinstance(frame_idx, int) or isinstance(frame_idx, bool):
        raise ValueError(f"frame_idx 必须是整数，收到 {frame_idx!r}")
    if frame_idx < 0 or frame_idx >= gop_len:
        raise ValueError(f"frame_idx {frame_idx} 越界（0..{gop_len - 1}）")

    depth = layers[frame_idx]
    if not isinstance(depth, int) or depth < 0:
        raise ValueError(f"帧 {frame_idx} 的层号 {depth!r} 非法")

    # I 帧不引用任何帧。
    if frame_idx == 0:
        return ()

    # P 帧（GOP 最后一帧、第 0 层）引用同层的 I 帧。
    if frame_idx == gop_len - 1 and depth == 0:
        return (0,)

    # 其余为 B 帧：必须能找到左右两侧层号严格更低的相邻帧。
    if depth == 0:
        raise ValueError(f"帧 {frame_idx} 的层号为 0，无法作为 B 帧找到更低层参考")

    left = None
    for idx in range(frame_idx - 1, -1, -1):
        if layers[idx] < depth:
            left = idx
            break

    right = None
    for idx in range(frame_idx + 1, gop_len):
        if layers[idx] < depth:
            right = idx
            break

    if left is None or right is None:
        raise ValueError(f"帧 {frame_idx} 找不到合法的左右参考")

    return (left, right)


def encode_order(layers: list[int]) -> list[int]:
    """返回编码顺序：按 ``(时间层, 显示序号)`` 升序排列的帧索引列表。

    层号小的帧先编码，同层按显示顺序编码；该顺序保证任意帧的参考都在它之前
    出现，因此 B 帧不会排到参考帧前面，解码时不会引用尚未到达的帧。
    返回值是 ``0 .. len(layers) - 1`` 的一个排列，索引容差为 0。
    """
    _check_layers(layers)
    return sorted(range(len(layers)), key=lambda idx: (layers[idx], idx))


def decode_order(layers: list[int]) -> list[int]:
    """返回解码顺序，必须与 ``encode_order(layers)`` 逐项完全一致。

    解码器按编码顺序接收并解码帧，显示顺序由后续重排解决；解码顺序一旦与编码
    顺序不一致（例如交换了两个时间层），就会在参考缺失处解码失败。
    返回值是 ``0 .. len(layers) - 1`` 的一个排列，索引容差为 0。
    """
    _check_layers(layers)
    return sorted(range(len(layers)), key=lambda idx: (layers[idx], idx))


def simulate_refs(order: list[int], layers: list[int]) -> list[int]:
    """模拟按 ``order`` 解码时参考是否可用，返回失败帧的索引列表。

    维护一个已解码集合：依次处理 ``order`` 中的帧 f，取得
    ``references(f, layers)``；若所有参考都已在集合中，则把 f 加入集合；否则把
    f 记入失败列表，并且不把 f 加入集合（后续引用它的帧会连锁失败）。

    ``order`` 必须是 ``0 .. len(layers) - 1`` 的一个排列，否则抛 ``ValueError``。
    返回列表按失败帧在 ``order`` 中出现的顺序排列，索引容差为 0；顺序完全正确
    时返回 ``[]``。
    """
    _check_layers(layers)
    frame_count = len(layers)

    if not isinstance(order, (list, tuple)) or len(order) != frame_count:
        raise ValueError("order 必须是 0..len(layers)-1 的一个排列")

    seen = [False] * frame_count
    for frame in order:
        if (
            not isinstance(frame, int)
            or isinstance(frame, bool)
            or frame < 0
            or frame >= frame_count
            or seen[frame]
        ):
            raise ValueError("order 必须是 0..len(layers)-1 的一个排列")
        seen[frame] = True

    decoded: set[int] = set()
    failed: list[int] = []
    for frame in order:
        refs = references(frame, layers)
        if all(ref in decoded for ref in refs):
            decoded.add(frame)
        else:
            # 不把失败帧加入集合，后续引用它的帧会连锁失败。
            failed.append(frame)

    return failed


def validate_pyramid(layers: list[int], gop_len: int) -> list[str]:
    """检查一个 GOP 的层数组，返回错误信息列表；合法时返回 ``[]``。

    至少检查以下各项：

    - ``len(layers) == gop_len``，且 ``gop_len`` 必须是 2 的幂、不小于 2；
    - 每个层号都是 ``0 .. layer_count(gop_len)`` 范围内的整数；
    - 帧 0（I 帧）和帧 ``gop_len - 1``（P 帧）的层号为 0；
    - 层号单调且不跳级：出现的层号集合恰好是 ``0 .. max(layers)``，并且
      ``max(layers) == layer_count(gop_len)``（采样不过密）；
    - 参考合法：``references`` 返回的索引都在 ``[0, gop_len - 1]`` 内、不等于
      自身、且参考层号不高于当前帧层号（B 帧的参考必须严格更低；P 帧引用同层
      I 帧是唯一例外），不跨 GOP、不引用未解码帧；
    - 孤儿帧：所有层号小于 ``max(layers)`` 的帧都必须被至少一个其它帧引用。

    错误信息是中文短句，其中与孤儿帧相关的信息必须包含子串 ``"孤儿"``；多个
    错误可以同时返回。检查结果的索引对比容差为 0。
    """
    errors: list[str] = []

    if not _is_power_of_two(gop_len) or gop_len < 2:
        return [f"gop_len 必须是 2 的幂且不小于 2，收到 {gop_len!r}"]

    if not isinstance(layers, (list, tuple)):
        return [f"层数组必须是列表，收到 {type(layers).__name__}"]

    if len(layers) != gop_len:
        errors.append(f"层数组长度 {len(layers)} 与 gop_len {gop_len} 不一致")
        # 长度不一致时无法安全地按下标做后续检查。
        return errors

    depth_limit = layer_count(gop_len)

    # 层号必须是范围内的整数；出现非法类型时无法继续做数值比较。
    valid_values = True
    for frame, layer in enumerate(layers):
        if not isinstance(layer, int) or isinstance(layer, bool):
            errors.append(f"帧 {frame} 的层号 {layer!r} 不是整数")
            valid_values = False
        elif layer < 0 or layer > depth_limit:
            errors.append(
                f"帧 {frame} 的层号 {layer} 越界（应为 0..{depth_limit}）"
            )
    if not valid_values:
        return errors

    # 锚点必须位于第 0 层。
    if layers[0] != 0:
        errors.append(f"I 帧（帧 0）必须位于第 0 层，实际为 {layers[0]}")
    if layers[-1] != 0:
        errors.append(f"P 帧（帧 {gop_len - 1}）必须位于第 0 层，实际为 {layers[-1]}")

    # 层号连续、且最大层号等于 log2 深度。
    max_layer = max(layers)
    if max_layer != depth_limit:
        errors.append(
            f"最大层号 {max_layer} 与 log2 深度 {depth_limit} 不一致"
        )
    present = set(layers)
    missing = sorted(set(range(max_layer + 1)) - present)
    if missing:
        errors.append(f"层号不连续，缺少层 {missing}")

    # 参考合法性 + 收集被引用帧。
    order = encode_order(layers)
    position = {frame: pos for pos, frame in enumerate(order)}
    referenced: set[int] = set()

    for frame in range(gop_len):
        try:
            refs = references(frame, layers)
        except ValueError as exc:
            errors.append(f"帧 {frame} 找不到合法参考：{exc}")
            continue

        for ref in refs:
            if ref < 0 or ref >= gop_len:
                errors.append(f"帧 {frame} 的参考 {ref} 越界，跨出了当前 GOP")
                continue
            if ref == frame:
                errors.append(f"帧 {frame} 不能引用自身")
                continue

            referenced.add(ref)

            if layers[ref] > layers[frame]:
                errors.append(
                    f"帧 {frame}（第 {layers[frame]} 层）引用了更高层的帧 "
                    f"{ref}（第 {layers[ref]} 层）"
                )
            elif layers[ref] == layers[frame]:
                is_p_frame_anchor = (
                    frame == gop_len - 1
                    and ref == 0
                    and layers[frame] == 0
                    and layers[ref] == 0
                )
                if not is_p_frame_anchor:
                    errors.append(
                        f"帧 {frame} 的参考 {ref} 层号未严格更低"
                    )

            if position[ref] >= position[frame]:
                errors.append(
                    f"帧 {frame} 的参考 {ref} 在编码顺序中不早于它（引用未解码帧）"
                )

    # 孤儿帧：层号低于最大层的帧必须被至少一个其它帧引用。
    for frame in range(gop_len):
        if layers[frame] < max_layer and frame not in referenced:
            errors.append(
                f"帧 {frame} 是孤儿帧（第 {layers[frame]} 层），没有被任何其它帧引用"
            )

    return errors


def pyramid_batch(gops: list[int], config: dict) -> dict:
    """批量规划多个 GOP，每个 GOP 都独立计算，互不共享可变状态。

    参数:
        gops: GOP 长度序列（每个元素都必须是 2 的幂且不小于 2）。
        config: 选项字典，支持的键如下（未给出的键取默认值）：
            - ``"validate"``: bool，默认 ``True``；为 ``True`` 时对每个 GOP 调用
              ``validate_pyramid``，结果放入该 GOP 的 ``"errors"``；为 ``False``
              时不做校验，``"errors"`` 恒为 ``[]``。
            - ``"decode_check"``: bool，默认 ``True``；为 ``True`` 时对每个 GOP
              调用 ``simulate_refs``，结果放入 ``"failed"``；为 ``False`` 时不做
              模拟，``"failed"`` 恒为 ``[]``。

    返回 ``{"gops": [...], "gop_count": int, "total_frames": int}``。``"gops"``
    中每个元素是 ``{"index": int, "gop_len": int, "layers": list[int],
    "encode_order": list[int], "decode_order": list[int], "failed": list[int],
    "errors": list[str]}``。每个 GOP 的 ``layers`` / ``encode_order`` /
    ``decode_order`` 都必须是独立对象，禁止把第一个 GOP 的层数组复用给后面的
    GOP；索引容差为 0。

    异常:
        TypeError: ``config`` 不是字典。
        ValueError: ``config`` 含未知键，或 ``gops`` 中某个长度非法。
    """
    if not isinstance(config, dict):
        raise TypeError(f"config 必须是字典，收到 {type(config).__name__}")

    unknown = [key for key in config if key not in ("validate", "decode_check")]
    if unknown:
        raise ValueError(f"config 含未知键：{unknown}")

    if not isinstance(gops, (list, tuple)):
        raise ValueError("gops 必须是长度序列")

    do_validate = bool(config.get("validate", True))
    do_decode_check = bool(config.get("decode_check", True))

    entries = []
    for index, gop_len in enumerate(gops):
        # 每个 GOP 各自计算，所有返回对象互不共享。
        try:
            _check_gop_len(gop_len)
        except ValueError as exc:
            raise ValueError(f"第 {index} 个 GOP 长度非法：{exc}") from None

        layers = assign_layers(gop_len)
        encoding = encode_order(layers)
        decoding = decode_order(layers)
        failed = simulate_refs(encoding, layers) if do_decode_check else []
        errors = validate_pyramid(layers, gop_len) if do_validate else []

        entries.append(
            {
                "index": index,
                "gop_len": gop_len,
                "layers": layers,
                "encode_order": encoding,
                "decode_order": decoding,
                "failed": failed,
                "errors": errors,
            }
        )

    return {
        "gops": entries,
        "gop_count": len(entries),
        "total_frames": sum(gops),
    }
