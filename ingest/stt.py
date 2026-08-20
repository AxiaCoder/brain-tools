"""Speech-to-text transcription.

Two interchangeable backends behind a single contract:

    transcribe(audio_path, lang) -> TranscriptResult

- ``local`` (default): faster-whisper running on this machine. No key, no
  quota, no network. The device is decided at call time from the VRAM actually
  free, never hardcoded, so a busy GPU degrades to CPU instead of failing.
- ``groq``: Groq Whisper API. Kept as a fallback; needs GROQ_API_KEY.

Env knobs (all optional):

    STT_BACKEND           local | groq                 (default: local)
    WHISPER_MODEL         faster-whisper model name    (default: large-v3-turbo)
    WHISPER_DEVICE        auto | cuda | cpu            (default: auto)
    WHISPER_COMPUTE_TYPE  ctranslate2 compute type     (default: per-device)
    WHISPER_VRAM_MIN_MB   free VRAM required for cuda  (default: 1800)
    WHISPER_CPU_THREADS   threads for the CPU fallback (default: min(4, cores))
"""

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3"
GROQ_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB - Groq API limit
MAX_FILE_SIZE = GROQ_MAX_FILE_SIZE  # kept for backwards compatibility
SUPPORTED_FORMATS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac"}

DEFAULT_LOCAL_MODEL = "large-v3-turbo"
DEFAULT_VRAM_MIN_MB = 1800
DEFAULT_CPU_THREADS = 4


@dataclass
class TranscriptResult:
    text: str
    lang: str  # Detected or provided language
    duration_s: Optional[float]  # Audio duration, if the backend reports it
    # Measurement / provenance - optional, never required by callers.
    backend: str = ""
    device: str = ""
    model: str = ""
    load_s: Optional[float] = None
    transcribe_s: Optional[float] = None


# ---------------------------------------------------------------- validation


def _validate_audio(audio_path: str, max_size: Optional[int] = None) -> Path:
    """Check the file exists, is small enough and has a known extension."""
    file_path = Path(audio_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if max_size is not None:
        file_size = file_path.stat().st_size
        if file_size > max_size:
            raise ValueError(
                f"File too large (max {max_size / (1024 * 1024):.0f}MB): "
                f"{file_size / (1024 * 1024):.1f}MB"
            )

    if file_path.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported audio format: {file_path.suffix}. "
            f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )

    return file_path


# ------------------------------------------------------------ device picking


