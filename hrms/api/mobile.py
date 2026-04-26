from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from hrms.api import get_current_employee_info
from hrms.hr.doctype.employee_attendance_tool.employee_attendance_tool import _clean_existing_attendance

PROCUREMENT_ROLES = {
	"System Manager",
	"Purchase Manager",
	"Purchase User",
	"Stock Manager",
	"Accounts Manager",
	"Accounts User",
}

ATTENDANCE_MANAGER_ROLES = {
	"System Manager",
	"HR Manager",
	"HR User",
	"Attendance Manager",
}

PROJECT_EDITOR_ROLES = ATTENDANCE_MANAGER_ROLES | {
	"Project Manager",
}

MATERIAL_REQUEST_STAGE_DEFS = [
	{
		"key": "material_request",
		"title": "物料申请",
		"caption": "需求部门提出采购需求",
		"accent": "brand",
	},
	{
		"key": "request_for_quotation",
		"title": "询价",
		"caption": "向供应商发出询价",
		"accent": "info",
	},
	{
		"key": "supplier_quotation",
		"title": "供应商报价",
		"caption": "收集并比较报价",
		"accent": "warning",
	},
	{
		"key": "purchase_order",
		"title": "采购订单",
		"caption": "确认供应商并正式下单",
		"accent": "warning",
	},
	{
		"key": "purchase_receipt",
		"title": "采购收货",
		"caption": "到货验收并入库",
		"accent": "success",
	},
	{
		"key": "purchase_invoice",
		"title": "采购发票",
		"caption": "挂账并核对账单",
		"accent": "brand",
	},
	{
		"key": "payment_entry",
		"title": "付款",
		"caption": "完成对供应商付款",
		"accent": "success",
	},
]


def _current_roles() -> set[str]:
	return set(frappe.get_roles(frappe.session.user))


def _has_any_role(role_names: set[str]) -> bool:
	return bool(_current_roles() & set(role_names))


def _can_manage_attendance() -> bool:
	return _has_any_role(ATTENDANCE_MANAGER_ROLES)


def _can_manage_projects() -> bool:
	return _has_any_role(PROJECT_EDITOR_ROLES)


def _can_manage_procurement() -> bool:
	return _has_any_role(PROCUREMENT_ROLES)


def _parse_name_list(value: str | list[str] | None) -> list[str]:
	if isinstance(value, str):
		text = value.strip()
		if not text:
			return []
		try:
			parsed = json.loads(text)
		except Exception:
			parsed = [text]
	elif isinstance(value, list):
		parsed = value
	else:
		parsed = []

	seen: set[str] = set()
	result: list[str] = []
	for raw in parsed:
		name = str(raw or "").strip()
		if not name or name in seen:
			continue
		seen.add(name)
		result.append(name)
	return result


def _get_current_employee_row(raise_exception: bool = True) -> frappe._dict | None:
	employee = frappe._dict(get_current_employee_info() or {})
	if employee or not raise_exception:
		return employee
	frappe.throw(_("未找到当前员工档案"), frappe.PermissionError)


def _get_employee_lookup(
	*,
	user_ids: list[str] | None = None,
	employee_ids: list[str] | None = None,
) -> tuple[dict[str, frappe._dict], dict[str, frappe._dict]]:
	filters = {}
	if user_ids:
		filters["user_id"] = ("in", user_ids)
	if employee_ids:
		filters["name"] = ("in", employee_ids)
	if not filters:
		return {}, {}

	rows = frappe.get_all(
		"Employee",
		fields=["name", "employee_name", "department", "designation", "status", "user_id"],
		filters=filters,
		limit_page_length=500,
		order_by="employee_name asc",
	)
	by_name = {row.name: frappe._dict(row) for row in rows}
	by_user_id = {
		row.user_id: frappe._dict(row)
		for row in rows
		if row.get("user_id")
	}
	return by_name, by_user_id


