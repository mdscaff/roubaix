#!/usr/bin/env python3
"""Record the /demo page in a browser, add TTS narration, emit an MP4.

Requires: ffmpeg on PATH, optional deps: pip install -e '.[demo]' && playwright install chromium

The demo page supports ``?record=1&duration=SECONDS`` for a seek-based animation timeline
(smooth scroll, zoom in/out, section highlights, live API reveal). Frames are captured at
24 fps and muxed with narration audio.

Environment (optional narration):
  ELEVENLABS_API_KEY   — use ElevenLabs instead of Edge TTS
  ELEVENLABS_VOICE_ID  — voice id (default: 21m00Tcm4TlvDq8ikWAM Rachel)

Outputs:
  dist/demo_narration.mp3
  dist/demo_raw.mp4
  dist/roubaix_demo.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from shutil import which

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"
SCRIPTS = REPO_ROOT / "scripts"
NARRATION_FILE = SCRIPTS / "demo_narration.txt"
DEFAULT_PORT = 8899
HOST = "127.0.0.1"
RECORD_FPS = 24


def _ffprobe_duration_seconds(path: Path) -> float | None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return float(out)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None


async def _write_narration_edge_tts(text: str, out_mp3: Path) -> None:
    import edge_tts

    voice = "en-US-GuyNeural"
    communicate = edge_tts.Communicate(text, voice=voice)
    await communicate.save(str(out_mp3))


def _write_narration_elevenlabs(text: str, out_mp3: Path) -> None:
    import httpx

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM").strip()
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
    }
    headers = {
        "xi-api-key": key,
        "accept": "audio/mpeg",
        "content-type": "application/json",
    }
    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        out_mp3.write_bytes(r.content)


def _wait_for_health(port: int, timeout_s: float = 30.0) -> None:
    url = f"http://{HOST}:{port}/healthz"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise RuntimeError(f"Server did not become healthy at {url}")


def _start_server(port: int) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--host",
            HOST,
            "--port",
            str(port),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _record_timeline(port: int, duration_s: float, out_video: Path) -> None:
    """Capture seek-based demo animation as a silent MP4."""
    from playwright.sync_api import sync_playwright

    frames_dir = DIST / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    url = f"http://{HOST}:{port}/demo?record=1&duration={duration_s:.2f}"
    total_frames = max(1, int(round(duration_s * RECORD_FPS)))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720})
        page = context.new_page()
        page.set_default_timeout(120_000)
        page.goto(url, wait_until="networkidle")
        page.wait_for_function("window.__demoRecorderReady === true")

        for frame_idx in range(total_frames):
            t = frame_idx / RECORD_FPS
            page.evaluate("(t) => window.__demoRecorder.seek(t)", t)
            page.wait_for_timeout(16)
            page.screenshot(
                path=str(frames_dir / f"frame_{frame_idx:06d}.png"),
                type="png",
                animations="disabled",
            )

        context.close()
        browser.close()

    cmd = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(RECORD_FPS),
        "-i",
        str(frames_dir / "frame_%06d.png"),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        str(out_video),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        proc.check_returncode()


def _mux_mp4(video: Path, audio: Path, out_mp4: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        proc.check_returncode()


def _ensure_path() -> None:
    """Prefer Homebrew binaries on macOS when PATH is minimal (e.g. IDE tasks)."""
    if sys.platform == "darwin":
        brew_bin = Path("/opt/homebrew/bin")
        if brew_bin.is_dir():
            os.environ["PATH"] = str(brew_bin) + os.pathsep + os.environ.get("PATH", "")


def main() -> int:
    _ensure_path()
    parser = argparse.ArgumentParser(description="Build narrated Roubaix demo video")
    parser.add_argument(
        "--tts",
        choices=("edge", "elevenlabs"),
        default="edge",
        help="Narration backend (default: edge / Microsoft Edge TTS, no API key)",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Temporary API port")
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="Keep dist/frames/ after encoding (debug)",
    )
    args = parser.parse_args()

    if not NARRATION_FILE.is_file():
        print(f"Missing narration text: {NARRATION_FILE}", file=sys.stderr)
        return 1

    text = NARRATION_FILE.read_text(encoding="utf-8").strip()
    if not text:
        print("Narration file is empty", file=sys.stderr)
        return 1

    DIST.mkdir(parents=True, exist_ok=True)
    audio_path = DIST / "demo_narration.mp3"
    raw_video = DIST / "demo_raw.mp4"
    out_mp4 = DIST / "roubaix_demo.mp4"

    print("Generating narration…")
    try:
        if args.tts == "elevenlabs":
            _write_narration_elevenlabs(text, audio_path)
        else:
            asyncio.run(_write_narration_edge_tts(text, audio_path))
    except Exception as e:
        print(f"Narration failed: {e}", file=sys.stderr)
        return 1

    duration = _ffprobe_duration_seconds(audio_path) or 55.0
    print(f"Narration duration: {duration:.1f}s — recording {int(duration * RECORD_FPS)} frames at {RECORD_FPS} fps")

    proc = _start_server(args.port)
    try:
        _wait_for_health(args.port)
        print("Recording animated demo timeline…")
        _record_timeline(args.port, duration_s=duration, out_video=raw_video)
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not args.keep_frames:
        shutil.rmtree(DIST / "frames", ignore_errors=True)

    if not which("ffmpeg"):
        print("ffmpeg not found on PATH; audio+video mux skipped.", file=sys.stderr)
        print(f"Raw video: {raw_video}\nAudio: {audio_path}", file=sys.stderr)
        return 0

    print("Muxing final MP4 with narration…")
    try:
        _mux_mp4(raw_video, audio_path, out_mp4)
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed: {e}", file=sys.stderr)
        return 1

    print(f"Done: {out_mp4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
