# Encrypted backup and restore (MIA-LIFE-11)

Owner-run, local, offline. No cloud target, no UI, no scheduler and no network access.

```text
pip install "mia[backup]"                       # adds the cryptography package
mia-backup create  --out backup.miabak          # data folder from MIA_DATA_DIR (or --data-dir)
mia-backup verify  backup.miabak
mia-backup preview backup.miabak --to ./restore-here
mia-backup restore backup.miabak --to ./restore-here [--policy abort|overwrite|side_by_side]
```

The passphrase comes from `MIA_BACKUP_PASSPHRASE` or a hidden prompt (entered twice on create). It is
deliberately not a command-line option, so it stays out of shell history and process lists.

## What is in a backup

Top-level `mia-*.sqlite3` stores and `computer-tasks.sqlite3`, each taken with SQLite's online backup API
(a consistent snapshot, including committed data still in a WAL file, while Mia is running) and checked
with `PRAGMA integrity_check`. A store that fails its check aborts the backup and nothing is written.

**Not in a backup, on purpose**

- Provider API keys: they live in the OS credential store (`mia-credentials`), never in the data folder. After
  restoring on a new machine, enter them again.
- `computer-permissions.sqlite3` and `computer-approvals.sqlite3`: restoring an old copy could silently
  re-grant permissions the owner has since revoked. Re-approve on the new installation.
- Downloads, screenshots, model files and anything else that is not one of the stores above.

## Key management

- The key is derived from the owner passphrase with scrypt (N=2^15, r=8, p=1, a fresh 16-byte random salt per
  backup). The archive stores only the salt and these parameters. Mia never stores, logs, prints or transmits
  the passphrase or the key, and error messages never contain them.
- **There is no recovery path for a lost passphrase.** Without it the backup cannot be opened by anyone,
  including Mia and its developers. Minimum 12 characters; use a long random passphrase and keep it in a
  password manager, separate from the backup file.
- A different key is derived per backup (random salt) and a fresh random nonce prefix is used, so backups of
  the same data are different files.

## Format and integrity

`header | AES-256-GCM chunks`. The header is `MIABAK01`, the scrypt parameters, salt, nonce prefix and chunk
size. Each 64 KiB chunk is authenticated together with the header, its index and a final-chunk flag, so a
flipped byte, truncation (even at a chunk boundary), appended data, dropped, duplicated or reordered chunks
and a swapped header all fail authentication. The decrypted payload is a gzip tar holding `manifest.json`
(names, sizes, SHA-256, creation time, exclusions) and the database snapshots. After decryption the checks
continue: only allowlisted store names, regular files only, each file's size and SHA-256 must match the
manifest exactly, no unlisted or duplicate members, no excluded stores, bounded sizes and counts, and every
database must pass `integrity_check`. Header values from an untrusted file are bounded before they drive
any work or memory.

## Preview, conflict policy and recovery

`preview` verifies the whole archive and prints what a restore would do per file (`new`, `identical`,
`differs`) without writing anything or creating the target folder.

| Policy | Existing file with different content |
| --- | --- |
| `abort` (default) | Stop before changing anything (exit code 2 on preview). |
| `overwrite` | Move the existing file and its `-wal`/`-shm`/`-journal` files into `restore-undo-<UTC time>/` inside the target, then place the restored file. Stale WAL files are never left next to a restored database. |
| `side_by_side` | Keep the existing file; write `<name>.restored-<UTC time>.sqlite3` next to it. A copy never matches a live store name, so Mia will not open it by accident. |

`mia-usage.sqlite3` (the spend ledger) is **never overwritten**, even with `overwrite`: an older ledger would
lower recorded spend and weaken budget limits. It is always restored side by side, and the preview says so.

Restore verifies the entire archive first, then places files one at a time through a temporary file and an
atomic rename. If anything fails part-way, files already placed are removed and files moved aside are put
back, leaving the target as it was. The archive itself is written to a `.partial` file, flushed and renamed,
so an interrupted backup never leaves something that looks valid. Plaintext staging happens only in a private
(owner-only) temporary folder that is removed on success and on failure.

**Restoring into a data folder that a running Mia has open is not safe.** Stop Mia first, or restore to a
new folder and point `MIA_DATA_DIR` at it. "Identical" means byte-identical: a live database is usually
re-packed by the snapshot, so it will normally show as `differs`.

## Limits

Local file only: no remote or cloud destination, no scheduled or automatic backups, no incremental or
deduplicated backups, no key rotation or multiple recipients, no UI, and no cross-version schema migration (a
restore returns the stores exactly as they were; Mia opens them as it does any existing store). Tested with
real SQLite files and real encryption; not tested with very large databases or on Windows file locking.
