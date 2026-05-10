import base64
import mimetypes
import os

import frappe
from frappe import _
from frappe.model import get_permitted_fields
from frappe.model.workflow import get_workflow_name
from frappe.query_builder import Order
from frappe.utils import add_days, cint, date_diff, getdate, strip_html

from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee

SUPPORTED_FIELD_TYPES = [
	"Link",
	"Select",
	"Small Text",
	"Text",
	"Long Text",
	"Text Editor",
	"Table",
	"Check",
	"Data",
	"Float",
	"Int",
	"Section Break",
	"Date",
	"Time",
	"Datetime",
	"Currency",
]


@frappe.whitelist()
def get_csrf_token() -> str:
	"""返回当前会话的 CSRF token，供移动端等第三方客户端在 POST/PUT/DELETE 请求中携带。

	若会话尚未生成 token，则即时生成并持久化到 session。
	"""
	return frappe.sessions.get_csrf_token()


@frappe.whitelist()
def get_current_user_info() -> dict:
	current_user = frappe.session.user
	user = frappe.db.get_value(
		"User", current_user, ["name", "first_name", "full_name", "user_image"], as_dict=True
	)
	user["roles"] = frappe.get_roles(current_user)

	return user


@frappe.whitelist()
def get_current_employee_info() -> dict:
	current_user = frappe.session.user
	employee = frappe.db.get_value(
		"Employee",
		{"user_id": current_user, "status": "Active"},
		[
			"name",
			"first_name",
			"employee_name",
			"designation",
			"department",
			"company",
			"reports_to",
			"user_id",
		],
		as_dict=True,
	)
	return employee


@frappe.whitelist()
def get_all_employees() -> list[dict]:
	return frappe.get_list(
		"Employee",
		fields=[
			"name",
			"employee_name",
			"designation",
			"department",
			"company",
			"reports_to",
			"user_id",
			"image",
			"status",
		],
		limit=999999,
	)


def get_current_employee() -> str:
	employee = get_current_employee_info().get("name")
	if not employee:
		frappe.throw(_("Employee not found"), frappe.PermissionError)
	return employee


# HR Settings
@frappe.whitelist()
def get_hr_settings() -> dict:
	settings = frappe.db.get_singles_dict("HR Settings", cast=True)
	return frappe._dict(
		allow_employee_checkin_from_mobile_app=settings.allow_employee_checkin_from_mobile_app,
		allow_geolocation_tracking=settings.allow_geolocation_tracking,
	)


# Notifications
@frappe.whitelist()
def get_unread_notifications_count() -> int:
	return frappe.db.count(
		"PWA Notification",
		{"to_user": frappe.session.user, "read": 0},
	)


@frappe.whitelist()
def mark_all_notifications_as_read() -> None:
	frappe.db.set_value(
		"PWA Notification",
		{"to_user": frappe.session.user, "read": 0},
		"read",
		1,
		update_modified=False,
	)


@frappe.whitelist()
def are_push_notifications_enabled() -> bool:
	try:
		return frappe.db.get_single_value("Push Notification Settings", "enable_push_notification_relay")
	except frappe.DoesNotExistError:
		# push notifications are not supported in the current framework version
		return False


# Attendance
@frappe.whitelist()
def get_attendance_calendar_events(from_date: str, to_date: str) -> dict[str, str]:
	employee = get_current_employee()
	holidays = get_holidays_for_calendar(employee, from_date, to_date)
	attendance = get_attendance_for_calendar(employee, from_date, to_date)
	events = {}

	date = getdate(from_date)
	while date_diff(to_date, date) >= 0:
		date_str = date.strftime("%Y-%m-%d")
		if date in attendance:
			events[date_str] = attendance[date]
		elif date in holidays:
			events[date_str] = "Holiday"
		date = add_days(date, 1)

	return events


def get_attendance_for_calendar(employee: str, from_date: str, to_date: str) -> list[dict[str, str]]:
	attendance = frappe.get_all(
		"Attendance",
		{"employee": employee, "attendance_date": ["between", [from_date, to_date]], "docstatus": 1},
		["attendance_date", "status"],
	)
	return {d["attendance_date"]: d["status"] for d in attendance}


def get_holidays_for_calendar(employee: str, from_date: str, to_date: str) -> list[str]:
	if holiday_list := get_holiday_list_for_employee(employee, raise_exception=False):
		return frappe.get_all(
			"Holiday",
			filters={"parent": holiday_list, "holiday_date": ["between", [from_date, to_date]]},
			pluck="holiday_date",
		)

	return []


@frappe.whitelist()
def get_shift_requests(
	employee: str,
	approver_id: str | None = None,
	for_approval: bool = False,
	limit: int | None = None,
) -> list[dict]:
	filters = get_filters("Shift Request", employee, approver_id, for_approval)
	fields = [
		"name",
		"employee",
		"employee_name",
		"shift_type",
		"from_date",
		"to_date",
		"status",
		"approver",
		"docstatus",
		"creation",
	]

	if workflow_state_field := get_workflow_state_field("Shift Request"):
		fields.append(workflow_state_field)

	shift_requests = frappe.get_list(
		"Shift Request",
		fields=fields,
		filters=filters,
		order_by="creation desc",
		limit=limit,
	)

	if workflow_state_field:
		for application in shift_requests:
			application["workflow_state_field"] = workflow_state_field

	return shift_requests


@frappe.whitelist()
def get_attendance_requests(
	employee: str,
	for_approval: bool = False,
	limit: int | None = None,
) -> list[dict]:
	filters = get_filters("Attendance Request", employee, None, for_approval)
	fields = [
		"name",
		"reason",
		"employee",
		"employee_name",
		"from_date",
		"to_date",
		"include_holidays",
		"shift",
		"docstatus",
		"creation",
	]

	if workflow_state_field := get_workflow_state_field("Attendance Request"):
		fields.append(workflow_state_field)

	attendance_requests = frappe.get_list(
		"Attendance Request",
		fields=fields,
		filters=filters,
		order_by="creation desc",
		limit=limit,
	)

	if workflow_state_field:
		for application in attendance_requests:
			application["workflow_state_field"] = workflow_state_field

	return attendance_requests


def get_filters(
	doctype: str,
	employee: str,
	approver_id: str | None = None,
	for_approval: bool = False,
) -> dict:
	filters = frappe._dict()
	if for_approval:
		filters.docstatus = ("in", [0, 1])
		filters.employee = ("!=", employee)

		if workflow := get_workflow(doctype):
			allowed_states = get_allowed_states_for_workflow(workflow, approver_id)
			filters[workflow.workflow_state_field] = ("in", allowed_states)
		elif doctype != "Attendance Request":
			approver_field_map = {
				"Shift Request": "approver",
				"Leave Application": "leave_approver",
				"Expense Claim": "expense_approver",
			}
			filters.status = "Open" if doctype == "Leave Application" else "Draft"
			if approver_id:
				filters[approver_field_map[doctype]] = approver_id
	else:
		filters.docstatus = ("!=", 2)
		filters.employee = employee

	return filters


