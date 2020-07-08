# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import get_fullname, flt, cstr
from frappe.model.document import Document
from erpnext.hr.utils import set_employee_name
from erpnext.accounts.party import get_party_account
from erpnext.accounts.general_ledger import make_gl_entries
from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
from erpnext.controllers.accounts_controller import AccountsController
from frappe.utils.csvutils import getlink
from erpnext.accounts.utils import get_account_currency

class InvalidExpenseApproverError(frappe.ValidationError): pass
class ExpenseApproverIdentityError(frappe.ValidationError): pass

class ExpenseEntry(AccountsController):
	def onload(self):
		self.get("__onload").make_payment_via_journal_entry = frappe.db.get_single_value('Accounts Settings',
			'make_payment_via_journal_entry')

	def validate(self):
		self.validate_currency()
		#self.calculate_total_amount()
		#set_employee_name(self)
		#self.set_expense_account(validate=True)
		#self.set_payable_account()
		self.set_cost_center()
		#self.calculate_taxes()
		#self.set_status()
		#if self.task and not self.project:
		#	self.project = frappe.db.get_value("Task", self.task, "project")

	def validate_currency(self):
		if self.currency != self.account_currency:
			frappe.throw(_("""Mode Of Payment Wrong Currency"""))
		for data in self.expenses:
			if self.default_currency != data.account_currency:
				frappe.throw(_("""Type Wrong Currency{0}{1}""").format(self.default_currency,data.account_currency))

	def set_status(self):
		self.status = {
			"0": "Draft",
			"1": "Submitted",
			"2": "Cancelled"
		}[cstr(self.docstatus or 0)]
		if flt(self.total_amount) > 0 and self.docstatus == 1 and self.approval_status == 'Approved':
			self.status = "Paid"
		elif self.docstatus == 1 and self.approval_status == 'Rejected':
			self.status = 'Rejected'
		elif self.docstatus == 2:
			self.status = 'Cancelled'

	def set_payable_account(self):
		if not self.payable_account and not self.is_paid:
			self.payable_account = frappe.get_cached_value('Company', self.company, 'default_expense_claim_payable_account')

	def set_cost_center(self):
		if not self.cost_center:
			self.cost_center = frappe.get_cached_value('Company', self.company, 'cost_center')

	def on_submit(self):
		if self.total_amount<=0:
			frappe.throw(_("""Must To Be more than 0 {0}""").format(self.total_amount))
		if self.approval_status=="Draft":
			frappe.throw(_("""Approval Status must be 'Approved' or 'Rejected'"""))

		self.update_task_and_project()
		if self.approval_status=="Approved":
			self.make_gl_entries()

		self.set_status()

	def on_cancel(self):
		self.update_task_and_project()
		if self.payment_account:
			self.make_gl_entries(cancel=True)

		self.set_status()

	def update_task_and_project(self):
		if self.task:
			self.update_task()
		elif self.project:
			frappe.get_doc("Project", self.project).update_project()

	def make_gl_entries(self, cancel=False):
		if flt(self.total_amount) > 0:
			gl_entries = self.get_gl_entries()
			make_gl_entries(gl_entries, cancel)

	def get_gl_entries(self):
		gl_entry = []
		self.validate_account_details()

		# payment entry
		if self.total_amount:
			gl_entry.append(
				self.get_gl_dict({
					"account": self.payment_account,
					"credit": self.base_total_amount,
					"credit_in_account_currency": self.total_amount,
					"conversion_rate":self.conversion_rate,
					"against": ",".join([d.default_account for d in self.expenses]),
					"against_voucher_type": self.doctype,
					"against_voucher": self.name,
					"cost_center": self.cost_center
				}, item=self)
			)	

		# expense entries
		for data in self.expenses:
			gl_entry.append(
				self.get_gl_dict({
					"account": data.default_account,
					"debit": data.base_amount,
					"debit_in_account_currency": data.base_amount,
					"conversion_rate":self.conversion_rate,
					"against": self.payment_account,
					"against_voucher_type": self.doctype,
					"against_voucher": self.name,
					"cost_center": data.cost_center
				}, item=data)
			)
		
		return gl_entry

	def validate_account_details(self):
		if not self.cost_center:
			frappe.throw(_("Cost center is required to book an expense claim"))

	def calculate_total_amount(self):
		self.total_claimed_amount = 0
		for d in self.get('expenses'):
			self.total_amount += flt(d.amount)

	def update_task(self):
		task = frappe.get_doc("Task", self.task)
		task.update_total_expense_claim()
		task.save()


	def set_expense_account(self, validate=False):
		for expense in self.expenses:
			if not expense.default_account or not validate:
				expense.default_account = get_expense_entry_account(expense.expense_type, self.company)["account"]


@frappe.whitelist()
def get_expense_entry_account(expense_claim_type, company):
	account = frappe.db.get_value("Expense Claim Account",
		{"parent": expense_claim_type, "company": company}, "default_account")
	if not account:
		frappe.throw(_("Please set default account in Expense Claim Type {0}")
			.format(expense_claim_type))

	return {
		"account": account
	}

@frappe.whitelist()
def get_payment_account(mode_of_payment, company):
	account = frappe.db.get_value("Mode of Payment Account",
		{"parent": mode_of_payment, "company": company}, "default_account")
	if not account:
		frappe.throw(_("Please set default account in Mode of Payment {0}")
			.format(mode_of_payment))

	return {
		"account": account
	}

