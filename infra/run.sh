#!/usr/bin/env bash
# Run the play-along pipeline for a song directory, inside the pinned image.
#
#   ./infra/run.sh reloj                     # fetch + align + render
#   ./infra/run.sh reloj align preview       # just re-fit and eyeball it
#
# Everything the pipeline needs lives in the song dir; nothing is installed on
# the host.
set -euo pipefail

IMAGE=sax-playalong-infra:latest
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(dirname "$here")

if [ $# -lt 1 ]; then
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
# `new` scaffolds a song directory and takes it as far as a clip to listen to,
# which is everything before the one step that needs a human ear.
if [ "$1" = "new" ]; then
    shift
    [ $# -ge 3 ] || { echo "usage: run.sh new <dir> <youtube-url> <score.mxl> [title] [credit]" >&2; exit 2; }
    name=$1; url=$2; mxl=$3; title=${4:-$1}; credit=${5:-}
    mkdir -p "$root/$name"
    cp -n "$mxl" "$root/$name/$(basename "$mxl")"
    set -- "$name" scaffold "score=$(basename "$mxl")" "url=$url" \
           "title=$title" "credit=$credit"
    "$0" "$@"
    "$0" "$name" fetch align
    "$0" "$name" clip 0 40
    echo
    echo "listen to the output video, then tune:"
    echo "  $0 $name align clip 0 40 offset=0.3"
    exit 0
fi

song=$1; shift

if [ ! -d "$root/$song" ]; then
    echo "no such song directory: $root/$song" >&2
    exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "building $IMAGE ..."
    docker build -t "$IMAGE" "$here"
fi

tty=(); [ -t 0 ] && [ -t 1 ] && tty=(-it)

exec docker run --rm "${tty[@]}" \
    --user "$(id -u):$(id -g)" \
    -v "$here:/infra:ro" \
    -v "$root:/work" \
    "$IMAGE" "/work/$song" "$@"