@frappe.whitelist()
def get_shift_request_approvers(employee: str) -> str | list[str]:
	shift_request_approver, department = frappe.get_cached_value(
		"Employee",
		employee,
		["shift_request_approver", "department"],
	)

	department_approvers = []
	if department:
		department_approvers = get_department_approvers(department, "shift_request_approver")
		if not shift_request_approver:
			shift_request_approver = frappe.db.get_value(
				"Department Approver",
				{"parent": department, "parentfield": "shift_request_approver", "idx": 1},
				"approver",
			)

	shift_request_approver_name = frappe.db.get_value("User", shift_request_approver, "full_name", cache=True)

	if shift_request_approver and shift_request_approver not in [
		approver.name for approver in department_approvers
	]:
		department_approvers.insert(
			0, {"name": shift_request_approver, "full_name": shift_request_approver_name}
		)

	return department_approvers


@frappe.whitelist()
def get_shifts() -> list[dict[str, str]]:
	employee = get_current_employee()
	ShiftAssignment = frappe.qb.DocType("Shift Assignment")
	ShiftType = frappe.qb.DocType("Shift Type")
	return (
		frappe.qb.from_(ShiftAssignment)
		.join(ShiftType)
		.on(ShiftAssignment.shift_type == ShiftType.name)
		.select(
			ShiftAssignment.name,
			ShiftAssignment.shift_type,
			ShiftAssignment.start_date,
			ShiftAssignment.end_date,
			ShiftType.start_time,
			ShiftType.end_time,
		)
		.where(
			(ShiftAssignment.employee == employee)
			& (ShiftAssignment.status == "Active")
			& (ShiftAssignment.docstatus == 1)
		)
		.orderby(ShiftAssignment.start_date, order=Order.asc)
	).run(as_dict=True)


# Leaves and Holidays
@frappe.whitelist()
def get_leave_applications(
	employee: str,
	approver_id: str | None = None,
	for_approval: bool = False,
	limit: int | None = None,
) -> list[dict]:
	filters = get_filters("Leave Application", employee, approver_id, for_approval)
	fields = [
		"name",
		"posting_date",
		"employee",
		"employee_name",
		"leave_type",
		"status",
		"from_date",
		"to_date",
		"half_day",
		"half_day_date",
		"description",
		"total_leave_days",
		"leave_balance",
		"leave_approver",
		"posting_date",
		"creation",
	]

	if workflow_state_field := get_workflow_state_field("Leave Application"):
		fields.append(workflow_state_field)

	applications = frappe.get_list(
		"Leave Application",
		fields=fields,
		filters=filters,
		order_by="posting_date desc",
		limit=limit,
	)

	if workflow_state_field:
		for application in applications:
			application["workflow_state_field"] = workflow_state_field

	return applications


@frappe.whitelist()
def get_leave_balance_map() -> dict[str, dict[str, float]]:
	"""
	Returns a map of leave type and balance details like:
	{
	        'Casual Leave': {'allocated_leaves': 10.0, 'balance_leaves': 5.0},
	        'Earned Leave': {'allocated_leaves': 3.0, 'balance_leaves': 3.0},
	}
	"""
	from hrms.hr.doctype.leave_application.leave_application import get_leave_details

	employee = get_current_employee()

	date = getdate()
	leave_map = {}

	leave_details = get_leave_details(employee, date)
	allocation = leave_details["leave_allocation"]

	for leave_type, details in allocation.items():
		leave_map[leave_type] = {
			"allocated_leaves": details.get("total_leaves"),
			"balance_leaves": details.get("remaining_leaves"),
		}

	return leave_map


@frappe.whitelist()
def get_holidays_for_employee(employee: str) -> list[dict]:
	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return []

	Holiday = frappe.qb.DocType("Holiday")
	holidays = (
		frappe.qb.from_(Holiday)
		.select(Holiday.name, Holiday.holiday_date, Holiday.description)
		.where((Holiday.parent == holiday_list) & (Holiday.weekly_off == 0))
		.orderby(Holiday.holiday_date, order=Order.asc)
	).run(as_dict=True)

	for holiday in holidays:
		holiday["description"] = strip_html(holiday["description"] or "").strip()

	return holidays


@frappe.whitelist()
def get_leave_approval_details(employee: str) -> dict:
	leave_approver, department = frappe.get_cached_value(
		"Employee",
		employee,
		["leave_approver", "department"],
	)

	if not leave_approver and department:
		leave_approver = frappe.db.get_value(
			"Department Approver",
			{"parent": department, "parentfield": "leave_approvers", "idx": 1},
			"approver",
		)

	leave_approver_name = frappe.db.get_value("User", leave_approver, "full_name", cache=True)
	department_approvers = get_department_approvers(department, "leave_approvers")

	if leave_approver and leave_approver not in [approver.name for approver in department_approvers]:
		department_approvers.append({"name": leave_approver, "full_name": leave_approver_name})

	return dict(
		leave_approver=leave_approver,
		leave_approver_name=leave_approver_name,
		department_approvers=department_approvers,
		is_mandatory=frappe.db.get_single_value(
			"HR Settings", "leave_approver_mandatory_in_leave_application"
		),
	)


def get_department_approvers(department: str, parentfield: str) -> list[str]:
	if not department:
		return []

	department_details = frappe.db.get_value("Department", department, ["lft", "rgt"], as_dict=True)
	departments = frappe.get_all(
		"Department",
		filters={
			"lft": ("<=", department_details.lft),
			"rgt": (">=", department_details.rgt),
			"disabled": 0,
		},
		pluck="name",
	)

	Approver = frappe.qb.DocType("Department Approver")
	User = frappe.qb.DocType("User")
	department_approvers = (
		frappe.qb.from_(User)
		.join(Approver)
		.on(Approver.approver == User.name)
		.select(User.name.as_("name"), User.full_name.as_("full_name"))
		.where((Approver.parent.isin(departments)) & (Approver.parentfield == parentfield))
	).run(as_dict=True)

	return department_approvers


@frappe.whitelist()
def get_leave_types(employee: str, date: str) -> list:
	from hrms.hr.doctype.leave_application.leave_application import get_leave_details

	date = date or getdate()

	leave_details = get_leave_details(employee, date)
	leave_types = list(leave_details["leave_allocation"].keys()) + leave_details["lwps"]

	return leave_types


