# 变更记录

## Unreleased

### Added

- macOS `make build` 自动安装 Pandoc、Poppler、BasicTeX 中文支持、Calibre
  和 Pillow。
- Calibre 官方下载源失败时使用带 Homebrew 校验的 GitHub Release 回退。
- 安装项目所需的精确 TeX 宏包前自动更新 `tlmgr`，处理 BasicTeX 与仓库
  版本差异，并避免下载完整 `latexextra` 集合。
- 使用 `kpsewhich` 检查实际 TeX 类、宏包和字体文件，并在进程内刷新
  BasicTeX/Calibre PATH，避免伪健康和重复安装。
- macOS PDF 的 CJK 等宽字体改用支持中文的苹方/思源候选，避免代码块中文
  缺字；TeX Live 基础包与管理器一起更新。
- macOS 工具链安装与 `auto` 引擎复检单元测试。
- 开发者架构、测试和代理开发约定文档。

### Changed

- 将平台工具链和 WSL 输出权限逻辑从主构建脚本拆分为独立模块。
- `make help` 明确区分自动安装的 `build` 与严格只读工具链的
  `build-local`。
- `make release` 未传 `VERSION` 时立即递增最新稳定版的补丁号并发布，
  不再受提交数或发布时间阈值限制。

### Fixed

- 修复 macOS 缺少本地电子书工具时只能报错、无法继续首次构建的问题。
- 修复 Apple Books 夜间主题强制覆盖 EPUB 代码 token 颜色、导致语法高亮全部
  显示为白色的问题。
- 修复 MOBI 夜间主题中的重点文字颜色对比度过低，以及 Apple Books EPUB
  夜间主题覆盖重点色、导致强调层级丢失的问题；旧阅读器改为继承正文色并保留
  结构化侧边标记，并隔离 Calibre 拍扁 EPUB 主题查询造成的浅色主题回归。
- 修复 EPUB/MOBI 夜间主题仍沿用浅色主题深青色标题和目录链接、导致文字对比度
  过低的问题。
- 修复标准 EPUB 深色媒体查询只切换前景色、仍保留浅色正文和组件背景的问题。
- macOS Pillow 改装入项目隔离 venv，避免 Homebrew Python 的 PEP 668
  保护导致首次自动构建失败。
- 使用新版 Pandoc 语法高亮参数，并为旧版本保留能力探测回退。
- 补齐 Pandoc 中文界面术语，消除 `zh-CN` 翻译缺失警告。
- 修复 `make release` 在未传 `VERSION` 时可能静默跳过发布的问题。
