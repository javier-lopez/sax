"""Fetching and probing the accompaniment track.

Two representations of the same recording: the original compressed stream,
muxed into the video untouched, and a 22 kHz mono wav that the aligner reads.
Decoding once up front keeps librosa off the network.

A song may opt into a third: a levelled copy (see mix.py), which then takes
the original's place in the video.
"""

import glob
import json
import os
import subprocess

ANALYSIS_SR = 22050


def _existing(build_dir):
    return sorted(glob.glob(os.path.join(build_dir, "audio.*")))


# YouTube signs its media URLs with a JavaScript challenge. deno runs the
# challenge, but the solver script itself ships separately and yt-dlp will not
# fetch it unless asked -- without it the site answers with storyboard images
# and nothing else, which reads as "this video has no formats".
EJS = ["--js-runtimes", "deno", "--remote-components", "ejs:github"]


def _auth(cookies, log):
    if not cookies:
        return []
    log(f"using the signed-in session in {os.path.basename(cookies)}")
    return ["--cookies", cookies]


def fetch(url, build_dir, refetch=False, cookies=None, log=print):
    """Download the highest-quality audio stream into build/audio.<ext>.

    Idempotent: an already-downloaded track is reused, so re-rendering never
    re-hits YouTube and a song whose source URL is unknown still works from a
    file dropped in by hand. A real download clears the slot first, so two
    formats can never sit side by side for the muxer to choose between.
    """
    have = _existing(build_dir)
    if have and not refetch:
        return have[0]
    if not url:
        raise RuntimeError(
            f"song.json has no source URL and {build_dir} holds no audio.*; "
            "either set \"source\" or drop the recording in by hand")
    for stale in have:
        os.remove(stale)
    subprocess.run(
        ["yt-dlp", "--no-playlist", *EJS,
         *_auth(cookies, log),
         # bestaudio already means "highest quality audio-only stream"; the
         # explicit sort makes bitrate then sample rate the tie-breakers rather
         # than yt-dlp's container preferences
         "-f", "bestaudio/best", "-S", "abr,asr",
         "-o", os.path.join(build_dir, "audio.%(ext)s"), url],
        check=True)
    have = _existing(build_dir)
    if not have:
        raise RuntimeError(f"yt-dlp produced no audio for {url}")
    return have[0]


def fetch_video(url, build_dir, refetch=False, cookies=None, log=print):
    """Download a finished video into build/video.<ext>.

    For a song the pipeline does not engrave: the picture is the input, not
    something to be produced. Muxed rather than separate streams, because the
    two are only ever re-encoded together here.
    """
    have = sorted(glob.glob(os.path.join(build_dir, "video.*")))
    if have and not refetch:
        return have[0]
    if not url:
        raise RuntimeError(
            f"song.json has no source URL and {build_dir} holds no video.*")
    for stale in have:
        os.remove(stale)
    subprocess.run(
        ["yt-dlp", "--no-playlist", *EJS,
         *_auth(cookies, log),
         "-f", "bestvideo+bestaudio/best", "--merge-output-format", "mkv",
         "-o", os.path.join(build_dir, "video.%(ext)s"), url],
        check=True)
    have = sorted(glob.glob(os.path.join(build_dir, "video.*")))
    if not have:
        raise RuntimeError(f"yt-dlp produced no video for {url}")
    return have[0]


def local_or_fetch(song):
    """A recording supplied as an input wins over anything downloadable."""
    if song.audio_in:
        if not os.path.exists(song.audio_in):
            raise RuntimeError(f"song.json names audio {song.audio_in}, which is missing")
        return song.audio_in
    return fetch(song.source, song.build, cookies=song.cookies)


def to_wav(src, dest):
    if os.path.exists(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
        return dest
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src,
         "-ac", "1", "-ar", str(ANALYSIS_SR), dest],
        check=True)
    return dest


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        check=True, capture_output=True, text=True).stdout
    return float(json.loads(out)["format"]["duration"])

