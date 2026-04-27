# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


from typing import Optional

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.model.workflow import get_workflow_name
from frappe.query_builder.functions import Sum
from frappe.utils import cstr, flt, get_link_to_form, now_datetime, today

import erpnext
from erpnext.accounts.doctype.repost_accounting_ledger.repost_accounting_ledger import (
	validate_docs_for_voucher_types,
)
from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
from erpnext.accounts.general_ledger import make_gl_entries
from erpnext.accounts.utils import (
	create_gain_loss_journal,
	unlink_ref_doc_from_payment_entries,
	update_reference_in_payment_entry,
)
from erpnext.controllers.accounts_controller import AccountsController

import hrms
from hrms.hr.utils import set_employee_name, share_doc_with_approver, validate_active_employee
from hrms.mixins.pwa_notifications import PWANotificationsMixin


class InvalidExpenseApproverError(frappe.ValidationError):
	pass


class ExpenseApproverIdentityError(frappe.ValidationError):
	pass


class MismatchError(frappe.ValidationError):
	pass


class ExpenseClaim(AccountsController, PWANotificationsMixin):
	def onload(self):
		self.get("__onload").make_payment_via_journal_entry = frappe.db.get_single_value(
			"Accounts Settings", "make_payment_via_journal_entry"
		)
		self.set_onload(
			"self_expense_approval_not_allowed",
			frappe.db.get_single_value("HR Settings", "prevent_self_expense_approval"),
		)

	def after_insert(self):
		self.notify_approver()

	def validate(self):
		validate_active_employee(self.employee)
		set_employee_name(self)
		self.validate_sanctioned_amount()
		self.calculate_total_amount()
		self.validate_advances()
		self.set_expense_account(validate=True)
		self.set_default_accounting_dimension()
		self.calculate_taxes()
		self.set_status()
		self.validate_company_and_department()
		if self.task and not self.project:
			self.project = frappe.db.get_value("Task", self.task, "project")

	def set_status(self, update=False):
		status = {"0": "Draft", "1": "Submitted", "2": "Cancelled"}[cstr(self.docstatus or 0)]

		precision = self.precision("grand_total")

		if self.approval_status == "Rejected":
			status = "Rejected"
		elif self.docstatus == 1 and self.approval_status == "Approved":
			if (
				# set as paid
				self.is_paid
				or (
					flt(self.total_sanctioned_amount) > 0
					and (
						# grand total is reimbursed
						(flt(self.grand_total, precision) == flt(self.total_amount_reimbursed, precision))
						# grand total (to be paid) is 0 since linked advances already cover the claimed amount
						or (flt(self.grand_total, precision) == 0)
					)
				)
			):
				status = "Paid"
			elif flt(self.total_sanctioned_amount) > 0:
				status = "Unpaid"

		if update:
			self.db_set("status", status)
			self.publish_update()
			self.notify_update()
		else:
			self.status = status

	def validate_company_and_department(self):
		if self.department:
			company = frappe.db.get_value("Department", self.department, "company")
			if company and self.company != company:
				frappe.throw(
					_("Department {0} does not belong to company: {1}").format(self.department, self.company),
					exc=MismatchError,
				)

	def validate_for_self_approval(self):
		self_expense_approval_not_allowed = frappe.db.get_single_value(
			"HR Settings", "prevent_self_expense_approval"
		)
		employee_user = frappe.db.get_value("Employee", self.employee, "user_id")
		if (
			self_expense_approval_not_allowed
			and employee_user == frappe.session.user
			and not get_workflow_name("Expense Claim")
		):
			frappe.throw(_("Self-approval for Expense Claims is not allowed"))

	def on_update(self):
		share_doc_with_approver(self, self.expense_approver)
		self.publish_update()
		self.notify_approval_status()

	def after_delete(self):
		self.publish_update()

	def on_discard(self):
		self.db_set("status", "Cancelled")
		self.db_set("approval_status", "Cancelled")

	def before_submit(self):
		if not self.payable_account and not self.is_paid:
			frappe.throw(_("Payable Account is mandatory to submit an Expense Claim"))

		self.validate_for_self_approval()

	def publish_update(self):
		employee_user = frappe.db.get_value("Employee", self.employee, "user_id", cache=True)
		hrms.refetch_resource("hrms:my_claims", employee_user)
		hrms.refetch_resource("hrms:team_claims")

	def on_submit(self):
		if self.approval_status == "Draft":
			frappe.throw(_("""Approval Status must be 'Approved' or 'Rejected'"""))

		self.update_task_and_project()
		self.make_gl_entries()
		update_reimbursed_amount(self)
		self.update_claimed_amount_in_employee_advance()
		self.create_exchange_gain_loss_je()
		if not frappe.db.get_single_value("Accounts Settings", "make_payment_via_journal_entry"):
			self.update_against_claim_in_pe()

	def on_update_after_submit(self):
		if self.check_if_fields_updated([], {"taxes": ("account_head",), "expenses": ()}):
			validate_docs_for_voucher_types(["Expense Claim"])
			self.repost_accounting_entries()

	def on_cancel(self):
		self.update_task_and_project()
		self.ignore_linked_doctypes = (
			"GL Entry",
			"Stock Ledger Entry",
			"Payment Ledger Entry",
			"Advance Payment Ledger Entry",
		)
		if self.payable_account:
			self.make_gl_entries(cancel=True)

		update_reimbursed_amount(self)

		self.update_claimed_amount_in_employee_advance()
		self.publish_update()
		unlink_ref_doc_from_payment_entries(self)

	def update_claimed_amount_in_employee_advance(self):
		for d in self.get("advances"):
			frappe.get_doc("Employee Advance", d.employee_advance).update_claimed_amount()

	def update_task_and_project(self):
		if self.task:
			task = frappe.get_doc("Task", self.task)

			ExpenseClaim = frappe.qb.DocType("Expense Claim")
			task.total_expense_claim = (
				frappe.qb.from_(ExpenseClaim)
				.select(Sum(ExpenseClaim.total_sanctioned_amount))
				.where(
					(ExpenseClaim.docstatus == 1)
					& (ExpenseClaim.project == self.project)
					& (ExpenseClaim.task == self.task)
				)
			).run()[0][0]

			task.save()
		elif self.project:
			frappe.get_doc("Project", self.project).update_project()

	def make_gl_entries(self, cancel=False):
		if flt(self.total_sanctioned_amount) > 0:
			gl_entries = self.get_gl_entries()
			make_gl_entries(gl_entries, cancel)

	def get_gl_entries(self):
		gl_entry = []
		self.validate_account_details()

		# payable entry
		if self.grand_total:
			gl_entry.append(
				self.get_gl_dict(
					{
						"account": self.payable_account,
						"credit": self.base_grand_total,
						"credit_in_account_currency": self.grand_total,
						"credit_in_transaction_currency": self.grand_total,
						"against": ",".join([d.default_account for d in self.expenses]),
						"party_type": "Employee",
						"party": self.employee,
						"against_voucher_type": self.doctype,
						"against_voucher": self.name,
						"cost_center": self.cost_center,
						"project": self.project,
						"transaction_exchange_rate": self.exchange_rate,
					},
					account_currency=self.currency,
					item=self,
				)
			)

		# expense entries
		for data in self.expenses:
			gl_entry.append(
				self.get_gl_dict(
					{
						"account": data.default_account,
						"debit": data.base_sanctioned_amount,
						"debit_in_account_currency": data.sanctioned_amount,
						"debit_in_transaction_currency": data.sanctioned_amount,
						"against": self.employee,
						"cost_center": data.cost_center or self.cost_center,
						"project": data.project or self.project,
						"transaction_exchange_rate": self.exchange_rate,
					},
					account_currency=self.currency,
					item=data,
				)
			)

		make_payment_via_je = frappe.db.get_single_value(
			"Accounts Settings", "make_payment_via_journal_entry"
		)
		# gl entry against advance
		for data in self.advances:
			if data.allocated_amount:
				gl_dict = {
					"account": data.advance_account,
					"credit": data.base_allocated_amount,
					"credit_in_account_currency": data.allocated_amount,
					"credit_in_transaction_currency": data.allocated_amount,
					"against": ",".join([d.default_account for d in self.expenses]),
					"party_type": "Employee",
					"party": self.employee,
					"voucher_type": self.doctype,
					"voucher_no": self.name,
					"advance_voucher_type": "Employee Advance",
					"advance_voucher_no": data.employee_advance,
					"transaction_exchange_rate": self.exchange_rate,
					"cost_center": self.cost_center,
					"project": self.project,
				}
				if not make_payment_via_je:
					gl_dict.update(
						{
							"against_voucher_type": "Payment Entry",
							"against_voucher": data.payment_entry,
						}
					)
				gl_entry.append(self.get_gl_dict(gl_dict, account_currency=self.currency))

		self.add_tax_gl_entries(gl_entry)

		if self.is_paid and self.grand_total:
			# payment entry
			payment_account = get_bank_cash_account(self.mode_of_payment, self.company).get("account")
			gl_entry.append(
				self.get_gl_dict(
					{
						"account": payment_account,
						"credit": self.base_grand_total,
						"credit_in_account_currency": self.grand_total,
						"credit_in_transaction_currency": self.grand_total,
						"against": self.employee,
						"transaction_exchange_rate": self.exchange_rate,
						"cost_center": self.cost_center,
						"project": self.project,
					},
					account_currency=self.currency,
					item=self,
				)
			)

			gl_entry.append(
				self.get_gl_dict(
					{
						"account": self.payable_account,
						"party_type": "Employee",
						"party": self.employee,
						"against": payment_account,
						"debit": self.base_grand_total,
						"debit_in_account_currency": self.grand_total,
						"debit_in_transaction_currency": self.grand_total,
						"against_voucher": self.name,
						"against_voucher_type": self.doctype,
						"transaction_exchange_rate": self.exchange_rate,
						"cost_center": self.cost_center,
						"project": self.project,
					},
					account_currency=self.currency,
					item=self,
				)
			)

		return gl_entry

	def add_tax_gl_entries(self, gl_entries):
		# tax table gl entries
		for tax in self.get("taxes"):
			gl_entries.append(
				self.get_gl_dict(
					{
						"account": tax.account_head,
						"debit": tax.base_tax_amount,
						"debit_in_account_currency": tax.tax_amount,
						"debit_in_transaction_currency": tax.tax_amount,
						"against": self.employee,
						"cost_center": tax.cost_center or self.cost_center,
						"project": tax.project or self.project,
						"against_voucher_type": self.doctype,
						"against_voucher": self.name,
						"transaction_exchange_rate": self.exchange_rate,
					},
					account_currency=self.currency,
					item=tax,
				)
			)

	def set_default_accounting_dimension(self):
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
			get_checks_for_pl_and_bs_accounts,
		)

		for dim in get_checks_for_pl_and_bs_accounts():
			if dim.company != self.company:
				continue

			field = frappe.scrub(dim.fieldname)

			if self.meta.get_field(field):
				if not self.get(field) and dim.mandatory_for_bs:
					self.set(field, dim.default_dimension)

			for row in self.get("expenses") or []:
				if row.meta.get_field(field):
					if not row.get(field) and dim.mandatory_for_pl:
						row.set(field, dim.default_dimension)

	def create_exchange_gain_loss_je(self):
		if not self.advances:
			return

		per_advance_gain_loss = 0
		total_advance_exchange_gain_loss = 0
		for advance in self.advances:
			if advance.base_allocated_amount and self.base_total_advance_amount:
				allocated_amount_in_adv_exchange_rate = flt(advance.allocated_amount) * flt(
					advance.exchange_rate
				)
				per_advance_gain_loss += flt(
					(advance.base_allocated_amount - allocated_amount_in_adv_exchange_rate),
					self.precision("total_exchange_gain_loss"),
				)

				if per_advance_gain_loss:
					advance.db_set("exchange_gain_loss", per_advance_gain_loss)
					total_advance_exchange_gain_loss += per_advance_gain_loss
		if total_advance_exchange_gain_loss:
			gain_loss_account = frappe.get_cached_value("Company", self.company, "exchange_gain_loss_account")
			self.db_set(
				{
					"total_exchange_gain_loss": total_advance_exchange_gain_loss,
					"gain_loss_account": gain_loss_account,
				}
			)
			dr_or_cr = "credit" if self.total_exchange_gain_loss > 0 else "debit"
			reverse_dr_or_cr = "debit" if dr_or_cr == "credit" else "credit"

			je = create_gain_loss_journal(
				company=self.company,
				posting_date=today(),
				party_type="Employee",
				party=self.employee,
				party_account=self.payable_account,
				gain_loss_account=self.gain_loss_account,
				exc_gain_loss=self.total_exchange_gain_loss,
				dr_or_cr=dr_or_cr,
				reverse_dr_or_cr=reverse_dr_or_cr,
				ref1_dt=self.doctype,
				ref1_dn=self.name,
				ref1_detail_no=1,
				ref2_dt=self.doctype,
				ref2_dn=self.name,
				ref2_detail_no=1,
				cost_center=self.cost_center,
				dimensions={},
			)
			frappe.msgprint(
				_("All Exchange Gain/Loss amount of {0} has been booked through {1}").format(
					self.name,
					get_link_to_form("Journal Entry", je),
				)
			)

	def validate_account_details(self):
		for data in self.expenses:
			if not data.cost_center:
				frappe.throw(
					_("Row {0}: {1} is required in the expenses table to book an expense claim.").format(
						data.idx, frappe.bold(_("Cost Center"))
					)
				)

		if self.is_paid:
			if not self.mode_of_payment:
				frappe.throw(_("Mode of payment is required to make a payment").format(self.employee))

	def calculate_total_amount(self):
		self.total_claimed_amount = 0
		self.total_sanctioned_amount = 0

		for d in self.get("expenses"):
			self.round_floats_in(d)

			if self.approval_status == "Rejected":
				d.sanctioned_amount = 0.0

			self.total_claimed_amount += flt(d.amount)
			self.total_sanctioned_amount += flt(d.sanctioned_amount)
			self.set_base_fields_amount(d, ["amount", "sanctioned_amount"])

		self.set_base_fields_amount(self, ["total_sanctioned_amount", "total_claimed_amount"])

	def set_base_fields_amount(self, doc, fields, exchange_rate=None):
		"""set values in base currency"""
		for f in fields:
			val = flt(
				flt(doc.get(f), doc.precision(f))
				* flt(exchange_rate if exchange_rate else self.exchange_rate),
				doc.precision("base_" + f),
			)
			doc.set("base_" + f, val)

	@frappe.whitelist()
	def calculate_taxes(self):
		self.total_taxes_and_charges = 0
		for tax in self.taxes:
			self.round_floats_in(tax)

			if tax.rate:
				tax.tax_amount = flt(
					flt(self.total_sanctioned_amount) * flt(flt(tax.rate) / 100),
					tax.precision("tax_amount"),
				)

			tax.total = flt(tax.tax_amount) + flt(self.total_sanctioned_amount)
			self.total_taxes_and_charges += flt(tax.tax_amount)
			self.set_base_fields_amount(tax, ["tax_amount", "total"])

		self.round_floats_in(self, ["total_taxes_and_charges"])

		self.grand_total = (
			flt(self.total_sanctioned_amount)
			+ flt(self.total_taxes_and_charges)
			- flt(self.total_advance_amount)
		)
		self.round_floats_in(self, ["grand_total"])
		self.set_base_fields_amount(self, ["grand_total"])

	def validate_advances(self):
		self.total_advance_amount = 0
		precision = self.precision("total_advance_amount")

		for d in self.get("advances"):
			advance_employee = frappe.db.get_value("Employee Advance", d.employee_advance, "employee")
			if self.employee != advance_employee:
				frappe.throw(_("Selected employee advance is not of employee {}").format(self.employee))

			self.round_floats_in(d)
			if d.allocated_amount and flt(d.allocated_amount) > flt(
				flt(d.unclaimed_amount) - flt(d.return_amount), precision
			):
				frappe.throw(
					_("Row {0}# Allocated amount {1} cannot be greater than unclaimed amount {2}").format(
						d.idx, d.allocated_amount, d.unclaimed_amount
					)
				)

			self.total_advance_amount += flt(d.allocated_amount)
			self.set_base_fields_amount(d, ["advance_paid", "unclaimed_amount"], d.exchange_rate)
			self.set_base_fields_amount(d, ["allocated_amount"])

		if self.total_advance_amount:
			self.round_floats_in(self, ["total_advance_amount"])
			amount_with_taxes = flt(
				(flt(self.total_sanctioned_amount, precision) + flt(self.total_taxes_and_charges, precision)),
				precision,
			)
			self.set_base_fields_amount(self, ["total_advance_amount"])

			if flt(self.total_advance_amount, precision) > amount_with_taxes:
				frappe.throw(_("Total advance amount cannot be greater than total sanctioned amount"))

	def validate_sanctioned_amount(self):
		for d in self.get("expenses"):
			if flt(d.sanctioned_amount) > flt(d.amount):
				frappe.throw(
					_("Sanctioned Amount cannot be greater than Claim Amount in Row {0}.").format(d.idx)
				)

	def set_expense_account(self, validate=False):
		for expense in self.expenses:
			if not expense.default_account or not validate:
				expense.default_account = get_expense_claim_account(expense.expense_type, self.company)[
					"account"
				]

	def update_against_claim_in_pe(self):
		reference_against_pe = []
		for advance in self.advances:
			if flt(advance.allocated_amount) > 0:
				args = frappe._dict(
					{
						"voucher_type": "Payment Entry",
						"voucher_no": advance.payment_entry,
						"against_voucher_type": self.doctype,
						"against_voucher": self.name,
						"voucher_detail_no": advance.payment_entry_reference,
						"account": advance.advance_account,
						"party_type": "Employee",
						"party": self.employee,
						"is_advance": "Yes",
						"dr_or_cr": "credit_in_account_currency",
						"unadjusted_amount": flt(advance.advance_paid),
						"allocated_amount": flt(advance.allocated_amount),
						"precision": advance.precision("advance_paid"),
						"exchange_rate": advance.exchange_rate,
						"difference_posting_date": advance.posting_date,
					}
				)
				reference_against_pe.append(args)
		if reference_against_pe:
			for pe_ref in reference_against_pe:
				payment_entry = frappe.get_doc("Payment Entry", pe_ref.voucher_no)
				update_reference_in_payment_entry(pe_ref, payment_entry, skip_ref_details_update_for_pe=True)