def _get_material_request_item_map(names: list[str]) -> dict[str, list[frappe._dict]]:
	if not names:
		return {}

	rows = frappe.get_all(
		"Material Request Item",
		fields=["parent", "item_code", "item_name", "qty", "uom", "warehouse", "schedule_date", "description"],
		filters={"parent": ("in", names)},
		order_by="idx asc",
		limit_page_length=1000,
	)
	item_map: dict[str, list[frappe._dict]] = {}
	for row in rows:
		item_map.setdefault(row.parent, []).append(frappe._dict(row))
	return item_map


def _build_request_item_summary(items: list[frappe._dict]) -> str:
	parts: list[str] = []
	for row in items[:3]:
		label = str(row.get("item_name") or row.get("item_code") or "").strip()
		if not label:
			continue
		qty = flt(row.get("qty"))
		uom = str(row.get("uom") or "").strip()
		if qty:
			parts.append(f"{label} x {qty:g}{(' ' + uom) if uom else ''}")
		else:
			parts.append(label)
	if not parts:
		return ""
	if len(items) > 3:
		parts.append(f"等{len(items)}项")
	return "、".join(parts)


def _material_request_queue_label(row: frappe._dict) -> str:
	docstatus = cint(row.get("docstatus"))
	status = str(row.get("status") or "").strip()
	per_ordered = flt(row.get("per_ordered"))
	if docstatus == 0:
		return "草稿待提交"
	if status in {"Stopped", "Cancelled"}:
		return "已结束"
	if status == "Ordered" or per_ordered >= 99.99:
		return "已生成采购单"
	if per_ordered > 0:
		return "部分下单"
	return "待采购"


def _serialize_material_request_rows(rows: list[frappe._dict]) -> list[dict]:
	if not rows:
		return []

	names = [row.name for row in rows if row.get("name")]
	_, employee_by_user_id = _get_employee_lookup(
		user_ids=[str(row.get("owner") or "").strip() for row in rows if row.get("owner")]
	)
	item_map = _get_material_request_item_map(names)

	result: list[dict] = []
	for row in rows:
		items = item_map.get(row.name, [])
		employee = employee_by_user_id.get(str(row.get("owner") or "").strip())
		result.append(
			{
				"name": row.name,
				"transaction_date": row.get("transaction_date"),
				"schedule_date": row.get("schedule_date"),
				"status": row.get("status") or "Draft",
				"material_request_type": row.get("material_request_type") or "Purchase",
				"owner": row.get("owner"),
				"modified": row.get("modified"),
				"employee_name": employee.get("employee_name") if employee else "",
				"department": employee.get("department") if employee else "",
				"items": _build_request_item_summary(items),
				"item_count": len(items),
				"qty_total": sum(flt(item.get("qty")) for item in items),
				"per_ordered": flt(row.get("per_ordered")),
				"workflow_label": _material_request_queue_label(row),
			}
		)
	return result


def _ensure_material_request_access(doc: frappe.model.document.Document) -> None:
	if _can_manage_procurement():
		return
	if doc.owner == frappe.session.user:
		return
	frappe.throw(_("您无权查看这条物料申请"), frappe.PermissionError)


def _get_child_parent_names(doctype: str, fieldname: str, value: str) -> list[str]:
	rows = frappe.get_all(
		doctype,
		fields=["parent"],
		filters={fieldname: value},
		order_by="modified desc",
		limit_page_length=500,
	)
	seen: set[str] = set()
	result: list[str] = []
	for row in rows:
		parent = str(row.get("parent") or "").strip()
		if not parent or parent in seen:
			continue
		seen.add(parent)
		result.append(parent)
	return result


def _get_docs_by_names(doctype: str, names: list[str], fields: list[str]) -> list[frappe._dict]:
	if not names:
		return []
	return frappe.get_all(
		doctype,
		fields=fields,
		filters={"name": ("in", names)},
		order_by="modified desc",
		limit_page_length=500,
	)


