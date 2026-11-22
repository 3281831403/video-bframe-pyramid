# video-bframe-pyramid

把 B 帧改成金字塔参考后，画面偶发闪块、码率也没有降多少：层数直接按 gop_len//2 估算，和真实的对数深度对不上；assign_layers 从 1 开始编号，I 帧也被推到第 1 层；references 给 B 帧挑了一个还没进编码序列的未来帧当参考；参考索引还会跨出当前 GOP 去借下一组的帧；encode_order 按显示时间排序，B 帧排到了参考帧前面；decode_order 交换了两个时间层，和编码顺序不一致；simulate_refs 不看已解码集合，任何顺序都返回成功；validate_pyramid 漏掉孤儿帧，没人引用的帧白占一个位置；pyramid_batch 把第一个 GOP 的层数组复用给第二个 GOP；采样过密导致最大层号超过 log2(gop_len)；bpyramid/layers.py 是核心，tests/test_layers.py 15个红。要修的是：把层数改回 log2 深度，I/P 锚点归入第 0 层，参考只取同一 GOP 内且已解码的帧，编码按层号升序、同层按显示序，解码顺序与编码顺序保持一致，参考可用性用已解码集合严格模拟，层校验补上越界与孤儿帧检查，批量规划让每个 GOP 各自计算。

## 运行方式

```bash
cd video-bframe-pyramid
python -m pytest tests -q
```

初始输出尾部：

```text
FFFFFFFFFFFFFFF                                                          [100%]
15 failed in 0.07s
```

即恰好 `15 failed`：15 个用例全部在调用 `bpyramid/layers.py` 的桩函数时收到
`NotImplementedError`，没有 collection、import 或 syntax error。只依赖标准库
`math` 与 `pytest`，离线运行、无随机、无外部依赖。

## 字段表

| 字段 | 取值 |
| --- | --- |
| 所属领域 | 游戏、图形与多媒体 |
| 子方向 | 音视频处理 |
| 任务类型 | Bugfix |
| 语言/框架 | Python 3, pytest |
| 环境可复现等级 | 无外部依赖 |

## 目录

```text
video-bframe-pyramid/
├── README.md
├── conftest.py
├── .gitignore
├── bpyramid/
│   ├── __init__.py
│   └── layers.py
└── tests/
    └── test_layers.py
```

## 约束

- `tests/test_layers.py` 不可修改，评测以该文件为准；不要靠删改用例来“通过”。
- `bpyramid/layers.py` 中的 8 个函数必须真正实现，不能保留 `NotImplementedError`。
- 只允许标准库（`math`）和 `pytest`，不得引入第三方包、不得联网、不得使用随机数。
