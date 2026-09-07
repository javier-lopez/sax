"""Karaoke-style play-along video: two score rows and a sweeping cursor.

The reading model is the one every karaoke screen uses and no page-turning
score viewer does: the eye never has to jump. Two 4-measure strips are on
screen at once; when the cursor leaves a row, that slot is refilled with the
row *after* the next one, so the row you are about to play was already there
and static while you played the previous one.

The cursor sweeps continuously rather than hopping note to note, because a
hop tells you where you are and a sweep tells you where you are going.
"""

import io
import json
import math
import os
import re
import shutil
import subprocess

import cairosvg
import numpy as np
import verovio
from PIL import Image, ImageDraw, ImageFont

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
RED = (204, 17, 17)
HEADER_H = 150
SLOT_GAP = 40
# how long the cursor may spend running into a row before its first note
ROW_RUN_IN = 0.6
BOTTOM_MARGIN = 40


_NUM = re.compile(r"-?\d*\.?\d+(?:[eE]-?\d+)?")
_CMD = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")


def _path_right_edge(d):
    """Rightmost x an SVG path reaches, walking relative commands. Noteheads
    are a move plus a few cubics, so tracking the pen is enough."""
    x = right = 0.0
    for cmd, args in _CMD.findall(d):
        n = [float(v) for v in _NUM.findall(args)]
        rel = cmd.islower()
        up = cmd.upper()
        if up in ("M", "L", "T"):
            step = 2
        elif up == "C":
            step = 6
        elif up in ("S", "Q"):
            step = 4
        elif up == "H":
            step = 1
        elif up == "V":
            continue
        elif up == "A":
            step = 7
        else:
            continue
        for i in range(0, len(n) - step + 1, step):
            seg = n[i:i + step]
            # every x in the segment bounds the glyph; the last one moves the pen
            xs = [seg[0]] if up in ("H",) else seg[0::2] if up != "A" else [seg[5]]
            for cx in xs:
                right = max(right, x + cx if rel else cx)
            last = seg[0] if up == "H" else (seg[5] if up == "A" else seg[-2])
            x = x + last if rel else last
    return right


# the trailing group catches verovio's self-closing <g ... />, which opens
# nothing and must never be pushed onto the ancestor stack
_GTAG = re.compile(r"<(/?)g\b([^>]*?)(/?)>")


def _ancestor_dx(svg, upto):
    """Accumulated x-translate of the <g> elements still open at `upto`.

    Verovio wraps the whole page in a page-margin group carrying a translate.
    A <use> transform is therefore NOT the glyph's page position, and reading it
    alone puts every note in the score off by that margin -- uniformly, which is
    exactly why it hides: the cursor stays wrong by the same amount everywhere
    and only shows up where a note sits close to the staff's left edge.
    """
    dx = 0.0
    stack = []
    for m in _GTAG.finditer(svg, 0, upto):
        if m.group(3):
            continue
        if m.group(1):
            if stack:
                dx -= stack.pop()
        else:
            t = re.search(r'transform="[^"]*?translate\(([-0-9.]+)', m.group(2))
            v = float(t.group(1)) if t else 0.0
            stack.append(v)
            dx += v
    return dx


def _final_barline_x(svg):
    """Page x of the last barline: where the music stops, as opposed to where
    the strip stops."""
    hits = list(re.finditer(r'class="barLine"[^>]*>\s*<path d="M([0-9.]+)', svg))
    if not hits:
        return None
    m = hits[-1]
    return float(m.group(1)) + _ancestor_dx(svg, m.start())


def _drop_courtesy_clefs(svg):
    """Remove every clef after the first one on the page.

    Verovio restates the clef at the end of a multi-measure rest, which is an
    engraving convention but reads as a duplicate on a two-row play-along --
    and with a block multi-rest the bar spans straight through it. These lead
    sheets never change clef mid-piece, so any clef past the first is that
    courtesy restatement. Whole <g> pairs are removed so ancestor bookkeeping
    stays balanced.
    """
    out = []
    pos = 0
    seen = 0
    for m in re.finditer(r'<g id="[^"]+" class="clef">', svg):
        if m.start() < pos:
            continue
        seen += 1
        if seen == 1:
            continue
        depth = 0
        i = m.start()
        for t in _GTAG.finditer(svg, m.start()):
            if t.group(3):
                continue
            depth += -1 if t.group(1) else 1
            if depth == 0:
                i = t.end()
                break
        out.append(svg[pos:m.start()])
        pos = i
    out.append(svg[pos:])
    return "".join(out)


