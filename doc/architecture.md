# Telegram Toolkit Architecture

This document summarizes the organization, data structures, and core logic of the Telegram Toolkit.

## Project Organization

The toolkit is organized as a Python package (`telegram_toolkit`) with a main entry point script (`telegram-tk`).

- `telegram-tk`: Launcher script that sets up the environment and runs the toolkit.
- `telegram_toolkit/`:
  - `__main__.py`: Entry point for `python -m telegram_toolkit`.
  - `cli.py`: Command-line interface definition (using `argparse`).
  - `resolver.py`: **(New)** Shared logic for resolving channel/group identifiers into Telegram entities. Handles fuzzy searching and user prompting for ambiguous matches.
  - `client.py`: Telethon client factory and configuration.
  - `dm_cache.py`: Core synchronization logic for private messages and metadata.
  - `find_dm_peer.py`: Logic for searching users and channels by name, username, or ID.
  - `list_users.py`: Logic for listing and caching channel/megagroup members.

## Data Schema (SQLite)

The toolkit uses a local SQLite database (stored in `.cache/`) to store messages and metadata.

### `messages`
Stores 1:1 private message history.
- `peer_user_id`: Telegram ID of the user.
- `message_id`: Unique ID of the message.
- `date_utc`: ISO timestamp.
- `from_me`: 1 if sent by you, 0 if received.
- `text`: Message body.

### `chats`
Metadata for users, bots, channels, and groups encountered in the dialog list.
- `peer_kind`: 'user', 'bot', 'channel', 'supergroup', or 'group'.
- `peer_id`: Telegram ID.
- `title`: Display name or channel title.
- `username`: Telegram @username (without the '@').

### `channel_member_snapshots`
Cached member lists for channels/groups (populated via the `list` command).
- `channel_id`: Marked ID of the channel.
- `user_id`: Telegram ID of the member.
- `username`, `first_name`, `last_name`: Member identity.
- `joined_utc`: When the member joined (if available).

### `deleted_peers`
Tracks IDs of accounts that have been deleted to exclude them from results.

## Key Workflows

### Entity Resolution
The `resolver.py` module provides a unified way to turn a user-provided string (like `@username`, a numeric ID, or a title fragment) into a Telegram entity. It:
1. Checks the local database for an exact ID match.
2. Performs a fuzzy search on the `chats` table if no ID is found.
3. If ambiguous, prompts the user to select the correct peer.
4. Falls back to Telegram's `get_entity` if the database lookup fails.

### Synchronization (`rescan`)
Walks the Telegram dialog list, updates metadata in the `chats` table, and syncs new 1:1 messages into the `messages` table for recent conversations.

## Path Resolution

To support running the toolkit from any working directory, paths are resolved as follows:

- **Code & Environment**: The toolkit uses `REPO_ROOT` (derived from the package location) to locate `.env` and the Python virtual environment.
- **Data & Sessions**:
  - **Database**: Defaults to `${REPO_ROOT}/.cache/private_dm_messages.sqlite`.
  - **Sessions**: Default to `${REPO_ROOT}/telegram_session.session`. This ensures that authentication state is shared across all call locations.
- **User Input/Output**: Relative paths provided as command-line arguments (e.g., `--output members.csv` or `--file ids.txt`) are resolved against the **Current Working Directory**, allowing for natural CLI usage.
