"""自检：确认引擎可导入、关键纯函数行为正确。不依赖网络和 rembg。"""

import sys

import numpy as np

sys.path.insert(0, r"C:\Users\shinayaa\.dsh\skills\hydro-wallpaper\scripts")

import hydro_engine as H  # noqa: E402

fails = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        fails.append(name)


print("[ratio / canvas]")
check("parse 9:16", abs(H.parse_ratio("9:16") - 0.5625) < 1e-9)
check("parse 16:9", abs(H.parse_ratio("16:9") - 16 / 9) < 1e-9)
check("parse with full-width colon", abs(H.parse_ratio("3：4") - 0.75) < 1e-9)
check("parse WxH", abs(H.parse_ratio("1080x1920") - 0.5625) < 1e-9)

check("width only -> derive height", H.resolve_canvas(9 / 16, 1440, None) == (1440, 2560))
check("height only -> derive width", H.resolve_canvas(9 / 16, None, 1920) == (1080, 1920))
check("both given -> as is", H.resolve_canvas(9 / 16, 1080, 2400) == (1080, 2400))
w, h = H.resolve_canvas(16 / 9, None, None, 1920)
check("landscape default", w == 1920 and abs(w / h - 16 / 9) < 0.01, f"{w}x{h}")

print("[color helpers]")
check("luma black", abs(H.luma((0, 0, 0))) < 1e-9)
check("luma white", abs(H.luma((1, 1, 1)) - 1.0) < 1e-6)
check("sat gray is 0", abs(H.sat((0.5, 0.5, 0.5))) < 1e-9)
check("sat red is 1", abs(H.sat((1, 0, 0)) - 1.0) < 1e-9)

v = H.vivid(np.array([0.5, 0.45, 0.4], np.float32), 1.5, 0.5)
check("vivid keeps luma target", abs(H.luma(v) - 0.5) < 0.02, f"{H.luma(v):.3f}")

print("[palette selection]")
# 大面积灰 + 小块暖色：主色必须选暖色
palette = [
    ((0.45, 0.45, 0.46), 0.55),  # 灰石板，占比最大
    ((0.72, 0.42, 0.30), 0.20),  # 红砖
    ((0.90, 0.86, 0.80), 0.25),  # 白墙
]
colors = H.choose_colors(palette, (0.45, 0.45, 0.46))
base = colors["base"]
check("gray palette -> warm base", H.sat(base) > 0.25, f"base={base} sat={H.sat(base):.2f}")

# 纯灰调图：不能崩
colors2 = H.choose_colors([((0.5, 0.5, 0.5), 1.0)], (0.5, 0.5, 0.5))
check("all-gray input survives", all(0 <= v <= 1 for v in colors2["base"]))

print("[background detection]")
white_bg = np.ones((200, 200, 3), np.float32)
white_bg[70:130, 60:140] = np.array([0.7, 0.4, 0.2], np.float32)
bgc, cov = H.dominant_background(white_bg)
check("white bg detected", cov > 0.7, f"cov={cov:.2f}")

noisy = np.random.default_rng(0).random((200, 200, 3)).astype(np.float32)
bgc2, cov2 = H.dominant_background(noisy)
check("noisy image -> low coverage", cov2 < 0.20, f"cov={cov2:.2f}")

print("[smear / gradient]")
photo = np.zeros((100, 120, 3), np.float32)
photo[..., 0] = np.linspace(0.2, 0.9, 120)[None, :]  # 横向渐变的红
photo[..., 1] = 0.4
photo[..., 2] = 0.3
col = H.column_smear(photo, 1080)
check("column_smear width", col.shape == (1080, 3), str(col.shape))
check("column_smear preserves gradient",
      float(col[:50, 0].mean()) < float(col[-50:, 0].mean()),
      "left should be darker than right")

grad = H.vertical_gradient(50, 60, [(0.0, (1, 0, 0)), (1.0, (0, 0, 1))])
check("gradient shape", grad.shape == (50, 60, 3))
check("gradient top is red", grad[0, 0, 0] > 0.9 and grad[0, 0, 2] < 0.1)
check("gradient bottom is blue", grad[-1, 0, 2] > 0.9 and grad[-1, 0, 0] < 0.1)

print("[alpha cleanup]")
a = np.zeros((100, 100), np.float32)
a[30:70, 30:70] = 1.0   # 主体
a[5:8, 5:8] = 1.0       # 噪点
a[45:50, 45:50] = 0.0   # 内部空洞
a[30:70, 30:70] = 1.0
a[45:50, 45:50] = 0.0
clean = H.cleanup_alpha(a, min_area_ratio=0.005)
check("noise removed", clean[5:8, 5:8].max() < 0.5)
check("hole filled", clean[45:50, 45:50].min() > 0.5)
check("subject kept", clean[40, 40] > 0.5)

print("[cover transform]")
scale, x0, y0 = H.cover_transform(1000, 500, 1080, 1920)
check("scale fills height", abs(scale - 1920 / 500) < 1e-6, f"{scale}")
check("crops horizontally", x0 > 0 and y0 == 0, f"x0={x0} y0={y0}")

