# -*- coding: utf-8 -*-
"""
splice_blur.py — 双图上下拼接：上图入画，下图高斯模糊铺满下半屏。

这是「blur 模式」的第二形态。hydro_engine 的 --mode blur 只模糊**同一张图
自己的下半截**；当用户另外给了一张图当延伸区背景（常见于「这两张图拼一起，
下面那张糊掉当背景」），走这个脚本。

用法：
    python splice_blur.py top.png bottom.png -o out.png \
        --width 1280 --height 2772 [--img-h 1000] [--focus-y 0.22]

关键设计（别改坏）：
  接缝的底色必须取自延伸区第 0 行。图在自己的底边 F 行里渐强模糊 + alpha
  渐隐，恰好收到这一行；延伸区也正是从这一行开始，于是天然连续。
  早先版本把羽化带放在接缝**下方**、内容却取自图上更靠上的行，
  整段错行，接缝跳变 0.0625（正常行间差 0.026），肉眼是一条硬线。
  现在的写法把跳变压到 0.0068 = 全图平均行间差，数值上完全辨识不出。
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from PIL import Image, ImageFilter


def load(p):
    return Image.open(p).convert("RGB")


def cover(img, w, h, fx=0.5, fy=0.5):
    """object-fit: cover —— 等比放大到铺满，再按焦点裁掉多余部分。"""
    iw, ih = img.size
    s = max(w / iw, h / ih)
    nw, nh = int(round(iw * s)), int(round(ih * s))
    r = img.resize((nw, nh), Image.LANCZOS)
    x = int(round((nw - w) * fx))
    y = int(round((nh - h) * fy))
    return r.crop((x, y, x + w, y + h))


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def luma(a):
    return 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]


def _u8(arr):
    return (np.clip(arr, 0, 1) * 255).astype(np.uint8)


def gauss_band(band: np.ndarray, radius: float) -> np.ndarray:
    return np.asarray(
        Image.fromarray(_u8(band), "RGB").filter(ImageFilter.GaussianBlur(radius=radius))
    ).astype(np.float32) / 255.0


def build(
    top_path: str,
    bg_path: str,
    out_path: str,
    width: int = 1280,
    height: int = 2772,
    img_h: int = 0,
    focus_y: float = 0.22,
    top_focus_y: float = 1.0,
    blur_frac: float = 0.030,
    blur_scale: float = 1.25,
    feather_frac: float = 0.105,
    seam_gain: float = 1.0,
    bottom_dim: float = 0.22,
    bg_dim: float = 0.0,
    vig: float = 0.14,
    grain: float = 0.006,
    seed: int = 7,
):
    W, H = int(width), int(height)
    top, bg = load(top_path), load(bg_path)

    # ---- 上图高度 ----
    # 不给就按宽度等比缩进画布（不裁，保住原构图与水印）。
    # 横图配竖画布时这一条很关键：硬套 anchor 0.33 会把横图两侧裁掉，
    # 主体反而变小。
    iw, ih = top.size
    nat = int(round(W * ih / iw))
    img_h = int(img_h) if img_h else nat
    img_h = min(max(img_h, 200), H - 64)
    ext_h = H - img_h

    if abs(img_h - nat) <= 2:
        photo = top.resize((W, img_h), Image.LANCZOS)
    else:
        photo = cover(top, W, img_h, 0.5, top_focus_y)
    canvas_top = np.asarray(photo).astype(np.float32) / 255.0

    # ---- 延伸区：下图放大 + 强高斯模糊 ----
    big = cover(bg, int(W * blur_scale), int(ext_h * blur_scale), 0.5, focus_y)
    r = max(6.0, ext_h * blur_frac)
    bg_arr = np.asarray(
        big.filter(ImageFilter.GaussianBlur(radius=r)).resize((W, ext_h), Image.LANCZOS)
    ).astype(np.float32) / 255.0

    if bg_dim > 0:
        bg_arr = bg_arr * (1.0 - bg_dim)

    # 接缝亮度对齐：延伸区顶行拉到上图底边的亮度附近，避免上下明暗断层。
    # 下限 0.40 而不是 0：压过头会把模糊背景的色相一起吃掉，下半屏变灰。
    seam_l = float(luma(canvas_top[-max(4, img_h // 40):]).mean())
    top_l = float(luma(bg_arr[:max(4, ext_h // 40)]).mean())
    gain_applied = 1.0
    if top_l > 1e-3:
        gain_applied = float(np.clip((seam_l * seam_gain) / top_l, 0.40, 2.4))
        bg_arr = np.clip(bg_arr * gain_applied, 0, 1)

    # 向下压暗，避免下半屏比上半屏还亮
    ys = np.linspace(0.0, 1.0, ext_h, dtype=np.float32)[:, None, None]
    bg_arr = bg_arr * (1.0 - bottom_dim * smoothstep(ys[:, :, 0])[:, :, None])

    canvas = np.zeros((H, W, 3), np.float32)
    canvas[:img_h] = canvas_top
    canvas[img_h:] = bg_arr

    # ---- 接缝溶解（见文件头「关键设计」）----
    F = max(8, int(H * feather_frac))
    F = min(F, img_h - 8)
    t = smoothstep(np.linspace(0.0, 1.0, F, dtype=np.float32))[:, None, None]

    band = canvas_top[-F:]
    band_blur = gauss_band(band, max(2.0, F * 0.55))
    band_soft = band * (1.0 - 0.7 * t) + band_blur * (0.7 * t)

    backdrop = bg_arr[0][None, :, :] * np.ones((F, 1, 1), np.float32)
    canvas[img_h - F:img_h] = band_soft * (1.0 - t) + backdrop * t

    # ---- 收尾 ----
    if vig > 0:
        yy = (np.linspace(0, 1, H, dtype=np.float32)[:, None] - 0.5) ** 2
        xx = (np.linspace(0, 1, W, dtype=np.float32)[None, :] - 0.5) ** 2
        d = np.sqrt(yy * 1.5 + xx * 1.0) / 0.88
        canvas *= (1.0 - vig * np.clip(d, 0, 1) ** 2.0)[..., None]
    if grain > 0:
        rng = np.random.default_rng(seed)
        canvas = np.clip(canvas + rng.normal(0, grain, canvas.shape).astype(np.float32), 0, 1)

    canvas = np.clip(canvas, 0, 1)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    Image.fromarray(_u8(canvas), "RGB").save(out_path)

    step = float(np.abs(canvas[img_h] - canvas[img_h - 1]).mean())
    typ = float(np.abs(canvas[img_h - 40] - canvas[img_h - 41]).mean())

    report = {
        "ok": True,
        "output": os.path.abspath(out_path),
        "width": W,
        "height": H,
        "mode": "splice_blur",
        "image_region_px": img_h,
        "extension_px": ext_h,
        "image_ratio": round(img_h / H, 4),
        "blur_radius_px": round(r, 1),
        "feather_px": F,
        "seam_luma_gain": round(gain_applied, 3),
        "seam_jump": round(step, 5),
        "row_step_typical": round(typ, 5),
        "seam_continuous": bool(step <= typ * 1.5),
    }
    return report


def main():
    p = argparse.ArgumentParser(description="双图上下拼接，下图模糊铺满延伸区")
    p.add_argument("top", help="上半部分的图（会被完整或按 cover 放进图像区）")
    p.add_argument("bg", help="下半部分取色来源的图（会模糊后铺满延伸区）")
    p.add_argument("-o", "--output", required=True, help="输出路径")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=2772)
    p.add_argument("--img-h", type=int, default=0,
                   help="图像区高度（像素）。0=按宽度等比缩进画布，不裁上图")
    p.add_argument("--focus-y", type=float, default=0.22,
                   help="下图取景焦点，0=顶部夜空，1=底部花丛（默认 0.22）")
    p.add_argument("--top-focus-y", type=float, default=1.0,
                   help="指定 --img-h 时上图的竖直焦点，1.0=保底部")
    p.add_argument("--blur-frac", type=float, default=0.030,
                   help="模糊半径占延伸区高度的比例（0.02 更清 / 0.05 更糊）")
    p.add_argument("--blur-scale", type=float, default=1.25)
    p.add_argument("--feather-frac", type=float, default=0.105,
                   help="接缝溶解带高度占画布比例（0.16 更柔和）")
    p.add_argument("--seam-gain", type=float, default=1.0)
    p.add_argument("--bottom-dim", type=float, default=0.22, help="延伸区向下压暗")
    p.add_argument("--bg-dim", type=float, default=0.0, help="延伸区整体压暗")
    p.add_argument("--vignette", type=float, default=0.14)
    p.add_argument("--grain", type=float, default=0.006)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    rep = build(
        a.top, a.bg, a.output,
        width=a.width, height=a.height, img_h=a.img_h,
        focus_y=a.focus_y, top_focus_y=a.top_focus_y,
        blur_frac=a.blur_frac, blur_scale=a.blur_scale,
        feather_frac=a.feather_frac, seam_gain=a.seam_gain,
        bottom_dim=a.bottom_dim, bg_dim=a.bg_dim,
        vig=a.vignette, grain=a.grain, seed=a.seed,
    )
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"{rep['output']}  {rep['width']}x{rep['height']}")
        print(f"  图像区 {rep['image_region_px']}px / 延伸区 {rep['extension_px']}px"
              f"（{rep['image_ratio']*100:.1f}%）")
        print(f"  模糊半径 {rep['blur_radius_px']}px  溶解带 {rep['feather_px']}px  "
              f"亮度锚定 gain {rep['seam_luma_gain']}")
        print(f"  接缝跳变 {rep['seam_jump']}  vs 全图行间差 {rep['row_step_typical']}"
              f"  → {'连续' if rep['seam_continuous'] else '存在可见硬边，检查 feather-frac'}")


if __name__ == "__main__":
    main()
