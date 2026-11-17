# -*- coding: utf-8 -*-
"""
make_examples.py — 用 assets/src 里的原创源图批量生成 README 用的示例图。

    python assets/make_examples.py            # 出全部方案
    python assets/make_examples.py --only extend

输出到 assets/examples/，并压成适合放进 git 的尺寸（长边 900、JPEG q86）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "src"
OUT = HERE / "examples"
PY = sys.executable
ENGINE = ROOT / "scripts" / "hydro_engine.py"
SPLICE = ROOT / "scripts" / "splice_blur.py"

W, H = 1080, 1920          # 示例统一用 FHD 竖屏，比 1280x2772 更好放进 README


def run(cmd):
    print("  $ " + " ".join(str(c) for c in cmd[1:]), flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"failed: {cmd}")
    return r


def shrink(src: Path, dst: Path, long_edge: int = 900, quality: int = 86):
    im = Image.open(src).convert("RGB")
    s = long_edge / max(im.size)
    if s < 1:
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    im.save(dst, quality=quality, optimize=True)
    kb = dst.stat().st_size / 1024
    print(f"  -> {dst.name}  {im.width}x{im.height}  {kb:.0f} KB")


def examples(only=None):
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = []

    # 1. extend —— 夜景城市
    jobs.append(("extend", [PY, ENGINE, SRC / "city_night.jpg",
                            "-o", OUT / "_t_extend.png",
                            "--width", W, "--height", H, "--anchor", 0.33]))

    # 2. blur —— 同一张图，换成模糊填充
    jobs.append(("blur", [PY, ENGINE, SRC / "city_night.jpg",
                          "-o", OUT / "_t_blur.png",
                          "--width", W, "--height", H, "--anchor", 0.42,
                          "--mode", "blur"]))

    # 3. extend + blur-mix —— 暖色黄昏（最出氛围的一版）
    jobs.append(("sunset", [PY, ENGINE, SRC / "sunset_sea.jpg",
                            "-o", OUT / "_t_sunset.png",
                            "--width", W, "--height", H, "--anchor", 0.42,
                            "--blur-mix", 0.55, "--blur-radius", 8,
                            "--post-tone-sat-high", 1.55, "--post-tone-sat-mid", 1.35,
                            "--post-luma-bottom-ratio", 0.30,
                            "--post-stop-glow", 0.09]))

    # 4. splice_blur —— 双图拼接
    jobs.append(("splice", [PY, SPLICE, SRC / "moon_top.jpg", SRC / "moon_bottom.jpg",
                            "-o", OUT / "_t_splice.png",
                            "--width", W, "--height", H, "--focus-y", 0.30]))

    # 5. subject —— 抠白底主体。需要 rembg + u2net 模型，缺了只跳过这一张。
    jobs.append(("subject", [PY, ENGINE, SRC / "whale_white.jpg",
                             "-o", OUT / "_t_subject.png",
                             "--width", W, "--height", H, "--anchor", 0.42,
                             "--mode", "subject", "--subject-model", "u2net"]))

    for name, cmd in jobs:
        if only and only != name:
            continue
        print(f"[{name}]")
        try:
            run(cmd)
        except SystemExit:
            if name == "subject":
                print("  ! subject 需要 rembg + u2net 模型，已跳过。"
                      "装法见 README「安装」一节。", flush=True)
                continue
            raise
        shrink(OUT / f"_t_{name}.png", OUT / f"{name}.jpg")
        os.remove(OUT / f"_t_{name}.png")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", default=None)
    a = p.parse_args()
    examples(a.only)
    print("完成。")


if __name__ == "__main__":
    main()
