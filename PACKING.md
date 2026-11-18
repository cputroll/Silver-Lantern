# Packing a game

`pack-game.py` excludes launcher executables at the game root, launcher state and temporary files, logs, and user data directories. `hookshot_options.ini` is also excluded by default because it contains user settings. Pass `--include-settings` only when the source settings are intentionally meant to ship to every player.
