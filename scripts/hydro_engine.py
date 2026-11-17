#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hydro_engine.py — 氢桌面风格壁纸引擎

把一张图裁成「上部图像区」，再把它接缝处的颜色向下延伸成背景，
可选抠出主体叠回去做沉浸感。对应氢 OS 1.0 氢桌面「33% 的自由度」，
但比例和分辨率全部由调用方指定。

两种模式：
  extend  —— 纯延展：图 + 向下延伸的渐变场（不抠图）
  subject —— 抠主体：主体留在渐变场上，可越过接缝向下溢出（需要 rembg）

只依赖 numpy + Pillow；subject 模式额外需要 rembg + onnxruntime。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def _f(arr) -> np.ndarray:
    return np.asarray(arr, dtype=np.float32) / 255.0


def _u8(arr: np.ndarray) -> np.ndarray:
    return np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)


def smoothstep(e0: float, e1: float, x):
    if e1 == e0:
        return np.clip(np.asarray(x, dtype=np.float32) - e0, 0, 1)
    t = np.clip((np.asarray(x, dtype=np.float32) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def luma(rgb) -> float:
    return float(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])


def sat(rgb) -> float:
    r, g, b = float(rgb[0]), float(rgb[1]), float(rgb[2])
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx <= 0 else (mx - mn) / mx


def parse_ratio(text: str) -> float:
    """'9:16' -> 0.5625（宽/高）"""
    text = text.strip().replace("：", ":")
    if "x" in text.lower() and ":" not in text:
        w, h = text.lower().split("x")
        return float(w) / float(h)
    a, b = text.split(":")
    return float(a) / float(b)


def resolve_canvas(ratio: float, width, height, long_edge: int = 1920):
    """按比例和分辨率算画布尺寸；只给一项时按比例推另一项。"""
    if width and height:
        return int(width), int(height)
    if width:
        return int(width), int(round(int(width) / ratio))
    if height:
        return int(round(int(height) * ratio)), int(height)
    if ratio >= 1.0:
        return int(long_edge), int(round(long_edge / ratio))
    return int(round(long_edge * ratio)), int(long_edge)


# --------------------------------------------------------------------------
# 图像变换
# --------------------------------------------------------------------------


def _to_pil(arr: np.ndarray) -> Image.Image:
    if arr.ndim == 3 and arr.shape[2] == 4:
        return Image.fromarray(_u8(arr), "RGBA")
    return Image.fromarray(_u8(arr), "RGB")


def cover_resize(img: np.ndarray, out_w: int, out_h: int, fx: float = 0.5, fy: float = 0.5):
    """
    object-fit: cover —— 等比放大铺满 out_w x out_h，再按焦点裁切。
    fx / fy 是裁切焦点（0=左/上，1=右/下）。
    """
    src_h, src_w = img.shape[0], img.shape[1]
    scale = max(out_w / src_w, out_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))

    pil = _to_pil(img).resize((new_w, new_h), Image.LANCZOS)
    arr = _f(np.asarray(pil))
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=2)

    x0 = int(round((new_w - out_w) * float(np.clip(fx, 0, 1))))
    y0 = int(round((new_h - out_h) * float(np.clip(fy, 0, 1))))
    return arr[y0 : y0 + out_h, x0 : x0 + out_w]


def fit_contain(img: np.ndarray, max_w: int, max_h: int):
    """等比缩到不超过 max_w x max_h，返回图和缩放系数。"""
    src_h, src_w = img.shape[0], img.shape[1]
    scale = min(max_w / src_w, max_h / src_h)
    nw, nh = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    pil = _to_pil(img).resize((nw, nh), Image.LANCZOS)
    arr = _f(np.asarray(pil))
    if arr.ndim == 2:
        arr = np.repeat(arr[..., None], 3, axis=2)
    return arr, scale


# --------------------------------------------------------------------------
# 取色
# --------------------------------------------------------------------------


def extract_palette(img: np.ndarray, n_colors: int = 14):
    """
    中位切分量化取代表色，返回 [(rgb, 占比)]，按占比降序。
    过小的色团丢掉、过于接近的色合并，避免渐变出现脏带。
    """
    rgb = img[..., :3]
    h, w = rgb.shape[0], rgb.shape[1]
    pil = Image.fromarray(_u8(rgb), "RGB")
    if w > 220:
        pil = pil.resize((220, max(1, round(h * 220 / w))), Image.LANCZOS)

    q = pil.quantize(colors=max(4, n_colors), method=Image.Quantize.MEDIANCUT)
    pal = np.array(q.getpalette()[0 : 3 * n_colors], dtype=np.float32).reshape(-1, 3) / 255.0
    idx = np.array(q, dtype=np.int32).ravel()
    counts = np.bincount(idx, minlength=len(pal)).astype(np.float32)
    counts = counts / max(counts.sum(), 1.0)

    out = []
    for i in np.argsort(-counts):
        if counts[i] <= 0.004:
            continue
        c = tuple(float(v) for v in pal[i])
        if any(math.dist(c, k) < 0.09 for k, _ in out):
            continue
        out.append((c, float(counts[i])))
        if len(out) >= n_colors:
            break
    return out


def edge_color(img: np.ndarray, side: str, band: float = 0.05):
    """取某条边的平均色，让延伸部分从接缝自然长出来。"""
    h, w = img.shape[0], img.shape[1]
    bh = max(1, int(h * band))
    bw = max(1, int(w * band))
    if side == "bottom":
        patch = img[h - bh : h, :, :3]
    elif side == "bl":
        patch = img[h - bh : h, 0:bw, :3]
    elif side == "br":
        patch = img[h - bh : h, w - bw : w, :3]
    elif side == "left":
        patch = img[:, 0:bw, :3]
    elif side == "right":
        patch = img[:, w - bw : w, :3]
    else:
        patch = img[..., :3]
    return tuple(float(v) for v in patch.reshape(-1, 3).mean(axis=0))


