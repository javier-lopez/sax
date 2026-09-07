"""Play-along video v2: karaoke-style two-row layout with a sweeping cursor.

- One 4-measure system ("row") per slot; current row + next row visible.
  Rows alternate slots: when the cursor finishes a row, the stale slot is
  replaced by the row after next (classic two-line karaoke pattern).
- Continuous vertical cursor band sweeping the active row, plus a red glow
  on the current note.
- Bar countdown overlays during the guitar waits (7-bar intro, 2-bar break).

Usage: python render2.py test   -> writes test_*.png stills for inspection
       python render2.py full   -> pipes 30fps frames to ffmpeg -> mkv
"""

import io
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import cairosvg
import numpy as np
import verovio
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 30
MEASURES_PER_ROW = 4
N_MEASURES = 60
N_ROWS = N_MEASURES // MEASURES_PER_ROW
SLOT_Y = (180, 620)          # top-left y of the two strips
TOP_BAND_H = 150
AUDIO = "reloj_backing_best.webm"
AUDIO_LEN = 183.066
TAIL = 10.0       # extra frozen seconds after the audio ends (breathing room)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
RED = (204, 17, 17)


def build_row_score():
    """Inject a system break every MEASURES_PER_ROW measures."""
    tree = ET.parse("mxl_extracted/score.xml")
    part = tree.getroot().find("part")
    for m in part.findall("measure"):
        for pr in m.findall("print"):     # drop MuseScore's own line breaks
            pr.attrib.pop("new-system", None)
            pr.attrib.pop("new-page", None)
        num = int(m.get("number"))
        if num > 1 and (num - 1) % MEASURES_PER_ROW == 0:
            pr = m.find("print")
            if pr is None:
                pr = ET.Element("print")
                m.insert(0, pr)
            pr.set("new-page", "yes")
    tree.write("score_rows.xml")


def layout_pages(tk):
    """Encoded page breaks put one 4-measure system on each page; the page
    height then shrink-wraps to content."""
    tk.setOptions({
        "breaks": "encoded",
        "pageWidth": 1650,
        "pageHeight": 900,
        "scale": 100,
        "adjustPageHeight": True,
        "header": "none",
        "footer": "none",
        "pageMarginTop": 30,
        "pageMarginBottom": 30,
        "pageMarginLeft": 30,
        "pageMarginRight": 30,
    })
    assert tk.loadFile("score_rows.xml")
    n = tk.getPageCount()
    if n != N_ROWS:
        raise RuntimeError(f"expected {N_ROWS} pages, got {n}")
    return 900


