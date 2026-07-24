# 电子书构建

执行：

```bash
make build
```

构建完成后会生成：

```text
dist/
├── algorithm-starts-with-counting.pdf
├── algorithm-starts-with-counting.epub
└── algorithm-starts-with-counting.mobi
```

构建内容固定为 `01.md` 到 `13.md`，顺序由 `book.toml` 控制。封面使用
`imgs/cover.png`，正文图片会按照各章中的 Markdown 引用打包。

## 构建方式

`make build` 默认使用当前 Linux/WSL 环境中的 Pandoc、XeLaTeX 和
Calibre，不会自动切换到 Docker。

在 Windows 原生终端运行时，构建脚本会提示进入 WSL 后再次执行
`make build`。首次在 Ubuntu/Debian WSL 中构建时，如果缺少工具，脚本会
通过 `apt-get` 自动安装 Pandoc、XeLaTeX、中文字体和 Calibre；期间
`sudo` 可能要求输入密码。后续构建直接复用已安装的工具。

WSL 下的 Makefile 固定使用 `/usr/bin/python3`，避免已激活的 Conda 或
venv 环境用旧版 Python 覆盖系统解释器。需要自定义时仍可通过
`make PYTHON=/path/to/python build` 显式指定。

如果 `.build/` 或 `dist/` 曾由 root 或 Docker 创建，WSL 本地构建会在
写入前自动检查这两个生成物目录，并仅对 owner 不匹配的目录执行一次
`sudo chown -R`。对于 `/mnt/c`、`/mnt/d` 等 DrvFS 挂载，如果 Windows
ACL 令 `chown` 不生效，脚本会继续尝试 `chmod`；仍不可写时会重建这两个
可再生成目录。源码和图片目录不会被修改。

Docker 仅在明确执行 `make build-docker` 或传入 `--engine docker` 时使用。
Docker 镜像会按 `docker/Dockerfile` 的 SHA-256 自动复用：镜像存在且 Dockerfile
没有变化时，还会启动一个一次性健康检查容器，确认 Pandoc、XeLaTeX、Calibre、
Poppler、字体和 Pillow 均可用。检查通过后跳过 `docker build`；镜像缺失、
Dockerfile 变化或健康检查失败时会自动重建。需要更新基础镜像或强制重建时执行：

```powershell
.\scripts\build.ps1 --engine docker --rebuild-docker-image
```

实际构建容器使用唯一名称、`--init` 和 `--rm`。无论构建成功、失败还是被中断，
脚本都会在结束时再次执行精确名称的强制清理，不会保留停止状态的构建容器。
首次 Docker 构建需要下载 Pandoc、中文 LaTeX 字体与 Calibre，镜像较大；
后续构建会复用缓存。

## 常用命令

```bash
make pdf
make epub
make mobi
make verify
make doctor
make clean
```

## 字体与图片体积

PDF 默认使用 `book.toml` 中的 `auto` 字体配置。构建脚本会按当前系统选择已安装字体：

- macOS：宋体 SC / 苹方 SC / SF Mono 或 Menlo
- Windows：宋体 / 微软雅黑 / Cascadia Mono 或 Consolas
- Linux、WSL 和 Docker：Noto CJK / DejaVu Sans Mono

PDF 为保证换一台电脑后版式不变，仍会对子集字体进行嵌入；字体只包含书中实际使用的
字形，通常不是文件体积的主要来源。EPUB 不嵌入字体，阅读器会从跨平台 CSS 字体栈中
选择本机字体。也可以在 `book.toml` 的 `[pdf]` 中写具体字体名覆盖自动选择。

构建时默认将 `.build/` 中的电子书图片副本转换为渐进式 JPEG，源文件不会被修改。
压缩参数位于 `book.toml`：

```toml
[images]
optimize = true
max_width = 1600
jpeg_quality = 84
cover_quality = 88
```

如需无损原图输出，可设置 `optimize = false`。

- `make pdf`：仅生成 PDF。
- `make epub`：仅生成 EPUB3。
- `make mobi`：生成 MOBI，并保留作为中间来源的 EPUB。
- `make verify`：检查三个输出文件的容器结构和可读性。
- `make doctor`：检查章节、图片、Pandoc、XeLaTeX、Calibre 与 Docker。
- `make clean`：删除 `.build/` 与 `dist/`。

要强制指定环境：

```bash
make build-local
make build-docker
```

## GitHub Release

自动发布：

```bash
make release
```

在 Windows PowerShell 中，`make release` 会自动调用 `scripts/release.ps1`
进入默认 WSL 发行版；也可以不安装 GNU Make，直接执行：

```powershell
.\scripts\release.ps1
.\scripts\release.ps1 --version v0.2.0
```

版本判断、Git 检查、`gh` 身份检查、电子书构建、产物校验和 GitHub 上传都会在
同一个 WSL 环境中完成，退出码会原样返回 PowerShell。

脚本读取 GitHub 上最新的非草稿 Release，并统计对应标签到当前 `HEAD`
之间的 Git 提交数。如果提交数大于 10，或者距离上次发布时间超过
24 小时，则自动递增补丁版本，例如 `v0.1.9` 变为 `v0.1.10`。条件均
未达到时命令正常退出，不创建 Release。仓库还没有 Release 时从
`v0.1.0` 开始。

显式指定版本会跳过时间与提交数阈值：

```bash
make release VERSION=v0.2.0
```

发布命令要求工作区干净且当前提交已经推送到 `origin`。满足条件后会：

1. 执行完整电子书构建与校验。
2. 创建对应版本的 Git 标签和 GitHub Release。
3. 将 `dist/` 中的 PDF、EPUB、MOBI 作为 Release 附件上传。
4. 输出可供下载的 GitHub Release URL。

Release 构建会在封面图片后的文字封面页显示“版本号”和“最后更新”日期；
PDF、EPUB、MOBI 保持一致。同时 EPUB 的 metadata identifier 也包含对应版本号。
普通的 `make build` 不会写入发布版本。

发布使用 GitHub CLI。WSL 中首次运行会自动尝试安装 `gh`，之后需要
用户执行一次 `gh auth login` 完成 GitHub 身份认证。

Windows 上如果尚未安装 GNU Make，也可以直接运行：

```powershell
.\scripts\build.ps1
```

该命令默认自动进入当前默认 WSL 发行版，在仓库对应的挂载路径中使用
`/usr/bin/python3` 执行本地构建，不需要手动打开 WSL 终端。明确指定
`--engine docker` 时才会留在 Windows 环境调用 Docker Desktop。

## 本机构建依赖

- Pandoc
- XeLaTeX
- `ctexbook`、`texlive-fonts-recommended` 与中文字体支持
- Calibre 命令行工具 `ebook-convert`、`ebook-meta`
- Poppler 的 `pdfinfo`（用于 PDF 验证，推荐）

如工具安装在非标准位置，可使用环境变量覆盖命令：

```bash
PANDOC=/path/to/pandoc \
XELATEX=/path/to/xelatex \
EBOOK_CONVERT=/path/to/ebook-convert \
make build-local
```

书名、作者、封面、文件名、字体和章节顺序集中配置在 `book.toml`。
