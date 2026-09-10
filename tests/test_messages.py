from self_orch.doctrine import is_verification_role, maybe_append_falsification, mission_block
from self_orch.rail import build_messages


def test_mission_block_quarantines_role():
    text = mission_block("gather-files", "List the three load-bearing files.")
    assert "Role: gather-files" in text
    assert "Mission brief:" in text
    assert "Do not launch agents" in text


def test_verification_gets_falsification_mandate():
    brief = maybe_append_falsification("verifier", "Check the claim.")
    assert "DISPROVE" in brief
    assert is_verification_role("red_team")
    assert not is_verification_role("worker")


def test_build_messages_always_has_user():
    msgs = build_messages("worker", "Do the thing.", "")
    assert msgs[0]["role"] == "system"
    assert any(m["role"] == "user" for m in msgs)


def test_user_msg_not_duplicated():
    msgs = build_messages("worker", "Do the thing.", "hello", history=[
        {"role": "user", "content": "hello"},
    ])
    users = [m for m in msgs if m["role"] == "user"]
    assert len(users) == 1
