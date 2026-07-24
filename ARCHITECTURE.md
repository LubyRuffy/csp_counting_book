# 开发者架构说明

## 系统整体架构

本项目是以 Markdown 为单一内容源的电子书构建系统：

```text
book.toml + 章节 Markdown + imgs/
                |
        scripts/build_book.py
         /        |        \
      Pandoc   XeLaTeX   Calibre
         \        |        /
          PDF / EPUB / MOBI
```

## 核心模块

- `scripts/build_book.py`：编排配置、资源预处理、格式构建与产物校验。
- `scripts/build_support.py`：命令执行、依赖探测以及 macOS/WSL 工具链安装。
- `scripts/output_permissions.py`：处理 WSL/DrvFS 生成目录所有权和文件锁。
- `scripts/pdf_layout.lua`、`scripts/epub.lua`、`styles/`：PDF 与 EPUB
  的版式规则；EPUB 过滤器为代码块和重点文字声明 Apple Books 深色主题
  自定义配色能力；`styles/mobi.css` 在 Calibre 转换末尾让 MOBI 重点文字退回
  阅读器前景色，同时保留结构化侧边标记；标题和目录颜色也由阅读主题控制。
- `scripts/pandoc-data/`：项目自带的 Pandoc 中文界面术语。
- `scripts/release.py`：版本判断、构建验证和 GitHub Release 发布。

## 请求与数据流

1. `make build` 调用 `build_book.py build --engine auto`。
2. 脚本读取 `book.toml`，计算版本与更新时间。
3. `auto` 引擎探测依赖；macOS 用 Homebrew/TeX Live、WSL 用 apt 自动补齐。
4. 图片副本在 `.build/` 优化，源图不变。
5. Pandoc 在 EPUB 代码块和重点文字外添加深色主题兼容容器，再由
   Pandoc/XeLaTeX/Calibre 写入 `dist/`。
6. 构建器重新打开并校验每种产物，失败时返回非零退出码。
