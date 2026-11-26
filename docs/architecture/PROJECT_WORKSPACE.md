# Project workspace identity and continuity

A project workspace is keyed by the normalized, case-insensitive project name used by the
conversation, personal-task and structured-memory stores. A registered project adds a goal,
status and milestones; its tasks, decisions and conversations remain in their original stores.

## Rename behavior

Renaming a registered project is one SQLite transaction. It updates the registry key/display name,
milestone ownership and project labels on conversations, personal tasks and structured-memory
records. A rename refuses to proceed when the target name already belongs to a registered
workspace or is already used by labelled material; Mia does not silently merge two workspaces.
The audit event records changed field names and linked-record counts, not project goal text,
milestone titles or task/decision content.

Project names may contain `/`. The API project-name path parameters use a path converter, and the
Projects page URL-encodes the name before requesting the workspace or its milestone routes.
Deep links use the `name` query parameter. Rename and slash-containing names preserve existing
task, decision, conversation and milestone associations.

## Brain project context

The Brain's shared memory-context builder includes the current conversation's own registered
project frame: project name, goal, status and a bounded list of open milestones. It adds nothing
for an unassigned conversation or an unregistered label. It never retrieves another project's
goal or milestones. The project frame is labelled memory data and participates in the existing
memory privacy policy: when retrieved memory is present, routing is restricted to eligible local
models unless the owner explicitly enables remote file-context access.

The existing `tests/test_project_context.py` regression coverage exercises own-project selection,
cross-project isolation, temporary-chat behavior, goal/status/milestone content, prompt data
envelopes and local-only routing. Rename and slash-name regression coverage lives in
`tests/test_project_workspace.py` and `tests/test_projects_workspace_page.py`.

Test execution and real-browser accessibility checks remain distinct from implementation status;
no test pass is implied when the tests have only been authored.
