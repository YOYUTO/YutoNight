"""Offscreen desktop workflow checks; no model downloads or external services."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtCore import QThread, Qt
    from PySide6.QtWidgets import QApplication, QFileDialog
    from yutonight import paths
    from yutonight.core import Cue, Project, load_project, save_project
    from yutonight.ui import MainWindow
except ModuleNotFoundError as error:
    raise unittest.SkipTest("桌面测试需要安装 PySide6 和 OpenCV") from error


class DesktopWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.errors = []
        self.window._error = self.errors.append
        self.window.project = Project(cues=[Cue("one", 1, 3, "こんにちは", "你好")])
        self.window._refresh_project()

    def tearDown(self):
        if self.window._thread is not None:
            self.wait_for_task()
        self.window.dirty = False
        self.window.close()
        self.app.processEvents()

    def wait_for_task(self):
        deadline = time.monotonic() + 5
        while self.window._thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(self.window._thread, "后台任务未正常结束")

    def test_manual_correction_is_reviewed_and_survives_save(self):
        self.window.table.item(0, 3).setText("您好")
        self.assertTrue(self.window.project.cues[0].reviewed)
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "project.json"
            self.window.api_key.setText("FAKE_TEST_KEY_NOT_A_CREDENTIAL")
            save_project(self.window.project, destination)
            restored = load_project(destination)
            self.assertEqual(restored.cues[0].translation, "您好")
            self.assertTrue(restored.cues[0].reviewed)
            self.assertNotIn("FAKE_TEST_KEY_NOT_A_CREDENTIAL", destination.read_text(encoding="utf-8"))

    def test_source_correction_invalidates_old_translation(self):
        self.window.project.cues[0].reviewed = True
        self.window._refresh_project()
        self.window.table.item(0, 2).setText("こんばんは")
        cue = self.window.project.cues[0]
        self.assertEqual(cue.source, "こんばんは")
        self.assertEqual(cue.translation, "")
        self.assertFalse(cue.reviewed)

    def test_invalid_timing_is_rejected_without_changing_cue(self):
        self.window.table.item(0, 0).setText("00:00:05,000")
        self.assertEqual(self.window.project.cues[0].start, 1)
        self.assertEqual(self.window.table.item(0, 0).text(), "00:00:01,000")
        self.assertEqual(len(self.errors), 1)

    def test_add_and_delete_cue_keep_unique_ids(self):
        self.window.add_cue()
        self.assertEqual(len(self.window.project.cues), 2)
        self.assertEqual(len({cue.id for cue in self.window.project.cues}), 2)
        self.window.table.setCurrentCell(0, 0)
        self.window.delete_cue()
        self.assertEqual(len(self.window.project.cues), 1)

    def test_preset_and_custom_values_round_trip(self):
        self.window.preset_combo.setCurrentText("自然")
        self.assertTrue(self.window.project.beauty["enabled"])
        self.assertEqual(self.window.project.beauty["smoothing"], 25)
        self.window.beauty_sliders["smoothing"].setValue(31)
        self.assertEqual(self.window.preset_combo.currentText(), "自定义")
        self.window._refresh_project()
        self.assertEqual(self.window.preset_combo.currentText(), "自定义")
        self.assertEqual(self.window.beauty_sliders["smoothing"].value(), 31)

    def test_protection_bounds_and_interval_validation(self):
        self.window.region_inputs["x"].setValue(95)
        self.window.region_inputs["w"].setValue(20)
        self.window.add_region()
        self.assertEqual(self.window.project.regions, [])
        self.window.region_inputs["x"].setValue(40)
        self.window.add_region()
        self.assertEqual(len(self.window.project.regions), 1)
        self.assertAlmostEqual(self.window.project.regions[0]["x"], 0.4)
        self.window.region_entire.setChecked(False)
        self.window.region_start.setValue(4)
        self.window.region_end.setValue(2)
        self.window.add_region()
        self.assertEqual(len(self.window.project.regions), 1)

    def test_open_ended_protection_is_displayable(self):
        self.window.project.regions = [{"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "start": 3, "end": None}]
        self.window._refresh_project()
        self.assertIn("末尾", self.window.region_combo.currentText())

    def test_relink_video_preserves_subtitles(self):
        original = self.window.project.cues[0]
        with patch.object(QFileDialog, "getOpenFileName", return_value=("D:/moved/video.mp4", "")):
            self.window.relink_video()
        self.assertEqual(self.window.project.video_path, "D:/moved/video.mp4")
        self.assertIs(self.window.project.cues[0], original)
        self.assertTrue(self.window.dirty)

    def test_subtitles_are_plain_text(self):
        self.window.project.cues[0].translation = "<b>字面标签</b>"
        self.window._position_changed(1500)
        self.assertEqual(self.window.translation_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(self.window.translation_label.text(), "<b>字面标签</b>")

    def test_existing_export_is_not_overwritten_or_started(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            target = Path(directory) / "target.mp4"
            source.write_bytes(b"source")
            target.write_bytes(b"existing")
            self.window.project.video_path = str(source)
            with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                self.window.export_video()
            self.assertEqual(target.read_bytes(), b"existing")
            self.assertIsNone(self.window._thread)
            self.assertTrue(self.errors)

    def test_transcribe_allows_empty_executable_for_cpu_fallback(self):
        self.window.project = Project(video_path="D:/video.mp4")
        self.window._refresh_project()
        self.window.whisper_executable.clear()
        self.window.whisper_model.setText("D:/models/ggml-small.bin")
        with patch.object(self.window, "_run_task") as run_task, patch("yutonight.ui.pipeline.transcribe_segments", return_value=iter([]), create=True) as transcribe:
            self.window.transcribe()
            self.assertEqual(run_task.call_count, 1)
            operation = run_task.call_args.args[1]
            list(operation(lambda message: None))
            self.assertEqual(transcribe.call_args.args[:3], ("D:/video.mp4", "", "D:/models/ggml-small.bin"))
        self.assertFalse(self.errors)

    def test_segment_results_are_editable_and_saved_before_recognition_finishes(self):
        first_visible, continue_second = threading.Event(), threading.Event()
        self.window.whisper_model.setText("D:/fake-test-model.bin")
        self.window.project = Project(video_path="D:/video.mp4")
        self.window._refresh_project()
        callback_threads = []
        original_append = getattr(self.window, "_append_recognized", lambda cues: None)
        def receive(cues):
            callback_threads.append(QThread.currentThread() == self.app.thread())
            original_append(cues)
            first_visible.set()
        def segments(*args, **kwargs):
            yield [Cue("first", 0, 2, "おはよう")]
            continue_second.wait(3)
            yield [Cue("second", 30, 32, "こんにちは")]
        try:
            with patch("yutonight.ui.pipeline.transcribe_segments", side_effect=segments, create=True), patch.object(self.window, "_append_recognized", side_effect=receive):
                self.window.transcribe()
                deadline = time.monotonic() + 2
                while not first_visible.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertTrue(first_visible.is_set(), "首段未在识别完成前显示")
                self.assertIsNotNone(self.window._thread)
                self.assertTrue(self.window.table.isEnabled())
                self.assertTrue(next(a for a in self.window._actions if a.text() == "保存项目").isEnabled())
                self.assertFalse(self.window.settings_tabs.isEnabled())
                self.window.table.item(0, 3).setText("早上好（人工校对）")
                with tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "partial.json"
                    with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
                        self.assertTrue(self.window.save_project())
                    self.assertEqual(load_project(target).cues[0].translation, "早上好（人工校对）")
                continue_second.set()
                self.wait_for_task()
        finally:
            continue_second.set()
        self.assertEqual(callback_threads, [True, True])
        self.assertEqual(len(self.window.project.cues), 2)
        self.assertEqual(self.window.project.cues[0].translation, "早上好（人工校对）")
        self.assertTrue(self.window.project.cues[0].reviewed)
        self.assertTrue(self.window.dirty, "后续片段到达后应提示再次保存")
        self.assertFalse(self.errors)

    def test_segment_append_preserves_an_uncommitted_cell_editor(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLineEdit
        self.window.show()
        self.window.activateWindow()
        self.window.table.setFocus()
        self.window.table.setCurrentCell(0, 3)
        self.window.table.editItem(self.window.table.item(0, 3))
        self.app.processEvents()
        editor = self.window.table.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        editor.setFocus()
        self.app.processEvents()
        self.assertTrue(editor.hasFocus())
        editor.setText("尚未提交的校对")
        self.window._append_recognized([Cue("second", 30, 32, "こんにちは")])
        self.app.processEvents()
        self.assertIs(self.window.table.findChild(QLineEdit), editor)
        self.assertEqual(editor.text(), "尚未提交的校对")
        self.assertTrue(editor.hasFocus())
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertEqual(self.window.project.cues[0].translation, "尚未提交的校对")
        self.assertTrue(self.window.project.cues[0].reviewed)
        self.assertEqual(len(self.window.project.cues), 2)

    def test_later_segment_failure_keeps_completed_results(self):
        self.window.whisper_model.setText("D:/fake-test-model.bin")
        self.window.project = Project(video_path="D:/video.mp4")
        def segments(*args, **kwargs):
            yield [Cue("first", 0, 2, "おはよう")]
            raise RuntimeError("第二段失败")
        with patch("yutonight.ui.pipeline.transcribe_segments", side_effect=segments, create=True):
            self.window.transcribe()
            self.wait_for_task()
        self.assertEqual([c.id for c in self.window.project.cues], ["first"])
        self.assertEqual(self.errors, ["第二段失败"])
        self.assertTrue(self.window.dirty)
        self.assertTrue(self.window.table.isEnabled())

    def test_failure_before_first_speech_preserves_previous_corrections(self):
        from PySide6.QtWidgets import QMessageBox
        self.window.whisper_model.setText("D:/fake-test-model.bin")
        self.window.project.video_path = "D:/video.mp4"
        self.window.project.cues[0].reviewed = True
        def segments(*args, **kwargs):
            yield []
            raise RuntimeError("识别失败")
        with patch("yutonight.ui.pipeline.transcribe_segments", side_effect=segments, create=True), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.transcribe()
            self.wait_for_task()
        self.assertEqual(self.window.project.cues[0].id, "one")
        self.assertTrue(self.window.project.cues[0].reviewed)
        self.assertFalse(self.window.dirty)
        self.assertEqual(self.errors, ["识别失败"])

    def test_transcribe_requires_a_model(self):
        self.window.project = Project(video_path="D:/video.mp4")
        self.window.whisper_model.clear()
        with patch.object(self.window, "_run_task") as run_task:
            self.window.transcribe()
            run_task.assert_not_called()
        self.assertEqual(len(self.errors), 1)

    def test_project_bundled_translation_is_default_and_read_only(self):
        self.assertEqual(self.window.provider_combo.currentData(), "bundled")
        self.assertEqual(self.window.model_name.text(), "bundled-translation")
        self.assertTrue(self.window.model_name.isReadOnly())
        self.assertTrue(self.window.base_url.isReadOnly())
        self.assertFalse(self.window.api_key.isEnabled())

    def test_custom_provider_choices_survive_switching(self):
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("ollama"))
        self.window.model_name.setText("example-local-model")
        self.window.base_url.setText("http://127.0.0.1:12345")
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("deepseek"))
        self.assertEqual(self.window.model_name.text(), "deepseek-chat")
        self.assertTrue(self.window.api_key.isEnabled())
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("bundled"))
        self.assertFalse(self.window.api_key.isEnabled())
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("ollama"))
        self.assertEqual(self.window.model_name.text(), "example-local-model")
        self.assertEqual(self.window.base_url.text(), "http://127.0.0.1:12345")
        self.assertFalse(self.window.model_name.isReadOnly())

    def test_missing_bundled_files_do_not_start_or_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(paths, "TRANSLATION_MODEL", root / "missing.gguf"), patch.object(paths, "LLAMA_SERVER", root / "missing.exe"), patch.object(self.window, "_run_task") as run_task:
                self.window.translate()
                run_task.assert_not_called()
        self.assertEqual(self.window.provider_combo.currentData(), "bundled")
        self.assertEqual(len(self.errors), 1)
        self.assertIn("models", self.errors[0])
        self.assertIn("runtime", self.errors[0])

    def test_bundled_translation_uses_managed_endpoint_without_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model, server = root / "model.gguf", root / "llama-server.exe"
            model.touch()
            server.touch()
            self.window.api_key.setText("FAKE_TEST_KEY_NOT_A_CREDENTIAL")
            with patch.object(paths, "TRANSLATION_MODEL", model), patch.object(paths, "LLAMA_SERVER", server), patch.object(self.window, "_run_task") as run_task, patch("yutonight.ui.pipeline.translate_cues", return_value=[]) as translate:
                self.window.translate()
                self.assertEqual(run_task.call_count, 1)
                operation = run_task.call_args.args[1]
                operation(lambda message: None)
                self.assertEqual(translate.call_args.args[1:3], ("bundled", "bundled-translation"))
                self.assertEqual(translate.call_args.kwargs["api_key"], "")
                self.assertEqual(translate.call_args.kwargs["base_url"], "")
        self.assertFalse(self.errors)

    def test_startup_consumes_portable_paths_independent_of_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "moved-package"
            model = root / "models" / "asr" / "ggml-large-v3-turbo-q5_0.bin"
            executable = root / "runtime" / "whisper" / "whisper-cli.exe"
            model.parent.mkdir(parents=True)
            executable.parent.mkdir(parents=True)
            model.touch()
            executable.touch()
            with patch.object(paths, "PROJECT_ROOT", root), patch.object(paths, "ASR_MODEL", model), patch.object(paths, "bundled_tool", return_value=str(executable)) as bundled_tool, patch.object(Path, "cwd", return_value=Path(directory) / "unrelated-cwd"):
                relocated = MainWindow()
                try:
                    self.assertEqual(Path(relocated.whisper_model.text()), model)
                    self.assertEqual(Path(relocated.whisper_executable.text()), executable)
                    bundled_tool.assert_called_with("whisper-cli")
                finally:
                    relocated.dirty = False
                    relocated.close()

    def test_deepseek_still_requires_an_explicit_key(self):
        self.window.provider_combo.setCurrentIndex(self.window.provider_combo.findData("deepseek"))
        with patch.object(self.window, "_run_task") as run_task:
            self.window.translate()
            run_task.assert_not_called()
        self.assertEqual(len(self.errors), 1)

    def test_task_stays_busy_until_native_thread_cleanup_finishes(self):
        operation_allowed = threading.Event()
        native_cleanup_done = threading.Event()
        def operation(progress):
            operation_allowed.wait(2)
            return "done"
        def native_tail():
            # Real finished signal handlers can run before native thread teardown.
            time.sleep(0.2)
            native_cleanup_done.set()
        self.window._run_task("测试", operation, lambda result: None)
        self.window._thread.finished.connect(native_tail, Qt.ConnectionType.DirectConnection)
        operation_allowed.set()
        self.wait_for_task()
        self.assertTrue(native_cleanup_done.is_set(), "UI released the worker before native thread cleanup finished")

    def test_background_result_is_applied_on_main_thread(self):
        results = []
        def receive(value):
            results.append((value, QThread.currentThread() == self.app.thread()))
        self.window._run_task("测试", lambda progress: "完成", receive)
        self.assertFalse(self.window.table.isEnabled())
        self.wait_for_task()
        self.assertEqual(results, [("完成", True)])
        self.assertTrue(self.window.table.isEnabled())
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
