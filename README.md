# C 盘安全清理工具

这是一个 Windows 本地 GUI 清理工具，用于扫描并清理当前用户可访问的低风险缓存。工具会先扫描并展示可清理大小、文件数量和路径，必须手动确认后才会删除。

## 运行源码版

```powershell
python .\c_drive_cleaner.py
```

## 打包 EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

生成的程序位于：

```text
dist\CDriveCleaner\CDriveCleaner.exe
```

如需重新干净构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1 -Clean
```

## 默认清理范围

- 当前用户临时目录，例如 `%TEMP%`
- Windows 用户级缓存，例如 `INetCache`、`WebCache`、WER 报告缓存
- 资源管理器缩略图缓存
- 常见用户级应用日志和崩溃报告缓存
- Microsoft Edge、Google Chrome、Mozilla Firefox 的常见缓存目录

工具不会默认扫描或删除桌面、文档、图片、视频、下载等个人文件目录，也不会强制请求管理员权限。

## 使用建议

- 点击“扫描”后先检查分类、大小和路径。
- 勾选需要清理的分类，再点击“清理所选”。
- 清理浏览器缓存前，建议先关闭 Edge、Chrome 和 Firefox。
- 正在被系统或应用占用的文件会自动跳过，并在日志中显示失败原因。

## 安全说明

第一版以安全为优先目标，只处理可确认属于缓存或临时数据的目录。无法确认安全的目录不会纳入默认规则。清理失败不会中断整个任务，失败项会保留在磁盘上。