print("[end-to-end smoke]")
import os
import tempfile
from PIL import Image

tmp = tempfile.mkdtemp()
src = os.path.join(tmp, "src.png")
arr = (np.random.default_rng(1).random((400, 600, 3)) * 255).astype(np.uint8)
arr[:120] = [200, 60, 40]
Image.fromarray(arr, "RGB").save(src)

out = os.path.join(tmp, "out.png")
rep = H.make_wallpaper(src, out, ratio=9 / 16, anchor=0.4)
check("output exists", os.path.exists(out))
check("size matches", (rep["width"], rep["height"]) == (1080, 1920))
check("anchor split correct",
      rep["image_region_px"] == 768 and rep["extension_px"] == 1152,
      f"{rep['image_region_px']}/{rep['extension_px']}")
check("palette reported", all(k in rep["palette"] for k in ("base", "accent", "deep", "bright")))

img = np.asarray(Image.open(out))
check("no all-black bottom", img[-200:].mean() > 10, f"{img[-200:].mean():.1f}")
check("has color variance", img[-400:].std() > 3, f"{img[-400:].std():.1f}")

print("[blur mode]")
out_b = os.path.join(tmp, "blur.png")
rep_b = H.make_wallpaper(src, out_b, ratio=9 / 16, anchor=0.4, mode="blur", blur_radius=6.0)
check("blur output exists", os.path.exists(out_b))
check("blur size matches", (rep_b["width"], rep_b["height"]) == (1080, 1920))

bimg = np.asarray(Image.open(out_b).convert("RGB"), np.float32) / 255.0
blum = 0.2126*bimg[...,0] + 0.7152*bimg[...,1] + 0.0722*bimg[...,2]
ih = rep_b["image_region_px"]
row = blum.mean(axis=1)
jump = abs(float(row[ih]) - float(row[ih-1]))
check("blur seam continuous", jump < 0.02, f"jump={jump:.5f}")
check("blur ext not overexposed", float((blum[ih:] > 0.90).mean()) < 0.01,
      f"{float((blum[ih:] > 0.90).mean())*100:.2f}%")

# blur 延伸区应该保留原图的色彩结构。
# 注意：这条断言只在「源图本身有色彩结构」时成立。
# 随机噪声图模糊后信息会塌缩，色块数反而减少 —— 那是图像性质，不是 bug。
# 所以这里专门造一张有结构（左暗右亮 + 一块暖色）的图来测。
print("[blur mode: 结构化源图]")
structured = os.path.join(tmp, "structured.png")
h2, w2 = 600, 900
sarr = np.zeros((h2, w2, 3), np.uint8)
sarr[:, :, 0] = np.linspace(20, 180, w2)[None, :].astype(np.uint8)
sarr[:, :, 1] = np.linspace(60, 120, h2)[:, None].astype(np.uint8)
sarr[:, :, 2] = 90
sarr[380:560, 300:620] = [230, 150, 70]        # 暖色块，模糊后应成为一团暖光
Image.fromarray(sarr, "RGB").save(structured)

out_s = os.path.join(tmp, "structured_blur.png")
rep_s = H.make_wallpaper(structured, out_s, ratio=9 / 16, anchor=0.45,
                         mode="blur", blur_radius=6.0)
ih_s = rep_s["image_region_px"]
simg = np.asarray(Image.open(out_s).convert("RGB"), np.float32) / 255.0
slum = 0.2126*simg[...,0] + 0.7152*simg[...,1] + 0.0722*simg[...,2]
srow = slum.mean(axis=1)
sjump = abs(float(srow[ih_s]) - float(srow[ih_s-1]))
check("structured blur seam continuous", sjump < 0.02, f"jump={sjump:.5f}")

# 暖色块应该在延伸区留下痕迹（红色通道在某处明显高于均值）
ext_r = simg[ih_s:, :, 0]
check("warm block survives into extension", float(ext_r.max()) > float(ext_r.mean()) * 1.15,
      f"max={ext_r.max():.3f} mean={ext_r.mean():.3f}")

print("[blur-mix blending]")
# 用结构化图验证：mix 越高，延伸区越接近原图的色彩结构
grad_p = os.path.join(tmp, "smix0.png")
blur_p = os.path.join(tmp, "smix1.png")
H.make_wallpaper(structured, grad_p, ratio=9 / 16, anchor=0.45, blur_mix=0.0)
H.make_wallpaper(structured, blur_p, ratio=9 / 16, anchor=0.45, blur_mix=1.0, blur_radius=6.0)


def ext_colors(p):
    a = (np.asarray(Image.open(p).convert("RGB"), np.float32) / 255.0)[ih_s:]
    e = a[::16, ::16].reshape(-1, 3)
    return len(np.unique((e * 16).astype(int), axis=0))


cg, cb = ext_colors(grad_p), ext_colors(blur_p)
check("blur keeps more color structure than pure gradient", cb > cg,
      f"blur={cb} gradient={cg}")

print()
if fails:
    print(f"FAILED {len(fails)}: {fails}")
    sys.exit(1)
print("ALL PASSED")
