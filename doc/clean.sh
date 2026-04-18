#!/usr/bin/env bash
# (1) Register deleted Telegram accounts by cached display name (case-insensitive
#     "deleted account"), merge with existing deleted_peers, delete those messages.
# (2) Optional: delete rows with empty display_name (set CLEAN_EMPTY=1) so a
#     rescan can refill names — same idea as the original clean.sh.
set -euo pipefail
cd "$(dirname "$0")"

DB="${1:-.cache/private_dm_messages.sqlite}"

if [[ ! -f "$DB" ]]; then
  echo "No database at: $DB" >&2
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 not found (install SQLite CLI)." >&2
  exit 1
fi

n_deleted="$(sqlite3 "$DB" <<'SQL'
CREATE TABLE IF NOT EXISTS deleted_peers (
  peer_user_id INTEGER PRIMARY KEY NOT NULL
);
INSERT OR IGNORE INTO deleted_peers (peer_user_id)
SELECT DISTINCT peer_user_id FROM messages
WHERE LOWER(TRIM(COALESCE(display_name, ''))) = 'deleted account';
DELETE FROM messages
WHERE peer_user_id IN (SELECT peer_user_id FROM deleted_peers)
   OR LOWER(TRIM(COALESCE(display_name, ''))) = 'deleted account';
SELECT changes();
SQL
)"

n_empty=0
if [[ "${CLEAN_EMPTY:-}" == "1" ]]; then
  n_empty="$(sqlite3 "$DB" <<'SQL'
DELETE FROM messages
WHERE display_name IS NULL OR TRIM(display_name) = '';
SELECT changes();
SQL
)"
fi

echo "Deleted ${n_deleted:-0} message row(s) (deleted-account / deleted_peers) in $DB"
if [[ "${CLEAN_EMPTY:-}" == "1" ]]; then
  echo "Deleted ${n_empty:-0} message row(s) with empty display_name (CLEAN_EMPTY=1)"
else
  echo "Tip: set CLEAN_EMPTY=1 to also remove rows with empty display_name before a rescan"
fi
echo "Peer ids remain in deleted_peers for search exclusion (where applicable)."

if [[ "${n_deleted:-0}" == "0" && "${n_empty:-0}" == "0" ]]; then
  echo "No rows matched. Sample display_name values (if any):" >&2
  sqlite3 -column -header "$DB" \
    "SELECT display_name, COUNT(*) AS n FROM messages GROUP BY display_name ORDER BY n DESC LIMIT 15;" \
    >&2 || true
fi
