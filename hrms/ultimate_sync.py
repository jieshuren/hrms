import frappe

def run():
    print("开始终极底账同步...")
    
    # 1. 找出所有凭证中包含项目的行
    je_data = frappe.db.sql("""
        SELECT parent, account, project, debit, credit 
        FROM `tabJournal Entry Account` 
        WHERE project IS NOT NULL AND project != ''
    """, as_dict=True)
    
    sync_count = 0
    fail_count = 0
    
    for row in je_data:
        # 尝试通过 凭证号 + 科目 + 金额 进行更新
        # 使用 ROUND 处理浮点数差异
        res = frappe.db.sql("""
            UPDATE `tabGL Entry` 
            SET project = %s 
            WHERE voucher_no = %s 
            AND account = %s 
            AND ROUND(debit, 2) = ROUND(%s, 2) 
            AND ROUND(credit, 2) = ROUND(%s, 2)
            AND (project IS NULL OR project = '')
        """, (row.project, row.parent, row.account, row.debit, row.credit))
        
        if res:
            sync_count += 1
        else:
            # 如果还是失败，尝试不看科目名（有时候科目名在导入时有微小差异）
            res2 = frappe.db.sql("""
                UPDATE `tabGL Entry` 
                SET project = %s 
                WHERE voucher_no = %s 
                AND ROUND(debit, 2) = ROUND(%s, 2) 
                AND ROUND(credit, 2) = ROUND(%s, 2)
                AND (project IS NULL OR project = '')
            """, (row.project, row.parent, row.debit, row.credit))
            if res2:
                sync_count += 1
            else:
                fail_count += 1

        if sync_count % 100 == 0:
            frappe.db.commit()

    frappe.db.commit()
    frappe.clear_cache()
    print(f"SUCCESS: 强制同步完成。")
    print(f"成功更新底账行数: {sync_count}")
    print(f"未能匹配的行数: {fail_count}")

if __name__ == "__main__":
    run()
