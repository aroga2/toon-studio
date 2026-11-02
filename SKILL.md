---
name: toon-studio
description: One-command AI cartoon film pipeline. Turns a story.json script into a fully mixed animated short - character reference, storyboard stills, animated clips, voiceover, and music - using the user's own KIE.ai and (optionally) ElevenLabs API keys. Use when the user wants to create an animated cartoon video, explainer, story or ad with AI models, or to fix/re-render parts of one.
---

# Toon Studio

A single-file Python pipeline (`studio.py`, stdlib + `elevenlabs` SDK only) that
produces a mixed MP4 cartoon film from a JSON script. Runs on Windows, macOS and
Linux. No GPU, no accounts beyond the API keys the user supplies.

## Setup (do this once per machine)

1. Python 3.10+.
2. `pip install elevenlabs imageio-ffmpeg` (ffmpeg binary ships with
   imageio-ffmpeg; override with the `FFMPEG` env var if desired).
3. Copy `.env.example` to `.env` and fill in:
   - `KIE_API_KEY` (required) - from https://kie.ai - covers image, video and
     music generation.
   - `ELEVENLABS_API_KEY` (recommended) - from https://elevenlabs.io - direct
     voice generation. KIE also resells ElevenLabs TTS, but that endpoint has
     recurring outages; the default story routes voice to the direct backend.
   - Model picks default to the budget/quality sweet spot measured 2026-11:
     `qwen3/pro-text-to-image` (stills, ~6.4 credits), `minimax-h3/image-to-video`
     (clips, ~48 credits / 6 s), Suno `V4_5` (music bed). Any KIE model id can be
     substituted via `KIE_IMAGE_MODEL` / `KIE_VIDEO_MODEL` / `KIE_MUSIC_MODEL`.

## Workflow

1. **Script.** Write a `story.json` (see `examples/moodcast-eva/story.json` and
   the header docstring in `studio.py`): title, style, characters (name, design,
   voice description, optional `photo` to cartoonize a real person, optional
   ElevenLabs `voice_id`), scenes (description, motion, duration 4-15 s,
   dialogue lines with speaker), music_prompt.
   Or draft one with an LLM:
   `python studio.py new "a kitten knight finds a golden key" --scenes 3 --out story.json`
2. **Build.**
   `python studio.py build story.json --outdir my-film`
   Stages: character reference -> storyboard stills -> animation -> voiceover ->
   music + mix. Every stage is cached; re-running only generates what's missing.
3. **Direct (optional, recommended).** Add `--director` to pause after the
   storyboard stills: view each image, then accept, regenerate with a note
   (`r wider shot`), or swap in your own image (`s path.png`) before animation
   credits are spent. In an agent without interactive stdin, use the folder
   protocol instead: `build --stop-after stills`, review/overwrite
   `sceneN.png` in the outdir (or `python studio.py regen story.json
   --outdir DIR <scene#> "director's note"`), then re-run the same build.
4. **Producer (optional).** Add `--producer` for a post-assembly fix loop:
   `r 2` re-renders scene 2's clip, `v 2 1` re-voices line 1 of scene 2,
   `m 0.2` re-mixes with a louder music bed, `a` accepts.
5. **Check first (no credits).** `python studio.py check story.json`
   validates the schema, reports speech fit per scene (estimated speech
   seconds vs. the scene window), and quotes the KIE credit cost of the
   missing stages plus the current balance - approve before spending.
6. **Approve the cast (cheap).** `python studio.py preview story.json`
   renders the first line of each distinct voice to
   `preview_<speaker>.mp3` so the user can approve voices before any video
   credits are spent.

Every build also writes `<film>.srt` next to the MP4 - a caption sidecar
with each line's exact placement on the film timeline (upload it with the
video, or style it on the platform).

## Hard-won model facts (don't re-derive)

- KIE task API: `POST {KIE_BASE_URL}/api/v1/jobs/createTask` with
  `{"model", "input": {...}}`, poll `GET /api/v1/jobs/recordInfo?taskId=...`;
  success = `successFlag == 1`; result at `data.response.resultUrls[0]`. Upload
  local files to `https://kieai.redpandaai.co/api/file-base64-upload` (JSON
  base64), NOT to api.kie.ai.
- Reference images: nano-banana family reads `image_input` (a list of URLs);
  the field `image_urls` is silently ignored and produces off-brief characters.
- MiniMax H3 (`minimax-h3/image-to-video`): input takes `first_frame_url`,
  integer duration 4-15, resolution `768P` or `2K`. It natively generates its
  OWN soundtrack - often full music with vocals - which the studio silences by
  default (`AMB_VOL = 0.0` in studio.py). Raise it only for renders you have
  verified are clean sound effects.
- Hailuo (`hailuo/2-3-image-to-video-pro`): `image_url`, string duration
  ("6"/"10"), resolution `768P`/`1080P`.
- Seedance (`bytedance/seedance-2-fast`): `first_frame_url`, integer duration,
  `generate_audio: false`.
- Suno music via KIE: `POST /api/v1/generate` (a placeholder `callBackUrl` is
  required; poll `GET /api/v1/generate/record-info?taskId=...` for
  `status == SUCCESS`, audio at `response.sunoData[0].audioUrl`).
- All motion prompts carry two hard rules (constants `NO_SPEAK`, `AUDIO_FX` in
  studio.py): characters never move their lips (off-screen dialogue otherwise
  reads as broken lip-sync) and no music/voices in clip soundtracks.

## Backends

story.json `"backends"` routes each primitive independently:
`{"image": "kie", "video": "kie", "voice": "elevenlabs", "music": "kie"}`.
Available: `kie` (image/video/music/voice), `elevenlabs` (voice only, direct
API), `higgsfield` (alternative CLI backend). Default for everything is
`higgsfield` unless `TOON_BACKEND` or story `backend` says otherwise.

## Cost transparency (measured on KIE, 2026-11)

| Model | Cost | Notes |
|---|---|---|
| qwen3/pro-text-to-image | ~6.4 credits/img | 1344x768, refs work, ~60 s |
| z-image | ~0.8 credits/img | cheaper, lower fidelity |
| nano-banana-pro | ~18 credits/img | premium quality |
| minimax-h3/image-to-video | ~48-56 credits / 6 s | fast (~90 s), makes own audio |
| hailuo/2-3 i2v pro | ~45 credits / 6 s | ~130 s |
| seedance-2-fast | ~148.8 credits / 6 s | premium motion |
| Suno V4_5 | ~24 credits/song | full song, mix trims it |

ElevenLabs voices: plan characters (e.g. eleven_v4 ~ a few hundred chars/line).
Check `GET /api/v1/chat/credit` for the KIE balance; the build prints credits
left after every run.

## Troubleshooting

- **Characters look wrong / ignore the reference**: check the reference actually
  uploaded (log shows the task id); nano-banana needs `image_input`, not
  `image_urls`.
- **Extra music or voices appear in the film**: clip-level soundtrack from H3 or
  similar - keep `AMB_VOL = 0.0`, or re-render the clip (it is nondeterministic).
- **Lip-flap over narration**: the NO_SPEAK rule usually holds; if one clip
  slips through, delete that `clipN.mp4` and re-run the build (a fresh render
  usually complies), or re-render with `--producer` and a note.
- **KIE voice tasks fail with 500**: route `voice` to `elevenlabs` (direct).
- **A generation failed mid-build**: just re-run the same command; finished
  stages are reused, only the missing asset regenerates.
