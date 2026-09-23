# infra — play-along video pipeline

Turns *a recording on YouTube* + *a lead sheet in MusicXML* into a karaoke-style
play-along video: two staff rows on screen, a cursor sweeping the one you are
playing, and a bar countdown over every wait.

Extracted from `reloj/`, which had grown the engine and the song into the same
directory. A song directory now holds only inputs and outputs.

```
./infra/run.sh new <dir> <youtube-url> <score.mxl> "Title" "Credit"
./infra/run.sh <song-dir> [stage ...]

./infra/run.sh reloj                      # fetch + align + render
./infra/run.sh reloj align preview        # re-fit and eyeball it
```

Nothing is installed on the host: `run.sh` builds `sax-playalong-infra` on first
use and runs everything inside it as the invoking user.

## Stages

| stage | reads | writes |
|---|---|---|
| `scaffold` | the score's metronome mark | `song.json` |
| `fetch` | `source` URL or the `audio` input | `build/audio.*`, `build/analysis.wav` |
| `align` | score + wav | `build/sync_map.json` |
| `preview` | sync map | `build/preview/*.png` |
| `clip F T` | sync map + audio | the `output` video, seconds F..T only |
| `render` | sync map + audio | the `output` video, and `build/levelled.opus` when the song sets `loudness` |

`run.sh new` is `scaffold` + `fetch` + `align` + a clip to listen to; it also
copies the score into the song directory. A bare `run.sh <song>` means `fetch`,
`align`, `render`.

Every invocation re-engraves the score first, whatever the stage: `build/score.xml`
is unpacked from the `.mxl` and `build/score_rows.xml` is the same score with a
page break at every row start. They are cheap and always current, so no stage has
to declare them.

Each stage reuses what the previous one left behind, so re-rendering never
re-downloads and re-aligning never re-fetches. Rebuilds **replace**: the output
video and the preview stills are cleared before they are written, and a real
download clears the audio slot first, so two formats can never sit side by side
for the muxer to pick between.

## A song directory

```
mysong/
  song.json          <- the only thing you author
  mysong.mxl         <- input
  mysong_playalong.mkv  <- output
  build/             <- everything derived; safe to delete
```

## song.json

```json
{
  "title": "El Círculo de la Vida",
  "credit": "Elton John",
  "instrument": "YDS-120: S11 (soprano, si bemol)",
  "source": "https://www.youtube.com/watch?v=...",
  "score_source": "https://musescore.com/user/.../scores/...",
  "score": "el-rey-leon.mxl",
  "output": "el-rey-leon_playalong.mkv",
  "layout":  { "measures_per_row": 4, "countdown_label": "ESPERA",
               "countdown_min_bars": 2, "tail_seconds": 10.0,
               "cursor_lag_seconds": -0.28 },
  "align":   { "quarter_dur": [0.7147], "t0": [-4.0, 30.0, 0.02],
               "transpose": [10], "offset": 1.18 }
}
```

Every key has a default in `playalong/config.py`; a song states only what it
changes. Keys prefixed with `_` are free-text notes and are never read — the
scaffold leaves an `_tempo` note explaining the sweep it chose.

### Top level

| key | what it does |
|---|---|
| `title`, `credit` | the header's first two lines |
| `instrument` | the horn the chart is written for. Shown on the opening card, under the credit in the header, and on the closing card. A reader who grabs the wrong horn is off by a transposition before the first note |
| `source` | the YouTube URL `fetch` downloads |
| `audio` | a recording already in the song directory, used *instead* of `source`. Then it is an input, not a download, and belongs beside the score rather than in `build/`. No song here uses it |
| `score_source` | where the `.mxl` was exported from. **Read by no code** — it is there so the next person can find the master |
| `score`, `output` | the `.mxl` in, the video out. **Omit `score`** and the song is passed through instead of engraved: its `source` is already a finished play-along, so `fetch` + `render` only wrap it in the card and the `lead_seconds` / `tail_seconds` silence. `align`, `preview` and `clip` refuse to run |
| `loudness` | opt-in level shaping; see **Loudness** |

### `layout`

