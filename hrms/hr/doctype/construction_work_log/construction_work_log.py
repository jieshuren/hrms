import frappe
from frappe.model.document import Document


class ConstructionWorkLog(Document):
	def validate(self):
		if self.project and not self.project_name:
			self.project_name = frappe.db.get_value("Project", self.project, "project_name") or self.project
		if self.status == "Draft" and self.docstatus == 1:
			self.status = "Submitted"
