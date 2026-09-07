# experiments — superseded approaches

The route to the alignment in `playalong/align.py`, kept because a rejected
experiment is the only record of why the surviving one looks the way it does.

| file | idea | why it lost |
|---|---|---|
| `align.py` | chroma + subsequence DTW | warps the score onto the record monotonically; cannot invent the accompaniment bars the chart omits |
| `beat_align.py` | beat-track the audio, slide the score over the beat grid | the beat tracker drifts on a bolero, and picking a start beat still cannot absorb a compressed break |
| `grid_align.py` | steady (tempo, offset) grid search | right shape, but with no structure model the whole piece has to fit one straight line |
| `struct_align.py` | grid search **plus** ghost head bars and a stretched break | this one worked; generalised into `playalong/align.py` |
| `render.py` | one full score page per frame, active note in red | the eye has to hunt for the red note, and page flips arrive without warning |
| `render2.py` | two rows, sweeping cursor | this one worked; generalised into `playalong/render.py` |
| `Dockerfile.reloj` | original image | superseded by `../Dockerfile` (adds yt-dlp) |

They are frozen: they read hardcoded paths from the old `reloj/` layout and will
not run as-is.
