from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from hrms.api.mobile import (
	_validate_material_request_payload,
	get_mobile_attendance_records,
	get_mobile_material_request_detail,
	get_mobile_material_request_queue,
	get_mobile_purchase_items,
)


class TestMobileAPI(FrappeTestCase):
	def test_material_request_detail_collects_full_procurement_progress(self):
		class FakeMaterialRequest:
			doctype = "Material Request"
			name = "MAT-MR-2026-0001"
			owner = "buyer@example.com"
			docstatus = 1

			def as_dict(self):
				return {
					"doctype": self.doctype,
					"name": self.name,
					"owner": self.owner,
					"docstatus": self.docstatus,
					"status": "Pending",
				}

		child_links = {
			"Request for Quotation Item": ["PUR-RFQ-2026-0001"],
			"Supplier Quotation Item": ["PUR-SQTN-2026-0001"],
			"Purchase Order Item": ["PUR-ORD-2026-0001"],
			"Purchase Receipt Item": ["MAT-PRE-2026-0001"],
			"Purchase Invoice Item": ["ACC-PINV-2026-0001"],
		}
		related_docs = {
			"Request for Quotation": [frappe._dict(name="PUR-RFQ-2026-0001", transaction_date="2026-04-26", status="Submitted", docstatus=1, modified="2026-04-26 10:00:00")],
			"Supplier Quotation": [frappe._dict(name="PUR-SQTN-2026-0001", supplier="测试供应商", transaction_date="2026-04-26", status="Submitted", grand_total=1000, docstatus=1, modified="2026-04-26 10:10:00")],
			"Purchase Order": [frappe._dict(name="PUR-ORD-2026-0001", supplier="测试供应商", transaction_date="2026-04-26", status="To Receive and Bill", grand_total=1000, per_received=100, per_billed=100, docstatus=1, modified="2026-04-26 10:20:00")],
			"Purchase Receipt": [frappe._dict(name="MAT-PRE-2026-0001", supplier="测试供应商", posting_date="2026-04-26", status="To Bill", grand_total=1000, docstatus=1, modified="2026-04-26 10:30:00")],
			"Purchase Invoice": [frappe._dict(name="ACC-PINV-2026-0001", supplier="测试供应商", posting_date="2026-04-26", status="Unpaid", grand_total=1000, outstanding_amount=0, docstatus=1, modified="2026-04-26 10:40:00")],
			"Payment Entry": [frappe._dict(name="ACC-PAY-2026-0001", posting_date="2026-04-26", status="Submitted", paid_amount=1000, docstatus=1, modified="2026-04-26 10:50:00")],
		}

		def fake_get_docs_by_names(doctype, names, fields):
			del fields
			rows = related_docs.get(doctype, [])
			name_set = set(names)
			return [row for row in rows if row.name in name_set]

		def fake_get_all(doctype, *args, **kwargs):
			if doctype == "Workflow Action":
				return [
					frappe._dict(
						name="WA-0001",
						status="Completed",
						workflow_state="Pending Department Approval",
						completed_by="dept@example.com",
						completed_by_role="Employee",
						creation="2026-04-26 09:30:00",
						modified="2026-04-26 09:40:00",
					)
				]
			return [frappe._dict(parent="ACC-PAY-2026-0001")]

		with (
			patch("hrms.api.mobile._ensure_material_request_access"),
			patch.object(frappe, "get_doc", return_value=FakeMaterialRequest()),
			patch("hrms.api.mobile._get_employee_lookup", return_value=({}, {"buyer@example.com": {"employee_name": "张采购", "department": "采购部"}})),
			patch("hrms.api.mobile._get_child_parent_names", side_effect=lambda doctype, fieldname, parent: child_links[doctype]),
			patch("hrms.api.mobile._get_docs_by_names", side_effect=fake_get_docs_by_names),
			patch.object(frappe, "get_all", side_effect=fake_get_all),
			patch.object(frappe, "session", frappe._dict(user="procurement@example.com")),
		):
			result = get_mobile_material_request_detail("MAT-MR-2026-0001")

		self.assertEqual(result["_employee_name"], "张采购")
		self.assertEqual(result["_department_name"], "采购部")
		self.assertFalse(result["can_edit"])
		self.assertEqual(result["progress"]["request_for_quotation"], 1)
		self.assertEqual(result["progress"]["supplier_quotation"], 1)
		self.assertEqual(result["progress"]["purchase_order"], 1)
		self.assertEqual(result["progress"]["purchase_receipt"], 1)
		self.assertEqual(result["progress"]["purchase_invoice"], 1)
		self.assertEqual(result["progress"]["payment_entry"], 1)
		self.assertEqual(result["related_docs"]["payment_entry"][0]["name"], "ACC-PAY-2026-0001")
		self.assertEqual(result["workflow_actions"][0]["completed_by"], "dept@example.com")

	def test_material_request_queue_returns_empty_for_non_procurement_user(self):
		with patch.object(frappe, "get_roles", return_value=["Employee"]):
			self.assertEqual(get_mobile_material_request_queue(), [])

	def test_mobile_purchase_items_excludes_template_items(self):
		with patch.object(frappe, "get_all", return_value=[]) as mock_get_all:
			get_mobile_purchase_items()

		filters = mock_get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["has_variants"], 0)

	def test_material_request_queue_serializes_requester_and_items(self):
		def fake_get_all(doctype, *args, **kwargs):
			if doctype == "Material Request":
				return [
					frappe._dict(
						name="MAT-MR-2026-0001",
						transaction_date="2026-04-26",
						schedule_date="2026-04-30",
						status="Pending",
						material_request_type="Purchase",
						owner="buyer@example.com",
						per_ordered=35,
						docstatus=1,
						modified="2026-04-26 10:00:00",
						creation="2026-04-26 09:00:00",
					)
				]
			if doctype == "Employee":
				return [
					frappe._dict(
						name="HR-EMP-0001",
						employee_name="张采购",
						department="采购部",
						designation="采购专员",
						status="Active",
						user_id="buyer@example.com",
					)
				]
			if doctype == "Material Request Item":
				return [
					frappe._dict(
						parent="MAT-MR-2026-0001",
						item_code="ITEM-001",
						item_name="钢板",
						qty=2,
						uom="张",
					),
					frappe._dict(
						parent="MAT-MR-2026-0001",
						item_code="ITEM-002",
						item_name="螺丝",
						qty=10,
						uom="个",
					),
				]
			raise AssertionError(f"Unexpected doctype: {doctype}")

		with (
			patch.object(frappe, "get_roles", return_value=["Purchase User"]),
			patch.object(frappe, "get_all", side_effect=fake_get_all),
			patch("hrms.api.mobile._get_mr_list_fields", return_value=[
				"name",
				"transaction_date",
				"schedule_date",
				"status",
				"material_request_type",
				"owner",
				"per_ordered",
				"docstatus",
				"modified",
				"creation",
			]),
		):
			rows = get_mobile_material_request_queue(limit=10)

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["employee_name"], "张采购")
		self.assertEqual(rows[0]["department"], "采购部")
		self.assertIn("钢板", rows[0]["items"])
		self.assertEqual(rows[0]["item_count"], 2)
		self.assertEqual(rows[0]["workflow_label"], "部分下单")

	def test_mobile_attendance_records_limit_regular_employee_to_self(self):
		with (
			patch("hrms.api.mobile.get_current_employee_info", return_value={"name": "HR-EMP-SELF"}),
			patch.object(frappe, "get_roles", return_value=["Employee"]),
			patch.object(frappe, "get_all", return_value=[]) as mock_get_all,
		):
			get_mobile_attendance_records(
				employee_ids='["HR-EMP-SELF", "HR-EMP-OTHER"]',
				date="2026-04-26",
			)

		self.assertEqual(mock_get_all.call_count, 1)
		filters = mock_get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["employee"], ("in", ["HR-EMP-SELF"]))

	def test_validate_material_request_payload_fills_purchase_uom(self):
		with patch.object(
			frappe,
			"get_all",
			return_value=[
				frappe._dict(
					name="ITEM-001",
					item_name="钢板",
					stock_uom="件",
					purchase_uom="张",
				)
			],
		):
			rows = _validate_material_request_payload(
				frappe._dict(
					company="华烁科技",
					transaction_date="2026-04-26",
					schedule_date="2026-04-30",
					items=[{"item_code": "ITEM-001", "qty": 2}],
				)
			)

		self.assertEqual(rows[0].uom, "张")