def _serialize_related_docs(rows: list[frappe._dict], date_field: str) -> list[dict]:
	result: list[dict] = []
	for row in rows:
		result.append(
			{
				"name": row.get("name"),
				"status": row.get("status") or "",
				"docstatus": cint(row.get("docstatus")),
				"supplier": row.get("supplier") or "",
				"date": row.get(date_field),
				"grand_total": flt(row.get("grand_total")),
				"outstanding_amount": flt(row.get("outstanding_amount")),
				"paid_amount": flt(row.get("paid_amount")),
			}
		)
	return result


def _normalize_material_request_payload(payload: str | dict | None, kwargs: dict) -> frappe._dict:
	if isinstance(payload, dict):
		data = payload
	elif isinstance(payload, str):
		text = payload.strip()
		data = frappe.parse_json(text) if text else {}
	else:
		data = kwargs or {}
	return frappe._dict(data or {})


def _validate_material_request_payload(data: frappe._dict) -> list[frappe._dict]:
	if not str(data.get("company") or "").strip():
		frappe.throw(_("请选择公司"))
	if not data.get("transaction_date"):
		frappe.throw(_("请选择申请日期"))
	if not data.get("schedule_date"):
		frappe.throw(_("请选择需求日期"))

	items = data.get("items") or []
	if not isinstance(items, list) or not items:
		frappe.throw(_("请至少填写一条物料明细"))

	normalized_items: list[frappe._dict] = []
	item_codes = []
	for idx, raw in enumerate(items, start=1):
		row = frappe._dict(raw or {})
		item_code = str(row.get("item_code") or "").strip()
		if not item_code:
			frappe.throw(_("第 {0} 行缺少物料").format(idx))
		qty = flt(row.get("qty"))
		if qty <= 0:
			frappe.throw(_("第 {0} 行数量必须大于 0").format(idx))
		item_codes.append(item_code)
		normalized_items.append(row)

	item_rows = frappe.get_all(
		"Item",
		fields=["name", "item_name", "stock_uom", "purchase_uom"],
		filters={"name": ("in", item_codes)},
		limit_page_length=500,
	)
	item_meta = {row.name: frappe._dict(row) for row in item_rows}
	for idx, row in enumerate(normalized_items, start=1):
		meta = item_meta.get(str(row.get("item_code") or "").strip())
		if not meta:
			frappe.throw(_("第 {0} 行物料不存在").format(idx))
		if not str(row.get("uom") or "").strip():
			row.uom = meta.get("purchase_uom") or meta.get("stock_uom")
		if not str(row.get("uom") or "").strip():
			frappe.throw(_("第 {0} 行缺少单位").format(idx))
	return normalized_items


def _apply_material_request_fields(doc, data: frappe._dict, items: list[frappe._dict]) -> None:
	doc.material_request_type = str(data.get("material_request_type") or "Purchase").strip()
	doc.company = str(data.get("company") or "").strip()
	doc.transaction_date = getdate(data.get("transaction_date"))
	doc.schedule_date = getdate(data.get("schedule_date"))
	doc.set_warehouse = str(data.get("set_warehouse") or "").strip() or None
	doc.set("items", [])
	for item in items:
		row = doc.append("items", {})
		if item.get("name"):
			row.name = item.get("name")
		row.item_code = str(item.get("item_code") or "").strip()
		row.qty = flt(item.get("qty"))
		row.uom = str(item.get("uom") or "").strip()
		row.schedule_date = getdate(item.get("schedule_date") or data.get("schedule_date"))
		row.warehouse = str(item.get("warehouse") or data.get("set_warehouse") or "").strip() or None
		row.project = str(item.get("project") or "").strip() or None
		row.description = str(item.get("description") or "").strip() or None