def dominant_background(photo: np.ndarray, tol: float = 0.055):
    """
    判断这张图是不是「纯色背景 + 一个主体」的构图（白底商品图、素材图、贴纸）。

    做法：取四角与边框一圈的像素求中位色当背景猜测，再算有多少像素接近它。
    返回 (bg_rgb, 覆盖率)；覆盖率 < 0.18 就认为背景不纯。
    """
    h, w = photo.shape[0], photo.shape[1]
    band = max(1, min(h, w) // 22)
    edges = np.concatenate([
        photo[0:band, :, :3].reshape(-1, 3),
        photo[h - band : h, :, :3].reshape(-1, 3),
        photo[:, 0:band, :3].reshape(-1, 3),
        photo[:, w - band : w, :3].reshape(-1, 3),
    ])
    bg = np.median(edges, axis=0).astype(np.float32)
    dist = np.sqrt(((photo[..., :3] - bg[None, None, :]) ** 2).sum(axis=2))
    coverage = float((dist < tol).mean())
    return tuple(float(v) for v in bg), coverage


def choose_colors(palette, seam, deep_gain: float = 0.42):
    """
    挑主色 / 强调色 / 深色底。

    关键点：不能用「占比最大」当主色 —— 照片里面积最大的常是地板、墙面、
    桌面这类低饱和灰块，拿它铺满下半屏只会得到一片死灰。这里改成
    「占比 × 饱和度」加权，并且把近乎无彩的色（sat < 0.12）显著降权，
    让画面里真正的记忆点（红砖、暖光、衣服的颜色）胜出。
    """
    if not palette:
        base = vivid(np.array(seam, dtype=np.float32), 1.25, 0.42)
        return {
            "base": tuple(base),
            "accent": tuple(np.clip(base * 1.18 + 0.04, 0, 1)),
            "deep": tuple(np.clip(base * (1 - deep_gain), 0, 1)),
            "bright": tuple(np.clip(base + 0.12, 0, 1)),
        }

    def chroma_weight(c):
        s = sat(c)
        # 无彩色（灰/白/黑）降到 1/6，低饱和按比例递减
        if s < 0.12:
            return 0.16
        return 0.30 + 0.70 * min(s / 0.55, 1.0)

    scored = []
    for c, wgt in palette:
        presence = max(wgt, 1e-4) ** 0.65
        light = 0.45 + 0.55 * min(luma(c) / 0.45, 1.0)
        scored.append((presence * chroma_weight(c) * light, c, wgt))
    scored.sort(key=lambda t: -t[0])
    base = vivid(np.array(scored[0][1], dtype=np.float32), 1.32, None)
    lb = luma(base)
    if lb < 0.26:
        base = vivid(base, 1.0, 0.32)
    elif lb > 0.72:
        base = vivid(base, 1.0, 0.68)

    accent = base
    best = -1.0
    for c, wgt in palette:
        if luma(c) < 0.16 or sat(c) < 0.15:
            continue
        v = sat(c) * (0.45 + 0.55 * min(wgt / 0.12, 1.0))
        if v > best:
            best, accent = v, vivid(np.array(c, dtype=np.float32), 1.35, None)

    darks = [np.array(c, np.float32) for c, _ in palette if luma(c) < luma(base) * 0.80]
    if darks:
        # 深色也要尽量带色相，别选到纯黑
        deep = min(darks, key=lambda c: luma(c) - 0.10 * sat(c))
        deep = vivid(deep, 1.2, None)
    else:
        deep = vivid(base, 1.0, max(0.08, luma(base) * (1.0 - deep_gain)))

    bright = vivid(base, 1.0, min(0.86, luma(base) * 1.45 + 0.12))

    return {
        "base": tuple(float(v) for v in base),
        "accent": tuple(float(v) for v in accent),
        "deep": tuple(float(v) for v in deep),
        "bright": tuple(float(v) for v in bright),
    }


# --------------------------------------------------------------------------
# 后处理参数（全部可调，不写死在流程里）
# --------------------------------------------------------------------------

POST_DEFAULTS = {
    # --- 代表色的三档发光态 ---
    "tone_sat_high": 1.30,      # 近接缝档的饱和度增益
    "tone_sat_mid": 1.22,       # 中段档的饱和度增益
    "tone_luma_gain": 1.55,     # 近接缝档亮度 = base亮度 × 该值 + tone_luma_add
    "tone_luma_add": 0.14,
    "tone_luma_cap": 0.62,      # 近接缝档亮度上限
    "tone_low_ratio": 0.34,     # 底层亮度 = base亮度 × 该值
    "tone_low_floor": 0.10,
    # --- 抹匀（接缝色向下抹） ---
    "smear_shade": 0.28,        # 抹匀色随深度压暗量
    "smear_from": 0.02,         # 抹匀权重开始抬起的位置（0..1）
    "smear_to": 0.30,           # 抹匀权重完全交给渐变的位置
    # 渐变停靠点位置（每一项都可单独调）
    "stop_glow": 0.14,          # 接缝档的位置
    "stop_mid": 0.38,           # 中段档的位置
    "stop_base": 0.68,          # 主色档的位置
    "stop_low": 0.88,           # 底层档的位置
    "start_glow_mix": 0.45,     # 渐变起点里 glow_c 的占比（其余是接缝色）
    "base_mix": 0.94,           # 中下段 base 的明度系数
    "end_dim": 0.86,            # 最底端再压暗到 low_c 的多少
    # --- 柔化与头部还原 ---
    "soften_radius": 1 / 26,    # 整体柔化半径 = 延伸区高 × 该值
    "head_keep": 0.05,          # 顶部还原成接缝色的高度比例
    "tint_falloff": 0.8,        # 左右色温随 y 衰减指数
    # --- 亮度锚定 ---
    "luma_top_gain": 0.90,      # 顶部目标亮度 = 接缝亮度 × 该值
    "luma_top_slope": 2.0,      # 顶部目标亮度上限 = 基调 × 该值 + luma_top_add
    "luma_top_add": 0.05,
    "luma_bottom_ratio": 0.50,  # 底部目标亮度 = 基调 × 该值
    "luma_strength": 0.85,      # 0=完全不锚定，1=完全贴合目标亮度
    # --- 高光 ---
    "glow_cx": 0.50, "glow_cy": 0.02, "glow_rx": 0.95, "glow_ry": 0.26,
    "glow_main": 0.75,          # 主高光强度系数（与 --glow 相乘）
    "glow2_cx": 0.14, "glow2_cy": 0.26, "glow2_rx": 0.48, "glow2_ry": 0.60,
    "glow_secondary": 0.22,     # 次高光强度系数
    # --- 其他后处理 ---
    "vig_power": 2.0,           # 暗角曲线指数
    "scrim_height": 0.16,       # 底部压暗高度
    # --- 接缝淡化 ---
    "fade_backdrop_ratio": 0.95,  # 淡出后的底色亮度 = 原图基调 × 该值
    "fade_lift_from": 0.82,     # 淡化带顶部（靠图像侧）的亮度系数
    "fade_backdrop_mix": 0.0,   # 0=底色取扩展区首行(无缝) / 1=取基调亮度(会跳变)
    # --- 纹理缝合（C）---
    "stitch_depth": 0.05,       # 缝入图像高频残差的深度占画布比例
    "stitch_radius": 3.0,       # 高通滤波半径（越大保留的低频越多）
    "stitch_gain": 1.0,         # 残差增益
    # --- 跨接缝过渡带（B）---
    "seam_blend_strength": 0.55,  # 过渡带混合强度
}

# 参数名 -> 中文说明（用于 --help 与自检）
POST_DOC = {
    "tone_sat_high": "接缝档饱和度增益",
    "tone_sat_mid": "中段档饱和度增益",
    "tone_luma_gain": "接缝档亮度增益",
    "tone_luma_add": "接缝档亮度加成",
    "tone_luma_cap": "接缝档亮度上限",
    "tone_low_ratio": "底层亮度比例",
    "tone_low_floor": "底层亮度下限",
    "smear_shade": "抹匀色压暗量",
    "smear_from": "抹匀起点",
    "smear_to": "抹匀收敛点",
    "stop_glow": "接缝档渐变位置",
    "stop_mid": "中段档渐变位置",
    "stop_base": "主色档渐变位置",
    "stop_low": "底层档渐变位置",
    "start_glow_mix": "渐变起点高光占比",
    "base_mix": "主色明度系数",
    "end_dim": "底端压暗系数",
    "soften_radius": "柔化半径系数",
    "head_keep": "顶部还原高度",
    "tint_falloff": "色温衰减指数",
    "luma_top_gain": "顶部亮度增益",
    "luma_top_slope": "顶部亮度上限斜率",
    "luma_top_add": "顶部亮度上限加成",
    "luma_bottom_ratio": "底部亮度比例",
    "luma_strength": "亮度锚定强度",
    "glow_cx": "主高光横向位置", "glow_cy": "主高光纵向位置",
    "glow_rx": "主高光横向半径", "glow_ry": "主高光纵向半径",
    "glow_main": "主高光强度",
    "glow2_cx": "次高光横向位置", "glow2_cy": "次高光纵向位置",
    "glow2_rx": "次高光横向半径", "glow2_ry": "次高光纵向半径",
    "glow_secondary": "次高光强度",
    "vig_power": "暗角曲线指数",
    "scrim_height": "底部压暗高度",
    "fade_backdrop_ratio": "淡化底色亮度比例",
    "fade_lift_from": "淡化带顶部亮度系数",
    "fade_backdrop_mix": "底色来源混合(0=接缝色,1=基调)",
    "stitch_depth": "纹理缝合深度",
    "stitch_radius": "纹理高通半径",
    "stitch_gain": "纹理残差增益",
    "seam_blend_strength": "跨接缝过渡带强度",
}


def default_post() -> dict:
    return dict(POST_DEFAULTS)


# --------------------------------------------------------------------------
# 渐变与后处理
# --------------------------------------------------------------------------


def vertical_gradient(h: int, w: int, stops) -> np.ndarray:
    """stops: [(位置0..1, rgb)]，位置递增。段内用 smoothstep 插值。"""
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)
    cols = np.array([c for _, c in stops], dtype=np.float32)
    pos = np.array([p for p, _ in stops], dtype=np.float32)

    acc = np.repeat(cols[0][None, :], h, axis=0)
    for i in range(len(stops) - 1):
        p0, p1 = float(pos[i]), float(pos[i + 1])
        t = smoothstep(p0, p1, ys)[:, None]
        mask = ((ys >= p0) & (ys <= p1)).astype(np.float32)[:, None]
        seg = lerp(cols[i][None, :], cols[i + 1][None, :], t)
        acc = acc * (1 - mask) + seg * mask
    return np.repeat(acc[:, None, :], w, axis=1)


