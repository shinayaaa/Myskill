"""带续传的 u2net 模型下载器：网络不稳时反复重试，已下载部分不丢。"""
import os
import sys
import time
import urllib.request

URLS = [
    "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx",
    "https://hf-mirror.com/tomjackson2023/rembg/resolve/main/u2net.onnx",
]


def download(url, dst, attempts_per_url=8):
    part = dst + ".part"
    for attempt in range(attempts_per_url):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        req = urllib.request.Request(url)
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                total = r.headers.get("Content-Length")
                if have and r.status != 206:
                    have = 0  # 服务端不支持续传，重头来
                mode = "ab" if have else "wb"
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(512 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
            size = os.path.getsize(part)
            print(f"  attempt {attempt+1}: {size/1e6:.1f} MB", flush=True)
            if size > 170_000_000:
                os.replace(part, dst)
                return True
        except Exception as e:
            print(f"  attempt {attempt+1} failed: {type(e).__name__}", flush=True)
            time.sleep(3)
    return False


def main():
    dst_dir = os.path.expanduser(r"~/.rembg/models/u2net")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "u2net.onnx")
    if os.path.exists(dst) and os.path.getsize(dst) > 170_000_000:
        print("already present:", dst, flush=True)
        return 0
    for url in URLS:
        print("trying", url, flush=True)
        if download(url, dst):
            print("OK", dst, os.path.getsize(dst), flush=True)
            return 0
    print("FAILED", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
