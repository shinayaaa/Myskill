# -*- coding: utf-8 -*-
"""
make_demo_sources.py — 程序化生成 demo 源图（100% 原创，无第三方版权）。

开源仓库里的示例图不能拿别人的插画，所以这些源图全部用 numpy + Pillow
画出来：渐变天空、光斑、剪影、几何主体。它们只有一个用途——
让 README 里的效果图能自证「这个引擎真的把一个东西往下延伸了」。

    python assets/generate_demo_sources.py --out assets/src
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

RNG = np.random.default_rng(20261117)


def nh():
    return np.random.default_rng(11)


def vgrad(h, w, stops):
    """竖直多段渐变。stops = [(位置0..1, (r,g,b)), ...]"""
    ys = np.linspace(0, 1, h, dtype=np.float32)
    out = np.zeros((h, w, 3), np.float32)
    pos = [s[0] for s in stops]
    for c in range(3):
        vals = np.array([s[1][c] for s in stops], np.float32)
        out[..., c] = np.interp(ys, pos, vals)[:, None]
    return out


def add_glow(img, cx, cy, rx, ry, color, strength):
    h, w, _ = img.shape
    yy = (np.linspace(0, 1, h, dtype=np.float32)[:, None] - cy) / max(ry, 1e-6)
    xx = (np.linspace(0, 1, w, dtype=np.float32)[None, :] - cx) / max(rx, 1e-6)
    d = np.sqrt(xx ** 2 + yy ** 2)
    m = np.clip(1.0 - d, 0, 1) ** 2 * strength
    col = np.array(color, np.float32)[None, None, :]
    return np.clip(img * (1 - m[..., None]) + (1 - (1 - img) * (1 - col)) * m[..., None], 0, 1)


def bokeh(img, n, ymin, ymax, rmin, rmax, colors, alpha=0.25):
    h, w, _ = img.shape
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB").convert("RGBA")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(n):
        r = int(RNG.uniform(rmin, rmax))
        x = int(RNG.uniform(-r, w + r))
        y = int(RNG.uniform(h * ymin, h * ymax))
        c = colors[RNG.integers(0, len(colors))]
        a = int(255 * alpha * RNG.uniform(0.5, 1.4))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*c, max(10, min(200, a))))
    layer = layer.filter(ImageFilter.GaussianBlur(max(1.0, w / 300)))
    out = Image.alpha_composite(pil, layer).convert("RGB")
    return np.asarray(out).astype(np.float32) / 255.0


def stars(img, n, ymax=0.6):
    h, w, _ = img.shape
    for _ in range(n):
        x = int(RNG.uniform(0, w))
        y = int(RNG.uniform(0, h * ymax))
        b = RNG.uniform(0.4, 1.0)
        r = max(0, int(RNG.uniform(0, 1.6)))
        img[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] += b * 0.35
    return np.clip(img, 0, 1)


def grain(img, amt=0.004):
    n = RNG.normal(0, amt, img.shape).astype(np.float32)
    return np.clip(img + n, 0, 1)


def save(img, path, w=None):
    """统一存 JPEG —— 这些源图要进 git，PNG 会有 1MB+ 一张。"""
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB")
    if w:
        h = int(round(pil.height * w / pil.width))
        pil = pil.resize((w, h), Image.LANCZOS)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    pil.save(path, quality=92, optimize=True)
    kb = os.path.getsize(path) / 1024
    print(f"  {path}  {pil.width}x{pil.height}  {kb:.0f} KB")


# --------------------------------------------------------------------------
# 1. 夜景城市（横图，配 extend / blur）
# --------------------------------------------------------------------------
def demo_city(path):
    W, H = 1600, 1000
    img = vgrad(H, W, [
        (0.00, (0.03, 0.05, 0.12)),
        (0.45, (0.08, 0.12, 0.26)),
        (0.72, (0.22, 0.26, 0.42)),
        (0.88, (0.42, 0.30, 0.30)),
        (1.00, (0.55, 0.33, 0.26)),
    ])
    img = add_glow(img, 0.62, 0.90, 0.55, 0.35, (1.0, 0.72, 0.42), 0.55)

    # 远处天际线剪影
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(pil)
    base = int(H * 0.74)
    x = 0
    heights = []
    while x < W:
        bw = int(RNG.uniform(45, 130))
        bh = int(RNG.uniform(40, 230))
        d.rectangle([x, base - bh, x + bw, H], fill=(14, 18, 32))
        heights.append((x, x + bw, base - bh))
        x += bw + int(RNG.uniform(4, 22))
    # 窗灯
    for (x0, x1, ytop) in heights:
        for _ in range(int((x1 - x0) * (H - ytop) / 900)):
            wx = int(RNG.uniform(x0 + 3, max(x0 + 4, x1 - 3)))
            wy = int(RNG.uniform(ytop + 6, H - 8))
            c = (255, 214, 150) if RNG.random() < 0.75 else (150, 210, 255)
            d.rectangle([wx, wy, wx + 2, wy + 3], fill=c)
    img = np.asarray(pil).astype(np.float32) / 255.0

    img = bokeh(img, 90, 0.35, 1.0, 8, 46, [(255, 205, 140), (255, 160, 90), (170, 210, 255)], 0.18)
    img = stars(img, 260, 0.5)
    img = grain(img)
    save(img, path, 1400)


# --------------------------------------------------------------------------
# 2. 暖色黄昏海面（横图，配 extend）
# --------------------------------------------------------------------------
def demo_sunset(path):
    W, H = 1600, 1000
    img = vgrad(H, W, [
        (0.00, (0.30, 0.24, 0.52)),
        (0.28, (0.62, 0.36, 0.46)),
        (0.52, (0.95, 0.55, 0.36)),
        (0.66, (1.00, 0.72, 0.44)),
        (0.74, (0.42, 0.34, 0.44)),
        (1.00, (0.12, 0.14, 0.28)),
    ])
    # 日轮
    h, w = H, W
    cy, cx = int(h * 0.63), int(w * 0.5)
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((xx - cx) ** 2 + ((yy - cy) * 1.35) ** 2)
    disc = np.clip(1.0 - (r - 62) / 26.0, 0, 1)[..., None]
    img = np.clip(img * (1 - disc) + np.array([1.0, 0.93, 0.74], np.float32)[None, None, :] * disc, 0, 1)
    img = add_glow(img, 0.5, 0.62, 0.42, 0.28, (1.0, 0.82, 0.55), 0.5)

    # 海面横向亮带（水波反光）
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB")
    d = ImageDraw.Draw(pil, "RGBA")
    for i in range(120):
        yy_ = RNG.uniform(0.66, 1.0)
        y = int(H * yy_)
        spread = (yy_ - 0.66) * 1.6
        half = int(W * (0.03 + spread * 0.35) * RNG.uniform(0.3, 1.0))
        xc = int(cx + RNG.normal(0, W * 0.05))
        a = int(110 * (1 - (yy_ - 0.66) / 0.34) * RNG.uniform(0.3, 1.0))
        d.line([xc - half, y, xc + half, y], fill=(255, 226, 180, max(0, a)), width=int(RNG.uniform(1, 4)))
    img = np.asarray(pil).astype(np.float32) / 255.0
    img = bokeh(img, 40, 0.6, 1.0, 6, 24, [(255, 220, 170)], 0.12)
    img = grain(img)
    save(img, path, 1400)


# --------------------------------------------------------------------------
# 3. 白底主体（配 subject 抠图）
# --------------------------------------------------------------------------
def demo_subject(path):
    W, H = 1200, 1200
    img = np.ones((H, W, 3), np.float32) * 0.97
    pil = Image.fromarray((img * 255).astype(np.uint8), "RGB").convert("RGBA")
    d = ImageDraw.Draw(pil)

    cx, cy = W // 2, int(H * 0.52)
    body = (58, 92, 150)
    light = (96, 140, 205)

    # 一只圆润的小鲸鱼：主体轮廓清晰，方便 rembg 抠。
    #
    # 尾巴一次画成**单一实心多边形**，并且和身体大幅重叠（起点 x 落在椭圆内部）。
    # 试过两次都不行，记在这里免得再踩：
    #   1) 两个三角形之间留楔形空白 -> u2net 判成背景，尾巴中间一个洞
    #   2) 补一块桥接多边形 -> 凹口是**开口**的，cleanup_alpha 只填封闭空洞，仍然漏
    # 结论：对 rembg 来说，主体最好是"单连通、无凹口"的形状。
    # 真实摄影素材不满足这条也能用（u2net 认得语义对象），但 demo 素材
    # 是纯几何色块，模型只能靠轮廓判断，所以必须做得干净。
    d.polygon([
        (cx + 120, cy + 20),      # 根部深埋进椭圆，保证连通
        (cx + 372, cy - 34),      # 尾鳍上尖
        (cx + 356, cy + 92),      # 尾鳍下尖
        (cx + 126, cy + 120),
    ], fill=body)
    d.ellipse([cx - 250, cy - 150, cx + 250, cy + 190], fill=body)
    d.ellipse([cx - 210, cy - 60, cx + 120, cy + 130], fill=light)
    # 背鳍：底边整条压在椭圆上，同样不留凹口
    d.polygon([(cx + 60, cy - 130), (cx + 232, cy - 196), (cx + 250, cy - 96)], fill=light)
    # 眼睛
    d.ellipse([cx - 150, cy - 60, cx - 118, cy - 24], fill=(255, 255, 255))
    d.ellipse([cx - 142, cy - 52, cx - 124, cy - 32], fill=(24, 32, 48))
    # 水花
    for i, x in enumerate(range(cx - 300, cx + 340, 40)):
        r = 14 + (i % 3) * 7
        d.ellipse([x - r, cy + 200 - r, x + r, cy + 200 + r], fill=(150, 200, 245))
    # 头顶小水柱
    d.ellipse([cx - 20, cy - 230, cx + 20, cy - 190], fill=(180, 220, 250))
    d.ellipse([cx - 40, cy - 280, cx - 4, cy - 240], fill=(200, 230, 250))

    img = np.asarray(pil.convert("RGB")).astype(np.float32) / 255.0
    img = grain(img, 0.003)
    save(img, path, 1000)


# --------------------------------------------------------------------------
# 4/5. 拼接用的上下两张
# --------------------------------------------------------------------------
def demo_splice_top(path):
    W, H = 1600, 1100
    img = vgrad(H, W, [
        (0.00, (0.05, 0.08, 0.20)),
        (0.38, (0.14, 0.22, 0.44)),
        (0.68, (0.34, 0.42, 0.66)),
        (0.86, (0.60, 0.64, 0.80)),
        (1.00, (0.16, 0.22, 0.40)),
    ])
    img = add_glow(img, 0.5, 0.05, 0.7, 0.5, (0.85, 0.92, 1.0), 0.5)
    # 一轮满月 + 云带
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8), "RGB").convert("RGBA")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    cx, cy, r = int(W * 0.66), int(H * 0.30), 96
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 252, 240, 255))
    lay = lay.filter(ImageFilter.GaussianBlur(1.2))
    pil = Image.alpha_composite(pil, lay)
    d = ImageDraw.Draw(pil, "RGBA")
    for i in range(14):
        y = int(H * RNG.uniform(0.2, 0.72))
        hgt = int(RNG.uniform(18, 60))
        x0 = int(RNG.uniform(-200, W * 0.6))
        d.ellipse([x0, y, x0 + int(RNG.uniform(400, 1100)), y + hgt],
                  fill=(200, 214, 238, int(RNG.uniform(30, 70))))
    pil = pil.filter(ImageFilter.GaussianBlur(9))

    # 底部山脊剪影：给拼接一个自然的"图的下沿"，接缝才站得住
    d = ImageDraw.Draw(pil)
    ridge = []
    x = 0
    while x <= W:
        ridge.append((x, int(H * (0.80 + 0.10 * np.sin(x / 190.0 + 1.1) + RNG.uniform(-0.02, 0.02)))))
        x += 24
    d.polygon(ridge + [(W, H), (0, H)], fill=(10, 14, 28))

    img = np.asarray(pil.convert("RGB")).astype(np.float32) / 255.0
    img = stars(img, 300, 0.72)
    img = grain(img)
    save(img, path, 1400)


def demo_splice_bottom(path):
    W, H = 1000, 2000
    img = vgrad(H, W, [
        (0.00, (0.04, 0.07, 0.18)),
        (0.40, (0.07, 0.13, 0.30)),
        (0.68, (0.10, 0.20, 0.42)),
        (0.86, (0.16, 0.30, 0.55)),
        (1.00, (0.30, 0.46, 0.72)),
    ])
    img = stars(img, 420, 0.55)
    # 底部一片雾状花丛般的浅色团块
    for i in range(70):
        img = add_glow(
            img,
            RNG.uniform(0, 1), RNG.uniform(0.78, 1.02),
            RNG.uniform(0.05, 0.16), RNG.uniform(0.03, 0.09),
            (0.72, 0.84, 0.98), RNG.uniform(0.15, 0.45),
        )
    img = bokeh(img, 70, 0.35, 1.0, 10, 40, [(190, 215, 250), (240, 245, 255)], 0.14)
    img = grain(img)
    save(img, path, 900)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="assets/src")
    a = p.parse_args()

    print("生成 demo 源图：")
    demo_city(os.path.join(a.out, "city_night.png"))
    demo_sunset(os.path.join(a.out, "sunset_sea.png"))
    demo_subject(os.path.join(a.out, "whale_white.png"))
    demo_splice_top(os.path.join(a.out, "moon_top.png"))
    demo_splice_bottom(os.path.join(a.out, "moon_bottom.png"))
    print("完成。")


if __name__ == "__main__":
    main()
