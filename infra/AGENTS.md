# AGENTS.md — how to run this pipeline

You are being asked to turn a YouTube backing track plus a MusicXML lead sheet
into a play-along video. Read this before touching anything; `README.md` next to
it explains the machinery, this file explains the **process and who does what**.

## The one fact that shapes everything

**Every recording here is a play-along: the melody is not in it, by design.**
The human plays the melody on saxophone. This is not a property of one song, it
is the format, and it is why the process has exactly one manual step.

Consequence: alignment splits into two halves with very different confidence.

| quantity | who decides | confidence |
|---|---|---|
| tempo | the grid search | high — cross-checks against the score's own metronome mark |
| key / transposition | the grid search | high — a wrong key loses by a wide margin |
| **absolute phase** (`align.offset`) | **the human's ear** | the signal does not contain it |

An accompaniment holds one chord for whole bars. Harmony correlation therefore
pins down *how fast* and *in what key*, and says almost nothing about *where
exactly* the beat falls. Do not try to automate `align.offset`. See **Dead
ends**.

## Division of labour

Automate everything except listening.

```
you:    scaffold, fetch, align, render a short clip
human:  listens to the clip, says earlier / later / good
you:    apply the number, re-clip
human:  approves
you:    full render
```

## Adding a song

One command. It scaffolds, downloads, aligns, and leaves a clip to listen to:

```
./infra/run.sh new <dir-name> <youtube-url> <path/to/score.mxl> "Title" "Credit"
```

It reads the score's metronome mark and centres the tempo sweep on it, which is
faster and far more trustworthy than sweeping blind.

**A song has exactly one output filename, and every render writes to it** — full
or segment. Never invent a second name, never suffix a variant. The human keeps
that one file open in a player and re-watches it; a new name per run turns their
verification into a file hunt. It is overwritten by design.

## The ear loop

Each try costs about thirty seconds:

```
./infra/run.sh <song> align clip 0 40 offset=0.35
```

`offset=` is written into `song.json`, so the value that sounded right is the
value the full render will use. There is no separate step to remember it.

**Positive moves everything later.** If the human says the cursor arrives at the
note *before* the note sounds, the offset must **increase**.

Ask in the units they perceive, not in yours:

> The bar reaches the note — does the note sound at that moment, a bit before,
> or a bit after? Roughly how much?

Their estimate is good to about a tenth of a second, which is all you need.
Take it literally, apply it, and re-clip. Do not average it against a number
you computed; you have no better number.

When they approve:

```
./infra/run.sh <song> render
```

### Two knobs, and they are not interchangeable

`align.offset` answers *where the music is*. It has a true value, it is the same
for every listener, and it can be measured (below). Move it only while the
cursor and the recording genuinely disagree.

`layout.cursor_lag_seconds` answers *how far ahead the cue must run for a human
to play to it*. A cue landing exactly on the beat is already too late: by the
time it arrives the player should have attacked. Negative moves the cursor
earlier. This is taste, it belongs to the player rather than to the song, and it
is the only knob left once the offset measures right. On this player, -0.08.

Getting these backwards is the expensive mistake. Absorbing a player's lead into
`align.offset` destroys the one number in the file that has an external check,
and the next agent inherits a value that no measurement can confirm.

### Measuring the offset instead of asking

Once you are within half a subdivision of correct, the onset envelope stops
aliasing and becomes an instrument. Predict every beat from the sync map, take
the envelope's peak inside a window of +-0.13s (narrower than half a triplet
eighth at this tempo), and report the median of peak minus prediction. Negative
means the recording arrives first, i.e. the cursor is late.

**Validate the estimator before believing it.** Shift the predictions by a known
amount and confirm it returns that amount with the sign flipped. On
`cant-help-falling-in-love` it tracked -0.10 through +0.10 to the millisecond.
Without that control, the global version of the same idea had already returned a
confident number pointing the wrong way.

Measured there: **+0.013s over 197 beats, IQR 6ms**. A sequenced backing track
has a machine-exact grid, so a median that tight is real. A live take will
scatter, and the scatter is itself the finding: it says no single offset fits and
`anchors` are wanted.

What this buys is the expensive half of the loop. The human is no longer needed
to *find* the offset, only to set their own lead.

### How to search for the offset

The ear is the only oracle, and every probe costs a render plus the person's
attention. Spend probes well: this is a search, not a walk.