def horizontal_tint(canvas, left, right, strength: float, falloff: float):
    """左右色温差，让延伸区不平板；强度随 y 向下衰减。"""
    if strength <= 0:
        return canvas
    h, w = canvas.shape[0], canvas.shape[1]
    xs = np.linspace(0.0, 1.0, w, dtype=np.float32)
    ramp = smoothstep(0.0, 1.0, xs)[None, :, None]
    tint = lerp(np.array(left, np.float32)[None, None, :],
                np.array(right, np.float32)[None, None, :], ramp)
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    wgt = (1.0 - ys) ** max(falloff, 0.01) * strength
    return canvas * (1 - wgt) + (canvas * 0.45 + tint * 0.55) * wgt


def add_elliptic_glow(canvas, cx, cy, rx, ry, color, strength: float):
    """柔和椭圆高光，screen 叠加。"""
    if strength <= 0:
        return canvas
    h, w = canvas.shape[0], canvas.shape[1]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = ((xx - cx * w) / max(rx * w, 1.0)) ** 2 + ((yy - cy * h) / max(ry * h, 1.0)) ** 2
    mask = np.exp(-d * 2.2)[..., None]
    glow = np.array(color, np.float32)[None, None, :]
    screened = 1.0 - (1.0 - canvas) * (1.0 - glow)
    return canvas * (1 - mask * strength) + screened * (mask * strength)


def vignette(canvas, strength: float = 0.18, power: float = 2.0):
    if strength <= 0:
        return canvas
    h, w = canvas.shape[0], canvas.shape[1]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = (xx / w - 0.5) * 2.0
    ny = (yy / h - 0.5) * 2.0
    r = np.sqrt(nx * nx + ny * ny) / math.sqrt(2.0)
    return canvas * (1.0 - np.clip(r, 0, 1) ** power * strength)[..., None]


def bottom_scrim(canvas, height_frac: float = 0.16, strength: float = 0.22):
    """底部压暗，给 dock / 手势条留可读性。"""
    if strength <= 0:
        return canvas
    h = canvas.shape[0]
    ys = np.linspace(0.0, 1.0, h, dtype=np.float32)
    m = (smoothstep(1.0 - height_frac, 1.0, ys) ** 1.3 * strength)[:, None, None]
    return canvas * (1 - m)


def add_grain(canvas, amount: float, seed: int = 7):
    """细颗粒，抗色带。"""
    if amount <= 0:
        return canvas
    rng = np.random.default_rng(seed)
    h, w = canvas.shape[0], canvas.shape[1]
    n = rng.normal(0.0, 1.0, (h, w, 1)).astype(np.float32)
    return np.clip(canvas + np.repeat(n, 3, axis=2) * amount, 0, 1)


# --------------------------------------------------------------------------
# 延伸场核心
# --------------------------------------------------------------------------


def column_smear(photo: np.ndarray, width: int, band: float = 0.08) -> np.ndarray:
    """
    取图片最底下一条带子，按列压成一条颜色向量（靠近接缝的行权重更高）。
    每列都来自各自接缝处的颜色，抹下去时接缝天然无缝，不会出现横向色带。
    """
    h = photo.shape[0]
    sh = max(2, int(h * band))
    strip = photo[h - sh : h, :, :3]
    wgt = np.linspace(0.30, 1.0, sh, dtype=np.float32)[:, None, None]
    col = (strip * wgt).sum(axis=0) / np.maximum(wgt.sum(), 1e-6)

    pil = Image.fromarray(_u8(col[None, :, :]), "RGB")
    pil = pil.resize((width, 1), Image.BICUBIC)
    pil = pil.filter(ImageFilter.GaussianBlur(radius=max(1.0, width / 220.0)))
    return _f(np.asarray(pil))[0]


def vivid(rgb, sat_gain: float = 1.0, target_luma: float | None = None):
    """
    提纯一个颜色：增益饱和度，可选定标亮度。
    延伸区不能直接用接缝色 —— 照片底边往往是灰的地面/暗的桌面，
    直接抹下去会得到一片死灰，丢掉氢桌面那种「图的光往下溢」的感觉。
    """
    c = np.array(rgb, dtype=np.float32)
    g = float(np.clip(sat_gain, 0, 4.0))
    lum = luma(c)
    c = np.clip(lum + (c - lum) * g, 0, 1)
    if target_luma is not None:
        cur = max(luma(c), 1e-3)
        c = np.clip(c * (float(target_luma) / cur), 0, 1)
    return c


def image_tone(photo: np.ndarray, bg_rgb=None, bg_cov: float = 0.0) -> float:
    """
    量出这张图的「基调亮度」，用来锚定延伸区的明暗。

    必须用中位亮度而不是平均值：夜景里路灯、雨丝的高光会让平均值虚高，
    而人眼判断一张图亮不亮看的是大面积区域。
    如果检测到纯色背景（白底素材图），就只看主体像素，
    否则白底会把基调亮度拉到 0.6 以上。
    """
    lum = 0.2126 * photo[..., 0] + 0.7152 * photo[..., 1] + 0.0722 * photo[..., 2]
    if bg_cov >= 0.25 and bg_rgb is not None:
        dist = np.sqrt(((photo[..., :3] - np.array(bg_rgb, np.float32)[None, None, :]) ** 2).sum(axis=2))
        fg = lum[dist > 0.10]
        if fg.size > photo.shape[0] * photo.shape[1] * 0.02:
            return float(np.median(fg))
    return float(np.median(lum))


def align_luma(rgb: np.ndarray, target: float, strength: float = 0.90) -> np.ndarray:
    """把一块区域的整体亮度平移到 target，保留色相与相对明暗关系。"""
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    cur = float(lum.mean())
    if cur <= 1e-6 or target <= 0:
        return rgb
    gain = (target / cur) ** float(np.clip(strength, 0, 1))
    return np.clip(rgb * gain, 0, 1)


def build_blur_extension(
    photo: np.ndarray,
    ext_h: int,
    W: int,
    blur_radius: float,
    blur_scale: float,
    streak: float,
    seed: int,
) -> np.ndarray:
    """
    【方案 blur】用图像自身放大 + 强模糊铺满延伸区。

    和取色延展的区别：取色延展把画面抽象成一片纯色，
    blur 方案保留原图的色彩结构 —— 哪边亮哪边暗、哪里有暖光，
    都会以一团团模糊色块延续下去，观感更"贴着图走"。

    三步：
      1. 取图像底部一段（越靠近接缝信息越相关）
      2. 放大并强模糊，抹掉具体形状只留色块
      3. 向下压暗，避免下半屏比上半屏还亮
    """
    from_h = max(8, int(photo.shape[0] * 0.55))
    strip = photo[-from_h:]

    # 放大后再模糊：先放大能避免模糊半径相对图像太小、糊不开
    big_h = max(2, int(ext_h * max(blur_scale, 1.0)))
    big_w = max(2, int(W * max(blur_scale, 1.0)))
    pil = Image.fromarray(_u8(strip), "RGB").resize((big_w, big_h), Image.BICUBIC)

    # 模糊半径按输出尺寸缩放，保证不同分辨率观感一致
    r = max(1.0, ext_h * blur_radius / 100.0)
    pil = pil.filter(ImageFilter.GaussianBlur(radius=r))
    arr = _f(np.asarray(pil.resize((W, ext_h), Image.BICUBIC)))

    # 竖向压暗：接缝处保持原亮度，往下逐渐收敛
    ys = np.linspace(0.0, 1.0, ext_h, dtype=np.float32)[:, None, None]
    arr = arr * (1.0 - 0.42 * smoothstep(0.0, 1.0, ys[:, :, 0])[:, :, None])

    # 可选：轻微竖向拉丝（模糊背景常见做法，逆光雨夜尤其搭）
    if streak > 0:
        sm = arr.mean(axis=1, keepdims=True)
        arr = arr * (1.0 - streak * 0.35) + sm * (streak * 0.35)
        ys2 = np.linspace(0.0, 1.0, ext_h, dtype=np.float32)[:, None, None]
        arr = arr * (0.92 + 0.08 * ys2)

    return np.clip(arr, 0, 1)


