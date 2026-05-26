<template>
	<div v-if="!props.value" class="text-gray-600 text-base">-</div>

	<Badge
		v-else-if="props.fieldtype === 'Select'"
		variant="outline"
		:theme="colorMap[props.value]"
		:label="selectValueMap[props.value] || props.value"
		size="md"
	/>

	<div v-else-if="props.fieldtype === 'Date'" class="text-gray-900 text-base">
		{{ dayjs(props.value).format("D MMM YYYY") }}
	</div>

	<Input
		v-else-if="props.fieldtype === 'Check'"
		type="checkbox"
		label=""
		v-model="props.value"
		:disabled="true"
		class="rounded-sm text-gray-800"
	/>

	<div
		v-else-if="['Small Text', 'Text', 'Long Text'].includes(props.fieldtype)"
		class="text-gray-900 text-base bg-gray-100 rounded py-3 pl-3 mt-2"
	>
		{{ props.value }}
	</div>

	<EmployeeAvatar
		v-else-if="props.fieldtype === 'Link' && ['employee', 'reports_to'].includes(props.fieldname)"
		:employeeID="props.value"
		:showLabel="true"
	/>

	<div
		v-else-if="props.fieldtype === 'geolocation'"
		class="rounded border-4 translate-z-0 block overflow-hidden w-full h-170 mt-2"
	>
		<iframe
			width="100%"
			height="170"
			frameborder="0"
			scrolling="no"
			marginheight="0"
			marginwidth="0"
			style="border: 0"
			:src="`https://maps.google.com/maps?q=${getCoordinates(props.value).latitude},${
				getCoordinates(props.value).longitude
			}&hl=zh-CN&z=15&amp;output=embed`"
		>
		</iframe>
	</div>

	<div v-else class="text-gray-900 text-base">{{ props.value }}</div>
</template>

<script setup>
import { inject } from "vue"
import { Badge, FormControl, Input } from "frappe-ui"

import EmployeeAvatar from "@/components/EmployeeAvatar.vue"

const dayjs = inject("$dayjs")

const selectValueMap = {
	Approved: "已批准",
	Rejected: "已拒绝",
	Open: "待审批",
	Draft: "草稿",
	Submitted: "已提交",
	Cancelled: "已取消",
	Paid: "已支付",
	Unpaid: "未支付",
	Claimed: "已报销",
	Returned: "已归还",
	Active: "活跃",
	Inactive: "停用",
	"Half Day": "半天",
	"On Leave": "请假",
	Present: "出勤",
	Absent: "缺勤",
	"Work From Home": "居家办公",
	Holiday: "假期",
	"Partly Claimed and Returned": "部分报销并归还",
	"Approved & Draft": "已批准 & 草稿",
	"Approved & Unpaid": "已批准 & 未支付",
	"Approved & Submitted": "已批准 & 已提交",
}

const props = defineProps({
	value: [String, Number, Boolean, Array, Object],
	fieldtype: String,
	fieldname: String,
})

const colorMap = {
	Approved: "green",
	Rejected: "red",
	Open: "orange",
}

const getCoordinates = (value) => {
	const [longitude, latitude] = JSON.parse(value).features[0].geometry.coordinates
	return { longitude, latitude }
}
</script>