def note_xy(svg, nid):
    i = svg.find(f'<g id="{nid}"')
    if i < 0:
        return None
    m = re.search(r'translate\(([-0-9.]+),\s*([-0-9.]+)\)', svg[i:i + 2000])
    return (float(m.group(1)), float(m.group(2))) if m else None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    with open("sync_map.json") as f:
        sync = json.load(f)
    kq = [k[0] for k in sync["knots"]]
    kt = [k[1] for k in sync["knots"]]

    def q_to_t(q):
        return float(np.interp(q, kq, kt))

    m_t = [m["t"] for m in sync["measures"]]      # downbeat times, m1..m60
    m_t.append(q_to_t(240.0))                     # end of m60
    bar = 4 * sync["structure"]["quarter_dur"]

    build_row_score()
    tk = verovio.toolkit()
    ph = layout_pages(tk)
    print(f"pageHeight={ph}, {tk.getPageCount()} pages")

    timemap = tk.renderToTimemap()
    if isinstance(timemap, (str, bytes)):
        timemap = json.loads(timemap)
    onsets = sorted((e["qstamp"], e["on"][0]) for e in timemap if e.get("on"))

    svgs = [tk.renderToSVG(p) for p in range(1, N_ROWS + 1)]
    vb = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svgs[0])
    vbw = float(vb.group(1))
    scale = W / vbw

    strips = []
    for svg in svgs:
        png = cairosvg.svg2png(bytestring=svg.encode(), output_width=W,
                               background_color="white")
        strips.append(Image.open(io.BytesIO(png)).convert("RGB"))

    # cursor checkpoints per row: (time, x_px), from row edges + note onsets
    row_pts = [[] for _ in range(N_ROWS)]
    note_info = []                                # (t_on, row, x, y)
    for q, nid in onsets:
        page = tk.getPageWithElement(nid)
        xy = note_xy(svgs[page - 1], nid)
        if xy is None:
            continue
        t = q_to_t(q)
        x, y = xy[0] * scale, xy[1] * scale
        row_pts[page - 1].append((t, x))
        note_info.append((t, page - 1, x, y))
    print(f"notes located: {len(note_info)}/{len(onsets)}")
    print("strip heights:", sorted({s.height for s in strips}))
    x_left, x_right = 0.055 * W, 0.985 * W
    rows_t = []                                   # (t_start, t_end) per row
    for r in range(N_ROWS):
        t0 = m_t[r * MEASURES_PER_ROW]
        t1 = m_t[min((r + 1) * MEASURES_PER_ROW, N_MEASURES)]
        rows_t.append((t0, t1))
        pts = sorted(row_pts[r] + [(t0, x_left), (t1, x_right)])
        ts = np.maximum.accumulate([p[0] for p in pts])
        xs = np.maximum.accumulate([p[1] for p in pts])
        row_pts[r] = (ts, xs)

    f_big = ImageFont.truetype(FONT, 150)
    f_mid = ImageFont.truetype(FONT, 44)
    f_sml = ImageFont.truetype(FONT, 30)

    # countdown windows: (t_from, t_to, number), derived from the structure
    counts = []
    G = sync["structure"]["ghost_head_bars"]
    B = sync["structure"]["extra_break_bars"]
    t_m1, t_pickup = m_t[0], q_to_t(7.0)
    wait = G + 2                                   # ghost bars + his m1-m2
    for k in range(G):
        counts.append((t_m1 - (G - k) * bar, t_m1 - (G - 1 - k) * bar, wait - k))
    counts.append((t_m1, m_t[1], 2))
    counts.append((m_t[1], t_pickup, 1))
    nb = B + 1                                     # his m36 break, in audio bars
    t_b0, t_b1 = m_t[35], m_t[36]
    for k in range(nb):
        counts.append((t_b0 + k * (t_b1 - t_b0) / nb,
                       t_b0 + (k + 1) * (t_b1 - t_b0) / nb, nb - k))

    def frame_at(t):
        img = Image.new("RGB", (W, H), "white")
        dr = ImageDraw.Draw(img, "RGBA")
        r = 0
        for i, (t0, t1) in enumerate(rows_t):
            if t >= t0:
                r = i
        cur_slot = r % 2
        slot_rows = {cur_slot: r}
        if r + 1 < N_ROWS:
            slot_rows[1 - cur_slot] = r + 1
        for slot, row in slot_rows.items():
            img.paste(strips[row], (0, SLOT_Y[slot]))
        dr.line([(0, SLOT_Y[1] - 25), (W, SLOT_Y[1] - 25)], fill=(210, 210, 210), width=2)

        # header
        dr.text((40, 40), "Reloj", font=f_mid, fill=(40, 40, 40))
        dr.text((40, 100), "Roberto Cantoral  ·  88 bpm", font=f_sml, fill=(120, 120, 120))
        mnum = r * MEASURES_PER_ROW + 1
        dr.text((W - 260, 55), f"compás {mnum}–{mnum + 3}", font=f_sml, fill=(120, 120, 120))

        # sweeping cursor on current row
        ts, xs = row_pts[r]
        cx = float(np.interp(t, ts, xs))
        y0, y1 = SLOT_Y[cur_slot], SLOT_Y[cur_slot] + strips[r].height
        dr.rectangle([cx - 34, y0, cx + 10, y1], fill=(255, 40, 40, 46))
        dr.rectangle([cx - 3, y0, cx + 3, y1], fill=(220, 30, 30, 150))

        # countdown overlay
        for t0, t1, n in counts:
            if t0 <= t < t1:
                dr.text((W / 2 - 250, 18), "GUITARRA", font=f_mid, fill=RED + (255,))
                dr.text((W / 2 + 90, -10), str(n), font=f_big, fill=RED + (255,))
                frac = (t - t0) / (t1 - t0)
                dr.rectangle([W / 2 - 250, 80, W / 2 - 250 + 330 * (1 - frac), 96],
                             fill=(230, 30, 30, 130))
        return img

    if mode == "test":
        for t in (2.0, 21.5, 23.0, 61.0, 114.5, 170.0):
            frame_at(t).save(f"test_t{t:.0f}.png")
        print("test frames written")
        return

    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", AUDIO, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "reloj_playalong.mkv"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n_frames = int((AUDIO_LEN + TAIL) * FPS)
    for i in range(n_frames):
        proc.stdin.write(frame_at(i / FPS).tobytes())
        if i % (FPS * 30) == 0:
            print(f"  {i}/{n_frames} frames ({i/FPS:.0f}s)")
    proc.stdin.close()
    proc.wait()
    print("done:", proc.returncode)


if __name__ == "__main__":
    main()