def update_reimbursed_amount(doc):
	total_amount_reimbursed = get_total_reimbursed_amount(doc)

	doc.total_amount_reimbursed = total_amount_reimbursed
	frappe.db.set_value("Expense Claim", doc.name, "total_amount_reimbursed", total_amount_reimbursed)

	doc.set_status(update=True)


def get_total_reimbursed_amount(doc):
	if doc.is_paid:
		# No need to check for cancelled state here as it will anyways update status as cancelled
		return doc.grand_total
	else:
		JournalEntryAccount = frappe.qb.DocType("Journal Entry Account")
		amount_via_jv = frappe.db.get_value(
			"Journal Entry Account",
			{"reference_name": doc.name, "docstatus": 1},
			Sum(
				JournalEntryAccount.debit_in_account_currency - JournalEntryAccount.credit_in_account_currency
			),
		)

		amount_via_payment_entry = frappe.db.get_value(
			"Payment Entry Reference",
			{
				"reference_name": doc.name,
				"advance_voucher_type": None,
				"docstatus": 1,
			},
			[{"SUM": "allocated_amount"}],
		)

		return flt(amount_via_jv) + flt(amount_via_payment_entry)


def get_outstanding_amount_for_claim(claim):
	precision = frappe.get_precision("Expense Claim", "grand_total")

	if isinstance(claim, str):
		claim = frappe.db.get_value(
			"Expense Claim",
			claim,
			(
				"total_sanctioned_amount",
				"total_taxes_and_charges",
				"total_amount_reimbursed",
				"total_advance_amount",
			),
			as_dict=True,
		)

	outstanding_amt = (
		flt(claim.total_sanctioned_amount)
		+ flt(claim.total_taxes_and_charges)
		- flt(claim.total_amount_reimbursed)
		- flt(claim.total_advance_amount)
	)

	return flt(outstanding_amt, precision)