def build_extension(
    photo, ext_h, W, colors, seam, left, right,
    glow, grain, vig, scrim, seed, tint_strength, smear_weight, depth,
    filter_bg: bool = False, tone: float | None = None, seam_luma: float | None = None,
    post: dict | None = None,
):
    """
    延伸区：接缝颜色按列往下抹一小段 → 迅速收敛到「提纯后的代表色」渐变
    → 高光/暗角/颗粒收尾。depth 是延伸区占整幅画布的比例。

    tone 是原图的基调亮度（中位亮度）。延伸区不能凭空发光：
    原图是中位 0.17 的夜景，延伸区就该是暗的；原图明亮，延伸区才跟着亮。
    最后用 align_luma 把整块区域平移到与接缝相称的亮度。
    """
    base = np.array(colors["base"], np.float32)
    deep = np.array(colors["deep"], np.float32)
    accent = np.array(colors["accent"], np.float32)
    bright = np.array(colors["bright"], np.float32)
    seam_c = np.array(seam, np.float32)

    p = post or default_post()

    # 代表色的三个「发光态」：近接缝最亮，向下逐渐收暗但保留色相。
    # 三档的饱和度增益与亮度上限都由参数控制。
    glow_c = vivid(base, p["tone_sat_high"], min(p["tone_luma_cap"], luma(base) * p["tone_luma_gain"] + p["tone_luma_add"]))
    mid_c = vivid(base, p["tone_sat_mid"], min(p["tone_luma_cap"] * 0.74, luma(base) * (p["tone_luma_gain"] * 0.71) + p["tone_luma_add"] * 0.43))
    low_c = vivid(base * 0.42 + deep * 0.58, 1.10, max(p["tone_low_floor"], luma(base) * p["tone_low_ratio"]))

    col = column_smear(photo, W)
    ys1 = np.linspace(0.0, 1.0, ext_h, dtype=np.float32)
    ys = ys1[:, None, None]

    # 源头色压暗量：越大越沉，越小越保留接缝本色
    shade = (1.0 - p["smear_shade"] * smoothstep(0.0, 1.0, ys1))[:, None, None]
    colfield = col[None, :, :] * shade

    # 渐变的停靠点位置与配色权重，全部可调
    grad = vertical_gradient(
        ext_h, W,
        [
            (0.00, tuple(seam_c * (1 - p["start_glow_mix"]) + glow_c * p["start_glow_mix"])),
            (p["stop_glow"], tuple(glow_c)),
            (p["stop_mid"], tuple(mid_c)),
            (p["stop_base"], tuple(base * p["base_mix"])),
            (p["stop_low"], tuple(low_c)),
            (1.00, tuple(low_c * p["end_dim"])),
        ],
    )

    # 抹匀只在最上面一小段起作用，之后完全交给提纯色渐变
    wgt = smoothstep(p["smear_from"], p["smear_to"], ys) * float(np.clip(smear_weight, 0, 1))
    ext = colfield * (1 - wgt) + grad * wgt
    ext = horizontal_tint(ext, left, right, tint_strength, p["tint_falloff"])

    # 整体柔化去色带，再把最上面几行还原成接缝色，保证严丝合缝。
    # 两种情况来源不同：
    #   普通图 → 用「抹匀色」col*shade，和上方图像真正连续；
    #   纯色背景图 → 抹匀色是背景白，必须改用渐变起点（已被替换成主体色）。
    soft = _f(np.asarray(
        Image.fromarray(_u8(ext), "RGB").filter(ImageFilter.GaussianBlur(radius=max(2.0, ext_h * p["soften_radius"])))
    ))
    keep = max(2, int(ext_h * p["head_keep"]))
    ramp = smoothstep(0.0, 1.0, np.linspace(0.0, 1.0, keep, dtype=np.float32))[:, None, None]
    head_tone = grad[:keep] if filter_bg else col[None, :, :] * shade[:keep]
    ext = soft.copy()
    ext[:keep] = head_tone * (1 - ramp) + soft[:keep] * ramp

    # ---- 亮度锚定 ---------------------------------------------------------
    # 延伸区的亮度必须由原图决定，不能是个固定值，
    # 否则夜景会被推成亮蓝色，跟上方图像完全不搭。
    # 必须在辉光/暗角之前做，否则后面加的效果会被这一步重新归一化掉。
    if tone is not None:
        top_l = float(seam_luma) if seam_luma is not None else tone * 1.6
        target_top = max(0.05, min(top_l * p["luma_top_gain"], tone * p["luma_top_slope"] + p["luma_top_add"]))
        target_bottom = max(0.03, tone * p["luma_bottom_ratio"])
        ys_g = np.linspace(0.0, 1.0, ext.shape[0], dtype=np.float32)
        curve = (target_top + (target_bottom - target_top) * smoothstep(0.0, 1.0, ys_g))[:, None, None]
        cur = 0.2126 * ext[..., 0:1] + 0.7152 * ext[..., 1:2] + 0.0722 * ext[..., 2:3]
        gain = np.clip(curve / np.maximum(cur, 1e-3), 0.0, 8.0) ** p["luma_strength"]
        ext = np.clip(ext * gain, 0, 1)

    # ---- 高光 -------------------------------------------------------------
    if glow > 0:
        ext = add_elliptic_glow(
            ext,
            p["glow_cx"], p["glow_cy"], p["glow_rx"],
            p["glow_ry"] * max(depth / 0.66, 0.6),
            tuple(glow_c),
            glow * p["glow_main"],
        )
        ext = add_elliptic_glow(
            ext,
            p["glow2_cx"], p["glow2_cy"], p["glow2_rx"], p["glow2_ry"],
            tuple(accent),
            glow * p["glow_secondary"],
        )

    ext = vignette(ext, vig, p["vig_power"])
    ext = add_grain(ext, grain, seed)
    ext = bottom_scrim(ext, p["scrim_height"], scrim)
    return ext


# --------------------------------------------------------------------------
# 抠图
# --------------------------------------------------------------------------

_REMBG_SESSION = None


def _rembg_session(model: str = "u2net"):
    global _REMBG_SESSION
    if _REMBG_SESSION is None:
        from rembg import new_session

        _REMBG_SESSION = new_session(model)
    return _REMBG_SESSION


def cut_subject(img: np.ndarray, model: str = "u2net", feather: float = 0.004):
    """返回 RGBA 主体（float32）；mask 边缘再羽化一道，叠回时才不像剪纸。"""
    from rembg import remove

    rgba = np.concatenate(
        [img[..., :3], np.ones((img.shape[0], img.shape[1], 1), np.float32)], axis=2
    )
    pil = Image.fromarray(_u8(rgba), "RGBA")
    out = remove(pil, session=_rembg_session(model), post_process_mask=True)
    arr = _f(np.asarray(out.convert("RGBA")))

    if feather > 0:
        a = Image.fromarray(_u8(np.repeat(arr[..., 3:4], 3, axis=2)), "RGB")
        r = max(1, int(max(arr.shape[0], arr.shape[1]) * feather))
        a = a.filter(ImageFilter.GaussianBlur(radius=r))
        arr[..., 3] = _f(np.asarray(a))[..., 0]
    return arr


def cover_transform(src_w: int, src_h: int, out_w: int, out_h: int, fx: float = 0.5, fy: float = 0.5):
    """
    返回 cover 裁切的变换参数 (scale, x0, y0)：
    源图放大 scale 倍后，左上角被裁掉 (x0, y0) 像素。
    用于把抠图结果按原图坐标精确叠回去。
    """
    scale = max(out_w / src_w, out_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    x0 = int(round((new_w - out_w) * float(np.clip(fx, 0, 1))))
    y0 = int(round((new_h - out_h) * float(np.clip(fy, 0, 1))))
    return scale, x0, y0


def warp_subject_to_canvas(
    subj_rgba: np.ndarray,
    canvas_w: int,
    canvas_h: int,
    scale: float,
    x0: int,
    y0: int,
):
    """
    把整张源图的 RGBA 按 cover 变换放到 canvas 坐标系里，
    左上角对齐 (-x0, -y0)。这样主体在原图中的位置被完整保留，
    即使它原本位于图像区之外（下方），也能自然延伸到延展区。
    """
    sw, sh = subj_rgba.shape[1], subj_rgba.shape[0]
    new_w = max(1, round(sw * scale))
    new_h = max(1, round(sh * scale))
    pil = Image.fromarray(_u8(subj_rgba), "RGBA").resize((new_w, new_h), Image.LANCZOS)
    full = _f(np.asarray(pil))

    layer = np.zeros((canvas_h, canvas_w, 4), np.float32)
    layer[..., 3] = 0.0
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    w = min(new_w - sx0, canvas_w - dx0)
    h = min(new_h - sy0, canvas_h - dy0)
    if w > 0 and h > 0:
        layer[dy0 : dy0 + h, dx0 : dx0 + w] = full[sy0 : sy0 + h, sx0 : sx0 + w]
    return layer


def cleanup_alpha(alpha: np.ndarray, min_area_ratio: float = 0.012, fill_holes: bool = True):
    """
    去掉抠图里的小碎块（噪点、被误认成主体的零散部件），
    并填掉主体内部的镂空。纯 numpy 实现，不引入 scipy。
    """
    from collections import deque

    m = (alpha > 0.5).astype(np.uint8)
    h, w = m.shape
    if h < 8 or w < 8:
        return alpha

    # 连通域标记（4 邻域），只保留面积够大的块
    seen = np.zeros((h, w), bool)
    keep = np.zeros((h, w), bool)
    min_area = max(16, int(h * w * min_area_ratio))
    ys, xs = np.nonzero(m)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx] or not m[sy, sx]:
            continue
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        comp = []
        while q:
            cy, cx = q.popleft()
            comp.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(comp) >= min_area:
            for cy, cx in comp:
                keep[cy, cx] = True

    if not keep.any():
        return alpha

    if fill_holes:
        # 从边框向内泛洪，标记「真背景」，其余的空洞补成主体
        bg = np.zeros((h, w), bool)
        q = deque()
        for x in range(w):
            for y in (0, h - 1):
                if not keep[y, x] and not bg[y, x]:
                    bg[y, x] = True
                    q.append((y, x))
        for y in range(h):
            for x in (0, w - 1):
                if not keep[y, x] and not bg[y, x]:
                    bg[y, x] = True
                    q.append((y, x))
        while q:
            cy, cx = q.popleft()
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and not keep[ny, nx] and not bg[ny, nx]:
                    bg[ny, nx] = True
                    q.append((ny, nx))
        keep = keep | (~bg)

    out = alpha.copy()
    out[~keep] = 0.0
    # 被填掉的洞要补成不透明，否则只是「掩码上算作主体、alpha 仍是 0」，
    # 叠回画布时洞里会透出背景。
    out[keep & (out < 0.5)] = 1.0
    return out


