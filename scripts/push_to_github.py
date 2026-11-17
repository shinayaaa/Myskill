# -*- coding: utf-8 -*-
"""
push_to_github.py — 用 GitHub Git Data API 把整个目录作为一次提交推上去。

不用 git CLI（这台机器没装），也不做「逐文件 create contents」那种
20 个提交的蠢做法：
    blob -> tree -> commit -> 更新 ref，一次性原子提交。

用法：
    set GH_TOKEN=...        # Windows: $env:GH_TOKEN="..."
    python push_to_github.py --owner shinayaaa --repo Myskill --dir . --branch main

安全：token 只从环境变量读，不落盘、不打印、不进提交内容。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
BINARY_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")


def api(path, method="GET", payload=None, token=None):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "hydro-wallpaper-publisher")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:600]


def collect(root: Path):
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.name in SKIP_FILES:
            continue
        out.append((rel.as_posix(), p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default=None)
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("缺少 GH_TOKEN 环境变量")

    root = Path(a.dir).resolve()
    files = collect(root)
    if not files:
        sys.exit("目录里没有可提交的文件")
    total = sum(f.stat().st_size for _, f in files)
    print(f"待提交 {len(files)} 个文件，共 {total/1024/1024:.2f} MB")

    # 1) 逐个建 blob
    entries = []
    for rel, path in files:
        raw = path.read_bytes()
        is_bin = any(raw.startswith(m) for m in BINARY_MAGIC) or rel.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif", ".ico")
        )
        payload = {
            "content": base64.b64encode(raw).decode(),
            "encoding": "base64",
        }
        st, blob = api(f"/repos/{a.owner}/{a.repo}/git/blobs", "POST", payload, token)
        if st not in (200, 201):
            sys.exit(f"建 blob 失败 {rel}: {st} {blob}")
        entries.append({
            "path": rel,
            "mode": "100755" if rel.endswith(".py") and os.name != "nt" else "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        print(f"  blob {rel}  {len(raw)/1024:.0f} KB")

    # 2) 建 tree
    st, tree = api(f"/repos/{a.owner}/{a.repo}/git/trees", "POST", {"tree": entries}, token)
    if st not in (200, 201):
        sys.exit(f"建 tree 失败: {st} {tree}")
    print(f"tree {tree['sha']}")

    # 3) 看分支是否已有提交
    st, ref = api(f"/repos/{a.owner}/{a.repo}/git/ref/heads/{a.branch}", token=token)
    parents = []
    if st == 200:
        parents = [ref["object"]["sha"]]
        print(f"已有分支 {a.branch} @ {parents[0][:8]}")
    else:
        print(f"分支 {a.branch} 不存在（空仓库），创建首个提交")

    # 4) 建 commit
    msg = a.message or "feat: hydro-wallpaper 1.0\n\n把任意一张图做成氢桌面风格壁纸：\n- extend 取色延展 / blur 模糊填充 / subject 抠图 / 裁切特写 / splice_blur 双图拼接\n- 接缝亮度对齐与渐强模糊，跳变压到全图行间差同量级\n- 32 项离线自检，示例素材程序化生成\n"
    payload = {"message": msg, "tree": tree["sha"], "parents": parents}
    st, commit = api(f"/repos/{a.owner}/{a.repo}/git/commits", "POST", payload, token)
    if st not in (200, 201):
        sys.exit(f"建 commit 失败: {st} {commit}")
    print(f"commit {commit['sha']}")

    # 5) 更新或创建 ref
    if parents:
        st, r = api(f"/repos/{a.owner}/{a.repo}/git/refs/heads/{a.branch}", "PATCH",
                    {"sha": commit["sha"], "force": False}, token)
    else:
        st, r = api(f"/repos/{a.owner}/{a.repo}/git/refs", "POST",
                    {"ref": f"refs/heads/{a.branch}", "sha": commit["sha"]}, token)
    if st not in (200, 201):
        sys.exit(f"更新 ref 失败: {st} {r}")

    print(f"\n完成 → https://github.com/{a.owner}/{a.repo}/tree/{a.branch}")


if __name__ == "__main__":
    main()
