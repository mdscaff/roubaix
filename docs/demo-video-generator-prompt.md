# Agent prompt: narrated product demo video

Use this prompt with Claude Code, Cursor, Codex, or any coding agent when you want a **narrated MP4 product demo** from a live web page—not a static screenshot slideshow.

Copy everything inside the block below as the task prompt. Replace `{PROJECT}` placeholders before sending.

---

## Prompt (copy from here)

```markdown
You are building a narrated product demo video pipeline for `{PROJECT_NAME}`.

## Goal

Produce `dist/{project_slug}_demo.mp4`: a ~60–120s MP4 with:
1. Professional TTS narration synced to visuals
2. Smooth scroll, zoom in/out, and section highlights—not a static frame
3. A live interaction beat (API call, form submit, or feature toggle) with progressive reveal
4. Reproducible CLI: one command rebuilds the video after copy or UI changes

## Non-negotiable architecture

Do NOT use Playwright `record_video_dir` as the primary capture method. It often yields a frozen-looking video when the page has long idle waits.

Use this pipeline instead:

```
narration.txt → TTS (mp3) → ffprobe duration
                         ↓
              demo page ?record=1&duration=SECONDS
                         ↓
              window.__demoRecorder.seek(t) each frame
                         ↓
              Playwright screenshots @ 24 fps
                         ↓
              ffmpeg → silent mp4 → mux with narration → final mp4
```

## Deliverables

1. **`scripts/demo_narration.txt`** — plain-text voiceover (~150–250 words). Structure:
   - Hook (what it is)
   - Problems / pain (2–4 beats)
   - Solution / outcomes (measurable claims only)
   - Live demo beat (what the viewer will see happen)
   - Roadmap honesty (what is not shipped yet)
   - Close

2. **`static/demo.html`** (or project demo route) with:
   - Normal interactive mode (unchanged UX)
   - Record mode when `?record=1&duration={seconds}`:
     - `window.__demoRecorderReady = true`
     - `window.__demoRecorder.seek(t)` — idempotent; sets scroll, zoom, focus, and UI state for time `t` in seconds
   - `#demo-viewport` wrapper for `transform: translateY() scale()` pan/zoom
   - `.record-focus` on active section; `.record-active` on list items / metrics
   - Prefetch live API response during record mode; typewriter-reveal JSON by `reveal` fraction (0–1)
   - Hide chrome not needed on video (theme toggle, etc.)

3. **`scripts/build_demo_video.py`** — CLI:
   - `--tts edge|elevenlabs` (Edge = no API key; ElevenLabs = `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`)
   - Starts temp server on `{PORT}` (default 8899), waits for `/healthz`
   - Generates narration mp3 → reads duration → captures `duration × 24` frames
   - Encodes `dist/demo_raw.mp4`, muxes `dist/{project_slug}_demo.mp4`
   - Requires: `ffmpeg`, `playwright install chromium`, optional `[demo]` extra (`playwright`, `edge-tts`, `httpx`)

4. **Keyframe timeline** in demo page JS:
   - Array of `{ t, scroll, zoom, originY, focus, … }` keyframes as fractions of total duration
   - Interpolate with easeInOut between keyframes
   - Map narration beats to visual beats (problems → outcomes → feature list → live demo → zoom on result → zoom out)
   - Never pad with >3s of identical frame at one scroll position

5. **Docs**: short README section with rebuild command.

## Keyframe timing template (adjust fractions to narration)

| Narration beat        | Time fraction | Visual                                      |
|-----------------------|---------------|---------------------------------------------|
| Hook / hero           | 0.00–0.10     | Zoom in on title                            |
| Problem 1…N           | 0.10–0.28     | Pan to problems; highlight each bullet      |
| Outcomes / metrics    | 0.28–0.44     | Pan grid; pulse active metric cards         |
| Deep feature / phase  | 0.44–0.56     | Zoom section; highlight feature bullets     |
| Live demo intro       | 0.56–0.62     | Pan to demo; pulse CTA button               |
| API / interaction     | 0.62–0.72     | "Calling…" → typewriter JSON reveal         |
| Telemetry / proof     | 0.72–0.85     | Zoom into result block                      |
| Close                 | 0.85–1.00     | Zoom out to full page                       |

## Record-mode seek API (required contract)

```javascript
window.__demoRecorder = {
  seek(t) { /* apply visual state for t seconds */ }
};
window.__demoRecorderReady = true;
```

Playwright capture loop:

```python
for frame_idx in range(int(duration * 24)):
    t = frame_idx / 24
    page.evaluate("(t) => window.__demoRecorder.seek(t)", t)
    page.screenshot(path=frames_dir / f"frame_{frame_idx:06d}.png")
```

## Quality checklist before declaring done

- [ ] Extract 4 frames at 0%, 33%, 66%, 100% — MD5 hashes must differ
- [ ] Final MP4 duration matches narration (±1s)
- [ ] No single visual state holds >5s unless narration requires it
- [ ] Live beat shows real server response (prefetched), not hardcoded JSON
- [ ] Interactive `/demo` still works without `?record=1`
- [ ] `dist/` gitignored; narration script committed

## Rebuild command

```bash
uv run --extra demo python scripts/build_demo_video.py --tts elevenlabs
# or --tts edge for free Microsoft Edge TTS
```

## Roubaix reference implementation

If working inside the Roubaix repo, read before editing:
- `scripts/build_demo_video.py`
- `scripts/demo_narration.txt`
- `static/demo.html` (search `recordMode`, `__demoRecorder`, `buildKeyframes`)
```

---

## Minimal one-liner trigger

> Build a narrated product demo video for this project using seek-based Playwright screenshots at 24fps, TTS narration, and ffmpeg mux—not Playwright video recording. Follow `docs/demo-video-generator-prompt.md`.
