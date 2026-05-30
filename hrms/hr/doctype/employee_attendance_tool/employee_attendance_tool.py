# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import datetime
import json

import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class EmployeeAttendanceTool(Document):
	pass


@frappe.whitelist()
def get_employees(
	date: str | datetime.date,
	department: str | None = None,
	branch: str | None = None,
	company: str | None = None,
	employment_type: str | None = None,
	designation: str | None = None,
	employee_grade: str | None = None,
	shift: str | None = None,
	filter_by_shift: bool | None = None,
) -> dict[str, list]:
	filters = {"status": "Active", "date_of_joining": ["<=", date]}

	for field, value in {
		"department": department,
		"branch": branch,
		"company": company,
		"employment_type": employment_type,
		"designation": designation,
		"grade": employee_grade,
	}.items():
		if value:
			filters[field] = value
	# list of all employees
	employee_list = frappe.get_list(
		"Employee",
		fields=["employee", "employee_name"],
		filters=filters,
		order_by="employee_name",
	)
	# marked attendance
	attendance_list = frappe.get_list(
		"Attendance",
		fields=["employee", "employee_name", "status", "shift", "leave_type"],
		filters={
			"attendance_date": date,
			"docstatus": 1,
			"modify_half_day_status": 0,
		},
		order_by="employee_name",
	)
	half_day_attendance_list = frappe.get_list(
		"Attendance",
		fields=["employee", "employee_name"],
		filters={
			"attendance_date": date,
			"docstatus": 1,
			"modify_half_day_status": 1,
			"leave_type": ("is", "set"),
		},
		order_by="employee_name",
	)
	unmarked_attendance = _get_unmarked_attendance(
		employee_list, [*attendance_list, *half_day_attendance_list]
	)
	if filter_by_shift:
		unmarked_attendance = _get_unmarked_attendance_with_shift(unmarked_attendance, shift, date)
	return {
		"marked": attendance_list,
		"half_day_marked": half_day_attendance_list,
		"unmarked": unmarked_attendance,
	}


def _get_unmarked_attendance(employee_list: list[dict], attendance_list: list[dict]) -> list[dict]:
	marked_employees = [entry.employee for entry in attendance_list]
	unmarked_attendance = []

	for entry in employee_list:
		if entry.employee not in marked_employees:
			unmarked_attendance.append(entry)

	return unmarked_attendance


def _get_unmarked_attendance_with_shift(unmarked_attendance, shift, date):
	# Fetch employees based on Shift Assignment
	shift_assigned_employees = frappe.get_list(
		"Shift Assignment",
		filters={
			"shift_type": shift,
			"start_date": ["<=", frappe.utils.getdate(date)],
		},
		fields=["employee"],
	)
	# Fetch employees based on default shifts
	default_shift_employees = frappe.get_list(
		"Employee",
		filters={
			"default_shift": shift,
		},
		fields=["employee"],
	)

	all_employees_with_shift = shift_assigned_employees + default_shift_employees
	distinct_employees_with_shift = {emp["employee"] for emp in all_employees_with_shift}

	# Filter unmarked attendance based on assigned employees
	shiftwise_unmarked_attendance = []
	for emp in unmarked_attendance:
		if emp["employee"] in distinct_employees_with_shift:
			shiftwise_unmarked_attendance.append(emp)

	return shiftwise_unmarked_attendance


@frappe.whitelist()
def get_attendance_by_project(
	project: str,
	date: str | datetime.date,
) -> list[dict]:
	"""按项目+日期查询考勤记录（绕过 REST API 对自定义字段的过滤限制）"""
	if not frappe.db.has_column("Attendance", "project"):
		return []
	return frappe.get_all(
		"Attendance",
		fields=["name", "employee", "employee_name", "status", "leave_type", "shift", "docstatus"],
		filters={
			"project": project,
			"attendance_date": getdate(date),
			"docstatus": ["!=", 2],
		},
		order_by="creation desc",
		limit_page_length=500,
	)


