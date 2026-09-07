"""Exhaustive (tempo, offset) grid search: steady-grid hypothesis.

Scores each candidate (quarter_duration d, start_offset t0) by the mean cosine
between the harmony template of each active score quarter and the audio chroma
sampled at that quarter's predicted midpoint. Sharp peak => trustworthy grid.
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
    notes, measure_starts = [], []
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


def main():
    notes, measure_starts, q_total = parse_score("mxl_extracted/score.xml")
    nq = int(round(q_total))

    T = np.zeros((12, nq))
    for onset, dur, midi in notes:
        if midi is None:
            continue
        a, b = int(onset), min(int(np.ceil(onset + dur)), nq)
        T[midi % 12, a:b] += 1.0
        T[(midi + 7) % 12, a:b] += 0.33
    active = T.sum(axis=0) > 0
    Tn = librosa.util.normalize(T, norm=2, axis=0, fill=True)
    act_idx = np.where(active)[0]
    Ta = Tn[:, act_idx]                      # (12, A)
    print(f"score: {nq} quarters, {len(act_idx)} active")

    y, _ = librosa.load("reloj_backing.wav", sr=SR)
    audio_dur = len(y) / SR
    Y = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    Yn = librosa.util.normalize(Y, norm=2, axis=0, fill=True)  # (12, F)
    nF = Yn.shape[1]
    fps = SR / HOP

    ds = np.arange(0.600, 0.800, 0.002)      # quarter duration candidates
    t0s = np.arange(0.0, 25.0, 0.05)         # start offsets
    qmid = act_idx + 0.5

    best = []
    heat = np.zeros((len(ds), len(t0s)))
    for i, d in enumerate(ds):
        # (len(t0s), A) frame indices
        times = t0s[:, None] + qmid[None, :] * d
        fr = np.clip((times * fps).astype(int), 0, nF - 1)
        valid = times < audio_dur - 0.05
        cos = np.einsum("ca,cta->ta", Ta, Yn[:, fr].transpose(0, 1, 2))
        cos = np.where(valid, cos, np.nan)
        score = np.nanmean(cos, axis=1)
        heat[i, :] = score
        j = int(np.nanargmax(score))
        best.append((float(score[j]), float(d), float(t0s[j])))

    best.sort(reverse=True)
    print("\ntop (corr, quarter_dur, t0):")
    for sc, d, t0 in best[:8]:
        print(f"  corr={sc:.4f}  d={d:.3f}s ({60/d:6.2f} bpm)  t0={t0:5.2f}s  "
              f"end={t0 + nq*d:6.1f}s")

    sc, d, t0 = best[0]
    p95 = float(np.nanpercentile(heat, 95))
    print(f"\npeak {sc:.4f} vs p95 {p95:.4f} (sharpness {sc - p95:+.4f})")

    knots = [[float(qi), float(t0 + qi * d)] for qi in range(nq + 1)]
    measures = [{"n": num, "q": mq, "t": round(t0 + mq * d, 3)}
                for num, mq in measure_starts]
    with open("sync_map.json", "w") as f:
        json.dump({"q0": 0.0, "q_end": q_total, "q_total": q_total,
                   "grid": {"quarter_dur": d, "t0": t0, "corr": sc},
                   "knots": knots, "measures": measures}, f, indent=1)
    print(f"melody entry (q=7) at {t0 + 7*d:.2f}s; "
          f"m60 ends {t0 + nq*d:.2f}s of {audio_dur:.2f}s audio")


if __name__ == "__main__":
    main()
