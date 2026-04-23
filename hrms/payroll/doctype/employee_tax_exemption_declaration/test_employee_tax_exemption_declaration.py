import frappe
from frappe.utils import get_year_ending, get_year_start, nowdate


def _upsert_sql(table: str, values: dict):
	if frappe.db.exists(table, values["name"]):
		return values["name"]

	columns = ", ".join(values.keys())
	placeholders = ", ".join(["%s"] * len(values))
	params = list(values.values())
	frappe.db.sql(
		f"INSERT INTO `tab{table}` ({columns}) VALUES ({placeholders})",
		params,
	)
	return values["name"]


def create_exemption_category():
	_upsert_sql(
		"Employee Tax Exemption Category",
		{
			"name": "_Test Category",
			"max_amount": 100000,
			"is_active": 1,
		},
	)
	_upsert_sql(
		"Employee Tax Exemption Sub Category",
		{
			"name": "_Test Sub Category",
			"exemption_category": "_Test Category",
			"max_amount": 100000,
			"is_active": 1,
		},
	)


def create_payroll_period(name=None, company=None, start_date=None, end_date=None):
	name = name or "_Test Payroll Period"
	company = company or "_Test Company"
	start_date = start_date or get_year_start(nowdate())
	end_date = end_date or get_year_ending(nowdate())

	if frappe.db.exists("Payroll Period", name):
		return frappe.get_doc("Payroll Period", name)

	payroll_period = frappe.get_doc(
		{
			"doctype": "Payroll Period",
			"name": name,
			"company": company,
			"start_date": start_date,
			"end_date": end_date,
		}
	)
	payroll_period.insert(ignore_permissions=True)
	return payroll_period