def free_vram_mb() -> Optional[int]:
    """Free VRAM on the first GPU, in MB. None if it cannot be read.

    Read at call time on purpose: the desktop, a browser or a game move this
    number by gigabytes, so a value probed at import would be a lie.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None

    if result.returncode != 0:
        return None

    lines = result.stdout.strip().splitlines()
    if not lines:
        return None

    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def pick_device() -> tuple:
    """Return (device, reason) for this call."""
    forced = os.environ.get("WHISPER_DEVICE", "auto").strip().lower()
    if forced in ("cuda", "gpu"):
        return "cuda", "forced by WHISPER_DEVICE"
    if forced == "cpu":
        return "cpu", "forced by WHISPER_DEVICE"

    try:
        vram_min = int(os.environ.get("WHISPER_VRAM_MIN_MB", DEFAULT_VRAM_MIN_MB))
    except ValueError:
        vram_min = DEFAULT_VRAM_MIN_MB

    free = free_vram_mb()
    if free is None:
        return "cpu", "no NVIDIA GPU detected"
    if free < vram_min:
        return "cpu", f"only {free}MB VRAM free (need {vram_min}MB)"
    return "cuda", f"{free}MB VRAM free"


def _cpu_threads() -> int:
    try:
        wanted = int(os.environ.get("WHISPER_CPU_THREADS", DEFAULT_CPU_THREADS))
    except ValueError:
        wanted = DEFAULT_CPU_THREADS
    return max(1, min(wanted, os.cpu_count() or wanted))


def _lower_priority() -> None:
    """Drop this process below normal priority so the desktop stays usable.

    Only called when transcribing on CPU. Best effort: never raises.
    """
    try:
        if sys.platform == "win32":
            import ctypes

            below_normal = 0x00004000
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(handle, below_normal)
        else:
            os.nice(10)
    except Exception:
        pass


# ------------------------------------------------------------- local backend

# Module-level cache: loading the model costs seconds, and a batch would
# otherwise pay it once per video. Keyed by the settings that define the model.
_MODEL_CACHE = {}


def _default_compute_type(device: str) -> str:
    override = os.environ.get("WHISPER_COMPUTE_TYPE", "").strip()
    if override:
        return override
    return "int8_float16" if device == "cuda" else "int8"


_CUDA_DLLS_REGISTERED = False


def _register_cuda_dlls() -> None:
    """Make the pip-installed cuBLAS / cuDNN DLLs findable on Windows.

    ctranslate2 needs them but ships neither. They come from the
    ``nvidia-cublas-cu12`` and ``nvidia-cudnn-cu12`` wheels, which drop their
    DLLs inside site-packages - a directory nothing searches by default.
    Without this, CUDA loads fine and then dies on the first encode with
    ``cublas64_12.dll is not found``.

    Both mechanisms are needed. ``os.add_dll_directory`` only serves loads that
    opt into LOAD_LIBRARY_SEARCH_USER_DIRS, and ctranslate2 resolves cuBLAS
    with a bare ``LoadLibrary`` that does not - it reads PATH. Registering the
    directories without also prepending PATH looks like it worked and fails
    identically at encode time.
    """
    global _CUDA_DLLS_REGISTERED
    if _CUDA_DLLS_REGISTERED or sys.platform != "win32":
        return

    try:
        import nvidia

        bin_dirs = []
        for package_root in nvidia.__path__:
            for bin_dir in sorted(Path(package_root).glob("*/bin")):
                if bin_dir.is_dir():
                    bin_dirs.append(str(bin_dir))
                    os.add_dll_directory(str(bin_dir))

        if bin_dirs:
            os.environ["PATH"] = os.pathsep.join(
                bin_dirs + [os.environ.get("PATH", "")]
            )
    except Exception:
        # Nothing to add, or no permission: the CPU fallback still applies.
        pass

    _CUDA_DLLS_REGISTERED = True


def _load_local_model(model_name: str, device: str, compute_type: str):
    key = (model_name, device, compute_type)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached, 0.0

    if device == "cuda":
        _register_cuda_dlls()

    from faster_whisper import WhisperModel

    kwargs = {"device": device, "compute_type": compute_type}
    if device == "cpu":
        kwargs["cpu_threads"] = _cpu_threads()

    started = time.perf_counter()
    model = WhisperModel(model_name, **kwargs)
    load_s = time.perf_counter() - started

    _MODEL_CACHE[key] = model
    return model, load_s


def _run_local(model_name: str, device: str, file_path: Path, lang: str = None):
    """Load the model and transcribe on one device. Returns the raw pieces.

    Kept as one unit because CUDA can fail at either step: a missing cuBLAS or
    cuDNN DLL only surfaces on the first encode, long after the model loaded
    without complaint.
    """
    model, load_s = _load_local_model(
        model_name, device, _default_compute_type(device)
    )

    if device == "cpu":
        _lower_priority()

    started = time.perf_counter()
    segments, info = model.transcribe(
        str(file_path),
        language=lang,
        # Music and silence are where Whisper invents lyrics. VAD drops them.
        vad_filter=True,
        # Short clips: never let one segment seed the next with its own noise.
        condition_on_previous_text=False,
    )
    # faster-whisper is lazy: nothing is decoded until the generator is drained,
    # so this line is where a CUDA failure actually lands.
    text = "".join(segment.text for segment in segments).strip()
    transcribe_s = time.perf_counter() - started

    return text, info, load_s, transcribe_s


def _transcribe_local(audio_path: str, lang: str = None) -> TranscriptResult:
    file_path = _validate_audio(audio_path)  # no size cap: nothing is uploaded

    model_name = os.environ.get("WHISPER_MODEL", "").strip() or DEFAULT_LOCAL_MODEL

    # A model already resident in VRAM has spent that memory: gating it on the
    # free-VRAM threshold would read its own footprint as a busy GPU and send
    # every call after the first one back to the CPU.
    cuda_key = (model_name, "cuda", _default_compute_type("cuda"))
    if cuda_key in _MODEL_CACHE:
        device = "cuda"
    else:
        device, _reason = pick_device()

    try:
        text, info, load_s, transcribe_s = _run_local(
            model_name, device, file_path, lang
        )
    except Exception as exc:
        if device == "cpu":
            raise RuntimeError(f"Local transcription failed: {exc}") from exc
        # CUDA refused (missing cuBLAS/cuDNN, OOM, driver): CPU still works.
        _MODEL_CACHE.pop((model_name, device, _default_compute_type(device)), None)
        device = "cpu"
        text, info, load_s, transcribe_s = _run_local(
            model_name, device, file_path, lang
        )

    return TranscriptResult(
        text=text,
        lang=getattr(info, "language", None) or lang or "unknown",
        duration_s=getattr(info, "duration", None),
        backend="local",
        device=device,
        model=model_name,
        load_s=load_s,
        transcribe_s=transcribe_s,
    )


# -------------------------------------------------------------- groq backend


def _transcribe_groq(audio_path: str, lang: str = None) -> TranscriptResult:
    file_path = _validate_audio(audio_path, max_size=GROQ_MAX_FILE_SIZE)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"model": WHISPER_MODEL, "response_format": "verbose_json"}
    if lang:
        data["language"] = lang

    started = time.perf_counter()
    try:
        with open(file_path, "rb") as audio_file:
            files = {"file": (file_path.name, audio_file, "audio/mpeg")}
            response = requests.post(
                GROQ_API_URL,
                headers=headers,
                files=files,
                data=data,
                timeout=60,
            )

        if response.status_code == 401:
            raise RuntimeError("Groq API authentication failed (invalid GROQ_API_KEY)")
        elif response.status_code == 429:
            raise RuntimeError("Groq API rate limit exceeded")
        elif response.status_code >= 500:
            raise RuntimeError(f"Groq API server error: {response.status_code}")
        elif response.status_code != 200:
            error_msg = response.json().get("error", {}).get("message", response.text)
            raise RuntimeError(f"Groq API error ({response.status_code}): {error_msg}")

        result = response.json()

    except requests.exceptions.Timeout:
        raise RuntimeError("Groq API request timed out (>60s)")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Groq API request failed: {e}")

    return TranscriptResult(
        text=result.get("text", ""),
        lang=result.get("language", lang or "unknown"),
        duration_s=result.get("duration"),
        backend="groq",
        device="api",
        model=WHISPER_MODEL,
        transcribe_s=time.perf_counter() - started,
    )


# ---------------------------------------------------------------- public API


def transcribe(audio_path: str, lang: str = None) -> TranscriptResult:
    """
    Transcribe an audio file.

    Args:
        audio_path: Path to audio file (mp3, m4a, wav, etc.)
        lang: Optional language hint (ISO 639-1, e.g. "fr", "en")

    Returns:
        TranscriptResult with transcribed text

    Raises:
        FileNotFoundError: Audio file doesn't exist
        ValueError: Unsupported format, or too large for the Groq backend
        RuntimeError: Backend failure (model load, missing key, rate limit...)
    """
    backend = os.environ.get("STT_BACKEND", "local").strip().lower() or "local"

    if backend == "groq":
        return _transcribe_groq(audio_path, lang)
    if backend == "local":
        return _transcribe_local(audio_path, lang)

    raise RuntimeError(
        f"Unknown STT_BACKEND: {backend!r} (expected 'local' or 'groq')"
    )
