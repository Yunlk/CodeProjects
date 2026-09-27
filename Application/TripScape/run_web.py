#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动原 Flask 网页版（等价于 python app.py），但关闭 Werkzeug 重载器。

两个注意点：

1. 不能用 `import app` 取入口文件 —— 同目录下的 `app/` 包会优先匹配 `app`，
   拿到的是包而不是 `app.py`（`python app.py` 之所以正常，是因为它作为 __main__ 运行）。
   这里用 importlib 按文件路径显式加载。
2. app.py 里是 app.run(debug=True)，debug 模式默认开启重载器，会额外 fork 一个子进程
   再加载一遍 QASystem + 句向量模型（约 1.5GB）。内存不宽裕时子进程会因 OpenBLAS
   分配失败而退出，表现为服务刚起来就死掉。这里只把 use_reloader 关掉，
   路由、接口、debug 报错页等行为与 python app.py 完全一致。

用法：
    python run_web.py            # http://127.0.0.1:5000
    python run_web.py 8000       # 指定端口
"""

import importlib.util
import sys
from pathlib import Path

ENTRY = Path(__file__).resolve().parent / "app.py"


def load_entry_module():
    """按文件路径加载 app.py，避开 app/ 包的同名遮蔽。"""
    spec = importlib.util.spec_from_file_location("tripscape_web", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    web = load_entry_module()
    print(f"启动原版 Flask 服务：http://127.0.0.1:{port} （入口 {ENTRY.name}，已关闭重载器）")
    web.app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