def subject_bbox(alpha: np.ndarray, thresh: float = 0.35):
    m = alpha > thresh
    if not m.any():
        return None
    ys, xs = np.where(m)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


# --------------------------------------------------------------------------
# 合成
# --------------------------------------------------------------------------


def scale_layer_about(layer: np.ndarray, factor: float, cx: float, cy: float):
    """
    以 (cx, cy) 为不动点缩放整层 RGBA。
    抠出的主体单独放大缩小时，底边中心保持不动，位置才不会漂。
    """
    if abs(factor - 1.0) < 1e-3:
        return layer
    h, w = layer.shape[0], layer.shape[1]
    new_w = max(2, int(round(w * factor)))
    new_h = max(2, int(round(h * factor)))
    pil = Image.fromarray(_u8(layer), "RGBA").resize((new_w, new_h), Image.LANCZOS)
    scaled = _f(np.asarray(pil))

    out = np.zeros_like(layer)
    ox = int(round(cx - cx * factor))
    oy = int(round(cy - cy * factor))

    dx0, dy0 = max(0, ox), max(0, oy)
    sx0, sy0 = max(0, -ox), max(0, -oy)
    cw = min(new_w - sx0, w - dx0)
    ch = min(new_h - sy0, h - dy0)
    if cw > 0 and ch > 0:
        out[dy0 : dy0 + ch, dx0 : dx0 + cw] = scaled[sy0 : sy0 + ch, sx0 : sx0 + cw]
    return out


def subject_bbox(alpha: np.ndarray, thresh: float = 0.35):
    m = alpha > thresh
    if not m.any():
        return None
    ys, xs = np.where(m)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def align_seam_luma(img_rows: np.ndarray, ext_rows: np.ndarray, strength: float = 1.0):
    """
    【A3 边界对齐】让接缝两侧在交界处亮度相等。

    实测发现接缝处存在单行 +0.14 的亮度跳变（全图平均单行跳变只有 0.0007），
    是整幅图最刺眼的不连续。原因是淡化带底色和扩展区首行是两个独立计算的
    曲线，各自合理却硬碰。

    双向各让一半：图像侧尾行抬一点、扩展区首行压一点，
    差值按 1 - smoothstep 在前若干行内衰减到 0。
    """
    if strength <= 0 or ext_rows.shape[0] == 0:
        return img_rows, ext_rows

    def luma_of(a):
        return 0.2126 * a[..., 0].mean() + 0.7152 * a[..., 1].mean() + 0.0722 * a[..., 2].mean()

    l_img = luma_of(img_rows[-1:])
    l_ext = luma_of(ext_rows[0:1])
    diff = l_ext - l_img
    if abs(diff) < 1e-4:
        return img_rows, ext_rows

    n_i = img_rows.shape[0]
    n_e = ext_rows.shape[0]
    blend_rows = max(2, min(n_i, n_e, 48))

    # 图像侧：从尾行向上衰减，尾行承担一半的修正
    ys_i = np.linspace(0.0, 1.0, n_i, dtype=np.float32)
    w_i = (smoothstep(0.0, 1.0, ys_i) ** 2)[:, None, None]
    gain_i = 1.0 + (diff * 0.5 * strength) / max(l_img, 1e-3) * w_i
    img_out = np.clip(img_rows * gain_i, 0, 1)

    # 扩展区：从首行向下衰减，首行承担另一半
    ys_e = np.linspace(0.0, 1.0, n_e, dtype=np.float32)
    w_e = (1.0 - smoothstep(0.0, 1.0, ys_e)) ** 2
    gain_e = 1.0 - (diff * 0.5 * strength) / max(l_ext, 1e-3) * w_e[:, None, None]
    ext_out = np.clip(ext_rows * gain_e, 0, 1)
    return img_out, ext_out


def highpass_texture(photo: np.ndarray, radius: float = 3.0) -> np.ndarray:
    """
    【C 纹理缝合】取图像的高频残差（细节 − 模糊），
    也就是雨丝、噪点、线条这类"看起来有质感"的部分。
    """
    src = photo[..., :3]
    blurred = _f(np.asarray(
        Image.fromarray(_u8(src), "RGB").filter(ImageFilter.GaussianBlur(radius=max(0.6, radius)))
    ))
    return src - blurred


def stitch_texture(
    ext_rows: np.ndarray,
    photo: np.ndarray,
    amount: float,
    depth: int,
    radius: float = 3.0,
    gain: float = 1.0,
) -> np.ndarray:
    """
    把图像底部的「无方向性颗粒」按 y 衰减地叠进扩展区顶部。

    踩过的坑：直接把高频残差按列铺下去，会在扩展区拉出一片竖向条纹。
    原因是雨丝本身是竖向细线，它属于「有方向的纹理」，一旦被复制成
    延展区的地面纹理，人眼立刻读成拖影/刮痕，比接缝本身更难忍。

    所以这里只提取各向同性的颗粒：
      1. 高通得到残差
      2. 分别横向、纵向模糊，取两者较小值近似「无方向」分量
      3. 幅值压到颗粒量级
    方向性强的结构（雨丝、电线杆边）在步骤 2 会被压掉。
    """
    if amount <= 0 or depth <= 0:
        return ext_rows
    n = min(depth, ext_rows.shape[0], photo.shape[0])
    if n <= 1:
        return ext_rows

    hp = highpass_texture(photo[-n:], radius) * gain

    def blur_axis(arr, r):
        u8 = _u8(np.clip(arr * 0.5 + 0.5, 0, 1))
        pil = Image.fromarray(u8, "RGB")
        pil = pil.filter(ImageFilter.GaussianBlur(radius=max(1.2, r)))
        return _f(np.asarray(pil)) * 2.0 - 1.0

    r = max(2.0, n / 30.0)
    # 横向模糊后再与原图相减 ≈ 保留竖向结构；纵向同理保留横向结构
    vert_struct = np.abs(hp - blur_axis(hp, r))
    horizon_struct = np.abs(hp - blur_axis(hp.transpose(1, 0, 2), r).transpose(1, 0, 2))
    directional = np.maximum(vert_struct, horizon_struct)

    # 残差里减掉方向性成分，剩下的才是各向同性颗粒
    grain_only = hp * np.clip(1.0 - directional / 0.05, 0.0, 1.0)

    ys = np.linspace(0.0, 1.0, n, dtype=np.float32)
    w = (1.0 - smoothstep(0.0, 1.0, ys)) ** 1.4 * amount

    grain_only = np.clip(grain_only, -0.035, 0.035)
    out = ext_rows.copy()
    out[:n] = np.clip(out[:n] + grain_only * w[:, None, None], 0, 1)
    return out


