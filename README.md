# Myskill

一组自用的 AI Agent 技能集合。每个技能独立成目录、自带 README 与依赖说明，
可以单独取用，也可以整套 clone 下来慢慢翻。

仓库只保证两件事：**每个目录都能独立跑**，**根目录这页永远是最新的索引**。

## 技能列表

| 技能 | 一句话 | 依赖 | 目录 |
| --- | --- | --- | --- |
| **hydro-wallpaper** | 把任意一张图做成「氢桌面」风格壁纸：上图 + 从图里长出来的延伸背景 | numpy + Pillow（抠图另需 rembg）| [`hydro-wallpaper/`](hydro-wallpaper/) |

<p align="center">
  <img src="hydro-wallpaper/assets/examples/extend.jpg" width="19%" alt="extend 取色延展">
  <img src="hydro-wallpaper/assets/examples/blur.jpg" width="19%" alt="blur 模糊填充">
  <img src="hydro-wallpaper/assets/examples/sunset.jpg" width="19%" alt="blur-mix 叠化">
  <img src="hydro-wallpaper/assets/examples/splice.jpg" width="19%" alt="splice_blur 双图拼接">
</p>

<p align="center">
  <sub>hydro-wallpaper 的四种输出：取色延展 · 模糊填充 · 叠化 · 双图拼接</sub>
</p>

## 目录约定

新增一个技能时，按这个结构放，索引里加一行就完事：

```
Myskill/
├── README.md              # 本文件，唯一索引
├── LICENSE                # 整仓统一许可（MIT）
├── .gitignore             # 仓库级忽略规则
├── tools/                 # 维护仓库用的脚本，不属于任何技能
│   └── push_to_github.py
└── <skill-name>/          # 一个技能一个目录
    ├── README.md          # 该技能的独立说明（能单独看懂）
    ├── SKILL.md           # 给 AI Agent 用的技能描述（可选）
    ├── LICENSE            # 仅当该技能许可与本仓不同才需要
    ├── scripts/           # 可执行代码
    ├── assets/            # 示例图、源素材、生成脚本
    └── setup_env.py       # 该技能自己的环境安装入口（可选）
```

三条规则：

1. **不共享代码。** 技能之间不互相 import。宁可复制几十行，也不要在
   一个技能的改动上牵连另一个——这些技能的使用场景本来就互不相干。
2. **不共享依赖清单。** 每个技能自己说明要装什么。整个仓库没有统一的
   `requirements.txt`，因为没人会想为了用一个壁纸脚本装一堆别的。
3. **目录名即技能名**，用 kebab-case：`hydro-wallpaper`、`pdf-shrink` 这样。

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
