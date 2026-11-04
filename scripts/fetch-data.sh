#!/usr/bin/env bash
# Downloads the LogHub datasets Canary is evaluated on, checking their SHA-256, into data/raw/:
#   HDFS_v1 (11.2 M lines, block labels) and BGL (4.7 M lines, line labels) from Zenodo record
#   8196385, and the LogHub-2k samples with hand-made templates from the LogHub repository.
# LogHub (https://github.com/logpai/loghub) is free for research; cite Zhu et al., "Loghub: A Large
# Collection of System Log Datasets for AI-driven Log Analytics", ISSRE 2023.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
raw="$root/data/raw"
mkdir -p "$raw/loghub2k"

fetch() { # url sha256 file
  local url="$1" sha="$2" file="$3"
  if [ ! -f "$file" ] || ! echo "$sha  $file" | sha256sum -c - >/dev/null 2>&1; then
    echo "downloading $url"
    curl -fsSL --retry 3 -o "$file.part" "$url" || { echo "download failed: $url" >&2; exit 1; }
    echo "$sha  $file.part" | sha256sum -c - >/dev/null || { echo "checksum mismatch: $url" >&2; rm -f "$file.part"; exit 1; }
    mv "$file.part" "$file"
  fi
}

zenodo="https://zenodo.org/records/8196385/files"
fetch "$zenodo/HDFS_v1.zip?download=1" 04f919f2185821f23f045dca611a7586429bdabc601bd7b43f30005f8e289b01 "$raw/HDFS_v1.zip"
fetch "$zenodo/BGL.zip?download=1" d67fd82a711aea0157a9b83175892c6ee60e384a2ddf5bc51f39118453816da8 "$raw/BGL.zip"
for d in HDFS_v1 BGL; do
  [ -d "$raw/$d" ] || unzip -qo "$raw/$d.zip" -d "$raw/$d"
done
gh="https://raw.githubusercontent.com/logpai/loghub/master"
for d in HDFS BGL; do
  curl -fsSL -o "$raw/loghub2k/${d}_2k.log" "$gh/$d/${d}_2k.log"
  curl -fsSL -o "$raw/loghub2k/${d}_2k.log_structured.csv" "$gh/$d/${d}_2k.log_structured.csv"
done
echo "done"
