# Telegram helpers (Telethon)

Small [Telethon](https://docs.telethon.dev/)–based CLI helpers: channel membership, moderation, private chat export, and a **local SQLite cache** for searching 1:1 DM text.

The main entry point is **`telegram-tk`** or **`python -m telegram_toolkit`** (subcommands: **`auth`**, **`search`**, **`rescan`**, **`full-rescan`**, **`show`**, **`name`**, **`channel-member`**, **`list`**). Everything lives in the **`telegram_toolkit/`** package; run from the **repository root** so imports and **`.env`** resolution match the docs.

## Setup

1. **Python 3** — Use a supported CPython (e.g. 3.9+). Create a venv at **`.venv/`**:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **API credentials** — Copy **`.env.example`** to **`.env`** and set values from [my.telegram.org/apps](https://my.telegram.org/apps):

   - `TELEGRAM_API_ID`
   - `TELEGRAM_API_HASH`
   - Optional: `TELEGRAM_SESSION` — session file basename (default `telegram_session` → `telegram_session.session` in the current working directory).

3. **Login once** (creates or updates the `.session` file):

   ```bash
   source .venv/bin/activate
   python -m telegram_toolkit auth
   ```

   Same as **`./telegram-tk auth`** if the launcher is executable.

Run repo tools from the **repository root** so imports and default paths resolve.

---

## `telegram-tk` (DM cache CLI)

| Subcommand | Purpose |
|------------|---------|
| **`auth`** | Interactive Telegram login (creates/updates **`TELEGRAM_SESSION`** / default session file). No extra flags. |
| **`search`** *TEXT…* | Search the SQLite cache only (no Telegram). Flags: `--cache`, `--verbose`, `--header`. |
| **`rescan`** | Refresh **top N** recent private user **messages** (default **N=20**) and **`chats`** rows for **channel / group** dialogs only in the **same first N** dialog slots. Flags: `--cache`, `--recent-peer-limit`, `--sync-per-peer-limit`, `--rescan-top-all`, `--no-bots`, `--notrace`. |
| **`full-rescan`** | Refresh **every** private 1:1 chat **and** **`chats`** metadata for **all** channels/groups in your dialog list. Flags: `--cache`, `--sync-per-peer-limit`, `--no-bots`, `--notrace`. |
| **`show`** *USER_ID* | Print cache + Telethon `get_entity` info for one numeric user id. Flags: `--cache`, `--notrace`. |
| **`name`** *TEXT…* | Find dialogs by **title / display name** (not message text). Uses the **`chats`** table (SQLite only; filled by **rescan** / **full-rescan** as above). Cyrillic → Latin, then **per-word** **rapidfuzz.WRatio** (default **82**) plus prefix / substring rules. Stdout: **space-padded** columns **`peer_kind`**, **`peer_id`**, **`title`**, **`username`**. Flags: `--cache`, `--min-score`, `--header`. |
| **`channel-member`** *CHANNEL* | For each **`--id`** / **`--file`** / stdin user id, prints TSV **`user_id`**, **`member`** (`0`/`1`) using **`get_permissions`**. Flags: **`--no-header`**, **`--ok-if-not-member`** (exit 0 even if some ids are not members). Exit **1** if any id is not a member (unless overridden); **2** on admin / RPC errors. |
| **`list`** *CHANNEL* | List **every member** of a channel or megagroup (Telethon **`iter_participants`**). **CHANNEL** can be **`@username`**, id, or a **title fragment** resolved like **`name`** from **`chats`**. TSV/CSV: **`user_id`**, **`username`**, **`first_name`**, **`last_name`**, **`joined_date`**, **`joined_time`**, **`last_private_date`**, **`last_private_time`** (all in one output time zone; **default `America/Los_Angeles`** US Pacific via **`--tz`**; **`PST`** / **`PDT`** / **`PT`** accepted as that zone). **`last_private_*`** uses the newest cached **1:1** row in **`messages`** (same DB as **rescan**); empty if unknown. Flags include **`--output`**, **`--tz`**, **`--min-score`**, **`--pick`**, **`--max-cache-age`**, **`--refresh`**, **`--limit`**, **`--no-header`**. |

**Typical use:** `telegram-tk rescan` then `telegram-tk search "keyword"`.

**Ways to run**

- From the clone: **`./telegram-tk`** (after `chmod +x telegram-tk`) or **`python -m telegram_toolkit`** with the same arguments (run from repo root).
- From anywhere: copy **`doc/telegram-tk.sh`** to **`~/bin/telegram-tk`**, set **`TELEGRAM_TK_REPO`** to your clone path (or edit the default inside the script), `chmod +x`. See the comments at the top of that file.

```bash
chmod +x telegram-tk    # once, in repo root
./telegram-tk auth
./telegram-tk search "invoice paid"
./telegram-tk rescan --recent-peer-limit 50 --notrace
./telegram-tk full-rescan
./telegram-tk show 15840524
./telegram-tk name "ivan" --header
./telegram-tk channel-member @YourChannel --id 123456 --ok-if-not-member
./telegram-tk list @YourChannel
./telegram-tk list -1001234567890
./telegram-tk list @YourMegagroup --no-header | grep -i pattern
```

**Legacy:** if the first token after **`python -m telegram_toolkit`** is not a subcommand, **`search`** is implied (e.g. **`python -m telegram_toolkit "invoice"`**).

### Behaviour notes (cache + search)

- **Default DB:** **`.cache/private_dm_messages.sqlite`** (override with **`--cache`** on each subcommand).
- **Search** is substring match, **Unicode case-insensitive** (Python `casefold` via SQLite).
- **`rescan`:** walks **all** dialogs. **`chats`** rows for **channels / basic groups** are updated only for dialogs in the **first N** positions (same **`N`** as **`--recent-peer-limit`**). **User/bot** **`chats`** rows are still updated for each user dialog seen. Only **private user** chats in the top‑**N** window get **message** rows synced; channel/supergroup/group **messages** are never written. Within that window, peers whose cache is **only your outgoing** messages are skipped unless **`--rescan-top-all`**. **`--sync-per-peer-limit`** caps how many newest messages are fetched the first time a peer is cached. **`full-rescan`** refreshes **`chats`** for **every** channel/group dialog.
- **Deleted accounts** (`User.deleted` from Telegram) are not synced; ids for those accounts go in **`deleted_peers`** and are excluded from search. **`clean.sh`** (see below) can trim SQLite rows for deleted-account display names; optional **`CLEAN_EMPTY=1`** removes rows with empty **`display_name`** before a rescan.
- **Search stdout:** default is **space-padded** columns, one row per person (`peer_user_id`, **`display_name`** as chat-list first/last name). **`--verbose`** → TSV per matching message. **`--header`** adds a header row (default is no header).
- **Stderr:** only **`rescan`** / **`full-rescan`** print progress (TTY spinner or one line per DM); **`--notrace`** turns that off.

**`name`** (deps: **`cyrtranslit`**, **`rapidfuzz`**) does **not** read channel/group message bodies; it matches **titles** from **`chats`** (cache only). Default stdout is **space-padded** columns (like **`search`**). Lower **`--min-score`** (WRatio) admits noisier fuzzy matches; raise **`--min-score`** if too many unrelated rows still match.

---

## Repo root helpers

| File | Purpose |
|------|---------|
| **`telegram-tk`** | `cd` to repo root, then **`.venv/bin/python -m telegram_toolkit`**. |
| **`run.sh`** | `source .venv` and runs **`python -m telegram_toolkit search "Крымский жребий"`** (edit for your query). |
| **`clean.sh`** | SQLite maintenance: register **deleted account** display names into **`deleted_peers`**, delete matching messages; optional **`CLEAN_EMPTY=1`** for empty **`display_name`**. Optional DB path: **`./clean.sh /path/to.sqlite`**. |

---

## Library (`telegram_toolkit/`)

| Module | Role |
|--------|------|
| **`client`** | **`make_client()`** — builds **`TelegramClient`** from **`TELEGRAM_*`** env vars. |
| **`dm_cache`** | Private DM SQLite cache: **`search_local`**, **`refresh_cache`**, **`show_peer`**, trace helpers, **`deleted_peers`** handling. |
| **`cli`** | Argparse implementation for **`telegram-tk`** (imports **`dm_cache`** + **`client`**). |
| **`list_users`**, **`delete_users`**, **`list_user_messages`** | Channel list / ban-by-id / private thread export (see **Channel / moderation CLIs** below). |
| **`find_dm_peer`**, **`channel_member`** | Library helpers behind **`name`** and **`channel-member`** (also **`python -m telegram_toolkit.find_dm_peer`** and **`python -m telegram_toolkit.channel_member`**). |

Importing **`telegram_toolkit`** (any submodule) runs **`.env`** loading from the repo root once.

---

## Channel / moderation CLIs

Each module is runnable with **`python -m telegram_toolkit.<module>`** from the repo root.

### `list_users`

TSV: `user_id`, `username`, `first_name`, `last_name`, `joined_date`, `joined_time`, `last_private_date`, `last_private_time` (default Pacific `America/Los_Angeles`, override with **`--tz`**; last-private from local DM cache; sorted by join time when available). Same behaviour as **`telegram-tk list`**.

```bash
./telegram-tk list @ChannelName
python -m telegram_toolkit.list_users @ChannelName
python -m telegram_toolkit.list_users @ChannelName --no-header | grep -i pattern
```

Options: **`--limit N`**, **`--no-header`**.

### `delete_users`

Bans by id. Use **`--dry-run`** first; **`--yes`** required to apply.

```bash
echo 123456789 | python -m telegram_toolkit.delete_users @Channel --dry-run
echo 123456789 | python -m telegram_toolkit.delete_users @Channel --yes
python -m telegram_toolkit.delete_users @Channel --yes --file ids.txt
```

Ids: stdin, **`--file`**, or repeated **`--id`**. Lines may be plain ids or TSV (first column = id).

### `list_user_messages`

Private 1:1 only. Columns: `message_id`, `date_utc`, `direction` (`out` / `in`), `sender_id`, `text`.

```bash
python -m telegram_toolkit.list_user_messages @username
python -m telegram_toolkit.list_user_messages 8718875571 --from-channel @ChannelWhereIdCameFrom
```

Numeric ids often need **`@username`** or **`--from-channel`** so Telethon can resolve the user. Default order is oldest first; **`--newest-first`** reverses.

### `find_dm_peer`

Display-name lookup (phonetic / fuzzy; Russian ↔ English typing). Same flags as **`telegram-tk name`**.

```bash
python -m telegram_toolkit.find_dm_peer "ivan" --header
python -m telegram_toolkit.find_dm_peer "petrov" --live --min-score 78
```

### `channel_member`

Membership check by user id. Same flags as **`telegram-tk channel-member`**.

```bash
python -m telegram_toolkit.channel_member @YourChannel --id 123456789
echo 123 | python -m telegram_toolkit.channel_member @YourChannel --no-header --ok-if-not-member
```

---

## Security

- Treat **`.env`** and **`*.session`** as secrets; each grants account access.
- **`.gitignore`** excludes **`.venv/`**, **`.env`**, **`*.session`**, and **`.cache/`**.

## License / compliance

Use these tools in line with Telegram’s terms of service and applicable law. Banning users and exporting data affects real people—use least privilege and dry runs where appropriate.
