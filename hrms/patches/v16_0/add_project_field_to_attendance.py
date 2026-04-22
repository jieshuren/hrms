from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	create_custom_field(
		"Attendance",
		{
			"fieldname": "project",
			"fieldtype": "Link",
			"label": _("Project"),
			"options": "Project",
			"insert_after": "department",
		},
	)

	backfill_project_on_attendance()


def backfill_project_on_attendance():
	import frappe

	records = frappe.get_all(
		"Attendance",
		filters={"project": ["is", "not set"]},
		fields=["name", "department"],
		limit=0,
	)

	if not records:
		return

	departments = {r.department for r in records if r.department}
	if not departments:
		return

	project_map = {}
	for dept in departments:
		projects = frappe.get_all(
			"Project",
			filters={"department": dept, "status": "Open"},
			fields=["name"],
			order_by="creation asc",
			limit=1,
		)
		if projects:
			project_map[dept] = projects[0].name

	if not project_map:
		return

	for rec in records:
		project = project_map.get(rec.department)
		if project:
			frappe.db.set_value("Attendance", rec.name, "project", project, update_modified=False)
