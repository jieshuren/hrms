<template>
	<ListItem
		:isTeamRequest="props.isTeamRequest"
		:employee="props.doc.employee"
		:employeeName="props.doc.employee_name"
	>
		<template #left>
			<LeaveIcon class="h-5 w-5 text-gray-500" />
			<div class="flex flex-col items-start gap-1.5">
				<div class="text-base font-normal text-gray-800">
					{{ __(props.doc.leave_type, null, "Leave Type") }}
				</div>
				<div class="text-xs font-normal text-gray-500">
					<span>{{ props.doc.leave_dates || getLeaveDates(props.doc) }}</span>
					<span class="whitespace-pre"> &middot; </span>
					<span class="whitespace-nowrap">{{ props.doc.total_leave_days }}天</span>
				</div>
			</div>
		</template>
		<template #right>
			<Badge variant="outline" :theme="colorMap[status]" :label="statusMap[status] || status" size="md" />
			<FeatherIcon name="chevron-right" class="h-5 w-5 text-gray-500" />
		</template>
	</ListItem>
</template>

<script setup>
import { computed } from "vue"
import { FeatherIcon, Badge } from "frappe-ui"

import ListItem from "@/components/ListItem.vue"
import LeaveIcon from "@/components/icons/LeaveIcon.vue"
import { getLeaveDates } from "@/data/leaves"

const statusMap = {
	Approved: "已批准",
	Rejected: "已拒绝",
	Open: "待审批",
	Draft: "草稿",
	Submitted: "已提交",
	Cancelled: "已取消",
}

const props = defineProps({
	doc: {
		type: Object,
	},
	isTeamRequest: {
		type: Boolean,
		default: false,
	},
	workflowStateField: {
		type: String,
		required: false,
	},
})

const status = computed(() => {
	return props.workflowStateField ? props.doc[props.workflowStateField] : props.doc.status
})

const colorMap = {
	Approved: "green",
	Rejected: "red",
	Open: "orange",
}
</script>
