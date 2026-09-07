# infra — play-along video pipeline

Turns *a recording on YouTube* + *a lead sheet in MusicXML* into a karaoke-style
play-along video: two staff rows on screen, a cursor sweeping the one you are
playing, and a bar countdown over every wait.

Extracted from `reloj/`, which had grown the engine and the song into the same
directory. A song directory now holds only inputs and outputs.

```
./infra/run.sh <song-dir> [stage ...]

./infra/run.sh reloj                      # fetch + align + render
./infra/run.sh reloj align preview        # re-fit and eyeball it
```

Nothing is installed on the host: `run.sh` builds `sax-playalong-infra` on first
use and runs everything inside it as the invoking user.

## Stages

| stage | reads | writes |
|---|---|---|
| `fetch` | `source` URL or the `audio` input | `build/audio.*`, `build/analysis.wav` |
| `align` | score + wav | `build/sync_map.json` |
| `preview` | sync map | `build/preview/*.png` |
| `clip F T` | sync map + audio | the `output` video, seconds F..T only |
| `render` | sync map + audio | the `output` video |

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
  "title": "Reloj",
  "credit": "Roberto Cantoral",
  "source": "https://www.youtube.com/watch?v=...",
  "audio": "reloj_backing.webm",
  "score": "reloj.mxl",
  "output": "reloj_playalong.mkv",
  "layout":  { "measures_per_row": 4, "countdown_label": "GUITARRA",
               "countdown_min_bars": 2, "tail_seconds": 10.0 },
  "align":   { "quarter_dur": [0.65, 0.70, 0.001], "t0": [-1.0, 8.0, 0.02],
               "head_bars": [5, 6, 7], "transpose": [0],
               "stretch": [{ "measure": 36, "extra_bars": [0, 1, 2] }],
               "entry_window": [21.5, 24.5], "lock": null }
}
```

Under `align`, `quarter_dur` and `t0` are `[start, stop, step]` sweeps; every
other list is a list of candidates to try.

`audio` is optional and points at a recording already in the song directory. It
wins over `source`, and it is how a song whose URL is lost stays reproducible —
`reloj` uses it, which is why its `build/` is as disposable as everyone else's.

## Audio quality

`fetch` asks for `bestaudio` sorted by bitrate then sample rate, and the render
muxes that stream with `-c:a copy` — a bitstream copy, never a re-encode. The
render then re-probes the output and fails if the codec changed, so a silent
transcode cannot ship.

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
paper left out. So the pipeline searches that description directly:

| knob | what it absorbs |
|---|---|
| `head_bars` | accompaniment bars before the score's measure 1 |
| `stretch` | a written measure the recording plays over more bars |
| `quarter_dur`, `t0` | tempo and where the form starts in the file |
| `transpose` | the chart is written for a transposing instrument |

Each candidate is scored by the cosine between the score's harmony template and
the audio chroma at the quarters it predicts — only on quarters that carry
melody, since rests are evidence-free.

**Always check `transpose`.** An alto sax chart sounds three semitones from the
paper. Search a wrong key and the correlation still reports a confident winner;
it is just wrong. A trustworthy fit has a clear margin over the runner-up key,
and its recovered bpm agrees with the score's own metronome mark.

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
`layout.cursor_lag_seconds` exists as a purely visual trim on top and defaults
to 0; reach for `align.offset` first, since it moves the countdowns too.

On the final note the cursor **parks** instead of sweeping on to the edge of the
row, and the display stops advancing rows. A bar that keeps moving reads as "the
music continues"; a bar that stops on the note reads as "hold this", which is
what the player still has to do while the recording plays out.

## Countdowns

Any wait long enough to lose the pulse gets a bar countdown ending exactly on
the player's re-entry. The gate is in *recording* bars, not written ones: the
bars a chart omits are invisible on paper and are precisely the long ones.
