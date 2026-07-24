# 项目开发规则

## 构建与运行

```bash
make build
make pdf
make epub
make mobi
make doctor
```

`make build` 可在 macOS 和 Ubuntu/Debian WSL 自动安装缺失工具。
`make build-local` 不修改系统工具链，`make build-docker` 使用隔离镜像。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py tests/*.py
make build
make verify
git diff --check
```

## 开发约定

- 内容和顺序以 `book.toml` 为准，不在脚本中硬编码章节业务规则。
- 生成物只写入 `.build/` 与 `dist/`，不得覆盖 Markdown 和源图片。
- 修改构建行为时同步更新 `README.md`、`BUILD.md`、架构/测试文档和
  `CHANGELOG.md`。
- Python 单文件不超过 1000 行；平台逻辑放入独立模块。
- 新增平台分支必须使用 mock 单测覆盖，不能让测试真的安装系统软件。
