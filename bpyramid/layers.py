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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


def encode_order(layers: list[int]) -> list[int]:
    """返回编码顺序：按 ``(时间层, 显示序号)`` 升序排列的帧索引列表。

    层号小的帧先编码，同层按显示顺序编码；该顺序保证任意帧的参考都在它之前
    出现，因此 B 帧不会排到参考帧前面，解码时不会引用尚未到达的帧。
    返回值是 ``0 .. len(layers) - 1`` 的一个排列，索引容差为 0。
    """
    raise NotImplementedError


def decode_order(layers: list[int]) -> list[int]:
    """返回解码顺序，必须与 ``encode_order(layers)`` 逐项完全一致。

    解码器按编码顺序接收并解码帧，显示顺序由后续重排解决；解码顺序一旦与编码
    顺序不一致（例如交换了两个时间层），就会在参考缺失处解码失败。
    返回值是 ``0 .. len(layers) - 1`` 的一个排列，索引容差为 0。
    """
    raise NotImplementedError


def simulate_refs(order: list[int], layers: list[int]) -> list[int]:
    """模拟按 ``order`` 解码时参考是否可用，返回失败帧的索引列表。

    维护一个已解码集合：依次处理 ``order`` 中的帧 f，取得
    ``references(f, layers)``；若所有参考都已在集合中，则把 f 加入集合；否则把
    f 记入失败列表，并且不把 f 加入集合（后续引用它的帧会连锁失败）。

    ``order`` 必须是 ``0 .. len(layers) - 1`` 的一个排列，否则抛 ``ValueError``。
    返回列表按失败帧在 ``order`` 中出现的顺序排列，索引容差为 0；顺序完全正确
    时返回 ``[]``。
    """
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError
