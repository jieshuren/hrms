from unittest.mock import patch
from types import SimpleNamespace

import frappe
from frappe.tests.utils import FrappeTestCase

from hrms.api import get_expense_claim_type_category_map
from hrms.hr.doctype.expense_claim.expense_claim import expense_claim_has_permission


class TestExpenseClaimAPI(FrappeTestCase):
	def test_get_expense_claim_type_category_map_resolves_account_category(self):
		def fake_get_all(doctype, *args, **kwargs):
			if doctype == "Expense Claim Type":
				return [{"name": "差旅费"}]
			if doctype == "Expense Claim Account":
				return [{"parent": "差旅费", "default_account": "Travel Expenses - _TC"}]
			if doctype == "Account":
				return [
					{
						"name": "Travel Expenses - _TC",
						"account_name": "Travel Expenses",
						"parent_account": "管理费用 - _TC",
					}
				]
			raise AssertionError(f"Unexpected doctype: {doctype}")

		with patch.object(frappe, "get_all", side_effect=fake_get_all):
			self.assertEqual(
				get_expense_claim_type_category_map("华烁科技"),
				{"差旅费": "管理费用"},
			)

	def test_get_expense_claim_type_category_map_without_company_marks_unknown(self):
		with patch.object(frappe, "get_all", return_value=[{"name": "差旅费"}]):
			self.assertEqual(
				get_expense_claim_type_category_map(None),
				{"差旅费": "无法识别"},
			)

	def test_same_department_employee_has_write_permission_only_in_peer_stage(self):
		doc = SimpleNamespace(
			owner="owner@example.com",
			expense_approver="",
			employee="HR-EMP-001",
			workflow_state="Draft",
			docstatus=0,
			department="研发部",
		)

		with (
			patch.object(frappe, "get_roles", return_value=["Employee"]),
			patch.object(
				frappe.db,
				"get_value",
				return_value=frappe._dict(name="HR-EMP-002", department="研发部"),
			),
		):
			self.assertFalse(expense_claim_has_permission(doc, ptype="write", user="peer@example.com"))

		doc.workflow_state = "Pending Peer Verification"

		with (
			patch.object(frappe, "get_roles", return_value=["Employee"]),
			patch.object(
				frappe.db,
				"get_value",
				return_value=frappe._dict(name="HR-EMP-002", department="研发部"),
			),
		):
			self.assertTrue(expense_claim_has_permission(doc, ptype="write", user="peer@example.com"))
