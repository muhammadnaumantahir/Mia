"""MIA-UX-02: owner-visible, opt-in project control in the Assistant page.

Contract checks plus a Node harness running the page's real ``refreshProjectControl`` and
``saveProject`` against a fake DOM/fetch. JS logic only; not a real-browser test.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"


def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def region(start: str, end: str) -> str:
    text = source()
    i = text.index(start)
    return text[i: text.index(end, i)]


def test_project_markup_is_labelled_keyboard_reachable_and_starts_disabled():
    page = source()
    assert '<label for="projectName"' in page
    for element_id in ("projectName", "projectSave", "projectClear"):
        at = page.index(f'id="{element_id}"')
        tag = page[page.rindex("<", 0, at): page.index(">", at) + 1]
        assert "disabled" in tag, element_id  # nothing is editable until a conversation exists
    assert 'maxlength="64"' in page  # mirrors MAX_PROJECT_NAME_CHARS in the memory store
    assert 'id="projectNote" class="sub" role="status" aria-live="polite"' in page


def test_project_text_states_the_real_recall_behaviour_and_nothing_more():
    page = source()
    assert "never recalls other projects' conversations here" in page
    assert "newest other saved conversation, from any project" in page


def test_control_is_wired_into_startup_replies_and_conversation_list():
    page = source()
    assert "setupTemporaryToggle();setupProjectControl();" in page
    assert "loadConversationList();refreshProjectControl();" in page
    assert "item.project" in region("async function loadConversationList(", "async function loadMemoryRecords(")
    assert "/project'" in page and "JSON.stringify({project:name})" in page


HARNESS = r"""
const assert = require('assert');
const els = {};
const mk = (id) => (els[id] = {id, value: '', textContent: '', disabled: false, style: {}});
['projectName','projectSave','projectClear','projectNote'].forEach(mk);
const $ = (id) => els[id];
let conversationId = null;
let listReloads = 0;
function loadConversationList() { listReloads += 1; }
let calls = [];
let responder = null;
global.fetch = async (url, opts) => { calls.push({url, opts}); return responder(url, opts); };
const reply = (status, body) => ({ok: status >= 200 && status < 300, status, json: async () => body});
__SOURCE__
(async () => {
  const out = {};
  // 1. No conversation yet: everything disabled, nothing fetched.
  await refreshProjectControl();
  out.no_conversation = {name: els.projectName.disabled, save: els.projectSave.disabled,
                         clear: els.projectClear.disabled, fetched: calls.length, note: els.projectNote.textContent};

  // 2. Conversation without a project: enabled, Clear stays disabled, honest note.
  conversationId = 'c1';
  responder = () => reply(200, {project: null});
  await refreshProjectControl();
  out.unassigned = {name: els.projectName.disabled, clear: els.projectClear.disabled, note: els.projectNote.textContent};

  // 3. Existing project is shown and can be cleared.
  responder = () => reply(200, {project: 'Atlas'});
  await refreshProjectControl();
  out.assigned = {value: els.projectName.value, clear: els.projectClear.disabled, note: els.projectNote.textContent};

  // 4. Saving posts the project to the right endpoint and reloads the list.
  calls = []; responder = (u, o) => reply(200, {conversation_id: 'c1', project: JSON.parse(o.body).project});
  await saveProject('Atlas migration');
  out.saved = {url: calls[0].url, method: calls[0].opts.method, body: JSON.parse(calls[0].opts.body),
               value: els.projectName.value, reloads: listReloads};

  // 5. Clearing sends null and updates the note.
  await saveProject(null);
  out.cleared = {body: JSON.parse(calls[1].opts.body), clear: els.projectClear.disabled, note: els.projectNote.textContent};

  // 6. Server validation errors are shown, not swallowed, and not styled as success.
  responder = () => reply(400, {detail: 'project name must be at most 64 characters'});
  await saveProject('x'.repeat(70));
  out.error = {note: els.projectNote.textContent, colour: els.projectNote.style.color};

  // 7. Read failure is reported.
  responder = () => reply(500, {});
  await refreshProjectControl();
  out.read_failure = els.projectNote.textContent;

  // 8. saveProject without a conversation does nothing.
  conversationId = null; calls = [];
  await saveProject('Atlas');
  out.no_conv_save_calls = calls.length;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_real_project_control_functions_behave_truthfully(tmp_path):
    code = region('const PROJECT_NONE=', "\nasync function loadCurrentConversation(){").replace(
        "function setupProjectControl(){", "function _unused_setupProjectControl(){")
    script = tmp_path / "project.js"
    script.write_text(HARNESS.replace("__SOURCE__", code), encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    out = json.loads(run.stdout.strip().splitlines()[-1])

    assert out["no_conversation"]["name"] and out["no_conversation"]["save"]
    assert out["no_conversation"]["fetched"] == 0 and "first message" in out["no_conversation"]["note"]
    assert not out["unassigned"]["name"] and out["unassigned"]["clear"]
    assert out["unassigned"]["note"].startswith("No project")
    assert out["assigned"]["value"] == "Atlas" and not out["assigned"]["clear"]
    assert "Atlas" in out["assigned"]["note"]
    assert out["saved"]["url"] == "/api/conversations/c1/project"
    assert out["saved"]["method"] == "POST" and out["saved"]["body"] == {"project": "Atlas migration"}
    assert out["saved"]["value"] == "Atlas migration" and out["saved"]["reloads"] == 1
    assert out["cleared"]["body"] == {"project": None} and out["cleared"]["clear"]
    assert out["cleared"]["note"].startswith("No project")
    assert "at most 64" in out["error"]["note"] and out["error"]["colour"]
    assert "Could not read" in out["read_failure"]
    assert out["no_conv_save_calls"] == 0
