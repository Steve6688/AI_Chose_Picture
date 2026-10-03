# 旅途拾光 — JPG 智能筛选器

一个面向 Windows 的本地照片筛选应用。它只读取 JPG/JPEG，通过清晰度、曝光、动态范围、色彩、构图、分辨率与重复度等指标给出 1–5 星建议，照片不会上传。

## Windows 使用

1. 安装 [Python 3.11+](https://www.python.org/downloads/windows/)（安装时勾选“Add Python to PATH”）。
2. 双击 `run.bat`。首次运行会自动安装 Pillow 和 NumPy。
3. 应用默认目录为 `D:\File\摄影记录\深圳的奇妙之旅\老婆\101_FUJI_2609`。
4. 点“开始 AI 分析”；完成后可按星级、收藏、横/竖幅或搜索筛选。

评分保存在所选照片目录下的 `.travel_photo_ratings.json`，不会改写 JPG 元数据。导出精选照片时会复制到新目录，不移动或删除原片。

## 开发运行

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

运行测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

