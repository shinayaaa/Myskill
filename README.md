# Myskill

一组自用的 AI Agent 技能集合。每个技能独立成目录、自带 README 与依赖说明，
可以单独取用，也可以整套 clone 下来慢慢翻。

仓库只保证两件事：**每个目录都能独立跑**，**根目录这页永远是最新的索引**。

## 技能列表

| 技能 | 一句话 | 依赖 | 目录 |
| --- | --- | --- | --- |
| **hydro-wallpaper** | 把任意一张图做成「氢桌面」风格壁纸：上图 + 从图里长出来的延伸背景 | numpy + Pillow（抠图另需 rembg）| [`hydro-wallpaper/`](hydro-wallpaper/) |

## 使用方式

所有技能都不依赖仓库本身，单独下载对应目录也能跑：

```bash
git clone https://github.com/shinayaaa/Myskill.git
cd Myskill/hydro-wallpaper
pip install numpy pillow
python scripts/hydro_engine.py photo.png -o out.png --ratio 9:16 --anchor 0.33
```

细节看各技能自己的 README。

## 贡献

这些技能是按具体需求写的，不是通用框架，所以接口会按需要变。
如果哪个正好解决你的问题，欢迎提 issue 说清使用场景；
提 PR 前请先确认改动能单独跑通该技能目录下的自检（如果有）。

## License

[MIT](LICENSE) —— 整个仓库，含所有技能目录，除非该目录内另有 LICENSE 声明。
