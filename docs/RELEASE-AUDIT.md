# Toon Studio — Pre-GitHub Audit, UX Proposals & RocketToons Feature Checklist

Date: 2026-11-02 · Scope: `toon-studio-skill/` (studio.py 1,110 lines, SKILL.md, README.md, .env.example, example story) · Auditor: K3.1 Pro pass over the full source.

---

## 1. Audit findings

### 1.1 Bugs (fix before publishing)

| # | Severity | Finding |
|---|---|---|
| B1 | **High** | **Dead code in `still_params()` (studio.py:733-743).** The prompt string is built *before* the `if img is not None:` block, and that block only reassigns the local `extra` variable — the "Match the reference character's face… do NOT copy the outfit" instruction **never reaches the prompt**. The wardrobe change we shipped yesterday worked only because qwen3 happened to follow the clothing described in the scene text. Fix: assemble `extra` first, then build the prompt string. |
| B2 | **High** | **Retry path strips the safety rules.** `video()` retries with `_simplify(prompt)` — the first sentence of the motion text only — so a retried clip loses `NO_SPEAK` and `AUDIO_FX`. A failed-then-retried render can come back with lip-flap or its own music (the exact bugs we spent 200 credits fixing). Fix: append `NO_SPEAK + AUDIO_FX` after simplifying. |
| B3 | Med | **`new` misuses the KIE key.** With `OPENAI_API_KEY` unset, `new_story()` sends the **KIE API key** to `api.moonshot.ai/v1/chat/completions` → guaranteed 401 with an ugly traceback. Fix: use the KIE key against KIE's LLM endpoint if one exists, else require `OPENAI_API_KEY` and fall back to the hand-edit template. |
| B4 | Med | **No story.json schema validation.** A missing `scenes`/`lines`/`text` key crashes with a raw `KeyError` deep in a stage that may already have spent credits. Fix: validate up front with friendly errors. |
| B5 | Low | Dead code/style: `total_ms` unused (studio.py:934); `video()` retry has an if/else with identical branches (studio.py:477-480); ambience filter chains are built even when `AMB_VOL = 0` (harmless, wasted graph); `_upload` cache is not thread-safe (benign: duplicate uploads, no corruption observed). |
| B6 | Low | **Concat fragility:** `concat` assumes uniform resolution across clips. A swapped external still (`s path.png`) with odd dimensions, or a model returning unexpected size, will break or silently rescale. Fix: add `scale=…:force_original_aspect_ratio=decrease,pad=…` normalization before concat. |

### 1.2 Design observations (not bugs)

- **Idempotency is the standout strength** — delete an artifact, re-run, only that regenerates. This is the property every workflow proposal below builds on.
- Interactive `--director` / `--producer` loops read stdin: fine in a terminal, but **agents (Claude Code, Codex, Hermes) have no interactive stdin** — the loops hit EOF and auto-accept. The approval workflows need file-based equivalents (see P0-2/P0-3).
- `assemble()` speech-fit warns when lines don't fit a scene (`MAX_TEMPO` 1.35) but proceeds — speech can spill over a scene cut. Acceptable for now; flag in `check`.
- Single global music bed per film; per-scene music exists in the data model only via one `music_prompt`.
- Lip-sync is deliberately *avoided* (NO_SPEAK + off-screen narration) rather than solved — a defensible scope cut, but it must be stated plainly in the README so users aren't surprised.

---

## 2. Workflow easiness proposals (prioritized)

### P0 — ship before/at GitHub (small, high value)

1. **Fix B1 + B2 + B3 + B4.** Correctness is UX: nothing erodes trust like a silent bad render.
2. **`studio.py check story.json`** — one command, no credits:
   - schema validation with human-readable errors,
   - **speech-fit report**: estimated line duration vs. scene duration per scene (catches the overflow before render),
   - **cost quote**: table of planned renders × current per-model rates (measured constants) + KIE balance lookup — the "credit protection / approve before job" feature from RocketToons Agent, parity.
3. **Non-interactive approvals ("folder protocol")** — make the review loops work for agents and GUI-less users:
   - `build --stop-after stills` → renders stage 1-2, prints: *"review `sceneN.png` in the outdir; overwrite any file or run `studio.py regen <story> <N> 'note'`, then re-run the same build command."* Re-running continues via idempotency — no new machinery needed beyond the stop flag and the `regen` helper.
   - Same for the script itself: `new` already writes story.json for hand-editing; add `studio.py review story.json` rendering a numbered, human-readable script (scenes, lines, durations, estimated cost) so editing the JSON is guided.
4. **Voice casting preview** — `studio.py preview story.json`: renders only the *first line of each distinct voice* to `preview_<name>.mp3` (a few hundred ElevenLabs characters). Users approve the cast before any video credits are spent. (We did this manually for Eva with the Hope/Sarah A/B.)

### P1 — next iteration

5. **Voiced animatic** — `studio.py animatic story.json`: stills + voices + music, crossfaded into a preview MP4. Approving the *story* costs zero video credits (RocketToons parity: "approve stills & a voiced animatic before animating").
6. **Subtitles** — export `film.srt` from the timeline (start times are already computed in `assemble()`). RocketToons caption styles parity, near-zero cost, big for social use.
7. **Takes** — before re-render, move existing `clipN.mp4` to `clipN_takeM.mp4`; `studio.py use-take <N> <M>` swaps back (RocketToons "keep 6 takes, switch free" parity).
8. **Per-scene music/ambience overrides** — optional `music_prompt` per scene; mix crossfades between beds.
9. **Story edit helper** — `studio.py set-line <scene> <line> "new text"` rewrites story.json and deletes exactly the dependent artifacts (that voice file, optionally its clip), making script edits safe without hand-tracking files.