@frappe.whitelist()
def make_bank_entry(dt, dn):
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	expense_claim = frappe.get_doc(dt, dn)
	default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Bank")
	if not default_bank_cash_account:
		default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Cash")

	payable_amount = get_outstanding_amount_for_claim(expense_claim)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Bank Entry"
	je.company = expense_claim.company
	je.remark = "Payment against Expense Claim: " + dn

	je.append(
		"accounts",
		{
			"account": expense_claim.payable_account,
			"debit_in_account_currency": payable_amount,
			"reference_type": "Expense Claim",
			"party_type": "Employee",
			"party": expense_claim.employee,
			"cost_center": erpnext.get_default_cost_center(expense_claim.company),
			"reference_name": expense_claim.name,
		},
	)

	je.append(
		"accounts",
		{
			"account": default_bank_cash_account.account,
			"credit_in_account_currency": payable_amount,
			"balance": default_bank_cash_account.balance,
			"account_currency": default_bank_cash_account.account_currency,
			"cost_center": erpnext.get_default_cost_center(expense_claim.company),
			"account_type": default_bank_cash_account.account_type,
		},
	)

	return je.as_dict()


@frappe.whitelist()
def get_expense_claim_account_and_cost_center(expense_claim_type, company):
	data = get_expense_claim_account(expense_claim_type, company)
	cost_center = erpnext.get_default_cost_center(company)

	return {"account": data.get("account"), "cost_center": cost_center}


