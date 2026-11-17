# -*- coding: utf-8 -*-
"""
push_to_github.py — 用 GitHub Git Data API 把整个目录作为一次提交推上去。

不用 git CLI，也不做「逐文件 create contents」那种 N 个提交的做法：
    blob -> tree -> commit -> 更新 ref，一次性原子提交。

适合把当前目录整体发布成一个仓库/分支快照。

用法：
    $env:GH_TOKEN = "..."     # token 只从环境变量读，不落盘、不进提交
    python tools/push_to_github.py --owner shinayaaa --repo Myskill --dir . --branch main

    # 预览要提交什么（不调用写接口）
    python tools/push_to_github.py --owner x --repo y --dir . --dry-run

注意：
  - 会**整体替换**目标分支的文件列表。target 分支上原本存在、但本地没有的
    文件会被删除（这是快照语义，不是增量同步）。
  - 所以推送前务必 --dry-run 看一遍清单。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", "out", "output"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini"}
BINARY_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".ico", ".zip", ".onnx")
BINARY_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")


def api(path, method="GET", payload=None, token=None, retries=4):
    """
    带重试的 API 调用。

    上传大文件（blob）时 GitHub 偶尔会在中途断连，
    报的是 RemoteDisconnected / ConnectionReset 而不是 HTTP 错误码，
    这类值得重试；4xx 是请求本身有问题，重试没用，直接返回。
    """
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None

    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "myskill-publisher")
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read().decode()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:600]
            if 400 <= e.code < 500:
                return e.code, body
            last = f"HTTP {e.code}: {body}"
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            wait = 2 ** attempt
            print(f"  ! {method} {path} 失败（{last}），{wait}s 后重试 "
                  f"[{attempt}/{retries - 1}]", flush=True)
            time.sleep(wait)
    return 0, last


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
    ap.add_argument("--dry-run", action="store_true", help="只列出将要提交的文件")
    a = ap.parse_args()

    root = Path(a.dir).resolve()
    files = collect(root)
    if not files:
        sys.exit("目录里没有可提交的文件")

    total = sum(f.stat().st_size for _, f in files)
    print(f"待提交 {len(files)} 个文件，共 {total/1024/1024:.2f} MB\n")
    for rel, p in files:
        print(f"  {p.stat().st_size/1024:>8.0f} KB  {rel}")
    if a.dry_run:
        print("\n[dry-run] 未调用任何写接口。")
        return

    token = os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("缺少 GH_TOKEN 环境变量")

    print()
    entries = []
    for rel, path in files:
        raw = path.read_bytes()
        payload = {"content": base64.b64encode(raw).decode(), "encoding": "base64"}
        st, blob = api(f"/repos/{a.owner}/{a.repo}/git/blobs", "POST", payload, token)
        if st not in (200, 201):
            sys.exit(f"建 blob 失败 {rel}: {st} {blob}")
        entries.append({
            "path": rel,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
    print(f"blobs 建好：{len(entries)}")

    st, tree = api(f"/repos/{a.owner}/{a.repo}/git/trees", "POST", {"tree": entries}, token)
    if st not in (200, 201):
        sys.exit(f"建 tree 失败: {st} {tree}")
    print(f"tree {tree['sha']}")

    st, ref = api(f"/repos/{a.owner}/{a.repo}/git/ref/heads/{a.branch}", token=token)
    parents = []
    if st == 200:
        parents = [ref["object"]["sha"]]
        print(f"已有分支 {a.branch} @ {parents[0][:8]}")
    else:
        print(f"分支 {a.branch} 不存在（空仓库），创建首个提交")

    msg = a.message or "chore: update repository"
    payload = {"message": msg, "tree": tree["sha"], "parents": parents}
    st, commit = api(f"/repos/{a.owner}/{a.repo}/git/commits", "POST", payload, token)
    if st not in (200, 201):
        sys.exit(f"建 commit 失败: {st} {commit}")
    print(f"commit {commit['sha']}")

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
