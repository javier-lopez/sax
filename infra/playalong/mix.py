"""Loudness for a track that is played over: even, and shaped by the score.

A backing recording is mixed for listening, and an arrangement that builds
from a whispered intro to a full chorus is exactly right there. Played over --
often outdoors, with both hands on the horn -- the same build buries the intro
under street noise and lands every new section as a jolt nobody can ride.

A generic leveller (dynaudnorm) was tried first and measured on el-rey-leon.
Its smoothing window is centred, so it starts turning down *before* a loud
section arrives and up before a quiet one: the end of every verse sagged
into the chorus. It also cannot know what the player wants, which is not flat
but shaped -- louder where the horn rests, a build into the ending.

The pipeline knows both where the music changes and where the horn plays, so
the gain is computed here instead:

- from the recording's own momentary loudness, smoothed by a median over a
  few bars. A median keeps a section change at the moment it happens; a mean
  or a Gaussian smears it both ways, which was the sag. It is computed on the
  meter's own 100 ms frames rather than per bar: a chorus that arrives on a
  pickup, mid-bar, moved the correction a bar early and dropped the end of
  the verse.
- plus an intent curve from the score: a boost over every long wait, lifts for
  the sections the player names -- each either flat or climbing across itself,
  since an intro that the arrangement writes as a build should still build -- and an optional crescendo to the end. Each
  step completes exactly on its boundary; the crescendo instead *starts* on
  its measure and climbs from there, because a build the player asked for at
  3:33 must not have happened by 3:33.
- levelling stops at the recording's last hit. What follows is decay, and
  raising a decay towards the song's level is how a clean ending turns into a
  ringing one.
- then one static gain to the target and a peak limiter. Nothing downstream
  looks ahead.
"""

import json
import os
import re
import subprocess

import numpy as np

SR = 48000
# a bar's gain may move this far from the song's median, no further: enough to
# lift a whispered intro level with the body, not enough to make hiss of it
MAX_BOOST_DB, MAX_CUT_DB = 24.0, 12.0
MEDIAN_BARS = 5
# a section marked "flatten" is levelled bar by bar, which evens out its own
# accents too: a passage whose every other bar is 3 dB down cannot be placed by
# level alone, only moved up and down as a block. Two bars was too slow to
# follow it -- the variation being flattened is itself bar-length.
FLATTEN_BARS = 1
# how long a level correction takes to cross a barline: long enough not to
# click, short enough to read as the section changing rather than a fade
EDGE_S = 0.3


def _decode(src):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-f", "f32le", "-ac", "2",
         "-ar", str(SR), "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def _momentary(src):
    """(time, momentary LUFS) every 100 ms, from ffmpeg's EBU R128 meter."""
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-v", "verbose", "-i", src,
         "-af", "ebur128=framelog=verbose", "-f", "null", "-"],
        check=True, capture_output=True, text=True).stderr
    rows = re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+|-inf)", log)
    t = np.array([float(a) for a, _ in rows])
    m = np.array([float(b) if b != "-inf" else -120.0 for _, b in rows])
    return t, m


def _integrated(x):
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-f", "f32le", "-ac", "2",
         "-ar", str(SR), "-i", "-", "-af", "ebur128", "-f", "null", "-"],
        input=x.astype(np.float32).tobytes(), check=True,
        capture_output=True).stderr.decode()
    return float(re.findall(r"I:\s*(-?[\d.]+) LUFS", log)[-1])


def _level_gain(t_m, m, window):
    """Per meter frame, the dB that bring the recording to its own median."""
    step = float(np.median(np.diff(t_m)))
    k = max(1, int(round(window / step / 2)))
    padded = np.pad(m, k, mode="edge")
    smooth = np.array([np.median(padded[i:i + 2 * k + 1]) for i in range(len(m))])
    return np.clip(np.median(smooth) - smooth, -MAX_CUT_DB, MAX_BOOST_DB)


def _crescendo(ts, cres):
    """The closing build: steady until its measure, then a climb held to the end.

    It adds to whatever level the music is already at. A build that started
    from zero dropped the floor out from under the section before it -- the
    band fell away exactly where the ending was supposed to take off.
    """
    if not cres:
        return 0.0
    a, span, db = cres
    return db * np.clip((ts - a) / span, 0.0, 1.0)


