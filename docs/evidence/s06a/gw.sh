#!/bin/bash
# start_gw <tag>  / stop_gw
source "$S6/env.sh"
start_gw() {
  cd "$WT"
  nohup "$PYBIN" "$S6/launch_gateway.py" > "$S6/logs/gw-$1.log" 2>&1 &
  for i in $(seq 1 30); do
    sleep 1
    P=$(grep -o "Started server process \[[0-9]*\]" "$S6/logs/gw-$1.log" | grep -o "[0-9]*")
    [ -n "$P" ] && break
  done
  echo "$P" > "$S6/logs/gw.pid"
  for i in $(seq 1 20); do curl -s -m 3 http://127.0.0.1:18997/readyz >/dev/null 2>&1 && break; sleep 1; done
  echo "gateway pid=$P tag=$1"
}
stop_gw() {
  P=$(cat "$S6/logs/gw.pid" 2>/dev/null)
  [ -n "$P" ] && powershell.exe -NoProfile -Command "Stop-Process -Id $P -Force -ErrorAction SilentlyContinue" >/dev/null 2>&1
  sleep 2; echo "gateway $P stopped"
}
