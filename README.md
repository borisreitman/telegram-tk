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

- Copy `doc/telegram-tk.sh` to `~/bin/telegram-tk` or somewhere in your search PATH, 
- set **`TELEGRAM_TK_REPO`** to your clone path (or edit the default inside the script), 
- `chmod +x` the copied script


### Running

You can get help on any command like this:

```bash
telegram-tk help
telegram-tk help <subcommand>
telegram-tk help search
telegram-tk help name
telegram-tk help list
```

First you must authenticate with `telegram-tk auth`.  Possible commands:

```bash
telegram-tk auth
telegram-tk search titanic
telegram-tk rescan --recent-peer-limit 50 --notrace
telegram-tk full-rescan
telegram-tk show 15840524
telegram-tk name aleks
telegram-tk list @YourChannel
telegram-tk list "Some Channel Name"
telegram-tk list @YourChannel --output foo.csv
```

## Security

- Treat **`.env`** and **`*.session`** as secrets; each grants account access.
- **`.gitignore`** excludes **`.venv/`**, **`.env`**, **`*.session`**, and **`.cache/`**.

## License / compliance

MIT license: https://spdx.org/licenses/MIT.html
