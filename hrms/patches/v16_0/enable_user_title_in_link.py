import frappe


def execute():
	"""Display User full_name in Link fields while storing user id."""
	if frappe.db.exists("DocType", "User"):
		frappe.db.set_value("DocType", "User", "show_title_field_in_link", 1, update_modified=False)
		frappe.clear_cache(doctype="User")
