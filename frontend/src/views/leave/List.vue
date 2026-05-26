<template>
	<ion-page>
		<ListView
			doctype="Leave Application"
			:pageTitle="'请假记录'"
			:tabButtons="TAB_BUTTONS"
			:fields="LEAVE_FIELDS"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"
import { inject } from "vue"

const __ = inject("$translate")
const TAB_BUTTONS = ["我的请假", "团队请假"]
const LEAVE_FIELDS = [
	"name",
	"employee",
	"employee_name",
	"leave_type",
	"from_date",
	"to_date",
	"total_leave_days",
	"status",
]
const STATUS_FILTER_OPTIONS = ["Open", "Approved", "Rejected"]
const FILTER_CONFIG = [
	{
		fieldname: "status",
		fieldtype: "Select",
		label: "状态",
		options: STATUS_FILTER_OPTIONS,
	},
	{
		fieldname: "leave_type",
		fieldtype: "Link",
		label: "请假类型",
		options: "Leave Type",
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
