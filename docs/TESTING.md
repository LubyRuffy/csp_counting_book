# 测试说明

## 单元测试

执行：

```bash
python3 -m unittest discover -s tests -v
```

平台安装测试会 mock Homebrew、`tlmgr`、隔离 Python venv、`pip` 和系统识别，
不会修改测试机器。Release 测试也会 mock GitHub CLI、构建和上传，验证未传
`VERSION` 时立即递增最新补丁版本并进入发布流程。测试重点覆盖依赖选择、
安装命令、安装后复检、版本决策和失败提示。

## 静态检查

```bash
python3 -m py_compile scripts/*.py tests/*.py
git diff --check
```

## 端到端构建

```bash
make build
make verify
```

成功标准是 `dist/` 中 PDF、EPUB、MOBI 均存在且通过容器/元数据校验。PDF
发布前还应渲染页面检查中文字体、分页、页眉页脚、图片和目录。

EPUB 发布前需解包确认代码块包含
`ibooks-dark-theme-use-custom-text-color`，且代码 token 保留 `dt`、`kw`、
`cf` 等高亮类；重点文字应包含 `book-emphasis` 和
`ibooks-dark-theme-use-custom-text-color`。支持主题查询时，浅色和深色重点色与
对应背景的对比度均不低于 4.5:1；不支持时必须继承阅读器前景色，并保留粗体与侧边
标记。MOBI 转换命令必须追加 `styles/mobi.css`，防止 Calibre 将 EPUB 深色规则
拍扁为默认颜色。章节标题和目录链接在夜间主题必须继承阅读器前景色，不能保留浅色
主题的深青色。深色媒体查询必须同时切换正文、行内代码、表头和引用块背景，不能
只换前景色。Apple Books 与 Calibre 走测都要覆盖原始主题和夜间主题。

## 一键排障

本项目没有网络交互 ID；一次构建就是最小可复现交互。执行：

```bash
make doctor
make build 2>&1 | tee build.log
```

提交 `build.log` 即可还原依赖探测、实际命令、失败阶段和退出码。
