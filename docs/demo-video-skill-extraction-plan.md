# Extraction plan: Demo Reel → standalone repo & publishable skill

Turn the Roubaix narrated-demo pipeline into **`demo-reel`** (working name): a small, project-agnostic toolkit any coding agent can install to generate CEO-ready product demo videos from a web page.

---

## 1. Problem statement

Teams ship features but not demos. Existing options fail in predictable ways:

| Approach | Failure mode |
|----------|----------------|
| Screen recording (Loom, manual) | Not reproducible; drifts from product |
| Playwright `record_video_dir` | Long idle waits → looks like a static image |
| Slide decks | Not live; no API proof |
| Generic video APIs | No tie-in to the actual app UI |

**Demo Reel** solves: *narration + seek-synced animation + live server beat + one-command rebuild*.

---

## 2. Product scope

### In scope (v1)

- CLI: `demo-reel build` → MP4
- Pluggable TTS: Edge (free), ElevenLabs, OpenAI (future)
- Generic demo page contract (`__demoRecorder.seek`)
- Keyframe timeline helper (JSON or JS generator)
- Frame capture @ configurable FPS
- ffmpeg encode + mux
- Example app (minimal FastAPI + static HTML)
- Agent skill (`SKILL.md`) + prompt template

### Out of scope (v1)

- Roubaix-specific routing/Cognee logic
- Cloud render farm / CI SaaS
- Non-web demos (terminal-only, mobile native)
- Auto-narration from git diff (v2 candidate)

---

## 3. What to extract from Roubaix

| Asset | Action |
|-------|--------|
| `scripts/build_demo_video.py` | Generalize → `demo_reel/cli.py` |
| `static/demo.html` record mode | Split → `examples/demo-page/` + `templates/demo-recorder.js` |
| `scripts/demo_narration.txt` | Move to example; ship `templates/narration.txt` |
| `docs/demo-video-generator-prompt.md` | Becomes skill `reference/prompt.md` |
| Roubaix `/demo` content | Stays in Roubaix; consumes demo-reel as dependency |

**Roubaix after extraction:** thin wrapper—project narration, keyframes, demo HTML content; calls `demo-reel build --config roubaix.demo.yaml`.

---

## 4. Proposed repo structure

```
demo-reel/
├── README.md
├── LICENSE (MIT)
├── pyproject.toml              # package: demo-reel
├── demo_reel/
│   ├── __init__.py
│   ├── cli.py                  # demo-reel build | init | validate
│   ├── config.py               # Pydantic DemoReelConfig
│   ├── tts/
│   │   ├── edge.py
│   │   ├── elevenlabs.py
│   │   └── base.py
│   ├── capture/
│   │   ├── playwright_frames.py
│   │   └── ffmpeg.py
│   ├── server.py               # optional: wrap uvicorn/static server
│   └── timeline/
│       ├── schema.py           # Keyframe model
│       └── validate.py         # frame hash diff check
├── templates/
│   ├── narration.txt
│   ├── demo.html.stub          # inject sections + recorder hook
│   ├── demo-recorder.js        # seek engine (project fills keyframes)
│   └── config.example.yaml
├── examples/
│   └── minimal-fastapi/
│       ├── app.py
│       ├── static/demo.html
│       ├── narration.txt
│       └── demo-reel.yaml
├── skill/
│   ├── SKILL.md                # Cursor / Claude Code skill
│   ├── prompt.md               # copy-paste agent prompt
│   └── examples.md
├── docs/
│   ├── architecture.md
│   ├── keyframe-authoring.md
│   └── publishing.md
└── tests/
    ├── test_timeline.py
    ├── test_config.py
    └── test_frame_diff.py      # ensures capture is not static
```

---

## 5. Configuration model (`demo-reel.yaml`)

```yaml
project_name: Roubaix
output: dist/roubaix_demo.mp4

server:
  command: ["uvicorn", "app.api.main:app", "--port", "{port}"]
  health_url: /healthz
  demo_url: /demo?record=1&duration={duration}

narration:
  file: scripts/demo_narration.txt
  tts: elevenlabs  # edge | elevenlabs

capture:
  fps: 24
  viewport: [1280, 720]

outputs:
  dir: dist
  keep_frames: false
```

Agents run `demo-reel init` to scaffold this in a new repo.

---

## 6. Skill packaging (multi-tool)

Publish the same core instructions in formats each ecosystem expects:

