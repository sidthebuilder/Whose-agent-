"""Make the narration for the demo video with a Microsoft neural voice (edge-tts). Needs internet.

  .venv\\Scripts\\python scripts/make_voice.py            # new or changed lines only
  .venv\\Scripts\\python scripts/make_voice.py --force    # every line again

Reads the lines in demo/demo.json and writes demo/voice/<id>.mp3, plus demo/voice/durations.json, which the
recorder uses to time captions and place each line under the video. Voice, speed and loudness are set in
demo.json. List the voices with: .venv\\Scripts\\edge-tts --list-voices
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "demo" / "demo.json"
OUT = ROOT / "demo" / "voice"


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def duration(path: Path) -> float:
    """Length of a media file in seconds, read from ffmpeg's report (decoding it when the header has none)."""
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        err = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path), "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", err)
        if not times:
            raise RuntimeError(f"cannot read the length of {path}")
        h, mi, s = times[-1]
    else:
        h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def line_hash(cfg: dict, text: str) -> str:
    return hashlib.sha1(f"{cfg['voice']}|{cfg['rate']}|{cfg['volume']}|{text}".encode()).hexdigest()[:12]


async def main() -> None:
    try:
        import edge_tts
    except ImportError:
        sys.exit("edge-tts is not installed: .venv\\Scripts\\python -m pip install -r demo/requirements.txt")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    force = "--force" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    meta_path = OUT / "durations.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    for key, text in cfg["lines"].items():
        h, mp3 = line_hash(cfg, text), OUT / f"{key}.mp3"
        if not force and mp3.exists() and meta.get(key, {}).get("hash") == h:
            print(f"  same     {key}")
            continue
        await edge_tts.Communicate(text, cfg["voice"], rate=cfg["rate"], volume=cfg["volume"]).save(str(mp3))
        meta[key] = {"hash": h, "seconds": round(duration(mp3), 3)}
        print(f"  wrote    {key}.mp3  {meta[key]['seconds']:.1f} s")
    meta = {k: v for k, v in meta.items() if k in cfg["lines"]}
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    total = sum(v["seconds"] for v in meta.values())
    print(f"{len(meta)} lines, {total:.0f} s of speech, voice {cfg['voice']}")


if __name__ == "__main__":
    asyncio.run(main())