# Expense Claims
@frappe.whitelist()
def get_expense_claims(
	employee: str,
	approver_id: str | None = None,
	for_approval: bool = False,
	limit: int | None = None,
) -> list[dict]:
	filters = get_filters("Expense Claim", employee, approver_id, for_approval)
	fields = [
		"`tabExpense Claim`.name",
		"`tabExpense Claim`.posting_date",
		"`tabExpense Claim`.employee",
		"`tabExpense Claim`.employee_name",
		"`tabExpense Claim`.currency",
		"`tabExpense Claim`.approval_status",
		"`tabExpense Claim`.status",
		"`tabExpense Claim`.expense_approver",
		"`tabExpense Claim`.total_claimed_amount",
		"`tabExpense Claim`.posting_date",
		"`tabExpense Claim`.company",
		"`tabExpense Claim`.docstatus",
		"`tabExpense Claim`.creation",
		"`tabExpense Claim Detail`.expense_type",
		{"COUNT": "`tabExpense Claim Detail`.expense_type", "as": "total_expenses"},
	]

	if workflow_state_field := get_workflow_state_field("Expense Claim"):
		fields.append(workflow_state_field)

	claims = frappe.get_list(
		"Expense Claim",
		fields=fields,
		filters=filters,
		order_by="`tabExpense Claim`.posting_date desc",
		group_by="`tabExpense Claim`.name",
		limit=limit,
	)

	if workflow_state_field:
		for claim in claims:
			claim["workflow_state_field"] = workflow_state_field

	return claims


@frappe.whitelist()
def get_expense_claim_summary() -> dict:
	employee = get_current_employee()

	from frappe.query_builder.functions import Sum

	Claim = frappe.qb.DocType("Expense Claim")

	pending_claims_case = (
		frappe.qb.terms.Case().when(Claim.approval_status == "Draft", Claim.total_claimed_amount).else_(0)
	)
	sum_pending_claims = Sum(pending_claims_case).as_("total_pending_amount")

	approved_claims_case = (
		frappe.qb.terms.Case()
		.when(Claim.approval_status == "Approved", Claim.total_sanctioned_amount)
		.else_(0)
	)
	sum_approved_claims = Sum(approved_claims_case).as_("total_approved_amount")

	approved_total_claimed_case = (
		frappe.qb.terms.Case().when(Claim.approval_status == "Approved", Claim.total_claimed_amount).else_(0)
	)
	sum_approved_total_claimed = Sum(approved_total_claimed_case).as_("total_claimed_in_approved")

	rejected_claims_case = (
		frappe.qb.terms.Case().when(Claim.approval_status == "Rejected", Claim.total_claimed_amount).else_(0)
	)
	sum_rejected_claims = Sum(rejected_claims_case).as_("total_rejected_amount")

	summary = (
		frappe.qb.from_(Claim)
		.select(
			sum_pending_claims,
			sum_approved_claims,
			sum_rejected_claims,
			sum_approved_total_claimed,
			Claim.company,
		)
		.where((Claim.docstatus != 2) & (Claim.employee == employee))
	).run(as_dict=True)[0]

	currency = frappe.db.get_value("Company", summary.company, "default_currency")
	summary["currency"] = currency

	return summary


@frappe.whitelist()
def get_expense_type_description(expense_type: str) -> str:
	return frappe.db.get_value("Expense Claim Type", expense_type, "description")


@frappe.whitelist()
def get_expense_claim_types() -> list[dict]:
	ClaimType = frappe.qb.DocType("Expense Claim Type")

	return (frappe.qb.from_(ClaimType).select(ClaimType.name, ClaimType.description)).run(as_dict=True)


@frappe.whitelist()
def get_expense_approval_details(employee: str) -> dict:
	expense_approver, department = frappe.get_cached_value(
		"Employee",
		employee,
		["expense_approver", "department"],
	)

	if not expense_approver and department:
		expense_approver = frappe.db.get_value(
			"Department Approver",
			{"parent": department, "parentfield": "expense_approvers", "idx": 1},
			"approver",
		)

	expense_approver_name = frappe.db.get_value("User", expense_approver, "full_name", cache=True)
	department_approvers = get_department_approvers(department, "expense_approvers")

	if expense_approver and expense_approver not in [approver.name for approver in department_approvers]:
		department_approvers.append({"name": expense_approver, "full_name": expense_approver_name})

	return dict(
		expense_approver=expense_approver,
		expense_approver_name=expense_approver_name,
		department_approvers=department_approvers,
		is_mandatory=frappe.db.get_single_value("HR Settings", "expense_approver_mandatory_in_expense_claim"),
	)
# Employee Advance
@frappe.whitelist()
def get_employee_advance_balance() -> list[dict]:
	employee = get_current_employee()
	Advance = frappe.qb.DocType("Employee Advance")

	advances = (
		frappe.qb.from_(Advance)
		.select(
			Advance.name,
			Advance.employee,
			Advance.status,
			Advance.purpose,
			Advance.paid_amount,
			(Advance.paid_amount - (Advance.claimed_amount + Advance.return_amount)).as_("balance_amount"),
			Advance.posting_date,
			Advance.currency,
		)
		.where(
			(Advance.docstatus == 1)
			& (Advance.paid_amount)
			& (Advance.employee == employee)
			# don't need claimed & returned advances, only partly or completely paid ones
			& (Advance.status.isin(["Paid", "Unpaid"]))
		)
		.orderby(Advance.posting_date, order=Order.desc)
	).run(as_dict=True)

	return advances


# Company
@frappe.whitelist()
def get_company_currencies() -> dict:
	Company = frappe.qb.DocType("Company")
	Currency = frappe.qb.DocType("Currency")

	query = (
		frappe.qb.from_(Company)
		.join(Currency)
		.on(Company.default_currency == Currency.name)
		.select(
			Company.name,
			Company.default_currency,
			Currency.name.as_("currency"),
			Currency.symbol.as_("symbol"),
		)
	)

	companies = query.run(as_dict=True)
	return {company.name: (company.default_currency, company.symbol) for company in companies}


@frappe.whitelist()
def get_currency_symbols() -> dict:
	Currency = frappe.qb.DocType("Currency")

	currencies = (frappe.qb.from_(Currency).select(Currency.name, Currency.symbol)).run(as_dict=True)

	return {currency.name: currency.symbol or currency.name for currency in currencies}


@frappe.whitelist()
def get_company_cost_center_and_expense_account(company: str) -> dict:
	return frappe.db.get_value(
		"Company", company, ["cost_center", "default_expense_claim_payable_account"], as_dict=True
	)