@frappe.whitelist()
def get_expense_claim_account(expense_claim_type, company):
	account = frappe.db.get_value(
		"Expense Claim Account", {"parent": expense_claim_type, "company": company}, "default_account"
	)
	if not account:
		frappe.throw(
			_("Set the default account for the {0} {1}").format(
				frappe.bold(_("Expense Claim Type")),
				get_link_to_form("Expense Claim Type", expense_claim_type),
			)
		)

	return {"account": account}


@frappe.whitelist()
def get_advances(expense_claim: str | dict | Document, advance_id: str | None = None):
	import json

	if isinstance(expense_claim, str):
		expense_claim = frappe._dict(json.loads(expense_claim))
	expense_claim_doc = frappe.get_doc(expense_claim)
	expense_claim_doc.advances = []

	advance = frappe.qb.DocType("Employee Advance")

	query = frappe.qb.from_(advance).select(
		advance.name,
		advance.purpose,
		advance.posting_date,
		advance.paid_amount,
		advance.claimed_amount,
		advance.return_amount,
		advance.advance_account,
	)

	if not advance_id:
		query = query.where(
			(advance.docstatus == 1)
			& (advance.employee == expense_claim_doc.employee)
			& (advance.paid_amount > 0)
			& (advance.status.notin(["Claimed", "Returned", "Partly Claimed and Returned"]))
		)
	else:
		query = query.where((advance.name == advance_id) & (advance.employee == expense_claim_doc.employee))

	advances = query.run(as_dict=True)

	payment_via_journal_entry = frappe.db.get_single_value(
		"Accounts Settings", "make_payment_via_journal_entry"
	)
	for advance in advances:
		advance.update({"payment_via_journal_entry": payment_via_journal_entry})
		get_expense_claim_advances(expense_claim_doc, advance)
	return expense_claim_doc.advances


