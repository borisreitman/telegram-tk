# Telegram Toolkit by Boris Reitman

This tool is designed for people who run their own channels on Telegram, and have a lot of users that they need to manage, paricularly cold outreach to many people in DMs.

## Setup


### Install dependencies
1. Create a venv at `.venv/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### API credentials

Copy `.env.example` to `.env` and set values from [my.telegram.org/apps](https://my.telegram.org/apps):

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- Optional: `TELEGRAM_SESSION` — session file basename (default `telegram_session` → `telegram_session.session` in the current working directory).


### Copy executable

Your can run either from checkout folder with `./telegram-tk` or by script copied somewhere to your PATH, so that you can run from anywhere. If you want to do the latter, then:

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

### Authentication

```bash
telegram-tk auth
```

### Initial scan

Run full scan to initialize local database stored in `.cache` folder. It's an SQLite database, which has metadata and also fetched 1-on-1 messages. (Channel messages are not fetched).

```bash
telegram-tk full-scan
```
### Usage Synopsis

```bash
telegram-tk auth
telegram-tk search titanic
telegram-tk rescan --recent-peer-limit 50 --notrace
telegram-tk full-rescan
telegram-tk show 15840524
telegram-tk name aleks
telegram-tk name aleks --channel @foobar
telegram-tk list @YourChannel
telegram-tk list "Some Channel Name"
telegram-tk list @YourChannel --output foo.csv
```

### Finding people by name

Example:
```
telegram-tk name Aleks
```

The `telegram-tk name` will search for a matching name, and will give you Telegram internal user ids.
It will do a similarity search, so you search for "aleks" it will find "Alex" and "Alexandra" as well. Convenient when you don't remember how someone spelled his name. If channel is provided, it will limit to members of that channel only.

Once you have user id of the person or channel, you can plug it into other commands like `list`. But, other commands too will 

If you want to limit name search to member of a channel you have admin rights to, you can do:

```
telegram-tk name Aleks --channel <channel>
```

Any command that accepts channel, can take the internal telegram ID of the channel, it's @foo "username" if it has one, or partial name. If more than one matches, you will be prompted to select one of channels.

### Finding people by text

Sometimes you can't remember the name of the person you discussed something with. So, you can search the 1-on-1 chat text, to find him. In the following example, you will find all peolpe with whom you discussed patents.

```
telegram-tk search patent
```

### Listing users in channels

This is implemented only for channels that you have admin rights to.

```
telegram-tk list <channel> --output members.csv
```

If you don't specify the output target, it will be output to console.



## Security

- Treat **`.env`** and **`*.session`** as secrets; each grants account access.
- **`.gitignore`** excludes **`.venv/`**, **`.env`**, **`*.session`**, and **`.cache/`**.

## License / compliance

MIT license: https://spdx.org/licenses/MIT.html
