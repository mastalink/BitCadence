watch_run() { # watch_run <run_id> <max_ticks>
  for i in $(seq 1 ${2:-18}); do
    S=$("$PYBIN" -c "
import sqlite3,os,sys
db=sqlite3.connect('file:'+os.environ['MCO_SCORE_DB']+'?mode=ro',uri=True)
r=db.execute('SELECT status FROM runs WHERE id=?',(sys.argv[1],)).fetchone()
print(r[0] if r else 'none')" "$1")
    echo "  t=$((i*5))s $1 -> $S"
    case "$S" in accepted|blocked|failed) return 0;; esac
    sleep 5
  done
}