@frappe.whitelist()
def get_expense_claim(employee_advance: str | dict, payment_via_journal_entry: str | int | bool) -> Document:
	if isinstance(employee_advance, str):
		employee_advance = frappe.get_doc("Employee Advance", employee_advance)

	company = employee_advance.company
	default_payable_account = frappe.get_cached_value(
		"Company", company, "default_expense_claim_payable_account"
	)
	default_cost_center = frappe.get_cached_value("Company", company, "cost_center")

	expense_claim = frappe.new_doc("Expense Claim")
	expense_claim.company = company
	expense_claim.currency = employee_advance.currency
	expense_claim.employee = employee_advance.employee
	expense_claim.payable_account = (
		default_payable_account
		if employee_advance.currency == erpnext.get_company_currency(company)
		else None
	)
	expense_claim.cost_center = default_cost_center
	expense_claim.is_paid = 1 if flt(employee_advance.paid_amount) else 0

	employee_advance.update(
		{
			"payment_via_journal_entry": payment_via_journal_entry,
		}
	)

	get_expense_claim_advances(expense_claim, employee_advance)
	return expense_claim


def get_expense_claim_advances(expense_claim, employee_advance):
	return_amount = flt(employee_advance.return_amount)
	if int(employee_advance.payment_via_journal_entry):
		paid_amount = flt(employee_advance.paid_amount)
		claimed_amount = flt(employee_advance.claimed_amount)
		exchange_rate = frappe.db.get_value(
			"Advance Payment Ledger Entry",
			{
				"voucher_type": "Journal Entry",
				"against_voucher_type": "Employee Advance",
				"against_voucher_no": employee_advance.name,
				"delinked": False,
				"amount": paid_amount,
			},
			"exchange_rate",
		)
		allocated_amount = get_allocation_amount(
			paid_amount=paid_amount, claimed_amount=claimed_amount, return_amount=return_amount
		)
		unclaimed_amount = paid_amount - claimed_amount
		expense_claim.append(
			"advances",
			{
				"advance_account": employee_advance.advance_account,
				"employee_advance": employee_advance.name,
				"posting_date": employee_advance.posting_date,
				"advance_paid": paid_amount,
				"base_advance_paid": flt(employee_advance.base_paid_amount),
				"unclaimed_amount": unclaimed_amount,
				"allocated_amount": allocated_amount,
				"return_amount": return_amount,
				"exchange_rate": exchange_rate,
			},
		)
	else:
		pe = frappe.qb.DocType("Payment Entry")
		pe_ref = frappe.qb.DocType("Payment Entry Reference")
		payment_entries = (
			frappe.qb.from_(pe)
			.inner_join(pe_ref)
			.on(pe_ref.parent == pe.name)
			.select(
				(pe.name).as_("payment_entry"),
				(pe.total_allocated_amount).as_("advance_paid"),
				(pe.unallocated_amount),
				(pe.base_total_allocated_amount).as_("base_advance_paid"),
				(pe.target_exchange_rate).as_("exchange_rate"),
				(pe_ref.name).as_("pe_ref_name"),
				(pe_ref.outstanding_amount),
				(pe_ref.allocated_amount).as_("pe_ref_allocated_amount"),
			)
			.where(
				(pe.docstatus == 1)
				& (pe_ref.reference_doctype == "Employee Advance")
				& (pe_ref.reference_name == employee_advance.name)
				& (pe_ref.allocated_amount > 0)
			)
		).run(as_dict=True)

		for pe in payment_entries:
			advance_paid = flt(pe.advance_paid) + flt(pe.unallocated_amount)
			unclaimed_amount = flt(pe.advance_paid)
			if flt(pe.pe_ref_allocated_amount):
				unclaimed_amount = flt(pe.pe_ref_allocated_amount) + flt(pe.unallocated_amount)
			allocated_amount = get_allocation_amount(
				paid_amount=flt(pe.advance_paid),
				claimed_amount=(flt(pe.advance_paid) - unclaimed_amount),
				return_amount=(return_amount),
			)

			expense_claim.append(
				"advances",
				{
					"advance_account": employee_advance.advance_account,
					"employee_advance": employee_advance.name,
					"posting_date": employee_advance.posting_date,
					"advance_paid": advance_paid,
					"base_advance_paid": advance_paid * pe.exchange_rate,
					"unclaimed_amount": unclaimed_amount,
					"allocated_amount": allocated_amount,
					"return_amount": return_amount,
					"exchange_rate": pe.exchange_rate,
					"payment_entry": pe.payment_entry,
					"payment_entry_reference": pe.pe_ref_name
					if flt(pe.advance_paid) >= advance_paid
					else None,
					"purpose": employee_advance.purpose,
				},
			)


