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

There are two ear loops, not one, and they run in this order: **timing first,
then loudness**. Settling `align.offset` needs a clip; judging a level arc needs
the whole piece, because it is the sections in relation to each other that is
being judged. Do not open the second loop before the first one closes.

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
earlier.

It is bounded above as well as below, and agents keep missing the upper bound.
The cursor is an opaque bar drawn over the staff, so a lead that parks it on the
notes the player is reading hides them -- "se adelanta" can mean the cue is
early *or* that the bar is sitting on the next bar of music. Ask which, because
the fix is the same knob in opposite directions. This is taste, it belongs to the player rather than to the song, and it
is the only knob left once the offset measures right. It defaults to -0.48. The
songs here carry -0.22, -0.28 and +0.35, each settled by ear on its own render;
`README.md` next to this file tabulates them. Take the human's number for the
song in front of you and do not average it against another song's.

**A lead on the far side of zero from its siblings is a finding, not a
preference.** The lead belongs to the player, so the same player's songs should
cluster. One that does not is absorbing an error in that song's map -- check its
`align.offset` and, if it is locked, whether the lock predates the current
renderer. Say so rather than quietly writing the number down.

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
   available here. Near is not enough on its own: half a percent still
   correlates well over four bars and becomes a second of drift by the end. If
   the human reports the cursor slipping *further* out the longer it plays, the
   offset is not the problem — measure the true tempo, pin `quarter_dur` to a
   single value, and re-derive the offset against it.
3. **Notes located.** The render logs `notes located: N/M`. It must be all of
   them; anything less means the cursor is guessing.

The `t0` spread across the top candidates is *expected* to be wide. That is the
phase ambiguity, not a bug.

## Shaping the loudness

Only when the song sets `loudness`. `infra/README.md` documents the keys; this
is how to run the loop without burning the human's attention.

**Move in steps of 3 dB or more.** A 1 dB change is inaudible and a 2 dB change
is arguable. A recorded failure: a round of 0.5-1.5 dB moves came back as "no
noté la diferencia, no sé qué estás moviendo" — every probe had been spent on a
change below the threshold of hearing. Bracket the way you bracket the offset:
overshoot deliberately, because the first reading that comes back "too much" is
worth more than any number of "still not enough".

**Ask about relations, not absolutes.** YouTube normalises the whole video to
about -14 LUFS and only ever downward, so the overall level is not yours to set;
what you control is which section sits above which. "Is the chorus louder than
the intro?" is answerable. "Is it loud enough?" is not.

**Judge on the whole render.** A clip cannot show an arc. This is the one stage
where the four-minute render is the cheap option.

**Report what you measured.** Give the per-section integrated LUFS after every
change. The human hears sections; the numbers tell you whether what they heard
is what you moved.

## Dead ends — do not re-derive these

All three were measured on `cant-help-falling-in-love` and all three failed.
Trying them again costs an hour and reaches the same place.

| approach | what happened |
|---|---|
| melody pitch tracking (`pyin`) | the melody is absent by design: 1 confident frame in 8142 |
| onset-envelope correlation, *globally* | aliases onto the accompaniment's subdivision — peaks every ~0.3s on a triplet ballad, and its global maximum pointed the **wrong way** (−0.46s, when the truth was about +0.5s). Constrained to a narrow window around an already-close prediction the same envelope is exact — see "Measuring the offset instead of asking" |
| per-window harmony offsets | six of eight windows returned ~0.00s with correlations differing in the third decimal: a flat surface, i.e. no answer rather than the answer zero |
| generic levelling (`dynaudnorm`) for the loudness arc | its smoothing window is centred, so it starts turning down *before* a loud section arrives and up before a quiet one: the end of every verse sagged into the chorus. A leveller that cannot see the barlines cannot land on them — hence the score-driven `mix.py` |

The lesson worth carrying: a global maximum is worthless until you have looked
at whether it is the *only* maximum.

## Hard rules

- **Never guess `align.offset`.** It comes from the human or it stays 0.
- **Never start a full render without approval of the offset.** It costs four
  minutes and `run.sh <song>` with no stage means `all`, which includes it.
- **Never re-level audio the human has already approved.** `build/levelled.opus`
  is cached against its inputs; a render that only changes the picture must
  reuse it. Say so before rendering, so they know what they are about to watch
  is the mix they signed off on.
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
| `expected N row-pages from verovio, got M` | the row plan and the breaks verovio honoured disagree — almost always a multi-measure rest, which verovio lays out as one element and inside which it drops any encoded break | do not touch `measures_per_row`; rows are planned by `Score.rows` and a multirest is deliberately its own row. Check that the `.mxl` really encodes the rest as `multiple-rest` |
| `no candidate grid fits inside the audio` | tempo sweep too narrow, or the score is longer than the recording | widen `quarter_dur`; confirm the recording is the whole song |
| cursor visibly wrong, `notes located` below total | verovio SVG changed shape | fix `_note_xy`, do not lower the threshold |
| `audio was re-encoded` | the muxer failed to stream-copy | do not silence it; the point is that whatever the render was handed ships whole. With `loudness` set that is `build/levelled.opus`, not the download — the download is re-encoded once, on purpose, and only there |
| the ending rings on instead of stopping | the leveller hunting for signal in the tail | the gain freezes at the last hit by design; check `ending.fade_s` rather than the section levels |
| a loudness change is inaudible | it was smaller than 3 dB | do not probe again at the same size; double it |
| countdown appears where the player is playing | `countdown_min_bars` too low, or the offset is far off | settle the offset first |
| two `audio.*` files in `build/` | a half-finished fetch | delete both and re-fetch; the muxer must never choose between formats |

## Conventions

Source comments explain **why**, never what. No banners, no task IDs, no
historical narration. Match the surrounding style.
