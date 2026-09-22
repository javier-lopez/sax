"""MusicXML: unpack, parse, and re-break into rows.

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
        self.multirests = {}      # measure index -> bars the rest spans
        # measures whose downbeat attacks a note of a half or longer -- the
        # held note is where the eye has time to jump to the next row
        self.long_downbeats = set()
        for i, m in enumerate(part.findall("measure")):
            span = m.findtext("attributes/measure-style/multiple-rest")
            if span:
                self.multirests[i] = int(span)
            first = True
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
                    if first:
                        first = False
                        tied_in = any(t.get("type") == "stop"
                                      for t in el.findall("tie"))
                        if (el.find("rest") is None and not tied_in
                                and dur >= 2.0):
                            self.long_downbeats.add(i)
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

    def rows(self, measures_per_row):
        """Row plan as (first, end) measure indices, end exclusive.

        A multi-measure rest is one row of its own, however long: verovio lays
        it out as a single element and drops any break encoded inside it, and
        one "wait 18" block reads better than the same wait chopped into rows.

        Between rests, rows are measures_per_row wide but may give or take one
        measure to keep a held note off the start of a row. The eye has to jump
        to the next row somewhere, and the only moment it can afford to is
        while a long note sounds -- so a row should end on that note, not open
        with it and leave the jump for the busy measure after.
        """
        rows, i = [], 0
        rests = sorted(self.multirests)
        while i < self.n_measures:
            if i in self.multirests:
                end = min(i + self.multirests[i], self.n_measures)
                rows.append((i, end))
            else:
                end = min([k for k in rests if k > i] + [self.n_measures])
                rows += self._plan_segment(i, end, measures_per_row)
            i = end
        return rows

    def _plan_segment(self, start, end, width):
        """Cheapest split of [start, end) into rows, by dynamic programming.

        Costs: 1 for each row off the nominal width, 3 for a row that opens on
        a held note, and a 0.5 credit for one that closes on one. A row wider
        or narrower than the rest is worth it for one clean page turn, never
        for nothing."""
        OFF_WIDTH, BAD_START, GOOD_END = 1.0, 3.0, 0.5
        best = {end: (0.0, [])}
        for i in range(end - 1, start - 1, -1):
            options = []
            for n in (width, width - 1, width + 1, *range(1, width - 1)):
                j = i + n
                if n < 1 or j > end or j not in best:
                    continue
                if n < width - 1 and j != end:
                    continue    # a stub row only as the segment's remainder
                cost = best[j][0] + (0.0 if n == width else OFF_WIDTH)
                if i != start and i in self.long_downbeats:
                    cost += BAD_START
                if j != end and (j - 1) in self.long_downbeats:
                    cost -= GOOD_END
                options.append((cost, [(i, j)] + best[j][1]))
            if options:
                best[i] = min(options, key=lambda o: o[0])
        return best[start][1]

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
    """Force one system per row by encoding a page break at every row start.

    Verovio honours encoded breaks with one page per system; the renderer then
    rasterises each page as an independent strip. MuseScore's own line breaks
    are dropped first or they fight this layout.
    """
    tree = ET.parse(src_xml)
    part = tree.getroot().find("part")
    starts = {r[0] for r in Score(src_xml).rows(measures_per_row)}
    for i, m in enumerate(part.findall("measure")):
        for d in [d for d in m.findall("direction")
                  if d.find("direction-type/metronome") is not None]:
            # verovio emits the beat-unit as a music-font text glyph, which the
            # rasteriser has no font for; the header already carries the tempo
            m.remove(d)
        for pr in m.findall("print"):
            pr.attrib.pop("new-system", None)
            pr.attrib.pop("new-page", None)
        if i > 0 and i in starts:
            pr = m.find("print")
            if pr is None:
                pr = ET.Element("print")
                m.insert(0, pr)
            pr.set("new-page", "yes")
    os.makedirs(os.path.dirname(dest_xml), exist_ok=True)
    tree.write(dest_xml)
