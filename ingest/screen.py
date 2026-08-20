"""Screen channel: text burned into images or video frames.

Some posts carry all their content on screen and nothing in the audio - a
carousel of text cards, a silent clip over a music track. This module reads
that channel so the curator gets it alongside the voice and the description.

Everything here runs locally on CPU and costs no context: OCR is cheap enough
that it always runs, which is why there is no "should we look at the screen?"
gate to get wrong.
"""

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# One frame every N seconds when sampling a video. Text cards in short-form
# video stay up for several seconds; sampling faster mostly re-reads the same
# card, and the frame de-duplication below would drop it anyway.
DEFAULT_SAMPLE_EVERY_S = 3.0
# Above this, stop sampling. A minute-long clip yields ~20 frames; the cap is
# there so a 10-minute video cannot silently turn into a 200-image OCR run.
DEFAULT_MAX_FRAMES = 40
# Only byte-identical frames are dropped before OCR. An earlier version used a
# 64-bit average hash with a tolerance of 5 bits, which looked sensible and was
# not: an average hash measures global brightness, and swapping the text on a
# card barely moves it. On a 75 s video with 9 text cards it discarded 9 frames
# out of 11 and the screen channel came back nearly empty - silently, because
# an empty screen channel is a legitimate result for a video without text.
# Redundant text is de-duplicated after OCR instead, where the comparison is on
# the words themselves and cannot lose content.
DUPLICATE_HASH_DISTANCE = 0


@dataclass
class ScreenText:
    """What the screen channel produced."""

    text: str = ""
    frames_read: int = 0
    frames_sampled: int = 0
    cover_path: Optional[str] = None
    engine: str = ""
    ocr_s: float = 0.0
    error: Optional[str] = None
    blocks: List[str] = field(default_factory=list)


# --------------------------------------------------------------- OCR engine

_ENGINE = None


def _get_engine():
    """RapidOCR, loaded once. ONNX Runtime on CPU - no torch, no GPU needed."""
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    return _ENGINE


