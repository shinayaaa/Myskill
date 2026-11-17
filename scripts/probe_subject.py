#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_subject.py — 量出图里主体、光斑、裙摆之类的边界行号。

裁切特写之前必须先量，不能靠目测：主体上缘、光斑范围、裙摆最宽处
和末端行号决定了裁切框，而裁切框又决定 anchor，全靠猜会反复返工。

用法：
    python probe_subject.py <图片> [--region L,T,R,B]

输出：各特征的行/列范围，以及推荐的裁切框。
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
from PIL import Image


def _f(a):
    return np.asarray(a, dtype=np.float32) / 255.0


def load(path):
    return _f(np.asarray(Image.open(path).convert("RGB")))


def luma_of(img):
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def sat_of(img):
    return img.max(axis=2) - img.min(axis=2)


def brightness_clusters(img, region=None, thr=0.95, sat_max=0.15, min_px=200,
                        max_aspect=2.2):
    """
    找极亮低饱和的「圆润紧凑块」（光斑/灯泡/月亮）。

    踩过的坑：光靠颜色分不开光斑和白色线稿人像 —— 两者都是「极亮 + 低饱和」，
    而且光斑的辉光和白色躯干在亮度上连成一片，连通块会合并，
    量出来的行范围能从光斑一直跨到腰腹。

    所以这里不猜整张图。要么用 --glow-box 显式给光斑的大致范围，
    要么脚本只接受「圆度足够」的块：面积 / 外接框面积 > min_fill。
    人像躯干是竖向长条，圆度必然很低。
    """
    lum = luma_of(img)
    sat = sat_of(img)
    m = (lum > thr) & (sat < sat_max)
    if region:
        l, t, r, b = region
        mask = np.zeros_like(m)
        mask[t:b, l:r] = True
        m &= mask
    if m.sum() < min_px:
        return None

    comps = _components(m, min_px=min_px)
    blobs = []
    for (y0, y1, x0, x1, n) in comps:
        cw, ch = (x1 - x0), (y1 - y0)
        if cw < 3 or ch < 3:
            continue
        aspect = max(cw / ch, ch / cw)
        if aspect > max_aspect:
            continue
        box_area = cw * ch
        fill = n / max(box_area, 1)
        # 光斑是发光体，边缘必然渐变，填充率天然很低（实测 0.12~0.17）。
        # 门槛定太高会把光斑筛掉，定太低又会放进稀疏的辉光和线稿。
        if fill < 0.06:
            continue
        blobs.append({
            "count": int(n),
            "rows": (int(y0), int(y1 - 1)),
            "cols": (int(x0), int(x1 - 1)),
            "center": (int((x0 + x1) / 2), int((y0 + y1) / 2)),
            "aspect": round(float(aspect), 2),
            "fill": round(float(fill), 2),
        })
    if not blobs:
        return None
    return max(blobs, key=lambda d: d["count"])


def _components(mask, min_px=200):
    """4 邻域连通块，返回 [(y0,y1,x0,x1,count)]。降采样找区域再回原尺度精算。"""
    from collections import deque

    small = mask[::4, ::4]
    sh, sw = small.shape
    seen = np.zeros((sh, sw), dtype=bool)
    out = []
    ys, xs = np.nonzero(small)
    if not ys.size:
        return out

    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx] or not small[sy, sx]:
            continue
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        ys_l, xs_l = [], []
        while q:
            cy, cx = q.popleft()
            ys_l.append(cy)
            xs_l.append(cx)
            for ny, nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        y0 = max(0, min(ys_l) * 4)
        y1 = min(mask.shape[0], (max(ys_l) + 1) * 4)
        x0 = max(0, min(xs_l) * 4)
        x1 = min(mask.shape[1], (max(xs_l) + 1) * 4)
        n = int(mask[y0:y1, x0:x1].sum())
        if n >= min_px:
            out.append((y0, y1, x0, x1, n))
    return out