def update_payment_for_expense_claim(doc, method=None):
	"""
	Updates payment/reimbursed amount in Expense Claim
	on Payment Entry/Journal Entry cancellation/submission
	"""
	if doc.doctype == "Payment Entry" and not (doc.payment_type == "Pay" and doc.party):
		return

	doctype_field_map = {
		"Journal Entry": ["accounts", "reference_type"],
		"Payment Entry": ["references", "reference_doctype"],
		"Unreconcile Payment": ["allocations", "reference_doctype"],
	}

	payment_table, doctype_field = doctype_field_map[doc.doctype]

	for d in doc.get(payment_table):
		if d.get(doctype_field) == "Expense Claim" and d.reference_name:
			expense_claim = frappe.get_doc("Expense Claim", d.reference_name)
			update_reimbursed_amount(expense_claim)

			if doc.doctype == "Payment Entry":
				update_outstanding_amount_in_payment_entry(expense_claim, d.name)


def update_outstanding_amount_in_payment_entry(expense_claim: dict, pe_reference: str):
	"""updates outstanding amount back in Payment Entry reference"""
	# TODO: refactor convoluted code after erpnext payment entry becomes extensible
	outstanding_amount = get_outstanding_amount_for_claim(expense_claim)
	frappe.db.set_value("Payment Entry Reference", pe_reference, "outstanding_amount", outstanding_amount)


def validate_expense_claim_in_jv(doc, method=None):
	"""Validates Expense Claim amount in Journal Entry"""
	if doc.voucher_type == "Exchange Gain Or Loss":
		return

	for d in doc.accounts:
		if d.reference_type == "Expense Claim":
			outstanding_amt = get_outstanding_amount_for_claim(d.reference_name)
			if d.debit and (d.debit > outstanding_amt):
				frappe.throw(
					_(
						"Row No {0}: Amount cannot be greater than the Outstanding Amount against Expense Claim {1}. Outstanding Amount is {2}"
					).format(d.idx, d.reference_name, outstanding_amt)
				)


@frappe.whitelist()
def make_expense_claim_for_delivery_trip(source_name, target_doc=None):
	doc = get_mapped_doc(
		"Delivery Trip",
		source_name,
		{"Delivery Trip": {"doctype": "Expense Claim", "field_map": {"name": "delivery_trip"}}},
		target_doc,
	)

	return doc


@frappe.whitelist()
def get_allocation_amount(paid_amount=None, claimed_amount=None, return_amount=None, unclaimed_amount=None):
	if unclaimed_amount is not None and return_amount is not None:
		return flt(unclaimed_amount) - flt(return_amount)
	elif paid_amount is not None and claimed_amount is not None and return_amount is not None:
		return flt(paid_amount) - (flt(claimed_amount) + flt(return_amount))
	else:
		frappe.throw(_("Invalid parameters provided. Please pass the required arguments."))


_PRIVILEGED_ROLES = {
	"Administrator",
	"System Manager",
	"HR Manager",
	"HR User",
	"Finance Approver",
	"General Manager",
	"Cashier",
	"Accounts Manager",
	"Accounts User",
}


def expense_claim_query(user: Optional[str] = None) -> str:
	"""permission_query_conditions 回调：返回 SQL WHERE 片段。"""
	user = user or frappe.session.user
	if not user or user == "Administrator":
		return ""

	user_roles = set(frappe.get_roles(user))
	if user_roles & _PRIVILEGED_ROLES:
		return ""

	employee_info = frappe.db.get_value(
		"Employee",
		{"user_id": user, "status": "Active"},
		["name", "department"],
		as_dict=True,
	)

	safe_user = frappe.db.escape(user)
	clauses = [f"`tabExpense Claim`.`owner` = {safe_user}"]

	if employee_info:
		employee_name = frappe.db.escape(employee_info.name)
		clauses.append(f"`tabExpense Claim`.`expense_approver` = {safe_user}")
		clauses.append(f"`tabExpense Claim`.`employee` = {employee_name}")

		if employee_info.department:
			safe_department = frappe.db.escape(employee_info.department)
			clauses.append(
				"(`tabExpense Claim`.`department` = {department} AND "
				"`tabExpense Claim`.`workflow_state` = 'Pending Peer Verification')".format(
					department=safe_department
				)
			)

		subordinate_employees = frappe.db.sql_list(
			"""
			SELECT name FROM `tabEmployee`
			WHERE reports_to = %s AND status = 'Active'
			""",
			(employee_info.name,),
		)
		if subordinate_employees:
			placeholder = ",".join(frappe.db.escape(emp) for emp in subordinate_employees)
			clauses.append(
				"(`tabExpense Claim`.`employee` IN ({emps}) AND "
				"`tabExpense Claim`.`workflow_state` NOT IN ('Draft', 'Pending Peer Verification'))".format(
					emps=placeholder
				)
			)

	return "(" + " OR ".join(clauses) + ")"


