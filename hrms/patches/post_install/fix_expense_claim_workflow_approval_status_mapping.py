"""
修复 Expense Claim 工作流中 approval_status 的自动映射配置。

问题:
- 工作流状态(如 Dept Pending, Finance Approved)与 approval_status 字段(Draft/Approved/Rejected)不一致
- 历史补丁曾尝试将 workflow_state 的值直接赋给 approval_status,导致值域不匹配

修复:
- 根据工作流状态的业务含义,正确映射到对应的 approval_status 值
- Pending/中间状态 → Draft
- 通过类状态 → Approved
- 拒绝类状态 → Rejected
"""

import frappe
from frappe.model.workflow import get_workflow_name


# 状态映射规则: workflow_state → approval_status
APPROVAL_STATUS_MAP = {
    # 待审批/中间状态 → Draft
    "pending": "Draft",
    "dept pending": "Draft",
    "finance pending": "Draft",
    "waiting for approval": "Draft",
    # 通过类状态 → Approved
    "approved": "Approved",
    "dept approved": "Approved",
    "finance approved": "Approved",
    "final approved": "Approved",
    # 拒绝类状态 → Rejected
    "rejected": "Rejected",
    "dept rejected": "Rejected",
    "finance rejected": "Rejected",
}


def get_approval_status_for_state(state_name: str) -> str:
    """根据工作流状态名称推断对应的 approval_status 值"""
    state_lower = state_name.lower().strip()

    # 精确匹配
    if state_lower in APPROVAL_STATUS_MAP:
        return APPROVAL_STATUS_MAP[state_lower]

    # 模糊匹配: 包含关键词
    for keyword, status in APPROVAL_STATUS_MAP.items():
        if keyword in state_lower:
            return status

    # 默认: 根据状态名称关键词推断
    if "reject" in state_lower:
        return "Rejected"
    if "approv" in state_lower or "complete" in state_lower or "final" in state_lower:
        return "Approved"

    # 无法推断时默认为 Draft
    return "Draft"


def execute():
    """执行修复"""
    doctype = "Expense Claim"
    workflow_name = get_workflow_name(doctype)

    if not workflow_name:
        print(f"[SKIP] 未找到 {doctype} 的活动工作流")
        return

    workflow = frappe.get_doc("Workflow", workflow_name)
    print(f"[INFO] 找到工作流: {workflow_name}")

    # 获取所有 workflow states
    workflow_states = frappe.get_all(
        "Workflow Document State",
        filters={"parent": workflow_name},
        fields=["name", "state", "update_field", "update_value", "doc_status"],
    )

    if not workflow_states:
        print(f"[SKIP] 工作流 {workflow_name} 没有配置状态")
        return

    updated_count = 0
    for state in workflow_states:
        state_name = state.state
        expected_update_field = "approval_status"
        expected_update_value = get_approval_status_for_state(state_name)

        # 检查是否需要更新
        needs_update = (
            state.update_field != expected_update_field
            or state.update_value != expected_update_value
        )

        if needs_update:
            frappe.set_value(
                "Workflow Document State",
                state.name,
                "update_field",
                expected_update_field,
            )
            frappe.set_value(
                "Workflow Document State",
                state.name,
                "update_value",
                expected_update_value,
            )
            updated_count += 1
            print(
                f"[UPDATE] 状态 '{state_name}': "
                f"update_field='{expected_update_field}', "
                f"update_value='{expected_update_value}'"
            )
        else:
            print(
                f"[OK] 状态 '{state_name}': update_field='{state.update_field}', "
                f"update_value='{state.update_value}' (已是正确的值)"
            )

    frappe.db.commit()
    print(f"\n[COMPLETE] 共更新 {updated_count}/{len(workflow_states)} 个工作流状态")
    print("[INFO] 修复完成! 工作流状态现在会自动同步到 approval_status 字段")
