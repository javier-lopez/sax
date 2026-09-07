"""Beat-based alignment for a backing track (melody NOT present in audio).

Strategy: the accompaniment carries the harmony and a steady bolero pulse.
1. Beat-track the audio; report tempo stability.
2. Build a per-quarter harmony template from the score (melody pitch class +
   fifth, held; rest quarters are neutral).
3. Beat-synchronous chroma cross-correlation: slide the 240-quarter template
   over the beat sequence to find which beat is score quarter 0.
Outputs sync_map.json (same schema align.py used).
"""

import json
import xml.etree.ElementTree as ET

import librosa
import numpy as np

SR = 22050
HOP = 512
STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_score(path):
    root = ET.parse(path).getroot()
    part = root.find("part")
    divisions = None
    q = 0.0
    notes = []
    measure_starts = []
    for m in part.findall("measure"):
        d = m.findtext("attributes/divisions")
        if d:
            divisions = int(d)
        measure_starts.append((m.get("number"), q))
        for el in m:
            if el.tag == "note":
                if el.find("grace") is not None or el.find("chord") is not None:
                    continue
                dur = int(el.findtext("duration")) / divisions
                if el.find("rest") is not None:
                    notes.append((q, dur, None))
                else:
                    midi = ((int(el.findtext("pitch/octave")) + 1) * 12
                            + STEP_TO_PC[el.findtext("pitch/step")]
                            + int(el.findtext("pitch/alter") or 0))
                    notes.append((q, dur, midi))
                q += dur
            elif el.tag == "backup":
                q -= int(el.findtext("duration")) / divisions
            elif el.tag == "forward":
                q += int(el.findtext("duration")) / divisions
    return notes, measure_starts, q


def quarter_template(notes, q_total):
    nq = int(round(q_total))
    T = np.zeros((12, nq))
    for onset, dur, midi in notes:
        if midi is None:
            continue
        a, b = int(onset), min(int(np.ceil(onset + dur)), nq)
        T[midi % 12, a:b] += 1.0
        T[(midi + 7) % 12, a:b] += 0.33
    return T


def main():
    notes, measure_starts, q_total = parse_score("mxl_extracted/score.xml")
    nq = int(round(q_total))
    print(f"score: {nq} quarters, {len(measure_starts)} measures")

    y, _ = librosa.load("reloj_backing.wav", sr=SR)
    dur = len(y) / SR

    # where does sound actually start/end?
    yt, idx = librosa.effects.trim(y, top_db=30)
    print(f"audio {dur:.2f}s, energy from {idx[0]/SR:.2f}s to {idx[1]/SR:.2f}s")

    oenv = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=SR,
                                           hop_length=HOP, units="time",
                                           trim=False)
    ibis = np.diff(beats)
    tempo = float(np.atleast_1d(tempo)[0])
    print(f"beat_track: tempo={tempo:.1f} bpm, {len(beats)} beats, "
          f"first={beats[0]:.2f}s last={beats[-1]:.2f}s")
    print(f"ibi: median={np.median(ibis):.3f}s  std={np.std(ibis):.3f}s  "
          f"min={ibis.min():.3f}  max={ibis.max():.3f}")
    tcurve = librosa.feature.tempo(onset_envelope=oenv, sr=SR,
                                   hop_length=HOP, aggregate=None)
    print(f"dynamic tempo: median={np.median(tcurve):.1f} "
          f"p10={np.percentile(tcurve,10):.1f} p90={np.percentile(tcurve,90):.1f}")

    # hypothesis A: audio IS the 60 measures on a steady grid from t0
    grid_bpm = 60.0 * nq / dur
    print(f"\nhypothesis A (steady, exact fit): {grid_bpm:.2f} bpm, "
          f"quarter = {dur/nq:.4f}s")

    # beat-synchronous chroma correlation scan
    C = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    bframes = librosa.time_to_frames(beats, sr=SR, hop_length=HOP)
    Cb = librosa.util.sync(C, bframes, aggregate=np.median)
    Cb = librosa.util.normalize(Cb, norm=2, axis=0, fill=True)
    T = quarter_template(notes, q_total)
    Tn = librosa.util.normalize(T, norm=2, axis=0, fill=True)
    # active = quarters where the template has content (skip neutral rests)
    active = T.sum(axis=0) > 0

    nb = Cb.shape[1]
    scores = []
    for off in range(-4, nb - nq + 5):
        cols = []
        for qi in range(nq):
            bi = off + qi
            if not active[qi] or bi < 0 or bi >= nb:
                continue
            cols.append(float(np.dot(Tn[:, qi], Cb[:, bi])))
        if cols:
            scores.append((off, float(np.mean(cols)), len(cols)))
    scores.sort(key=lambda s: -s[1])
    print("\ntop beat offsets (offset, mean_cosine, quarters_used):")
    for off, sc, n in scores[:5]:
        t0 = beats[off] if 0 <= off < nb else off * np.median(ibis) + beats[0]
        print(f"  offset {off:+3d} -> score q0 at {t0:6.2f}s   corr={sc:.4f}  n={n}")

    best_off = scores[0][0]

    # build knots: quarter q -> beat time (extrapolate at edges)
    knots = []
    med = float(np.median(ibis))
    for qi in range(nq + 1):
        bi = best_off + qi
        if bi < 0:
            t = float(beats[0]) + (bi) * med
        elif bi >= nb:
            t = float(beats[-1]) + (bi - (nb - 1)) * med
        else:
            t = float(beats[bi])
        knots.append([float(qi), t])
    tarr = np.maximum.accumulate([k[1] for k in knots])
    knots = [[k[0], float(t)] for k, t in zip(knots, tarr)]

    measures = []
    for num, mq in measure_starts:
        tt = float(np.interp(mq, [k[0] for k in knots], [k[1] for k in knots]))
        measures.append({"n": num, "q": mq, "t": round(tt, 3)})

    with open("sync_map.json", "w") as f:
        json.dump({"q0": 0.0, "q_end": q_total, "q_total": q_total,
                   "beat_offset": best_off, "knots": knots,
                   "measures": measures}, f, indent=1)

    print("\nmeasure downbeats (every 4th):")
    for m in measures[::4]:
        print(f"  m{m['n']:>3} -> {m['t']:7.2f}s")
    print(f"last measure ends ~{knots[-1][1]:.2f}s (audio {dur:.2f}s)")


if __name__ == "__main__":
    main()