def add_global_grain(canvas: np.ndarray, amount: float, seed: int = 7) -> np.ndarray:
    """
    【D 统一颗粒】颗粒不再只加在扩展区，而是整幅图共享一层。
    之前只有扩展区有颗粒，图像区干净，接缝处颗粒有无也构成一条断层。
    """
    if amount <= 0:
        return canvas
    rng = np.random.default_rng(seed)
    h, w = canvas.shape[0], canvas.shape[1]
    n = rng.normal(0.0, 1.0, (h, w, 1)).astype(np.float32)
    return np.clip(canvas + np.repeat(n, 3, axis=2) * amount, 0, 1)


def blend_across_seam(
    canvas: np.ndarray,
    img_h: int,
    above: int = 60,
    below: int = 60,
    strength: float = 0.6,
):
    """
    【B 跨接缝过渡带】淡化不再止于 y=img_h，而是继续侵入扩展区若干行，
    在 img_h ± N 内做两侧交叉混合。接缝从"一条线"变成"一条有宽度的带"。
    """
    if strength <= 0 or above <= 0 or below <= 0:
        return canvas
    H = canvas.shape[0]
    top = max(0, img_h - above)
    bot = min(H, img_h + below)
    if bot - top < 4:
        return canvas

    seg = canvas[top:bot].copy()
    # 以接缝为界的上下两段互为参照，各自向对方平均色靠拢，
    # 靠拢量在接缝处最大、向两端衰减。
    up_mean = canvas[max(0, img_h - above) : img_h].reshape(-1, 3).mean(axis=0)
    dn_mean = canvas[img_h : min(H, img_h + below)].reshape(-1, 3).mean(axis=0)

    ys = (np.arange(top, bot, dtype=np.float32) - img_h) / max(above, below, 1)
    w = np.exp(-(ys ** 2) * 3.0) * strength
    # 接缝上方用下方均色、下方用上方均色，各自向对方靠拢
    target = np.where((ys < 0)[:, None], dn_mean[None, :], up_mean[None, :])
    target = np.repeat(target[:, None, :], canvas.shape[1], axis=1)
    out = seg * (1 - w[:, None, None]) + target * w[:, None, None]
    canvas[top:bot] = np.clip(out, 0, 1)
    return canvas


