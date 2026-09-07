"""Fit the written score onto the recording: a structure-aware grid search.

The problem the DTW and beat-tracking experiments could not solve: a lead
sheet is not a transcription of the record. It omits accompaniment bars at the
head and compresses multi-bar breaks into a single written bar, so no monotone
warping of one onto the other exists. What does exist is a *steady* grid plus
a small integer description of what the paper left out.

So we grid-search that description directly -- (head bars, stretched bars,
quarter duration, start offset, transposition) -- and score each candidate by
how well the score's harmony template correlates with the audio chroma at the
quarters it predicts. Only quarters carrying melody are scored; rests are
evidence-free and would just dilute the correlation.
"""

import itertools
import json
import os

import librosa
import numpy as np

HOP = 512


def _sweep(spec):
    """A three-element list is a [start, stop, step] sweep; anything else is a
    literal list of candidates. Only continuous parameters use sweeps."""
    if len(spec) == 3 and isinstance(spec, list):
        return np.arange(spec[0], spec[1], spec[2])
    return np.asarray(spec, dtype=float)


def q_map(head_bars, stretches, beats_per_measure, q_total):
    """Score quarters -> recording quarters, as a piecewise-linear map.

    Breakpoints: the head insertion shifts everything by head_bars measures;
    each stretch expands one written measure over (1 + extra) real measures,
    which is what a chart means by a single bar labelled "guitar x4".
    """
    xs, ys = [0.0], [beats_per_measure * head_bars]
    off = beats_per_measure * head_bars
    for q_start, q_len, extra in sorted(stretches):
        xs.append(q_start)
        ys.append(q_start + off)
        off += beats_per_measure * extra
        xs.append(q_start + q_len)
        ys.append(q_start + q_len + off)
    tail = q_total + beats_per_measure
    xs.append(tail)
    ys.append(tail + off)
    return np.asarray(xs), np.asarray(ys)


def _stretch_combos(score, spec):
    """Cartesian product over each declared stretch's candidate bar counts."""
    if not spec:
        return [()]
    per = []
    for s in spec:
        idx = int(s["measure"]) - 1
        q_start = score.measure_q(idx)
        q_len = score.measure_q(idx + 1) - q_start
        per.append([(q_start, q_len, int(e)) for e in s["extra_bars"]])
    return list(itertools.product(*per))


def search(score, wav_path, cfg, log=print):
    """Return the winning structure dict."""
    T = score.harmony_template()
    active = np.where(T.sum(axis=0) > 0)[0]
    Ta = librosa.util.normalize(T[:, active], norm=2, axis=0, fill=True)

    y, sr = librosa.load(wav_path, sr=None)
    audio_dur = len(y) / sr
    Y = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP)
    Yn = librosa.util.normalize(Y, norm=2, axis=0, fill=True)
    n_frames = Yn.shape[1]
    fps = sr / HOP
    log(f"audio {audio_dur:.1f}s, {n_frames} chroma frames; "
        f"score {len(active)} active quarters of {int(round(score.q_total))}")

    ds = _sweep(cfg["quarter_dur"])
    t0s = _sweep(cfg["t0"])
    bpm_m = score.beats_per_measure
    q_entry = score.pitched[0][0]

    results = []
    for head in cfg["head_bars"]:
        for stretches in _stretch_combos(score, cfg["stretch"]):
            xs, ys = q_map(head, stretches, bpm_m, score.q_total)
            # sample each active quarter at its midpoint: most harmonically stable
            oq = np.interp(active + 0.5, xs, ys)
            q_last = float(np.interp(score.q_total, xs, ys))
            for k in cfg["transpose"]:
                Tk = np.roll(Ta, int(k), axis=0)
                for d in ds:
                    times = t0s[:, None] + oq[None, :] * d
                    fr = np.clip((times * fps).astype(int), 0, n_frames - 1)
                    cos = np.einsum("ca,cta->ta", Tk, Yn[:, fr])
                    sc = np.mean(cos, axis=1)
                    fits = (t0s > -1.0) & (t0s + q_last * d <= audio_dur)
                    sc = np.where(fits, sc, -np.inf)
                    j = int(np.argmax(sc))
                    if np.isfinite(sc[j]):
                        results.append((float(sc[j]), int(head), stretches,
                                        int(k), float(d), float(t0s[j])))
    if not results:
        raise RuntimeError("no candidate grid fits inside the audio; "
                           "widen quarter_dur/t0 or check the score length")

    results.sort(key=lambda r: -r[0])
    log("\n corr    head  stretch  transp  quarter    bpm     t0     entry")
    for sc, head, st, k, d, t0 in results[:8]:
        xs, ys = q_map(head, st, bpm_m, score.q_total)
        entry = t0 + float(np.interp(q_entry, xs, ys)) * d
        log(f" {sc:.4f}   {head:>3}   {[e for *_, e in st]!s:>7}  {k:+3d}   "
            f"{d:.3f}s  {60/d:6.2f}  {t0:6.2f}s  {entry:6.2f}s")

    window = cfg.get("entry_window")
    if window:
        lo, hi = window
        def entry_of(r):
            xs, ys = q_map(r[1], r[2], bpm_m, score.q_total)
            return r[5] + float(np.interp(q_entry, xs, ys)) * r[4]
        anchored = [r for r in results if lo <= entry_of(r) <= hi]
        if anchored:
            log(f"\n{len(anchored)} candidates inside the {lo}-{hi}s entry window")
            results = anchored
        else:
            log(f"\nWARNING: no candidate enters inside {lo}-{hi}s; ignoring the window")

    sc, head, stretches, k, d, t0 = results[0]
    margin = sc - results[1][0] if len(results) > 1 else float("nan")
    log(f"\nwinner: head={head} bars, stretch={[e for *_, e in stretches]}, "
        f"transpose={k:+d}, {60/d:.2f} bpm, t0={t0:.2f}s, corr={sc:.4f} "
        f"(margin {margin:+.4f})")
    return {"head_bars": head, "stretch": [list(s) for s in stretches],
            "transpose": k, "quarter_dur": d, "t0": t0, "corr": sc}


