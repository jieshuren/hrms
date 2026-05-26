<template>
	<ion-page>
		<ListView
			doctype="Expense Claim"
			:pageTitle="'报销记录'"
			:tabButtons="TAB_BUTTONS"
			:fields="EXPENSE_CLAIM_FIELDS"
			groupBy="`tabExpense Claim`.name"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"


const TAB_BUTTONS = ["我的报销", "团队报销"]
const EXPENSE_CLAIM_FIELDS = [
	"`tabExpense Claim`.name",
	"`tabExpense Claim`.employee",
	"`tabExpense Claim`.employee_name",
	"`tabExpense Claim`.currency",
	"`tabExpense Claim`.approval_status",
	"`tabExpense Claim`.status",
	"`tabExpense Claim`.expense_approver",
	"`tabExpense Claim`.total_claimed_amount",
	"`tabExpense Claim`.posting_date",
	"`tabExpense Claim`.company",
	"`tabExpense Claim Detail`.expense_type",
	{"COUNT": "`tabExpense Claim Detail`.expense_type", "as":"total_expenses"}
]

const FILTER_CONFIG = [
	{
		fieldname: "approval_status",
		fieldtype: "Select",
		label: "审批状态",
		options: ["Draft", "Approved", "Rejected"],
	},
	{
		fieldname: "status",
		fieldtype: "Select",
		label: "状态",
		options: ["Draft", "Paid", "Unpaid", "Rejected", "Submitted", "Cancelled"],
	},
	{
		fieldname: "employee",
		fieldtype: "Link",
		label: "员工",
		options: "Employee",
	},
	{
		fieldname: "department",
		fieldtype: "Link",
		label: "部门",
		options: "Department",
	},
	{ fieldname: "posting_date", fieldtype: "Date", label: "提交日期" },
]
</script>
