<template>
	<ion-page>
		<ListView
			doctype="Attendance Request"
			:pageTitle="'考勤申请记录'"
			:fields="ATTENDANCE_REQUEST_FIELDS"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"
import { inject } from "vue"

const __ = inject("$translate")

const ATTENDANCE_REQUEST_FIELDS = ["name", "reason", "from_date", "to_date", "docstatus"]
const FILTER_CONFIG = [
	{
		fieldname: "shift",
		fieldtype: "Link",
		label: "班次",
		options: "Shift Type",
	},
	{ fieldname: "from_date", fieldtype: "Date", label: "开始日期" },
	{ fieldname: "to_date", fieldtype: "Date", label: "结束日期" },
]
</script>
