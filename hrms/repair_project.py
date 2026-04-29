import pandas as pd
import glob
import frappe
import os

def run():
    # 1. 映射表
    projects = frappe.get_all("Project", fields=["name", "project_name"])
    project_map = {p.project_name: p.name for p in projects}
    
    # 2. 匹配库 (使用绝对路径或 bench 根目录路径)
    base_path = frappe.utils.get_bench_path()
    excel_files = glob.glob(os.path.join(base_path, '财务数据/HuaShuo_凭证列表_*.xlsx'))
    mapping = {}
    
    print(f"开始解析 {len(excel_files)} 个 Excel 文件...")
    for f in excel_files:
        try:
            df = pd.read_excel(f)
            df = df[df['项目'].notna()]
            for _, row in df.iterrows():
                try:
                    # 凭证日期处理
                    d = str(row['凭证日期']).split(' ')[0]
                    no = str(int(row['凭证号']))
                    # 金额匹配
                    debit = float(row['借方金额']) if pd.notna(row['借方金额']) else 0
                    credit = float(row['贷方金额']) if pd.notna(row['贷方金额']) else 0
                    amt = round(debit if debit > 0 else credit, 2)
                    
                    p_name = str(row['项目']).strip()
                    p_id = project_map.get(p_name)
                    if p_id:
                        mapping[(d, no, amt)] = p_id
                except: continue
        except: continue

    print(f"Excel匹配库构建完成: {len(mapping)} 条记录")

    # 3. 更新
    updated = 0
    # 注意：posting_date 属于父表 tabJournal Entry
    accounts = frappe.db.sql("""
        SELECT a.name, a.parent, j.posting_date, a.debit, a.credit, j.cheque_no
        FROM `tabJournal Entry Account` a
        JOIN `tabJournal Entry` j ON a.parent = j.name
        WHERE (a.project IS NULL OR a.project = '') AND j.docstatus = 1
    """, as_dict=True)

    for a in accounts:
        if not a.cheque_no: continue
        amt = round(float(a.debit) if float(a.debit) > 0 else float(a.credit), 2)
        key = (str(a.posting_date), str(a.cheque_no), amt)
        
        p_id = mapping.get(key)
        if p_id:
            # 更新分录行
            frappe.db.set_value("Journal Entry Account", a.name, "project", p_id, update_modified=False)
            # 更新底账
            frappe.db.sql("UPDATE `tabGL Entry` SET project = %s WHERE voucher_detail_no = %s", (p_id, a.name))
            updated += 1
            if updated % 100 == 0:
                print(f"已修复 {updated} 条...")

    frappe.db.commit()
    print(f"SUCCESS: 共修复 {updated} 条分录的项目关联。")
