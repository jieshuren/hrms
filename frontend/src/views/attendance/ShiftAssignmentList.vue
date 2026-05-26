<template>
	<ion-page>
		<ListView
			doctype="Shift Assignment"
			:pageTitle="'排班分配记录'"
			:fields="SHIFT_ASSIGNMENT_FIELDS"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { inject } from "vue"
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"

const __ = inject("$translate")

const SHIFT_ASSIGNMENT_FIELDS = ["name", "shift_type", "start_date", "end_date", "docstatus"]
const FILTER_CONFIG = [
	{
		fieldname: "shift_type",
		fieldtype: "Link",
		label: "班次类型",
		options: "Shift Type",
	},
	{ fieldname: "start_date", fieldtype: "Date", label: "开始日期" },
	{ fieldname: "end_date", fieldtype: "Date", label: "结束日期" },
]
</script>