def expense_claim_has_permission(doc, ptype: Optional[str] = None, user: Optional[str] = None) -> bool:
	"""行级权限兜底（覆盖 get_doc / Form 查看）。"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	user_roles = set(frappe.get_roles(user))
	if user_roles & _PRIVILEGED_ROLES:
		return True

	if doc.owner == user:
		return True
	if getattr(doc, "expense_approver", None) == user:
		return True

	employee_info = frappe.db.get_value(
		"Employee",
		{"user_id": user, "status": "Active"},
		["name", "department"],
		as_dict=True,
	)
	if not employee_info:
		return False

	if getattr(doc, "employee", None) == employee_info.name:
		return True

	workflow_state = (getattr(doc, "workflow_state", None) or "").strip()

	if (
		ptype == "write"
		and getattr(doc, "docstatus", 0) == 0
		and workflow_state == "Pending Peer Verification"
		and getattr(doc, "department", None) == employee_info.department
	):
		return True

	if workflow_state == "Pending Peer Verification" and getattr(doc, "department", None) == employee_info.department:
		return True

	if workflow_state not in {"Draft", "Pending Peer Verification"}:
		applicant_reports_to = frappe.db.get_value(
			"Employee", getattr(doc, "employee", None), "reports_to"
		)
		if applicant_reports_to == employee_info.name:
			return True

	return False


def is_same_department_peer(doc, user: str) -> bool:
	"""同部门、非申请人本人 = 可以点击验收。"""
	if not doc or not user:
		return False
	if user == "Administrator":
		return True
	if user == (_doc_get(doc, "owner") or ""):
		return False

	doc_department = _doc_get(doc, "department")
	if not doc_department:
		return False

	user_department = frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, "department"
	)
	return bool(user_department) and user_department == doc_department


def is_department_head(doc, user: str) -> bool:
	"""申请人 Employee.reports_to 对应的 user_id == 当前用户。"""
	if not doc or not user:
		return False
	if user == "Administrator":
		return True

	employee = _doc_get(doc, "employee")
	if not employee:
		return False

	reports_to = frappe.db.get_value("Employee", employee, "reports_to")
	if not reports_to:
		return False

	head_user = frappe.db.get_value("Employee", reports_to, "user_id")
	return bool(head_user) and head_user == user


def _doc_get(doc, key: str):
	"""兼容 Workflow safe_eval 传入 dict 或 Document。"""
	if isinstance(doc, dict):
		return doc.get(key)
	return getattr(doc, key, None)


_PEER_STATES = {"Pending Department Approval"}
_DEPT_STATES = {"Pending Finance Approval"}
_FINANCE_STATES = {"Pending GM Approval"}
_GM_STATES = {"Approved"}


def ensure_sanctioned_amounts_before_submit(doc, method: Optional[str] = None) -> None:
	"""若审批链未手工填写核准金额，默认按申报金额全额核准。"""
	import erpnext

	expenses = getattr(doc, "expenses", None) or []
	if not expenses:
		return

	updated = False
	default_cost_center = None
	if getattr(doc, "company", None):
		default_cost_center = erpnext.get_default_cost_center(doc.company)
		if not default_cost_center:
			default_cost_center = frappe.db.get_value(
				"Cost Center",
				{"company": doc.company, "is_group": 0},
				"name",
			)
	for row in expenses:
		claimed = float(getattr(row, "amount", 0) or 0)
		sanctioned = float(getattr(row, "sanctioned_amount", 0) or 0)
		if claimed > 0 and sanctioned <= 0:
			row.sanctioned_amount = claimed
			updated = True
		if default_cost_center and not getattr(row, "cost_center", None):
			row.cost_center = default_cost_center
			updated = True

	if updated:
		doc.calculate_total_amount()
		doc.calculate_taxes()


def handle_expense_claim_on_update(doc, method: Optional[str] = None) -> None:
	"""Expense Claim.on_update 钩子入口。"""
	if getattr(getattr(doc, "flags", None), "skip_ai_category_classification", False):
		return

	new_state = (getattr(doc, "workflow_state", None) or "").strip()
	if not new_state:
		return

	_record_approval_trail(doc, new_state)

	if new_state == "Paid" and doc.docstatus == 1:
		_auto_create_payment_entry(doc)


def _record_approval_trail(doc, new_state: str) -> None:
	"""在状态推进时把审批人和时间落在自定义字段上。"""
	now = now_datetime()
	user = frappe.session.user

	if new_state in _PEER_STATES and not getattr(doc, "custom_peer_verifier", None):
		doc.db_set("custom_peer_verifier", user, update_modified=False)
		doc.db_set("custom_peer_verified_on", now, update_modified=False)
	elif new_state in _DEPT_STATES and not getattr(doc, "custom_dept_head_approver", None):
		doc.db_set("custom_dept_head_approver", user, update_modified=False)
		doc.db_set("custom_dept_head_approved_on", now, update_modified=False)
	elif new_state in _FINANCE_STATES and not getattr(doc, "custom_finance_approver", None):
		doc.db_set("custom_finance_approver", user, update_modified=False)
		doc.db_set("custom_finance_approved_on", now, update_modified=False)
	elif new_state in _GM_STATES and not getattr(doc, "custom_gm_approver", None):
		doc.db_set("custom_gm_approver", user, update_modified=False)
		doc.db_set("custom_gm_approved_on", now, update_modified=False)


def _notify_claimant_of_payment(doc, payment_entry_name: str) -> None:
	"""付款成功后给报销人发送桌面/移动端通知 + Socket.IO 实时事件。"""
	employee = getattr(doc, "employee", None)
	if not employee:
		return
	user_id = frappe.db.get_value("Employee", employee, "user_id")
	if not user_id:
		return

	subject = frappe._("报销单 {0} 已付款").format(doc.name)
	amount = frappe.utils.fmt_money(
		doc.total_sanctioned_amount or doc.total_claimed_amount or 0,
		currency=getattr(doc, "currency", None),
	)
	body = frappe._("出纳已确认支付，金额：{0}。付款单号：{1}").format(amount, payment_entry_name)

	try:
		notif = frappe.new_doc("Notification Log")
		notif.for_user = user_id
		notif.from_user = frappe.session.user
		notif.subject = subject
		notif.email_content = body
		notif.document_type = "Expense Claim"
		notif.document_name = doc.name
		notif.type = "Alert"
		notif.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ExpenseClaim notify_claimant_of_payment")

	try:
		frappe.publish_realtime(
			event="expense_claim_paid",
			message={
				"claim": doc.name,
				"payment_entry": payment_entry_name,
				"amount": amount,
				"mode_of_payment": getattr(doc, "custom_cashier_mode_of_payment", None),
			},
			user=user_id,
			after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ExpenseClaim publish_realtime")


def _auto_create_payment_entry(doc) -> None:
	"""出纳确认支付 → 自动创建并提交付款分录，冲销其他应付款。"""
	if doc.docstatus != 1:
		return
	if getattr(doc, "custom_related_journal_entry", None):
		return
	if getattr(doc, "custom_related_payment_entry", None):
		return
	existing_je = _find_existing_bank_entry_for_claim(doc.name)
	if existing_je:
		doc.db_set("custom_related_journal_entry", existing_je, update_modified=False)
		return

	from hrms.hr.doctype.expense_claim.expense_claim import get_outstanding_amount_for_claim

	selected_mode = (getattr(doc, "custom_cashier_mode_of_payment", None) or "").strip()
	if not selected_mode and doc.mode_of_payment:
	        selected_mode = doc.mode_of_payment

	selected_account = None
	if selected_mode:
	        selected_account = _resolve_mode_of_payment_account(selected_mode, doc.company)

	if not selected_account and doc.bank_or_cash_account:
	        selected_account = doc.bank_or_cash_account

	# 最终兜底：尝试获取公司默认银行/现金账户
	if not selected_account:
	        from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account
	        res = get_default_bank_cash_account(doc.company, "Bank")
	        if not res:
	                res = get_default_bank_cash_account(doc.company, "Cash")
	        if res:
	                selected_account = res.get("account")

	if not selected_account:
	        frappe.throw(
	                "未能确定出纳付款账户。请在移动端重新选择付款方式（确保该 Mode of Payment 已在 "
	                "Company 下配置默认账户），或在 Company 设置中填写『默认银行账户 / 默认现金账户』。"
	        )
	outstanding_amount = get_outstanding_amount_for_claim(doc)
	if outstanding_amount <= 0:
		return

	je = _make_bank_entry_for_expense_claim(doc, selected_account, outstanding_amount, selected_mode)
	je.insert(ignore_permissions=True)
	je.submit()

	now = now_datetime()
	doc.db_set("custom_cashier_user", frappe.session.user, update_modified=False)
	doc.db_set("custom_paid_on", now, update_modified=False)
	doc.db_set("is_paid", 1, update_modified=False)
	doc.db_set("custom_related_journal_entry", je.name, update_modified=False)
	doc.db_set("custom_related_payment_entry", None, update_modified=False)

	_notify_claimant_of_payment(doc, je.name)


def _make_bank_entry_for_expense_claim(doc, bank_account: str, amount: float, mode_of_payment: Optional[str] = None):
	"""按官方 Make Bank Entry 思路创建 Journal Entry。"""
	import erpnext

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Bank Entry"
	je.company = doc.company
	je.posting_date = getattr(doc, "posting_date", None) or frappe.utils.nowdate()
	je.cheque_no = doc.name
	je.cheque_date = je.posting_date
	je.user_remark = f"Payment against Expense Claim: {doc.name}"
	if mode_of_payment:
		je.mode_of_payment = mode_of_payment

	cost_center = erpnext.get_default_cost_center(doc.company)

	je.append(
		"accounts",
		{
			"account": doc.payable_account,
			"debit_in_account_currency": amount,
			"party_type": "Employee",
			"party": doc.employee,
			"reference_type": "Expense Claim",
			"reference_name": doc.name,
			"cost_center": cost_center,
		},
	)
	je.append(
		"accounts",
		{
			"account": bank_account,
			"credit_in_account_currency": amount,
			"cost_center": cost_center,
			"against_voucher_type": "Expense Claim",
			"against_voucher": doc.name,
		},
	)

	return je


def _resolve_mode_of_payment_account(mode_of_payment: str, company: str) -> str | None:
	if not mode_of_payment or not company:
		return None
	return frappe.db.get_value(
		"Mode of Payment Account",
		{"parent": mode_of_payment, "company": company},
		"default_account",
	)


def _find_existing_bank_entry_for_claim(claim_name: str) -> str | None:
	return frappe.db.exists(
		"Journal Entry",
		{"voucher_type": "Bank Entry", "cheque_no": claim_name, "docstatus": 1},
	)
