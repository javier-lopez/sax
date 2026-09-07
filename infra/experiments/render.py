"""Render play-along frames: score pages with the active note highlighted red.

Inputs:  mxl_extracted/score.xml, sync_map.json
Outputs: frames/f0000.png ... (1920x1080), frames.txt (ffmpeg concat with durations)

One frame per note onset; the highlight persists through rests so the cursor
never vanishes mid-phrase. Page flips happen when the active note lives on the
next page, mirroring how play-along videos read.
"""

import json
import os

import cairosvg
import numpy as np
import verovio

W, H = 1920, 1080
HIGHLIGHT = "#cc1111"
AUDIO_LEN = 183.066


def main():
    with open("sync_map.json") as f:
        sync = json.load(f)
    kq = [k[0] for k in sync["knots"]]
    kt = [k[1] for k in sync["knots"]]

    def q_to_t(q):
        return float(np.interp(q, kq, kt))

    tk = verovio.toolkit()
    tk.setOptions({
        "pageWidth": 2560,
        "pageHeight": 1440,
        "scale": 72,
        "adjustPageHeight": False,
        "font": "Leland",
        "header": "auto",
        "footer": "none",
        "spacingSystem": 18,
        "justifyVertically": True,
    })
    assert tk.loadFile("mxl_extracted/score.xml"), "verovio failed to load score"
    n_pages = tk.getPageCount()
    print(f"verovio: {n_pages} pages")

    timemap = tk.renderToTimemap()
    if isinstance(timemap, (str, bytes)):
        timemap = json.loads(timemap)
    # note onsets: (qstamp, first_note_id)
    onsets = []
    for entry in timemap:
        if entry.get("on"):
            onsets.append((entry["qstamp"], entry["on"][0]))
    onsets.sort()
    print(f"timemap: {len(onsets)} note onsets, q {onsets[0][0]} .. {onsets[-1][0]}")

    page_svg = {p: tk.renderToSVG(p) for p in range(1, n_pages + 1)}
    note_page = {nid: tk.getPageWithElement(nid) for _, nid in onsets}

    os.makedirs("frames", exist_ok=True)

    def rasterize(svg, path):
        cairosvg.svg2png(bytestring=svg.encode(), write_to=path,
                         output_width=W, output_height=H,
                         background_color="white")

    def highlighted(page, note_id):
        svg = page_svg[page]
        needle = f'<g id="{note_id}"'
        assert needle in svg, f"note id {note_id} not found on page {page}"
        return svg.replace(
            needle,
            f'<g fill="{HIGHLIGHT}" stroke="{HIGHLIGHT}" id="{note_id}"', 1)

    frames = []  # (png_path, duration_seconds)

    # intro: plain first page until the melody starts
    rasterize(page_svg[1], "frames/f0000.png")
    t_start = q_to_t(onsets[0][0])
    frames.append(("frames/f0000.png", t_start))

    for i, (q, nid) in enumerate(onsets):
        t = q_to_t(q)
        t_next = q_to_t(onsets[i + 1][0]) if i + 1 < len(onsets) else AUDIO_LEN
        dur = max(t_next - t, 0.02)
        path = f"frames/f{i+1:04d}.png"
        rasterize(highlighted(note_page[nid], nid), path)
        frames.append((path, dur))
        if (i + 1) % 40 == 0:
            print(f"  {i+1}/{len(onsets)} frames")

    with open("frames.txt", "w") as f:
        for path, dur in frames:
            f.write(f"file '{path}'\nduration {dur:.4f}\n")
        f.write(f"file '{frames[-1][0]}'\n")  # concat demuxer needs a final entry

    total = sum(d for _, d in frames)
    print(f"{len(frames)} frames, video span {total:.2f}s (audio {AUDIO_LEN}s)")


if __name__ == "__main__":
    main()
