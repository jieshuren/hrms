<template>
	<ion-page>
		<ListView
			doctype="Employee Checkin"
			:pageTitle="'员工签到记录'"
			:fields="EMPLOYEE_CHECKIN_FIELDS"
			:filterConfig="FILTER_CONFIG"
		/>
	</ion-page>
</template>

<script setup>
import { IonPage } from "@ionic/vue"
import ListView from "@/components/ListView.vue"
import { inject } from "vue"

const __ = inject("$translate")

const EMPLOYEE_CHECKIN_FIELDS = ["name", "log_type", "time", "latitude", "longitude"]

const FILTER_CONFIG = [
	{
		fieldname: "log_type",
		fieldtype: "Select",
		label: "记录类型",
		options: "IN\nOUT",
	},
	{
		fieldname: "shift",
		fieldtype: "Link",
		label: "班次",
		options: "Shift Type",
	},
]
</script>