def _glyph_widths(svg):
    """Width of each music glyph defined in this SVG, in glyph units."""
    out = {}
    for gid, d in re.findall(r'<g id="(E[0-9A-F]{3}-[^"]+)">\s*<path[^>]*d="([^"]+)"', svg):
        out[gid] = _path_right_edge(d)
    return out


def _note_xy(svg, nid, widths):
    """Horizontal centre of a note's head, in SVG units.

    Verovio anchors a notehead at its LEFT edge. Sweeping the cursor to that
    anchor makes it touch the note half a notehead early -- ~0.1s at a ballad
    tempo, which reads as "the bar says play before the note actually sounds".
    Centring on the glyph fixes it at any tempo or scale.
    """
    i = svg.find(f'<g id="{nid}"')
    if i < 0:
        return None
    dx = _ancestor_dx(svg, i)
    window = svg[i:i + 2000]
    m = re.search(r'translate\(([-0-9.]+),\s*([-0-9.]+)\)', window)
    if not m:
        m = re.search(r'\bx="([-0-9.]+)"\s+y="([-0-9.]+)"', window)
        return (float(m.group(1)) + dx, float(m.group(2))) if m else None
    x, y = float(m.group(1)) + dx, float(m.group(2))
    href = re.search(r'xlink:href="#(E[0-9A-F]{3}-[^"]+)"', window)
    sc = re.search(r'scale\(([-0-9.]+)', window[m.end():m.end() + 60])
    if href and href.group(1) in widths:
        x += widths[href.group(1)] * (float(sc.group(1)) if sc else 1.0) / 2.0
    return x, y


def _render_strips(rows_xml, n_rows, width):
    tk = verovio.toolkit()
    tk.setOptions({
        "breaks": "encoded",
        "pageWidth": 1650,
        "pageHeight": 900,
        "scale": 100,
        "adjustPageHeight": True,   # each page shrink-wraps its single system
        "header": "none",
        "footer": "none",
        "pageMarginTop": 30,
        "pageMarginBottom": 30,
        "pageMarginLeft": 30,
        "pageMarginRight": 30,
        # a solid bar spanning the measure, which is what a player reads as
        # "wait this many bars"; the default glyph form draws a stub
        "multiRestStyle": "block",
    })
    if not tk.loadFile(rows_xml):
        raise RuntimeError(f"verovio failed to load {rows_xml}")
    got = tk.getPageCount()
    if got != n_rows:
        raise RuntimeError(f"expected {n_rows} row-pages from verovio, got {got}")

    svgs = [_drop_courtesy_clefs(tk.renderToSVG(p))
            for p in range(1, n_rows + 1)]
    vbw = float(re.search(r'viewBox="0 0 ([0-9.]+) ', svgs[0]).group(1))
    strips = []
    for svg in svgs:
        png = cairosvg.svg2png(bytestring=svg.encode(), output_width=width,
                               background_color="white")
        strips.append(Image.open(io.BytesIO(png)).convert("RGB"))

    timemap = tk.renderToTimemap()
    if isinstance(timemap, (str, bytes)):
        timemap = json.loads(timemap)
    onsets = sorted((e["qstamp"], e["on"][0]) for e in timemap if e.get("on"))
    pages = {nid: tk.getPageWithElement(nid) for _, nid in onsets}
    return strips, svgs, onsets, pages, width / vbw