@frappe.whitelist()
def get_mobile_companies() -> list[dict]:
	return frappe.get_all(
		"Company",
		fields=["name", "company_name"],
		order_by="company_name asc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_mobile_warehouses(company: str | None = None) -> list[dict]:
	filters = {"disabled": 0}
	if str(company or "").strip():
		filters["company"] = company
	return frappe.get_all(
		"Warehouse",
		fields=["name", "warehouse_name", "company"],
		filters=filters,
		order_by="warehouse_name asc",
		limit_page_length=300,
	)


@frappe.whitelist()
def get_mobile_projects(department: str | None = None) -> list[dict]:
	filters = {
		"status": "Open",
		"is_active": "Yes",
	}
	if str(department or "").strip():
		filters["department"] = department
	return frappe.get_all(
		"Project",
		fields=["name", "project_name", "department", "status", "is_active"],
		filters=filters,
		order_by="modified desc",
		limit_page_length=300,
	)


@frappe.whitelist()
def get_mobile_purchase_items() -> list[dict]:
	return frappe.get_all(
		"Item",
		fields=["name", "item_name", "stock_uom", "purchase_uom"],
		filters={"disabled": 0, "is_purchase_item": 1},
		order_by="modified desc",
		limit_page_length=300,
	)


@frappe.whitelist()
def get_mobile_departments() -> list[dict]:
	return frappe.get_all(
		"Department",
		fields=["name", "department_name"],
		order_by="department_name asc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_mobile_shift_types() -> list[dict]:
	return frappe.get_all(
		"Shift Type",
		fields=["name"],
		order_by="name asc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_mobile_employees(
	employee_ids: str | list[str] | None = None,
	department: str | None = None,
	active_only: int | bool = 1,
) -> list[dict]:
	current_employee = _get_current_employee_row(raise_exception=False)
	allowed_ids = _parse_name_list(employee_ids)
	filters = {}

	if _can_manage_attendance():
		if allowed_ids:
			filters["name"] = ("in", allowed_ids)
		if str(department or "").strip():
			filters["department"] = department
	else:
		if not current_employee:
			return []
		current_employee_id = str(current_employee.get("name") or "").strip()
		if allowed_ids:
			allowed_ids = [employee_id for employee_id in allowed_ids if employee_id == current_employee_id]
			if not allowed_ids:
				return []
			filters["name"] = ("in", allowed_ids)
		else:
			filters["name"] = current_employee_id

	if cint(active_only):
		filters["status"] = "Active"

	return frappe.get_all(
		"Employee",
		fields=["name", "employee_name", "department", "designation", "status"],
		filters=filters,
		order_by="employee_name asc",
		limit_page_length=500,
	)


@frappe.whitelist()
def get_mobile_attendance_records(
	employee_ids: str | list[str] | None = None,
	employee: str | None = None,
	date: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	project: str | None = None,
) -> list[dict]:
	current_employee = _get_current_employee_row(raise_exception=False)
	if employee:
		requested_ids = [str(employee).strip()]
	else:
		requested_ids = _parse_name_list(employee_ids)

	if not requested_ids and current_employee:
		requested_ids = [str(current_employee.get("name") or "").strip()]

	if not requested_ids:
		return []

	if not _can_manage_attendance():
		own_id = str((current_employee or {}).get("name") or "").strip()
		requested_ids = [employee_id for employee_id in requested_ids if employee_id == own_id]
		if not requested_ids:
			return []

	filters = {
		"employee": ("in", requested_ids),
		"docstatus": ("!=", 2),
	}
	if date:
		filters["attendance_date"] = getdate(date)
	elif from_date and to_date:
		filters["attendance_date"] = ["between", [getdate(from_date), getdate(to_date)]]
	elif from_date:
		filters["attendance_date"] = [">=", getdate(from_date)]
	elif to_date:
		filters["attendance_date"] = ["<=", getdate(to_date)]

	if str(project or "").strip() and frappe.db.has_column("Attendance", "project"):
		filters["project"] = project

	fields = [
		"name",
		"employee",
		"employee_name",
		"attendance_date",
		"status",
		"leave_type",
		"in_time",
		"out_time",
		"working_hours",
		"shift",
		"late_entry",
		"early_exit",
		"docstatus",
	]
	if frappe.db.has_column("Attendance", "project"):
		fields.append("project")

	return frappe.get_all(
		"Attendance",
		fields=fields,
		filters=filters,
		order_by="attendance_date asc, creation desc",
		limit_page_length=1000,
	)


@frappe.whitelist()
def mark_mobile_attendance(
	employee_ids: str | list[str],
	status: str,
	date: str,
	shift: str | None = None,
	project: str | None = None,
	leave_type: str | None = None,
) -> dict:
	if not _can_manage_attendance():
		frappe.throw(_("您没有团队考勤权限"), frappe.PermissionError)

	ids = _parse_name_list(employee_ids)
	if not ids:
		frappe.throw(_("请选择员工"))

	status = str(status or "").strip()
	if status not in {"Present", "Absent", "On Leave"}:
		frappe.throw(_("不支持的考勤状态"))

	attendance_date = getdate(date)
	effective_leave_type = str(leave_type or "").strip() if status == "On Leave" else None
	project_value = str(project or "").strip() if frappe.db.has_column("Attendance", "project") else None

	for employee_id in ids:
		_clean_existing_attendance(employee_id, attendance_date)
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": employee_id,
				"attendance_date": attendance_date,
				"status": status,
				"leave_type": effective_leave_type,
				"late_entry": 0,
				"early_exit": 0,
				"shift": str(shift or "").strip() or None,
				"project": project_value,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()

	return {"created": len(ids)}


@frappe.whitelist()
def cancel_mobile_attendance(name: str) -> dict:
	if not _can_manage_attendance():
		frappe.throw(_("您没有团队考勤权限"), frappe.PermissionError)
	doc = frappe.get_doc("Attendance", name)
	doc.flags.ignore_permissions = True
	if cint(doc.docstatus) == 1:
		doc.cancel()
	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def delete_mobile_attendance(name: str) -> dict:
	if not _can_manage_attendance():
		frappe.throw(_("您没有团队考勤权限"), frappe.PermissionError)
	frappe.delete_doc(
		"Attendance",
		name,
		ignore_permissions=True,
		force=True,
		delete_permanently=True,
	)
	return {"name": name}


@frappe.whitelist()
def create_mobile_project(project_name: str, department: str | None = None) -> dict:
	if not _can_manage_projects():
		frappe.throw(_("您没有创建项目权限"), frappe.PermissionError)

	project_name = str(project_name or "").strip()
	if not project_name:
		frappe.throw(_("请输入项目名称"))

	doc = frappe.get_doc(
		{
			"doctype": "Project",
			"project_name": project_name,
			"department": str(department or "").strip() or None,
			"status": "Open",
			"is_active": "Yes",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return {"name": doc.name}


@frappe.whitelist()
def get_mobile_material_request_queue(limit: int | None = 50) -> list[dict]:
	if not _can_manage_procurement():
		return []

	rows = frappe.get_all(
		"Material Request",
		fields=[
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
		],
		filters={
			"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]),
			"docstatus": ("!=", 2),
			"status": ("not in", ["Stopped", "Cancelled"]),
		},
		order_by="modified desc",
		limit_page_length=cint(limit) or 50,
	)
	return _serialize_material_request_rows([frappe._dict(row) for row in rows])


@frappe.whitelist()
def get_mobile_procurement_overview() -> dict:
	queue = get_mobile_material_request_queue(limit=20) if _can_manage_procurement() else []
	my_rows = frappe.get_all(
		"Material Request",
		fields=[
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
		],
		filters={
			"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]),
			"docstatus": ("!=", 2),
			"owner": frappe.session.user,
		},
		order_by="creation desc",
		limit_page_length=10,
	)
	my_requests = _serialize_material_request_rows([frappe._dict(row) for row in my_rows])

	if _can_manage_procurement():
		stage_counts = {
			"material_request": frappe.db.count(
				"Material Request", {"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]), "docstatus": ("!=", 2)}
			),
			"request_for_quotation": frappe.db.count("Request for Quotation", {"docstatus": ("!=", 2)}),
			"supplier_quotation": frappe.db.count("Supplier Quotation", {"docstatus": ("!=", 2)}),
			"purchase_order": frappe.db.count("Purchase Order", {"docstatus": ("!=", 2)}),
			"purchase_receipt": frappe.db.count("Purchase Receipt", {"docstatus": ("!=", 2)}),
			"purchase_invoice": frappe.db.count("Purchase Invoice", {"docstatus": ("!=", 2)}),
			"payment_entry": frappe.db.count(
				"Payment Entry",
				{
					"party_type": "Supplier",
					"payment_type": "Pay",
					"docstatus": 1,
				},
			),
		}
		recent_orders = frappe.get_all(
			"Purchase Order",
			fields=[
				"name",
				"supplier",
				"transaction_date",
				"schedule_date",
				"status",
				"grand_total",
				"currency",
				"per_received",
				"per_billed",
			],
			filters={"docstatus": ("!=", 2)},
			order_by="modified desc",
			limit_page_length=6,
		)
		kpis = {
			"pending_approval": len(queue),
			"to_receive": frappe.db.count("Purchase Order", {"docstatus": 1, "per_received": ("<", 99.99)}),
			"to_bill": frappe.db.count("Purchase Order", {"docstatus": 1, "per_billed": ("<", 99.99)}),
			"to_pay": frappe.db.count(
				"Purchase Invoice", {"docstatus": 1, "outstanding_amount": (">", 0)}
			),
		}
	else:
		stage_counts = {
			"material_request": frappe.db.count(
				"Material Request",
				{
					"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]),
					"docstatus": ("!=", 2),
					"owner": frappe.session.user,
				},
			),
			"request_for_quotation": 0,
			"supplier_quotation": 0,
			"purchase_order": 0,
			"purchase_receipt": 0,
			"purchase_invoice": 0,
			"payment_entry": 0,
		}
		recent_orders = []
		kpis = {
			"pending_approval": 0,
			"to_receive": 0,
			"to_bill": 0,
			"to_pay": 0,
		}

	return {
		"stage_cards": [{**stage, "count": cint(stage_counts.get(stage["key"], 0))} for stage in MATERIAL_REQUEST_STAGE_DEFS],
		"kpis": kpis,
		"recent_orders": recent_orders,
		"approval_items": queue,
		"my_requests": my_requests,
	}


@frappe.whitelist()
def get_mobile_material_request_detail(name: str) -> dict:
	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)

	result = doc.as_dict()
	_, employee_by_user_id = _get_employee_lookup(user_ids=[str(doc.owner or "").strip()])
	employee = employee_by_user_id.get(str(doc.owner or "").strip())
	result["_employee_name"] = employee.get("employee_name") if employee else ""
	result["_department_name"] = employee.get("department") if employee else ""
	result["can_edit"] = bool(doc.owner == frappe.session.user and cint(doc.docstatus) == 0)

	rfq_names = _get_child_parent_names("Request for Quotation Item", "material_request", doc.name)
	supplier_quote_names = _get_child_parent_names("Supplier Quotation Item", "material_request", doc.name)
	purchase_order_names = _get_child_parent_names("Purchase Order Item", "material_request", doc.name)
	purchase_receipt_names = _get_child_parent_names("Purchase Receipt Item", "material_request", doc.name)
	purchase_invoice_names = _get_child_parent_names("Purchase Invoice Item", "material_request", doc.name)

	rfqs = _get_docs_by_names(
		"Request for Quotation",
		rfq_names,
		["name", "transaction_date", "status", "docstatus", "modified"],
	)
	supplier_quotes = _get_docs_by_names(
		"Supplier Quotation",
		supplier_quote_names,
		["name", "supplier", "transaction_date", "status", "grand_total", "docstatus", "modified"],
	)
	purchase_orders = _get_docs_by_names(
		"Purchase Order",
		purchase_order_names,
		[
			"name",
			"supplier",
			"transaction_date",
			"status",
			"grand_total",
			"per_received",
			"per_billed",
			"docstatus",
			"modified",
		],
	)
	purchase_receipts = _get_docs_by_names(
		"Purchase Receipt",
		purchase_receipt_names,
		["name", "supplier", "posting_date", "status", "grand_total", "docstatus", "modified"],
	)
	purchase_invoices = _get_docs_by_names(
		"Purchase Invoice",
		purchase_invoice_names,
		[
			"name",
			"supplier",
			"posting_date",
			"status",
			"grand_total",
			"outstanding_amount",
			"docstatus",
			"modified",
		],
	)

	ref_names = list({*purchase_order_names, *purchase_invoice_names})
	payment_entry_refs = []
	if ref_names:
		payment_entry_refs = frappe.get_all(
			"Payment Entry Reference",
			fields=["parent"],
			filters={
				"reference_doctype": ("in", ["Purchase Order", "Purchase Invoice"]),
				"reference_name": ("in", ref_names),
			},
			order_by="modified desc",
			limit_page_length=500,
		)
	payment_entry_names = []
	seen_payment_entries: set[str] = set()
	for row in payment_entry_refs:
		parent = str(row.get("parent") or "").strip()
		if not parent or parent in seen_payment_entries:
			continue
		seen_payment_entries.add(parent)
		payment_entry_names.append(parent)

	payment_entries = _get_docs_by_names(
		"Payment Entry",
		payment_entry_names,
		["name", "posting_date", "status", "paid_amount", "docstatus", "modified"],
	)

	result["related_docs"] = {
		"request_for_quotation": _serialize_related_docs(rfqs, "transaction_date"),
		"supplier_quotation": _serialize_related_docs(supplier_quotes, "transaction_date"),
		"purchase_order": _serialize_related_docs(purchase_orders, "transaction_date"),
		"purchase_receipt": _serialize_related_docs(purchase_receipts, "posting_date"),
		"purchase_invoice": _serialize_related_docs(purchase_invoices, "posting_date"),
		"payment_entry": _serialize_related_docs(payment_entries, "posting_date"),
	}
	result["progress"] = {
		"request_for_quotation": len(rfqs),
		"supplier_quotation": len(supplier_quotes),
		"purchase_order": len(purchase_orders),
		"purchase_receipt": len(purchase_receipts),
		"purchase_invoice": len(purchase_invoices),
		"payment_entry": len(payment_entries),
	}
	return result


@frappe.whitelist()
def create_mobile_material_request(payload: str | dict | None = None, **kwargs) -> dict:
	current_employee = _get_current_employee_row()
	data = _normalize_material_request_payload(payload, kwargs)
	items = _validate_material_request_payload(data)

	doc = frappe.new_doc("Material Request")
	_apply_material_request_fields(doc, data, items)
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()

	return {
		"name": doc.name,
		"employee": current_employee.get("name"),
		"docstatus": cint(doc.docstatus),
	}


@frappe.whitelist()
def update_mobile_material_request(payload: str | dict | None = None, **kwargs) -> dict:
	data = _normalize_material_request_payload(payload, kwargs)
	name = str(data.get("name") or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)
	if doc.owner != frappe.session.user:
		frappe.throw(_("只有申请人本人可以修改草稿"), frappe.PermissionError)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的物料申请可以修改"))

	client_modified = str(data.get("modified") or "").strip()
	server_modified = str(doc.modified or "").strip()
	if client_modified and server_modified and client_modified != server_modified:
		frappe.throw(_("单据已被其他人更新，请返回列表刷新后重试"))

	items = _validate_material_request_payload(data)
	_apply_material_request_fields(doc, data, items)
	doc.flags.ignore_permissions = True
	doc.save()
	doc.submit()

	return {
		"name": doc.name,
		"docstatus": cint(doc.docstatus),
	}
