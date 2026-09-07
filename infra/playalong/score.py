"""MusicXML: unpack, parse, and re-break into fixed-width rows.

The four alignment experiments in the original reloj/ each carried their own
copy of this parser. It is the one piece every stage needs, so it lives here
and nowhere else.
"""

import os
import xml.etree.ElementTree as ET
import zipfile

STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def extract(mxl_path, dest_xml):
    """Accept either a compressed .mxl or a bare .xml/.musicxml."""
    if not mxl_path.lower().endswith(".mxl"):
        with open(mxl_path, "rb") as src, open(dest_xml, "wb") as dst:
            dst.write(src.read())
        return dest_xml
    with zipfile.ZipFile(mxl_path) as z:
        root_path = "score.xml"
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rf = container.find(".//rootfile")
            if rf is not None and rf.get("full-path"):
                root_path = rf.get("full-path")
        except KeyError:
            pass
        with open(dest_xml, "wb") as dst:
            dst.write(z.read(root_path))
    return dest_xml


class Score:
    """Flattened single-part view: quarter-note timeline, notes, measures."""

    def __init__(self, path):
        self.path = path
        root = ET.parse(path).getroot()
        part = root.find("part")
        if part is None:
            raise RuntimeError(f"{path}: no <part> element")

        divisions = None
        q = 0.0
        self.notes = []           # (onset_q, dur_q, midi or None for a rest)
        self.measures = []        # (number, onset_q)
        self.beats_per_measure = 4.0
        for m in part.findall("measure"):
            d = m.findtext("attributes/divisions")
            if d:
                divisions = int(d)
            beats = m.findtext("attributes/time/beats")
            beat_type = m.findtext("attributes/time/beat-type")
            if beats and beat_type:
                # measured in quarter notes, which is the unit of the whole pipeline
                self.beats_per_measure = 4.0 * int(beats) / int(beat_type)
            self.measures.append((m.get("number"), q))
            for el in m:
                if el.tag == "note":
                    if el.find("grace") is not None or el.find("chord") is not None:
                        continue
                    dur = int(el.findtext("duration")) / divisions
                    if el.find("rest") is not None:
                        self.notes.append((q, dur, None))
                    else:
                        midi = ((int(el.findtext("pitch/octave")) + 1) * 12
                                + STEP_TO_PC[el.findtext("pitch/step")]
                                + int(el.findtext("pitch/alter") or 0))
                        self.notes.append((q, dur, midi))
                    q += dur
                elif el.tag == "backup":
                    q -= int(el.findtext("duration")) / divisions
                elif el.tag == "forward":
                    q += int(el.findtext("duration")) / divisions
        self.q_total = q
        self.n_measures = len(self.measures)
        self.declared_bpm = self._declared_bpm(part)

    @staticmethod
    def _declared_bpm(part):
        """Quarter-note bpm the engraver wrote down, if any. Worth trusting as
        a search centre: a transcription is usually made against the recording,
        so its metronome mark lands within a few percent of the truth."""
        for sound in part.iter("sound"):
            if sound.get("tempo"):
                return float(sound.get("tempo"))
        for per in part.iter("per-minute"):
            try:
                return float(per.text)
            except (TypeError, ValueError):
                pass
        return None

    @property
    def pitched(self):
        return [n for n in self.notes if n[2] is not None]

    def measure_q(self, number_index):
        """Quarter offset of measure #index (1-based), clamped to the end."""
        if number_index >= self.n_measures:
            return self.q_total
        return self.measures[number_index][1]

    def harmony_template(self):
        """One 12-bin chroma column per quarter: melody pitch class plus its
        fifth, held for the note's duration. Rest quarters stay all-zero and
        are excluded from scoring — they carry no harmonic evidence."""
        import numpy as np

        nq = int(round(self.q_total))
        T = np.zeros((12, nq))
        for onset, dur, midi in self.notes:
            if midi is None:
                continue
            a, b = int(onset), min(int(np.ceil(onset + dur)), nq)
            T[midi % 12, a:b] += 1.0
            T[(midi + 7) % 12, a:b] += 0.33
        return T

    def melody_gaps(self, min_q):
        """Stretches of at least min_q quarters with no melody, as (start_q,
        end_q). These are where the player waits and wants a bar countdown."""
        gaps = []
        cursor = 0.0
        for onset, dur, midi in self.notes:
            if midi is None:
                continue
            if onset - cursor >= min_q:
                gaps.append((cursor, onset))
            cursor = max(cursor, onset + dur)
        return gaps


def build_row_score(src_xml, dest_xml, measures_per_row):
    """Force one system per row by encoding a page break every N measures.

    Verovio honours encoded breaks with one page per system; the renderer then
    rasterises each page as an independent strip. MuseScore's own line breaks
    are dropped first or they fight this layout.
    """
    tree = ET.parse(src_xml)
    part = tree.getroot().find("part")
    for m in part.findall("measure"):
        for d in [d for d in m.findall("direction")
                  if d.find("direction-type/metronome") is not None]:
            # verovio emits the beat-unit as a music-font text glyph, which the
            # rasteriser has no font for; the header already carries the tempo
            m.remove(d)
        for pr in m.findall("print"):
            pr.attrib.pop("new-system", None)
            pr.attrib.pop("new-page", None)
        num = int(m.get("number"))
        if num > 1 and (num - 1) % measures_per_row == 0:
            pr = m.find("print")
            if pr is None:
                pr = ET.Element("print")
                m.insert(0, pr)
            pr.set("new-page", "yes")
    os.makedirs(os.path.dirname(dest_xml), exist_ok=True)
    tree.write(dest_xml)