def _sort_reading_order(result) -> List[str]:
    """Order OCR blocks the way a human reads them, not the way they were found.

    RapidOCR returns blocks in detection order, which scrambles a sentence that
    spans several lines: "Une societe ou la magie | ne | que lors des eclipses |
    fonctionne". Sorting by vertical band then by horizontal position puts the
    words back in order. Measured on a real carousel: this is the single defect
    that made raw OCR output unusable.
    """
    if not result:
        return []

    items = []
    for entry in result:
        box, text = entry[0], entry[1]
        if not text or not str(text).strip():
            continue
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append((sum(ys) / len(ys), sum(xs) / len(xs), max(ys) - min(ys), str(text).strip()))

    if not items:
        return []

    # Group into lines: two blocks belong to the same line when their centres
    # are closer than half the height of a block. Absolute pixel thresholds
    # would break on a different resolution.
    median_height = sorted(i[2] for i in items)[len(items) // 2] or 1
    tolerance = median_height * 0.6

    items.sort(key=lambda i: i[0])
    lines = [[items[0]]]
    for item in items[1:]:
        if abs(item[0] - lines[-1][0][0]) <= tolerance:
            lines[-1].append(item)
        else:
            lines.append([item])

    ordered = []
    for line in lines:
        line.sort(key=lambda i: i[1])
        ordered.append(" ".join(i[3] for i in line))
    return ordered


def _ocr_image(path: Path) -> List[str]:
    result, _ = _get_engine()(str(path))
    return _sort_reading_order(result)


def _ocr_array(array) -> List[str]:
    result, _ = _get_engine()(array)
    return _sort_reading_order(result)


# ------------------------------------------------------------ frame sampling


def _average_hash(frame) -> int:
    """256-bit average hash, used only to spot a frame we have already read.

    16x16 rather than the usual 8x8: at 8x8 two different text cards can land
    on the same hash, and this comparison is the last thing standing between a
    new card and being silently skipped. Resolution is cheap here, a miss is
    not.
    """
    import numpy as np

    small = frame.reformat(width=16, height=16, format="gray").to_ndarray()
    mean = small.mean()
    bits = (small >= mean).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def _is_duplicate(candidate: int, seen: List[int]) -> bool:
    return any(bin(candidate ^ other).count("1") <= DUPLICATE_HASH_DISTANCE for other in seen)


def sample_frames(video_path: Path, every_s: float, max_frames: int):
    """Yield (timestamp, frame) sampled from a video on a fixed time grid.

    Every frame is decoded and one is kept every `every_s` seconds. Decoding is
    not the expensive part - 2 265 frames of a 75 s clip decode in about a
    second - the OCR is, and the time grid is what bounds it.

    Sampling only key frames was tried and dropped: TikTok lays them out every
    seven seconds or so, with no relation to when a text card changes, so the
    coverage depends on the encoder rather than on the content.
    """
    import av

    container = av.open(str(video_path))
    try:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"

        seen_hashes = []
        next_target = 0.0
        yielded = 0

        for frame in container.decode(stream):
            if frame.time is None:
                continue
            if frame.time < next_target:
                continue

            digest = _average_hash(frame)
            next_target = frame.time + every_s
            if _is_duplicate(digest, seen_hashes):
                continue

            seen_hashes.append(digest)
            yielded += 1
            yield frame.time, frame

            if yielded >= max_frames:
                return
    finally:
        container.close()


# ------------------------------------------------------------------ assembly


def _assemble(per_frame_blocks: List[List[str]]) -> tuple:
    """Flatten frames into one text, dropping lines already seen.

    Burned-in captions repeat across frames; keeping every occurrence would
    bury the actual content under its own echo.
    """
    seen = set()
    kept = []
    for blocks in per_frame_blocks:
        for line in blocks:
            key = " ".join(line.lower().split())
            if not key or key in seen:
                continue
            seen.add(key)
            kept.append(line)
    return "\n".join(kept), kept


# -------------------------------------------------------------- public API


def read_images(paths: List[Path], cover_dest: Optional[Path] = None) -> ScreenText:
    """Read a set of images (a carousel), in order."""
    started = time.perf_counter()
    per_frame = []

    try:
        for path in paths:
            per_frame.append(_ocr_image(path))
    except Exception as exc:
        return ScreenText(
            engine="rapidocr",
            frames_sampled=len(paths),
            error=f"{type(exc).__name__}: {exc}",
            ocr_s=time.perf_counter() - started,
        )

    text, blocks = _assemble(per_frame)

    cover = None
    if cover_dest and paths:
        cover = _save_cover_from_file(paths[0], cover_dest)

    return ScreenText(
        text=text,
        blocks=blocks,
        frames_read=len(per_frame),
        frames_sampled=len(paths),
        cover_path=str(cover) if cover else None,
        engine="rapidocr",
        ocr_s=time.perf_counter() - started,
    )


def read_video(
    video_path: Path,
    cover_dest: Optional[Path] = None,
    every_s: float = None,
    max_frames: int = None,
) -> ScreenText:
    """Sample a video and read the text burned into it."""
    every_s = every_s or float(os.environ.get("SCREEN_SAMPLE_EVERY_S", DEFAULT_SAMPLE_EVERY_S))
    max_frames = max_frames or int(os.environ.get("SCREEN_MAX_FRAMES", DEFAULT_MAX_FRAMES))

    started = time.perf_counter()
    per_frame = []
    sampled = 0
    cover = None

    try:
        for _timestamp, frame in sample_frames(video_path, every_s, max_frames):
            sampled += 1
            if cover_dest and cover is None:
                cover = _save_cover_from_frame(frame, cover_dest)
            per_frame.append(_ocr_array(frame.to_ndarray(format="bgr24")))
    except Exception as exc:
        return ScreenText(
            engine="rapidocr",
            frames_sampled=sampled,
            cover_path=str(cover) if cover else None,
            error=f"{type(exc).__name__}: {exc}",
            ocr_s=time.perf_counter() - started,
        )

    text, blocks = _assemble(per_frame)

    return ScreenText(
        text=text,
        blocks=blocks,
        frames_read=len(per_frame),
        frames_sampled=sampled,
        cover_path=str(cover) if cover else None,
        engine="rapidocr",
        ocr_s=time.perf_counter() - started,
    )


# ---------------------------------------------------------------- the cover

# The one file that outlives the run. Not a media cache: a single JPEG, so the
# curator can look at the title card with its own eyes when OCR mangles it -
# which is exactly what stylised, curved lettering does to it.


def _save_cover_from_file(source: Path, dest: Path) -> Optional[Path]:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        return dest
    except Exception:
        return None


def _save_cover_from_frame(frame, dest: Path) -> Optional[Path]:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        frame.to_image().save(str(dest), quality=85)
        return dest
    except Exception:
        return None