| key | default | what it does |
|---|---|---|
| `width`, `height`, `fps` | 1920, 1080, 30 | the frame |
| `measures_per_row` | 4 | *nominal* row width; see **Rows** |
| `countdown_label` | `"ESPERA"` | the word beside the count |
| `countdown_min_bars` | 2 | shortest wait that earns a countdown, in recording bars. Also gates which waits `loudness` lifts |
| `cursor_lag_seconds` | -0.48 | the player's lead; see **Where the cursor sits** |
| `tail_seconds` | 10.0 | silence held after the last note, so the video can sit in a playlist without the next one clipping its ending |
| `lead_seconds` | 0.0 | silence held *before* the first note. Passthrough only: an engraved song's lead is its countdown, measured in bars |
| `intro_seconds` | 8.0 | how long the instrument card covers the staves at the start, fading out over the last second |

### `align`

`quarter_dur` and `t0` are `[start, stop, step]` sweeps; every other list is a
list of candidates to try. A one-element list pins a value.

| key | default | what it absorbs |
|---|---|---|
| `quarter_dur`, `t0` | `[0.45, 1.10, 0.002]`, `[-1.0, 30.0, 0.02]` | tempo, and where the form starts in the file |
| `head_bars` | `[0]` | accompaniment bars before the score's measure 1 |
| `transpose` | `[0]` | semitone rotation of the score's pitch classes to match the audio. **Matching only — the engraved score is never transposed** |
| `stretch` | `[]` | `{"measure": M, "extra_bars": [...]}` — a written measure the recording plays over more bars |
| `offset` | 0.0 | seconds added to every knot, settled by ear; see **Settling the offset** |
| `anchors` | `[]` | `[[measure, seconds], ...]` — a piecewise-linear offset curve, for a take that does not hold one tempo. Overrides the flat `offset`. Write one with `offset=<measure>:<seconds>` on the command line |
| `entry_window` | `null` | `[lo, hi]` seconds the melody entry must fall inside |
| `lock` | `null` | bypasses the search entirely; see **`lock`** |

## Rows

`measures_per_row` is a target, not a rule. `Score.rows` plans the rows and two
things override the width:

A **multi-measure rest is always one row of its own**, however many bars it
spans. Verovio lays a multi-rest out as a single element and drops any break
encoded inside it, so it cannot be cut anyway — and one "wait 18" block reads
better than the same wait chopped into rows.

Between rests, a dynamic program splits the stretch into rows that may be one
measure wider or narrower than nominal. The eye has to jump to the next row
somewhere, and the only moment it can afford to is while a long note sounds, so
a row is steered to **end** on a note of a half or longer and never to open with
one. Costs are in `_plan_segment`: 1 for a row off the nominal width, 3 for
opening on a held note, a 0.5 credit for closing on one — an uneven row is worth
one clean page turn, never nothing.

So the header prints `compás 12–16`, not a fixed count, and two rows on screen
can be different widths.

## Loudness

By default the downloaded stream is muxed untouched. A song that sets
`loudness` opts into level shaping instead: `playalong/mix.py` writes
`build/levelled.opus` and the render muxes that.

This exists because a backing track is mastered for listening, not for playing
over. Its quiet intro and its loud chorus are correct on their own terms and
wrong for a player who needs the sections they play to sit above the ones they
count through. The shaping is **score-driven**: it knows where the bars are,
so a change can land on a barline instead of drifting across one.

```json
"loudness": {
  "target": -14.0, "peak": -2.0, "rest_boost_db": 1.5, "ramp_bars": 2,
  "sections": [
    { "from_measure": 1,  "to_measure": 18, "db": -1.5, "to_db": 3.0, "flatten": true },
    { "from_measure": 19, "to_measure": 34, "db": 7.0 },
    { "from_measure": 51, "to_measure": 66, "db": 5.5, "enter_bars": 8 }
  ],
  "crescendo": { "from_measure": 75, "db": 1.0, "ramp_bars": 4 },
  "ending": { "fade_s": 1.5 }
}
```

The gain at any moment is two curves added:

1. **Levelling.** The recording's EBU R128 momentary loudness, smoothed by a
   sliding *median* five bars wide, inverted. A median ignores a single loud
   stab the way a mean cannot. Clipped to +24 / −12 dB.
2. **Intent**, read off the score: `rest_boost_db` over every melody gap long
   enough to earn a countdown, plus each `sections` entry, plus a closing
   `crescendo`.

