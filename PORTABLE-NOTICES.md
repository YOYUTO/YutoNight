# 夜语 0.2.0 便携包第三方说明

本页记录便携包中的主要第三方模型、运行时及其官方来源。具体许可文本见同级 `licenses/` 和 `runtime/app/licenses/`；以随包实际文件与各项目许可原文为准。本包目前供用户在自己的 Windows 11 x64 电脑间完整迁移；公开再分发前仍须检查全部组件的许可、源码提供及告知义务。

| 组件与包内位置 | 许可与官方来源 |
| --- | --- |
| Qwen3-4B Q4_K_M 翻译模型，`models/translation/` | [Qwen 官方模型卡](https://huggingface.co/Qwen/Qwen3-4B-GGUF)，Apache-2.0；许可文本 `licenses/Qwen3-4B-Apache-2.0.txt`。 |
| Whisper large-v3-turbo q5_0 识别模型，`models/asr/` | [ggml 模型仓库](https://huggingface.co/ggerganov/whisper.cpp)，源自 [OpenAI Whisper](https://github.com/openai/whisper)；MIT 许可文本 `licenses/Whisper-MIT.txt`。 |
| `llama-server` CPU 运行时，`runtime/llama/` | [llama.cpp 源码及 MIT 许可](https://github.com/ggml-org/llama.cpp)，本包使用 b11804 Windows CPU x64 发布版；许可文本 `licenses/llama.cpp-MIT.txt`。随附 LLVM OpenMP 的许可在 `runtime/llama/LICENSE-LLVM-OpenMP`。 |
| `whisper-cli` CPU 运行时，`runtime/whisper/` | [whisper.cpp 源码及 MIT 许可](https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE)，本包使用 b5454 Windows x64 发布版；许可文本 `licenses/whisper.cpp-MIT.txt`。其运行时许可与 OpenAI 模型许可分别适用。 |
| `ffmpeg.exe`、`ffprobe.exe`，`runtime/ffmpeg/` | [Gyan 的 Windows FFmpeg 9.0 发布包](https://github.com/GyanD/codexffmpeg/releases/tag/9.0)；本包使用 full 静态构建，该构建启用 GPL/version3，按 GPLv3 发布。实际版本、构建选项及许可声明保存在 `licenses/FFmpeg-build-license.txt`，GPLv3 全文见 `licenses/GPL-3.0.txt`。[对应 FFmpeg 源码版本](https://github.com/FFmpeg/FFmpeg/commit/d32b387f2b)及[Gyan 构建说明与发布入口](https://www.gyan.dev/ffmpeg/builds/)可用于追溯；该发布页没有提供可核实的构建脚本。 |
| Qt 6.12、PySide6、Shiboken6，`runtime/app/_internal/PySide6/` | [Qt for Python 官方许可说明](https://doc.qt.io/qtforpython-6/)，社区版本提供 LGPLv3/GPLv3 许可；[Qt LGPLv3 条款](https://doc.qt.io/qt-6/lgpl.html)。打包的对应许可文件位于 `runtime/app/licenses/PySide6-Essentials/`、`PySide6-Addons/`、`shiboken6/` 与 `licenses/Qt-*.txt`。 |
| Microsoft C++ 应用本地运行库，`runtime/llama/`、`runtime/whisper/Release/` 及应用目录 | 来自官方 PySide6 Windows wheel 的 `MSVCP140.dll`、`VCRUNTIME140.dll` 与 `VCRUNTIME140_1.dll`；参见 [Microsoft 运行库部署说明](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files)。 |
| Python、NumPy、OpenCV、PyInstaller | 分别见 [Python 许可](https://docs.python.org/3/license.html)、[NumPy 许可](https://numpy.org/doc/stable/license.html)、[OpenCV 源码及许可](https://github.com/opencv/opencv/blob/4.14.0/LICENSE)、[PyInstaller 许可与打包例外](https://pyinstaller.org/en/stable/license.html)。随包文本位于 `runtime/app/licenses/`。 |

Qt 库采用动态加载。关闭夜语后，可在 `runtime/app/_internal/PySide6/` 替换兼容的 `Qt6*.dll`，并在 `plugins/` 替换配套插件；替换版本须与 PySide6 6.12 的二进制接口兼容。若需使用自行构建的 Qt/PySide6，可从同目录的 `yutonight/` 源码运行，安装所需依赖后按 `README.md` 的命令重新构建 `runtime/app/`。完整目录迁移时保留许可文本和第三方组件来源信息。
