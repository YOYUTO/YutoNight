"""Conservative CPU face beauty and streaming video rendering.

Only detected frontal-face skin is processed. Protected regions are manual;
this module does not detect or remove mosaics and does not reshape faces.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from fractions import Fraction

import cv2
import numpy as np
from .paths import bundled_tool

PRESETS = {
    "原画": {"enabled": False, "smoothing": 0, "whitening": 0, "sharpening": 0},
    "自然": {"enabled": True, "smoothing": 25, "whitening": 12, "sharpening": 10},
    "白净": {"enabled": True, "smoothing": 40, "whitening": 35, "sharpening": 10},
    "柔和": {"enabled": True, "smoothing": 55, "whitening": 20, "sharpening": 0},
    "清晰": {"enabled": True, "smoothing": 15, "whitening": 8, "sharpening": 30},
}
_DETECTORS = threading.local()
_CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _strengths(settings):
    if not isinstance(settings.get("enabled", False), bool):
        raise ValueError("美颜开关必须是布尔值")
    values = []
    for key in ("smoothing", "whitening", "sharpening"):
        value = settings.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("美颜参数必须是 0 到 100 的有限数字")
        values.append(float(value) / 100)
    return values


def _protected_mask(shape, regions, time_seconds):
    height, width = shape[:2]
    mask = np.zeros((height, width), np.uint8)
    for region in regions:
        values = [region.get(key) for key in ("x", "y", "w", "h")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise ValueError("保护区域必须使用归一化坐标")
        x, y, w, h = values
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
            raise ValueError("保护区域必须位于画面内")
        start, end = region.get("start"), region.get("end")
        for value in (start, end):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError("保护时间必须是非负秒数")
        if start is not None and end is not None and end <= start:
            raise ValueError("保护结束时间必须大于开始时间")
        if (start is not None and time_seconds < start) or (end is not None and time_seconds >= end):
            continue
        left, top = max(0, math.floor(x * width)), max(0, math.floor(y * height))
        right, bottom = min(width, math.ceil((x + w) * width)), min(height, math.ceil((y + h) * height))
        mask[top:bottom, left:right] = 255
    return mask


def _load_face_detector():
    # OpenCV's filename reader cannot open Chinese paths on Windows. Read with
    # Python, then deserialize in memory so relocated bundles keep working.
    filename = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    storage = cv2.FileStorage(filename.read_text(encoding="utf-8"),
                              cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
    try:
        detector = cv2.CascadeClassifier()
        detector.read(storage.getFirstTopLevelNode())
    finally:
        storage.release()
    if detector.empty():
        raise RuntimeError("无法加载本地人脸检测器，请检查 OpenCV 模型文件")
    return detector


def _find_faces(frame):
    if not hasattr(_DETECTORS, "face"):
        _DETECTORS.face = _load_face_detector()
    height, width = frame.shape[:2]
    scale = min(1.0, 640 / max(height, width))
    small = cv2.resize(frame, None, fx=scale, fy=scale) if scale < 1 else frame
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    faces = _DETECTORS.face.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(36, 36))
    return [(int(x / scale), int(y / scale), int(w / scale), int(h / scale)) for x, y, w, h in faces]


def _face_mask(shape, faces):
    mask = np.zeros(shape[:2], np.uint8)
    for x, y, w, h in faces:
        # Ellipse avoids hair/background; conservative holes preserve eyes/mouth.
        cv2.ellipse(mask, (x + w // 2, y + h // 2), (int(w * .44), int(h * .48)), 0, 0, 360, 255, -1)
        for cx in (.31, .69):
            cv2.ellipse(mask, (int(x + w * cx), int(y + h * .39)), (max(1, int(w * .18)), max(1, int(h * .10))), 0, 0, 360, 0, -1)
        cv2.ellipse(mask, (x + w // 2, int(y + h * .77)), (max(1, int(w * .24)), max(1, int(h * .13))), 0, 0, 360, 0, -1)
    return mask


def process_frame(frame_bgr, settings, regions, time_seconds=0):
    """Return a new BGR uint8 frame. Protected source pixels are unchanged."""
    if not isinstance(frame_bgr, np.ndarray) or frame_bgr.dtype != np.uint8 or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError("美颜输入必须为 uint8 BGR 三通道图像")
    if not math.isfinite(time_seconds) or time_seconds < 0:
        raise ValueError("帧时间必须是非负秒数")
    smoothing, whitening, sharpening = _strengths(settings)
    protected = _protected_mask(frame_bgr.shape, regions, time_seconds)
    if not settings.get("enabled", False) or not any((smoothing, whitening, sharpening)):
        return frame_bgr.copy()
    # Do not let a protected patch influence face detection.
    detection_frame = frame_bgr.copy()
    detection_frame[protected != 0] = 127
    faces = _find_faces(detection_frame)
    if not faces:
        return frame_bgr.copy()
    face_mask = _face_mask(frame_bgr.shape, faces)
    skin = cv2.inRange(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YCrCb), (0, 133, 77), (255, 180, 135))
    valid = (protected == 0).astype(np.float32)
    source = frame_bgr.astype(np.float32)
    # Normalized convolution excludes protected pixels from all blur sampling.
    weight = cv2.GaussianBlur(valid, (9, 9), 3)
    blurred = cv2.GaussianBlur(source * valid[:, :, None], (9, 9), 3) / np.maximum(weight[:, :, None], 1e-6)
    target = source + (blurred - source) * smoothing * .85
    target += (255 - target) * whitening * .22
    target += (source - blurred) * sharpening * .35
    # Edge gating preserves real contours. The guard avoids derivative samples
    # crossing the protected patch, in addition to excluding blur sampling.
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge_gate = 1 - np.clip(cv2.magnitude(gx, gy) / 180, 0, 1)
    alpha = cv2.GaussianBlur(face_mask.astype(np.float32) / 255, (7, 7), 1.5)
    alpha *= (face_mask > 0) * (skin > 0) * edge_gate
    guard = cv2.dilate(protected, np.ones((5, 5), np.uint8))
    alpha[guard > 0] = 0
    output = np.clip(np.rint(source + (target - source) * alpha[:, :, None]), 0, 255).astype(np.uint8)
    output[protected > 0] = frame_bgr[protected > 0]
    return output


def _video_info(video_path, ffprobe):
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json", str(video_path)], capture_output=True, creationflags=_CREATE_FLAGS)
    if result.returncode:
        raise RuntimeError("无法读取视频信息")
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        if stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
            raise RuntimeError("首版美颜不支持 HDR，请先转换为 SDR 再处理")
        width, height = int(stream["width"]), int(stream["height"])
        rate = Fraction(stream.get("avg_frame_rate", "0/1"))
        if rate <= 0:
            rate = Fraction(stream.get("r_frame_rate", "0/1"))
        duration = float(data.get("format", {}).get("duration", 0))
        rotation = next((float(s["rotation"]) for s in stream.get("side_data_list", []) if "rotation" in s), float(stream.get("tags", {}).get("rotate", 0)))
        if round(rotation) % 180 == 90:
            width, height = height, width
        if width <= 0 or height <= 0 or rate <= 0:
            raise ValueError
        aspect = stream.get("sample_aspect_ratio", "1:1").replace(":", "/")
        aspect = Fraction(aspect) if aspect not in ("N/A", "0/1") else Fraction(1)
        if aspect <= 0:
            aspect = Fraction(1)
        if round(rotation) % 180 == 90:
            aspect = 1 / aspect
        return width, height, rate, max(0, duration), aspect
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        raise RuntimeError("视频没有可用的画面尺寸或帧率") from None


def _read_frame(pipe, length):
    data = bytearray()
    while len(data) < length:
        chunk = pipe.read(length - len(data))
        if not chunk:
            if data:
                raise RuntimeError("视频解码产生了不完整画面")
            return None
        data.extend(chunk)
    return data


def render_video(video_path, output_path, settings, regions, progress=lambda message: None):
    """Stream frames through the CPU filter; preserve source presentation time.

    FFmpeg normalizes VFR to source average CFR before frame processing.
    Protected pixels are exact before lossy H.264 compression, not bit-exact in
    the encoded file. Output audio is AAC; no full-video frame cache is created.
    """
    source, output = Path(video_path).resolve(), Path(output_path).resolve()
    if source == output:
        raise ValueError("不能覆盖原视频，请选择其他导出路径")
    if output.exists():
        raise FileExistsError("导出文件已存在，请选择其他路径")
    _strengths(settings)
    _protected_mask((1, 1), regions, 0)
    ffmpeg, ffprobe = bundled_tool("ffmpeg") or shutil.which("ffmpeg"), bundled_tool("ffprobe") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("请先安装 FFmpeg 和 ffprobe，并添加到 PATH")
    width, height, rate, duration, aspect = _video_info(source, ffprobe)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame_bytes = width * height * 3
    progress("开始 CPU 美颜；可变帧率视频将按原时间线转换为固定帧率")
    with tempfile.TemporaryDirectory(prefix="yutonight-beauty-", dir=output.parent) as temp_dir, tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log:
        candidate = Path(temp_dir) / "beauty.mp4"
        decoder_args = [ffmpeg, "-nostdin", "-v", "error", "-i", str(source), "-map", "0:v:0", "-vf", f"fps={rate}:start_time=0", "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        encoder_args = [ffmpeg, "-nostdin", "-v", "error", "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{width}x{height}", "-framerate", str(rate), "-i", "pipe:0", "-i", str(source), "-map", "0:v:0", "-map", "1:a:0?", "-map_metadata", "1", "-vf", f"pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar={aspect}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-y", str(candidate)]
        decoder = subprocess.Popen(decoder_args, stdout=subprocess.PIPE, stderr=decode_log, creationflags=_CREATE_FLAGS)
        encoder = None
        count = 0
        try:
            encoder = subprocess.Popen(encoder_args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encode_log, creationflags=_CREATE_FLAGS)
            while True:
                raw = _read_frame(decoder.stdout, frame_bytes)
                if raw is None:
                    break
                frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
                result = process_frame(frame, settings, regions, count / float(rate))
                encoder.stdin.write(result.tobytes())
                count += 1
                if count % max(1, round(float(rate))) == 0:
                    elapsed = count / float(rate)
                    progress(f"CPU 美颜 {min(99, int(elapsed / duration * 100))}%" if duration else f"CPU 美颜已处理 {count} 帧")
            encoder.stdin.close()
            if decoder.wait() != 0:
                raise RuntimeError("视频解码失败，请检查视频文件")
            if encoder.wait() != 0 or count == 0:
                raise RuntimeError("视频编码失败，请检查输出空间和编码器")
            os.link(candidate, output)
            progress("美颜视频处理完成")
        except BrokenPipeError:
            raise RuntimeError("视频编码中断，请检查输出空间和 FFmpeg") from None
        finally:
            decoder.stdout.close()
            for process in (decoder, encoder):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if encoder is not None and encoder.stdin is not None and not encoder.stdin.closed:
                try:
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