def _alpha_over(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> np.ndarray:
    dh, dw = dst.shape[0], dst.shape[1]
    sh, sw = src.shape[0], src.shape[1]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + sw), min(dh, y + sh)
    if x1 <= x0 or y1 <= y0:
        return dst
    sx0, sy0 = x0 - x, y0 - y
    s = src[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
    d = dst[y0:y1, x0:x1]
    a = s[..., 3:4]
    d[:] = d * (1 - a) + s[..., :3] * a
    return dst


def make_wallpaper(
    src_path: str,
    out_path: str,
    ratio: float = 9 / 16,
    width=None,
    height=None,
    long_edge: int = 1920,
    anchor: float = 0.33,
    mode: str = "extend",
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    bg_mode: str = "gradient",
    subject_scale: float = 1.0,
    subject_overflow: float = 0.14,
    subject_model: str = "u2net",
    subject_bottom: float = 0.0,
    glow: float = 0.26,
    smear_weight: float = 0.72,
    grain: float = 0.008,
    vig: float = 0.18,
    scrim: float = 0.20,
    seam_feather: float = 0.040,
    seam_fade: float = 0.10,
    seam_blur: float = 0.030,
    seam_align: float = 1.0,
    seam_blend: float = 0.0,
    stitch: float = 0.85,
    blur_edge: float = 0.0,
    blur_mix: float = 0.0,
    blur_radius: float = 6.0,
    blur_scale: float = 1.35,
    blur_streak: float = 0.0,
    blur_fade: float = 0.22,
    tint_strength: float = 0.30,
    deep_gain: float = 0.42,
    post: dict | None = None,
    seed: int = 7,
) -> dict:
    W, H = resolve_canvas(ratio, width, height, long_edge)
    p = post or default_post()
    anchor = float(np.clip(anchor, 0.08, 0.92))
    img_h = max(16, int(round(H * anchor)))
    ext_h = H - img_h

    raw = Image.open(src_path).convert("RGBA")
    src = _f(np.asarray(raw))

    photo = cover_resize(src[..., :3], W, img_h, focus_x, focus_y)
    palette = extract_palette(photo, 14)

    # 白底素材图/贴纸：大面积纯背景会把取色冲淡，剔除后再挑代表色
    bg_rgb, bg_cov = dominant_background(photo)
    if bg_cov >= 0.18:
        bg_l = luma(bg_rgb)
        filtered = [
            (c, w) for c, w in palette
            if not (luma(c) > 0.80 and bg_l > 0.78) and not (luma(c) < 0.14 and bg_l < 0.16)
        ]
        if len(filtered) >= 2:
            palette = filtered

    seam = edge_color(photo, "bottom")
    left = edge_color(photo, "bl")
    right = edge_color(photo, "br")

    # 接缝本身就是那片纯背景时，改用主体色起头，否则延伸区会被白色冲淡
    bg_flattened = False
    if bg_cov >= 0.28:
        seam_span = max(
            np.sqrt(((photo[..., :3] - np.array(bg_rgb, np.float32)[None, None, :]) ** 2).sum(axis=2))[
                -max(2, img_h // 12) :, :
            ].mean(),
            1e-6,
        )
        if seam_span < 0.06:
            main_c = palette[0][0] if palette else bg_rgb
            seam = tuple(v * 0.80 for v in main_c)
            left = tuple(v * 0.78 for v in main_c)
            right = tuple(v * 0.82 for v in main_c)
            bg_flattened = True

    colors = choose_colors(palette, seam, deep_gain)
    tone = image_tone(photo, bg_rgb, bg_cov)

    base = np.array(colors["base"], np.float32)
    bright = np.array(colors["bright"], np.float32)
    seam_c = np.array(seam, np.float32)

    ext = build_extension(
        photo, ext_h, W, colors, seam, left, right,
        glow=glow, grain=0.0, vig=vig, scrim=scrim, seed=seed,
        tint_strength=tint_strength, smear_weight=smear_weight,
        depth=ext_h / H, filter_bg=bg_flattened,
        tone=tone, seam_luma=luma(seam), post=post,
    )

    # ---- 方案 blur：延伸区改用「图像放大 + 强模糊」 --------------------
    # blur_mix 控制占比：1=纯模糊，0=纯取色延展，中间值是两者叠化。
    # 这样既能整片换成模糊背景，也能让模糊只作为取色渐变上的一层氛围。
    if mode == "blur" or blur_mix > 0:
        blur_ext = build_blur_extension(
            photo, ext_h, W,
            blur_radius=blur_radius,
            blur_scale=blur_scale,
            streak=blur_streak,
            seed=seed,
        )
        mix = 1.0 if mode == "blur" else float(np.clip(blur_mix, 0.0, 1.0))
        ext = ext * (1.0 - mix) + blur_ext * mix

    # 【C 纹理缝合】把图像底部的雨丝/噪点残差缝进扩展区顶部。
    # 扩展区本来是纯平滑渐变，纹理在接缝处突然消失，这个断层
    # 比亮度断层更容易被眼睛抓住。
    if stitch > 0:
        ext = stitch_texture(
            ext, photo, amount=stitch,
            depth=max(8, int(H * p["stitch_depth"])),
            radius=p["stitch_radius"],
            gain=p["stitch_gain"],
        )

    canvas = np.zeros((H, W, 3), np.float32)
    canvas[0:img_h] = photo
    canvas[img_h:H] = ext

    subject_extra = {}

    if mode == "subject":
        subj = cut_subject(src, subject_model, feather=max(0.003, seam_feather * 0.15))
        subj[..., 3] = cleanup_alpha(subj[..., 3], min_area_ratio=0.010, fill_holes=True)
        bbox = subject_bbox(subj[..., 3])
        if bbox is None:
            raise RuntimeError("抠图失败：没有找到主体，换 --subject-model 或改用 --mode extend")

        if bg_mode == "gradient":
            # 背景整个交给渐变：图像区也铺渐变，只留主体
            head = vertical_gradient(
                img_h, W,
                [
                    (0.00, tuple(seam_c * 0.86 + base * 0.14)),
                    (0.55, tuple(seam_c * 0.92 + base * 0.08)),
                    (1.00, tuple(seam_c)),
                ],
            )
            head = horizontal_tint(head, left, right, tint_strength * 0.8, 1.4)
            head = add_elliptic_glow(head, 0.5, 1.02, 1.2, 0.95, tuple(bright), glow * 0.65)
            head = vignette(head, vig * 0.8)
            head = add_grain(head, grain * 0.8, seed + 1)
            canvas[0:img_h] = head
        else:
            canvas[0:img_h] = photo

        # 按原图坐标把主体映射进画布：位置、比例都跟原构图一致
        scale, x0, y0 = cover_transform(
            src.shape[1], src.shape[0], W, img_h, focus_x, focus_y
        )
        layer = warp_subject_to_canvas(subj, W, H, scale, x0, y0)

        bx0, by0, bx1, by1 = bbox
        # subject-scale 微调主体大小（围绕其底边中心缩放）
        if abs(subject_scale - 1.0) > 1e-3:
            cx = (bx0 + bx1) / 2 * scale - x0
            byb = by1 * scale - y0
            layer = scale_layer_about(layer, float(np.clip(subject_scale, 0.3, 2.5)), cx, byb)

        # 放置高度：默认让主体底边正好压在接缝下方一点
        if subject_bottom > 0:
            target_bottom = int(H * float(np.clip(subject_bottom, 0.05, 1.0)))
        else:
            target_bottom = int(img_h + ext_h * float(np.clip(subject_overflow, 0.0, 0.95)))
        dy = target_bottom - int(by1 * scale - y0)
        if dy != 0:
            layer = np.roll(layer, dy, axis=0)
            if dy > 0:
                layer[:dy, :, :] = 0.0
            else:
                layer[dy:, :, :] = 0.0

        if glow > 0:
            ys_s = np.linspace(0.0, 1.0, H, dtype=np.float32)
            band = np.exp(-(((ys_s - img_h / H) ** 2) / 0.02)) * glow * 0.55
            glow_col = np.array(colors["bright"], np.float32)[None, None, :]
            screened = 1.0 - (1.0 - canvas) * (1.0 - glow_col)
            m = (band[:, None] * layer[..., 3])[..., None].astype(np.float32)
            canvas = canvas * (1 - m) + screened * m
        canvas = _alpha_over(canvas, layer, 0, 0)

        ys_col = np.where(layer[..., 3].max(axis=1) > 0.3)[0]
        subject_extra = {
            "bg_mode": bg_mode,
            "subject_source_px": [bbox[2] - bbox[0], bbox[3] - bbox[1]],
            "subject_scale": round(float(subject_scale), 3),
            "subject_top_px": int(ys_col.min()) if ys_col.size else -1,
            "subject_bottom_px": int(ys_col.max()) if ys_col.size else -1,
            "subject_crosses_seam": bool(
                ys_col.size and ys_col.min() < img_h < ys_col.max()
            ),
        }

    # ---- 接缝处理 ---------------------------------------------------------
    # seam_fade：图像底边真正淡出（alpha 渐变），露出下方的延伸区。
    #   比"颜色混合"更自然 —— 图像自己的颜色和细节被保留到最后一刻，
    #   是"溶解过去"而不是"被染成背景色"。
    # seam_feather：没有用淡出时的退路，做双向颜色渗透。
    #
    # blur 模式下把淡化带拉长（按 blur_fade 放大），因为模糊背景本身信息量低，
    # 图像直接切上去会显得"贴了张纸"；给一段更长的渐弱区才融得进去。
    if mode == "blur" and blur_fade > 0:
        seam_fade = max(seam_fade, blur_fade)
        seam_blur = max(seam_blur, blur_fade * 0.6)

    if seam_fade > 0 and mode != "subject":
        fpx = max(2, int(round(H * seam_fade)))
        top = max(0, img_h - fpx)
        n = img_h - top
        if n > 0:
            ys_f = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None, None]
            # 图像 alpha：顶部 1 → 接缝处 0，用 smoothstep 保证两端切线平滑
            alpha = 1.0 - smoothstep(0.0, 1.0, ys_f)
            alpha = alpha ** 1.15

            band = canvas[top:img_h].copy()

            # 淡化带先做渐强模糊：越靠近接缝越糊。
            # 没有这一步的话，图像以最锐利的状态突然消失，接缝处会有
            # 一种"被刀切掉"的生硬感；加了之后是"化开"。
            if seam_blur > 0:
                r = max(1.0, fpx * seam_blur * 1.6)
                blurred = _f(np.asarray(
                    Image.fromarray(_u8(band), "RGB").filter(ImageFilter.GaussianBlur(radius=r))
                ))
                mix = (smoothstep(0.0, 1.0, ys_f) ** 1.3) * 1.0
                band = band * (1 - mix) + blurred * mix

            # 淡出露出的底色。
            #
            # 这里有个踩过的坑：曾经为了消除"泛白发雾"，把底色亮度压到
            # 原图基调附近，结果接缝处出现 0.14 的亮度跳变 —— 因为底色
            # 不再等于扩展区首行，两段各算各的。
            #
            # 正确做法是底色必须取自扩展区首行 ext[0]，接缝才无缝。
            # "泛白"的真正原因不是取 ext[0]，而是当时的 ext[0] 本身偏亮
            # （没做亮度锚定）；现在亮度锚定 + A3 对齐之后，
            # ext[0] 已经和图像尾行吻合，直接用它既无缝也不泛白。
            #
            # fade_backdrop_mix：0=完全用 ext[0]（无缝，默认），
            #                    1=完全用基调亮度（旧行为，会产生跳变）
            hue_ref = ext[0:1]
            mix = float(np.clip(p["fade_backdrop_mix"], 0.0, 1.0))
            if mix < 1.0:
                base_ref = hue_ref
            want = max(0.04, float(tone) * p["fade_backdrop_ratio"] if tone is not None else 0.20)
            cur1 = float(0.2126 * hue_ref[..., 0].mean()
                         + 0.7152 * hue_ref[..., 1].mean()
                         + 0.0722 * hue_ref[..., 2].mean())
            tone_ref = np.clip(hue_ref * (want / max(cur1, 1e-4)), 0, 1)
            backdrop_src = hue_ref * (1.0 - mix) + tone_ref * mix
            backdrop = np.repeat(backdrop_src, n, axis=0)
            # 越靠近图像侧越暗一点，避免出现一条比两边都亮的带
            lift = (p["fade_lift_from"] + (1.0 - p["fade_lift_from"]) * ys_f)[..., 0:1]
            backdrop = np.clip(backdrop * lift, 0, 1)

            canvas[top:img_h] = band * alpha + backdrop * (1.0 - alpha)

    elif seam_feather > 0 and mode != "subject":
        fpx = max(2, int(round(H * seam_feather)))
        top = max(0, img_h - fpx)
        n = img_h - top
        band = canvas[top:img_h].copy()
        ys = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None, None]
        # 上：图像区底部，最多吸收 55% 的延伸色
        a_up = (smoothstep(0.0, 1.0, ys) ** 1.6) * 0.55
        canvas[top:img_h] = band * (1 - a_up) + ext[:n] * a_up
        # 下：延伸区顶部，从图像区底部色过渡进渐变
        m = min(ext.shape[0], n)
        if m > 0:
            below = canvas[img_h : img_h + m].copy()
            src_col = canvas[img_h - 1 : img_h]  # 上侧最后一行的实际颜色
            b_up = (1.0 - smoothstep(0.0, 1.0, np.linspace(0.0, 1.0, m, dtype=np.float32))[:, None, None]) ** 1.6 * 0.45
            canvas[img_h : img_h + m] = below * (1 - b_up) + src_col * b_up

    if blur_edge > 0:
        fpx = max(2, int(round(H * blur_edge)))
        top = max(0, img_h - fpx // 2)
        bottom = min(H, img_h + fpx)
        seg = canvas[top:bottom]
        blurred = _f(np.asarray(
            Image.fromarray(_u8(seg), "RGB").filter(ImageFilter.GaussianBlur(radius=max(1.0, fpx / 3.0)))
        ))
        ys = np.linspace(0.0, 1.0, seg.shape[0], dtype=np.float32)[:, None, None]
        m = np.exp(-((ys - 0.5) ** 2) / 0.08)
        canvas[top:bottom] = seg * (1 - m) + blurred * m

    # 【A3 边界对齐】让接缝两侧在交界处亮度相等。
    # 必须放在所有改变图像区/扩展区颜色的步骤之后、颗粒之前，
    # 这样对齐用的是两者的最终真实亮度。
    if seam_align > 0 and mode != "subject":
        n_ctx = max(4, int(round(H * 0.025)))
        i_lo = max(0, img_h - n_ctx)
        e_hi = min(H, img_h + n_ctx)
        if i_lo < img_h < e_hi:
            canvas[i_lo:img_h], canvas[img_h:e_hi] = align_seam_luma(
                canvas[i_lo:img_h], canvas[img_h:e_hi], strength=seam_align
            )

    # 【B 跨接缝过渡带】可选：让接缝从一条线变成一条有宽度的带。
    # 放在 A3 之后，并在混合完成后重新对齐一次 —— 混合会再次引入
    # 两侧均值差，不重新对齐的话接缝跳变会被拉回来。
    if seam_blend > 0 and mode != "subject":
        canvas = blend_across_seam(
            canvas, img_h,
            above=max(4, int(round(H * seam_blend))),
            below=max(4, int(round(H * seam_blend))),
            strength=p["seam_blend_strength"],
        )
        if seam_align > 0:
            n_ctx = max(4, int(round(H * 0.025)))
            i_lo = max(0, img_h - n_ctx)
            e_hi = min(H, img_h + n_ctx)
            if i_lo < img_h < e_hi:
                canvas[i_lo:img_h], canvas[img_h:e_hi] = align_seam_luma(
                    canvas[i_lo:img_h], canvas[img_h:e_hi], strength=seam_align
                )

    # 【D 统一颗粒】整幅图共享一层颗粒。
    # 之前只给扩展区加颗粒，图像区干净，接缝处颗粒有无又是一条断层。
    canvas = add_global_grain(canvas, grain, seed)

    out = Image.fromarray(_u8(np.clip(canvas, 0, 1)), "RGB")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    out.save(out_path, quality=95)

    return {
        "ok": True,
        "output": os.path.abspath(out_path),
        "width": W,
        "height": H,
        "mode": mode,
        "anchor": round(anchor, 4),
        "image_region_px": img_h,
        "extension_px": ext_h,
        "palette": {k: [round(v, 4) for v in colors[k]] for k in colors},
        "seam_color": [round(v, 4) for v in seam],
        "source_tone": round(float(tone), 4),
        "post": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in (post or default_post()).items()},
        **subject_extra,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="氢桌面风格壁纸：取代表色 + 向下延伸渐变（可选抠主体）")
    p.add_argument("input", help="源图路径")
    p.add_argument("-o", "--output", default=None, help="输出路径（默认 <输入名>_wall.png）")
    p.add_argument("--ratio", default="9:16", help="宽高比，如 9:16 / 16:9 / 3:4（默认 9:16）")
    p.add_argument("--width", type=int, default=None, help="输出宽")
    p.add_argument("--height", type=int, default=None, help="输出高")
    p.add_argument("--long-edge", type=int, default=1920, help="未指定分辨率时长边像素")
    p.add_argument("--anchor", type=float, default=0.33, help="图像区占画布高的比例（默认 0.33）")
    p.add_argument("--mode", choices=["extend", "subject", "blur"], default="extend",
                   help="extend=取色延展 / subject=抠主体 / blur=放大模糊填充")
    p.add_argument("--blur-mix", type=float, default=0.0,
                   help="模糊背景占延伸区的比例（0=纯取色延展，1=纯模糊；extend 模式下也可用）")
    p.add_argument("--blur-radius", type=float, default=6.0,
                   help="模糊强度（相对延伸区高度的百分比，默认 6.0，越大越糊）")
    p.add_argument("--blur-scale", type=float, default=1.35,
                   help="放大倍数（先放大再模糊，默认 1.35）")
    p.add_argument("--blur-streak", type=float, default=0.0,
                   help="竖向拉丝强度 0..1（模糊背景常见的氛围处理）")
    p.add_argument("--blur-fade", type=float, default=0.22,
                   help="blur 模式下图像与模糊背景之间的渐弱区高度占画布比例（默认 0.22）")
    p.add_argument("--focus-x", type=float, default=0.5)
    p.add_argument("--focus-y", type=float, default=0.5)
    p.add_argument("--bg-mode", choices=["gradient", "keep"], default="gradient")
    p.add_argument("--subject-scale", type=float, default=1.0, help="主体高度相对图像区的倍数")
    p.add_argument("--subject-overflow", type=float, default=0.14, help="主体越过接缝、占延伸区高度的比例")
    p.add_argument("--subject-bottom", type=float, default=0.0, help="主体底边的绝对位置（占画布高），给值后忽略 overflow")
    p.add_argument("--subject-model", default="u2net", help="u2net / isnet-general-use / birefnet-general")
    p.add_argument("--glow", type=float, default=0.26, help="高光强度 0..1")
    p.add_argument("--smear-weight", type=float, default=0.72,
                   help="接缝色向下抹匀的权重 0..1（越高越接近单色渐变，越低越像图在用色继续）")
    p.add_argument("--grain", type=float, default=0.008)
    p.add_argument("--vignette", dest="vig", type=float, default=0.18)
    p.add_argument("--scrim", type=float, default=0.20, help="底部压暗（dock 可读性）")
    p.add_argument("--seam-feather", type=float, default=0.040, help="接缝羽化高度占画布比例")
    p.add_argument("--seam-fade", type=float, default=0.10,
                   help="接缝淡化高度占画布比例（默认 0.10；0=关闭淡化，改走颜色混合）")
    p.add_argument("--seam-blur", type=float, default=0.030,
                   help="接缝淡化带内的渐强模糊强度（默认 0.03；0=关闭）")
    p.add_argument("--seam-align", type=float, default=1.0,
                   help="接缝亮度对齐强度（默认 1.0；0=关闭）。消除接缝两侧的亮度硬跳")
    p.add_argument("--seam-blend", type=float, default=0.0,
                   help="跨接缝过渡带宽度占画布比例（默认 0=关闭）；>0 时接缝变成有宽度的带")
    p.add_argument("--stitch", type=float, default=0.85,
                   help="纹理缝合强度（默认 0.85；0=关闭）。把图像的雨丝/噪点残差缝进扩展区")
    p.add_argument("--blur-edge", type=float, default=0.0, help="接缝局部模糊半径占画布比例")
    p.add_argument("--tint-strength", type=float, default=0.30)
    p.add_argument("--deep-gain", type=float, default=0.42)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", action="store_true", help="只输出 JSON 报告")

    # 后处理细项：每一项都可通过 --post-<name> 覆盖
    grp = p.add_argument_group("后处理细项（--post-*，不填用默认值）")
    for key, val in POST_DEFAULTS.items():
        grp.add_argument(
            f"--post-{key.replace('_', '-')}",
            dest=f"post_{key}",
            type=type(val),
            default=None,
            help=f"{POST_DOC.get(key, key)}（默认 {val}）",
        )
    return p