@frappe.whitelist()
def get_monthly_attendance_by_project(
	project: str,
	month: str,
) -> list[dict]:
	"""按项目+月份查询考勤记录（绕过 REST API 对自定义字段的过滤限制）"""
	if not frappe.db.has_column("Attendance", "project"):
		return []
	from datetime import datetime
	year, mo = map(int, month.split("-"))
	start = datetime(year, mo, 1)
	if mo == 12:
		end = datetime(year + 1, 1, 1)
	else:
		end = datetime(year, mo + 1, 1)
	# 减一天得到当月最后一天
	from datetime import timedelta
	end = end - timedelta(days=1)
	from frappe.utils import getdate as _getdate
	return frappe.get_all(
		"Attendance",
		fields=[
			"name", "employee", "employee_name", "attendance_date",
			"status", "leave_type", "in_time", "out_time", "working_hours",
			"shift", "late_entry", "early_exit", "project",
		],
		filters={
			"project": project,
			"attendance_date": ["between", [_getdate(start), _getdate(end)]],
			"docstatus": ["!=", 2],
		},
		order_by="attendance_date asc, creation asc",
		limit_page_length=0,
	)


def _clean_existing_attendance(employee: str, attendance_date) -> None:
	"""取消已提交、删除草稿的同日考勤记录，避免 DuplicateAttendance / OverlappingShift 冲突。

	主要用于借调员工场景：员工原部门已有当日考勤时仍能在新项目下重新登记。
	"""
	existing = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": attendance_date,
			"docstatus": ["!=", 2],
		},
		fields=["name", "docstatus"],
	)
	for rec in existing:
		try:
			if rec.docstatus == 1:
				doc = frappe.get_doc("Attendance", rec.name)
				doc.flags.ignore_permissions = True
				doc.cancel()
			else:
				frappe.delete_doc(
					"Attendance",
					rec.name,
					force=True,
					ignore_permissions=True,
					delete_permanently=True,
				)
		except Exception:
			# 清理失败则由后续 insert 的校验兜底报错
			frappe.log_error(
				title="mark_employee_attendance: clean existing failed",
				message=frappe.get_traceback(),
			)


@frappe.whitelist()
def mark_employee_attendance(
	employee_list: list | str,
	status: str,
	date: str | datetime.date,
	leave_type: str | None = None,
	company: str | None = None,
	late_entry: int | None = None,
	early_exit: int | None = None,
	shift: str | None = None,
	project: str | None = None,
	mark_half_day: bool | None = False,
	half_day_status: str | None = None,
	half_day_employee_list: list | str | None = None,
) -> None:
	if isinstance(employee_list, str):
		employee_list = json.loads(employee_list)

	attendance_date = getdate(date)
	effective_leave_type = leave_type if status == "On Leave" else None

	for employee in employee_list:
		_clean_existing_attendance(employee, attendance_date)

		attendance = frappe.get_doc(
			dict(
				doctype="Attendance",
				employee=employee,
				attendance_date=attendance_date,
				status=status,
				leave_type=effective_leave_type,
				late_entry=late_entry,
				early_exit=early_exit,
				shift=shift,
				project=project,
			)
		)
		attendance.insert()
		attendance.submit()
	if mark_half_day:
		if isinstance(half_day_employee_list, str):
			half_day_employee_list = json.loads(half_day_employee_list)
		Attendance = frappe.qb.DocType("Attendance")
		for employee in half_day_employee_list:
			frappe.qb.update(Attendance).where(
				(Attendance.employee == employee) & (Attendance.attendance_date == date)
			).set(Attendance.half_day_status, half_day_status).set(Attendance.shift, shift).set(
				Attendance.late_entry, late_entry
			).set(Attendance.early_exit, early_exit).set(Attendance.modify_half_day_status, 0).run()
