# 夜语 YutoNight 0.2.0

夜语是 Windows 11 x64 桌面工具，用于给视频制作日中双语字幕，并试用可调 CPU 美颜。

## 源码版与完整便携版

两种版本分别保留：本仓库管理源码、测试和构建配置；完整便携版包含应用、FFmpeg、识别与翻译运行时及模型。Git 克隆不包含 `models/`、`runtime/`、`releases/` 或用户视频项目。完整便携包单独保存，复制整个解压目录到另一台 Windows 11 x64 电脑即可使用，无需另行安装 Python 或 Ollama。

共同开发请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。从源码启动：

```powershell
git clone https://github.com/YOYUTO/YutoNight.git
cd YutoNight
.\install.cmd
.\.venv\Scripts\python.exe -m yutonight
```

源码运行必须使用上面的 Python 命令；工作目录若有便携 EXE，`start.cmd` 会优先运行它，不能据此确认源码修改。

若已有完整便携包，可把其中的 `models/` 以及 `runtime/ffmpeg/`、`runtime/whisper/`、`runtime/llama/` 复制到源码目录，复用相同模型。也可运行 `python tools/fetch-assets.py` 从官方源下载并校验固定模型及 llama/whisper CPU 运行时；FFmpeg/ffprobe 需另行准备并加入 PATH，或放入 `runtime/ffmpeg/`。下载过程不需要账户或 API Key，模型约 3.07 GB，不是源码测试的前置条件。

本仓库的自有代码尚未指定开源许可证；各第三方组件继续适用自己的许可，见 [PORTABLE-NOTICES.md](PORTABLE-NOTICES.md)。

## 便携版启动与自检

1. 保留 `start.cmd`、`check.cmd`、`runtime/`、`models/` 和 `licenses/` 的相对位置，把整个目录复制到目标电脑。
2. 双击 `check.cmd`。它优先运行 `runtime/app/YutoNight.exe --self-check`，等待程序结束后显示 `portable-self-check.json`。检查会核对依赖、运行时与模型文件，不执行识别或翻译，也不读取 API Key。
3. 双击 `start.cmd`。它优先运行便携程序；只有缺少便携程序时，才尝试本目录的 `.venv` 源码环境。

默认随包文件包括 `models/asr/ggml-large-v3-turbo-q5_0.bin`、`models/translation/qwen3-4b-q4_k_m.gguf`、`runtime/ffmpeg/`、`runtime/whisper/`、`runtime/llama/` 和 `runtime/app/`。模型与 CPU 运行时均在本机执行，启动和运行时不会自动下载模型。首次处理长视频或运行翻译可能耗时较久，请预留磁盘空间。

## 字幕流程

- 导入视频后，可本地识别日语音轨，逐条修改原文、中文译文和时间；也可导入 UTF-8 日文 SRT，从人工字幕继续。
- 修改中文会将条目标为已校对；修改日文会清空旧译文并解除校对状态。翻译会保留已校对条目。可保存 JSON 项目，导出双语 SRT，或将字幕烧录到 MP4。
- 默认“随包本地翻译”使用 Qwen3-4B Q4_K_M 和 CPU `llama-server`，无需 Ollama。也可自行选择本机 Ollama，或填写 DeepSeek 官方接口设置。DeepSeek 只发送字幕文本、相邻上下文和术语表，不发送视频或音轨；API Key 仅保存在当前进程内存，不写入项目、日志或脚本。
- 识别默认使用随包的 Whisper large-v3-turbo q5_0 与 CPU `whisper-cli`。不同口音、背景声与片源会影响识别结果，导出前应逐条校对；实际成人日语素材上的识别质量尚未验证。

## 美颜与保护区域

美颜是 OpenCV CPU 实验版，提供“原画、自然、白净、柔和、清晰”预设，并可调整磨皮、美白和锐化。当前帧可前后对比。仅对检测到的正脸皮肤区域处理；没有检测到人脸时保留原帧。

可手动画固定保护区域，并限定生效时间段。移动的遮挡需要分段调整和检查；目前没有自动马赛克追踪、瘦脸或大眼。保护框只显示在预览中，不会画入导出视频。导出采用有损视频编码，不能保证成片解码后的保护区域逐像素相同。

处理目前使用 CPU；另一台电脑的 RX 9070 尚未实测，不能据此推断加速、速度或显存需求。HDR 美颜会拒绝处理。可变帧率视频转为平均帧率的固定帧率输出；同时使用美颜和烧录字幕可能需要二次编码与额外磁盘空间。任务运行期间请等待完成再关闭窗口。

## 源码运行与重新构建

若只取得源码，需 Windows 11 x64、64 位 Python 3.11、可用的 FFmpeg/ffprobe，以及联网安装 `requirements.txt` 中的依赖。运行 `install.cmd` 创建 `.venv`，然后用 `.venv\Scripts\python.exe -m yutonight` 启动。源码运行可以使用本目录的 `runtime/` 与 `models/`；若这些文件未提供，须按界面提示自行准备运行时和模型。

重新构建便携应用时，先在 `.venv` 安装 PyInstaller，再从项目根目录运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\build-portable.ps1 -Replace`。构建脚本将旧版 `runtime/app` 备份到 `work/`，生成的程序仍需与 `runtime/` 的其他工具及 `models/` 一起迁移。`check.cmd` 可用于构建后检查。第三方组件来源、许可与 Qt 动态库替换方式见 [PORTABLE-NOTICES.md](PORTABLE-NOTICES.md)。

自动测试：`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`。
