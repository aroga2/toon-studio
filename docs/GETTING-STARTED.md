# Getting Started with Toon Studio — for humans *and* their AI agents

You do **not** need to know GitHub to use this tool. Pick the path that fits you:

- **Path A — "I have an AI agent"** (ChatGPT, Claude, Kimi, Hermes, Codex, Cursor…): paste the block in section A to your agent and follow along. The agent does all the downloading and setup; you only paste two API keys when asked.
- **Path B — "No agent, just me"**: skip to section B for plain manual steps (download a ZIP, run three commands).

---

## What you need before starting

1. **A KIE API key** (required) — covers images, video and music.
   Get one at <https://kie.ai> → sign up → Dashboard → API Key. It looks like
   a long random string. Copy it somewhere safe; you will paste it once.
2. **An ElevenLabs API key** (recommended) — the voices.
   Get one at <https://elevenlabs.io> → Profile → API Keys. On the free tier
   this is enough for short films. *Toon Studio talks to ElevenLabs directly
   (not through KIE), because KIE's voice endpoint is unreliable.*
3. **Python 3.10 or newer.** Not sure? Run `python --version` (or ask your
   agent to check). If missing: <https://python.org> → Downloads → install
   with "Add python to PATH" checked.

What it costs: a typical 20-second, 3-scene film uses **~200 KIE credits**
(a few cents to tens of cents depending on plan) and a few thousand
ElevenLabs characters. The tool prints an exact quote *before* you spend
anything (see `check` below).

---

## Path A — give this to your AI agent

Copy everything in the box below and paste it to your agent in a **new
chat**, replacing `<REPO-URL>` with the link to this repository
(it looks like `https://github.com/<someone>/toon-studio`):

```text
Install and set up the "Toon Studio" AI cartoon film pipeline for me.
The repository is at: <REPO-URL>

Work step by step, and STOP to ask me before any step that spends API credits.

1. Verify I have Python 3.10+ (`python --version` or `python3 --version`).
2. Download the repo: `git clone <REPO-URL> toon-studio` (if git is missing,
   download the ZIP: GitHub page → green "Code" button → "Download ZIP" →
   unzip into a folder named toon-studio).
3. Install dependencies: `pip install elevenlabs imageio-ffmpeg`
4. In the toon-studio folder, copy `.env.example` to `.env`.
5. Ask me for my KIE API key (https://kie.ai) and my ElevenLabs API key
   (https://elevenlabs.io). Put them into `.env` as KIE_API_KEY and
   ELEVENLABS_API_KEY. NEVER print the keys back to me in chat, never commit
   them anywhere, and make sure `.env` stays git-ignored.
6. Sanity-check the install (spends NOTHING):
   `python studio.py check examples/moodcast-eva/story.json`
   It should print "schema: OK", a speech-fit table and a cost quote.
7. Show me the quote and ask if I want to run the sample film:
   `python studio.py build examples/moodcast-eva/story.json --outdir my-first-film`
   This costs the quoted KIE credits. Wait for my YES before running.
8. When it finishes, show me `my-first-film/moodcast_eva_weather_triggered_ads.mp4`
   and its `.srt` caption file, and explain how to make my own film:
   `python studio.py new "my idea here" --scenes 3 --out story.json`
   then `python studio.py check story.json` (approve the script & quote),
   then `python studio.py preview story.json` (approve the voices),
   then `python studio.py build story.json --stop-after stills` (approve the
   images; regenerate any with: python studio.py regen story.json <scene#> "note"),
   then re-run the build to finish.
Report every command you run and its output. If anything fails, stop and
explain the error in plain language before trying fixes.
```

That single block gives the agent the full workflow: **script approval →
cost approval → voice approval → stills approval → spend**. Every approval
step is free; credits are only spent when a build actually renders.

---

## Path B — manual setup (no agent)

1. **Download**: open the repository page → green **Code** button →
   **Download ZIP** → unzip to a folder, e.g. `toon-studio`.
2. **Install dependencies**: open a terminal in that folder
   (Windows: Shift+right-click in the folder → "Open in Terminal";
   Mac: Terminal app → `cd` into the folder) and run:
   ```bash
   pip install elevenlabs imageio-ffmpeg
   ```
3. **Configure**: copy `.env.example` → rename the copy to `.env`
   (Windows tip: rename via `copy .env.example .env` in the terminal —
   Explorer hides file extensions). Open `.env` in any text editor and paste:
   - your KIE key after `KIE_API_KEY=`
   - your ElevenLabs key after `ELEVENLABS_API_KEY=`
4. **Verify** (free): `python studio.py check examples/moodcast-eva/story.json`
5. **Make the sample film** (costs the quoted credits):
   ```bash
   python studio.py build examples/moodcast-eva/story.json --outdir my-first-film
   ```
   Result: `my-first-film/moodcast_eva_weather_triggered_ads.mp4` with
   burned-in captions and a matching `.srt` file.
6. **Make your own**: `python studio.py new "a kitten knight finds a golden key" --scenes 3 --out story.json` → open `story.json` in a text editor and adjust anything you like → `python studio.py build story.json --stop-after stills` to review the pictures first → re-run the same command to finish.

---

## The five commands (and what each one is for)

| Command | Costs? | Purpose |
|---|---|---|
| `python studio.py new "idea" --scenes 3` | no* | drafts a story.json (needs `OPENAI_API_KEY`; without one it writes a template to fill in) |
| `python studio.py check story.json` | **no** | validates the script, warns if dialogue is too long for a scene, **quotes the exact credit cost**, shows your balance |
| `python studio.py preview story.json` | ~free | renders one sample line per voice so you can approve the cast before spending video credits |
| `python studio.py build story.json --stop-after stills` | images only | renders character + storyboard images, then stops so you can review/overwrite/regen them |
| `python studio.py build story.json` | yes | renders whatever is missing and assembles the final MP4 (+ .srt captions) |

\* the LLM that drafts scripts is billed by your LLM provider if configured.

Useful flags: `--outdir DIR` chooses the output folder; `--director` for an
interactive still-by-still review in a real terminal; `--producer` for a
post-assembly fix loop (re-render one scene, re-voice one line, re-mix
levels); `--no-captions` if you don't want text burned into the video.

## If something goes wrong

- **"KIE_API_KEY not set" / "needs KIE_API_KEY"** — `.env` is missing, named
  wrong (must be exactly `.env`), or the key line has a typo.
- **A render fails mid-build** — just re-run the same command; finished
  stages are cached, only the missing asset regenerates.
- **A character looks wrong or wears the wrong clothes** — delete that
  `sceneN.png` (and `ref_<name>.png` if the character itself is wrong) and
  re-run the build; or use `python studio.py regen story.json <scene#> "note"`.
- **Voices sound wrong** — change the `voice_id` in story.json (ElevenLabs
  voice library: <https://elevenlabs.io/voice-library>), delete the
  `vo*.wav` files, re-run; or re-cast with `python studio.py preview story.json`.
- **Extra music/voices in the film** — some video models add their own
  soundtrack; Toon Studio silences it by default (`AMB_VOL = 0.0` in
  studio.py). If you enable it, verify each clip first.
- Full model-API details and the troubleshooting table: see `SKILL.md`.

## Ground rules for AI agents operating on a user's behalf

1. Never print, log, or commit API keys; edit `.env` only via scripts, not
   by displaying it.
2. Always run `check` and show the quote before the first `build` that
   spends credits; wait for explicit approval.
3. Use the folder protocol (`--stop-after stills`, file review, `regen`)
   for approvals when there is no interactive terminal.
4. Keep generated films out of the git repository; only story.json files
   belong in version control.
