import frappe

def run():
    # 1. 强制将凭证行中的项目同步到底账
    # 逻辑：只要凭证行有项目，而对应的底账没有项目，就同步。
    # 匹配条件：凭证号 + 科目 + 借方金额 + 贷方金额
    
    print("开始强制底账同步...")
    
    # 查找所有已经在凭证行填了项目的数据
    je_data = frappe.db.sql("""
        SELECT name, parent, account, project, debit, credit 
        FROM `tabJournal Entry Account` 
        WHERE project IS NOT NULL AND project != ''
    """, as_dict=True)
    
    print(f"检测到已修复的凭证行: {len(je_data)} 条")
    
    sync_count = 0
    for row in je_data:
        # 执行强制底账更新
        res = frappe.db.sql("""
            UPDATE `tabGL Entry` 
            SET project = %s 
            WHERE voucher_no = %s 
            AND account = %s 
            AND debit = %s 
            AND credit = %s 
            AND (project IS NULL OR project = '')
        """, (row.project, row.parent, row.account, row.debit, row.credit))
        
        if res:
            sync_count += 1
            if sync_count % 100 == 0:
                print(f"已同步底账 {sync_count} 条...")
                frappe.db.commit()

    frappe.db.commit()
    
    # 2. 清理缓存
    frappe.clear_cache()
    
    print(f"SUCCESS: 强制同步完成，共更新底账 {sync_count} 条，并已清理系统缓存。")

if __name__ == "__main__":
    run()