def _build_account_path_text_for_category(account_name: str | None) -> str:
	"""自末级 Account 沿 parent_account 走到根，拼成一段文本，供 `parse_category` 匹配。

	原实现只拼接了直接父级，若「管理费用」等只在更上层才出现，则匹配失败，全落到「无法识别」。
	"""
	if not (account_name or "").strip():
		return ""

	names: list[str] = []
	current = str(account_name).strip()
	seen: set[str] = set()
	for _ in range(64):
		if not current or current in seen:
			break
		seen.add(current)
		row = frappe.db.get_value(
			"Account",
			current,
			["parent_account", "account_name", "name"],
			as_dict=True,
		)
		if not row:
			break
		label = (row.get("account_name") or row.get("name") or current).strip()
		if label:
			names.append(label)
		parent = (row.get("parent_account") or "").strip()
		if not parent:
			break
		current = parent

	return " ".join(reversed(names))


def _parse_expense_type_category_from_account_path(path: str | None) -> str:
	"""与移动端 `ExpenseCategory` 一致的费用大类；无法归入下列标签时返回「无法识别」。"""
	text = str(path or "")

	# 资产/存货等（顺序：先具体类型）
	if "固定资产" in text:
		return "固定资产"
	if "库存商品" in text:
		return "库存商品"
	if "财务费用" in text:
		return "财务费用"
	if "其他业务成本" in text:
		return "其他业务成本"
	# 销售费用单独成类（与「管理费用」并列），避免都挤在「无法识别」
	if "销售费用" in text:
		return "销售费用"
	# 制造/施工/合同履约/生产成本 等常挂在「制造费用」或成本类下
	if (
		"主营业务成本" in text
		or "制造费用" in text
		or "生产成本" in text
		or "合同履约成本" in text
		or "工程施工" in text
	):
		return "主营业务成本"
	# 管理费用、研发、长期摊销等
	if (
		"管理费用" in text
		or "研发支出" in text
		or "研究支出" in text
		or "开发支出" in text
		or "长期待摊" in text
	):
		return "管理费用"
	return "无法识别"


@frappe.whitelist()
def get_expense_claim_type_category_map(company: str | None = None) -> dict[str, str]:
	"""返回 Expense Claim Type 到费用大类的映射。

	移动端需要这个接口把报销类型归到固定资产、管理费用等大类中。
	"""
	def row_value(row, key: str) -> str:
		if isinstance(row, dict):
			return row.get(key)
		return getattr(row, key, None)

	types = frappe.get_all("Expense Claim Type", fields=["name"])
	if not types:
		return {}

	if not company:
		return {row_value(row, "name"): "无法识别" for row in types if row_value(row, "name")}

	accounts = frappe.get_all(
		"Expense Claim Account",
		filters={"company": company},
		fields=["parent", "default_account"],
	)
	account_by_type = {
		row_value(row, "parent"): row_value(row, "default_account")
		for row in accounts
		if row_value(row, "parent")
	}

	path_cache: dict[str, str] = {}

	mapping: dict[str, str] = {}
	for row in types:
		row_name = str(row_value(row, "name") or "").strip()
		type_name = row_name
		# 报销类型若本身就是「无法识别」，固定放到同名大类，避免混入主营业务成本等分组。
		if type_name == "无法识别":
			mapping[row_name] = "无法识别"
			continue
		account_name = account_by_type.get(row_name)
		if not account_name:
			mapping[row_name] = "无法识别"
			continue
		if not frappe.db.exists("Account", account_name):
			mapping[row_name] = "无法识别"
			continue
		if account_name not in path_cache:
			path_cache[account_name] = _build_account_path_text_for_category(account_name)
		path = path_cache[account_name]
		mapping[row_name] = _parse_expense_type_category_from_account_path(path)

	return mapping


# Form View APIs
@frappe.whitelist()
def get_doctype_fields(doctype: str) -> list[dict]:
	fields = frappe.get_meta(doctype).fields
	return [
		field
		for field in fields
		if field.fieldtype in SUPPORTED_FIELD_TYPES and field.fieldname != "amended_from"
	]


@frappe.whitelist()
def get_doctype_states(doctype: str) -> dict:
	states = frappe.get_meta(doctype).states
	return {state.title: state.color.lower() for state in states}


# File
@frappe.whitelist()
def get_attachments(dt: str, dn: str):
	return frappe.get_list(
		"File",
		fields=["name", "file_name", "file_url", "is_private", "folder"],
		filters={"attached_to_name": str(dn), "attached_to_doctype": dt},
		order_by="creation asc",
	)


@frappe.whitelist()
def get_file_content_base64(name: str) -> dict:
	"""按「File」文档的 name 返回 base64 内容，供 App/小程序在 downloadFile 无法带 Cookie 时打开 PDF 等。

	大小上限约 20MB，避免内存压力。
	"""
	limit = 20 * 1024 * 1024
	name = (name or "").strip()
	if not name:
		frappe.throw(_("请提供附件 File 的 name"))
	try:
		file_doc = frappe.get_doc("File", name)
	except Exception:
		frappe.throw(_("附件不存在"))
	if cint(getattr(file_doc, "is_folder", 0)):
		frappe.throw(_("不是文件"))
	if not frappe.has_permission("File", "read", doc=file_doc):
		frappe.throw(_("无权访问该文件"))
	# 远程/外链不读盘
	if file_doc.is_remote_file:
		return {
			"is_remote": True,
			"file_url": file_doc.file_url or "",
			"file_name": file_doc.file_name or "file",
		}
	fpath = file_doc.get_full_path()
	if not fpath or not os.path.exists(fpath):
		frappe.throw(_("文件在服务器上不存在"))
	fsize = os.path.getsize(fpath)
	if fsize > limit:
		frappe.throw(_("文件过大，请在电脑端打开"))
	with open(fpath, "rb") as f:
		raw = f.read()
	mime, _ = mimetypes.guess_type(file_doc.file_name or "")
	return {
		"is_remote": False,
		"file_name": file_doc.file_name or "file",
		"mime": mime or "application/octet-stream",
		"content_base64": base64.b64encode(raw).decode("ascii"),
		"size": fsize,
	}


@frappe.whitelist()
def get_user_full_names(user_ids: str | list[str] | None = None) -> dict[str, str]:
	"""批量返回用户显示姓名。

	前端需要展示审批轨迹中的姓名时，通过这个接口查询，避免直接访问 User 资源导致权限错误。
	"""
	if isinstance(user_ids, str):
		try:
			import json

			ids = json.loads(user_ids)
		except Exception:
			ids = [user_ids]
	elif isinstance(user_ids, list):
		ids = user_ids
	else:
		ids = []

	clean_ids = [str(user_id).strip() for user_id in ids if str(user_id).strip()]
	if not clean_ids:
		return {}

	rows = frappe.get_all(
		"User",
		fields=["name", "full_name", "username"],
		filters={"name": ("in", clean_ids)},
	)
	return {
		row.name: (row.full_name or row.username or row.name)
		for row in rows
	}


