"""How loud each finished video is, measured once and written down.

The player hears these back to back in a playlist, so the number that matters
is not any song's own level but the step between neighbours. Measuring is a
full decode of every render, which is slow enough that nobody does it casually
and then nobody knows the levels at all -- so the figures live in
loudness.json at the repo root and are re-measured only when the audio they
describe has changed.

What invalidates an entry is the hash of the AUDIO STREAM, not of the file.
A render's picture changes whenever the cursor moves a few milliseconds, which
happens repeatedly while a song is being settled by ear and never once changes
how loud it is; hashing the whole .mkv would throw the measurement away every
time. Hashing the copied audio packets covers exactly what ebur128 reads: a new
source, a new trim or a new gain_db moves it, a new cursor does not. If the
opus encoder ever turns out to be non-deterministic the cache simply re-measures
more often, which is a cost and not a wrong answer.
"""

import hashlib
import json
import os
import re
import subprocess

CACHE_NAME = "loudness.json"
SCHEMA = 1


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True,
                          text=True, errors="replace")


def audio_hash(path):
    """sha256 of the encoded audio packets, with the container left out.

    -c copy means no decode and no re-encode, so this is a cheap read of the
    one stream the loudness figures come from.
    """
    out = _run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a",
                "-c", "copy", "-f", "hash", "-hash", "sha256", "-"]).stdout
    m = re.search(r"SHA256=([0-9a-f]+)", out)
    if not m:
        raise RuntimeError(f"no audio hash for {path}: {out!r}")
    return m.group(1)


def measure(path):
    """EBU R128 over the whole file: integrated LUFS, true peak, LRA.

    Integrated loudness is gated, so the ten seconds of lead and the long tail
    do not drag it down -- which is the whole reason to use it here rather than
    mean volume.
    """
    log = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path,
                "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr

    def last(pattern, unit):
        found = re.findall(pattern + r"\s*(-?[\d.]+) " + unit, log)
        if not found:
            raise RuntimeError(f"ebur128 gave no {pattern!r} for {path}")
        return float(found[-1])

    dur = float(_run(["ffprobe", "-v", "error", "-show_entries",
                      "format=duration", "-of", "csv=p=0", path]).stdout)
    return {
        "integrated_lufs": last(r"\bI:", "LUFS"),
        "true_peak_dbtp": last(r"\bPeak:", "dBFS"),
        "lra": last(r"\bLRA:", "LU"),
        "duration_s": round(dur, 2),
    }


def _outputs(root):
    """(song, relative path) for every song whose render is on disk."""
    found = []
    for name in sorted(os.listdir(root)):
        sj = os.path.join(root, name, "song.json")
        if not os.path.exists(sj):
            continue
        with open(sj) as f:
            data = json.load(f)
        rel = os.path.join(name, data.get("output", "playalong.mkv"))
        if os.path.exists(os.path.join(root, rel)):
            found.append((name, rel, data))
    return found


def _commit(root):
    """The recipe's revision when a figure was taken, as provenance only.

    Never the cache key: the renders are gitignored, so the commit says nothing
    about which bytes are on this disk -- two checkouts at the same revision can
    hold different renders, and a README commit would otherwise throw away every
    measurement. audio_sha256 is what invalidates; this is here so a figure can
    be read against the decision that produced it.

    The image carries no git, so run.sh reads HEAD on the host and passes it in.
    """
    return os.environ.get("SAX_COMMIT") or None


def refresh(root, force=False, log=print):
    """Measure what has changed, keep what has not, and write the cache."""
    path = os.path.join(root, CACHE_NAME)
    old = {}
    if os.path.exists(path) and not force:
        with open(path) as f:
            cached = json.load(f)
        if cached.get("schema") == SCHEMA:
            old = cached.get("songs", {})

    songs, fresh, kept = {}, 0, 0
    for name, rel, data in _outputs(root):
        full = os.path.join(root, rel)
        h = audio_hash(full)
        prev = old.get(name)
        if prev and prev.get("audio_sha256") == h:
            songs[name] = prev
            kept += 1
            continue
        entry = {"file": rel, "audio_sha256": h}
        entry.update(measure(full))
        # the knob that was set when this was measured, so a figure can be read
        # against the decision that produced it
        entry["gain_db"] = float(data.get("gain_db", 0.0))
        entry["commit"] = _commit(root)
        songs[name] = entry
        fresh += 1
        log(f"measured {name}: {entry['integrated_lufs']:+.1f} LUFS")

    with open(path, "w") as f:
        json.dump({
            "_": "EBU R128 of each finished render; re-measured when a song's "
                 "audio_sha256 changes. See infra/playalong/loudness.py",
            "schema": SCHEMA,
            "songs": songs,
        }, f, indent=2, sort_keys=True)
        f.write("\n")
    log(f"{CACHE_NAME}: {fresh} measured, {kept} reused")
    return songs


def report(songs, focus=None, log=print):
    """The levels, loudest first, with the step to the next song beside each."""
    rows = sorted(songs.items(), key=lambda kv: -kv[1]["integrated_lufs"])
    log("")
    log(f"{'song':28s} {'LUFS':>7s} {'peak':>7s} {'LRA':>6s} {'step':>6s}"
        f" {'gain':>6s}")
    for i, (name, e) in enumerate(rows):
        step = ("" if i + 1 == len(rows) else
                f"{rows[i + 1][1]['integrated_lufs'] - e['integrated_lufs']:+.1f}")
        gain = f"{e['gain_db']:+.1f}" if e["gain_db"] else ""
        mark = " <-" if name == focus else ""
        log(f"{name:28s} {e['integrated_lufs']:+7.1f} {e['true_peak_dbtp']:+7.1f}"
            f" {e['lra']:6.1f} {step:>6s} {gain:>6s}{mark}")
    lufs = [e["integrated_lufs"] for _, e in rows]
    log("")
    log(f"spread {max(lufs) - min(lufs):.1f} dB across {len(rows)} songs; "
        f"median {sorted(lufs)[len(lufs) // 2]:+.1f} LUFS")
    if focus and focus in songs:
        f = songs[focus]["integrated_lufs"]
        above = sum(1 for v in lufs if v > f)
        log(f"{focus} at {f:+.1f} LUFS: {above} louder, "
            f"{len(lufs) - above - 1} quieter, "
            f"{f - sorted(lufs)[len(lufs) // 2]:+.1f} dB off the median")


def main(argv):
    root = argv[1] if len(argv) > 1 else "/work"
    force = "--force" in argv
    focus = next((a for a in argv[2:] if not a.startswith("-")), None)
    report(refresh(root, force=force), focus=focus)
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv))
