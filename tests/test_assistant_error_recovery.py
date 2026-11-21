"""MIA-UX-03: truthful, actionable failure and recovery UX in the Assistant page.

Source-contract checks plus a Node harness that runs the page's real ``chatErrorHint``
classifier. The harness covers JS logic only; it is not a real-browser or screen-reader test.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"


def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def block(start_marker: str, end_marker: str) -> str:
    text = source()
    start = text.index(start_marker)
    return text[start: text.index(end_marker, start)]


def test_failure_hint_is_no_longer_the_unconditional_ollama_message():
    flow = block("async function submitPrompt(", "\n$('stopReply').addEventListener")
    assert "$('hint').textContent=chatErrorHint(error);" in flow
    assert "$('hint').textContent='Check that Ollama or an enabled provider is running.';" not in flow


def test_http_status_is_carried_on_errors_from_both_request_paths():
    page = source()
    assert page.count("httpFailure.status=response.status") == 2


def test_failed_reply_offers_a_retry_that_discloses_it_resends_the_message():
    page = source()
    flow = block("async function submitPrompt(", "\n$('stopReply').addEventListener")
    assert "addRetryAction(failedBubble,prompt,{voiceReply});" in flow
    helper = block("function addRetryAction(", "\nlet chatBusy=false;")
    assert "sends this message again" in helper
    assert "if(chatBusy)return;" in helper  # never start a second concurrent turn
    assert "submitPrompt(prompt,options)" in helper
    assert "type" in helper and "button.type='button'" in page


def test_message_log_is_marked_busy_while_a_reply_is_in_flight_and_cleared_after():
    flow = block("async function submitPrompt(", "\n$('stopReply').addEventListener")
    assert "$('messages').setAttribute('aria-busy','true')" in flow
    final = flow[flow.index("}finally{"):]
    assert "$('messages').setAttribute('aria-busy','false')" in final


HARNESS = r"""
const assert = require('assert');
__HINT_SOURCE__
const hint = (e) => chatErrorHint(e);
const err = (message, status, name) => Object.assign(new Error(message), {status, name: name || 'Error'});
const out = {
  network: hint(err('Failed to fetch', 0, 'TypeError')),
  permission_status: hint(err('nope', 403)),
  permission_text: hint(err('Browser fill needs an active owner grant', 409)),
  budget_status: hint(err('daily limit', 429)),
  budget_text: hint(err('Daily spend cap reached', 400)),
  unavailable_status: hint(err('upstream', 503)),
  unavailable_text: hint(err('No healthy provider is available', 500)),
  generic: hint(err('weird', 500)),
  empty: hint(undefined),
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_error_classifier_gives_specific_guidance_and_never_blames_the_wrong_thing(tmp_path):
    fn = block("function chatErrorHint(", "\nfunction addRetryAction(")
    script = tmp_path / "hint.js"
    script.write_text(HARNESS.replace("__HINT_SOURCE__", fn), encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    out = json.loads(run.stdout.strip().splitlines()[-1])

    assert "local server" in out["network"]
    assert "permission" in out["permission_status"] and "permission" in out["permission_text"]
    assert "limit" in out["budget_status"] and "limit" in out["budget_text"]
    assert "provider" in out["unavailable_status"] and "provider" in out["unavailable_text"]
    # A permission/budget/generic failure must not tell the owner to go start Ollama.
    for key in ("permission_status", "permission_text", "budget_status", "budget_text", "generic", "empty"):
        assert "Ollama" not in out[key], key
    assert "retry" in out["generic"].lower()
    assert out["empty"] == out["generic"]  # undefined error degrades to the generic guidance