| key | what it does |
|---|---|
| `target`, `peak` | final static shift to this integrated LUFS, then a limiter at this peak |
| `rest_boost_db` | lift over the waits, where nothing is being played over the track |
| `ramp_bars` | bars a section takes to arrive, by default |
| `sections[].from_measure`, `.to_measure` | inclusive, in written measures |
| `sections[].db`, `.to_db` | flat lift, or a ramp across the section |
| `sections[].flatten` | also squash the section's *internal* swings, on a one-bar window |
| `sections[].enter_bars` | override `ramp_bars` for this entry. `0` lands it on the barline |
| `crescendo` | a final lift starting at `from_measure`, reaching `db` over `ramp_bars` |
| `ending.fade_s` | seconds to fade after the recording's last hit |

Two behaviours are worth knowing because they are not obvious from the keys.
A **rise anticipates and a drop lands on the barline** (`max(step, ahead)`):
arriving at full level exactly when a loud section starts sounds late, while
dropping early cuts the bar that was still playing. And the gain **freezes at
the recording's last hit**, so the levelling cannot hunt for signal in the tail
and amplify the room — the boom sounds and stops, it does not ring.

Set it by ear in ≥3 dB steps. Below about 1 dB nothing is audible and the probe
is wasted.

## Audio quality

`fetch` asks for `bestaudio` sorted by bitrate then sample rate, and the render
muxes it with `-c:a copy` — a bitstream copy, never a re-encode. The render then
re-probes the output and fails if the codec changed, so a silent transcode
cannot ship.

When `loudness` is set that guarantee moves one file along: the shaped audio is
encoded once, to Opus at 192 kb/s, and *that* is what ships untouched. The
re-probe compares against it, so the check still holds — it just now promises
"the levelled copy shipped whole" rather than "the recording did". The result is
cached against a stamp of its inputs, so re-rendering does not re-level.

The image carries **deno** because yt-dlp needs a JavaScript runtime or it warns
that some formats may be missing, which would quietly cap the audio below the
best available. `--js-runtimes node` is accepted and then ignored by this
version; only deno is actually implemented.

## How the alignment works, and why it is not DTW

A lead sheet is not a transcription of the record. It leaves out the
accompaniment bars before the tune starts, and it compresses a multi-bar break
into one written measure labelled "guitar x4". No monotone warping of the paper
onto the recording exists, which is why the DTW and beat-tracking attempts in
`experiments/` all failed on real charts.

What does exist is a *steady grid* plus a small integer description of what the
paper left out. So the pipeline searches that description directly, over the
`head_bars`, `stretch`, `quarter_dur`, `t0` and `transpose` candidates above.

Each candidate is scored by the cosine between the score's harmony template and
the audio chroma at the quarters it predicts — only on quarters that carry
melody, since rests are evidence-free.

**Always check `transpose`.** An alto sax chart sounds three semitones from the
paper. Search a wrong key and the correlation still reports a confident winner;
it is just wrong. A trustworthy fit has a clear margin over the runner-up key,
and its recovered bpm agrees with the score's own metronome mark.

**And check the tempo, not just the phase.** A winner half a percent off the
true tempo still correlates well — chroma is forgiving over four bars — and
turns into a second of drift by the end of the piece. `el-rey-leon` won at 83.03
bpm against a true 83.95 and looked right for fifty bars. If a human reports the
cursor drifting further out the longer it plays, the offset is not the problem;
pin `quarter_dur` to a single measured value and re-derive the offset.

## `lock`

Correlation maximises harmonic agreement, not playability. Once the numbers are
settled by ear, put them in `align.lock` and the search is skipped entirely:

```json
"lock": { "head_bars": 6, "extra_bars": [1],
          "quarter_dur": 0.682, "t0": 1.358, "transpose": 0 }
```

`reloj` ships locked — those are hand-tuned values, and re-deriving them would
be a regression.

## Settling the offset by ear — the one manual step

**Every song here is a play-along: the melody is not in the recording, by
design.** That is not a quirk of one track, it is what the format is. The
player supplies the melody.

