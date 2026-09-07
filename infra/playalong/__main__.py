"""Stage runner: python -m playalong <song-dir> [stage ...]

Stages, in order, each reusing what the previous one left in <song>/build:
  fetch    download the recording and decode an analysis wav
  align    fit the score onto the recording -> sync_map.json
  preview  a handful of stills, to check the alignment by eye
  clip F T seconds F..T only, to build/clip.mkv -- the fast loop for
           settling align.offset by ear

`offset=<seconds>` anywhere in the arguments writes that value into song.json
before running, so tuning by ear is one command per try:

  <song> align clip 8 30 offset=0.6
  render   the full video
`all` is fetch + align + render.
"""

import os
import sys

from . import align, audio, config, render
from .score import Score, build_row_score, extract

STAGES = ("fetch", "align", "preview", "clip", "render")


def _prepare(song):
    extract(song.score_file, song.score_xml)
    score = Score(song.score_xml)
    build_row_score(song.score_xml, song.rows_xml,
                    song.layout["measures_per_row"])
    print(f"score: {score.n_measures} measures, "
          f"{score.q_total:.0f} quarters, {score.beats_per_measure:g}/4 feel")
    return score


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    argv_rest = argv[2:]
    kv = dict(a.split("=", 1) for a in argv_rest if "=" in a and not _is_num(a))
    if "scaffold" in argv_rest:
        # runs before config.load, which needs a song.json that does not exist yet
        from .score import Score, extract
        score_name = kv["score"]
        tmp = os.path.join(argv[1], ".scaffold.xml")
        extract(os.path.join(argv[1], score_name), tmp)
        bpm = Score(tmp).declared_bpm
        os.remove(tmp)
        path = config.scaffold(argv[1], score_name, kv.get("url"),
                               kv.get("title") or score_name,
                               kv.get("credit", ""), bpm)
        print(f"wrote {path}"
              + (f" (tempo swept around the score's {bpm:g} bpm)" if bpm
                 else " (score declares no tempo; sweeping blind)"))
        argv_rest = [a for a in argv_rest if a != "scaffold" and "=" not in a]
        if not argv_rest:
            return 0

    over = [a for a in argv_rest if a.startswith("offset=")]
    if over:
        _write_offset(argv[1], over[-1].split("=", 1)[1])
        argv_rest = [a for a in argv_rest if not a.startswith("offset=")]
    argv_rest = [a for a in argv_rest if "=" not in a or _is_num(a)]
    song = config.load(argv[1])
    # numeric trailing args belong to `clip`
    span = [float(a) for a in argv_rest if _is_num(a)]
    stages = [a for a in argv_rest if not _is_num(a)] or ["all"]
    if "all" in stages:
        stages = ["fetch", "align", "render"]
    bad = [s for s in stages if s not in STAGES]
    if bad:
        print(f"unknown stage(s): {bad}; pick from {STAGES} or 'all'")
        return 2

    score = _prepare(song)
    src = None

    if "fetch" in stages:
        src = audio.local_or_fetch(song)
        audio.to_wav(src, song.wav)
        print(f"audio: {src} ({audio.duration(src):.1f}s)")

    if "align" in stages:
        align.run(song, score)

    if "clip" in stages and len(span) != 2:
        print("clip needs two times: ./run.sh <song> clip 10 30")
        return 2

    if {"preview", "render", "clip"} & set(stages):
        import json
        with open(song.sync_map) as f:
            sync = json.load(f)
        if "preview" in stages:
            render.preview(song, score, sync)
        if {"render", "clip"} & set(stages):
            if src is None:
                src = audio.local_or_fetch(song)
            render.render_video(song, score, sync, src, audio.duration(src),
                                span=tuple(span) if "clip" in stages else None)
    return 0


def _write_offset(song_dir, spec):
    """Persist the ear offset. It is the song's setting, not a run flag: the
    value that sounded right must be the value the full render uses.

    `offset=1.2` sets the flat offset; `offset=25:1.45` pins measure 25 and
    leaves the rest to interpolate, for a performance that breathes.
    """
    import json
    path = os.path.join(song_dir, "song.json")
    with open(path) as f:
        data = json.load(f)
    align = data.setdefault("align", {})
    if ":" in spec:
        m, v = spec.split(":", 1)
        anchors = {int(a[0]): float(a[1]) for a in align.get("anchors") or []}
        anchors[int(m)] = float(v)
        align["anchors"] = [[k, anchors[k]] for k in sorted(anchors)]
        value = f"m{int(m)} = {float(v):+.3f}s"
    else:
        align["offset"] = float(spec)
        value = f"{float(spec):+.3f}s"
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"align offset {value} written to {path}")


def _is_num(tok):
    try:
        float(tok)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv))
