"""Structure-aware grid alignment.

The recording contains ghost measures the user's transcription compressed:
G guitar bars before his m1, and B extra guitar bars inside his m36 break
(the original chart says "guitarra 7" and "guitarra 2"). We grid-search
(G, B, quarter_dur, t0) and score by harmony-template correlation on the
active quarters only. The winning structure yields sync_map.json for HIS
60-measure score.
"""

import json
import xml.etree.ElementTree as ET

import librosa
import numpy as np

SR = 22050
HOP = 512
STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
BREAK_Q = 144  # his q >= this is after the m36 guitar break (m37 starts at 144)


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
                if el.find("rest") is None:
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


def his_q_to_orig(q, G, B):
    if q <= 140:
        return q + 4 * G
    if q >= BREAK_Q:
        return q + 4 * G + 4 * B
    # inside the m36 break bar: stretch its 4 quarters over 4 + 4B
    return 140 + 4 * G + (q - 140) * (4 + 4 * B) / 4.0


def main():
    notes, measure_starts, q_total = parse_score("mxl_extracted/score.xml")

    y, _ = librosa.load("reloj_backing.wav", sr=SR)
    audio_dur = len(y) / SR
    Y = librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP)
    Yn = librosa.util.normalize(Y, norm=2, axis=0, fill=True)
    nF = Yn.shape[1]
    fps = SR / HOP

    # active-quarter harmony template in HIS timeline
    nq = int(round(q_total))
    T = np.zeros((12, nq))
    for onset, dur, midi in notes:
        a, b = int(onset), min(int(np.ceil(onset + dur)), nq)
        T[midi % 12, a:b] += 1.0
        T[(midi + 7) % 12, a:b] += 0.33
    active = np.where(T.sum(axis=0) > 0)[0]
    Ta = librosa.util.normalize(T[:, active], norm=2, axis=0, fill=True)

    ds = np.arange(0.650, 0.700, 0.001)
    t0s = np.arange(-1.0, 8.0, 0.02)
    results = []
    for G in (5, 6, 7, 8, 9):
        for B in (0, 1, 2):
            oq = np.array([his_q_to_orig(q + 0.5, G, B) for q in active])
            q_last = his_q_to_orig(240, G, B)
            best = (-1, None, None)
            for d in ds:
                times = t0s[:, None] + oq[None, :] * d
                fr = np.clip((times * fps).astype(int), 0, nF - 1)
                cos = np.einsum("ca,cta->ta", Ta, Yn[:, fr])
                sc = np.mean(cos, axis=1)
                # the whole piece must fit inside the audio
                fits = (t0s > -0.5) & (t0s + q_last * d <= audio_dur)
                sc = np.where(fits, sc, -np.inf)
                j = int(np.argmax(sc))
                if sc[j] > best[0]:
                    best = (float(sc[j]), float(d), float(t0s[j]))
            if best[1] is not None:
                results.append((best[0], G, B, best[1], best[2]))

    results.sort(reverse=True)
    print("corr    G  B  quarter_dur   bpm    t0    entry   end_of_his_m60")
    for sc, G, B, d, t0 in results:
        end = t0 + his_q_to_orig(240, G, B) * d
        entry = t0 + his_q_to_orig(7, G, B) * d
        print(f"{sc:.4f}  {G}  {B}   {d:.3f}s   {60/d:6.2f}  {t0:5.2f}s  {entry:5.2f}s  {end:6.1f}s")

    # the user's ear anchors the melody entry near 23s; prefer candidates there
    anchored = [r for r in results
                if 21.5 <= r[4] + his_q_to_orig(7, r[1], r[2]) * r[3] <= 24.5]
    if anchored:
        print(f"\n{len(anchored)} candidates inside the 21.5-24.5s entry window")
        results = anchored
    sc, G, B, d, t0 = results[0]
    print(f"\nwinner: G={G} ghost head bars, B={B} extra break bars, "
          f"{60/d:.2f} bpm, t0={t0:.2f}s, corr={sc:.4f}")
    if len(results) > 1:
        print(f"margin over runner-up: {sc - results[1][0]:+.4f}")

    knots = []
    for qi in range(nq + 1):
        knots.append([float(qi), float(t0 + his_q_to_orig(qi, G, B) * d)])
    measures = [{"n": num, "q": mq,
                 "t": round(t0 + his_q_to_orig(mq, G, B) * d, 3)}
                for num, mq in measure_starts]
    with open("sync_map.json", "w") as f:
        json.dump({"q0": 0.0, "q_end": q_total, "q_total": q_total,
                   "structure": {"ghost_head_bars": G, "extra_break_bars": B,
                                 "quarter_dur": d, "t0": t0, "corr": sc},
                   "knots": knots, "measures": measures}, f, indent=1)

    print(f"\nmelody entry (his q=7, pickup) at {t0 + his_q_to_orig(7, G, B)*d:.2f}s")
    for m in measures[::8]:
        print(f"  m{m['n']:>3} -> {m['t']:7.2f}s")
    print(f"his m60 ends {t0 + his_q_to_orig(240, G, B)*d:.2f}s of {audio_dur:.2f}s")


if __name__ == "__main__":
    main()
