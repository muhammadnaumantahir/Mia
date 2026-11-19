import json

import pytest

from mia.brain.broker import ToolCallRecord
from mia.brain.intent import analyze_intent
from mia.brain.verify import (
    OperationLedger,
    evaluate_response,
    recovery_advice,
    validate_structured_output,
    verify_tool_record,
)

A = analyze_intent("explain dns")


def rec(status="completed", output=None, **kw):
    return ToolCallRecord(name="t", status=status, output=output if output is not None else {"ok": True, "items": [1]}, **kw)


def test_ledger_states_are_explicit_and_validated():
    led = OperationLedger()
    op = led.add("list files")
    assert op.status == "planned" and not led.any_executed
    led.set(op, "attempted")
    led.set(op, "completed")
    assert led.any_executed
    led.set(op, "verified")
    assert [o.status for o in led.operations] == ["verified"]
    with pytest.raises(ValueError):
        led.set(op, "done-ish")


def test_verify_tool_record():
    ok, why = verify_tool_record(rec())
    assert ok
    r = rec(output={"ok": True})
    assert verify_tool_record(r)[0] and r.verified
    r = rec(output={})
    assert not verify_tool_record(r)[0] and not r.verified
    assert not verify_tool_record(rec(output={"ok": False, "error": "x"}))[0]
    assert not verify_tool_record(rec(status="failed"))[0]
    pending = rec("approval_required", {"ok": True}, pending_action={"approval_id": "a"})
    ok, why = verify_tool_record(pending)
    assert ok and "not yet executed" in why
    assert not verify_tool_record(rec("approval_required", pending_action={}))[0]
    custom = {"t": lambda r: "custom says no"}
    assert verify_tool_record(rec(), custom) == (False, "custom says no")


def test_structured_output_validation():
    assert validate_structured_output('{"a": 1}', ("a",)) is None
    assert validate_structured_output('```json\n{"a": 1}\n```', ("a",)) is None
    assert "not valid JSON" in validate_structured_output("sure! here it is", ())
    assert "missing required" in validate_structured_output('{"b": 1}', ("a",))
    assert "object" in validate_structured_output("[1]", ("a",))


@pytest.mark.parametrize("text", [
    "I've deleted the old folder for you.",
    "I have successfully sent the email.",
    "Done! I launched the application.",
    "Your file has been created.",
])
def test_unverified_mutation_claims_are_flagged_and_annotated(text):
    r = evaluate_response(text, analysis=A, ledger=OperationLedger())
    assert "unverified_completion_claim" in r.codes()
    assert "no record of actually performing" in r.annotated_text and r.confidence != "high"


def test_mutation_claim_even_with_completed_read_tool_is_still_unverified():
    led = OperationLedger()
    led.set(led.add("list files"), "completed")
    r = evaluate_response("I've deleted the folder.", analysis=A, ledger=led)
    assert "unverified_completion_claim" in r.codes()


def test_mutation_claim_accepted_only_with_approved_execution_record():
    led = OperationLedger()
    op = led.add("write file", kind="approved_execution")
    led.set(op, "verified")
    r = evaluate_response("I've saved the file.", analysis=A, ledger=led)
    assert "unverified_completion_claim" not in r.codes()


def test_inspection_claim_needs_a_completed_tool():
    r = evaluate_response("I inspected the files and all is fine.", analysis=A, ledger=OperationLedger())
    assert "unverified_inspection_claim" in r.codes()
    led = OperationLedger()
    led.set(led.add("list"), "completed")
    assert "unverified_inspection_claim" not in evaluate_response(
        "I inspected the files.", analysis=A, ledger=led).codes()


def test_failed_operation_must_be_reported():
    led = OperationLedger()
    led.set(led.add("read config.yml"), "failed")
    r = evaluate_response("Everything looks great.", analysis=A, ledger=led)
    assert "failure_not_reported" in r.codes() and "read config.yml failed" in r.annotated_text
    ok = evaluate_response("I could not read config.yml.", analysis=A, ledger=led)
    assert "failure_not_reported" not in ok.codes()


def test_empty_invalid_structured_and_citations():
    assert not evaluate_response("  ", analysis=A, ledger=OperationLedger()).ok
    r = evaluate_response("sure thing", analysis=A, ledger=OperationLedger(), structured=True)
    assert "invalid_structured_output" in r.codes() and r.confidence == "low"
    r = evaluate_response("We chose Postgres [M3].", analysis=A, ledger=OperationLedger(),
                          memory_reference_count=2)
    assert "unsupported_memory_citation" in r.codes()
    assert "unsupported_memory_citation" not in evaluate_response(
        "We chose Postgres [M2].", analysis=A, ledger=OperationLedger(), memory_reference_count=2).codes()


def test_ambiguity_not_clarified_and_low_confidence():
    amb = analyze_intent("fix it")
    assert "missing_information" in evaluate_response("Fixed.", analysis=amb, ledger=OperationLedger()).codes()
    assert "missing_information" not in evaluate_response("Which file?", analysis=amb, ledger=OperationLedger()).codes()
    r = evaluate_response("I think maybe, probably, I'm not sure it might work.", analysis=A,
                          ledger=OperationLedger())
    assert "low_confidence" in r.codes()


def test_clean_response_is_high_confidence_and_report_serialises():
    r = evaluate_response("DNS maps names to addresses.", analysis=A, ledger=OperationLedger())
    assert r.ok and r.confidence == "high" and not r.issues
    json.dumps(r.to_dict())


def test_recovery_advice_by_class():
    assert "Try again" in recovery_advice("transient", "list files")
    assert "permission" in recovery_advice("permission", "read x")
    assert "approval" in recovery_advice("needs_approval", "launch app")
    assert "FileNotFound" in recovery_advice("permanent", "read x", "FileNotFound: /a")
    assert "could not complete" in recovery_advice("unknown-class", "x")