### P2 — later

10. `xfade` transitions (RocketToons 9/11 transitions parity — ffmpeg-native).
11. `studio.py clip <image> "motion"` — one-shot photo-to-video (RocketToons Studio parity).
12. Burned-in animated captions (needs per-word timing from ElevenLabs `with-timestamps` API).
13. Style presets (the "8 styles" menu) as named shortcuts over the free-text `style` field.
14. Real lip-sync (KIE/Runway-style talking-head) — deliberately out of scope; the NO_SPEAK design is the budget answer.

---

## 3. RocketToons Bundle — feature coverage checklist

| Bundle feature | Status in Toon Studio | Notes |
|---|---|---|
| **Commercial** | | |
| Complete cartoon films from one prompt/script | ✅ | `new` (LLM script) + `build` |
| Photo → cartoon character | ✅ | `photo` on a character; cartoonized first |
| Characters consistent across scenes | ✅ | reference-image pipeline (qwen3 + ref) |
| Multiple visual styles | ◐ | free-text `style`; no preset menu (P2-13) |
| Custom characters/locations/props | ✅ | characters yes; locations/props via scene text (single ref image limit) |
| AI scripts, formats, idea presets | ◐ | LLM `new` from any brief; no format/occasion library |
| Series / next-episode planning | ❌ | just run `new` again; no series memory |
| **Approve stills before animating** | ✅ | `--director` (terminal) / `--stop-after stills` (proposed P0-3) |
| **Voiced animatic approval** | ❌ | proposed P1-5 |
| Animate from stills | ✅ | still = first frame, verified (that's "what you saw is what you get") |
| Direct shots: camera, action, sound, duration | ◐ | motion + duration + ambience per scene; no camera grammar library |
| @-mention characters per shot | ✅ | per-scene `characters` list (same intent) |
| 40 voices w/ auto casting | ◐ | any ElevenLabs voice_id; keyword fallback guess; no roster UI (P0-4) |
| Fix one line without redoing scene | ✅ | `v <scene> <line>` in producer pass (or delete vo file + rebuild) |
| 54 languages | ◐ | multilingual TTS handles it; no language picker/QA |
| Keep takes per shot | ❌ | proposed P1-7 |
| Music tracks / transitions / zoom-pan / captions | ◐ | Suno bed (1/film), no transitions or captions yet (P1-6, P2-10/12) |
| 16:9 / 9:16 / 1:1 exports | ✅ | `aspect_ratio` passthrough (verified 16:9; others untested) |
| 1-click social posting | ❌ | out of scope (local tool; BYO publishing) |
| Client watch pages | ❌ | out of scope |
| HD MP4, no watermark, commercial use | ✅ | your renders, your license (verify model ToS) |
| **Elite** | | |
| Unlimited films / 15-min films / storage | ✅ n/a | local pipeline: unlimited, your disk |
| Content calendar, batch publish, analytics | ❌ | out of scope |
| AI captions/hashtags | ❌ | SRT proposed P1-6; hashtags n/a |
| **Pro** | | |
| Pro video engine w/ clip audio | ◐ | H3 "audio" exists but is silenced by default (its music/vocals fight the bed) |
| Pro image quality | ◐ | model-choice (nano-banana-pro option) |
| Cinematic styles | ◐ | style presets proposed P2-13 |
| Hollywood transitions | ❌ | proposed P2-10 |
| AI-composed scores | ✅ | Suno per film; per-scene proposed P1-8 |
| 30 pro voices / Voiceover Studio word-timing | ◐ | voice_id any; no word-timing UI (P2-12 needs it) |
| Multi-track timeline editor | ❌ | out of scope (ffmpeg mix only) |
| 1080p + codec choice | ✅ | `video_resolution` (H3: 2K at 1080p+) |
| Priority queue / team seats | ✅ n/a | your API accounts |
| **Studio** | | |
| Instant clip from one sentence | ❌ | proposed P2-11 |
| Animate any photo / extend video / clip chaining | ❌ | extend = re-render w/ last frame (manual); chaining manual |
| Product photo → video ad | ✅ | story with product photo character |
| Reaction/meme/talking-character clips | ❌ | talking characters = lip-sync, out of scope by design |
| Camera styles, per-clip model choice | ◐ | model per film (env), not per clip |
| **Agent** | | |
| Control from ChatGPT/Claude/any agent | ✅ | this skill (SKILL.md) — *is* the Agent tier |
| Auto scripts / characters / clip prompts by chat | ✅ | `new` + story editing |
| Storyboards in chat | ◐ | files in outdir; chat rendering depends on host |
| Credit quote before job | ❌ | proposed P0-2 (`check`) |
| Switch takes / export from chat | ◐ | file-based; no chat-native take switch (P1-7) |
| **Reseller / bonuses** | | |
| Reseller accounts, dashboard | ❌ | out of scope (nothing to resell — BYO keys) |
| Kids/bible/viral prompt libraries | ❌ | could ship as `prompts/` folder later |
| Founders' pass / storage / team | ✅ n/a | MIT, local, yours |

**Score: core film-making ✅ (script → consistent characters → approved stills → animation → voices → music → mixed MP4, agent-operable); social/editor/reseller tiers ❌ by design; biggest gaps vs. bundle: animatic approval, takes, captions, transitions, credit quote.**

---

## 4. Recommendation

1. Land P0 items 1-4 (bugfixes, `check`, folder-protocol approvals, voice preview) **before the GitHub push** — they're small and they're the features users will photograph.
2. Add this audit's section 3 as `docs/CHECKLIST.md` in the repo for transparency.
3. Push as public `toon-studio`; roadmap P1/P2 as GitHub issues.
