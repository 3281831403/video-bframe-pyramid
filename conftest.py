"""pytest 根配置：把仓库根目录放进 sys.path，保证 ``bpyramid`` 可导入。"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