@frappe.whitelist()
def upload_base64_file(content, filename, dt=None, dn=None, fieldname=None):
	import base64
	import io
	from mimetypes import guess_type

	from PIL import Image, ImageOps

	from frappe.handler import ALLOWED_MIMETYPES

	decoded_content = base64.b64decode(content)
	content_type = guess_type(filename)[0]
	if content_type not in ALLOWED_MIMETYPES:
		frappe.throw(_("You can only upload JPG, PNG, PDF, TXT or Microsoft documents."))

	if content_type.startswith("image/jpeg"):
		# transpose the image according to the orientation tag, and remove the orientation data
		with Image.open(io.BytesIO(decoded_content)) as image:
			transpose_img = ImageOps.exif_transpose(image)
			# convert the image back to bytes
			file_content = io.BytesIO()
			transpose_img.save(file_content, format="JPEG")
			file_content = file_content.getvalue()
	else:
		file_content = decoded_content

	return frappe.get_doc(
		{
			"doctype": "File",
			"attached_to_doctype": dt,
			"attached_to_name": dn,
			"attached_to_field": fieldname,
			"folder": "Home",
			"file_name": filename,
			"content": file_content,
			"is_private": 1,
		}
	).insert()


@frappe.whitelist()
def delete_attachment(filename: str):
	frappe.delete_doc("File", filename)


@frappe.whitelist()
def _download_pdf(doctype: str, docname: str) -> str:
	import base64

	from frappe.utils.print_format import download_pdf

	default_print_format = frappe.get_meta(doctype).default_print_format or "Standard"

	try:
		download_pdf(doctype, docname, format=default_print_format)
	except Exception as e:
		frappe.throw(_("Failed to download PDF: {0}").format(str(e)))

	base64content = base64.b64encode(frappe.local.response.filecontent)
	content_type = frappe.local.response.type

	return f"data:{content_type};base64," + base64content.decode("utf-8")


# Workflow
@frappe.whitelist()
def get_workflow(doctype: str) -> dict:
	workflow = get_workflow_name(doctype)
	if not workflow:
		return frappe._dict()
	return frappe.get_doc("Workflow", workflow)


def get_workflow_state_field(doctype: str) -> str | None:
	workflow_name = get_workflow_name(doctype)
	if not workflow_name:
		return None

	override_status, workflow_state_field = frappe.db.get_value(
		"Workflow",
		workflow_name,
		["override_status", "workflow_state_field"],
	)
	# NOTE: checkbox labelled 'Don't Override Status' is named override_status hence the inverted logic
	if not override_status:
		return workflow_state_field
	return None


def get_allowed_states_for_workflow(workflow: dict, user_id: str) -> list[str]:
	user_roles = frappe.get_roles(user_id)
	return [transition.state for transition in workflow.transitions if transition.allowed in user_roles]


# Permissions
@frappe.whitelist()
def get_permitted_fields_for_write(doctype: str) -> list[str]:
	return get_permitted_fields(doctype, permission_type="write")


@frappe.whitelist()
def ensure_expense_claim_folders():
	paths = [
		(None, "Home"),
		("Home", "Attachments"),
		("Home/Attachments", "Expense Claim"),
		("Home/Attachments/Expense Claim", "单据图片"),
		("Home/Attachments/Expense Claim", "实物图片")
	]
	for parent, name in paths:
		if not frappe.db.exists("File", {"file_name": name, "is_folder": 1, "folder": parent}):
			frappe.get_doc({
				"doctype": "File",
				"file_name": name,
				"is_folder": 1,
				"folder": parent
			}).insert(ignore_permissions=True)
	frappe.db.commit()
	return "Folders ensured"

@frappe.whitelist(methods=["POST"])
def classify_expense_text(text_content: str, category_hierarchy: str, model: str | None = None) -> dict:
	"""
	基于纯文本内容对费用进行 AI 分类。无需图片，速度更快，成本更低。
	配置见项目根目录 receipt_recognition.json（text_classification 段）。
	"""
	import json
	import requests as http_requests
	from requests.exceptions import RequestException

	from hrms.receipt_recognition_settings import (
		build_classification_prompt,
		classification_http_headers,
		get_receipt_ai_config,
	)

	if not text_content:
		return {"报销类型": "无法识别"}

	cfg = get_receipt_ai_config()
	tc = cfg.get("text_classification") or {}
	va = cfg.get("vision_api") or {}
	fallback = (
		tc.get("fallback_response")
		if isinstance(tc.get("fallback_response"), dict)
		else {"报销类型": "无法识别"}
	)

	endpoint = str(tc.get("endpoint") or va.get("endpoint") or "").strip()
	if not endpoint:
		return fallback

	headers = classification_http_headers(cfg)
	if "Authorization" not in headers:
		return fallback

	ai_model = (model or "").strip() or str(tc.get("model_default") or "hy3-preview").strip()
	prompt = build_classification_prompt(cfg, text_content, category_hierarchy)

	payload = {
		"model": ai_model,
		"messages": [{"role": "user", "content": prompt}],
		"temperature": float(tc.get("temperature") if tc.get("temperature") is not None else 0.1),
		"stream": False,
	}
	if tc.get("use_response_format_json_object"):
		payload["response_format"] = {"type": "json_object"}
	payload_str = json.dumps(payload, ensure_ascii=False)

	timeout = int(tc.get("timeout_seconds") or 30)

	try:
		response = http_requests.post(endpoint, headers=headers, data=payload_str.encode("utf-8"), timeout=timeout)
		response.raise_for_status()
		res_data = response.json()
		content = res_data["choices"][0]["message"]["content"]
		return json.loads(content)
	except Exception as e:
		frappe.log_error(f"Classification Error: {e}", "Expense Classification Failure")
		return fallback

# Receipt Image Recognition
@frappe.whitelist(methods=["POST"])
def recognize_receipt_image(**kwargs):
	"""
	通过后端代理调用 AI 模型进行报销票据图片识别。
	支持 OpenAI 兼容格式；远程网关与提示词见项目根目录 receipt_recognition.json。
	图片通过 Base64 编码在 JSON body 中发送；PDF 请使用 recognize_receipt_file。
	"""
	image_base64 = kwargs.get("image_base64")
	model = kwargs.get("model")
	server_url = kwargs.get("server_url")
	category_hierarchy = kwargs.get("category_hierarchy")
	file_type = kwargs.get("file_type") or "image"

	if not image_base64:
		frappe.throw("识别请求失败：未收到图片数据 (image_base64)")

	return _do_recognize_receipt(image_base64, file_type, model, server_url, category_hierarchy)


