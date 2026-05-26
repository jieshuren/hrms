<template>
	<BaseLayout pageTitle="考勤">
		<template #body>
			<div class="flex flex-col mt-7 mb-7 p-4 gap-7">
				<AttendanceCalendar />
				<div class="w-full">
					<router-link :to="{ name: 'AttendanceRequestFormView' }" v-slot="{ navigate }">
						<Button @click="navigate" variant="solid" class="w-full py-5 text-base">
							{{ "申请考勤" }}
						</Button>
					</router-link>
				</div>
				<div>
					<div class="text-lg text-gray-800 font-bold">{{ "最近考勤申请" }}</div>
					<RequestList
						:component="markRaw(AttendanceRequestItem)"
						:items="myAttendanceRequests?.data?.slice(0, 5)"
						:addListButton="true"
						:listButtonRoute="'AttendanceRequestListView'"
					/>
				</div>
				<div>
					<div class="text-lg text-gray-800 font-bold">{{ "即将到来的班次" }}</div>
					<RequestList
						:component="markRaw(ShiftAssignmentItem)"
						:items="upcomingShifts"
						:addListButton="true"
						listButtonRoute="ShiftAssignmentListView"
						:emptyStateMessage="'您没有即将到来的班次'"
					/>
				</div>
				<div class="w-full">
					<router-link :to="{ name: 'ShiftRequestFormView' }" v-slot="{ navigate }">
						<Button @click="navigate" variant="solid" class="w-full py-5 text-base">
							{{ "申请班次" }}
						</Button>
					</router-link>
				</div>
				<div>
					<div class="text-lg text-gray-800 font-bold">{{ "最近班次申请" }}</div>
					<RequestList
						:component="markRaw(ShiftRequestItem)"
						:items="myShiftRequests?.data?.slice(0, 5)"
						:addListButton="true"
						listButtonRoute="ShiftRequestListView"
					/>
				</div>
			</div>
		</template>
	</BaseLayout>
</template>

<script setup>
import { computed, inject, markRaw } from "vue"
import { createResource } from "frappe-ui"

import BaseLayout from "@/components/BaseLayout.vue"
import AttendanceRequestItem from "@/components/AttendanceRequestItem.vue"
import ShiftRequestItem from "@/components/ShiftRequestItem.vue"
import ShiftAssignmentItem from "@/components/ShiftAssignmentItem.vue"
import RequestList from "@/components/RequestList.vue"
import AttendanceCalendar from "@/components/AttendanceCalendar.vue"

import {
	getShiftDates,
	getTotalShiftDays,
	getShiftTiming,
	myAttendanceRequests,
	myShiftRequests,
} from "@/data/attendance"

const dayjs = inject("$dayjs")

const shifts = createResource({
	url: "hrms.api.get_shifts",
	auto: true,
	cache: "hrms:shifts",
	transform: (data) => {
		return data.map((assignment) => {
			assignment.doctype = "Shift Assignment"
			assignment.is_upcoming = !assignment.end_date || dayjs(assignment.end_date).isAfter(dayjs())
			assignment.shift_dates = getShiftDates(assignment)
			assignment.total_shift_days = getTotalShiftDays(assignment)
			assignment.shift_timing = getShiftTiming(assignment)
			return assignment
		})
	},
})

const upcomingShifts = computed(() => {
	const filteredShifts = shifts.data?.filter((shift) => shift.is_upcoming)

	// show only 5 upcoming shifts
	return filteredShifts?.slice(0, 5)
})
</script>
