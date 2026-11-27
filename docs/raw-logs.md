# Raw experiment logs

Only tables and reports (`docs/results/`) are committed to Git. Raw logs from `runs/`—`results.csv`, episode folders (`calls.jsonl`, `effects.jsonl`, `model_calls.jsonl`, `trace.json`, `bank.sqlite`, etc.)—are stored in the shared **`handoff-bench-runs`** folder on Google Drive. Anyone can use them to verify tables and inspect episodes (`scripts/show.py`).

## One-time setup: connect the shared folder

1. Ask Alina to grant you **Editor** access to `handoff-bench-runs`.
2. Install [rclone](https://rclone.org):

   ```bash
   brew install rclone
   ```

3. Connect your Google account and point the remote directly at the shared folder:

   ```bash
   rclone config create gdrive drive scope=drive root_folder_id=1d33p8WVDL3DxKhBYWWvBuVvGcStwSZH2
   ```

   A browser will open. Sign in and authorize access. `gdrive:` now refers to the shared folder’s root.

4. Verify the connection:

   ```bash
   rclone lsd gdrive:
   ```

The rclone token is stored in `~/.config/rclone/rclone.conf` and grants access to your Drive. Do not commit or share it. Each person should connect their own account.

## Upload a run

Upload each run as a single archive named `<run_id>.tar.gz`. Google Drive accepts about 30 files per minute, while a run contains thousands (about 13 per episode): uploading a folder of 150 episodes can take an hour; an archive takes seconds.

From the repository root:

```bash
tar -czf /tmp/prefreeze-haiku-retell.tar.gz -C runs prefreeze-haiku-retell
```

```bash
rclone copy /tmp/prefreeze-haiku-retell.tar.gz gdrive: --progress
```

To upload several runs:

```bash
for r in runs/prefreeze-*-retell/; do n=$(basename "$r"); tar -czf "/tmp/$n.tar.gz" -C runs "$n" && rclone copy "/tmp/$n.tar.gz" gdrive: --progress; done
```

Verify that the archive arrived intact by comparing checksums:

```bash
rclone check /tmp/prefreeze-haiku-retell.tar.gz gdrive: --one-way --include prefreeze-haiku-retell.tar.gz
```

`rclone copy` does not delete files on Drive (unlike `rclone sync`, which we do not use). If an archive with the same name already exists and differs, rclone replaces it with the new one.

## Download a run

```bash
rclone copy gdrive:prefreeze-haiku-retell.tar.gz /tmp/ --progress
```

```bash
tar -xzf /tmp/prefreeze-haiku-retell.tar.gz -C runs
```

## Rules

- **Upload before merging a PR with results.** The results README lists each `run_id` in the Runs table; use it to find the archive.
- **Do not rename archives on Drive.** If a run is recalculated or renumbered with a script from `scripts/` (for example, `renumber_hedged.py`), upload the new archive under the same name and note the change in the results README.
- **Broken runs** (key limit, provider failure) go in `gdrive:_broken/`, not alongside valid runs.
- Runs from branches that will not go into `main` (for example, `main-for-harness`) also go on Drive under their own `run_id`.

If rclone warns that its shared Google `client_id` will stop working: it currently works; when it stops, create your own `client_id` ([rclone instructions](https://rclone.org/drive/#making-your-own-client-id)) and run `rclone config` again.