def _intent(ts, spans, ramp, bar, dt, enters=()):
    """The score-driven shape in dB, from (start, end, dB, dB at end) spans.

    A span whose two levels differ climbs across itself; the rest hold. Every
    change completes on its boundary, taking `ramp` to get there: the player
    enters over a level that has already settled, and a section that should
    hit harder is arrived at, not switched on after the fact.
    """
    step = np.zeros_like(ts)
    for a, b, db0, db1 in spans:
        inside = (ts >= a) & (ts < b)
        if db1 == db0:
            step += db0 * inside
        else:
            frac = np.clip((ts - a) / max(b - a, 1e-6), 0.0, 1.0)
            step += (db0 + (db1 - db0) * frac) * inside
    n = max(1, int(round(ramp / dt)))
    c = np.concatenate([[0.0], np.cumsum(np.pad(step, (0, n), mode="edge"))])
    ahead = (c[n:n + len(ts)] - c[:len(ts)]) / n
    # A rise is anticipated and a fall lands on its downbeat. Taking the higher
    # of the two does both: before a lift the ramp is above the old level, so
    # the music grows into the section; before a drop it is below the level
    # still playing, so the section plays out to its last bar and the band
    # drops with the barline, the way a band does.
    curve = np.maximum(step, ahead)
    # A section may set its own entry instead: the change then starts on its
    # first downbeat and takes as long as it asks for. What sounds right is not
    # the same at every seam -- a chorus can be walked down into over a few
    # bars, while the passage after it has to arrive on the beat, not before.
    for a, bars in enters:
        lo = float(np.interp(a - dt, ts, step))
        hi = float(np.interp(a + dt, ts, step))
        span = bars * bar
        before = (ts >= a - ramp) & (ts < a)
        curve[before] = lo
        after = (ts >= a) & (ts <= a + span)
        frac = np.clip((ts[after] - a) / span, 0.0, 1.0) if span > 0 else 1.0
        curve[after] = lo + (hi - lo) * frac
    return curve


def plan(song, score, sync):
    """Everything the gain needs from the score, as spans in seconds.

    - every long wait, boosted: the horn is silent, so the track carries the
      song. The ramp down finishes at the entry, so the player comes in over
      the level they will play at, not over a fade;
    - `sections`: measure ranges the player wants lifted or lowered -- the
      arrangement's own build, which levelling bar by bar would flatten;
    - `crescendo`: from a measure to the end, reaching its level in a few bars
      and holding it, so the last bars land at full weight rather than
      swelling only on the final chord.
    """
    kq = [k[0] for k in sync["knots"]]
    kt = [k[1] for k in sync["knots"]]
    q_to_t = lambda q: float(np.interp(q, kq, kt))
    m_to_t = lambda n: q_to_t(score.measure_q(int(n) - 1))
    bar = score.beats_per_measure * sync["structure"]["quarter_dur"]
    cfg = song.loudness
    min_bars = song.layout["countdown_min_bars"]
    spans = []
    for q0, q1 in score.melody_gaps(1.0):
        a = 0.0 if q0 <= 0 else q_to_t(q0)
        b = q_to_t(q1)
        if (b - a) / bar >= min_bars:
            rest = float(cfg.get("rest_boost_db", 2.0))
            spans.append((a, b, rest, rest))
    flat, enters = [], []
    for sec in cfg.get("sections", []):
        a, b = m_to_t(sec["from_measure"]), m_to_t(sec["to_measure"] + 1)
        db = float(sec["db"])
        spans.append((a, b, db, float(sec.get("to_db", db))))
        if sec.get("flatten"):
            flat.append((a, b))
        if "enter_bars" in sec:
            enters.append((a, float(sec["enter_bars"])))
    cres = None
    if cfg.get("crescendo"):
        c = cfg["crescendo"]
        start = m_to_t(c["from_measure"])
        cres = (start, float(c.get("ramp_bars", 4)) * bar, float(c["db"]))
        # a section that ends where the build begins holds its level to the end
        # instead of expiring under it
        spans = [(a, 1e9 if abs(b - start) < 1e-3 else b, db0, db1)
                 for a, b, db0, db1 in spans]
    last = score.pitched[-1]
    return {"m1": q_to_t(0.0), "bar": bar, "spans": spans, "flatten": flat,
            "enters": enters, "cres": cres,
            "last_note_end": q_to_t(last[0] + last[1]),
            "ramp": float(cfg.get("ramp_bars", 2)) * bar,
            "fade": float((cfg.get("ending") or {}).get("fade_s", 0.0))}


