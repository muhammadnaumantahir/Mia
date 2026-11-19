from pathlib import Path

ASSISTANT_PAGE = (
    Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"
)


def assistant_source() -> str:
    return ASSISTANT_PAGE.read_text(encoding="utf-8")


def test_hands_free_voice_is_explicitly_user_started():
    source = assistant_source()
    assert 'id="voiceMode" type="button" aria-pressed="false">Start voice' in source
    assert 'let voiceMode=false;' in source
    assert "recognition.start();" in source
    assert "recognition.continuous=true;" in source
    assert "recognition.interimResults=true;" in source


def test_voice_turns_auto_submit_speak_and_resume_listening():
    source = assistant_source()
    start = source.index("function finishVoiceUtterance(){")
    end = source.index("\nfunction startVoiceRecognition(){", start)
    finish = source[start:end]
    assert "await submitPrompt(prompt,{voiceReply:true});" in finish
    assert "await speakReply" in finish  # voice stop command has spoken confirmation
    assert "queueVoiceRestart(650);" in finish

    submit_start = source.index("async function submitPrompt(")
    submit_end = source.index("\n$('composer').addEventListener", submit_start)
    submit = source[submit_start:submit_end]
    assert "await speakReply(result.content,{force:voiceReply});" in submit or (
        "if(!voiceReply||voiceMode)await speakReply(result.content,{force:voiceReply});"
        in submit
    )


def test_voice_has_stop_commands_and_browser_privacy_disclosure():
    source = assistant_source()
    for phrase in ("stop listening", "stop voice", "goodbye mia", "sleep mia"):
        assert phrase in source
    assert "may process microphone audio outside Mia" in source
    assert "Local record" in source
    assert "voicePlaybackCancelled=true;" in source
    assert "id=\"previewVoice\"" in source
    assert "Playing a short voice preview" in source


def test_memory_references_remain_visible_in_voice_chat_modes():
    source = assistant_source()
    assert 'id="memoryRecords"' in source
    assert "renderMemoryReferences(assistantBubble,result.context_references||[])" in source
    assert "renderMemoryReferences(bubble,streamed.done.context_references||[])" in source
