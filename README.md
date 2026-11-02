# Toon Studio

One-command AI cartoon film pipeline. A single `studio.py` turns a JSON script
into a mixed MP4 animated short: character design, storyboard stills, animated
clips, voiceover and music — with the API keys **you** supply.

- **Images**: KIE.ai models (default `qwen3/pro-text-to-image`)
- **Video**: KIE.ai models (default `minimax-h3/image-to-video`)
- **Voices**: direct ElevenLabs (recommended) or KIE's TTS resell
- **Music**: Suno via KIE (default `V4_5`, instrumental)
- **Mix**: ffmpeg — speech-fit timeline, sidechain-ducked music, peak-limited

Works on Windows, macOS, Linux. No GPU required.

## See it in action

From one `story.json` - this entire film (stills, animation, voices, music,
captions) rendered by the pipeline:

<video src="https://github.com/aroga2/toon-studio/releases/download/v1.0.0/demo.mp4" controls muted playsinline></video>

(12-second cut of the [MoodCast Eva](examples/moodcast-eva/story.json) example
film; full version with the `.srt` sidecar is what a build produces.)

## Quickstart

New here? Start with **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)** — it
assumes zero GitHub knowledge, includes a paste-it-to-your-agent setup block,
and shows the free approve-before-you-spend workflow.

```bash
pip install elevenlabs imageio-ffmpeg
cp .env.example .env        # add your KIE_API_KEY (+ ELEVENLABS_API_KEY)
python studio.py new "a curious robot opens a bakery" --scenes 3 --out story.json
python studio.py build story.json --outdir my-film --director
```

`--director` pauses after the storyboard stills so you can approve, regenerate
(`r wider shot`) or swap (`s path/to.png`) each image before animation credits
are spent. `--producer` adds a post-assembly fix loop (re-render one clip,
re-voice one line, re-mix levels). Agents without a terminal get the same
workflow via the folder protocol: `build --stop-after stills`, review or
overwrite the stills, optionally `studio.py regen story.json <scene#> "note"`,
then re-run the build.

Two no-credit guardrails: `studio.py check story.json` (schema validation,
speech-fit report, cost quote with live balance) and
`studio.py preview story.json` (render one sample line per voice so the cast
is approved before video credits are spent).

Every stage is cached in the output dir — re-running only generates what's
missing, so iterating on one scene costs one render, not a whole film.

## Configuration

All via `.env` (see `.env.example`) or the environment:

| Variable | Default | Purpose |
|---|---|---|
| `KIE_API_KEY` | — (required) | KIE.ai key: image, video, music |
| `KIE_BASE_URL` | `https://api.kie.ai` | KIE API base |
| `KIE_IMAGE_MODEL` | `qwen3/pro-text-to-image` | stills / character refs |
| `KIE_VIDEO_MODEL` | `minimax-h3/image-to-video` | clip animation |
| `KIE_MUSIC_MODEL` | `V4_5` | Suno version |
| `ELEVENLABS_API_KEY` | — (recommended) | direct TTS |
| `ELEVENLABS_MODEL` | `eleven_v4` | TTS model |
| `TOON_BACKEND` | `higgsfield` | default backend when story doesn't say |

Per-story, `backends` in story.json overrides any primitive, e.g.:

```json
"backends": {"image": "kie", "video": "kie", "voice": "elevenlabs", "music": "kie"}
```

Characters take an optional `photo` (a real person's picture is cartoonized
first) and an ElevenLabs `voice_id` per character or per line. Scenes take an
optional `ambience` string (sound-design note for the video model).

## Using it from any AI agent

The repo is a self-contained **agent skill**: `SKILL.md` at the root tells an
agent what the tool does, how to set it up, and the model-API pitfalls that are
expensive to rediscover. Point your agent at this repo (clone, submodule, or
copy the folder) and tell it to follow `SKILL.md`.

- **Claude Code / Codex / Hermes and other SKILL.md-capable agents**: place the
  folder in your skills directory (e.g. `~/.claude/skills/toon-studio`) or just
  paste `SKILL.md` into context — the pipeline is plain `python studio.py`.
- **Any other agent**: same — the interface is two commands (`new`, `build`)
  and one JSON file.

Users always bring their own keys: the repo contains no credentials, and
`.env` is git-ignored.

## Costs (measured on KIE, November 2026)

A 3-scene, ~20 s film on the defaults costs roughly **200–260 KIE credits**
(~2 minutes of render time) plus a few thousand ElevenLabs characters.
Full table in `SKILL.md`.

## Notes & caveats

- MiniMax H3 auto-generates a soundtrack (often music with vocals). The studio
  keeps it out of the mix by default (`AMB_VOL = 0.0`); see `SKILL.md`.
- Characters are prompted to never move their lips; dialogue is always
  off-screen narration. Occasional single-clip slip-throughs: delete that
  `clipN.mp4` and re-run — renders are nondeterministic and usually comply on a
  fresh attempt.
- Generated film assets (clips, stills, audio) should live outside this repo;
  keep only `story.json` scripts under `examples/` or your own projects.

## License

MIT (see LICENSE).