def level(src, dest, song, score, sync, tail, log=print):
    cfg = song.loudness
    target = float(cfg.get("target", -14.0))
    peak = float(cfg.get("peak", -1.0))
    p = plan(song, score, sync)
    stamp = {"src": os.path.getmtime(src), "plan": p, "target": target,
             "peak": peak, "tail": tail, "v": 11}
    sidecar = dest + ".json"
    if os.path.exists(dest) and os.path.exists(sidecar):
        with open(sidecar) as f:
            if json.load(f) == json.loads(json.dumps(stamp)):
                return dest

    x = _decode(src)
    dur = len(x) / SR
    t_m, m = _momentary(src)
    dt = 0.01
    ts = np.arange(0.0, dur, dt)
    g = np.interp(ts, t_m, _level_gain(t_m, m, MEDIAN_BARS * p["bar"]))
    if p["flatten"]:
        fast = np.interp(ts, t_m, _level_gain(t_m, m, FLATTEN_BARS * p["bar"]))
        inside = np.zeros_like(ts, dtype=bool)
        for a, b in p["flatten"]:
            inside |= (ts >= a) & (ts < b)
        g = np.where(inside, fast, g)

    # the recording's last hit: the loudest moment from the final written note
    # on. Everything after it is that chord decaying, so the gain freezes -- and
    # optionally fades, for an ending that stops instead of ringing
    after = t_m >= p["last_note_end"] - p["bar"]
    t_hit = float(t_m[after][int(np.argmax(m[after]))])
    g[ts > t_hit] = float(np.interp(t_hit, ts, g))

    # the correction crosses a boundary over EDGE_S, centred on it: long enough
    # not to click, short enough to read as the section changing
    k = max(1, int(EDGE_S / dt))
    g = np.convolve(np.pad(g, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1),
                    mode="same")[k:-k]
    # the same crossing as a levelling change, so a drop on a barline is a
    # section change rather than a click
    intent = (_intent(ts, p["spans"], p["ramp"], p["bar"], dt, p["enters"])
              + _crescendo(ts, p["cres"]))
    g += np.convolve(np.pad(intent, k, mode="edge"),
                     np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]

    amp = 10 ** (g / 20)
    if p["fade"]:
        # starts after the hit has spoken, so the boom lands whole
        amp *= np.clip(1.0 - (ts - (t_hit + 0.3)) / p["fade"], 0.0, 1.0)
    y = x * np.interp(np.arange(len(x)) / SR, ts, amp)[:, None]
    shift = target - _integrated(y)

    limit = 10 ** (peak / 20)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", "-f", "f32le", "-ac", "2",
         "-ar", str(SR), "-i", "-",
         "-af", f"volume={shift:.2f}dB,alimiter=limit={limit:.4f}:attack=5"
                f":release=50:level=disabled,apad=pad_dur={tail}",
         "-c:a", "libopus", "-b:a", "192k", dest],
        input=y.astype(np.float32).tobytes(), check=True, capture_output=True)
    log(f"levelled: last hit at {t_hit:.1f}s"
        + (f", fade {p['fade']:.1f}s" if p["fade"] else "") + "; "
        + ", ".join(f"{a:.0f}-{b:.0f}s {db:+.1f} dB" for a, b, db, _ in p["spans"])
        + (f", crescendo from {p['cres'][0]:.0f}s over {p['cres'][1]:.0f}s "
           f"{p['cres'][2]:+.1f} dB" if p["cres"] else "")
        + f"; {shift:+.1f} dB to {target:.0f} LUFS")
    with open(sidecar, "w") as f:
        json.dump(stamp, f)
    return dest
