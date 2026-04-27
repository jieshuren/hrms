# Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
import frappe
from frappe import bold
from frappe.utils import strip_html


class PWANotificationsMixin:
	"""Mixin class for managing PWA updates"""

	def _get_notification_subject(self) -> str:
		"""Notification title shown in PWA cards."""
		if self.doctype != "Expense Claim":
			return f"{self.doctype} {self.name}"

		# Prefer explicit remark first, then the first non-empty expense detail.
		if self.get("remark"):
			return self.remark.strip()

		for row in self.get("expenses") or []:
			description = strip_html((row.get("description") or "")).strip()
			if description:
				return description
			expense_type = (row.get("expense_type") or "").strip()
			if expense_type:
				return expense_type

		return self.doctype

	def notify_approval_status(self):
		"""Send Leave Application, Expense Claim & Shift Request Approval status notification - to employees"""
		status_field = self._get_doc_status_field()
		status = self.get(status_field)

		if self.has_value_changed(status_field) and status in ["Approved", "Rejected"]:
			from_user = frappe.session.user
			from_user_name = self._get_user_name(from_user)
			to_user = self._get_employee_user()

			if from_user == to_user:
				return

			notification = frappe.new_doc("PWA Notification")
			notification.from_user = from_user
			notification.to_user = to_user

			subject = self._get_notification_subject()
			notification.message = (
				f"{bold('Your')} {bold(self.doctype)} {bold(subject)} "
				f"has been {bold(status)} by {bold(from_user_name)}"
			)

			notification.reference_document_type = self.doctype
			notification.reference_document_name = self.name
			notification.insert(ignore_permissions=True)

	def notify_approver(self):
		"""Send new Leave Application, Expense Claim & Shift Request request notification - to approvers"""
		from_user = self._get_employee_user()
		to_user = self._get_doc_approver()

		if not to_user or from_user == to_user:
			return

		notification = frappe.new_doc("PWA Notification")
		subject = self._get_notification_subject()
		notification.message = (
			f"{bold(self.employee_name)} raised a new {bold(self.doctype)} for approval: {bold(subject)}"
		)
		notification.from_user = from_user
		notification.to_user = to_user

		notification.reference_document_type = self.doctype
		notification.reference_document_name = self.name
		notification.insert(ignore_permissions=True)

	def _get_doc_status_field(self) -> str:
		APPROVAL_STATUS_FIELD = {
			"Leave Application": "status",
			"Expense Claim": "approval_status",
			"Shift Request": "status",
		}
		return APPROVAL_STATUS_FIELD[self.doctype]

	def _get_doc_approver(self) -> str:
		APPROVER_FIELD = {
			"Leave Application": "leave_approver",
			"Expense Claim": "expense_approver",
			"Shift Request": "approver",
		}
		approver_field = APPROVER_FIELD[self.doctype]
		return self.get(approver_field)

	def _get_employee_user(self) -> str:
		return frappe.db.get_value("Employee", self.employee, "user_id", cache=True)

	def _get_user_name(self, user) -> str:
		return frappe.db.get_value("User", user, "full_name", cache=True)
