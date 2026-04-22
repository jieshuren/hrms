"""
同步已有报销单的 approval_status 字段,使其与 workflow_state 保持一致。

场景:
- 工作流修复前创建的报销单,其 approval_status 可能没有正确更新
- 需要将所有存量单据的 approval_status 重新计算
"""

import frappe
from frappe.model.workflow import get_workflow_name


# 工作流状态 → approval_status 映射
STATE_TO_APPROVAL_STATUS = {
    "pending": "Draft",
    "dept pending": "Draft",
    "finance pending": "Draft",
    "waiting for approval": "Draft",
    "draft": "Draft",
    "approved": "Approved",
    "dept approved": "Approved",
    "finance approved": "Approved",
    "final approved": "Approved",
    "rejected": "Rejected",
    "dept rejected": "Rejected",
    "finance rejected": "Rejected",
}


def get_approval_status(workflow_state: str) -> str:
    """根据 workflow_state 推断 approval_status"""
    if not workflow_state:
        return "Draft"

    state_lower = workflow_state.lower().strip()

    # 精确匹配
    if state_lower in STATE_TO_APPROVAL_STATUS:
        return STATE_TO_APPROVAL_STATUS[state_lower]

    # 模糊匹配
    for keyword, status in STATE_TO_APPROVAL_STATUS.items():
        if keyword in state_lower:
            return status

    # 默认
    if "reject" in state_lower:
        return "Rejected"
    if "approv" in state_lower or "complete" in state_lower or "final" in state_lower:
        return "Approved"
    return "Draft"


def sync():
    """执行同步"""
    workflow_name = get_workflow_name("Expense Claim")
    if not workflow_name:
        print("[SKIP] 未找到 Expense Claim 的活动工作流")
        return

    claims = frappe.get_all(
        "Expense Claim",
        filters={"docstatus": ("!=", 2)},
        fields=["name", "workflow_state", "approval_status", "docstatus"]
    )

    if not claims:
        print("[INFO] 没有需要同步的报销单")
        return

    updated_count = 0
    for claim in claims:
        expected_status = get_approval_status(claim.workflow_state)

        if claim.approval_status != expected_status:
            frappe.db.set_value(
                "Expense Claim",
                claim.name,
                "approval_status",
                expected_status,
                update_modified=False
            )
            updated_count += 1
            print(
                f"[UPDATE] {claim.name}: "
                f"workflow_state='{claim.workflow_state}', "
                f"approval_status '{claim.approval_status}' → '{expected_status}'"
            )
        else:
            print(
                f"[OK] {claim.name}: "
                f"workflow_state='{claim.workflow_state}', "
                f"approval_status='{claim.approval_status}' (已同步)"
            )

    frappe.db.commit()
    print(f"\n[COMPLETE] 共更新 {updated_count}/{len(claims)} 张报销单")


if __name__ == "__main__":
    sync()
