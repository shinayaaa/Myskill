#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_env.py — 给 hydro-wallpaper 装运行环境。

只做三件事：
  1. 在 skill 目录下建独立 .venv（不污染系统 Python）
  2. 装 numpy + Pillow（extend / blur / 裁切特写必需）
  3. 装 rembg + onnxruntime（subject 抠图模式才需要）

用法：
    python setup_env.py              # 完整安装
    python setup_env.py --core-only  # 只装核心（不需要抠图时）

装完会打印引擎和自检的调用路径。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"


def run(cmd, **kw):
    print("  $ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, **kw)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="安装 hydro-wallpaper 的运行环境")
    p.add_argument("--core-only", action="store_true",
                   help="只装 numpy + Pillow，不装 rembg（不需要抠图时）")
    p.add_argument("--force", action="store_true", help="已存在 .venv 也重建")
    args = p.parse_args(argv)

    if VENV.exists() and args.force:
        import shutil
        print(f"删除旧环境 {VENV}")
        shutil.rmtree(VENV, ignore_errors=True)

    if not venv_python().exists():
        print(f"创建虚拟环境 {VENV}")
        r = run([sys.executable, "-m", "venv", str(VENV)])
        if r.returncode != 0:
            print("创建 venv 失败", file=sys.stderr)
            return 1
    else:
        print(f"复用已有环境 {VENV}")

    py = str(venv_python())
    run([py, "-m", "pip", "install", "--upgrade", "pip", "-q"])

    pkgs = ["numpy", "pillow"]
    if not args.core_only:
        pkgs += ["rembg", "onnxruntime"]
    print(f"安装依赖：{', '.join(pkgs)}")
    r = run([py, "-m", "pip", "install", *pkgs])
    if r.returncode != 0:
        print("依赖安装失败", file=sys.stderr)
        return 1

    print("\n验证：")
    r = run([py, "-c", "import numpy, PIL; print('numpy', numpy.__version__, '| Pillow', PIL.__version__)"])
    if r.returncode != 0:
        return 1

    if not args.core_only:
        r = run([py, "-c", "import rembg, onnxruntime; print('rembg ok, onnxruntime', onnxruntime.__version__)"])
        if r.returncode != 0:
            print("rembg 不可用，subject 模式将无法使用；其余方案不受影响", file=sys.stderr)

    print(f"""
完成。常用路径：

  python : {py}
  engine : {HERE / 'scripts' / 'hydro_engine.py'}
  probe  : {HERE / 'scripts' / 'probe_subject.py'}
  selftest: {HERE / 'scripts' / 'selftest.py'}

自检：
  "{py}" "{HERE / 'scripts' / 'selftest.py'}"

subject 模式首次运行会自动下载 u2net（176MB）。
网络不稳时用带续传的下载器：
  "{py}" "{HERE / 'scripts' / 'download_u2net.py'}"
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
