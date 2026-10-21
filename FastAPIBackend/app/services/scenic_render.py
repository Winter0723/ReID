"""Render an immutable Scenic edit using FFmpeg, without model or cloud calls.

All selected sources are verified before any editing starts. FFmpeg errors are
fatal; encoder fallback never removes filters, audio or composition choices.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable

from app.services.ffmpeg import ffmpeg_executable
from app.services.scenic_templates import TEMPLATES, music_path, music_version
from app.services.storage import storage


class ScenicRenderError(RuntimeError):
    pass


def _run(arguments: list[str], *, cwd: Path | None = None, timeout: int = 3600) -> str:
    try:
        result = subprocess.run(
            arguments, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScenicRenderError(f"视频处理程序无法运行或超时：{exc}") from exc
    if result.returncode:
        raise ScenicRenderError(f"视频处理失败：{result.stderr.strip()[-3500:]}")
    return result.stdout


def _ffprobe(ffmpeg: str) -> str:
    adjacent = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    executable = str(adjacent) if adjacent.exists() else shutil.which("ffprobe")
    if not executable:
        raise ScenicRenderError("增强剪辑需要 FFprobe，请安装完整 FFmpeg 后重试")
    return executable


def probe_media(path: Path, ffprobe: str | None = None) -> dict:
    executable = ffprobe or _ffprobe(ffmpeg_executable() or "ffmpeg")
    result = json.loads(_run([
        executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ], timeout=120))
    video = next((stream for stream in result.get("streams", []) if stream.get("codec_type") == "video"), None)
    if not video:
        raise ScenicRenderError("素材没有可解码的图像轨道")
    duration = float(result.get("format", {}).get("duration") or video.get("duration") or 0)
    width, height = int(video["width"]), int(video["height"])
    rotation = float(video.get("tags", {}).get("rotate", 0))
    for side in video.get("side_data_list", []):
        rotation = float(side.get("rotation", rotation))
    if round(rotation) % 180:
        width, height = height, width
    return {
        "duration_seconds": duration, "width": width, "height": height,
        "rotation": rotation, "frame_rate": video.get("avg_frame_rate", "0/1"),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in result.get("streams", [])),
        "video_codec": video.get("codec_name"), "streams": result.get("streams", []),
    }


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value, name: str, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ScenicRenderError(f"{name}必须为数字") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ScenicRenderError(f"{name}超出允许范围")
    return result


def _font(work: Path) -> str:
    configured = os.getenv("SCENIC_FONT_FILE", "")
    candidates = [
        (configured, os.getenv("SCENIC_FONT_FAMILY", "Microsoft YaHei")),
        ("C:/Windows/Fonts/msyh.ttc", "Microsoft YaHei"),
        ("C:/Windows/Fonts/simhei.ttf", "SimHei"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "Noto Sans CJK SC"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf", "Noto Sans CJK SC"),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "WenQuanYi Zen Hei"),
        ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
    ]
    for filename, family in candidates:
        if filename and Path(filename).is_file():
            fonts = work / "fonts"
            fonts.mkdir(exist_ok=True)
            shutil.copyfile(filename, fonts / f"scenic{Path(filename).suffix}")
            return family
    raise ScenicRenderError("增强剪辑需要中文字体，请安装 Noto Sans CJK 或设置 SCENIC_FONT_FILE 与 SCENIC_FONT_FAMILY")


def _ass_time(seconds: float) -> str:
    hundredths = max(0, round(seconds * 100))
    hours, rest = divmod(hundredths, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, centiseconds = divmod(rest, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def _text(value, length: int = 80) -> str:
    # Reject ASS override/control syntax while retaining literal readable text.
    return str(value or "").replace("\\", "／").replace("{", "（").replace("}", "）").replace("\r", " ").replace("\n", " ")[:length]


def _subtitles(path: Path, width: int, height: int, family: str, template: dict, events: list[tuple]) -> None:
    fontsize = 58 if width > height else 48
    color = template["color"].lstrip("#")
    ass_color = f"&H00{color[4:6]}{color[2:4]}{color[0:2]}"
    header = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n"
        "\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Title,{family},{fontsize},&H00FFFFFF,{ass_color},&H65000000,&H70000000,-1,0,0,0,100,100,1,0,1,2,1,5,80,80,50,1\n"
        f"Style: Caption,{family},{int(fontsize * .62)},&H00FFFFFF,{ass_color},&H45000000,&H70000000,0,0,0,0,100,100,0,0,1,2,1,2,65,65,85,1\n"
        f"Style: Brand,{family},{int(fontsize * .48)},{ass_color},{ass_color},&H45000000,&H70000000,0,0,0,0,100,100,0,0,1,1,1,9,48,48,48,1\n"
        f"Style: Watermark,{family},{fontsize},&H75FFFFFF,{ass_color},&H75000000,&H70000000,-1,0,0,0,100,100,3,0,1,2,1,5,80,80,50,1\n"
        "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for start, end, style, value in events:
        if value and end > start:
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{{\\fad(180,180)}}{_text(value)}\n")
    path.write_text("".join(lines), encoding="utf-8-sig")


@lru_cache(maxsize=8)
def _filter_file_option(ffmpeg: str) -> str:
    # Older FFmpeg reads graphs through filter_complex_script. New releases
    # remove it in favor of the general file-argument syntax. Probe once per
    # executable, including development builds without a release version.
    help_text = _run([ffmpeg, "-hide_banner", "-h", "long"], timeout=15)
    for line in help_text.splitlines():
        if line.lstrip().startswith("-filter_complex_script "):
            return "-/filter_complex" if "-/filter_complex" in line else "-filter_complex_script"
    return "-/filter_complex"


def _encode(ffmpeg: str, inputs: list[str], options: list[str], destination: Path, work: Path) -> None:
    prefix = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-xerror", "-filter_complex_threads", "1"]
    codec = os.getenv("SCENIC_VIDEO_ENCODER", "libx264")
    if codec not in {"libx264", "h264_nvenc"}:
        raise ScenicRenderError("SCENIC_VIDEO_ENCODER 仅支持 libx264 或 h264_nvenc")
    choices = [codec, "libx264"] if codec == "h264_nvenc" else [codec]
    options = list(options)
    if "-filter_complex" in options:
        index = options.index("-filter_complex")
        # Keeping all verified tracking samples can exceed Windows' process
        # command-line limit. FFmpeg reads the exact graph from a local file;
        # software fallback consumes the same filters and audio routing.
        graph = work / f"{destination.stem}.filters"
        graph.write_text(options[index + 1], encoding="utf-8")
        options[index:index + 2] = [_filter_file_option(ffmpeg), str(graph)]
    for attempt, encoder in enumerate(choices):
        encoding = ["-c:v", encoder, "-preset", "fast", "-crf", "20"] if encoder == "libx264" else ["-c:v", encoder, "-preset", "p4", "-cq", "21"]
        try:
            _run(prefix + inputs + options + encoding + [
                "-threads", "2", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(destination),
            ], cwd=work)
            return
        except ScenicRenderError:
            destination.unlink(missing_ok=True)
            if attempt == len(choices) - 1:
                raise


def _tracking_expression(clip: dict, axis: str) -> str:
    focus = _number(clip.get(f"focus_{axis}", .5), "构图焦点", 0, 1)
    points = clip.get("trajectory") or []
    if clip.get("crop_mode") != "track" or not points:
        return f"{focus:.6f}"
    start = _number(clip["start_seconds"], "镜头入点", 0, 86400 * 7)
    # Analysis already smooths and checks these exact centers against the
    # person's box. Starting at the manual/default focus, smoothing again or
    # dropping samples can move the crop away from that verified safe path.
    normalized = sorted({
        _number(point.get("time_seconds"), "跟踪时间", 0, 86400 * 7) - start:
        _number(point.get(axis), "跟踪焦点", 0, 1)
        for point in points
    }.items())
    if len(normalized) == 1:
        return f"{normalized[0][1]:.6f}"

    def interpolate(left: int, right: int) -> str:
        if right - left == 1:
            at, value = normalized[left]
            until, target = normalized[right]
            return f"({value:.6f}+({target-value:.6f})*((t-({at:.6f}))/{until-at:.6f}))"
        middle = (left + right) // 2
        # A balanced expression retains every sample without making FFmpeg's
        # parser recurse once for every tracking observation.
        return f"if(lt(t,{normalized[middle][0]:.6f}),{interpolate(left, middle)},{interpolate(middle, right)})"

    expression = interpolate(0, len(normalized) - 1)
    first_time, first_value = normalized[0]
    last_time, last_value = normalized[-1]
    return f"if(lte(t,{first_time:.6f}),{first_value:.6f},if(gte(t,{last_time:.6f}),{last_value:.6f},{expression}))"


def _framing(clip: dict, width: int, height: int, duration: float, template: dict) -> str:
    # Convert anamorphic samples to square pixels before using their dimensions
    # for fit/focus/track. Merely setting SAR to one stretches the actual scene.
    prefix = "[0:v]setpts=PTS-STARTPTS,fps=30,scale=w='max(2,round(iw*sar/2)*2)':h=ih,setsar=1"
    mode = clip.get("crop_mode", "fit")
    if mode not in {"fit", "focus", "track"}:
        raise ScenicRenderError("无效的构图方式")
    if mode == "track" and not clip.get("trajectory"):
        mode = "fit"
    if mode == "fit":
        framing = (
            f"{prefix},split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=18:2[back];"
            f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[front];"
            "[back][front]overlay=(W-w)/2:(H-h)/2:shortest=1"
        )
    else:
        x, y = _tracking_expression(clip, "x"), _tracking_expression(clip, "y")
        framing = (
            f"{prefix},crop=w='min(iw,ih*{width}/{height})':h='min(ih,iw*{height}/{width})':"
            f"x='max(0,min(iw-ow,iw*({x})-ow/2))':y='max(0,min(ih-oh,ih*({y})-oh/2))',"
            f"scale={width}:{height}"
        )
    if clip["kind"] == "image":
        # Fit photos leave their complete foreground intact. A gentle pan of the
        # canvas is only used for explicitly focused photo compositions.
        if mode != "fit":
            zoom = float(template["photo_zoom"])
            framing += f",zoompan=z='1+{zoom}*min(on/{max(1, round(duration*30))},1)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps=30"
        else:
            # Start slightly smaller and approach full scale, preserving every
            # photo edge throughout the motion (the surrounding canvas fills it).
            framing = (
                f"{prefix},split=2[bg][fg];"
                f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=18:2[back];"
                f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"scale=w='trunc(iw*(0.97+0.03*min(t/{duration:.6f},1))/2)*2':h='trunc(ih*(0.97+0.03*min(t/{duration:.6f},1))/2)*2':eval=frame[front];"
                "[back][front]overlay=(W-w)/2:(H-h)/2:shortest=1"
            )
    return framing + ",format=yuv420p,settb=1/30[v]"


def _join(ffmpeg: str, clips: list[Path], durations: list[float], transition: float, destination: Path, work: Path, boundaries: list[float] | None = None) -> float:
    if len(clips) == 1:
        shutil.copyfile(clips[0], destination)
        return durations[0]
    inputs, graph = [], []
    for index, clip in enumerate(clips):
        inputs += ["-i", str(clip)]
        graph += [f"[{index}:v]settb=1/30,setpts=PTS-STARTPTS[v{index}]", f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]"]
    total = durations[0]
    video, audio = "v0", "a0"
    for index in range(1, len(clips)):
        dissolve = boundaries[index - 1] if boundaries is not None else min(transition, durations[index - 1] / 3, durations[index] / 3)
        next_video, next_audio = f"jv{index}", f"ja{index}"
        if dissolve > 0:
            graph += [f"[{video}][v{index}]xfade=transition=fade:duration={dissolve:.6f}:offset={total-dissolve:.6f}[{next_video}]",
                      f"[{audio}][a{index}]acrossfade=d={dissolve:.6f}:c1=tri:c2=tri[{next_audio}]"]
        else:
            graph += [f"[{video}][v{index}]concat=n=2:v=1:a=0[{next_video}]", f"[{audio}][a{index}]concat=n=2:v=0:a=1[{next_audio}]"]
        total += durations[index] - dissolve
        video, audio = next_video, next_audio
    _encode(ffmpeg, inputs, ["-filter_complex", ";".join(graph), "-map", f"[{video}]", "-map", f"[{audio}]", "-t", f"{total:.6f}"], destination, work)
    return total


def render_variant(snapshot: dict, orientation: str, output_dir: Path, progress: Callable | None = None) -> dict:
    """Render full, full-length staff draft, short visitor preview and cover.

    Progress is called with ``(fraction, message)``. Trajectory times are absolute
    source-media seconds. Source captions use ``location_name`` or ``title``;
    diagnostic camera identifiers and similarity values never enter the film.
    """
    def report(value: float, message: str):
        if progress:
            progress(value, message)

    if orientation not in {"landscape", "portrait"}:
        raise ScenicRenderError("不支持的成片画幅")
    template = TEMPLATES.get(snapshot.get("template_id", "documentary"))
    if not template or str(snapshot.get("template_version", template["version"])) != template["version"]:
        raise ScenicRenderError("剪辑模板版本不存在，请重新保存草稿")
    clips = snapshot.get("variants", {}).get(orientation) or []
    if isinstance(clips, dict):
        clips = clips.get("clips", [])
    clips = [dict(clip) for clip in clips if clip.get("included", True)]
    if not clips:
        raise ScenicRenderError("该画幅没有可生成的镜头")
    if len(clips) > 100:
        raise ScenicRenderError("单版成片最多支持 100 个镜头")
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        raise ScenicRenderError("增强剪辑需要 FFmpeg，请安装后重试")
    ffprobe = _ffprobe(ffmpeg)
    width, height = (1920, 1080) if orientation == "landscape" else (1080, 1920)
    music_volume = _number(snapshot.get("music_volume", .28), "配乐音量", 0, 1)
    original_volume = _number(snapshot.get("original_volume", .85), "原声音量", 0, 1)
    music_id = snapshot.get("music_id", template["music_id"])
    if music_id == "auto":
        music_id = template["music_id"]
    try:
        music = music_path(music_id) if music_id and music_id != "none" else None
    except (ValueError, RuntimeError, OSError) as exc:
        raise ScenicRenderError(str(exc)) from exc
    if music:
        if str(snapshot.get("music_version", music_version(music_id))) != music_version(music_id):
            raise ScenicRenderError("提交版本引用的配乐版本不可用")
        if snapshot.get("music_sha256") and _hash(music) != snapshot["music_sha256"]:
            raise ScenicRenderError("提交版本引用的配乐已发生变化，请重新保存草稿")
    verified, cache = [], {}
    report(.03, "正在校验冻结素材与剪辑资源")
    for clip in clips:
        if clip.get("kind") not in {"image", "video"}:
            raise ScenicRenderError("剪辑素材类型无效")
        key = str(clip.get("object_key") or "")
        if not key or not clip.get("source_sha256"):
            raise ScenicRenderError("提交快照缺少素材路径或指纹，请重新保存草稿")
        try:
            if key not in cache:
                source = Path(storage.path_for(key))
                if not source.is_file():
                    raise ScenicRenderError("素材文件不存在")
                cache[key] = (source, _hash(source), probe_media(source, ffprobe))
            source, digest, metadata = cache[key]
        except Exception as exc:
            raise ScenicRenderError(f"已提交的镜头 {clip.get('id', '')} 不可用：{exc}") from exc
        if digest != clip["source_sha256"]:
            raise ScenicRenderError(f"已提交的镜头 {clip.get('id', '')} 已发生变化，无法生成原版本")
        start = _number(clip.get("start_seconds", 0), "镜头入点", 0, 86400 * 7)
        end = _number(clip.get("end_seconds", 0), "镜头出点", 0, 86400 * 7)
        if end <= start or end - start > 120:
            raise ScenicRenderError("镜头时长必须大于零且不超过 120 秒")
        if clip["kind"] == "video" and (metadata["duration_seconds"] <= 0 or end > metadata["duration_seconds"] + .04):
            raise ScenicRenderError("已提交镜头的入出点超出实际素材时长，请重新保存草稿")
        # Quantize at the common output frame rate, avoiding cumulative xfade
        # offsets at fractional frames for variable-frame-rate inputs.
        duration = max(1 / 30, round((end - start) * 30) / 30)
        verified.append((clip, source, metadata, duration))
    if sum(item[3] for item in verified) > 600:
        raise ScenicRenderError("单版成片时长不能超过 10 分钟")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scenic_render_", dir=output_dir) as temporary:
        work = Path(temporary)
        family = _font(work)
        segments, durations = [], []
        for index, (clip, source, metadata, duration) in enumerate(verified):
            report(.08 + .48 * index / len(verified), f"正在处理镜头 {index + 1}/{len(verified)}")
            inputs = ["-loop", "1", "-framerate", "30", "-i", str(source)] if clip["kind"] == "image" else ["-ss", str(clip["start_seconds"]), "-i", str(source)]
            use_audio = bool(clip.get("keep_audio")) and metadata["has_audio"] and clip["kind"] == "video"
            if not use_audio:
                inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_index = 0 if use_audio else 1
            graph = _framing(clip, width, height, duration, template)
            graph += f";[{audio_index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,apad,atrim=duration={duration:.6f}"
            if use_audio:
                graph += f",loudnorm=I=-20:TP=-2:LRA=11,volume={original_volume:.4f},afade=t=in:d=0.06,afade=t=out:st={max(0, duration-.1):.6f}:d=0.1"
            graph += "[a]"
            segment = work / f"segment_{index:03}.mp4"
            _encode(ffmpeg, inputs, ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{duration:.6f}"], segment, work)
            segments.append(segment)
            durations.append(duration)
        report(.58, "正在编排转场与完整时间线")
        transition = float(template["transition_seconds"])
        joined = work / "joined.mp4"
        # Bounded fan-in avoids opening every source decoder simultaneously.
        # Keep original first/last durations at group boundaries so very short
        # edge clips cannot acquire an oversized dissolve during a later pass.
        first_durations, last_durations = list(durations), list(durations)
        while len(segments) > 6:
            grouped, group_durations, group_first, group_last = [], [], [], []
            for index in range(0, len(segments), 6):
                target = work / f"group_{len(segments)}_{index}.mp4"
                stop = min(index + 6, len(segments))
                boundaries = [min(transition, last_durations[item] / 3, first_durations[item + 1] / 3) for item in range(index, stop - 1)]
                group_durations.append(_join(ffmpeg, segments[index:stop], durations[index:stop], transition, target, work, boundaries))
                grouped.append(target)
                group_first.append(first_durations[index])
                group_last.append(last_durations[stop - 1])
            segments, durations = grouped, group_durations
            first_durations, last_durations = group_first, group_last
        boundaries = [min(transition, left / 3, right / 3) for left, right in zip(last_durations, first_durations[1:])]
        total = _join(ffmpeg, segments, durations, transition, joined, work, boundaries)
        events = []
        title = snapshot.get("title") or "美好旅程"
        events.append((.25, min(3.5, total * .4), "Title", title))
        events.append((max(total - 2.5, total * .7), total - .15, "Title", snapshot.get("ending_title") or "珍藏这一程"))
        if snapshot.get("brand"):
            events.append((0, total, "Brand", snapshot["brand"]))
        cursor = 0.0
        cover_candidates = {}
        for index, (clip, _, _, duration) in enumerate(verified):
            cover_candidates[str(clip["id"])] = cursor + duration / 2
            location = clip.get("location_name") or clip.get("title") or ""
            date = str(clip.get("recorded_at") or "")[:10]
            caption = " · ".join(value for value in (location, date) if value)
            if caption:
                events.append((cursor + .15, min(total, cursor + duration - .15), "Caption", caption))
            dissolve = min(transition, duration/3, verified[index+1][3]/3) if index+1 < len(verified) else 0
            cursor += duration - dissolve
        _subtitles(work / "film.ass", width, height, family, template, events)
        report(.68, "正在制作配乐、字幕和正式成片")
        inputs = ["-i", str(joined)]
        graph = "[0:v]subtitles=filename=film.ass:fontsdir=fonts[v]"
        if music and music_volume > 0:
            inputs += ["-stream_loop", "-1", "-i", str(music)]
            graph += (
                f";[1:a]atrim=duration={total:.6f},asetpts=PTS-STARTPTS,loudnorm=I=-19:TP=-2:LRA=8,volume={music_volume:.4f},"
                f"afade=t=in:d=0.6,afade=t=out:st={max(0,total-1.2):.6f}:d=1.2[music];"
                "[0:a]asplit=2[original][side];[music][side]sidechaincompress=threshold=0.035:ratio=5:attack=30:release=350[ducked];"
                "[original][ducked]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.94:level=false[a]"
            )
        else:
            graph += ";[0:a]alimiter=limit=0.94:level=false[a]"
        full = work / "full.mp4"
        _encode(ffmpeg, inputs, ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{total:.6f}", "-metadata", f"title={_text(title)}"], full, work)
        report(.82, "正在生成工作人员预览、游客试看与封面")
        draft = work / "draft_preview.mp4"
        _encode(ffmpeg, ["-i", str(full)], ["-vf", "scale=960:540" if orientation == "landscape" else "scale=540:960", "-map", "0:v:0", "-map", "0:a:0"], draft, work)
        visitor_duration = min(8.0, max(.2, total * .35))
        _subtitles(work / "visitor.ass", width, height, family, template, [(0, visitor_duration, "Watermark", "游客试看 · 待领取")])
        preview = work / "preview.mp4"
        scale = "960:540" if orientation == "landscape" else "540:960"
        _encode(ffmpeg, ["-i", str(full)], ["-vf", f"subtitles=filename=visitor.ass:fontsdir=fonts,scale={scale}", "-t", f"{visitor_duration:.6f}"], preview, work)
        cover = work / "cover.jpg"
        cover_clip_id = str(snapshot.get("cover_clip_id") or "")
        if cover_clip_id not in cover_candidates:
            cover_clip_id = str(verified[0][0]["id"])
        requested_time = snapshot.get("cover_time_seconds")
        cover_time = _number(requested_time if requested_time is not None else cover_candidates[cover_clip_id], "封面时间", 0, max(total - 1/30, 0))
        _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{cover_time:.6f}", "-i", str(full), "-frames:v", "1", "-q:v", "2", str(cover)], cwd=work)
        report(.93, "正在校验成片图像、音轨与时长")
        metadata = probe_media(full, ffprobe)
        if metadata["width"] != width or metadata["height"] != height or not metadata["has_audio"] or metadata["video_codec"] != "h264":
            raise ScenicRenderError("成片画幅、编码或音轨校验未通过")
        if abs(metadata["duration_seconds"] - total) > .25:
            raise ScenicRenderError("成片时长与冻结时间线不一致")
        _run([ffmpeg, "-v", "error", "-xerror", "-i", str(full), "-f", "null", "-"], timeout=3600)
        if not cover.is_file() or cover.stat().st_size == 0:
            raise ScenicRenderError("封面生成失败")
        result = {"duration_seconds": metadata["duration_seconds"], "cover_clip_id": cover_clip_id, "cover_time_seconds": cover_time}
        for key, source in [("full_path", full), ("preview_path", preview), ("draft_preview_path", draft), ("cover_path", cover)]:
            destination = output_dir / source.name
            source.replace(destination)
            result[key] = destination
        report(1.0, "成片已完成")
        return result
