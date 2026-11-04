#!/usr/bin/env bash
# CI: the whole path on LogHub's 2,000-line HDFS sample: agent -> collector -> ClickHouse -> live
# detector. On failure the end of the log is published as an error annotation, readable without
# signing in.
set -uo pipefail
log=integration.log
exec 3>&1  # the job's own output, for the annotation (stdout is redirected to the log below)
report() {
  local tail
  tail=$(tail -n 40 "$log" | sed ':a;N;$!ba;s/%/%25/g;s/\r//g;s/\n/%0A/g')
  echo "::error title=integration failed::${tail}" >&3
  exit 1
}
trap report ERR
set -e
{
  curl -fsSL -o hdfs2k.log https://raw.githubusercontent.com/logpai/loghub/master/HDFS/HDFS_2k.log
  bin/collector > collector.log 2>&1 &
  sleep 3
  agent/target/release/canary-agent --file hdfs2k.log --format hdfs --source hdfs
  sleep 3
  n=$(curl -s -u canary:canary "http://127.0.0.1:8124/?database=canary" --data-binary "SELECT count() FROM logs")
  echo "stored $n lines"
  cat collector.log
  test "$n" = "2000"
  (cd detector && python -m canary.live --source hdfs --dataset HDFS)
  s=$(curl -s -u canary:canary "http://127.0.0.1:8124/?database=canary" --data-binary "SELECT count() FROM scored_sessions")
  echo "scored $s sessions"
  test "$s" -gt 0
} > "$log" 2>&1
cat "$log"
