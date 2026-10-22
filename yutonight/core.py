"""Local subtitle project data, independent of the UI and model runtimes."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import math
from pathlib import Path
import re
import tempfile

DEFAULT_BEAUTY = {"enabled": False, "smoothing": 0, "whitening": 0, "sharpening": 0}


@dataclass
class Cue:
    id: str
    start: float
    end: float
    source: str = ""
    translation: str = ""
    reviewed: bool = False


@dataclass
class Project:
    video_path: str = ""
    cues: list[Cue] = field(default_factory=list)
    beauty: dict = field(default_factory=lambda: dict(DEFAULT_BEAUTY))
    regions: list[dict] = field(default_factory=list)


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}必须是有限数字")
    return float(value)


def validate_project(project: Project) -> None:
    if not isinstance(project.video_path, str):
        raise ValueError("视频路径必须是文本")
    ids = set()
    for cue in project.cues:
        if not isinstance(cue.id, str) or not cue.id or cue.id in ids:
            raise ValueError("字幕 ID 为空或重复")
        ids.add(cue.id)
        start = _number(cue.start, "字幕开始时间")
        end = _number(cue.end, "字幕结束时间")
        if start < 0 or end <= start:
            raise ValueError("字幕时间必须满足 0 ≤ 开始 < 结束")
        if not isinstance(cue.source, str) or not isinstance(cue.translation, str):
            raise ValueError("字幕内容必须是文本")
        if not isinstance(cue.reviewed, bool):
            raise ValueError("校对状态必须是布尔值")
    if not isinstance(project.beauty, dict):
        raise ValueError("美颜配置无效")
    if set(project.beauty) - set(DEFAULT_BEAUTY):
        raise ValueError("美颜配置含未知字段")
    if not isinstance(project.beauty.get("enabled", False), bool):
        raise ValueError("美颜开关必须是布尔值")
    for name in ("smoothing", "whitening", "sharpening"):
        value = _number(project.beauty.get(name, 0), name)
        if not 0 <= value <= 100:
            raise ValueError("美颜强度必须在 0–100 范围内")
    for region in project.regions:
        if not isinstance(region, dict) or set(region) - {"x", "y", "w", "h", "start", "end"}:
            raise ValueError("保护区域配置无效")
        values = {k: _number(region.get(k), f"保护区域 {k}") for k in ("x", "y", "w", "h")}
        x, y, w, h = (values[k] for k in ("x", "y", "w", "h"))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
            raise ValueError("保护区域必须在画面范围内")
        start = _number(region.get("start", 0), "保护开始时间")
        end = region.get("end")
        if start < 0 or (end is not None and _number(end, "保护结束时间") <= start):
            raise ValueError("保护时间必须满足 0 ≤ 开始 < 结束")


def save_project(project: Project, filename: str | Path) -> None:
    validate_project(project)
    target = Path(filename).resolve()
    if project.video_path and str(target).casefold() == str(Path(project.video_path).resolve()).casefold():
        raise ValueError("项目文件不能覆盖原视频")
    payload = {"schema_version": 1, **asdict(project)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_project(filename: str | Path) -> Project:
    try:
        payload = json.loads(Path(filename).read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("不支持的项目文件版本")
        if set(payload) - {"schema_version", "video_path", "cues", "beauty", "regions"}:
            raise ValueError("项目文件含未知字段")
        cues = [Cue(**item) for item in payload.get("cues", [])]
        beauty = dict(DEFAULT_BEAUTY)
        beauty.update(payload.get("beauty", {}))
        project = Project(payload.get("video_path", ""), cues, beauty, payload.get("regions", []))
        validate_project(project)
        return project
    except (TypeError, KeyError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError("项目文件格式无效") from exc


def _parse_timestamp(text: str) -> float:
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})", text.strip())
    if not match:
        raise ValueError("SRT 时间格式无效")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def parse_srt(text: str) -> list[Cue]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2 or "-->" not in lines[0]:
            raise ValueError("SRT 条目缺少时间轴或内容")
        start_text, end_text = lines[0].split("-->", 1)
        start = _parse_timestamp(start_text)
        end = _parse_timestamp(end_text.split()[0])
        cues.append(Cue(f"cue-{len(cues) + 1:06d}", start, end, "\n".join(lines[1:]).strip()))
    validate_project(Project(cues=cues))
    return cues


def format_timestamp(seconds: float) -> str:
    if _number(seconds, "时间") < 0:
        raise ValueError("时间不能为负数")
    millis = round(seconds * 1000)
    hours, remainder = divmod(millis, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    whole, remainder = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{remainder:03d}"


def format_srt(cues: list[Cue], mode: str = "bilingual") -> str:
    if mode not in {"bilingual", "source", "translation"}:
        raise ValueError("未知字幕导出模式")
    validate_project(Project(cues=cues))
    blocks = []
    for cue in cues:
        texts = [cue.source, cue.translation] if mode == "bilingual" else [getattr(cue, mode)]
        content = "\n".join(t.strip() for t in texts if t.strip())
        # Blank lines delimit SRT entries; retain text lines without accidental entry separators.
        content = re.sub(r"\n\s*\n", "\n", content.replace("\r", ""))
        if content:
            blocks.append(f"{len(blocks) + 1}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n{content}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")