class Renderer:
    def __init__(self, song, score, sync, log=print):
        self.song, self.score, self.log = song, score, log
        lay = song.layout
        self.W, self.H, self.fps = lay["width"], lay["height"], lay["fps"]
        self.mpr = lay["measures_per_row"]
        self.cursor_lag = lay["cursor_lag_seconds"]
        self.n_rows = math.ceil(score.n_measures / self.mpr)

        knots = sync["knots"]
        self.kq = [k[0] for k in knots]
        self.kt = [k[1] for k in knots]
        self.structure = sync["structure"]
        self.bar = score.beats_per_measure * sync["structure"]["quarter_dur"]
        self.bpm = 60.0 / sync["structure"]["quarter_dur"]

        strips, svgs, onsets, pages, svg_scale = _render_strips(
            song.rows_xml, self.n_rows, self.W)

        # fit both slots vertically; shrink uniformly if the systems are tall
        max_h = max(s.height for s in strips)
        avail = self.H - HEADER_H - BOTTOM_MARGIN
        fit = min(1.0, (avail - SLOT_GAP) / (2 * max_h))
        if fit < 1.0:
            strips = [s.resize((int(s.width * fit), int(s.height * fit)),
                               Image.LANCZOS) for s in strips]
            max_h = max(s.height for s in strips)
        self.strips = strips
        self.strip_w = strips[0].width
        self.x0 = (self.W - self.strip_w) // 2
        block = 2 * max_h + SLOT_GAP
        top = HEADER_H + max(0, (avail - block) // 2)
        self.slot_y = (top, top + max_h + SLOT_GAP)
        self.log(f"{self.n_rows} rows, strip {self.strip_w}x{max_h} "
                 f"(fit {fit:.2f}), slots at y={self.slot_y}")

        self._build_cursor_track(svgs, onsets, pages, svg_scale * fit)
        self._build_countdowns()

        f = ImageFont.truetype
        self.f_big, self.f_mid, self.f_sml = f(FONT_BOLD, 150), f(FONT_BOLD, 44), f(FONT_BOLD, 30)

    def q_to_t(self, q):
        return float(np.interp(q, self.kq, self.kt))

    def _build_cursor_track(self, svgs, onsets, pages, scale):
        """Per row, a monotone (time -> x) track through the note heads, pinned
        to the row's own start and end so the sweep spans the full system."""
        row_pts = [[] for _ in range(self.n_rows)]
        widths = [_glyph_widths(s) for s in svgs]
        located = 0
        for q, nid in onsets:
            page = pages[nid]
            xy = _note_xy(svgs[page - 1], nid, widths[page - 1])
            if xy is None:
                continue
            located += 1
            row_pts[page - 1].append((self.q_to_t(q), xy[0] * scale + self.x0))
        self.log(f"notes located: {located}/{len(onsets)}")
        if located < 0.8 * len(onsets):
            raise RuntimeError("verovio SVG note positions not readable; "
                               "the cursor would be wrong")

        x_left = 0.055 * self.strip_w + self.x0
        x_right = 0.985 * self.strip_w + self.x0
        # The row holding the final note is where the video ends, visually. Its
        # track gets no right-edge pin, so the bar crosses the last note over its
        # written length and stops on the closing barline. Sweeping to the strip
        # edge would carry the eye off the staff; freezing on the note's onset
        # would hide the note itself.
        self.last_row = max(r for r in range(self.n_rows) if row_pts[r])
        last_note = self.score.pitched[-1]
        end_t = self.q_to_t(last_note[0] + last_note[1])
        bl = _final_barline_x(svgs[self.last_row])
        end_x = (bl * scale + self.x0) if bl else max(x for _, x in row_pts[self.last_row])

        def edge(pts, t, fallback):
            """Where the row's own timeline reaches at t.

            With proportional spacing x is linear in time, so a line through the
            row's notes extrapolates its start and end honestly. Fixed fractions
            of the strip cannot: they ignore the clef, and they made the cursor
            enter every row behind its first note and overshoot its last one.
            """
            if len(pts) < 2:
                return fallback
            a, b = np.polyfit([p[0] for p in pts], [p[1] for p in pts], 1)
            return float(np.clip(a * t + b, 0.02 * self.W, 0.99 * self.W))

        self.rows_t, self.track = [], []
        for r in range(self.n_rows):
            t0 = self.q_to_t(self.score.measure_q(r * self.mpr))
            t1 = self.q_to_t(self.score.measure_q(min((r + 1) * self.mpr,
                                                      self.score.n_measures)))
            pts_r = sorted(row_pts[r])
            # A row whose first note is on the downbeat gives the cursor nowhere
            # to come from: it materialises already sitting on the note. So the
            # row opens early, at the blank left of the staff, and the bar runs
            # into the first note. The entry time comes from extending the row's
            # own line backwards, which keeps the speed continuous -- an entry at
            # any other rate would read as the cursor lurching.
            t_in = t0
            if len(pts_r) >= 2:
                a, b = np.polyfit([p[0] for p in pts_r], [p[1] for p in pts_r], 1)
                if a > 0:
                    t_in = float(np.clip((x_left - b) / a, t0 - ROW_RUN_IN, t0))
            self.rows_t.append((t_in, t1))
            edges = [(t_in, edge(pts_r, t_in, x_left))]
            edges.append((end_t, end_x) if r == self.last_row
                         else (t1, edge(pts_r, t1, x_right)))
            pts = sorted(pts_r + edges)
            ts = np.maximum.accumulate([p[0] for p in pts])
            xs = np.maximum.accumulate([p[1] for p in pts])
            self.track.append((ts, xs))

    def _build_countdowns(self):
        """A bar countdown over every wait long enough to lose the pulse.

        Counted backwards from the entry in whole recording bars, so the "1"
        always lands on the bar immediately before the player comes in --
        including the bars the chart never wrote down: the lead-in before
        measure 1, and any measure the recording stretches.
        """
        min_bars = self.song.layout["countdown_min_bars"]
        head_lead = self.structure["head_bars"] * self.bar
        # Gate on the wait the player actually sits through, in recording bars.
        # Score quarters cannot decide this: the bars a chart omits at the head,
        # or compresses into one written measure, are invisible on paper and are
        # precisely the ones long enough to lose the pulse.
        self.counts = []
        waits = 0
        for q_start, q_end in self.score.melody_gaps(1.0):
            t_end = self.q_to_t(q_end)
            t_start = self.q_to_t(q_start)
            if q_start <= 0.0:
                t_start -= head_lead
            n = int(round((t_end - t_start) / self.bar))
            if n < min_bars:
                continue
            waits += 1
            for k in range(n):
                self.counts.append((t_end - (n - k) * self.bar,
                                    t_end - (n - 1 - k) * self.bar, n - k))
        self.log(f"countdown windows: {len(self.counts)} "
                 f"over {waits} waits")

    def frame_at(self, t):
        img = Image.new("RGB", (self.W, self.H), "white")
        dr = ImageDraw.Draw(img, "RGBA")

        # The cursor runs ahead of the sound so the player reads the cue before
        # attacking it. The row has to turn on that same clock: switching on t
        # would drop the cursor into the new row already a full lead inside it,
        # skipping its first notes.
        tc = t - self.cursor_lag
        r = 0
        for i, (t0, _) in enumerate(self.rows_t):
            if tc >= t0:
                r = i
        r = min(r, self.last_row)   # never advance past the note being held
        cur_slot = r % 2
        slots = {cur_slot: r}
        if r + 1 < self.n_rows:
            slots[1 - cur_slot] = r + 1
        for slot, row in slots.items():
            img.paste(self.strips[row], (self.x0, self.slot_y[slot]))
        dr.line([(0, self.slot_y[1] - SLOT_GAP // 2),
                 (self.W, self.slot_y[1] - SLOT_GAP // 2)],
                fill=(210, 210, 210), width=2)

        dr.text((40, 40), self.song.title, font=self.f_mid, fill=(40, 40, 40))
        credit = self.song.credit
        sub = f"{credit}  ·  {self.bpm:.0f} bpm" if credit else f"{self.bpm:.0f} bpm"
        dr.text((40, 100), sub, font=self.f_sml, fill=(120, 120, 120))
        m0 = r * self.mpr + 1
        m1 = min((r + 1) * self.mpr, self.score.n_measures)
        label = f"compás {m0}–{m1}" if m1 > m0 else f"compás {m0}"
        dr.text((self.W - 40 - dr.textlength(label, font=self.f_sml), 55),
                label, font=self.f_sml, fill=(120, 120, 120))

        ts, xs = self.track[r]
        cx = float(np.interp(tc, ts, xs))
        y0 = self.slot_y[cur_slot]
        y1 = y0 + self.strips[r].height
        dr.rectangle([cx - 34, y0, cx + 10, y1], fill=(255, 40, 40, 46))
        dr.rectangle([cx - 3, y0, cx + 3, y1], fill=(220, 30, 30, 150))

        for t0, t1, n in self.counts:
            if t0 <= t < t1:
                lab = self.song.layout["countdown_label"]
                dr.text((self.W / 2 - 250, 18), lab, font=self.f_mid, fill=RED + (255,))
                dr.text((self.W / 2 + 90, -10), str(n), font=self.f_big, fill=RED + (255,))
                frac = (t - t0) / (t1 - t0)
                dr.rectangle([self.W / 2 - 250, 80,
                              self.W / 2 - 250 + 330 * (1 - frac), 96],
                             fill=(230, 30, 30, 130))
        return img


def preview(song, score, sync, times=None, log=print):
    """Stills at interesting moments: every countdown, the entry, each row edge
    sampled. Cheaper than a full render when checking alignment by eye."""
    r = Renderer(song, score, sync, log=log)
    if not times:
        entry = r.q_to_t(score.pitched[0][0])
        times = [entry - 2.0, entry + 0.5]
        times += [t0 + 0.4 for t0, _, n in r.counts if n in (1, 3)]
        times += [r.rows_t[i][0] + 1.0
                  for i in (r.n_rows // 3, 2 * r.n_rows // 3, r.n_rows - 1)]
    # a re-preview replaces the old stills; stale ones from a previous
    # alignment are indistinguishable from current ones by eye
    shutil.rmtree(song.preview_dir, ignore_errors=True)
    os.makedirs(song.preview_dir, exist_ok=True)
    out = []
    for t in sorted(set(round(max(t, 0.0), 2) for t in times)):
        path = os.path.join(song.preview_dir, f"t{t:07.2f}.png")
        r.frame_at(t).save(path)
        out.append(path)
    log(f"{len(out)} preview stills in {song.preview_dir}")
    return out


def render_video(song, score, sync, audio_path, audio_len, log=print,
                 span=None):
    """span=(from, to) renders just those seconds -- the fast loop for settling
    align.offset by ear, thirty seconds instead of four minutes.

    A segment writes to the SAME path as the full render. One filename per song,
    always overwritten, is what a person can keep open in a player and re-watch;
    a new name per run makes them hunt for the file every time. A clip re-encodes
    its audio so the seek is sample-accurate; the full render stream-copies.
    """
    r = Renderer(song, score, sync, log=log)
    tail = song.layout["tail_seconds"]
    if span:
        t_from, t_to = span
        out = song.output
        audio_args = ["-ss", str(t_from), "-t", str(t_to - t_from), "-i", audio_path]
        codec = ["-c:a", "libopus", "-b:a", "160k"]
    else:
        t_from, t_to = 0.0, audio_len + tail
        out = song.output
        audio_args = ["-i", audio_path]
        codec = ["-c:a", "copy"]
    if os.path.exists(out):
        os.remove(out)             # iterate in place, never accumulate
    n_frames = int((t_to - t_from) * r.fps)
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{r.W}x{r.H}", "-r", str(r.fps), "-i", "-",
           *audio_args, "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", *codec, out]
    log(f"encoding {n_frames} frames ({t_from:.0f}-{t_to:.0f}s) -> {out}")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n_frames):
        proc.stdin.write(r.frame_at(t_from + i / r.fps).tobytes())
        if i % (r.fps * 30) == 0:
            log(f"  {i}/{n_frames} frames ({i / r.fps:.0f}s)")
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    out_codec = _audio_codec(out)
    if not span:
        src_codec = _audio_codec(audio_path)
        if src_codec != out_codec:
            raise RuntimeError(f"audio was re-encoded: {src_codec} -> {out_codec}")
        log(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB), "
            f"audio {out_codec} copied through untouched")
    else:
        log(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
    return out


def _audio_codec(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,sample_rate,channels", "-of", "csv=p=0", path],
        check=True, capture_output=True, text=True).stdout.strip()
    return out