def _largest_component(mask):
    comps = _components(mask, min_px=1)
    if not comps:
        return mask
    y0, y1, x0, x1, _ = max(comps, key=lambda t: t[4])
    out = np.zeros_like(mask)
    out[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    return out


def _largest_component(mask):
    """4 邻域最大连通块。纯 numpy + 队列，不引入 scipy。"""
    from collections import deque

    h, w = mask.shape
    if mask.sum() == 0:
        return mask
    # 先降采样加速：先在 1/4 尺度找大致区域，再回原尺度取并集
    small = mask[::4, ::4]
    sh, sw = small.shape
    seen = np.zeros_like(small, dtype=bool)
    best = []
    ys, xs = np.nonzero(small)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx] or not small[sy, sx]:
            continue
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        comp = []
        while q:
            cy, cx = q.popleft()
            comp.append((cy, cx))
            for ny, nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(comp) > len(best):
            best = comp
    if not best:
        return mask
    ys2 = np.array([c[0] for c in best]) * 4
    xs2 = np.array([c[1] for c in best]) * 4
    y0, y1 = ys2.min(), min(mask.shape[0], ys2.max() + 4)
    x0, x1 = xs2.min(), min(mask.shape[1], xs2.max() + 4)
    out = np.zeros_like(mask)
    out[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    return out


def lineart_rows(img, region=None, lum_min=0.70, sat_max=0.30, min_per_row=8):
    """找白色线稿类主体，逐行统计，返回行范围与密度曲线。"""
    lum = luma_of(img)
    sat = sat_of(img)
    m = (lum > lum_min) & (sat < sat_max)
    if region:
        l, t, r, b = region
        mask = np.zeros_like(m)
        mask[t:b, l:r] = True
        m &= mask

    rows = m.sum(axis=1)
    cols = m.sum(axis=0)
    nz_rows = np.nonzero(rows > min_per_row)[0]
    nz_cols = np.nonzero(cols > min_per_row)[0]
    if not nz_rows.size:
        return None

    return {
        "count": int(m.sum()),
        "rows": (int(nz_rows.min()), int(nz_rows.max())),
        "cols": (int(nz_cols.min()), int(nz_cols.max())),
        "row_density": rows,
    }


def widest_band(img, region, top, bottom, step=50, lum_min=0.70, sat_max=0.30):
    """在指定行区间内找水平跨度最大的那一段（裙摆/披风/翅膀）。"""
    lum = luma_of(img)
    sat = sat_of(img)
    l, _, r, _ = region
    m = (lum > lum_min) & (sat < sat_max)
    out = []
    for y0 in range(top, min(bottom, img.shape[0]), step):
        y1 = min(img.shape[0], y0 + step)
        seg = m[y0:y1, l:r]
        cols = np.nonzero(seg.sum(axis=0) > 1)[0]
        if cols.size < 2:
            continue
        out.append({
            "rows": (y0, y1),
            "cols": (int(cols.min()) + l, int(cols.max()) + l),
            "span": int(cols.max() - cols.min()),
        })
    return out


def recommend_box(features, margin_top=80, margin_bottom=100, margin_x=40):
    """把各特征的外接并集 + 留白，合成推荐的裁切框。"""
    rows_lo, rows_hi = [], []
    cols_lo, cols_hi = [], []
    for f in features:
        if not f:
            continue
        rows_lo.append(f["rows"][0])
        rows_hi.append(f["rows"][1])
        cols_lo.append(f["cols"][0])
        cols_hi.append(f["cols"][1])
    if not rows_lo:
        return None
    return (
        max(0, min(cols_lo) - margin_x),
        max(0, min(rows_lo) - margin_top),
        max(cols_hi) + margin_x,
        max(rows_hi) + margin_bottom,
    )


def anchor_for(box, canvas_w, canvas_h, target_h_px=None):
    """
    裁切框 → anchor 的换算。

    引擎是 object-fit: cover：裁片等比放大到铺满 画布宽 × (画布高 × anchor)，
    再按焦点裁掉多余部分。所以真正决定"能否装下"的判据是：

        需要的高度 = anchor × canvas_h
        裁片放大到宽度铺满时的实际高度 = canvas_w / (crop_w / crop_h)

    前者 ≥ 后者时，纵向不会裁掉任何东西，只裁左右。
    """
    l, t, r, b = box
    cw, ch = r - l, b - t
    crop_ar = cw / ch
    canvas_ar = canvas_w / canvas_h

    if target_h_px is not None:
        return target_h_px / canvas_h

    # 刚好不裁纵向所需的最小 anchor
    scaled_h = canvas_w / crop_ar
    return scaled_h / canvas_h


def main(argv=None):
    p = argparse.ArgumentParser(description="量主体/光斑/裙摆边界，推荐裁切框")
    p.add_argument("image")
    p.add_argument("--region", default=None, help="限定区域 L,T,R,B，如 470,120,1400,1750")
    p.add_argument("--glow-box", default=None,
                   help="光斑大致范围 L,T,R,B。强烈建议给 —— 不给会靠圆度盲猜，容易误判")
    p.add_argument("--glow-thr", type=float, default=0.85)
    p.add_argument("--line-thr", type=float, default=0.70)
    p.add_argument("--canvas", default="1280x2772", help="目标画布，用于算 anchor")
    p.add_argument("--step", type=int, default=50)
    args = p.parse_args(argv)

    img = load(args.image)
    h, w = img.shape[:2]
    print(f"图片 {w}x{h}")

    region = None
    if args.region:
        region = tuple(int(v) for v in args.region.split(","))
        print(f"限定区域 {region}")

    feats = []

    glow = brightness_clusters(img,
                               tuple(int(v) for v in args.glow_box.split(",")) if args.glow_box else region,
                               thr=args.glow_thr, sat_max=0.22)
    if glow:
        print(f"\n[光斑] {glow['count']} px  行 {glow['rows'][0]}..{glow['rows'][1]}  "
              f"列 {glow['cols'][0]}..{glow['cols'][1]}  中心 {glow['center']}  "
              f"长宽比 {glow['aspect']} 填充率 {glow['fill']}")
        feats.append(glow)
    else:
        print("\n[光斑] 未检出。用 --glow-box 指定大致范围，或调低 --glow-thr")

    line = lineart_rows(img, region, lum_min=args.line_thr, sat_max=0.30)
    if line:
        print(f"\n[线稿主体] {line['count']} px  行 {line['rows'][0]}..{line['rows'][1]}  "
              f"列 {line['cols'][0]}..{line['cols'][1]}")
        feats.append(line)

        print("\n[逐段密度] 找裙摆/最大宽度区")
        rows = line["row_density"]
        for y0 in range(line["rows"][0], line["rows"][1] + 1, args.step):
            y1 = min(line["rows"][1] + 1, y0 + args.step)
            n = int(rows[y0:y1].sum())
            if n <= 0:
                continue
            bar = "#" * min(60, n // 40)
            print(f"  {y0:5d}-{y1:5d}: {n:6d} {bar}")

        if region:
            wide = widest_band(img, region, line["rows"][0], line["rows"][1],
                               step=args.step, lum_min=args.line_thr)
            if wide:
                best = max(wide, key=lambda d: d["span"])
                print(f"\n[最宽段/裙摆] 行 {best['rows'][0]}..{best['rows'][1]}  "
                      f"列 {best['cols'][0]}..{best['cols'][1]}  跨度 {best['span']} px")

    box = recommend_box(feats)
    if box:
        l, t, r, b = box
        cw, ch = r - l, b - t
        print(f"\n[推荐裁切框] {l},{t} .. {r},{b}   尺寸 {cw}x{ch}  宽高比 {cw/ch:.3f}")

        cw_canvas, ch_canvas = (int(v) for v in args.canvas.lower().split("x"))
        need = anchor_for(box, cw_canvas, ch_canvas)
        print(f"\n[anchor 换算] 画布 {cw_canvas}x{ch_canvas}")
        print(f"  裁片放大到宽度铺满时高度 = {cw_canvas}/{cw/ch:.3f} = {cw_canvas/(cw/ch):.0f}px")
        print(f"  刚好不裁纵向所需的最小 anchor = {need:.3f}")
        print(f"  低于此值会裁掉上下内容；高于此值则留更多留白")
        for a in (0.33, 0.42, 0.45, 0.50, 0.55):
            avail = ch_canvas * a
            ok = "OK " if avail >= cw_canvas / (cw / ch) else "裁切"
            print(f"    anchor {a:.2f} -> 图像区 {avail:6.0f}px  {ok}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