def _locked(score, cfg):
    """A hand-tuned structure wins over anything the search would pick.

    Correlation maximises harmonic agreement, not playability; once the player
    has settled the numbers by ear, re-deriving them is a regression risk.
    """
    lock = dict(cfg["lock"])
    spec = cfg["stretch"]
    extras = lock.pop("extra_bars", None)
    if extras is not None:
        st = []
        for s, e in zip(spec, extras):
            idx = int(s["measure"]) - 1
            q_start = score.measure_q(idx)
            st.append([q_start, score.measure_q(idx + 1) - q_start, int(e)])
        lock["stretch"] = st
    lock.setdefault("stretch", [])
    lock.setdefault("head_bars", 0)
    lock.setdefault("transpose", 0)
    lock.setdefault("corr", None)
    return lock


def build_sync_map(score, structure, out_path):
    """Freeze the structure into the only artefact the renderer reads:
    quarter -> seconds knots, plus measure downbeats."""
    xs, ys = q_map(structure["head_bars"],
                   [tuple(s) for s in structure["stretch"]],
                   score.beats_per_measure, score.q_total)
    d, t0 = structure["quarter_dur"], structure["t0"]
    # Harmony correlation fixes tempo and key but not absolute phase: a backing
    # track holds one chord for whole bars, so nothing in the signal says where
    # inside the chord the beat is. This is the ear's knob. Positive = later.
    #
    # `anchors` generalises it. A real performance breathes -- the player
    # stretches a phrase, leans on a cadence -- while the score is square, so no
    # single straight line can track it. Each anchor pins one measure's offset by
    # ear and the rest interpolate, which turns one global constant into a curve
    # that follows the performance. A sequenced backing track needs none; a live
    # take needs several.
    off = structure.get("offset", 0.0)
    anchors = structure.get("anchors") or []
    if anchors:
        aq = [score.measure_q(int(m) - 1) for m, _ in anchors]
        av = [float(v) for _, v in anchors]
        order = np.argsort(aq)
        aq = np.asarray(aq)[order]
        av = np.asarray(av)[order]
        off_at = lambda q: float(np.interp(q, aq, av))
    else:
        off_at = lambda q: off

    def q_to_t(q):
        return float(t0 + np.interp(q, xs, ys) * d + off_at(q))

    nq = int(round(score.q_total))
    knots = [[float(q), q_to_t(q)] for q in range(nq + 1)]
    measures = [{"n": num, "q": mq, "t": round(q_to_t(mq), 3)}
                for num, mq in score.measures]
    data = {"q_total": score.q_total,
            "beats_per_measure": score.beats_per_measure,
            "structure": structure, "knots": knots, "measures": measures}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=1)
    return data


def run(song, score, log=print):
    cfg = song.align
    if cfg.get("lock"):
        structure = _locked(score, cfg)
        log(f"structure locked by song.json: {structure}")
    else:
        structure = search(score, song.wav, cfg, log=log)
    structure["offset"] = cfg.get("offset", 0.0)
    structure["anchors"] = cfg.get("anchors") or []
    if structure["anchors"]:
        log("ear anchors: " + ", ".join(f"m{int(m)}{v:+.3f}s"
                                        for m, v in structure["anchors"]))
    elif structure["offset"]:
        log(f"applying ear offset {structure['offset']:+.3f}s to the whole map")
    data = build_sync_map(score, structure, song.sync_map)
    entry = float(np.interp(score.pitched[0][0],
                            [k[0] for k in data["knots"]],
                            [k[1] for k in data["knots"]]))
    log(f"melody entry at {entry:.2f}s; "
        f"m1 at {data['measures'][0]['t']:.2f}s, "
        f"last measure at {data['measures'][-1]['t']:.2f}s")
    return data
