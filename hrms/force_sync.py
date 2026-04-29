import frappe

def run():
    print("开始强制底账覆盖同步...")
    
    # 查找已经填了项目的凭证行
    je_data = frappe.db.sql("""
        SELECT parent, account, project, debit, credit 
        FROM `tabJournal Entry Account` 
        WHERE project IS NOT NULL AND project != ''
    """, as_dict=True)
    
    sync_count = 0
    
    for row in je_data:
        # 强制更新，不加 (project IS NULL) 限制
        res = frappe.db.sql("""
            UPDATE `tabGL Entry` 
            SET project = %s 
            WHERE voucher_no = %s 
            AND account = %s 
            AND ROUND(debit, 2) = ROUND(%s, 2) 
            AND ROUND(credit, 2) = ROUND(%s, 2)
        """, (row.project, row.parent, row.account, row.debit, row.credit))
        
        if res:
            sync_count += 1
            if sync_count == 1:
                print(f"成功更新第一条: {row.parent} -> {row.project}")

    frappe.db.commit()
    frappe.clear_cache()
    print(f"SUCCESS: 共更新 {sync_count} 条底账。")

if __name__ == "__main__":
    run()
