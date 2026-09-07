# sax play-along

https://javier.io/blog/es/2026/07/27/sax-yds-120.html

For personal backing tracks videos.

Give it a recording on YouTube and a lead sheet in MusicXML and it renders a
video: two staff rows on screen, a cursor sweeping the one you are playing, and
a bar countdown over every wait long enough to lose the pulse. The recordings
carry no melody — that is the point. You play the melody.

```
./infra/run.sh new <dir> <youtube-url> <score.mxl> "Title" "Credit"
./infra/run.sh <dir> render
```

## Layout

```
infra/          the engine. Nothing song-specific lives here.
  run.sh        the only entry point; everything runs in Docker
  playalong/    fetch, align, engrave, render
  AGENTS.md     the working protocol -- read this before changing anything
  README.md     how the pipeline works, stage by stage
  experiments/  superseded aligners, kept because they record what failed
<song>/         one directory per song: inputs, song.json, and the video
```

A song directory holds inputs and outputs, never code. Everything derived lands
in `<song>/build/` and is ignored — delete it and the next run rebuilds it.

## Scores: the .mxl is a lockfile, not a copy

The scores are written in MuseScore, which saves to the cloud. That is where
they live and where they are edited; nothing here tries to be their master, and
no step in this repo asks you to remember to export anything.

The `.mxl` files are still committed, for a different reason. A song directory
holds a video and the score that produced it, and git records them changing
together — so any video in the history can be traced back to the exact notes it
was rendered from. That is a lockfile's job, and a lockfile is *supposed* to lag
its source.

So a score edited on MuseScore and not updated here breaks nothing. The repo has
not gone stale; it is still telling the truth about the video sitting next to it.
When you want the new notes, you download the `.mxl` and re-render — and the
download is part of re-rendering, not maintenance you forgot to do.

`song.json` records where each score came from in `score_source`, which is the
only link that has to stay current.

## The two knobs

`align.offset` — where the music is. It has a true value, the same for every
listener, and it can be measured against the recording's own onsets. See
`infra/AGENTS.md`.

`layout.cursor_lag_seconds` — how far ahead of the sound the cursor runs.
Negative moves it earlier. A cue landing exactly on the beat is already too
late: by then you should have attacked. This belongs to the player, not to the
song, and the default (`-0.48`) is already set.

## Requirements

Docker. Nothing else — `run.sh` builds its own image and runs as you, so no
tool ever lands on the host.
