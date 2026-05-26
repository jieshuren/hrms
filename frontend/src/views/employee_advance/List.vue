<template>
	<ion-page>
		<ListView
			doctype="Employee Advance"
			:pageTitle="'员工预付款'"
			:tabButtons="TAB_BUTTONS"
			:fields="EMPLOYEE_ADVANCE_FIELDS"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"
import { inject } from "vue"

const __ = inject("$translate")
const TAB_BUTTONS = ["我的预付款", "团队预付款"]
const EMPLOYEE_ADVANCE_FIELDS = [
	"name",
	"employee",
	"employee_name",
	"status",
	"purpose",
	"advance_amount",
	"paid_amount",
	"claimed_amount",
	"return_amount",
	"posting_date",
	"currency",
]

const STATUS_FILTER_OPTIONS = [
	"Draft",
	"Paid",
	"Unpaid",
	"Claimed",
	"Returned",
	"Partly Claimed and Returned",
	"Cancelled",
]
const FILTER_CONFIG = [
	{
		fieldname: "status",
		fieldtype: "Select",
		label: "状态",
		options: STATUS_FILTER_OPTIONS,
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
	{
		fieldname: "advance_amount",
		fieldtype: "Currency",
		label: "预付金额",
	},
	{ fieldname: "paid_amount", fieldtype: "Currency", label: "已付金额" },
]
</script>
