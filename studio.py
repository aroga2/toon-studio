#!/usr/bin/env python3
"""
Toon Studio - one-command AI cartoon film pipeline.

Stages (mirrors a RocketToons-style production flow):
  1. Character reference images (one per character)     [Nano Banana 2]
  2. Storyboard stills per scene, character-consistent  [Nano Banana 2 + ref]
  3. Animation per still (start frame = approved still)  [Seedance 2.0]
  4. Voiceover per line, silence-trimmed + loudness-norm [Seed Audio 1.0]
  5. Music bed                                          [Sonilo Music]
  6. Mix + assemble (music ducked under speech, limited)  [ffmpeg]

Usage:
  python studio.py new "a kitten knight finds a golden key" [--scenes 3] [--out story.json]
      Writes a story.json. If OPENAI_API_KEY is set, an OpenAI-compatible
      LLM (OPENAI_BASE_URL, --model) fills it in from your idea; otherwise
      you get a template to edit by hand.

  python studio.py build story.json [--outdir DIR] [--dry-run] [--director] [--producer]
      Runs the pipeline. Existing stage outputs are reused (idempotent).
      --director pauses after the storyboard stills: view each image, then
      accept it, regenerate it with a note ('r wider shot'), or swap in your
      own image ('s path/to.png') before animation credits are spent.
      --producer opens a fix loop after the film is assembled:
      'r 2' re-renders scene 2's clip, 'v 2 1' re-voices line 1 of scene 2,
      'm 0.2' re-mixes with a louder music bed, 'a' accepts the film.

story.json format:
{
  "title": "Pip's First Flight",
  "style": "3D animated cartoon film, Pixar style",
  "aspect_ratio": "16:9",            // 16:9 | 9:16 | 1:1
  "video_resolution": "720p",        // 480p | 720p | 1080p | 4k
  "characters": [
    {"name": "Pip",
     "photo": "pip_photo.jpg",      // optional: real photo to cartoonize first
     "design": "small round chubby baby dragon, bright orange scales, ...",
     "voice":  "excited young boy character voice, high-pitched, joyful"}
  ],
  "scenes": [
    {"description": "Pip sits nervously at the cliff edge at sunrise, ...",
     "characters": ["Pip"],          // who appears; empty = environment only
     "motion": "gentle push-in, wind moving grass and fog",
     "duration": 6,                  // seconds, 4-15 (video model limit)
     "lines": [
       {"speaker": "narrator",        // or a character name
        "voice": "warm gentle male narrator voice, storytelling tone",
        "text": "Pip was the smallest dragon in the valley."}
     ]}
  ],
  "music_prompt": "tender orchestral score, soft strings and piano, whimsical"
}

Mixing defaults (validated on 'Pip's First Flight'):
  voice: loudnorm I=-19 -> per-scene atempo-fit (max 1.35) -> timeline delays
  music: volume 0.13, sidechain duck 15:1 on speech, fade out last 1.5s
  peak limiter 0.8 (-1.9 dB) to survive AAC encode.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---------------------------------------------------------------- config

MUSIC_VOL = 0.13          # music bed level relative to voices at ~1.1
DUCK_RATIO = 15           # sidechain compression ratio while speech is present
DUCK_THRESHOLD = 0.03
VOICE_TARGET_LUFS = -19
MAX_TEMPO = 1.35          # fastest speech fit adjustment
LINE_OFFSET = 0.6         # first line starts this many seconds into its scene
LINE_GAP = 0.35           # gap between consecutive lines in a scene
END_PAD = 0.4             # keep last line this far from scene end
AMB_VOL = 0.0            # clip ambience (H3 native track) - 0 = off (H3 often
                         # scores clips with its own music/vocals, which fights
                         # the Suno bed; raise e.g. to 0.2 only for quiet FX
                         # renders verified to be voice/music-free)

# Hard rule appended to every motion prompt: characters must never appear to
# speak, or off-screen dialogue reads as broken lip-sync.
NO_SPEAK = ("IMPORTANT - nobody in this clip speaks at any moment. All "
            "characters keep their lips gently closed and completely still "
            "for the entire duration: no talking, no lip movement, no mouth "
            "opening or closing, not even while smiling. All dialogue and "
            "narration is off-screen.")
AUDIO_FX = ("Audio: natural ambience only - soft weather and street sound "
            "effects. No music, no singing, no choir, no voices, no jingles, "
            "no instruments of any kind in the soundtrack.")

DEFAULT_STYLE = "3D animated cartoon film, Pixar style"
DEFAULT_MUSIC = ("tender orchestral score for a children's animated film: "
                 "soft strings, gentle piano, warm and whimsical, no vocals")

# ---------------------------------------------------------------- helpers

def fail(msg):
    sys.exit(f"studio: error: {msg}")


def run(cmd, capture=True):
    p = subprocess.run(cmd, capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if capture and p.returncode != 0:
        fail(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def parse_hf_json(stdout):
    """Parse higgsfield --json output tolerantly (log lines may precede JSON)."""
    start = stdout.find("[")
    if start < 0:
        fail(f"no JSON in higgsfield output:\n{stdout[-2000:]}")
    job, _ = json.JSONDecoder().raw_decode(stdout[start:])
    return job[0] if isinstance(job, list) else job


def hf_create(model, out_path: Path, retries=2, **params):
    """Submit a higgsfield job, wait, download result. Returns result URL."""
    prompt = params.pop("prompt")
    args = ["higgsfield", "generate", "create", model,
            "--prompt", prompt, "--wait", "--json"]
    for k, v in params.items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                args.append(flag)
        elif isinstance(v, (list, tuple)):
            for item in v:
                args += [flag, str(item)]
        else:
            args += [flag, str(v)]

    last_err = None
    for attempt in range(retries):
        p = run(args, capture=True)
        try:
            job = parse_hf_json(p.stdout)
        except Exception:
            last_err = p.stdout[-500:]
            continue
        if job.get("status") == "completed" and job.get("result_url"):
            dl = run(["curl", "-fsSL", "-o", str(out_path), job["result_url"]],
                     capture=True)
            if out_path.exists() and out_path.stat().st_size > 0:
                return job
            last_err = "download failed"
        else:
            last_err = f"status={job.get('status')}"
            if attempt < retries - 1:
                # simplify the prompt for the retry (content-flag resilience)
                args[args.index("--prompt") + 1] = _simplify(prompt)
                print(f"  retry ({last_err}): simplified prompt")
    fail(f"generation failed for {model} ({out_path.name}): {last_err}")


def _simplify(prompt):
    """Strip scene detail for a conservative retry prompt."""
    cut = re.split(r"[.:;]", prompt)[0]
    return cut.strip()[:200] if cut.strip() else "cute cartoon scene, gentle motion"


def get_ffmpeg():
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def probe_duration(path: Path):
    # `ffmpeg -i` always exits 1 (no output specified) - parse stderr anyway
    p = subprocess.run([get_ffmpeg(), "-nostdin", "-i", str(path)],
                       capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    if not m:
        fail(f"cannot read duration of {path}")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + s


def has_audio(path: Path):
    p = subprocess.run([get_ffmpeg(), "-nostdin", "-i", str(path)],
                       capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return "Audio:" in p.stderr


def trim_silence(src: Path, dst: Path):
    # -nostdin: never let ffmpeg swallow a line from the caller's stdin
    run([get_ffmpeg(), "-nostdin", "-y", "-i", str(src), "-af",
         "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
         "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
         "areverse", "-c:a", "pcm_s16le", str(dst)], capture=True)


def credits_left():
    p = run(["higgsfield", "workspace", "list"], capture=True)
    for line in p.stdout.splitlines():
        if re.match(r"^[0-9a-f]{8}-", line.strip()):
            m = re.search(r"^[0-9a-f-]+\s+\S+\s+\S+\s+([\d.]+)", line.strip())
            if m:
                return m.group(1)
    return "?"


# ---------------------------------------------------------------- backends
#
# A backend supplies the four generation primitives the pipeline needs:
#   image(out, prompt, aspect, ref=None)      still / character reference
#   video(out, prompt, start_image, duration, resolution)
#   voice(out, prompt)                        one spoken line
#   music(out, prompt, duration)              score bed
# Select per story with "backend": "higgsfield" | "kie" in story.json,
# or globally with the TOON_BACKEND env var (story wins).

class HiggsfieldBackend:
    """Default backend: Higgsfield CLI (credit-based).

    Model ids overridable via env: HF_IMAGE_MODEL, HF_VIDEO_MODEL,
    HF_VOICE_MODEL, HF_MUSIC_MODEL. Cheaper video options: kling3_0_turbo,
    seedance_1_5, minimax_hailuo, veo3_1_lite (see `higgsfield model list`).
    """
    name = "higgsfield"

    def __init__(self):
        self.m_image = os.environ.get("HF_IMAGE_MODEL", "nano_banana_2")
        self.m_video = os.environ.get("HF_VIDEO_MODEL", "seedance_2_0")
        self.m_voice = os.environ.get("HF_VOICE_MODEL", "seed_audio")
        self.m_music = os.environ.get("HF_MUSIC_MODEL", "sonilo_music")

    def image(self, out, prompt, aspect, ref=None):
        params = dict(prompt=prompt, aspect_ratio=aspect)
        if ref:
            params["image"] = str(ref)
        hf_create(self.m_image, out, retries=2, **params)

    def video(self, out, prompt, start_image, duration, resolution):
        hf_create(self.m_video, out, retries=2, prompt=prompt,
                  start_image=str(start_image), duration=duration,
                  resolution=resolution)

    def voice(self, out, text=None, voice_desc=None, voice_id=None, prompt=None):
        if prompt is None:
            prompt = f"{voice_desc or 'warm narrator voice, clear and expressive'}, " \
                     f"speaking clearly: {text}"
        hf_create(self.m_voice, out, retries=2, prompt=prompt)

    def music(self, out, prompt, duration):
        hf_create(self.m_music, out, retries=2, prompt=prompt,
                  duration=duration)

    def credits(self):
        return credits_left()


PRIMITIVES = ("image", "video", "voice", "music")


def get_backend(story):
    """Resolve the backend, honoring per-primitive overrides.

    story "backend": "higgsfield" | "kie"        (one backend for everything)
    story "backends": {"image": "kie", ...}        (per primitive; any subset,
                                                    missing keys fall back to
                                                    "backend"/TOON_BACKEND)
    """
    sel = dict(story.get("backends") or {})
    default = story.get("backend") or os.environ.get("TOON_BACKEND", "higgsfield")
    for prim in PRIMITIVES:
        sel.setdefault(prim, default)
    impls = {}
    for name in sorted(set(sel.values())):
        if name == "higgsfield":
            impls[name] = HiggsfieldBackend()
        elif name == "kie":
            impls[name] = get_kie_backend()
        elif name == "elevenlabs":
            impls[name] = ElevenLabsBackend()
        else:
            fail(f"unknown backend '{name}' (expected 'higgsfield', 'kie' or "
                 f"'elevenlabs')")
    if len(impls) == 1:
        return next(iter(impls.values()))
    return HybridBackend(sel, impls)


class HybridBackend:
    """Routes each primitive to a different backend (e.g. KIE for image/video,
    Higgsfield for voice/music until KIE audio is wired)."""

    def __init__(self, sel, impls):
        self.sel = sel
        self.impls = impls
        self.name = "hybrid(" + ",".join(f"{k}={v}" for k, v in sel.items()) + ")"

    def __getattr__(self, prim):
        if prim in PRIMITIVES:
            return getattr(self.impls[self.sel[prim]], prim)
        raise AttributeError(prim)

    def credits(self):
        return "; ".join(f"{n}: {b.credits()}" for n, b in self.impls.items())


def get_kie_backend():
    """KIE backend (https://kie.ai unified task API) - requires KIE_API_KEY
    plus configured models: env KIE_IMAGE_MODEL / KIE_VIDEO_MODEL (voice and
    music are not wired yet - route them to higgsfield via story "backends").
    """
    key = os.environ.get("KIE_API_KEY")
    if not key:
        fail("backend 'kie' needs KIE_API_KEY in the environment")
    base = os.environ.get("KIE_BASE_URL", "https://api.kie.ai")
    for suf in ("/api/v1", "/v1"):
        if base.rstrip("/").endswith(suf):
            base = base.rstrip("/")[: -len(suf)]
            break
    models = {k: os.environ.get(f"KIE_{k.upper()}_MODEL") for k in
              ("image", "video", "voice", "music")}
    return KIEBackend(key, base, models)


class KIEBackend:
    """Unified KIE task API backend: POST /api/v1/jobs/createTask with an
    async task, then GET /api/v1/jobs/recordInfo?taskId=... until done.

    Local files referenced by a task are uploaded first via the KIE file
    upload service (base64). Models (env KIE_*_MODEL): image e.g.
    "nano-banana-pro" (~18 credits/img), video e.g. "bytedance/seedance-2-fast"
    (~11.7 credits/s). Every poll reports credits consumed by the task.
    """
    name = "kie"
    UPLOAD_URL = "https://kieai.redpandaai.co/api/file-base64-upload"

    def __init__(self, key, base, models):
        self.key = key
        self.base = base.rstrip("/")
        self.models = models

    def _req(self, path, payload=None, method="POST", timeout=300):
        req = urllib.request.Request(
            self.base + "/api/v1" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:
                return e.code, {}

    def _upload(self, path):
        """Upload a local file, return a public URL. Caches per file."""
        p = Path(path)
        cache = self.__dict__.setdefault("_upload_cache", {})
        if str(p) in cache:
            return cache[str(p)]
        b64 = "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
        req = urllib.request.Request(
            self.UPLOAD_URL,
            data=json.dumps({"base64Data": b64, "uploadPath": "toon-studio",
                             "fileName": p.name}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.key}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            fail(f"KIE upload of {p.name} failed: {e.code} {e.read()[:200]}")
        url = (resp.get("data") or {}).get("downloadUrl")
        if not url:
            fail(f"KIE upload of {p.name}: no downloadUrl in response")
        cache[str(p)] = url
        return url

    def _create_task(self, model, inp):
        st, resp = self._req("/jobs/createTask", {"model": model, "input": inp})
        if st != 200 or resp.get("code") != 200:
            fail(f"KIE createTask [{model}] failed: http={st} "
                 f"{json.dumps(resp)[:300]}")
        return resp["data"]["taskId"]

    def _wait(self, task_id, out, label):
        import time
        t0 = time.time()
        while True:
            time.sleep(6)
            st, resp = self._req(f"/jobs/recordInfo?taskId={task_id}",
                                 method="GET", timeout=60)
            if st != 200 or resp.get("code") != 200:
                if time.time() - t0 > 900:
                    fail(f"KIE task {label}: status polling failed for 15min: "
                         f"http={st} {json.dumps(resp)[:200]}")
                print(f"  {label}: poll http={st}, retrying ...")
                time.sleep(10)
                continue
            d = resp["data"]
            state = d.get("state") or d.get("status")
            if d.get("successFlag") == 1 or state in ("success", "completed"):
                cost = d.get("creditsConsumed")
                if cost is not None:
                    print(f"  {label}: done in {int(time.time() - t0)}s, "
                          f"{cost} credits")
                urls = (d.get("response") or {}).get("resultUrls") or []
                if not urls:
                    fail(f"KIE task {label} succeeded but no resultUrls: "
                         f"{json.dumps(d)[:300]}")
                run(["curl", "-fsSL", "-o", str(out), urls[0]], capture=True)
                if not out.exists() or out.stat().st_size == 0:
                    fail(f"KIE result download failed for {out.name}")
                return
            if state in ("fail", "failed", "error") or d.get("failCode"):
                fail(f"KIE task {label} failed: state={state} "
                     f"failCode={d.get('failCode')} msg={d.get('failMsg')}")
            if time.time() - t0 > 900:
                fail(f"KIE task {label} timed out after 15min (state={state})")

    def _model(self, prim):
        m = self.models.get(prim)
        if not m:
            fail(f"KIE_{prim.upper()}_MODEL not set - set it in .env, or route "
                 f"'{prim}' to another backend via story \"backends\"")
        return m

    def image(self, out, prompt, aspect, ref=None):
        inp = {"prompt": prompt, "aspect_ratio": aspect}
        if ref:
            # nano-banana-pro reference images: uploaded URLs via image_input
            inp["image_input"] = [self._upload(ref)]
        self._wait(self._create_task(self._model("image"), inp), out, out.name)

    def video(self, out, prompt, start_image, duration, resolution):
        model = self._model("video")
        url = self._upload(start_image)
        if model.startswith("hailuo/"):
            # MiniMax Hailuo: image_url field, string duration (6 or 10 only),
            # 768P|1080P resolution
            res = {"1080p": "1080P"}.get(resolution, "768P")
            dur = "6" if int(duration) <= 6 else "10"
            inp = {"prompt": prompt, "image_url": url,
                   "duration": dur, "resolution": res}
        elif model.startswith("minimax-h3"):
            # MiniMax H3: first_frame_url, duration 4-15 (int), 768P|2K,
            # natively generates an ambient audio track (the mixer keeps it,
            # ducked low under the music)
            res = "2K" if resolution in ("1080p", "2k", "4k") else "768P"
            inp = {"prompt": prompt, "first_frame_url": url,
                   "duration": int(duration), "resolution": res}
        else:
            inp = {"prompt": prompt,
                   "first_frame_url": url,
                   "duration": int(duration),
                   "resolution": resolution,
                   "generate_audio": False}
        for attempt in range(2):
            try:
                self._wait(self._create_task(model, inp), out, out.name)
                return
            except SystemExit:
                if attempt == 0:
                    print("  retry with simplified motion prompt")
                    prompt = _simplify(prompt)
                    if model.startswith("hailuo/"):
                        inp["prompt"] = prompt
                    else:
                        inp["prompt"] = prompt
                else:
                    raise

    def voice(self, out, text=None, voice_desc=None, voice_id=None, prompt=None):
        """ElevenLabs TTS via the unified task API.

        Voice selection (first hit wins): the voice_id argument (from the
        line's or character's "voice_id" in story.json), env KIE_VOICE_ID,
        then the model default. Preview voices at
        https://static.aiquickdraw.com/elevenlabs/voice/<voice_id>.mp3
        """
        if text is None:                       # legacy combined prompt
            text = (prompt or "").split(": ", 1)[-1]
        vid = voice_id or os.environ.get("KIE_VOICE_ID") or \
            "EkK5I93UQWFDigLMpZcX"
        inp = {"text": text, "voice": vid,
               "stability": 0.5, "similarity_boost": 0.75, "speed": 1}
        self._wait(self._create_task(self._model("voice"), inp), out, out.name)

    def music(self, out, prompt, duration):
        """Suno instrumental bed via the dedicated music endpoint.

        Suno writes full songs (~60-240s); the mix stage trims to the film.
        KIE insists on callBackUrl - a placeholder is fine, we poll instead.
        Model via env KIE_MUSIC_MODEL (a Suno version: V4 | V4_5 | V4_5PLUS |
        V4_5ALL | V5), default V4_5.
        """
        import time
        version = self.models.get("music") or "V4_5"
        title = re.sub(r"[^A-Za-z0-9 ]+", "", out.stem)[:40] or "Toon Studio"
        payload = {"prompt": prompt, "customMode": True, "instrumental": True,
                   "model": version, "style": prompt[:950], "title": title,
                   "callBackUrl": "https://example.com/toon-studio-callback"}
        st, resp = self._req("/generate", payload, timeout=120)
        if st != 200 or resp.get("code") != 200:
            fail(f"KIE Suno generate failed: http={st} "
                 f"{json.dumps(resp)[:300]}")
        task_id = resp["data"]["taskId"]
        t0 = time.time()
        while True:
            time.sleep(8)
            st, resp = self._req(f"/generate/record-info?taskId={task_id}",
                                 method="GET", timeout=60)
            d = (resp.get("data") or {}) if resp.get("code") == 200 else {}
            if (d.get("status") or "").upper() == "SUCCESS":
                tracks = (d.get("response") or {}).get("sunoData") or []
                if tracks:
                    print(f"  {out.name}: suno done in "
                          f"{int(time.time() - t0)}s "
                          f"({tracks[0].get('duration', '?')}s track)")
                    run(["curl", "-fsSL", "-o", str(out),
                         tracks[0]["audioUrl"]], capture=True)
                    if not out.exists() or out.stat().st_size == 0:
                        fail(f"KIE Suno download failed for {out.name}")
                    return
                fail(f"KIE Suno task {task_id} SUCCESS but empty sunoData")
            if (d.get("status") or "").upper() in ("FAILED", "ERROR"):
                fail(f"KIE Suno task failed: {json.dumps(d)[:300]}")
            if time.time() - t0 > 900:
                fail(f"KIE Suno task {task_id} timed out after 15min "
                     f"(status={d.get('status')})")

    def credits(self):
        st, resp = self._req("/chat/credit", method="GET", timeout=30)
        if st == 200:
            d = resp.get("data")
            return f"{d} credits" if isinstance(d, (int, float)) else str(d)
        return "?"


class ElevenLabsBackend:
    """Direct ElevenLabs API voice backend (synchronous TTS).

    Requires the elevenlabs SDK (pip install elevenlabs) and ELEVENLABS_API_KEY
    (onboarded via `npx @elevenlabs/cli onboard init` into .env.local, which
    load_dotenv also reads). Voice selection: the voice_id argument (story
    "voice_id" on the line or character), else env ELEVENLABS_VOICE_ID, else
    a keyword guess from the voice description (female->Charlotte,
    authoritative/narrator male->George, other male->Daniel). Model via env
    ELEVENLABS_MODEL (default eleven_v4). Only voice() is implemented - route
    the other primitives elsewhere via story "backends".
    """
    name = "elevenlabs"
    GENDER_F = ("woman", "female", "girl", "young woman")
    GENDER_M = ("man", "male", "boy", "narrator", "deep")
    DEFAULT_VOICES = {"female": "XB0fDUnXU5powFXDhCwa",   # Charlotte
                    "narrator": "JBFqnCBsd6RMkjVDRZzb",   # George
                    "male": "onwK4e9ZLuTAKqWW03F9"}       # Daniel

    def __init__(self):
        try:
            from elevenlabs import ElevenLabs
        except ImportError:
            fail("backend 'elevenlabs' needs the SDK: pip install elevenlabs")
        if not os.environ.get("ELEVENLABS_API_KEY"):
            fail("backend 'elevenlabs' needs ELEVENLABS_API_KEY (from "
                 ".env.local via `npx @elevenlabs/cli onboard init`)")
        self.client = ElevenLabs()
        self.model = os.environ.get("ELEVENLABS_MODEL", "eleven_v4")

    def _pick_voice(self, voice_desc, voice_id):
        if voice_id:
            return voice_id
        env = os.environ.get("ELEVENLABS_VOICE_ID")
        if env:
            return env
        d = (voice_desc or "").lower()
        if any(k in d for k in self.GENDER_F):
            return self.DEFAULT_VOICES["female"]
        if "narrator" in d or "storytelling" in d:
            return self.DEFAULT_VOICES["narrator"]
        if any(k in d for k in self.GENDER_M):
            return self.DEFAULT_VOICES["male"]
        return self.DEFAULT_VOICES["narrator"]

    def voice(self, out, text=None, voice_desc=None, voice_id=None, prompt=None):
        if text is None:                       # legacy combined prompt
            text = (prompt or "").split(": ", 1)[-1]
        from elevenlabs import VoiceSettings
        audio = self.client.text_to_speech.convert(
            text=text,
            voice_id=self._pick_voice(voice_desc, voice_id),
            model_id=self.model,
            output_format="mp3_44100_128",
            voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
        )
        with open(out, "wb") as f:
            for chunk in audio:
                f.write(chunk)
        if not out.exists() or out.stat().st_size == 0:
            fail(f"ElevenLabs returned empty audio for {out.name}")

    def credits(self):
        try:
            u = self.client.user.get()
            return f"{u.subscription.character_limit - u.subscription.character_count} chars left"
        except Exception:
            return "n/a"

    def __getattr__(self, prim):
        if prim in PRIMITIVES:
            fail(f"backend 'elevenlabs' only does voice - route '{prim}' to "
                 f"higgsfield or kie via story \"backends\"")
        raise AttributeError(prim)


# ---------------------------------------------------------------- LLM story

LLM_SYS = ("You are a children's cartoon screenwriter. Respond with STRICT JSON only: "
           "a story object with keys title, characters (list of {name, design, voice}), "
           "scenes (list of {description, characters, motion, duration, lines}), music_prompt. "
           "Each scene: 1-2 lines of dialogue/narration max, each under 20 words, "
           "duration 5-8 seconds. Characters: name, a detailed visual design description "
           "(colors, size, distinctive features), and a voice description "
           "(age, gender, tone). music_prompt: one sentence.")


def new_story(idea, n_scenes, out_path: Path, model=None):
    tpl = {
        "title": "TODO title",
        "style": DEFAULT_STYLE,
        "aspect_ratio": "16:9",
        "video_resolution": "720p",
        "characters": [{"name": "TODO", "design": "TODO", "voice": "TODO"}],
        "scenes": [{"description": "TODO", "characters": [], "motion": "gentle motion",
                    "duration": 6,
                    "lines": [{"speaker": "narrator", "voice": "TODO", "text": "TODO"}]}
                   for _ in range(n_scenes)],
        "music_prompt": DEFAULT_MUSIC,
    }
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("KIE_API_KEY")
    if key:
        base = os.environ.get("OPENAI_BASE_URL", "https://api.moonshot.ai/v1")
        model = model or os.environ.get("TOON_MODEL", "kimi-k2-0905-preview")
        req = {"model": model, "temperature": 0.8,
               "messages": [{"role": "system", "content": LLM_SYS},
                            {"role": "user", "content":
                             f"Write a {n_scenes}-scene cartoon film script for this idea: {idea}"}]}
        r = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(req).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(r, timeout=120) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        story, _ = json.JSONDecoder().raw_decode(text[text.find("{"):])
        story.setdefault("style", DEFAULT_STYLE)
        story.setdefault("aspect_ratio", "16:9")
        story.setdefault("video_resolution", "720p")
        story.setdefault("music_prompt", DEFAULT_MUSIC)
        tpl = story
        print(f"story written by {model}")
    else:
        print("no OPENAI_API_KEY/KIE_API_KEY found - writing a template to edit by hand")
    tpl["_idea"] = idea
    out_path.write_text(json.dumps(tpl, indent=2, ensure_ascii=False))
    print(f"story file: {out_path}")


# ---------------------------------------------------------------- pipeline

class Stage:
    def __init__(self, outdir: Path, backend, dry=False):
        self.outdir = outdir
        self.backend = backend
        self.dry = dry

    def path(self, name):
        return self.outdir / name

    def have(self, name):
        p = self.path(name)
        return p.exists() and p.stat().st_size > 0

    def gen(self, name, fn, **params):
        """Generate via backend method `fn` unless output already exists."""
        out = self.path(name)
        if self.have(name):
            print(f"  reuse {name}")
            return out
        print(f"  gen {name} [{self.backend.name}:{fn}]")
        if not self.dry:
            getattr(self.backend, fn)(out, **params)
        return out


def gen_voice(st, story, scene_i, line_i, line):
    raw = st.path(f"vo{scene_i}_{line_i}.wav")
    trm = st.path(f"vo{scene_i}_{line_i}_trim.wav")
    if st.have(trm.name):
        print(f"  reuse {trm.name}")
        return raw, trm
    speaker = line.get("speaker", "narrator")
    ch = next((c for c in story.get("characters", []) if c["name"] == speaker), None)
    voice_desc = line.get("voice") or (ch and ch.get("voice")) or \
        "warm narrator voice, clear and expressive"
    print(f"  gen {raw.name} [{st.backend.name}:voice]")
    if not st.dry:
        st.backend.voice(raw, text=line["text"], voice_desc=voice_desc,
                         voice_id=line.get("voice_id") or (ch and ch.get("voice_id")))
        trim_silence(raw, trm)
    return raw, trm


def still_params(story, scene, char_ref, aspect, extra=""):
    style = story.get("style", DEFAULT_STYLE)
    cast = ", ".join(c["name"] for c in story.get("characters", [])
                     if c["name"] in scene.get("characters", []))
    designs = "; ".join(c["design"] for c in story.get("characters", [])
                        if c["name"] in scene.get("characters", []))
    who = (f"Featuring character(s): {cast}. Exact designs: {designs}. " if cast else "")
    img = char_ref.get(scene.get("characters", [None])[0]) if scene.get("characters") else None
    params = dict(
        prompt=(f"Scene from an animated cartoon film, {style}. {who}"
                f"{scene['description']}. {extra} Cinematic composition, {style} "
                f"lighting, storyboard-quality film still."),
        aspect=aspect)
    if img is not None:
        params["ref"] = str(img)
        extra = ("Match the reference character's face, hairstyle and identity "
                 "exactly, but do NOT copy the outfit from the reference image "
                 "- dress the character exactly as described in this scene. "
                 + extra)
    return params


def director_review(story, scenes, stills, char_ref, aspect, backend):
    """Interactive approval of storyboard stills before animation credits are spent.

    Enter = accept, 'r <note>' = regenerate with director's note,
    's <file>' = swap in an external image. EOF (piped input) accepts the rest.
    """
    aspect = story.get("aspect_ratio", aspect)
    print("\n== DIRECTOR PASS: review the storyboard stills ==")
    print(f"   images are in: {stills[0].parent}")
    for i, (scene, still) in enumerate(zip(scenes, stills), 1):
        while True:
            print(f"\n  scene {i}: {scene['description'][:90]}")
            print(f"  still : {still}")
            try:
                ans = input("  [Enter]=accept  r <note>=regenerate  s <file>=swap > ").strip()
            except EOFError:
                print("  (no input available - accepting all remaining)")
                ans = ""
            if not ans:
                break
            cmd, _, arg = ans.partition(" ")
            if cmd.lower().startswith("r"):
                note = (arg or "different composition") + ". "
                print(f"  regenerating scene {i} ...")
                backend.image(still, **still_params(story, scene, char_ref, aspect,
                                                    extra=note))
                break
            if cmd.lower().startswith("s"):
                src = Path(arg.strip().strip('"'))
                if src.exists() and src.resolve() != still.resolve():
                    still.write_bytes(src.read_bytes())
                    print(f"  swapped in {src.name}")
                    break
                print(f"  file not found: {src}")
                continue
            print("  ? use Enter, 'r <note>', or 's <path>'")
    return stills


def build(story_path: Path, outdir: Path, dry=False, director=False, producer=False):
    story = json.loads(story_path.read_text(encoding="utf-8"))
    title = story.get("title", "untitled")
    style = story.get("style", DEFAULT_STYLE)
    aspect = story.get("aspect_ratio", "16:9")
    res = story.get("video_resolution", "720p")
    scenes = story["scenes"]
    outdir.mkdir(parents=True, exist_ok=True)
    backend = get_backend(story)
    st = Stage(outdir, backend, dry)
    print(f"== backend: {backend.name} ==")

    print(f"== {title} ==")
    print("== stage 1/5: character references ==")
    char_ref = {}
    for ch in story.get("characters", []):
        name = ch["name"].lower().replace(" ", "_")
        photo = ch.get("photo")
        if photo:
            # cartoonize a real-world photo first, then use it as the reference
            photo_p = Path(photo)
            if not photo_p.is_absolute() and not photo_p.exists():
                alt = story_path.parent / photo
                if alt.exists():
                    photo_p = alt
            if not photo_p.exists():
                fail(f"photo for character '{ch['name']}' not found: {photo}")
            ref = st.gen(f"ref_{name}.png", "image",
                         prompt=(f"Turn the subject of this photo into a character from an "
                                 f"animated cartoon film, {style}. {ch['design']}. Keep the "
                                 f"subject's recognizable features - face shape, markings, "
                                 f"colors, accessories. Full body, front view, standing, "
                                 f"plain soft studio background, highly detailed."),
                         ref=photo_p, aspect="1:1")
        else:
            ref = st.gen(f"ref_{name}.png", "image",
                         prompt=(f"Single character from an animated cartoon film, {style}. "
                                 f"{ch['design']}. Full body, front view, standing, neutral "
                                 f"expression, plain soft studio background, character design "
                                 f"quality, highly detailed."),
                         aspect="1:1")
        char_ref[ch["name"]] = ref

    print("== stage 2/5: storyboard stills ==")
    def still_for(idx, scene):
        return st.gen(f"scene{idx}.png", "image",
                      **still_params(story, scene, char_ref, aspect))

    with ThreadPoolExecutor(max_workers=3) as ex:
        stills = list(ex.map(lambda t: still_for(*t), enumerate(scenes, 1)))

    if director and not dry:
        stills = director_review(story, scenes, stills, char_ref, aspect, backend)

    print("== stage 3/5: animation ==")
    def video_for(idx, pair):
        scene, still = pair
        v = st.path(f"clip{idx}.mp4")
        if st.have(f"clip{idx}.mp4"):
            print(f"  reuse clip{idx}.mp4")
            return v
        print(f"  gen clip{idx}.mp4 [{backend.name}:video]")
        if not dry:
            amb = scene.get("ambience")
            backend.video(v,
                          prompt=(f"{scene.get('motion', 'gentle natural motion')}. "
                                  f"Subtle character animation, cinematic. {NO_SPEAK} "
                                  f"{AUDIO_FX} " + (amb + " " if amb else "")),
                          start_image=still,
                          duration=int(scene.get("duration", 6)),
                          resolution=res)
        return v

    with ThreadPoolExecutor(max_workers=3) as ex:
        clips = list(ex.map(lambda t: video_for(t[0], t[1]), enumerate(zip(scenes, stills), 1)))

    print("== stage 4/5: voiceover ==")
    with ThreadPoolExecutor(max_workers=4) as ex:
        voice_files = list(ex.map(
            lambda t: [gen_voice(st, story, t[0], li + 1, l)
                       for li, l in enumerate(t[1].get("lines", []))],
            enumerate(scenes, 1)))

    print("== stage 5/5: music + mix ==")
    total = sum(probe_duration(c) for c in clips) if not dry else \
        sum(float(s.get("duration", 6)) for s in scenes)
    music = st.gen("music.m4a", "music",
                   prompt=story.get("music_prompt", DEFAULT_MUSIC),
                   duration=int(round(total)))

    if dry:
        print(f"\nDRY RUN complete. total runtime {total:.1f}s -> {outdir}")
        return

    assemble(story, scenes, clips, voice_files, music, outdir, total)
    print(f"\ndone: {outdir / (slug(title) + '.mp4')}")
    print(f"credits left: {backend.credits()}")
    if producer:
        producer_pass(story, scenes, stills, clips, voice_files, music, outdir,
                      backend)
        print(f"credits left: {backend.credits()}")


def slug(title):
    s = title.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "film"


def assemble(story, scenes, clips, voice_files, music, outdir, total,
             music_vol=MUSIC_VOL):
    ff = get_ffmpeg()
    n = len(clips)
    boundaries = [0.0]
    for c in clips:
        boundaries.append(boundaries[-1] + probe_duration(c))

    # video: concat + normalize
    parts = [f"[{i}:v]" for i in range(n)]
    fgraph = f"{''.join(parts)}concat=n={n}:v=1:a=0[vv];[vv]fps=24,setsar=1[vout];"

    # voices: per-line loudnorm, fit-to-scene atempo, timed delays
    chains, mix_ins, idx = [], [], n
    for si, scene in enumerate(scenes, 1):
        start = boundaries[si - 1] + LINE_OFFSET
        files = voice_files[si - 1]
        durs = [probe_duration(t[1]) for t in files]
        end_limit = boundaries[si] - END_PAD
        avail = end_limit - start
        need = sum(durs) + LINE_GAP * max(0, len(durs) - 1)
        factor = need / avail if avail > 0 and need > avail else 1.0
        if factor > MAX_TEMPO:
            print(f"  warning: scene {si} speech needs {need:.1f}s, has {avail:.1f}s "
                  f"(clip it in the story.json)")
        t = start
        for li, (raw_trm, dur) in enumerate(zip(files, durs)):
            line, trm = raw_trm
            delay_ms = int(round(t * 1000))
            chain = (f"[{idx}:a]loudnorm=I={VOICE_TARGET_LUFS}:TP=-2:LRA=11,"
                     f"dynaudnorm=f=150:g=5")
            if factor > 1.0:
                chain += f",atempo={min(MAX_TEMPO, factor):.3f}"
            chain += f",adelay={delay_ms}|{delay_ms}[vo{idx}]"
            chains.append(chain)
            mix_ins.append(f"[vo{idx}]")
            eff = min(MAX_TEMPO, factor) if factor > 1.0 else 1.0
            t += dur / eff + LINE_GAP
            idx += 1

    total_ms = int(round(total * 1000))
    fgraph += ";".join(chains) + ";"
    fgraph += (f"{''.join(mix_ins)}amix=inputs={len(mix_ins)}:normalize=0,"
               f"apad=whole_dur={total:.2f},volume=1.1,asplit=2[voice][sc];")
    fgraph += (f"[{idx}:a]atrim=0:{total:.2f},volume={music_vol}[musr];"
               f"[musr][sc]sidechaincompress=threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}"
               f":attack=5:release=400:makeup=1,afade=t=out:st={total - 1.7:.2f}:d=1.5[musduck];")
    # clip ambience (e.g. MiniMax H3's native track): rain, wind, street -
    # keeps the world alive under the music. Clips without an audio stream
    # (seedance/hailuo) are skipped.
    amb_ins = []
    for i, c in enumerate(clips):
        if not has_audio(c):
            continue
        delay_ms = int(round(boundaries[i] * 1000))
        fgraph += (f"[{i}:a]aresample=44100,atrim=0:{boundaries[i + 1] - boundaries[i]:.2f},"
                   f"asetpts=PTS-STARTPTS,volume={AMB_VOL},"
                   f"apad=whole_dur={total - boundaries[i]:.2f},"
                   f"adelay={delay_ms}|{delay_ms}[amb{i}];")
        amb_ins.append(f"[amb{i}]")
    mix_in = f"[voice][musduck]{''.join(amb_ins)}"
    fgraph += (f"{mix_in}amix=inputs={2 + len(amb_ins)}:normalize=0,"
               "alimiter=limit=0.8:level=false," f"atrim=0:{total:.2f}[aout]")

    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]
    for scene_files in voice_files:
        for raw, trm in scene_files:
            inputs += ["-i", str(trm)]
    inputs += ["-i", str(music)]

    out = outdir / (slug(story.get("title", "film")) + ".mp4")
    cmd = ([ff, "-nostdin", "-y"] + inputs + ["-filter_complex", fgraph,
           "-map", "[vout]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
           "-t", f"{total:.2f}", str(out)])
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0 or not out.exists():
        fail(f"ffmpeg failed:\n{p.stderr[-3000:]}")
    # loudness sanity check
    v = run([ff, "-nostdin", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
            capture=True)
    mx = re.search(r"max_volume: ([-\d.]+)", v.stderr)
    print(f"  mix peak {mx.group(1)} dB (target <= -1.0)" if mx else "  mix checked")
    return out


def producer_pass(story, scenes, stills, clips, voice_files, music, outdir,
                  backend):
    """Post-assembly fix loop: re-render a clip, re-voice a line, or re-mix.

    Commands:
      r <scene#> [note]   re-render that scene's clip (note amends the motion)
      v <scene#> <line#>  re-voice one dialogue line
      m <volume>          re-mix with a new music-bed volume (default 0.13)
      a  or  Enter        accept the film and finish
    EOF (piped input) finishes the pass.
    """
    res = story.get("video_resolution", "720p")
    music_vol = MUSIC_VOL
    film = outdir / (slug(story.get("title", "film")) + ".mp4")

    def remix():
        total = sum(probe_duration(c) for c in clips)
        assemble(story, scenes, clips, voice_files, music, outdir, total,
                 music_vol=music_vol)

    print(f"\n== PRODUCER PASS: {film.name} ==")
    print("   r <scene> [note]=re-render clip  v <scene> <line>=re-voice  "
          "m <vol>=re-mix  a/Enter=done")
    while True:
        try:
            ans = input("producer> ").strip()
        except EOFError:
            print("  (no input available - finishing)")
            break
        if not ans or ans.lower().startswith("a"):
            break
        cmd, _, arg = ans.partition(" ")
        toks = arg.split()
        try:
            if cmd == "r" and toks:
                i = int(toks[0])
                scene, still, clip = scenes[i - 1], stills[i - 1], clips[i - 1]
                note = " ".join(toks[1:])
                motion = scene.get("motion", "gentle natural motion")
                if note:
                    motion = f"{motion}. {note}"
                clip.unlink(missing_ok=True)
                print(f"  re-rendering clip {i} ...")
                backend.video(clip,
                              prompt=(f"{motion}. Subtle character animation, "
                                      f"cinematic. {NO_SPEAK} {AUDIO_FX} "
                                      f"{scene.get('ambience', '')}"),
                              start_image=still,
                              duration=int(scene.get("duration", 6)),
                              resolution=res)
                remix()
            elif cmd == "v" and len(toks) >= 2:
                i, li = int(toks[0]), int(toks[1])
                line = scenes[i - 1]["lines"][li - 1]
                for p in (outdir / f"vo{i}_{li}.wav", outdir / f"vo{i}_{li}_trim.wav"):
                    p.unlink(missing_ok=True)
                print(f"  re-voicing line {li} of scene {i} ...")
                voice_files[i - 1][li - 1] = gen_voice(Stage(outdir, backend), story,
                                                       i, li, line)
                remix()
            elif cmd == "m" and toks:
                music_vol = float(toks[0])
                print(f"  re-mixing (music volume {music_vol}) ...")
                remix()
            else:
                print("  ? r <scene> [note] | v <scene> <line> | m <vol> | a")
        except (ValueError, IndexError):
            print("  ? scene/line numbers out of range")
    return film


# ---------------------------------------------------------------- main

def load_dotenv():
    """Load KEY=VALUE pairs from .env.local then .env next to this script.
    Priority: shell > .env > .env.local (e.g. ElevenLabs onboard key)."""
    for name in (".env.local", ".env"):
        env_file = Path(__file__).with_name(name)
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
        print(f"loaded env: {env_file}")


def main():
    ap = argparse.ArgumentParser(prog="studio",
                                 description="One-command AI cartoon film pipeline.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("new", help="create a story.json from an idea (or template)")
    a.add_argument("idea")
    a.add_argument("--scenes", type=int, default=3)
    a.add_argument("--out", type=Path, default=Path("story.json"))
    a.add_argument("--model", help="LLM model id (default: env TOON_MODEL or "
                                    "kimi-k2-0905-preview)")

    b = sub.add_parser("build", help="run the pipeline on a story.json")
    b.add_argument("story", type=Path)
    b.add_argument("--outdir", type=Path,
                   default=None, help="default: ./<story-stem>-film")
    b.add_argument("--dry-run", action="store_true",
                   help="print the plan without generating")
    b.add_argument("--director", action="store_true",
                   help="pause after storyboard stills to accept/regenerate/swap "
                        "each one before animation")
    b.add_argument("--producer", action="store_true",
                   help="after the film is assembled, offer targeted fixes: "
                        "re-render a clip, re-voice a line, or re-mix levels")

    args = ap.parse_args()
    load_dotenv()
    if args.cmd == "new":
        new_story(args.idea, args.scenes, args.out, args.model)
    else:
        outdir = args.outdir or Path(args.story.stem + "-film")
        build(args.story, outdir, args.dry_run, args.director, args.producer)


if __name__ == "__main__":
    main()
