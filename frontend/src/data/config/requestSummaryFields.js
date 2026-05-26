// This config holds the fields that should be shown in the request summary action sheet
// TODO: This should be config-driven somehow

export const LEAVE_FIELDS = [
	{
		fieldname: "name",
		label: "编号",
		fieldtype: "Data",
	},
	{
		fieldname: "leave_type",
		label: "请假类型",
		fieldtype: "Link",
	},
	{
		fieldname: "leave_dates",
		label: "请假日期",
		fieldtype: "Data",
	},
	{
		fieldname: "half_day",
		label: "半天",
		fieldtype: "Check",
	},
	{
		fieldname: "half_day_date",
		label: "半天日期",
		fieldtype: "Date",
	},
	{
		fieldname: "total_leave_days",
		label: "请假天数",
		fieldtype: "Float",
	},
	{
		fieldname: "employee",
		label: "员工",
		fieldtype: "Link",
	},
	{
		fieldname: "leave_balance",
		label: "假期余额",
		fieldtype: "Float",
	},
	{
		fieldname: "status",
		label: "状态",
		fieldtype: "Select",
	},
	{
		fieldname: "description",
		label: "原因",
		fieldtype: "Small Text",
	},
]

export const EXPENSE_CLAIM_FIELDS = [
	{
		fieldname: "name",
		label: "编号",
		fieldtype: "Data",
	},
	{
		fieldname: "posting_date",
		label: "日期",
		fieldtype: "Date",
	},
	{
		fieldname: "employee",
		label: "员工",
		fieldtype: "Link",
	},
	{
		fieldname: "expenses",
		label: "费用明细",
		fieldtype: "Table",
		componentName: "ExpenseItems",
	},
	{
		fieldname: "total_claimed_amount",
		label: "报销总额",
		fieldtype: "Currency",
	},
	{
		fieldname: "total_sanctioned_amount",
		label: "核准总额",
		fieldtype: "Currency",
	},
	{
		fieldname: "total_taxes_and_charges",
		label: "税费合计",
		fieldtype: "Currency",
	},
	{
		fieldname: "total_advance_amount",
		label: "预付总额",
		fieldtype: "Currency",
	},
	{
		fieldname: "grand_total",
		label: "合计",
		fieldtype: "Currency",
	},
	{
		fieldname: "status",
		label: "状态",
		fieldtype: "Select",
	},
	{
		fieldname: "approval_status",
		label: "审批状态",
		fieldtype: "Select",
	},
]

export const ATTENDANCE_REQUEST_FIELDS = [
	{
		fieldname: "name",
		label: "编号",
		fieldtype: "Data",
	},
	{
		fieldname: "attendance_dates",
		label: "考勤日期",
		fieldtype: "Data",
	},
	{
		fieldname: "total_attendance_days",
		label: "考勤天数",
		fieldtype: "Data",
	},
	{
		fieldname: "include_holidays",
		label: "包含节假日",
		fieldtype: "Check",
	},
	{
		fieldname: "shift",
		label: "班次",
		fieldtype: "Link",
	},
	{
		fieldname: "reason",
		label: "原因",
		fieldtype: "Select",
	},
	{
		fieldname: "employee",
		label: "员工",
		fieldtype: "Link",
	},
]

export const SHIFT_FIELDS = [
	{
		fieldname: "name",
		label: "编号",
		fieldtype: "Data",
	},
	{
		fieldname: "shift_type",
		label: "班次类型",
		fieldtype: "Link",
	},
	{
		fieldname: "shift_timing",
		label: "班次时间",
		fieldtype: "Data",
	},
	{
		fieldname: "shift_dates",
		label: "排班日期",
		fieldtype: "Data",
	},
	{
		fieldname: "total_shift_days",
		label: "排班天数",
		fieldtype: "Data",
	},
	{
		fieldname: "employee",
		label: "员工",
		fieldtype: "Link",
	},
]

export const SHIFT_REQUEST_FIELDS = [
	{
		fieldname: "name",
		label: "编号",
		fieldtype: "Data",
	},
	{
		fieldname: "shift_type",
		label: "班次类型",
		fieldtype: "Link",
	},
	{
		fieldname: "shift_dates",
		label: "排班日期",
		fieldtype: "Data",
	},
	{
		fieldname: "total_shift_days",
		label: "排班天数",
		fieldtype: "Data",
	},
	{
		fieldname: "employee",
		label: "员工",
		fieldtype: "Link",
	},
	{
		fieldname: "status",
		label: "状态",
		fieldtype: "Select",
	},
]

export const EMPLOYEE_CHECKIN_FIELDS = [
	{
		fieldname: "name",
		label: "编号",
		fieldtype: "Data",
	},
	{
		fieldname: "log_type",
		label: "打卡类型",
		fieldtype: "Data",
	},
	{
		fieldname: "date",
		label: "日期",
		fieldtype: "Date",
	},
	{
		fieldname: "formatted_time",
		label: "时间",
		fieldtype: "Time",
	},
	{
		fieldname: "formatted_latitude",
		label: "纬度",
		fieldtype: "Data",
	},
	{
		fieldname: "formatted_longitude",
		label: "经度",
		fieldtype: "Data",
	},
	{
		fieldname: "geolocation",
		label: "地理位置",
		fieldtype: "geolocation",
	},
]
