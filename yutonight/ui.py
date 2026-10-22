"""Chinese desktop workspace for local subtitles and conservative face beauty."""
from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolBar, QVBoxLayout, QWidget,
    QAbstractItemView, QHeaderView,
)

from . import beauty, paths, pipeline
from .core import Cue, Project, format_srt, load_project, parse_srt, save_project


def _time_text(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    whole, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(whole, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def _seconds(text: str) -> float:
    value = text.strip().replace(",", ".")
    if ":" not in value:
        result = float(value)
    else:
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError("时间应为 HH:MM:SS,mmm 或秒数。")
        hour, minute, second = int(parts[0]), int(parts[1]), float(parts[2])
        if hour < 0 or not 0 <= minute < 60 or not 0 <= second < 60:
            raise ValueError("时间格式不正确。")
        result = hour * 3600 + minute * 60 + second
    if not 0 <= result < float("inf"):
        raise ValueError("时间必须是有限的非负秒数。")
    return result


class _Worker(QObject):
    message = Signal(str)
    succeeded = Signal(object)
    partial = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation, streaming=False):
        super().__init__()
        self.operation = operation
        self.streaming = streaming

    @Slot()
    def run(self):
        try:
            result = self.operation(self.message.emit)
            if self.streaming:
                for batch in result:
                    self.partial.emit(batch)
                result = None
            self.succeeded.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("夜语 · 本地视频字幕与美颜")
        self.resize(1320, 920)
        self.project = Project()
        self.project_path: Path | None = None
        self.dirty = False
        self._loading = False
        self._thread = None
        self._worker = None
        self._task_callback = None
        self._partial_callback = None
        self._recognition_replacing = False
        self._busy_widgets = []
        self._actions = []
        self._build_ui()
        self._refresh_project()

    def _build_ui(self):
        toolbar = QToolBar("项目操作", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label, callback in (
            ("导入视频", self.open_video), ("重新关联视频", self.relink_video), ("打开项目", self.open_project),
            ("保存项目", self.save_project), ("导入原文 SRT", self.import_subtitles),
            ("导出字幕", self.export_subtitles), ("导出视频", self.export_video),
        ):
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
            self._actions.append(action)
            if callback == self.save_project:
                self._save_action = action
        self.video_path_label = QLabel("尚未导入视频")
        self.video_path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.video_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.video_path_label.setWordWrap(True)
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(480, 240)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.7)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(lambda duration: self.seek_slider.setRange(0, duration))
        self.player.errorOccurred.connect(self._media_error)
        self.play_button = QPushButton("播放 / 暂停")
        self.play_button.clicked.connect(self._toggle_play)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(self.player.setPosition)
        self.time_label = QLabel("00:00:00,000")
        controls = QHBoxLayout()
        controls.addWidget(self.play_button)
        controls.addWidget(self.seek_slider, 1)
        controls.addWidget(self.time_label)
        self.source_label = QLabel("日文原文")
        self.translation_label = QLabel("中文译文")
        for label in (self.source_label, self.translation_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setMinimumHeight(28)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.translation_label.setStyleSheet("font-size: 17px; font-weight: 600;")
        player_layout = QVBoxLayout()
        player_layout.addWidget(self.video_path_label)
        player_layout.addWidget(self.video_widget, 1)
        player_layout.addLayout(controls)
        player_layout.addWidget(self.source_label)
        player_layout.addWidget(self.translation_label)
        player_panel = QWidget()
        player_panel.setLayout(player_layout)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("subtitleTable")
        self.table.setHorizontalHeaderLabels(["开始", "结束", "日文原文", "中文译文", "已校对"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 112)
        self.table.setColumnWidth(1, 112)
        self.table.setColumnWidth(4, 64)
        self.table.itemChanged.connect(self._cue_changed)
        self.table.currentCellChanged.connect(self._cue_selected)
        cue_tools = QHBoxLayout()
        self.add_cue_button = QPushButton("在当前位置添加字幕")
        self.add_cue_button.clicked.connect(self.add_cue)
        self.delete_cue_button = QPushButton("删除选中字幕")
        self.delete_cue_button.clicked.connect(self.delete_cue)
        cue_tools.addWidget(self.add_cue_button)
        cue_tools.addWidget(self.delete_cue_button)
        cue_tools.addStretch()
        cue_layout = QVBoxLayout()
        cue_layout.addLayout(cue_tools)
        cue_layout.addWidget(self.table)
        cue_panel = QWidget()
        cue_panel.setLayout(cue_layout)
        left = QSplitter(Qt.Orientation.Vertical)
        left.addWidget(player_panel)
        left.addWidget(cue_panel)
        left.setSizes([460, 330])
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setMinimumWidth(360)
        self.settings_tabs.setMaximumWidth(470)
        self.settings_tabs.addTab(self._recognition_tab(), "识别与翻译")
        self.settings_tabs.addTab(self._beauty_tab(), "美颜与保护")
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.settings_tabs)
        splitter.setStretchFactor(0, 1)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("就绪 · 请先导入视频或打开项目")
        self._busy_widgets.extend([self.table, self.settings_tabs, self.add_cue_button,
                                   self.delete_cue_button])

    def _recognition_tab(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        group = QGroupBox("本地日语识别 · whisper.cpp")
        form = QFormLayout(group)
        self.whisper_executable = QLineEdit()
        self.whisper_executable.setPlaceholderText("可留空：使用 FFmpeg 内置识别（CPU）")
        executable = paths.bundled_tool("whisper-cli")
        if executable:
            self.whisper_executable.setText(str(executable))
        self.whisper_model = QLineEdit()
        self.whisper_model.setPlaceholderText("未找到随附识别模型，请检查 models 文件夹或手动选择")
        for candidate in (paths.ASR_MODEL, paths.PROJECT_ROOT / "models" / "ggml-small.bin", paths.PROJECT_ROOT / "models" / "ggml-base.bin"):
            if candidate.is_file():
                self.whisper_model.setText(str(candidate))
                break
        form.addRow("识别程序", self._path_field(self.whisper_executable, "选择 whisper.cpp 程序"))
        form.addRow("识别模型", self._path_field(self.whisper_model, "选择识别模型"))
        self.transcribe_button = QPushButton("分段识别日语字幕")
        self.transcribe_button.clicked.connect(self.transcribe)
        form.addRow(self.transcribe_button)
        recognition_hint = QLabel("每段完成后显示字幕，可边播放边校对、保存；识别结束后可批量翻译。")
        recognition_hint.setWordWrap(True)
        form.addRow(recognition_hint)
        layout.addWidget(group)
        group = QGroupBox("翻译")
        form = QFormLayout(group)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("项目内模型（离线）", "bundled")
        self.provider_combo.addItem("本地 Ollama", "ollama")
        self.provider_combo.addItem("DeepSeek API", "deepseek")
        self.model_name = QLineEdit()
        self.model_name.setPlaceholderText("填写已安装的本地模型名称")
        self.base_url = QLineEdit()
        self._active_provider = None
        self._provider_configs = {
            "ollama": ("", "http://127.0.0.1:11434"),
            "deepseek": ("deepseek-chat", "https://api.deepseek.com"),
        }
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("仅本次运行保存在内存中")
        self.api_key.setEnabled(False)
        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        self._provider_changed()
        form.addRow("翻译方式", self.provider_combo)
        form.addRow("模型名称", self.model_name)
        form.addRow("服务地址", self.base_url)
        form.addRow("API Key", self.api_key)
        self.glossary = QPlainTextEdit()
        self.glossary.setPlaceholderText("可选：术语对应表，每行填写 日文 = 中文")
        self.glossary.setMaximumHeight(95)
        form.addRow("术语表", self.glossary)
        self.translate_button = QPushButton("翻译未校对字幕")
        self.translate_button.clicked.connect(self.translate)
        form.addRow(self.translate_button)
        notice = QLabel("项目内模式自动启动随附模型，运行时不会下载模型，无需安装 Ollama。\n本地模式仅连接本机；DeepSeek 仅发送字幕文本及术语上下文。\n已校对条目会保留；未校对条目的现有译文可能被覆盖。\n翻译按片段检查回传完整性，句内语义仍需人工校对。\nAPI Key 不写入项目文件。")
        notice.setWordWrap(True)
        form.addRow(notice)
        layout.addWidget(group)
        self.burn_subtitles = QCheckBox("导出视频时烧录双语字幕")
        self.burn_subtitles.setChecked(True)
        layout.addWidget(self.burn_subtitles)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        return scroll

    def _beauty_tab(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        notice = QLabel("CPU 美颜（实验版）\n仅对检测到的正脸皮肤区域处理。保护框需手动设置，不包含自动马赛克检测或脸部形变。\n保护框位置固定；移动遮挡请按时间分段设置并检查全程。未覆盖区域不受保护。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.beauty_enabled = QCheckBox("启用视频美颜")
        self.beauty_enabled.toggled.connect(self._beauty_changed)
        layout.addWidget(self.beauty_enabled)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([*beauty.PRESETS, "自定义"])
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        form = QFormLayout()
        form.addRow("预设", self.preset_combo)
        self.beauty_sliders = {}
        for key, label in (("smoothing", "磨皮"), ("whitening", "美白"), ("sharpening", "细节增强")):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName(key + "Slider")
            slider.setRange(0, 100)
            value_label = QLabel("0")
            slider.valueChanged.connect(lambda value, target=value_label: target.setText(str(value)))
            slider.valueChanged.connect(self._beauty_changed)
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(value_label)
            form.addRow(label, row)
            self.beauty_sliders[key] = slider
        layout.addLayout(form)
        self.preview_button = QPushButton("对比当前位置的美颜效果")
        self.preview_button.clicked.connect(self.preview_beauty)
        layout.addWidget(self.preview_button)
        group = QGroupBox("保护区域 · 坐标以画面百分比表示")
        form = QFormLayout(group)
        self.region_inputs = {}
        for key, label, value in (("x", "左侧 X %", 40), ("y", "顶部 Y %", 60),
                                  ("w", "宽度 %", 20), ("h", "高度 %", 20)):
            spin = QDoubleSpinBox()
            spin.setRange(0 if key in ("x", "y") else 0.1, 100)
            spin.setDecimals(1)
            spin.setValue(value)
            form.addRow(label, spin)
            self.region_inputs[key] = spin
        self.region_entire = QCheckBox("应用于整段视频")
        self.region_entire.setChecked(True)
        form.addRow(self.region_entire)
        self.region_start = QDoubleSpinBox()
        self.region_end = QDoubleSpinBox()
        for spin in (self.region_start, self.region_end):
            spin.setRange(0, 999999)
            spin.setDecimals(3)
            spin.setSuffix(" 秒")
            spin.setEnabled(False)
        self.region_end.setValue(10)
        self.region_entire.toggled.connect(lambda enabled: self.region_start.setEnabled(not enabled))
        self.region_entire.toggled.connect(lambda enabled: self.region_end.setEnabled(not enabled))
        form.addRow("开始", self.region_start)
        form.addRow("结束", self.region_end)
        self.add_region_button = QPushButton("添加保护框")
        self.add_region_button.clicked.connect(self.add_region)
        form.addRow(self.add_region_button)
        layout.addWidget(group)
        self.region_combo = QComboBox()
        layout.addWidget(self.region_combo)
        remove_button = QPushButton("删除选中的保护框")
        remove_button.clicked.connect(self.delete_region)
        layout.addWidget(remove_button)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        return scroll

    def _path_field(self, field, title):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        button = QPushButton("浏览")
        button.setMaximumWidth(52)
        button.clicked.connect(lambda: self._browse_path(field, title))
        layout.addWidget(field)
        layout.addWidget(button)
        return widget

    def _browse_path(self, field, title):
        filename, _ = QFileDialog.getOpenFileName(self, title, field.text())
        if filename:
            field.setText(filename)

    def _set_dirty(self):
        if not self._loading:
            self.dirty = True
            self._update_title()

    def _update_title(self):
        name = self.project_path.name if self.project_path else "未保存项目"
        self.setWindowTitle(f"{'* ' if self.dirty else ''}{name} · 夜语")

    def _refresh_project(self):
        self._loading = True
        try:
            self.video_path_label.setText(self.project.video_path or "尚未导入视频")
            self.table.setRowCount(len(self.project.cues))
            for row, cue in enumerate(self.project.cues):
                self._write_cue_row(row, cue)
            self.table.resizeRowsToContents()
            settings = self.project.beauty
            self.beauty_enabled.setChecked(bool(settings.get("enabled", False)))
            for key, slider in self.beauty_sliders.items():
                slider.setValue(int(settings.get(key, 0)))
            matching = next((name for name, preset in beauty.PRESETS.items() if all(settings.get(key, 0) == value for key, value in preset.items())), "自定义")
            self.preset_combo.setCurrentText(matching)
            self._refresh_regions()
            self._update_title()
        finally:
            self._loading = False

    def _write_cue_row(self, row, cue):
        for column, text in enumerate((_time_text(cue.start), _time_text(cue.end), cue.source, cue.translation)):
            self.table.setItem(row, column, QTableWidgetItem(text))
        item = QTableWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if cue.reviewed else Qt.CheckState.Unchecked)
        self.table.setItem(row, 4, item)

    def _refresh_regions(self):
        self.region_combo.clear()
        for index, region in enumerate(self.project.regions):
            time_text = "整段" if region.get("start") is None and region.get("end") is None else f"{region.get('start') or 0:g}–{region.get('end') if region.get('end') is not None else '末尾'} 秒"
            self.region_combo.addItem(f"{index + 1}. X={region['x'] * 100:g}% Y={region['y'] * 100:g}% W={region['w'] * 100:g}% H={region['h'] * 100:g}% · {time_text}")

    def _replace_project(self, project, project_path=None):
        self.player.stop()
        self.project = project
        self.project_path = Path(project_path) if project_path else None
        self.dirty = False
        self.player.setSource(QUrl.fromLocalFile(project.video_path) if project.video_path else QUrl())
        self._refresh_project()
        self._position_changed(0)

    def _confirm_discard(self):
        if not self.dirty:
            return True
        result = QMessageBox.question(self, "项目尚未保存", "是否先保存当前项目？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if result == QMessageBox.StandardButton.Save:
            return self.save_project()
        return result == QMessageBox.StandardButton.Discard

    def open_video(self):
        if not self._confirm_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "导入视频", "", "视频 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts);;所有文件 (*)")
        if filename:
            self._replace_project(Project(video_path=filename))
            self._set_dirty()

    def relink_video(self):
        filename, _ = QFileDialog.getOpenFileName(self, "重新关联视频（保留字幕和美颜设置）", "", "视频 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts);;所有文件 (*)")
        if filename:
            self.player.stop()
            self.project.video_path = filename
            self.player.setSource(QUrl.fromLocalFile(filename))
            self.video_path_label.setText(filename)
            self._set_dirty()
            self.statusBar().showMessage("已重新关联视频；原有字幕和美颜设置保留，请检查时间轴。")

    def open_project(self):
        if not self._confirm_discard():
            return
        filename, _ = QFileDialog.getOpenFileName(self, "打开项目", "", "夜语项目 (*.json)")
        if filename:
            try:
                project = load_project(filename)
                self._replace_project(project, filename)
                if project.video_path and not Path(project.video_path).is_file():
                    self.statusBar().showMessage("视频文件不存在；请确认原视频路径。字幕仍可编辑。")
            except Exception as error:
                self._error(str(error))

    def save_project(self):
        destination = self.project_path
        if destination is None:
            filename, _ = QFileDialog.getSaveFileName(self, "保存项目", "项目.json", "夜语项目 (*.json)")
            if not filename:
                return False
            destination = Path(filename)
            if destination.suffix.lower() != ".json":
                destination = destination.with_suffix(".json")
        try:
            save_project(self.project, destination)
            self.project_path = destination
            self.dirty = False
            self._update_title()
            self.statusBar().showMessage("项目已保存；API Key 未写入项目。")
            return True
        except Exception as error:
            self._error(str(error))
            return False

    def import_subtitles(self):
        filename, _ = QFileDialog.getOpenFileName(self, "导入日文原文 SRT", "", "SRT 字幕 (*.srt)")
        if not filename:
            return
        if self.project.cues and QMessageBox.question(self, "替换字幕", "导入将替换当前字幕，是否继续？") != QMessageBox.StandardButton.Yes:
            return
        try:
            cues = parse_srt(Path(filename).read_text(encoding="utf-8-sig"))
            self.project.cues = cues
            self._refresh_project()
            self._set_dirty()
        except Exception as error:
            self._error(str(error))

    def export_subtitles(self):
        if not self.project.cues:
            return self._error("请先识别、导入或添加字幕。")
        filename, _ = QFileDialog.getSaveFileName(self, "导出双语字幕", "双语字幕.srt", "SRT 字幕 (*.srt)")
        if filename:
            try:
                destination = Path(filename)
                if destination.suffix.lower() != ".srt":
                    destination = destination.with_suffix(".srt")
                if self.project.video_path and destination.resolve() == Path(self.project.video_path).resolve():
                    raise ValueError("字幕输出不能覆盖原视频。")
                destination.write_text(format_srt(self.project.cues, mode="bilingual"), encoding="utf-8-sig")
                self.statusBar().showMessage("双语字幕已导出。")
            except Exception as error:
                self._error(str(error))

    def _cue_changed(self, item):
        if self._loading or item.row() >= len(self.project.cues):
            return
        cue = self.project.cues[item.row()]
        column = item.column()
        try:
            if column in (0, 1):
                value = _seconds(item.text())
                if (column == 0 and value >= cue.end) or (column == 1 and value <= cue.start):
                    raise ValueError("字幕结束时间必须晚于开始时间。")
                if column == 0:
                    cue.start = value
                else:
                    cue.end = value
            elif column == 2:
                if cue.source != item.text():
                    cue.source = item.text()
                    cue.translation = ""
                    cue.reviewed = False
            elif column == 3:
                cue.translation = item.text()
                cue.reviewed = bool(cue.translation.strip())
            elif column == 4:
                cue.reviewed = item.checkState() == Qt.CheckState.Checked
            self._set_dirty()
            selected = item.row()
            self._refresh_project()
            self.table.setCurrentCell(selected, column)
            self._position_changed(self.player.position())
        except ValueError as error:
            self._refresh_project()
            self._error(str(error))

    def _cue_selected(self, row, column, previous_row, previous_column):
        if not self._loading and 0 <= row < len(self.project.cues):
            self.player.setPosition(round(self.project.cues[row].start * 1000))

    def add_cue(self):
        start = self.player.position() / 1000
        self.project.cues.append(Cue(id=uuid.uuid4().hex, start=start, end=start + 2))
        self.project.cues.sort(key=lambda cue: cue.start)
        self._refresh_project()
        self._set_dirty()

    def delete_cue(self):
        row = self.table.currentRow()
        if row >= 0:
            del self.project.cues[row]
            self._refresh_project()
            self._set_dirty()

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _position_changed(self, position):
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position)
        self.time_label.setText(_time_text(position / 1000))
        active = next((cue for cue in self.project.cues if cue.start <= position / 1000 < cue.end), None)
        self.source_label.setText(active.source if active else "")
        self.translation_label.setText(active.translation if active else "")

    def _media_error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.statusBar().showMessage("视频预览失败：" + message)

    def _provider_changed(self):
        if self._active_provider in self._provider_configs:
            self._provider_configs[self._active_provider] = (self.model_name.text(), self.base_url.text())
        provider = self.provider_combo.currentData()
        bundled = provider == "bundled"
        self.model_name.setReadOnly(bundled)
        self.base_url.setReadOnly(bundled)
        self.api_key.setEnabled(provider == "deepseek")
        if bundled:
            self.model_name.setText("bundled-translation")
            self.base_url.setText("由程序自动启动本地服务")
        else:
            model, base_url = self._provider_configs[provider]
            self.model_name.setText(model)
            self.base_url.setText(base_url)
        self.model_name.setPlaceholderText("填写已安装的本地模型名称" if provider == "ollama" else "翻译模型名称")
        self._active_provider = provider

    def _run_task(self, title, operation, on_success, *, on_partial=None):
        if self._thread is not None:
            return
        self.player.pause()
        for widget in self._busy_widgets:
            widget.setEnabled(False)
        for action in self._actions:
            action.setEnabled(False)
        if on_partial is not None:
            self.table.setEnabled(True)
            self._save_action.setEnabled(True)
        self.statusBar().showMessage(title)
        self._thread = QThread(self)
        self._worker = _Worker(operation, streaming=on_partial is not None)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.message.connect(self.statusBar().showMessage)
        self._task_callback = on_success
        self._partial_callback = on_partial
        self._worker.partial.connect(self._task_partial)
        self._worker.succeeded.connect(self._task_succeeded)
        self._worker.failed.connect(self._task_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._task_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    @Slot(object)
    def _task_partial(self, result):
        if self._partial_callback is not None:
            self._partial_callback(result)

    @Slot(object)
    def _task_succeeded(self, result):
        try:
            if self._task_callback is not None:
                self._task_callback(result)
        except Exception as error:
            self._error(str(error))

    @Slot(str)
    def _task_failed(self, message):
        if self._partial_callback is not None:
            self.statusBar().showMessage("识别中断；现有字幕及人工修改已保留，可保存项目。")
        self._error(message)

    @Slot()
    def _task_finished(self):
        self._thread.wait()
        self._thread = None
        self._worker = None
        self._task_callback = None
        self._partial_callback = None
        for widget in self._busy_widgets:
            widget.setEnabled(True)
        for action in self._actions:
            action.setEnabled(True)

    def transcribe(self):
        if self._thread is not None:
            return
        if not self.project.video_path:
            return self._error("请先导入视频。")
        if not self.whisper_model.text().strip():
            return self._error("未找到识别模型。请复制完整的 models 文件夹，或手动选择本地模型；程序路径可留空，使用 FFmpeg 内置识别（CPU）。")
        if self.project.cues and QMessageBox.question(self, "重新识别", "首次识别出对白后，会替换现有字幕及校对内容。请先保存需要保留的项目。是否继续？") != QMessageBox.StandardButton.Yes:
            return
        video = self.project.video_path
        executable, model = self.whisper_executable.text().strip(), self.whisper_model.text().strip()
        self._recognition_replacing = True
        self._run_task("正在分段识别日语…", lambda progress: pipeline.transcribe_segments(video, executable, model, progress=progress), self._recognition_complete, on_partial=self._append_recognized)

    def _append_recognized(self, cues):
        if not cues:
            return
        self._loading = True
        try:
            if self._recognition_replacing:
                self.project.cues = []
                self.table.setRowCount(0)
                self._recognition_replacing = False
            first_row = len(self.project.cues)
            self.project.cues.extend(cues)
            self.table.setRowCount(len(self.project.cues))
            # Append only: do not replace existing cells or an active edit widget.
            for row in range(first_row, len(self.project.cues)):
                self._write_cue_row(row, self.project.cues[row])
                self.table.resizeRowToContents(row)
        finally:
            self._loading = False
        self._set_dirty()
        self._position_changed(self.player.position())

    def _recognition_complete(self, result):
        self.statusBar().showMessage(f"识别完成，共 {len(self.project.cues)} 条字幕；请翻译、校对后保存。")

    def _accept_cues(self, cues):
        self.project.cues = cues
        self._refresh_project()
        self._set_dirty()
        self.statusBar().showMessage(f"已完成，共 {len(cues)} 条字幕；请校对后保存。")

    def translate(self):
        if not self.project.cues:
            return self._error("请先识别、导入或添加字幕。")
        if not self.model_name.text().strip():
            return self._error("请填写翻译模型名称。")
        provider = self.provider_combo.currentData()
        if provider == "bundled":
            if not paths.TRANSLATION_MODEL.is_file() or not paths.LLAMA_SERVER.is_file():
                return self._error("随附翻译模型或运行程序缺失。请复制完整的 models 和 runtime 文件夹，再使用项目内离线翻译；程序不会自动下载或切换到在线服务。")
        if provider == "deepseek" and not self.api_key.text().strip():
            return self._error("请在本机填写 DeepSeek API Key。")
        cues = copy.deepcopy(self.project.cues)
        model = self.model_name.text().strip()
        key = self.api_key.text().strip() if provider == "deepseek" else ""
        base_url = "" if provider == "bundled" else self.base_url.text().strip()
        glossary = self.glossary.toPlainText()
        self._run_task("正在翻译…", lambda progress: pipeline.translate_cues(cues, provider, model, api_key=key, base_url=base_url, glossary=glossary, progress=progress), self._accept_cues)

    def _apply_preset(self, name):
        if self._loading or name not in beauty.PRESETS:
            return
        settings = beauty.PRESETS[name]
        self._loading = True
        self.beauty_enabled.setChecked(bool(settings.get("enabled", True)))
        for key, slider in self.beauty_sliders.items():
            slider.setValue(settings.get(key, 0))
        self._loading = False
        self._beauty_changed()

    def _beauty_changed(self, *args):
        if not self._loading:
            self.project.beauty = {"enabled": self.beauty_enabled.isChecked(), **{key: slider.value() for key, slider in self.beauty_sliders.items()}}
            matching = next((name for name, preset in beauty.PRESETS.items() if preset == self.project.beauty), "自定义")
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentText(matching)
            self.preset_combo.blockSignals(False)
            self._set_dirty()

    def add_region(self):
        region = {key: spin.value() / 100 for key, spin in self.region_inputs.items()}
        if region["x"] + region["w"] > 1.000001 or region["y"] + region["h"] > 1.000001:
            return self._error("保护框超出画面：X＋宽度、Y＋高度均不能超过 100%。")
        if not self.region_entire.isChecked():
            start, end = self.region_start.value(), self.region_end.value()
            if end <= start:
                return self._error("保护范围结束时间必须晚于开始时间。")
            region.update(start=start, end=end)
        self.project.regions.append(region)
        self._refresh_regions()
        self._set_dirty()

    def delete_region(self):
        index = self.region_combo.currentIndex()
        if index >= 0:
            del self.project.regions[index]
            self._refresh_regions()
            self._set_dirty()

    def preview_beauty(self):
        if not self.project.video_path:
            return self._error("请先导入视频。")
        video, moment = self.project.video_path, self.player.position() / 1000
        settings, regions = copy.deepcopy(self.project.beauty), copy.deepcopy(self.project.regions)
        def operation(progress):
            import cv2
            capture = cv2.VideoCapture(video)
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, moment * 1000)
                valid, frame = capture.read()
                if not valid:
                    raise ValueError("无法读取当前位置视频帧。")
                result = beauty.process_frame(frame, settings, regions, time_seconds=moment)
                for region in regions:
                    if region.get("start") is not None and moment < region["start"]:
                        continue
                    if region.get("end") is not None and moment >= region["end"]:
                        continue
                    height, width = frame.shape[:2]
                    x, y = round(region["x"] * width), round(region["y"] * height)
                    right = round((region["x"] + region["w"]) * width)
                    bottom = round((region["y"] + region["h"]) * height)
                    cv2.rectangle(result, (x, y), (right, bottom), (255, 180, 20), 2)
                return frame, result
            finally:
                capture.release()
        self._run_task("正在生成当前帧美颜对比…", operation, self._show_preview)

    def _show_preview(self, frames):
        import cv2
        dialog = QDialog(self)
        dialog.setWindowTitle("当前帧对比 · 蓝色边框仅用于标记保护范围")
        layout = QHBoxLayout(dialog)
        for title, frame in zip(("原画", "美颜结果"), frames):
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setPixmap(QPixmap.fromImage(image).scaled(600, 650, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            column = QVBoxLayout()
            column.addWidget(QLabel(title))
            column.addWidget(label)
            layout.addLayout(column)
        self.statusBar().showMessage("预览已生成；保护框边框不会出现在导出视频中。")
        dialog.exec()

    def export_video(self):
        if not self.project.video_path:
            return self._error("请先导入视频。")
        if self.burn_subtitles.isChecked() and not self.project.cues:
            return self._error("当前没有字幕；请先生成字幕，或取消烧录字幕。")
        source = Path(self.project.video_path)
        filename, _ = QFileDialog.getSaveFileName(self, "导出视频", str(source.with_name(source.stem + "_处理.mp4")), "MP4 视频 (*.mp4)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not filename:
            return
        destination = Path(filename)
        if destination.suffix.lower() != ".mp4":
            destination = destination.with_suffix(".mp4")
        if destination.resolve() == source.resolve():
            return self._error("输出路径不能覆盖原视频。")
        if destination.exists():
            return self._error("输出文件已存在；请选择新的文件名，程序不会覆盖已有视频。")
        project, burn = copy.deepcopy(self.project), self.burn_subtitles.isChecked()
        self._run_task("正在导出视频…", lambda progress: pipeline.export_video(project, str(destination), burn_subtitles=burn, progress=progress), lambda result: self.statusBar().showMessage("视频已导出：" + str(destination)))

    def _error(self, message):
        self.statusBar().showMessage("操作未完成。")
        QMessageBox.warning(self, "操作未完成", message or "发生未知错误。")

    def closeEvent(self, event):
        if self._thread is not None:
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再关闭窗口。")
            event.ignore()
        elif self._confirm_discard():
            self.player.stop()
            self.api_key.clear()
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("夜语")
    window = MainWindow()
    window.show()
    return app.exec()