The consequence is structural. Harmony correlation reads the accompaniment, and
an accompaniment holds one chord for whole bars — so it pins down **tempo** and
**key** decisively, and says almost nothing about **absolute phase**. Measured
on `cant-help-falling-in-love`: the eight best candidates spanned `t0` from
1.24s to 2.74s while correlation moved 0.027, and a per-window sweep found
"best" offsets scattered over ±1s with correlation differences in the third
decimal. A flat surface is not a wrong answer, it is *no* answer.

Three sharper anchors were tried and none survives this material:

| anchor | why it fails here |
|---|---|
| melody f0 (`pyin`) | the melody is absent — 1 confident frame in 8142 |
| onset envelope | aliases onto the accompaniment's subdivision; on a triplet ballad it peaks every ~0.3s, and its global maximum pointed the wrong way |
| beat tracking | inherits the same subdivision ambiguity |

So the ear settles it, and `align.offset` is a first-class part of every song's
setup rather than an escape hatch. Positive moves the whole map later — cursor,
row flips and countdowns together, which is what you want: if the map is early,
everything is early.

The loop, roughly twenty seconds per try:

```
./infra/run.sh <song> align clip 8 30 offset=0.6    # writes it into song.json
# listen to the output video, adjust, repeat
./infra/run.sh <song> render                        # only when it sounds right
```

`offset=<measure>:<seconds>` instead writes an entry into `align.anchors`, for a
take whose tempo moves: the offset then interpolates between the measures you
have pinned rather than applying flat.

## Where the cursor sits

Verovio anchors a notehead at its **left edge**. Sweeping the cursor to that
anchor puts the bar on the note half a notehead early — about 0.1s at a ballad
tempo, which reads as "the bar says play, but the note starts a moment later".

The renderer therefore measures each notehead glyph from the SVG path and
centres the cursor on it. Being geometric, it is right at any tempo and any
scale, and it is per-glyph: a whole note is wider than a crotchet and gets a
bigger shift.

This is a separate, smaller term from `align.offset`: geometry moves the cursor
relative to the *notes*, the offset moves the whole map relative to the *audio*.
`layout.cursor_lag_seconds` is the third term and the player's own: it runs the
whole display ahead of the sound, rows included, so the cursor has crossed into
the next row before its first note is due. It defaults to `-0.48`. Reach for
`align.offset` first, since it is the one that moves the countdowns too — and
never absorb a player's lead into it, because that is the only number in the
file with an external check.

The repo does not agree with itself on the lead, which is expected: each was
settled by ear on a different render.

| song | `cursor_lag_seconds` |
|---|---|
| `cant-help-falling-in-love` | -0.22 |
| `el-rey-leon` | -0.28 |
| `reloj` | **+0.35** |

The lead is bounded on both sides, and the upper bound is the one that
surprises: the cursor is an opaque bar drawn over the staff, so a lead large
enough to park it on the notes being read hides them. Too little and the cue
arrives after the moment to attack; too much and it covers the music.

`reloj` is the number to notice. The same player settled the other two around
-0.25 and this one at **+0.35** -- not a little different, but on the other side
of zero, with the bar reaching each note *after* it sounds. A lead is the
player's own constant, so one song sitting across zero from the rest is not
taste changing: it says that song's map is off by about that much, and the lead
is absorbing it. `reloj` is locked with values hand-tuned against an older
renderer and carries no `align.offset` of its own, which is where to look. The
video is right either way -- the player judged it -- but the next person should
know the number is doing two jobs.

A row's cursor starts at the row's left edge and runs in to the first note, so
it is already moving when the note arrives — it does not materialise on top of
it. The run-in is measured back from that first note's glyph, not extrapolated
from the note spacing, because engraved spacing is not proportional to time.

On the final note the cursor sweeps across it for its written length, stops on
the closing barline, and the display stops advancing rows. A bar that keeps
moving reads as "the music continues"; a bar that stops reads as "hold this",
which is what the player still has to do while the recording plays out. The
empty slot beside it then carries a closing card with the piece's numbers.

## Countdowns

Any wait long enough to lose the pulse gets a bar countdown ending exactly on
the player's re-entry. The gate is in *recording* bars, not written ones: the
bars a chart omits are invisible on paper and are precisely the long ones.

The label and the number are measured and centred **as one block**, and nudged
off centre only if a long credit or a long instrument name reaches in. Pinning
each piece to its own offset from the centre leaves a one-digit count sitting
visibly left of a two-digit one.
