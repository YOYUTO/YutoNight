# 参与开发

本仓库维护夜语的**源码版**。完整便携版是由源码、模型和外部运行时组合生成的交付包；模型、可执行运行时、构建缓存和个人媒体文件不进入 Git。

## 本地运行

在 Windows 11 x64 安装 Python 3.11，并确保 `ffmpeg`、`ffprobe` 可从命令行调用。在仓库根目录执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m yutonight
```

开发源码时请用最后一行直接启动。`start.cmd` 会优先打开 `runtime/app/YutoNight.exe`；如果本地留有旧的便携程序，用它启动可能看不到刚修改的源码。

单元测试使用合成媒体、本机 HTTP 测试服务和模拟响应，不需要大模型或真实 API Key。运行：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

确保 FFmpeg 和 ffprobe 都在 `PATH` 中，否则视频集成测试会跳过。CI 在 Windows、Python 3.11 环境中安装 FFmpeg 并运行同一套测试。

## 提交与评审

以 `main` 为稳定分支，从最新 `main` 创建简短的功能或修复分支。每个分支聚焦一件事；提交前运行完整测试，再通过 Pull Request 合并。PR 请说明改动的用户行为、验证命令与结果，以及仍需手工验证的媒体场景。修复缺陷时，优先添加能复现问题的测试。

不要提交 `models/`、`runtime/`、`.venv/`、`work/`、构建产物、真实视频或字幕。不要提交 API Key、令牌、`.env`、包含凭据的日志；测试只用明显的假值。需要展示媒体行为时，使用自行生成的短合成素材。

美颜目前是 OpenCV CPU 实验基线。保护区域由用户手动指定位置和时间，不会自动追踪马赛克或遮挡；也没有瘦脸、大眼。提交相关改动时，应保留保护区域不被美颜采样污染的测试，并用合成素材检查导出。真实人物或成人素材的识别和美颜质量不能由单元测试推断。
