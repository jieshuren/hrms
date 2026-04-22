import frappe

print("开始修复用户角色...")

# 1. 给王亚男添加所有建议的角色
wang_user = frappe.get_doc("User", "18704599704@aierp.local")
roles_to_add = ["Employee", "HR User", "HR Manager", "Expense Approver", "Accounts User", "All"]

for role_name in roles_to_add:
    if frappe.db.exists("Role", role_name):
        existing = any(r.role == role_name for r in wang_user.roles)
        if not existing:
            wang_user.append("roles", {"role": role_name})
            print("添加角色给王亚男:", role_name)

wang_user.save()
frappe.db.commit()
print("王亚男的角色:", [r.role for r in wang_user.roles])

# 2. 检查所有用户
users = frappe.get_all("User", filters={"enabled": 1}, fields=["name", "full_name", "user_type"])

for user_data in users:
    user = frappe.get_doc("User", user_data.name)
    
    if user.user_type == "System User" or user.name in ["Administrator", "Guest"]:
        continue
    
    if not user.roles:
        print("用户没有角色:", user.name)
        for role_name in ["Employee", "All"]:
            if frappe.db.exists("Role", role_name):
                user.append("roles", {"role": role_name})
        user.save()
        print("已添加基本角色给:", user.name)

frappe.db.commit()
print("完成！")
