from __future__ import annotations

from mini_nanobot.tools.goal import create_goal, update_goal


def test_create_goal_requires_explicit_authorization() -> None:
    command = create_goal.func("持续整理项目", "call-1", {})

    assert "goal_state" not in command.update
    assert "拒绝创建长期目标" in command.update["messages"][0].content


def test_create_goal_records_authorized_objective() -> None:
    command = create_goal.func(
        "持续整理项目",
        "call-2",
        {"goal_creation_allowed": True},
    )

    assert command.update["goal_state"]["status"] == "active"
    assert command.update["goal_state"]["objective"] == "持续整理项目"


def test_update_goal_complete() -> None:
    command = update_goal.func(
        "complete",
        "已全部整理完毕",
        "call-3",
        {"goal_state": {"status": "active", "objective": "整理"}},
    )
    assert command.update["goal_state"]["status"] == "completed"
    assert command.update["goal_state"]["detail"] == "已全部整理完毕"


def test_replace_without_active_goal_is_rejected() -> None:
    """安全规则：没有进行中的目标时，replace 不能变相新建目标（绕过 /goal 授权）。"""
    command = update_goal.func("replace", "新目标", "call-4", {})

    assert "goal_state" not in command.update
    assert "拒绝替换目标" in command.update["messages"][0].content


def test_replace_with_active_goal_keeps_active() -> None:
    command = update_goal.func(
        "replace",
        "换一个新方向",
        "call-5",
        {"goal_state": {"status": "active", "objective": "旧目标"}},
    )
    assert command.update["goal_state"]["status"] == "active"
    assert command.update["goal_state"]["objective"] == "换一个新方向"
