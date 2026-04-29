import frappe

def run():
    print("开始精准底账匹配修复...")
    
    # 获取所有有项目的分录行
    je_data = frappe.db.sql("""
        SELECT a.parent, a.account, a.project, a.debit, a.credit
        FROM `tabJournal Entry Account` a
        WHERE a.project IS NOT NULL AND a.project != ''
    """, as_dict=True)
    
    success = 0
    total = len(je_data)
    
    print(f"总计需要处理凭证行: {total}")

    for idx, row in enumerate(je_data):
        # 寻找对应的底账行
        # 使用数据库级别的严格匹配
        gl_entries = frappe.get_all("GL Entry", 
            filters={
                "voucher_no": row.parent,
                "account": row.account,
                "debit": row.debit,
                "credit": row.credit
            },
            fields=["name", "project"]
        )
        
        for gl in gl_entries:
            if gl.project != row.project:
                # 强行更新
                frappe.db.sql("UPDATE `tabGL Entry` SET project = %s WHERE name = %s", (row.project, gl.name))
                success += 1

        if idx % 100 == 0:
            print(f"进度: {idx}/{total}...")
            frappe.db.commit()

    frappe.db.commit()
    frappe.clear_cache()
    print(f"完成！共成功修正底账项目字段: {success} 条。")

if __name__ == "__main__":
    run()