def _generate_pdf_thumbnail(file_data, width=300):
	"""将 PDF 第一页渲染为 JPEG 缩略图，返回 base64 编码字符串。"""
	import base64
	import io

	try:
		import fitz
	except ImportError:
		frappe.log_error("PyMuPDF (fitz) 未安装，无法生成 PDF 缩略图", "PDF Thumbnail")
		return ""

	try:
		doc = fitz.open(stream=file_data, filetype="pdf")
		if not doc.page_count:
			doc.close()
			frappe.log_error("PDF 文件无页面", "PDF Thumbnail")
			return ""
		page = doc.load_page(0)
		zoom = width / (page.rect.width or 612)
		mat = fitz.Matrix(zoom, zoom)
		pix = page.get_pixmap(matrix=mat)
		img_bytes = pix.tobytes("jpeg")
		doc.close()
		thumbnail = base64.b64encode(img_bytes).decode("utf-8")
		return thumbnail
	except Exception as e:
		frappe.log_error(f"PDF 缩略图生成失败: {e}", "PDF Thumbnail")
		return ""


@frappe.whitelist()
def get_pdf_thumbnail(file_url):
	"""根据已上传的 PDF 文件 URL 生成并返回第一页缩略图的 base64。"""
	import base64

	if not file_url:
		frappe.throw("缺少 file_url 参数")

	file_url = str(file_url).strip()
	if file_url.startswith(("http://", "https://")):
		import requests as http_requests
		try:
			resp = http_requests.get(file_url, timeout=15)
			resp.raise_for_status()
			file_data = resp.content
		except Exception:
			frappe.throw("无法下载文件")
	else:
		file_path = frappe.get_site_path("public", file_url.lstrip("/"))
		if not os.path.exists(file_path):
			private_path = frappe.get_site_path("private", file_url.lstrip("/"))
			if os.path.exists(private_path):
				file_path = private_path
			else:
				frappe.throw("文件不存在")
		with open(file_path, "rb") as f:
			file_data = f.read()

	thumbnail = _generate_pdf_thumbnail(file_data)
	if not thumbnail:
		frappe.throw("无法生成 PDF 缩略图")
	return {"thumbnail_base64": thumbnail}


# Receipt File Recognition (upload-based, for large PDFs)
@frappe.whitelist(methods=["POST"])
def recognize_receipt_file(**kwargs):
	"""
	通过文件上传方式接收票据文件（主要支持 PDF），调用远程 AI 网关识别。
	解决大文件通过 JSON POST 发送 Base64 时超出请求体限制的问题。
	网关与提示词见项目根目录 receipt_recognition.json。
	"""
	import base64
	import json

	from frappe.handler import ALLOWED_MIMETYPES

	file_type = kwargs.get("file_type") or "image"
	model = kwargs.get("model")
	server_url = kwargs.get("server_url")
	category_hierarchy = kwargs.get("category_hierarchy")

	uploaded = frappe.request.files.get("file")
	if not uploaded:
		frappe.throw("未收到文件")

	filename = uploaded.filename or ""
	content_type = uploaded.content_type or ""

	if content_type and content_type not in ALLOWED_MIMETYPES:
		frappe.throw(f"不支持的文件类型：{content_type}")

	file_data = uploaded.stream.read()
	if not file_data:
		frappe.throw("文件内容为空")

	pure_base64 = base64.b64encode(file_data).decode("utf-8")

	if content_type == "application/pdf":
		file_type = "pdf"

	result = _do_recognize_receipt(pure_base64, file_type, model, server_url, category_hierarchy)

	if file_type == "pdf":
		thumbnail_base64 = _generate_pdf_thumbnail(file_data)
		if isinstance(result, dict):
			result["thumbnail_base64"] = thumbnail_base64 or ""
		elif isinstance(result, str):
			try:
				parsed = json.loads(result)
				parsed["thumbnail_base64"] = thumbnail_base64 or ""
				result = parsed
			except:
				result = {"message": result, "thumbnail_base64": thumbnail_base64 or ""}

	return result


def _extract_braced_json_fragments(text: str) -> list:
	"""从可能含前后缀的文本中提取最外层 {...} 片段（与前端 OCR 解析策略一致）。"""
	result = []
	start = -1
	depth = 0
	in_string = False
	escape = False
	for i, ch in enumerate(text):
		if in_string:
			if escape:
				escape = False
				continue
			if ch == "\\":
				escape = True
				continue
			if ch == '"':
				in_string = False
			continue
		if ch == '"':
			in_string = True
			continue
		if ch == "{":
			if depth == 0:
				start = i
			depth += 1
			continue
		if ch == "}":
			if depth <= 0:
				continue
			depth -= 1
			if depth == 0 and start >= 0:
				result.append(text[start : i + 1])
				start = -1
	return result


def _parse_json_from_model_content(content) -> dict | list:
	"""解析视觉模型 message.content：整段 JSON、markdown 代码块、或文本中的 JSON 对象。"""
	import json
	import re

	if content is None:
		raise ValueError("empty content")
	s = str(content).strip()
	m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, flags=re.IGNORECASE)
	if m:
		s = m.group(1).strip()
	try:
		return json.loads(s)
	except ValueError:
		pass
	for frag in _extract_braced_json_fragments(s):
		try:
			obj = json.loads(frag)
			if isinstance(obj, (dict, list)):
				return obj
		except ValueError:
			continue
	raise ValueError("模型返回内容中未找到有效 JSON")