| Platform | Artifact | Install path |
|----------|----------|--------------|
| **Cursor** | `skill/SKILL.md` | Copy to `~/.cursor/skills/demo-reel/` or `.cursor/skills/` |
| **Claude Code** | `CLAUDE.md` snippet + `skill/SKILL.md` | User adds to project or plugin |
| **Codex / OpenAI** | `AGENTS.md` section + `skill/prompt.md` | Reference in repo AGENTS.md |
| **Generic** | `skill/prompt.md` | Paste into any chat |

### SKILL.md frontmatter (draft)

```yaml
---
name: demo-reel
description: >-
  Builds narrated MP4 product demo videos from a web page using seek-synced
  Playwright frame capture, TTS narration, and ffmpeg. Use when the user asks
  for a demo video, product walkthrough video, narrated screencast, or CEO demo.
---
```

### Skill workflow (agent steps)

1. Read `demo-reel.yaml` or run `demo-reel init`
2. Author/update `narration.txt` and keyframe timeline
3. Ensure demo page implements `__demoRecorder.seek(t)`
4. Run `demo-reel build`
5. Run `demo-reel validate` (frame diff + duration check)
6. Report output path

---

## 7. Extraction phases

### Phase 0 — Document & freeze (done in Roubaix)

- [x] `docs/demo-video-generator-prompt.md`
- [x] Working reference impl in Roubaix
- [x] Published standalone repo: https://github.com/mdscaff/demo-reel

### Phase 1 — Extract core library (1–2 days)

- [x] Create `demo-reel` repo — https://github.com/mdscaff/demo-reel
- Move generalized capture/TTS/ffmpeg code
- Add Pydantic config + CLI
- Port tests (frame diff, duration mux)
- CI: pytest + ruff on 3.11/3.12

### Phase 2 — Templates & init (1 day)

- `demo-reel init` scaffolds config, narration stub, demo HTML with recorder hook
- `templates/demo-recorder.js` — keyframe engine extracted from Roubaix
- Minimal FastAPI example that passes validate

### Phase 3 — Skill publish (0.5 day)

- [x] Finalize `skill/SKILL.md` and root `SKILL.md`
- [x] `skill/prompt.md` = agent copy-paste prompt
- [x] README with install instructions for Cursor + Claude Code
- [x] GitHub release v0.1.0 with example MP4

### Phase 4 — Roubaix consumer (0.5 day)

- Replace Roubaix `build_demo_video.py` with thin wrapper calling `demo-reel`
- Roubaix-only: content (HTML sections, narration, keyframes)
- Verify `dist/roubaix_demo.mp4` unchanged within visual tolerance

### Phase 5 — v1.1 enhancements (backlog)

- OpenAI TTS backend
- Keyframe authoring CLI (`demo-reel timeline lint`)
- Narration duration → auto-suggest keyframe fractions
- GitHub Action: `demo-reel build` on release tag
- Optional MCP tool: `build_demo_video(config_path)`

---

## 8. Publishing checklist

- [x] PyPI package `demo-reel` (optional; install via git for now)
- [x] GitHub repo `mdscaff/demo-reel` (public)
- [ ] README demo GIF (3s clip from example MP4)
- [x] License: MIT
- [x] Skill install one-liner in README:
  ```bash
  git clone https://github.com/mdscaff/demo-reel ~/.cursor/skills/demo-reel
  ```
- [x] Version tag `v0.1.0` with example MP4 attached to Release

---

## 9. Success metrics

| Metric | Target |
|--------|--------|
| Time to first MP4 (new project) | <30 min with skill |
| Rebuild after copy change | <3 min |
| Static-frame test | 4 sample frames ≠ same hash |
| Agent success rate | Agent completes build without manual ffmpeg debugging |
| Roubaix regression | Visual parity with current demo |

---

## 10. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| ffmpeg not installed | CLI preflight with clear install hint |
| Playwright browsers missing | `demo-reel doctor` runs `playwright install chromium` |
| Keyframe/narration drift | `validate` warns when keyframe `t` max ≠ narration duration |
| Project-specific server | Configurable `server.command` + health URL |
| Skill too long for context | Progressive disclosure: SKILL.md → reference/*.md |

---

## 11. Immediate next step

Create the `demo-reel` repo and land Phase 1 with a straight copy-refactor of:

- `scripts/build_demo_video.py` → `demo_reel/capture/` + `demo_reel/cli.py`
- Record-mode JS → `templates/demo-recorder.js`
- `docs/demo-video-generator-prompt.md` → `skill/prompt.md`

Roubaix keeps its product demo content; it becomes the **reference consumer**, not the framework host.
