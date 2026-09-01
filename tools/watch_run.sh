#!/usr/bin/env bash
# Emit one line per event on a live driver.py run: server lag, a stop
# condition, a disconnect, or the run ending. Every event says whether the
# run is still alive, because "server lag" means something different if the
# process died with it.
#
#   tools/watch_run.sh <pid> <log>
#
# Meant to be handed to the Monitor tool, which turns each stdout line into
# a notification. See .claude/skills/watch_run/SKILL.md.

PID="$1"
LOG="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
CHK="$HERE/check_disconnect.py"

QUIET_AFTER=150
POLL=20

LAG='not answering|answering again|does not count|starting the pass again|going to the default state|the server stalled'

if [ -z "$PID" ] || [ -z "$LOG" ]; then
  echo "usage: watch_run.sh <pid> <log>"
  exit 2
fi

# grep -c prints 0 AND exits 1 when nothing matches, so `|| echo 0` would
# make the value "0\n0" and every -gt after it an "integer expected" error.
count() {
  c=$(grep -cE "$1" "$LOG" 2>/dev/null)
  echo "${c:-0}" | head -1
}

alive() {
  tasklist //FI "PID eq $PID" //NH 2>/dev/null | grep -q "$PID"
}

state() {
  if alive; then echo "run ALIVE"; else echo "run DEAD"; fi
}

war_open() {
  grep -q "WAR LAG" "$LOG" 2>/dev/null &&
    [ "$(count 'WAR LAG: done')" -lt "$(count 'WAR LAG: a war')" ]
}

seen_lag=$(count "$LAG")
seen_stop=$(count "STOPPED:")
told_quiet=0

while true; do
  now=$(count "$LAG")
  if [ "$now" -gt "$seen_lag" ] 2>/dev/null; then
    echo "SERVER LAG at $(date '+%H:%M:%S') -- $(state)"
    grep -E "$LAG" "$LOG" | tail -n $((now - seen_lag)) | tail -6
    seen_lag=$now
  fi

  now=$(count "STOPPED:")
  if [ "$now" -gt "$seen_stop" ] 2>/dev/null; then
    echo "STOP CONDITION at $(date '+%H:%M:%S') -- $(state)"
    grep "STOPPED:" "$LOG" | tail -n $((now - seen_stop))
    seen_stop=$now
  fi

  quiet=$(( $(date +%s) - $(stat -c %Y "$LOG" 2>/dev/null || date +%s) ))
  if [ "$quiet" -gt "$QUIET_AFTER" ]; then
    if [ "$told_quiet" -eq 0 ]; then
      if war_open; then
        echo "LOG QUIET ${quiet}s at $(date '+%H:%M:%S') -- a war window is open, not a disconnect -- $(state)"
      else
        echo "LOG QUIET ${quiet}s at $(date '+%H:%M:%S') -- checking the screen -- $(state)"
        if [ "$(py "$CHK" 2>/dev/null)" = "DISCONNECTED" ]; then
          echo "DISCONNECTED -- the game is on the disconnect notice; py src_1080p/recovery.py is the way back"
        else
          echo "not a disconnect; the run is quiet for another reason"
        fi
      fi
      told_quiet=1
    fi
  else
    told_quiet=0
  fi

  if ! alive; then
    echo "RUN ENDED at $(date '+%H:%M:%S') -- pid $PID is gone -- run DEAD"
    grep -E "STOPPED:|stopped:|crashed:|Traceback|ran for|TOTAL " "$LOG" 2>/dev/null | tail -6
    exit 0
  fi
  sleep "$POLL"
done