def _do_recognize_receipt(image_base64, file_type="image", model=None, server_url=None, category_hierarchy=None):
	"""共用识别逻辑，被 recognize_receipt_image 和 recognize_receipt_file 调用。
	统一使用 OpenAI 兼容格式（远程网关）。配置见项目根目录 receipt_recognition.json。
	"""
	import json
	import requests as http_requests
	from requests.exceptions import RequestException

	from hrms.receipt_recognition_settings import (
		build_receipt_vision_prompt,
		get_receipt_ai_config,
		vision_http_headers,
	)

	if not image_base64:
		frappe.throw("识别请求失败：未收到数据")

	cfg = get_receipt_ai_config()
	va = cfg.get("vision_api") or {}
	media = cfg.get("media") or {}
	pd = cfg.get("payload_defaults") or {}

	is_pdf = file_type == "pdf"
	doc_word = "PDF 文件" if is_pdf else "图片"
	prompt = build_receipt_vision_prompt(cfg, doc_word, category_hierarchy or None)

	if "," in image_base64:
		pure_base64 = image_base64.split(",")[1]
	else:
		pure_base64 = image_base64

	if is_pdf:
		mime = str(media.get("pdf_data_url_mime") or "application/pdf").strip()
	else:
		mime = str(media.get("image_data_url_mime") or "image/jpeg").strip()
	image_url = f"data:{mime};base64,{pure_base64}"

	resolved_model = (model or "").strip() or str(va.get("model_default") or "").strip()
	if not resolved_model:
		frappe.throw("未配置视觉识别模型：请在 receipt_recognition.json 的 vision_api.model_default 或请求参数 model 中设置")

	endpoint = (server_url or "").strip() or str(va.get("endpoint") or "").strip()
	if not endpoint:
		frappe.throw(
			"未配置视觉识别远程地址 vision_api.endpoint（项目根目录 receipt_recognition.json 或站点 receipt_recognition / hunyuan_endpoint）"
		)

	headers = vision_http_headers(cfg)
	if "Authorization" not in headers:
		frappe.throw(
			"未配置 vision_api.api_key（项目根目录 receipt_recognition.json、站点 receipt_recognition.vision_api.api_key 或兼容项 hunyuan_api_key）"
		)

	timeout = int(va.get("timeout_seconds") or 120)
	temperature = float(va.get("temperature") if va.get("temperature") is not None else 0.2)
	stream = bool(pd.get("stream")) if "stream" in pd else False

	payload = {
		"model": resolved_model,
		"messages": [
			{
				"role": "user",
				"content": [
					{"type": "text", "text": prompt},
					{"type": "image_url", "image_url": {"url": image_url}},
				],
			}
		],
		"temperature": temperature,
		"stream": stream,
	}
	payload_str = json.dumps(payload, ensure_ascii=False)

	try:
		response = http_requests.post(endpoint, headers=headers, data=payload_str.encode("utf-8"), timeout=timeout)
		response.raise_for_status()
		res_data = response.json()

		try:
			content = res_data["choices"][0]["message"]["content"]
			parsed = _parse_json_from_model_content(content)
			if isinstance(parsed, list):
				if parsed and all(isinstance(x, dict) for x in parsed):
					parsed = {"detail_list": parsed}
				else:
					frappe.throw("视觉模型返回的 JSON 数组格式无效")
			if not isinstance(parsed, dict):
				frappe.throw("视觉模型应返回 JSON 对象或明细对象数组")
			return parsed
		except (KeyError, IndexError, ValueError) as e:
			frappe.log_error(f"Vision Model Parsing Error: {e}\nRaw: {res_data}", "Receipt Recognition Failure")
			frappe.throw("视觉模型返回内容解析失败：" + str(e))

	except RequestException as exc:
		frappe.throw(f"连接视觉识别服务失败：{exc}")


@frappe.whitelist()
def withdraw_expense_claim_to_draft(name: str):
	"""将待审批的报销单撤回到草稿，供申请人修改后重新提交。"""
	if not name:
		frappe.throw("缺少报销单编号")

	doc = frappe.get_doc("Expense Claim", name)
	doc.check_permission("write")

	if frappe.session.user != "Administrator" and doc.owner != frappe.session.user:
		frappe.throw("只有单据创建人可以撤回修改")

	if getattr(doc, "docstatus", 0) != 0:
		frappe.throw("只有未入账的待审批报销单才能撤回修改")

	if getattr(doc, "custom_related_journal_entry", None) or getattr(doc, "custom_related_payment_entry", None):
		frappe.throw("该单据已关联财务凭证，无法撤回修改")

	values = {
		"workflow_state": "Draft",
		"approval_status": "Draft",
		"custom_peer_verifier": "",
		"custom_peer_verified_on": None,
		"custom_dept_head_approver": "",
		"custom_dept_head_approved_on": None,
		"custom_finance_approver": "",
		"custom_finance_approved_on": None,
		"custom_gm_approver": "",
		"custom_gm_approved_on": None,
	}
	frappe.db.set_value("Expense Claim", name, values, update_modified=True)

	from frappe.workflow.doctype.workflow_action.workflow_action import clear_workflow_actions

	clear_workflow_actions("Expense Claim", name)
	frappe.db.commit()

	return frappe.get_doc("Expense Claim", name).as_dict()


@frappe.whitelist(methods=["POST"])
def update_expense_claim_detail_type(claim_name: str, detail_name: str, expense_type: str) -> dict:
	"""财务审核前，允许财务审批人修正明细费用分类。"""
	claim_name = (claim_name or "").strip()
	detail_name = (detail_name or "").strip()
	expense_type = (expense_type or "").strip()
	if not claim_name or not detail_name or not expense_type:
		frappe.throw("参数不完整")

	claim = frappe.get_doc("Expense Claim", claim_name)
	claim.check_permission("write")
	if getattr(claim, "docstatus", 0) != 0:
		frappe.throw("仅未提交单据允许修改分类")
	if (getattr(claim, "workflow_state", "") or "").strip() != "Pending Finance Approval":
		frappe.throw("仅待财务审核阶段允许修改分类")
	if "Finance Approver" not in frappe.get_roles(frappe.session.user):
		frappe.throw("仅财务审批人允许修改分类")
	if not frappe.db.exists("Expense Claim Type", expense_type):
		frappe.throw("费用分类不存在")

	target_row = None
	for row in claim.get("expenses") or []:
		if row.name == detail_name:
			target_row = row
			break
	if not target_row:
		frappe.throw("未找到对应的报销明细")

	target_row.expense_type = expense_type
	target_row.default_account = None
	claim.set_expense_account(validate=False)
	claim.calculate_total_amount()
	claim.calculate_taxes()
	claim.flags.skip_ai_category_classification = True
	claim.save()
	claim.publish_update()

	return {"name": claim.name, "detail_name": detail_name, "expense_type": expense_type}