def collect_post(args) -> dict:
    """把 CLI 上的 --post-* 覆盖项合并进默认字典。"""
    post = default_post()
    for key in POST_DEFAULTS:
        v = getattr(args, f"post_{key}", None)
        if v is not None:
            post[key] = v
    return post


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out = args.output
    if not out:
        stem, _ = os.path.splitext(os.path.abspath(args.input))
        out = f"{stem}_wall.png"
    try:
        rep = make_wallpaper(
            args.input, out,
            ratio=parse_ratio(args.ratio),
            width=args.width, height=args.height, long_edge=args.long_edge,
            anchor=args.anchor, mode=args.mode,
            focus_x=args.focus_x, focus_y=args.focus_y,
            bg_mode=args.bg_mode,
            subject_scale=args.subject_scale,
            subject_overflow=args.subject_overflow,
            subject_bottom=args.subject_bottom,
            subject_model=args.subject_model,
            glow=args.glow, smear_weight=args.smear_weight,
            grain=args.grain, vig=args.vig, scrim=args.scrim,
            seam_feather=args.seam_feather, seam_fade=args.seam_fade,
            seam_blur=args.seam_blur, seam_align=args.seam_align,
            seam_blend=args.seam_blend, stitch=args.stitch,
            blur_edge=args.blur_edge,
            blur_mix=args.blur_mix, blur_radius=args.blur_radius,
            blur_scale=args.blur_scale, blur_streak=args.blur_streak,
            blur_fade=args.blur_fade,
            tint_strength=args.tint_strength, deep_gain=args.deep_gain,
            seed=args.seed, post=collect_post(args),
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(
            "OK %s  %dx%d  mode=%s  anchor=%.2f  image=%dpx  ext=%dpx"
            % (rep["output"], rep["width"], rep["height"], rep["mode"],
               rep["anchor"], rep["image_region_px"], rep["extension_px"])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
