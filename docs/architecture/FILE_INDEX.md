# File intelligence workspace

**Route:** `/files`  
**Implementation:** `src/mia/knowledge/file_index.html`, `src/mia/knowledge/file_index.py`  
**Task:** MIA-LIFE-06 and its UI/Brain/refresh child slices.

## Owner workflow

1. The owner chooses one of the configured filesystem roots and browses to a folder.
2. Pressing **Add selected folder** records the explicit selection. It does not read or index the folder.
3. Pressing **Index now** reads only that selected folder and allowed descendants through the existing filesystem adapter.
4. Search is local FTS5 keyword search. Results show the root, relative path, inclusive line range, a short snippet, staleness state and an untrusted-content label.
5. **Verify against live file** re-reads the source and checks the indexed text and file hash. A stale/changed file must not be presented as current verified evidence.
6. The owner may remove one file from the index, remove a full folder selection, or purge the entire index. These actions never delete the original files.

The directory browser exposes folders only. The server still validates every path and refuses traversal, symlinks and disallowed folders; the UI is not a security boundary.

## Refresh lifecycle

Automatic refresh is off by default. The owner explicitly starts it and chooses a bounded interval (5–3,600 seconds) and maximum folder batch (1–5). Status exposes whether the worker is running, how many folders were refreshed, last run and last error. The owner can stop it at any time. Emergency Stop / Stop All remains enforced by the existing service.

Purging the entire index first stops the refresh worker, then removes the selected folders and stored chunks. Removing a single file only removes its current indexed snapshot; a future refresh can index it again while its containing folder remains selected. Removing the folder selection is the durable way to stop indexing that folder.

## Trust, privacy and limitations

File content is untrusted data. The index records instruction-like spans and search hits remain marked untrusted. The page uses text-only DOM updates for names, paths, snippets and errors; it does not render indexed HTML or execute content. The browser UI does not upload file contents to a remote service.

The UI search is lexical/keyword-only. Mia's Brain has a separate local-only file-search tool that re-verifies chunks and gives exact path/line/hash citations to the Gateway for a source-grounded answer. Remote model routing is withheld when private indexed file context is present unless the owner explicitly enables the existing remote file-context setting.

Credential-like files, private-key content, excluded directories, symlinks and oversized/unreadable content are skipped. Deletion uses secure-delete, FTS rebuild and compaction. Search snippets can still reveal content from folders the owner selected, so the owner should select only folders they are comfortable making searchable by Mia.

## Regression coverage

- `tests/test_file_index.py`: selection/path security, index/search/verify, deletion, stale files, injection flags, stop-all, watcher and API lifecycle.
- `tests/test_file_index_page.py`: real FastAPI + Chromium page flow, explicit folder selection, indexing/search/verification, injection warning, traversal refusal, deletion without source-file changes, refresh start/stop, purge stopping refresh, navigation and narrow-screen reflow.

Tests were authored for the completed UI and purge lifecycle but were not executed in this session. Screen-reader and non-Chromium acceptance remains `OWNER_VERIFICATION_REQUIRED`.