@frappe.whitelist(methods=["POST"])
def reclassify_expense_claim_detail_type_by_ai(claim_name: str, detail_name: str) -> dict:
	"""财务审核前，对单条报销明细执行 AI 重新分类。"""
	claim_name = (claim_name or "").strip()
	detail_name = (detail_name or "").strip()
	if not claim_name or not detail_name:
		frappe.throw("参数不完整")

	claim = frappe.get_doc("Expense Claim", claim_name)
	claim.check_permission("write")
	if getattr(claim, "docstatus", 0) != 0:
		frappe.throw("仅未提交单据允许重新分类")
	if (getattr(claim, "workflow_state", "") or "").strip() != "Pending Finance Approval":
		frappe.throw("仅待财务审核阶段允许重新分类")
	if "Finance Approver" not in frappe.get_roles(frappe.session.user):
		frappe.throw("仅财务审批人允许重新分类")

	target_row = None
	for row in claim.get("expenses") or []:
		if row.name == detail_name:
			target_row = row
			break
	if not target_row:
		frappe.throw("未找到对应的报销明细")

	allowed_types = [
		row.name
		for row in frappe.get_all("Expense Claim Type", fields=["name"])
		if (row.name or "").strip() != "无法识别"
	]
	if not allowed_types:
		frappe.throw("未配置报销分类")

	type_category_map = get_expense_claim_type_category_map(claim.company)
	grouped: dict[str, list[str]] = {}
	for expense_type in allowed_types:
		category = type_category_map.get(expense_type) or "无法识别"
		grouped.setdefault(category, []).append(expense_type)

	lines = ["【可选报销类型层级结构】"]
	for category, types in grouped.items():
		lines.append(f"- {category}:")
		for expense_type in types:
			lines.append(f"  - {expense_type}")
	hierarchy_text = "\n".join(lines)

	text_to_classify = f"单据名称: {target_row.custom_receipt_item_name or ''}, 说明: {target_row.description or ''}"
	if not text_to_classify.replace("单据名称: , 说明: ", "").strip():
		frappe.throw("该条明细缺少可用于分类的文本内容")

	response = classify_expense_text(
		text_content=text_to_classify,
		category_hierarchy=hierarchy_text,
	)
	predicted_type = str((response or {}).get("报销类型") or "").strip()
	if not predicted_type or predicted_type == "无法识别":
		frappe.throw("AI 未识别出有效分类，请手动选择")
	if predicted_type not in allowed_types:
		frappe.throw("AI 返回的分类不在可选范围内，请手动选择")

	target_row.expense_type = predicted_type
	target_row.default_account = None
	claim.set_expense_account(validate=False)
	claim.calculate_total_amount()
	claim.calculate_taxes()
	claim.flags.skip_ai_category_classification = True
	claim.save()
	claim.publish_update()

	return {"name": claim.name, "detail_name": detail_name, "expense_type": predicted_type}

@frappe.whitelist()
def sync_bank_accounts_to_mop():
	"""根据会计科目表同步生成简洁中文名称的付款方式，并汉化现有项目。"""
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		return "未找到默认公司"

	# 1. 汉化系统默认的常见付款方式
	translations = {
		"Wire Transfer": "银行转账",
		"Cash": "现金支付",
		"Credit Card": "信用卡",
		"Check": "支票"
	}
	for old_name, new_name in translations.items():
		if frappe.db.exists("Mode of Payment", old_name):
			frappe.rename_doc("Mode of Payment", old_name, new_name, force=True, merge=True)

	# 2. 从会计科目同步
	bank_accounts = frappe.get_all("Account", 
		filters={"account_type": ["in", ["Bank", "Cash"]], "is_group": 0, "company": company},
		fields=["name", "account_type", "account_name"]
	)

	results = []
	for acc in bank_accounts:
		# 清洗名称：移除 "1001 - " 这种前缀，以及 " - 华烁" 这种公司后缀
		raw_name = acc.account_name
		clean_name = raw_name.split(" - ")[1] if " - " in raw_name else raw_name
		clean_name = clean_name.replace(" - 华烁", "").strip()
		
		if not frappe.db.exists("Mode of Payment", clean_name):
			doc = frappe.new_doc("Mode of Payment")
			doc.mode_of_payment = clean_name
			doc.type = acc.account_type
			doc.enabled = 1
			doc.insert(ignore_permissions=True)
		
		mop_doc = frappe.get_doc("Mode of Payment", clean_name)
		mop_doc.set("accounts", [])
		mop_doc.append("accounts", {
			"company": company,
			"default_account": acc.name
		})
		mop_doc.save(ignore_permissions=True)
		results.append(f"{clean_name} -> {acc.name}")

	frappe.db.commit()
	return results


@frappe.whitelist()
def get_cashier_modes_of_payment(company: str | None = None) -> list[dict]:
	"""获取出纳可用的付款方式及其关联科目。"""
	if not company:
		company = frappe.db.get_single_value("Global Defaults", "default_company")

	mops = frappe.get_all(
		"Mode of Payment",
		filters={"enabled": 1},
		fields=["name", "type"],
		order_by="name asc"
	)
	
	res = []
	for mop in mops:
		account = frappe.db.get_value("Mode of Payment Account", 
			{"parent": mop.name, "company": company}, 
			"default_account"
		)
		res.append({
			"name": mop.name,
			"type": mop.type,
			"account": account or ""
		})
	return res

@frappe.whitelist(methods=["POST"])
def confirm_expense_claim_payment(claim_name: str, mop: str, advances: list | str = None, action: str = None) -> dict:
	"""出纳确认付款：一次性更新付款方式、预付款抵扣并执行工作流动作。"""
	if isinstance(advances, str) and advances.strip():
		import json
		try:
			advances = json.loads(advances)
		except Exception:
			advances = []
	
	if not advances or not isinstance(advances, list):
		advances = []

	doc = frappe.get_doc("Expense Claim", claim_name)
	
	# 权限校验
	user_roles = frappe.get_roles(frappe.session.user)
	allowed_roles = ["Cashier", "Administrator", "Finance Approver", "出纳", "财务人员", "财务审核"]
	
	has_permission = any(role in user_roles for role in allowed_roles)
	
	if not has_permission:
		frappe.logger().warning(f"Permission Denied for {frappe.session.user}: Roles found {user_roles}")
		frappe.throw("只有出纳或财务人员允许执行此操作", frappe.PermissionError)

	# 1. 更新付款方式相关字段
	doc.custom_cashier_mode_of_payment = mop
	doc.custom_cashier_user = frappe.session.user
	doc.custom_paid_on = frappe.utils.now_datetime()
	
	# 2. 更新预付款子表
	# 允许在提交后更新预付款抵扣额（出纳确认时可能需要微调）
	doc.set("advances", [])
	for row in advances:
		if not row.get("employee_advance"):
			continue
		doc.append("advances", {
			"employee_advance": row.get("employee_advance"),
			"allocated_amount": row.get("allocated_amount"),
			"advance_amount": row.get("advance_amount"),
			"unclaimed_amount": row.get("unclaimed_amount")
		})
	
	# 3. 执行工作流动作
	# 绕过提交后的修改限制（针对预付款子表）
	doc.flags.ignore_validate_update_after_submit = True
	
	from frappe.workflow.doctype.workflow_action.workflow_action import apply_workflow
	apply_workflow(doc, action)
	
	return {"name": doc.name, "workflow_state": doc.workflow_state}
