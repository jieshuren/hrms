<template>
	<ion-page>
		<ListView
			doctype="Shift Request"
			pageTitle="班次申请记录"
			:tabButtons="TAB_BUTTONS"
			:fields="SHIFT_REQUEST_FIELDS"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { inject } from "vue"
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"

const __ = inject("$translate")
const TAB_BUTTONS = ["我的申请", "团队申请"]
const SHIFT_REQUEST_FIELDS = [
	"name",
	"employee",
	"employee_name",
	"shift_type",
	"from_date",
	"to_date",
	"approver",
	"status",
	"docstatus",
]
const STATUS_FILTER_OPTIONS = ["Draft", "Approved", "Rejected"]
const FILTER_CONFIG = [
	{
		fieldname: "status",
		fieldtype: "Select",
		label: "状态",
		options: STATUS_FILTER_OPTIONS,
	},
	{
		fieldname: "shift_type",
		fieldtype: "Link",
		label: "班次类型",
		options: "Shift Type",
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
	{ fieldname: "from_date", fieldtype: "Date", label: "开始日期" },
	{ fieldname: "to_date", fieldtype: "Date", label: "结束日期" },
]
</script>
