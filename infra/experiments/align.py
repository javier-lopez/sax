"""Align a monophonic MusicXML melody to a real recording via chroma + subsequence DTW.

Inputs:  mxl_extracted/score.xml, reloj_backing.wav
Outputs: sync_map.json  {q0, q_end, knots: [[quarter, seconds], ...], measures: [...]}

The melody template is rendered as an idealized chromagram (12 frames per
quarter note, matching the MusicXML divisions). Subsequence DTW lets the
score land anywhere inside the audio, absorbing the guitar intro/outro.
"""

import json
import sys
import xml.etree.ElementTree as ET

import librosa
import numpy as np

SR = 22050
HOP = 512
SCORE_FPQ = 12  # score frames per quarter note

STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_score(path):
    root = ET.parse(path).getroot()
    part = root.find("part")
    divisions = None
    q = 0.0
    notes = []          # (onset_q, dur_q, midi or None)
    measure_starts = [] # (measure_number, onset_q)
    for m in part.findall("measure"):
        d = m.findtext("attributes/divisions")
        if d:
            divisions = int(d)
        measure_starts.append((m.get("number"), q))
        for el in m:
            if el.tag == "note":
                if el.find("grace") is not None:
                    continue
                dur = int(el.findtext("duration")) / divisions
                if el.find("chord") is not None:
                    continue  # monophonic score; ignore stacked notes
                if el.find("rest") is not None:
                    notes.append((q, dur, None))
                else:
                    midi = (
                        (int(el.findtext("pitch/octave")) + 1) * 12
                        + STEP_TO_PC[el.findtext("pitch/step")]
                        + int(el.findtext("pitch/alter") or 0)
                    )
                    notes.append((q, dur, midi))
                q += dur
            elif el.tag == "backup":
                q -= int(el.findtext("duration")) / divisions
            elif el.tag == "forward":
                q += int(el.findtext("duration")) / divisions
    return notes, measure_starts, q


def score_chroma(notes, q_total):
    n_frames = int(round(q_total * SCORE_FPQ))
    C = np.zeros((12, n_frames))
    for onset, dur, midi in notes:
        if midi is None:
            continue
        a = int(round(onset * SCORE_FPQ))
        b = int(round((onset + dur) * SCORE_FPQ))
        C[midi % 12, a:b] = 1.0
        C[(midi + 7) % 12, a:b] = np.maximum(C[(midi + 7) % 12, a:b], 0.33)
    return C


def main():
    notes, measure_starts, q_total = parse_score("mxl_extracted/score.xml")
    pitched = [n for n in notes if n[2] is not None]
    q0 = pitched[0][0]                     # first melody onset (pickup)
    q_end = pitched[-1][0] + pitched[-1][1]  # end of last note
    print(f"score: {len(notes)} events, melody q-span [{q0}, {q_end}] of {q_total}")

    C_full = score_chroma(notes, q_total)
    a = int(round(q0 * SCORE_FPQ))
    b = int(round(q_end * SCORE_FPQ))
    X = C_full[:, a:b]
    X = librosa.util.normalize(X, norm=2, axis=0, fill=True)

    y, _ = librosa.load("reloj_backing.wav", sr=SR)
    Y = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    Y = librosa.util.normalize(Y, norm=2, axis=0, fill=True)
    print(f"audio: {len(y)/SR:.1f}s, chroma {Y.shape[1]} frames")

    D, wp = librosa.sequence.dtw(X=X, Y=Y, metric="cosine", subseq=True)
    wp = wp[::-1]  # ascending
    print(f"dtw path: {len(wp)} steps, audio span "
          f"{wp[0][1]*HOP/SR:.2f}s .. {wp[-1][1]*HOP/SR:.2f}s")

    # median audio time for each score frame
    sf_to_times = {}
    for sf, af in wp:
        sf_to_times.setdefault(int(sf), []).append(af * HOP / SR)
    max_sf = X.shape[1] - 1

    def time_at_scoreframe(sf):
        sf = min(max(int(round(sf)), 0), max_sf)
        # walk outward to the nearest matched frame
        for off in range(0, max_sf + 1):
            for cand in (sf - off, sf + off):
                if cand in sf_to_times:
                    return float(np.median(sf_to_times[cand]))
        raise RuntimeError("empty dtw path")

    # knots at every quarter note within the melody span
    knots = []
    qq = q0
    while qq <= q_end + 1e-6:
        sf = (qq - q0) * SCORE_FPQ
        knots.append([round(qq, 4), time_at_scoreframe(sf)])
        qq += 1.0
    t = np.maximum.accumulate([k[1] for k in knots])
    knots = [[k[0], float(tt)] for k, tt in zip(knots, t)]

    measures = []
    for num, mq in measure_starts:
        if mq < q0:
            measures.append({"n": num, "q": mq, "t": None})
        else:
            tt = float(np.interp(mq, [k[0] for k in knots], [k[1] for k in knots]))
            measures.append({"n": num, "q": mq, "t": round(tt, 3)})

    with open("sync_map.json", "w") as f:
        json.dump({"q0": q0, "q_end": q_end, "q_total": q_total,
                   "knots": knots, "measures": measures}, f, indent=1)

    # diagnostics: per-measure seconds + implied bpm
    print("\nmeasure  start_t   dur_s   bpm")
    timed = [m for m in measures if m["t"] is not None]
    for i, m in enumerate(timed[:-1]):
        dur = timed[i + 1]["t"] - m["t"]
        span_q = timed[i + 1]["q"] - m["q"]
        bpm = 60.0 * span_q / dur if dur > 0.05 else float("inf")
        flag = "  <-- check" if (bpm < 50 or bpm > 140) else ""
        print(f"m{m['n']:>3}  {m['t']:7.2f}  {dur:6.2f}  {bpm:6.1f}{flag}")
    print(f"melody: {knots[0][1]:.2f}s .. {knots[-1][1]:.2f}s")


if __name__ == "__main__":
    sys.exit(main())
