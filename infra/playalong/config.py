"""Per-song configuration: the only thing a song directory has to author.

A song dir holds song.json plus its inputs (the .mxl); everything the pipeline
derives lands in <song>/build/ and the finished video at the top level.
"""

import json
import os

DEFAULT_LAYOUT = {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "measures_per_row": 4,
    "countdown_label": "ESPERA",
    "countdown_min_bars": 2,
    # Negative runs the cursor ahead of the sound. A cue landing exactly on the
    # beat is already too late: by then the player should have attacked. This is
    # the player's own constant, settled by ear, not a property of the song.
    "cursor_lag_seconds": -0.48,
    "tail_seconds": 10.0,
}

DEFAULT_ALIGN = {
    # [start, stop, step] sweeps, in seconds
    "quarter_dur": [0.45, 1.10, 0.002],
    "t0": [-1.0, 30.0, 0.02],
    # extra accompaniment bars the recording has before the score's measure 1
    "head_bars": [0],
    # semitone rotations of the score's pitch classes to try against the audio
    "transpose": [0],
    # written measures the recording stretches over more bars than the paper shows
    "stretch": [],
    # seconds added to every knot; the ear settles what correlation cannot
    "offset": 0.0,
    # [[measure, seconds], ...] -- a per-measure offset curve for a performance
    # that does not hold a steady tempo; overrides the flat "offset" when present
    "anchors": [],
    # optional [lo, hi] seconds the melody entry must fall inside (the player's ear)
    "entry_window": None,
    # bypasses the search entirely; see align.py
    "lock": None,
}


class Song:
    def __init__(self, root, data):
        self.root = root
        self.build = os.path.join(root, "build")
        self.title = data.get("title") or os.path.basename(root)
        self.credit = data.get("credit", "")
        self.source = data.get("source")
        # a local recording, for a song whose source is not a URL: the file is
        # then an input rather than a download, and build/ stays disposable
        self.audio_in = (os.path.join(root, data["audio"])
                         if data.get("audio") else None)
        self.score_file = os.path.join(root, data["score"])
        self.output = os.path.join(root, data.get("output", "playalong.mkv"))
        self.layout = {**DEFAULT_LAYOUT, **data.get("layout", {})}
        self.align = {**DEFAULT_ALIGN, **data.get("align", {})}

    # derived paths, all under build/ so the song dir stays inputs + outputs
    @property
    def score_xml(self):
        return os.path.join(self.build, "score.xml")

    @property
    def rows_xml(self):
        return os.path.join(self.build, "score_rows.xml")

    @property
    def wav(self):
        # deliberately not audio.*: the source recording owns that name
        return os.path.join(self.build, "analysis.wav")

    @property
    def sync_map(self):
        return os.path.join(self.build, "sync_map.json")

    @property
    def preview_dir(self):
        return os.path.join(self.build, "preview")


def load(song_dir):
    path = os.path.join(song_dir, "song.json")
    with open(path) as f:
        data = json.load(f)
    song = Song(os.path.abspath(song_dir), data)
    os.makedirs(song.build, exist_ok=True)
    return song


SCAFFOLD_NOTE = ("tempo swept around the score's own metronome mark"
                 " -- widen if align reports a winner at the sweep's edge")


def scaffold(song_dir, score_name, url, title, credit, declared_bpm):
    """Write a song.json a human never had to type.

    The tempo sweep is centred on the score's metronome mark when it has one,
    which turns a blind 0.45-1.10s search into a narrow, fast and far more
    trustworthy one. align.offset starts at 0 and is the one value a person
    still has to supply.
    """
    if declared_bpm:
        q = 60.0 / declared_bpm
        sweep = [round(q * 0.88, 4), round(q * 1.12, 4), 0.002]
    else:
        sweep = list(DEFAULT_ALIGN["quarter_dur"])
    data = {
        "title": title,
        "credit": credit,
        "source": url,
        "score": score_name,
        "output": f"{os.path.basename(song_dir)}_playalong.mkv",
        "layout": {"measures_per_row": 4, "countdown_label": "ESPERA",
                   "countdown_min_bars": 2, "tail_seconds": 8.0},
        "align": {"_tempo": SCAFFOLD_NOTE,
                  "quarter_dur": sweep,
                  "t0": [-1.0, 30.0, 0.02],
                  "head_bars": [0],
                  "transpose": list(range(12)),
                  "stretch": [],
                  "offset": 0.0,
    # [[measure, seconds], ...] -- a per-measure offset curve for a performance
    # that does not hold a steady tempo; overrides the flat "offset" when present
    "anchors": [],
                  "entry_window": None,
                  "lock": None},
    }
    path = os.path.join(song_dir, "song.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path