A binary search **cannot start here**. Bisection needs two bounds that disagree,
and every early reading lands on the same side ("still early"). So run two
phases:

1. **Bracket by doubling.** While the answer keeps coming back on the same side,
   double the step: +0.1, +0.2, +0.4, ... and deliberately overshoot. The first
   reading on the *other* side is worth more than any number of readings on the
   same side, because it is the one that turns a walk into a search. Say so when
   asking: tell them you want to know if it went too far.
2. **Bisect.** With one "too early" and one "too late", take the midpoint.
   Three or four probes settle it.

Change one variable per probe, and re-render the **same passage** the person
last judged — a new offset and a new section at once tells you nothing.

Stop when they say it is right, not at a numeric tolerance. Below roughly 0.05s
the difference stops being audible and further probes spend their attention for
nothing.

Recorded failure: settling `cant-help-falling-in-love` took seven probes
(0.25, 0.76, 0.85, 0.95, 1.05, 1.12, 1.25) because each step was a small
increment from the same side. Doubling would have bracketed it in three, and
the residual measurement would have skipped the search altogether.

## Reading the align output

Check three things before showing anything to the human. If any fails, say so
rather than proceeding.

1. **Key margin.** The winning `transp` should beat the runner-up *key* clearly.
   On a good fit the gap is several hundredths. If several keys are within a
   hundredth, the fit is not trustworthy.
2. **Tempo agreement.** The recovered bpm should land near the score's declared
   metronome mark. Two independent sources agreeing is the strongest evidence
   available here.
3. **Notes located.** The render logs `notes located: N/M`. It must be all of
   them; anything less means the cursor is guessing.

The `t0` spread across the top candidates is *expected* to be wide. That is the
phase ambiguity, not a bug.

## Dead ends — do not re-derive these

All three were measured on `cant-help-falling-in-love` and all three failed.
Trying them again costs an hour and reaches the same place.

| approach | what happened |
|---|---|
| melody pitch tracking (`pyin`) | the melody is absent by design: 1 confident frame in 8142 |
| onset-envelope correlation, *globally* | aliases onto the accompaniment's subdivision — peaks every ~0.3s on a triplet ballad, and its global maximum pointed the **wrong way** (−0.46s, when the truth was about +0.5s). Constrained to a narrow window around an already-close prediction the same envelope is exact — see "Measuring the offset instead of asking" |
| per-window harmony offsets | six of eight windows returned ~0.00s with correlations differing in the third decimal: a flat surface, i.e. no answer rather than the answer zero |

The lesson worth carrying: a global maximum is worthless until you have looked
at whether it is the *only* maximum.

## Hard rules

- **Never guess `align.offset`.** It comes from the human or it stays 0.
- **Never start a full render without approval of the offset.** It costs four
  minutes and `run.sh <song>` with no stage means `all`, which includes it.
- **Never touch a song whose `align.lock` is set.** `reloj` is locked with
  hand-tuned values; re-deriving them is a regression.
- **Inputs are sacred.** `song.json`, the `.mxl`, and any recording named by
  `audio` are inputs. `build/` is the only disposable directory — but check
  first that the song has a `source` URL, or its audio is a one-of-a-kind input
  and belongs beside the score, not in `build/`.
- **Clean up after diagnostics.** Probe frames, `.npy` dumps and scratch files do
  not belong in a song directory.
- **Do not install anything on the host.** Everything runs in the image that
  `run.sh` builds.

## Failure modes

| symptom | cause | fix |
|---|---|---|
| `expected N row-pages from verovio, got M` | the score has fewer/more measures than the row layout assumes | check `measures_per_row`; a score whose measure count is not a multiple still works, the last row is short |
| `no candidate grid fits inside the audio` | tempo sweep too narrow, or the score is longer than the recording | widen `quarter_dur`; confirm the recording is the whole song |
| cursor visibly wrong, `notes located` below total | verovio SVG changed shape | fix `_note_xy`, do not lower the threshold |
| `audio was re-encoded` | the muxer failed to stream-copy | do not silence it; the point is that the audio ships untouched |
| countdown appears where the player is playing | `countdown_min_bars` too low, or the offset is far off | settle the offset first |
| two `audio.*` files in `build/` | a half-finished fetch | delete both and re-fetch; the muxer must never choose between formats |

## Conventions

Source comments explain **why**, never what. No banners, no task IDs, no
historical narration. Match the surrounding style.
