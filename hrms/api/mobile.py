from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from hrms.api import get_current_employee_info
from hrms.hr.doctype.employee_attendance_tool.employee_attendance_tool import _clean_existing_attendance

PROCUREMENT_ROLES = {
	"System Manager",
	"Purchase Manager",
	"Purchase User",
	"Stock Manager",
	"Accounts Manager",
	"Accounts User",
}

ATTENDANCE_MANAGER_ROLES = {
	"System Manager",
	"HR Manager",
	"HR User",
	"Attendance Manager",
}

PROJECT_EDITOR_ROLES = ATTENDANCE_MANAGER_ROLES | {
	"Project Manager",
}

MATERIAL_REQUEST_STAGE_DEFS = [
	{
		"key": "material_request",
		"title": "物料申请",
		"caption": "需求部门提出采购需求",
		"accent": "brand",
	},
	{
		"key": "request_for_quotation",
		"title": "询价",
		"caption": "向供应商发出询价",
		"accent": "info",
	},
	{
		"key": "supplier_quotation",
		"title": "供应商报价",
		"caption": "收集并比较报价",
		"accent": "warning",
	},
	{
		"key": "purchase_order",
		"title": "采购订单",
		"caption": "确认供应商并正式下单",
		"accent": "warning",
	},
	{
		"key": "purchase_receipt",
		"title": "采购收货",
		"caption": "到货验收并入库",
		"accent": "success",
	},
	{
		"key": "purchase_invoice",
		"title": "采购发票",
		"caption": "挂账并核对账单",
		"accent": "brand",
	},
	{
		"key": "payment_entry",
		"title": "付款",
		"caption": "完成对供应商付款",
		"accent": "success",
	},
]


def _current_roles() -> set[str]:
	return set(frappe.get_roles(frappe.session.user))


def _has_any_role(role_names: set[str]) -> bool:
	return bool(_current_roles() & set(role_names))


def _can_manage_attendance() -> bool:
	return _has_any_role(ATTENDANCE_MANAGER_ROLES)


def _can_manage_projects() -> bool:
	return _has_any_role(PROJECT_EDITOR_ROLES)


def _can_manage_procurement() -> bool:
	return _has_any_role(PROCUREMENT_ROLES)


def _parse_name_list(value: str | list[str] | None) -> list[str]:
	if isinstance(value, str):
		text = value.strip()
		if not text:
			return []
		try:
			parsed = json.loads(text)
		except Exception:
			parsed = [text]
	elif isinstance(value, list):
		parsed = value
	else:
		parsed = []

	seen: set[str] = set()
	result: list[str] = []
	for raw in parsed:
		name = str(raw or "").strip()
		if not name or name in seen:
			continue
		seen.add(name)
		result.append(name)
	return result


# ============================================================
# 移动端 + 微信小程序共用：票据识别、费用分类、出纳付款等
# 经 hrms.api.__init__ 再导出，对外 URL 仍为 hrms.api.<函数名>
# ============================================================

@frappe.whitelist(methods=["POST"])
def classify_expense_text(text_content: str, category_hierarchy: str, model: str | None = None) -> dict:
	"""
	Based on pure text content, classify expenses using AI. No image needed, faster and cheaper.
	Config: receipt_recognition.json (text_classification section).
	"""
	import json
	import requests as http_requests
	from requests.exceptions import RequestException

	from hrms.receipt_recognition_settings import (
		build_classification_prompt,
		classification_http_headers,
		get_receipt_ai_config,
	)

	if not text_content:
		return {"报销类型": "无法识别"}

	cfg = get_receipt_ai_config()
	tc = cfg.get("text_classification") or {}
	va = cfg.get("vision_api") or {}
	fallback = (
		tc.get("fallback_response")
		if isinstance(tc.get("fallback_response"), dict)
		else {"报销类型": "无法识别"}
	)

	endpoint = str(tc.get("endpoint") or va.get("endpoint") or "").strip()
	if not endpoint:
		return fallback

	headers = classification_http_headers(cfg)
	if "Authorization" not in headers:
		return fallback

	ai_model = (model or "").strip() or str(tc.get("model_default") or "hy3-preview").strip()
	prompt = build_classification_prompt(cfg, text_content, category_hierarchy)

	payload = {
		"model": ai_model,
		"messages": [{"role": "user", "content": prompt}],
		"temperature": float(tc.get("temperature") if tc.get("temperature") is not None else 0.1),
		"stream": False,
	}
	if tc.get("use_response_format_json_object"):
		payload["response_format"] = {"type": "json_object"}
	payload_str = json.dumps(payload, ensure_ascii=False)

	timeout = int(tc.get("timeout_seconds") or 30)

	try:
		response = http_requests.post(endpoint, headers=headers, data=payload_str.encode("utf-8"), timeout=timeout)
		response.raise_for_status()
		res_data = response.json()
		content = res_data["choices"][0]["message"]["content"]
		return json.loads(content)
	except Exception as e:
		frappe.log_error(f"Classification Error: {e}", "Expense Classification Failure")
		return fallback


def _extract_braced_json_fragments(text: str) -> list:
	"""Extract outermost {...} JSON fragments from text that may contain prefixes/suffixes."""
	result = []
	start = -1
	depth = 0
	in_string = False
	escape = False
	for i, ch in enumerate(text):
		if in_string:
			if escape:
				escape = False
				continue
			if ch == "\\":
				escape = True
				continue
			if ch == '"':
				in_string = False
			continue
		if ch == '"':
			in_string = True
			continue
		if ch == "{":
			if depth == 0:
				start = i
			depth += 1
			continue
		if ch == "}":
			if depth <= 0:
				continue
			depth -= 1
			if depth == 0 and start >= 0:
				result.append(text[start : i + 1])
				start = -1
	return result


def _parse_json_from_model_content(content) -> dict | list:
	"""Parse vision model message.content: full JSON, markdown code block, or JSON object in text."""
	import json
	import re

	if content is None:
		raise ValueError("empty content")
	s = str(content).strip()
	m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, flags=re.IGNORECASE)
	if m:
		s = m.group(1).strip()
	try:
		return json.loads(s)
	except ValueError:
		pass
	for frag in _extract_braced_json_fragments(s):
		try:
			obj = json.loads(frag)
			if isinstance(obj, (dict, list)):
				return obj
		except ValueError:
			continue
	raise ValueError("模型返回内容中未找到有效 JSON")


def _do_recognize_receipt(image_base64, file_type="image", model=None, server_url=None, category_hierarchy=None):
	"""Shared recognition logic, called by recognize_receipt_image and recognize_receipt_file."""
	import json
	import requests as http_requests
	from requests.exceptions import RequestException

	from hrms.receipt_recognition_settings import (
		build_receipt_vision_prompt,
		get_receipt_ai_config,
		vision_http_headers,
	)

	if not image_base64:
		frappe.throw("识别请求失败：未收到数据")

	cfg = get_receipt_ai_config()
	va = cfg.get("vision_api") or {}
	media = cfg.get("media") or {}
	pd = cfg.get("payload_defaults") or {}

	is_pdf = file_type == "pdf"
	doc_word = "PDF 文件" if is_pdf else "图片"
	prompt = build_receipt_vision_prompt(cfg, doc_word, category_hierarchy or None)

	if "," in image_base64:
		pure_base64 = image_base64.split(",")[1]
	else:
		pure_base64 = image_base64

	if is_pdf:
		mime = str(media.get("pdf_data_url_mime") or "application/pdf").strip()
	else:
		mime = str(media.get("image_data_url_mime") or "image/jpeg").strip()
	image_url = f"data:{mime};base64,{pure_base64}"

	resolved_model = (model or "").strip() or str(va.get("model_default") or "").strip()
	if not resolved_model:
		frappe.throw("未配置视觉识别模型：请在 receipt_recognition.json 的 vision_api.model_default 或请求参数 model 中设置")

	endpoint = (server_url or "").strip() or str(va.get("endpoint") or "").strip()
	if not endpoint:
		frappe.throw(
			"未配置视觉识别远程地址 vision_api.endpoint（项目根目录 receipt_recognition.json 或站点 receipt_recognition / hunyuan_endpoint）"
		)

	headers = vision_http_headers(cfg)
	if "Authorization" not in headers:
		frappe.throw(
			"未配置 vision_api.api_key（项目根目录 receipt_recognition.json、站点 receipt_recognition.vision_api.api_key 或兼容项 hunyuan_api_key）"
		)

	timeout = int(va.get("timeout_seconds") or 120)
	temperature = float(va.get("temperature") if va.get("temperature") is not None else 0.2)
	stream = bool(pd.get("stream")) if "stream" in pd else False

	payload = {
		"model": resolved_model,
		"messages": [
			{
				"role": "user",
				"content": [
					{"type": "text", "text": prompt},
					{"type": "image_url", "image_url": {"url": image_url}},
				],
			}
		],
		"temperature": temperature,
		"stream": stream,
	}
	payload_str = json.dumps(payload, ensure_ascii=False)

	try:
		response = http_requests.post(endpoint, headers=headers, data=payload_str.encode("utf-8"), timeout=timeout)
		response.raise_for_status()
		res_data = response.json()

		try:
			content = res_data["choices"][0]["message"]["content"]
			parsed = _parse_json_from_model_content(content)
			if isinstance(parsed, list):
				if parsed and all(isinstance(x, dict) for x in parsed):
					parsed = {"detail_list": parsed}
				else:
					frappe.throw("视觉模型返回的 JSON 数组格式无效")
			if not isinstance(parsed, dict):
				frappe.throw("视觉模型应返回 JSON 对象或明细对象数组")
			return parsed
		except (KeyError, IndexError, ValueError) as e:
			frappe.log_error(f"Vision Model Parsing Error: {e}\nRaw: {res_data}", "Receipt Recognition Failure")
			frappe.throw("视觉模型返回内容解析失败：" + str(e))

	except RequestException as exc:
		frappe.throw(f"连接视觉识别服务失败：{exc}")


def _generate_pdf_thumbnail(file_data, width=300):
	"""Render the first page of a PDF as a JPEG thumbnail, return base64 string."""
	import base64
	import io

	try:
		import fitz
	except ImportError:
		frappe.log_error("PyMuPDF (fitz) 未安装，无法生成 PDF 缩略图", "PDF Thumbnail")
		return ""

	try:
		doc = fitz.open(stream=file_data, filetype="pdf")
		if not doc.page_count:
			doc.close()
			frappe.log_error("PDF 文件无页面", "PDF Thumbnail")
			return ""
		page = doc.load_page(0)
		zoom = width / (page.rect.width or 612)
		mat = fitz.Matrix(zoom, zoom)
		pix = page.get_pixmap(matrix=mat)
		img_bytes = pix.tobytes("jpeg")
		doc.close()
		thumbnail = base64.b64encode(img_bytes).decode("utf-8")
		return thumbnail
	except Exception as e:
		frappe.log_error(f"PDF 缩略图生成失败: {e}", "PDF Thumbnail")
		return ""


@frappe.whitelist()
def get_pdf_thumbnail(file_url):
	"""Generate and return base64 thumbnail for the first page of an uploaded PDF file."""
	import base64

	if not file_url:
		frappe.throw("缺少 file_url 参数")

	file_url = str(file_url).strip()
	if file_url.startswith(("http://", "https://")):
		import requests as http_requests
		try:
			resp = http_requests.get(file_url, timeout=15)
			resp.raise_for_status()
			file_data = resp.content
		except Exception:
			frappe.throw("无法下载文件")
	else:
		import os
		file_path = frappe.get_site_path("public", file_url.lstrip("/"))
		if not os.path.exists(file_path):
			private_path = frappe.get_site_path("private", file_url.lstrip("/"))
			if os.path.exists(private_path):
				file_path = private_path
			else:
				frappe.throw("文件不存在")
		with open(file_path, "rb") as f:
			file_data = f.read()

	thumbnail = _generate_pdf_thumbnail(file_data)
	if not thumbnail:
		frappe.throw("无法生成 PDF 缩略图")
	return {"thumbnail_base64": thumbnail}


@frappe.whitelist(methods=["POST"])
def recognize_receipt_image(**kwargs):
	"""
	AI receipt recognition via backend proxy (image as base64).
	Config: receipt_recognition.json. Use recognize_receipt_file for PDF uploads.
	"""
	image_base64 = kwargs.get("image_base64")
	model = kwargs.get("model")
	server_url = kwargs.get("server_url")
	category_hierarchy = kwargs.get("category_hierarchy")
	file_type = kwargs.get("file_type") or "image"

	if not image_base64:
		frappe.throw("识别请求失败：未收到图片数据 (image_base64)")

	return _do_recognize_receipt(image_base64, file_type, model, server_url, category_hierarchy)


@frappe.whitelist(methods=["POST"])
def recognize_receipt_file(**kwargs):
	"""
	File upload-based receipt recognition (mainly for PDFs).
	Solves the issue of large base64 payloads exceeding request body limits.
	"""
	import base64
	import json

	from frappe.handler import ALLOWED_MIMETYPES

	file_type = kwargs.get("file_type") or "image"
	model = kwargs.get("model")
	server_url = kwargs.get("server_url")
	category_hierarchy = kwargs.get("category_hierarchy")

	uploaded = frappe.request.files.get("file")
	if not uploaded:
		frappe.throw("未收到文件")

	filename = uploaded.filename or ""
	content_type = uploaded.content_type or ""

	if content_type and content_type not in ALLOWED_MIMETYPES:
		frappe.throw(f"不支持的文件类型：{content_type}")

	file_data = uploaded.stream.read()
	if not file_data:
		frappe.throw("文件内容为空")

	pure_base64 = base64.b64encode(file_data).decode("utf-8")

	if content_type == "application/pdf":
		file_type = "pdf"

	result = _do_recognize_receipt(pure_base64, file_type, model, server_url, category_hierarchy)

	if file_type == "pdf":
		thumbnail_base64 = _generate_pdf_thumbnail(file_data)
		if isinstance(result, dict):
			result["thumbnail_base64"] = thumbnail_base64 or ""
		elif isinstance(result, str):
			try:
				parsed = json.loads(result)
				parsed["thumbnail_base64"] = thumbnail_base64 or ""
				result = parsed
			except Exception:
				result = {"message": result, "thumbnail_base64": thumbnail_base64 or ""}

	return result


@frappe.whitelist()
def withdraw_expense_claim_to_draft(name: str):
	"""Withdraw a pending expense claim back to draft for the applicant to edit and resubmit."""
	if not name:
		frappe.throw("缺少报销单编号")

	doc = frappe.get_doc("Expense Claim", name)
	doc.check_permission("write")

	if frappe.session.user != "Administrator" and doc.owner != frappe.session.user:
		frappe.throw("只有单据创建人可以撤回修改")

	if getattr(doc, "docstatus", 0) != 0:
		frappe.throw("只有未入账的待审批报销单才能撤回修改")

	if getattr(doc, "custom_related_journal_entry", None) or getattr(doc, "custom_related_payment_entry", None):
		frappe.throw("该单据已关联财务凭证，无法撤回修改")

	values = {
		"workflow_state": "Draft",
		"approval_status": "Draft",
		"custom_peer_verifier": "",
		"custom_peer_verified_on": None,
		"custom_dept_head_approver": "",
		"custom_dept_head_approved_on": None,
		"custom_finance_approver": "",
		"custom_finance_approved_on": None,
		"custom_gm_approver": "",
		"custom_gm_approved_on": None,
	}
	frappe.db.set_value("Expense Claim", name, values, update_modified=True)

	from frappe.workflow.doctype.workflow_action.workflow_action import clear_workflow_actions

	clear_workflow_actions("Expense Claim", name)
	frappe.db.commit()

	return frappe.get_doc("Expense Claim", name).as_dict()


@frappe.whitelist(methods=["POST"])
def update_expense_claim_detail_type(claim_name: str, detail_name: str, expense_type: str) -> dict:
	"""Before finance approval, allow finance approver to correct expense classification of a detail line."""
	claim_name = (claim_name or "").strip()
	detail_name = (detail_name or "").strip()
	expense_type = (expense_type or "").strip()
	if not claim_name or not detail_name or not expense_type:
		frappe.throw("参数不完整")

	claim = frappe.get_doc("Expense Claim", claim_name)
	claim.check_permission("write")
	if getattr(claim, "docstatus", 0) != 0:
		frappe.throw("仅未提交单据允许修改分类")
	if (getattr(claim, "workflow_state", "") or "").strip() != "Pending Finance Approval":
		frappe.throw("仅待财务审核阶段允许修改分类")
	if "Finance Approver" not in frappe.get_roles(frappe.session.user):
		frappe.throw("仅财务审批人允许修改分类")
	if not frappe.db.exists("Expense Claim Type", expense_type):
		frappe.throw("费用分类不存在")

	target_row = None
	for row in claim.get("expenses") or []:
		if row.name == detail_name:
			target_row = row
			break
	if not target_row:
		frappe.throw("未找到对应的报销明细")

	target_row.expense_type = expense_type
	target_row.default_account = None
	claim.set_expense_account(validate=False)
	claim.calculate_total_amount()
	claim.calculate_taxes()
	claim.flags.skip_ai_category_classification = True
	claim.save()
	claim.publish_update()

	return {"name": claim.name, "detail_name": detail_name, "expense_type": expense_type}


@frappe.whitelist(methods=["POST"])
def reclassify_expense_claim_detail_type_by_ai(claim_name: str, detail_name: str) -> dict:
	"""Before finance approval, run AI re-classification on a single expense detail line."""
	claim_name = (claim_name or "").strip()
	detail_name = (detail_name or "").strip()
	if not claim_name or not detail_name:
		frappe.throw("参数不完整")

	claim = frappe.get_doc("Expense Claim", claim_name)
	claim.check_permission("write")
	if getattr(claim, "docstatus", 0) != 0:
		frappe.throw("仅未提交单据允许重新分类")
	if (getattr(claim, "workflow_state", "") or "").strip() != "Pending Finance Approval":
		frappe.throw("仅待财务审核阶段允许重新分类")
	if "Finance Approver" not in frappe.get_roles(frappe.session.user):
		frappe.throw("仅财务审批人允许重新分类")

	target_row = None
	for row in claim.get("expenses") or []:
		if row.name == detail_name:
			target_row = row
			break
	if not target_row:
		frappe.throw("未找到对应的报销明细")

	allowed_types = [
		row.name
		for row in frappe.get_all("Expense Claim Type", fields=["name"])
		if (row.name or "").strip() != "无法识别"
	]
	if not allowed_types:
		frappe.throw("未配置报销分类")

	from hrms.api import get_expense_claim_type_category_map

	type_category_map = get_expense_claim_type_category_map(claim.company)
	grouped: dict[str, list[str]] = {}
	for expense_type in allowed_types:
		category = type_category_map.get(expense_type) or "无法识别"
		grouped.setdefault(category, []).append(expense_type)

	lines = ["【可选报销类型层级结构】"]
	for category, types in grouped.items():
		lines.append(f"- {category}:")
		for expense_type in types:
			lines.append(f"  - {expense_type}")
	hierarchy_text = "\n".join(lines)

	text_to_classify = f"单据名称: {target_row.custom_receipt_item_name or ''}, 说明: {target_row.description or ''}"
	if not text_to_classify.replace("单据名称: , 说明: ", "").strip():
		frappe.throw("该条明细缺少可用于分类的文本内容")

	response = classify_expense_text(
		text_content=text_to_classify,
		category_hierarchy=hierarchy_text,
	)
	predicted_type = str((response or {}).get("报销类型") or "").strip()
	if not predicted_type or predicted_type == "无法识别":
		frappe.throw("AI 未识别出有效分类，请手动选择")
	if predicted_type not in allowed_types:
		frappe.throw("AI 返回的分类不在可选范围内，请手动选择")

	target_row.expense_type = predicted_type
	target_row.default_account = None
	claim.set_expense_account(validate=False)
	claim.calculate_total_amount()
	claim.calculate_taxes()
	claim.flags.skip_ai_category_classification = True
	claim.save()
	claim.publish_update()

	return {"name": claim.name, "detail_name": detail_name, "expense_type": predicted_type}


@frappe.whitelist()
def sync_bank_accounts_to_mop():
	"""Sync bank accounts from chart of accounts to generate Chinese-mode-of-payment entries."""
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if not company:
		return "未找到默认公司"

	# 1. Localize common payment modes
	translations = {
		"Wire Transfer": "银行转账",
		"Cash": "现金支付",
		"Credit Card": "信用卡",
		"Check": "支票"
	}
	for old_name, new_name in translations.items():
		if frappe.db.exists("Mode of Payment", old_name):
			frappe.rename_doc("Mode of Payment", old_name, new_name, force=True, merge=True)

	# 2. Sync from accounts
	bank_accounts = frappe.get_all("Account",
		filters={"account_type": ["in", ["Bank", "Cash"]], "is_group": 0, "company": company},
		fields=["name", "account_type", "account_name"]
	)

	results = []
	for acc in bank_accounts:
		raw_name = acc.account_name
		clean_name = raw_name.split(" - ")[1] if " - " in raw_name else raw_name
		clean_name = clean_name.replace(" - 华烁", "").strip()

		if not frappe.db.exists("Mode of Payment", clean_name):
			doc = frappe.new_doc("Mode of Payment")
			doc.mode_of_payment = clean_name
			doc.type = acc.account_type
			doc.enabled = 1
			doc.insert(ignore_permissions=True)

		mop_doc = frappe.get_doc("Mode of Payment", clean_name)
		mop_doc.set("accounts", [])
		mop_doc.append("accounts", {
			"company": company,
			"default_account": acc.name
		})
		mop_doc.save(ignore_permissions=True)
		results.append(f"{clean_name} -> {acc.name}")

	frappe.db.commit()
	return results


@frappe.whitelist()
def get_cashier_modes_of_payment(company: str | None = None) -> list[dict]:
	"""Get available payment modes with their linked accounts for cashier."""
	if not company:
		company = frappe.db.get_single_value("Global Defaults", "default_company")

	mops = frappe.get_all(
		"Mode of Payment",
		filters={"enabled": 1},
		fields=["name", "type"],
		order_by="name asc"
	)

	res = []
	for mop in mops:
		account = frappe.db.get_value("Mode of Payment Account",
			{"parent": mop.name, "company": company},
			"default_account"
		)
		res.append({
			"name": mop.name,
			"type": mop.type,
			"account": account or ""
		})
	return res


@frappe.whitelist(methods=["POST"])
def confirm_expense_claim_payment(claim_name: str, mop: str, advances: list | str = None, action: str = None) -> dict:
	"""Cashier confirms payment: update payment method, advance deductions, and execute workflow action."""
	if isinstance(advances, str) and advances.strip():
		try:
			advances = json.loads(advances)
		except Exception:
			advances = []

	if not advances or not isinstance(advances, list):
		advances = []

	doc = frappe.get_doc("Expense Claim", claim_name)

	# Permission check
	user_roles = frappe.get_roles(frappe.session.user)
	allowed_roles = ["Cashier", "Administrator", "Finance Approver", "出纳", "财务人员", "财务审核"]

	has_permission = any(role in user_roles for role in allowed_roles)

	if not has_permission:
		frappe.logger().warning(f"Permission Denied for {frappe.session.user}: Roles found {user_roles}")
		frappe.throw("只有出纳或财务人员允许执行此操作", frappe.PermissionError)

	# 1. Update payment method fields
	doc.custom_cashier_mode_of_payment = mop
	doc.custom_cashier_user = frappe.session.user
	doc.custom_paid_on = frappe.utils.now_datetime()

	# 2. Update advances child table
	doc.set("advances", [])
	for row in advances:
		if not row.get("employee_advance"):
			continue
		doc.append("advances", {
			"employee_advance": row.get("employee_advance"),
			"allocated_amount": row.get("allocated_amount"),
			"advance_amount": row.get("advance_amount"),
			"unclaimed_amount": row.get("unclaimed_amount")
		})

	# 3. Execute workflow action
	doc.flags.ignore_validate_update_after_submit = True

	from frappe.workflow.doctype.workflow_action.workflow_action import apply_workflow
	apply_workflow(doc, action)

	return {"name": doc.name, "workflow_state": doc.workflow_state}



def _get_current_employee_row(raise_exception: bool = True) -> frappe._dict | None:
	employee = frappe._dict(get_current_employee_info() or {})
	if employee or not raise_exception:
		return employee
	frappe.throw(_("未找到当前员工档案"), frappe.PermissionError)


def _get_employee_lookup(
	*,
	user_ids: list[str] | None = None,
	employee_ids: list[str] | None = None,
) -> tuple[dict[str, frappe._dict], dict[str, frappe._dict]]:
	filters = {}
	if user_ids:
		filters["user_id"] = ("in", user_ids)
	if employee_ids:
		filters["name"] = ("in", employee_ids)
	if not filters:
		return {}, {}

	rows = frappe.get_all(
		"Employee",
		fields=["name", "employee_name", "department", "designation", "status", "user_id"],
		filters=filters,
		limit_page_length=500,
		order_by="employee_name asc",
	)
	by_name = {row.name: frappe._dict(row) for row in rows}
	by_user_id = {
		row.user_id: frappe._dict(row)
		for row in rows
		if row.get("user_id")
	}
	return by_name, by_user_id


def _get_material_request_item_map(names: list[str]) -> dict[str, list[frappe._dict]]:
	if not names:
		return {}

	rows = frappe.get_all(
		"Material Request Item",
		fields=["parent", "item_code", "item_name", "qty", "uom", "warehouse", "schedule_date", "description"],
		filters={"parent": ("in", names)},
		order_by="idx asc",
		limit_page_length=1000,
	)
	item_map: dict[str, list[frappe._dict]] = {}
	for row in rows:
		item_map.setdefault(row.parent, []).append(frappe._dict(row))
	return item_map


def _build_request_item_summary(items: list[frappe._dict]) -> str:
	parts: list[str] = []
	for row in items[:3]:
		label = str(row.get("item_name") or row.get("item_code") or "").strip()
		if not label:
			continue
		qty = flt(row.get("qty"))
		uom = str(row.get("uom") or "").strip()
		if qty:
			parts.append(f"{label} x {qty:g}{(' ' + uom) if uom else ''}")
		else:
			parts.append(label)
	if not parts:
		return ""
	if len(items) > 3:
		parts.append(f"等{len(items)}项")
	return "、".join(parts)


def _material_request_queue_label(row: frappe._dict) -> str:
	docstatus = cint(row.get("docstatus"))
	status = str(row.get("status") or "").strip()
	per_ordered = flt(row.get("per_ordered"))
	workflow_state = row.get("workflow_state")
	if workflow_state and workflow_state not in {"Draft", "草稿"}:
		return workflow_state

	if docstatus == 0:
		return "草稿待提交"
	if status in {"Stopped", "Cancelled"}:
		return "已结束"
	if status == "Ordered" or per_ordered >= 99.99:
		return "已生成采购单"
	if per_ordered > 0:
		return "部分下单"
	return "待采购"

def _serialize_material_request_rows(rows: list[frappe._dict]) -> list[dict]:
	if not rows:
		return []

	names = [row.name for row in rows if row.get("name")]
	_, employee_by_user_id = _get_employee_lookup(
		user_ids=[str(row.get("owner") or "").strip() for row in rows if row.get("owner")]
	)
	item_map = _get_material_request_item_map(names)

	result: list[dict] = []
	for row in rows:
		items = item_map.get(row.name, [])
		employee = employee_by_user_id.get(str(row.get("owner") or "").strip())
		result.append(
			{
				"name": row.name,
				"transaction_date": row.get("transaction_date"),
				"schedule_date": row.get("schedule_date"),
				"status": row.get("status") or "Draft",
				"material_request_type": row.get("material_request_type") or "Purchase",
				"owner": row.get("owner"),
				"modified": row.get("modified"),
				"employee_name": employee.get("employee_name") if employee else "",
				"department": employee.get("department") if employee else "",
				"items": _build_request_item_summary(items),
				"item_count": len(items),
				"qty_total": sum(flt(item.get("qty")) for item in items),
				"per_ordered": flt(row.get("per_ordered")),
				"workflow_state": row.get("workflow_state") or "",
				"workflow_label": _material_request_queue_label(row),
			}
		)
	return result


def _get_mr_list_fields() -> list[str]:
	fields = [
		"name",
		"transaction_date",
		"schedule_date",
		"status",
		"material_request_type",
		"owner",
		"per_ordered",
		"docstatus",
		"modified",
		"creation",
	]
	if frappe.get_meta("Material Request").has_field("workflow_state"):
		fields.append("workflow_state")
	return fields


def _ensure_material_request_access(doc: frappe.model.document.Document) -> None:
	if _can_manage_procurement():
		return
	if doc.owner == frappe.session.user:
		return
	frappe.throw(_("您无权查看这条物料申请"), frappe.PermissionError)


def _get_child_parent_names(doctype: str, fieldname: str, value: str) -> list[str]:
	rows = frappe.get_all(
		doctype,
		fields=["parent"],
		filters={fieldname: value},
		order_by="modified desc",
		limit_page_length=500,
	)
	seen: set[str] = set()
	result: list[str] = []
	for row in rows:
		parent = str(row.get("parent") or "").strip()
		if not parent or parent in seen:
			continue
		seen.add(parent)
		result.append(parent)
	return result


def _get_docs_by_names(doctype: str, names: list[str], fields: list[str]) -> list[frappe._dict]:
	if not names:
		return []
	return frappe.get_all(
		doctype,
		fields=fields,
		filters={"name": ("in", names)},
		order_by="modified desc",
		limit_page_length=500,
	)


def _serialize_related_docs(rows: list[frappe._dict], date_field: str) -> list[dict]:
	result: list[dict] = []
	for row in rows:
		result.append(
			{
				"name": row.get("name"),
				"status": row.get("status") or "",
				"docstatus": cint(row.get("docstatus")),
				"supplier": row.get("supplier") or "",
				"date": row.get(date_field),
				"grand_total": flt(row.get("grand_total")),
				"outstanding_amount": flt(row.get("outstanding_amount")),
				"paid_amount": flt(row.get("paid_amount")),
			}
		)
	return result


def _normalize_material_request_payload(payload: str | dict | None, kwargs: dict) -> frappe._dict:
	if isinstance(payload, dict):
		data = payload
	elif isinstance(payload, str):
		text = payload.strip()
		data = frappe.parse_json(text) if text else {}
	else:
		data = kwargs or {}
	return frappe._dict(data or {})


def _validate_material_request_payload(data: frappe._dict) -> list[frappe._dict]:
	if not str(data.get("company") or "").strip():
		frappe.throw(_("请选择公司"))
	if not data.get("transaction_date"):
		frappe.throw(_("请选择申请日期"))
	if not data.get("schedule_date"):
		frappe.throw(_("请选择需求日期"))

	items = data.get("items") or []
	if not isinstance(items, list) or not items:
		frappe.throw(_("请至少填写一条物料明细"))

	normalized_items: list[frappe._dict] = []
	item_codes = []
	for idx, raw in enumerate(items, start=1):
		row = frappe._dict(raw or {})
		item_code = str(row.get("item_code") or "").strip()
		if not item_code:
			frappe.throw(_("第 {0} 行缺少物料").format(idx))
		qty = flt(row.get("qty"))
		if qty <= 0:
			frappe.throw(_("第 {0} 行数量必须大于 0").format(idx))
		item_codes.append(item_code)
		normalized_items.append(row)

	item_rows = frappe.get_all(
		"Item",
		fields=["name", "item_name", "stock_uom", "purchase_uom", "has_variants"],
		filters={"name": ("in", item_codes)},
		limit_page_length=500,
	)
	item_meta = {row.name: frappe._dict(row) for row in item_rows}
	for idx, row in enumerate(normalized_items, start=1):
		meta = item_meta.get(str(row.get("item_code") or "").strip())
		if not meta:
			frappe.throw(_("第 {0} 行物料不存在").format(idx))
		if not str(row.get("uom") or "").strip():
			row.uom = meta.get("purchase_uom") or meta.get("stock_uom")
		if not str(row.get("uom") or "").strip():
			frappe.throw(_("第 {0} 行缺少单位").format(idx))
	return normalized_items


def _apply_material_request_fields(doc, data: frappe._dict, items: list[frappe._dict]) -> None:
	doc.material_request_type = str(data.get("material_request_type") or "Purchase").strip()
	doc.company = str(data.get("company") or "").strip()
	doc.transaction_date = getdate(data.get("transaction_date"))
	doc.schedule_date = getdate(data.get("schedule_date"))
	doc.set_warehouse = str(data.get("set_warehouse") or "").strip() or None
	doc.buying_price_list = str(data.get("buying_price_list") or "").strip() or None
	doc.from_warehouse = str(data.get("from_warehouse") or "").strip() or None
	doc.set("items", [])
	for item in items:
		row = doc.append("items", {})
		if item.get("name"):
			row.name = item.get("name")
		row.item_code = str(item.get("item_code") or "").strip()
		row.qty = flt(item.get("qty"))
		row.uom = str(item.get("uom") or "").strip()
		row.rate = flt(item.get("rate"))
		row.schedule_date = getdate(item.get("schedule_date") or data.get("schedule_date"))
		row.from_warehouse = str(item.get("from_warehouse") or data.get("from_warehouse") or "").strip() or None
		row.warehouse = str(item.get("warehouse") or data.get("set_warehouse") or "").strip() or None
		row.project = str(item.get("project") or "").strip() or None
		row.description = str(item.get("description") or "").strip() or None


@frappe.whitelist()
def get_mobile_companies() -> list[dict]:
	return frappe.get_all(
		"Company",
		fields=["name", "company_name"],
		order_by="company_name asc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_mobile_warehouses(company: str | None = None) -> list[dict]:
	filters = {"disabled": 0}
	if str(company or "").strip():
		filters["company"] = company
	return frappe.get_all(
		"Warehouse",
		fields=["name", "warehouse_name", "company"],
		filters=filters,
		order_by="warehouse_name asc",
		limit_page_length=300,
	)


@frappe.whitelist()
def get_mobile_projects(department: str | None = None) -> list[dict]:
	filters = {
		"status": "Open",
		"is_active": "Yes",
	}
	if str(department or "").strip():
		filters["department"] = department
	return frappe.get_all(
		"Project",
		fields=["name", "project_name", "department", "status", "is_active"],
		filters=filters,
		order_by="modified desc",
		limit_page_length=300,
	)


@frappe.whitelist()
def get_mobile_purchase_items() -> list[dict]:
	return frappe.get_all(
		"Item",
		fields=["name", "item_name", "stock_uom", "purchase_uom"],
		filters={"disabled": 0, "is_purchase_item": 1, "has_variants": 0},
		order_by="modified desc",
		limit_page_length=300,
	)

@frappe.whitelist()
def get_mobile_departments() -> list[dict]:
	return frappe.get_all(
		"Department",
		fields=["name", "department_name"],
		order_by="department_name asc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_mobile_shift_types() -> list[dict]:
	return frappe.get_all(
		"Shift Type",
		fields=["name"],
		order_by="name asc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_mobile_employees(
	employee_ids: str | list[str] | None = None,
	department: str | None = None,
	active_only: int | bool = 1,
) -> list[dict]:
	current_employee = _get_current_employee_row(raise_exception=False)
	allowed_ids = _parse_name_list(employee_ids)
	filters = {}

	if _can_manage_attendance():
		if allowed_ids:
			filters["name"] = ("in", allowed_ids)
		if str(department or "").strip():
			filters["department"] = department
	else:
		if not current_employee:
			return []
		current_employee_id = str(current_employee.get("name") or "").strip()
		if allowed_ids:
			allowed_ids = [employee_id for employee_id in allowed_ids if employee_id == current_employee_id]
			if not allowed_ids:
				return []
			filters["name"] = ("in", allowed_ids)
		else:
			filters["name"] = current_employee_id

	if cint(active_only):
		filters["status"] = "Active"

	return frappe.get_all(
		"Employee",
		fields=["name", "employee_name", "department", "designation", "status"],
		filters=filters,
		order_by="employee_name asc",
		limit_page_length=500,
	)


@frappe.whitelist()
def get_mobile_attendance_records(
	employee_ids: str | list[str] | None = None,
	employee: str | None = None,
	date: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	project: str | None = None,
) -> list[dict]:
	current_employee = _get_current_employee_row(raise_exception=False)
	if employee:
		requested_ids = [str(employee).strip()]
	else:
		requested_ids = _parse_name_list(employee_ids)

	if not requested_ids and current_employee:
		requested_ids = [str(current_employee.get("name") or "").strip()]

	if not requested_ids:
		return []

	if not _can_manage_attendance():
		own_id = str((current_employee or {}).get("name") or "").strip()
		requested_ids = [employee_id for employee_id in requested_ids if employee_id == own_id]
		if not requested_ids:
			return []

	filters = {
		"employee": ("in", requested_ids),
		"docstatus": ("!=", 2),
	}
	if date:
		filters["attendance_date"] = getdate(date)
	elif from_date and to_date:
		filters["attendance_date"] = ["between", [getdate(from_date), getdate(to_date)]]
	elif from_date:
		filters["attendance_date"] = [">=", getdate(from_date)]
	elif to_date:
		filters["attendance_date"] = ["<=", getdate(to_date)]

	if str(project or "").strip() and frappe.db.has_column("Attendance", "project"):
		filters["project"] = project

	fields = [
		"name",
		"employee",
		"employee_name",
		"attendance_date",
		"status",
		"leave_type",
		"in_time",
		"out_time",
		"working_hours",
		"shift",
		"late_entry",
		"early_exit",
		"docstatus",
	]
	if frappe.db.has_column("Attendance", "project"):
		fields.append("project")

	return frappe.get_all(
		"Attendance",
		fields=fields,
		filters=filters,
		order_by="attendance_date asc, creation desc",
		limit_page_length=1000,
	)


@frappe.whitelist()
def mark_mobile_attendance(
	employee_ids: str | list[str],
	status: str,
	date: str,
	shift: str | None = None,
	project: str | None = None,
	leave_type: str | None = None,
) -> dict:
	if not _can_manage_attendance():
		frappe.throw(_("您没有团队考勤权限"), frappe.PermissionError)

	ids = _parse_name_list(employee_ids)
	if not ids:
		frappe.throw(_("请选择员工"))

	status = str(status or "").strip()
	if status not in {"Present", "Absent", "On Leave"}:
		frappe.throw(_("不支持的考勤状态"))

	attendance_date = getdate(date)
	effective_leave_type = str(leave_type or "").strip() if status == "On Leave" else None
	project_value = str(project or "").strip() if frappe.db.has_column("Attendance", "project") else None

	for employee_id in ids:
		_clean_existing_attendance(employee_id, attendance_date)
		doc = frappe.get_doc(
			{
				"doctype": "Attendance",
				"employee": employee_id,
				"attendance_date": attendance_date,
				"status": status,
				"leave_type": effective_leave_type,
				"late_entry": 0,
				"early_exit": 0,
				"shift": str(shift or "").strip() or None,
				"project": project_value,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()

	return {"created": len(ids)}


@frappe.whitelist()
def cancel_mobile_attendance(name: str) -> dict:
	if not _can_manage_attendance():
		frappe.throw(_("您没有团队考勤权限"), frappe.PermissionError)
	doc = frappe.get_doc("Attendance", name)
	doc.flags.ignore_permissions = True
	if cint(doc.docstatus) == 1:
		doc.cancel()
	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def delete_mobile_attendance(name: str) -> dict:
	if not _can_manage_attendance():
		frappe.throw(_("您没有团队考勤权限"), frappe.PermissionError)
	frappe.delete_doc(
		"Attendance",
		name,
		ignore_permissions=True,
		force=True,
		delete_permanently=True,
	)
	return {"name": name}


@frappe.whitelist()
def create_mobile_project(project_name: str, department: str | None = None) -> dict:
	if not _can_manage_projects():
		frappe.throw(_("您没有创建项目权限"), frappe.PermissionError)

	project_name = str(project_name or "").strip()
	if not project_name:
		frappe.throw(_("请输入项目名称"))

	doc = frappe.get_doc(
		{
			"doctype": "Project",
			"project_name": project_name,
			"department": str(department or "").strip() or None,
			"status": "Open",
			"is_active": "Yes",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return {"name": doc.name}


@frappe.whitelist()
def get_mobile_material_request_queue(limit: int | None = 50) -> list[dict]:
	if not _can_manage_procurement():
		return []

	rows = frappe.get_all(
		"Material Request",
		fields=_get_mr_list_fields(),
		filters={
			"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]),
			"docstatus": ("!=", 2),
			"status": ("not in", ["Stopped", "Cancelled"]),
		},
		order_by="modified desc",
		limit_page_length=cint(limit) or 50,
	)
	return _serialize_material_request_rows([frappe._dict(row) for row in rows])


@frappe.whitelist()
def get_mobile_procurement_overview() -> dict:
	queue = get_mobile_material_request_queue(limit=20) if _can_manage_procurement() else []
	my_rows = frappe.get_all(
		"Material Request",
		fields=_get_mr_list_fields(),
		filters={
			"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]),
			"owner": frappe.session.user,
		},
		order_by="modified desc",
		limit_page_length=10,
	)
	my_requests = _serialize_material_request_rows([frappe._dict(row) for row in my_rows])

	if _can_manage_procurement():
		stage_counts = {
			"material_request": frappe.db.count(
				"Material Request", {"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]), "docstatus": ("!=", 2)}
			),
			"request_for_quotation": frappe.db.count("Request for Quotation", {"docstatus": ("!=", 2)}),
			"supplier_quotation": frappe.db.count("Supplier Quotation", {"docstatus": ("!=", 2)}),
			"purchase_order": frappe.db.count("Purchase Order", {"docstatus": ("!=", 2)}),
			"purchase_receipt": frappe.db.count("Purchase Receipt", {"docstatus": ("!=", 2)}),
			"purchase_invoice": frappe.db.count("Purchase Invoice", {"docstatus": ("!=", 2)}),
			"payment_entry": frappe.db.count(
				"Payment Entry",
				{
					"party_type": "Supplier",
					"payment_type": "Pay",
					"docstatus": 1,
				},
			),
		}
		recent_orders = frappe.get_all(
			"Purchase Order",
			fields=[
				"name",
				"supplier",
				"transaction_date",
				"schedule_date",
				"status",
				"grand_total",
				"currency",
				"per_received",
				"per_billed",
			],
			filters={"docstatus": ("!=", 2)},
			order_by="modified desc",
			limit_page_length=6,
		)
		kpis = {
			"pending_approval": len(queue),
			"to_receive": frappe.db.count("Purchase Order", {"docstatus": 1, "per_received": ("<", 99.99)}),
			"to_bill": frappe.db.count("Purchase Order", {"docstatus": 1, "per_billed": ("<", 99.99)}),
			"to_pay": frappe.db.count(
				"Purchase Invoice", {"docstatus": 1, "outstanding_amount": (">", 0)}
			),
		}
	else:
		stage_counts = {
			"material_request": frappe.db.count(
				"Material Request",
				{
					"material_request_type": ("in", ["Purchase", "Material Transfer", "Material Issue"]),
					"docstatus": ("!=", 2),
					"owner": frappe.session.user,
				},
			),
			"request_for_quotation": 0,
			"supplier_quotation": 0,
			"purchase_order": 0,
			"purchase_receipt": 0,
			"purchase_invoice": 0,
			"payment_entry": 0,
		}
		recent_orders = []
		kpis = {
			"pending_approval": 0,
			"to_receive": 0,
			"to_bill": 0,
			"to_pay": 0,
		}

	return {
		"stage_cards": [{**stage, "count": cint(stage_counts.get(stage["key"], 0))} for stage in MATERIAL_REQUEST_STAGE_DEFS],
		"kpis": kpis,
		"recent_orders": recent_orders,
		"approval_items": queue,
		"my_requests": my_requests,
	}


@frappe.whitelist()
def get_mobile_material_request_detail(name: str) -> dict:
	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)

	result = doc.as_dict()
	_, employee_by_user_id = _get_employee_lookup(user_ids=[str(doc.owner or "").strip()])
	employee = employee_by_user_id.get(str(doc.owner or "").strip())
	result["_employee_name"] = employee.get("employee_name") if employee else ""
	result["_department_name"] = employee.get("department") if employee else ""
	result["can_edit"] = bool(doc.owner == frappe.session.user and cint(doc.docstatus) == 0)
	result["workflow_actions"] = _get_material_request_workflow_actions(doc.name)

	rfq_names = _get_child_parent_names("Request for Quotation Item", "material_request", doc.name)
	supplier_quote_names = _get_child_parent_names("Supplier Quotation Item", "material_request", doc.name)
	purchase_order_names = _get_child_parent_names("Purchase Order Item", "material_request", doc.name)
	purchase_receipt_names = _get_child_parent_names("Purchase Receipt Item", "material_request", doc.name)
	purchase_invoice_names = _get_child_parent_names("Purchase Invoice Item", "material_request", doc.name)

	rfqs = _get_docs_by_names(
		"Request for Quotation",
		rfq_names,
		["name", "transaction_date", "status", "docstatus", "modified"],
	)
	supplier_quotes = _get_docs_by_names(
		"Supplier Quotation",
		supplier_quote_names,
		["name", "supplier", "transaction_date", "status", "grand_total", "docstatus", "modified"],
	)
	purchase_orders = _get_docs_by_names(
		"Purchase Order",
		purchase_order_names,
		[
			"name",
			"supplier",
			"transaction_date",
			"status",
			"grand_total",
			"per_received",
			"per_billed",
			"docstatus",
			"modified",
		],
	)
	purchase_receipts = _get_docs_by_names(
		"Purchase Receipt",
		purchase_receipt_names,
		["name", "supplier", "posting_date", "status", "grand_total", "docstatus", "modified"],
	)
	purchase_invoices = _get_docs_by_names(
		"Purchase Invoice",
		purchase_invoice_names,
		[
			"name",
			"supplier",
			"posting_date",
			"status",
			"grand_total",
			"outstanding_amount",
			"docstatus",
			"modified",
		],
	)

	ref_names = list({*purchase_order_names, *purchase_invoice_names})
	payment_entry_refs = []
	if ref_names:
		payment_entry_refs = frappe.get_all(
			"Payment Entry Reference",
			fields=["parent"],
			filters={
				"reference_doctype": ("in", ["Purchase Order", "Purchase Invoice"]),
				"reference_name": ("in", ref_names),
			},
			order_by="modified desc",
			limit_page_length=500,
		)
	payment_entry_names = []
	seen_payment_entries: set[str] = set()
	for row in payment_entry_refs:
		parent = str(row.get("parent") or "").strip()
		if not parent or parent in seen_payment_entries:
			continue
		seen_payment_entries.add(parent)
		payment_entry_names.append(parent)

	payment_entries = _get_docs_by_names(
		"Payment Entry",
		payment_entry_names,
		["name", "posting_date", "status", "paid_amount", "docstatus", "modified"],
	)

	result["related_docs"] = {
		"request_for_quotation": _serialize_related_docs(rfqs, "transaction_date"),
		"supplier_quotation": _serialize_related_docs(supplier_quotes, "transaction_date"),
		"purchase_order": _serialize_related_docs(purchase_orders, "transaction_date"),
		"purchase_receipt": _serialize_related_docs(purchase_receipts, "posting_date"),
		"purchase_invoice": _serialize_related_docs(purchase_invoices, "posting_date"),
		"payment_entry": _serialize_related_docs(payment_entries, "posting_date"),
	}
	result["progress"] = {
		"request_for_quotation": len(rfqs),
		"supplier_quotation": len(supplier_quotes),
		"purchase_order": len(purchase_orders),
		"purchase_receipt": len(purchase_receipts),
		"purchase_invoice": len(purchase_invoices),
		"payment_entry": len(payment_entries),
	}
	return result


def _get_material_request_workflow_actions(name: str) -> list[dict]:
	rows = frappe.get_all(
		"Workflow Action",
		fields=[
			"name",
			"status",
			"workflow_state",
			"completed_by",
			"completed_by_role",
			"creation",
			"modified",
		],
		filters={
			"reference_doctype": "Material Request",
			"reference_name": name,
		},
		order_by="creation asc",
		limit_page_length=100,
	)
	return [dict(row) for row in rows]


@frappe.whitelist()
def create_mobile_material_request(payload: str | dict | None = None, **kwargs) -> dict:
	current_employee = _get_current_employee_row()
	data = _normalize_material_request_payload(payload, kwargs)
	items = _validate_material_request_payload(data)

	doc = frappe.new_doc("Material Request")
	_apply_material_request_fields(doc, data, items)
	doc.flags.ignore_permissions = True
	doc.insert()

	return {
		"name": doc.name,
		"employee": current_employee.get("name"),
		"docstatus": cint(doc.docstatus),
	}


@frappe.whitelist()
def update_mobile_material_request(payload: str | dict | None = None, **kwargs) -> dict:
	data = _normalize_material_request_payload(payload, kwargs)
	name = str(data.get("name") or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)
	if doc.owner != frappe.session.user:
		frappe.throw(_("只有申请人本人可以修改草稿"), frappe.PermissionError)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的物料申请可以修改"))

	client_modified = str(data.get("modified") or "").strip()
	server_modified = str(doc.modified or "").strip()
	if client_modified and server_modified and client_modified != server_modified:
		frappe.throw(_("单据已被其他人更新，请返回列表刷新后重试"))

	items = _validate_material_request_payload(data)
	_apply_material_request_fields(doc, data, items)
	doc.flags.ignore_permissions = True
	doc.save()

	return {
		"name": doc.name,
		"docstatus": cint(doc.docstatus),
	}


@frappe.whitelist()
def submit_mobile_material_request(name: str) -> dict:
	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)
	if doc.owner != frappe.session.user:
		frappe.throw(_("只有申请人本人可以提交物料申请"), frappe.PermissionError)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的物料申请可以提交"))

	doc.flags.ignore_permissions = True

	# Try to apply workflow action if available
	workflow_name = None
	try:
		from frappe.model.workflow import get_workflow_name
		workflow_name = get_workflow_name("Material Request")
	except Exception:
		pass

	if workflow_name:
		from frappe.model.workflow import apply_workflow, get_transitions_for_user

		transitions = get_transitions_for_user("Material Request", doc.name)
		submit_action = next(
			(t.action for t in transitions if t.action.lower() in ["submit", "submit for approval", "提交", "确认"]),
			None,
		)
		if submit_action:
			try:
				apply_workflow(doc, submit_action)
				return {
					"name": doc.name,
					"docstatus": cint(doc.docstatus),
					"workflow_applied": True,
					"action": submit_action,
				}
			except Exception as e:
				# If workflow application fails, log it and fall back to normal submit if docstatus is still 0
				frappe.log_error(f"Workflow failed for {doc.name}: {str(e)}", "Mobile API Error")

	# Fallback to normal submit
	if cint(doc.docstatus) == 0:
		doc.submit()

	return {
		"name": doc.name,
		"docstatus": cint(doc.docstatus),
	}


@frappe.whitelist()
def delete_mobile_material_request(name: str) -> dict:
	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)
	if doc.owner != frappe.session.user:
		frappe.throw(_("只有申请人本人可以删除物料申请"), frappe.PermissionError)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的物料申请可以删除"))

	frappe.delete_doc("Material Request", name, ignore_permissions=True)

	return {"name": name, "deleted": True}


@frappe.whitelist()
def cancel_mobile_material_request(name: str) -> dict:
	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)
	if doc.owner != frappe.session.user:
		frappe.throw(_("只有申请人本人可以取消物料申请"), frappe.PermissionError)
	if cint(doc.docstatus) != 1:
		frappe.throw(_("只有已提交的物料申请可以取消"))

	doc.flags.ignore_permissions = True
	doc.cancel()

	return {
		"name": doc.name,
		"docstatus": cint(doc.docstatus),
	}


@frappe.whitelist()
def reset_mobile_material_request(name: str) -> dict:
	"""将已审批的物料申请回退到草稿状态（取消、删除并重建）"""
	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Material Request", name)
	_ensure_material_request_access(doc)
	if doc.owner != frappe.session.user:
		frappe.throw(_("只有申请人本人可以回退物料申请"), frappe.PermissionError)
	if cint(doc.docstatus) != 1:
		frappe.throw(_("只有已提交的物料申请可以回退"))

	# 保存单据数据用于重建
	snapshot = {
		"company": doc.company,
		"transaction_date": doc.transaction_date,
		"schedule_date": doc.schedule_date,
		"material_request_type": doc.material_request_type,
		"buying_price_list": doc.get("buying_price_list"),
		"from_warehouse": doc.get("from_warehouse"),
		"set_warehouse": doc.get("set_warehouse"),
		"items": [
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": item.rate,
				"uom": item.uom,
				"schedule_date": item.schedule_date,
				"from_warehouse": item.from_warehouse,
				"warehouse": item.warehouse,
				"project": item.project,
				"description": item.description,
			}
			for item in doc.items
		],
	}

	# 取消并删除原单据
	doc.flags.ignore_permissions = True
	doc.cancel()
	frappe.delete_doc("Material Request", name, ignore_permissions=True)

	# 重建草稿状态单据
	new_doc = frappe.new_doc("Material Request")
	_apply_material_request_fields(new_doc, snapshot, snapshot["items"])
	new_doc.flags.ignore_permissions = True
	new_doc.insert()

	return {
		"name": new_doc.name,
		"docstatus": cint(new_doc.docstatus),
		"original_name": name,
	}

@frappe.whitelist()
def get_mobile_price_lists() -> list[dict]:
        return frappe.get_all(
                "Price List",
                fields=["name", "enabled", "buying", "selling", "currency"],
                filters={"enabled": 1, "buying": 1},
                order_by="name asc"
        )

@frappe.whitelist()
def get_mobile_item_warehouses(item_code: str) -> dict:
        # 获取各仓库库存余额
        balances = frappe.db.get_all(
                "Bin",
                filters={"item_code": item_code, "actual_qty": [">", 0]},
                fields=["warehouse", "actual_qty"]
        )
        
        # 获取默认仓库
        default_warehouse = frappe.db.get_value(
                "Item Default", 
                {"parent": item_code}, 
                "default_warehouse"
        )
        
        return {
                "balances": balances,
                "default_warehouse": default_warehouse
        }


@frappe.whitelist()
def get_mobile_item_price(item_code: str, price_list: str) -> dict:
        price = frappe.db.get_value(
                "Item Price",
                {"item_code": item_code, "price_list": price_list},
                ["price_list_rate", "currency"],
                as_dict=True
        )
        return price or {"price_list_rate": 0, "currency": ""}


@frappe.whitelist()
def get_mobile_suppliers() -> list[dict]:
	if not _can_manage_procurement():
		return []
	return frappe.get_all(
		"Supplier",
		fields=["name", "supplier_name", "supplier_group"],
		filters={"disabled": 0},
		order_by="supplier_name asc",
		limit_page_length=500,
	)


@frappe.whitelist()
def create_mobile_rfq(material_request: str, suppliers: str | list[str] | None = None, **kwargs) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	mr_name = str(material_request or "").strip()
	if not mr_name:
		frappe.throw(_("请指定物料申请"))

	mr = frappe.get_doc("Material Request", mr_name)
	if cint(mr.docstatus) != 1:
		frappe.throw(_("物料申请必须已提交才能创建询价单"))
	if mr.material_request_type != "Purchase":
		frappe.throw(_("只有采购类型的物料申请才能创建询价单"))

	supplier_list = _parse_name_list(suppliers)
	if not supplier_list:
		frappe.throw(_("请至少选择一个供应商"))

	from erpnext.stock.doctype.material_request.material_request import make_request_for_quotation

	rfq = make_request_for_quotation(mr_name)
	rfq.company = mr.company
	rfq.suppliers = []
	for supplier_name in supplier_list:
		rfq.append("suppliers", {"supplier": supplier_name})

	rfq.flags.ignore_permissions = True
	rfq.insert()

	return {"name": rfq.name, "docstatus": cint(rfq.docstatus)}


@frappe.whitelist()
def submit_mobile_rfq(name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Request for Quotation", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的询价单可以提交"))

	doc.flags.ignore_permissions = True
	doc.submit()

	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def get_mobile_rfq_share_links(name: str) -> list[dict]:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Request for Quotation", name)
	if cint(doc.docstatus) != 1:
		frappe.throw(_("只有已提交的询价单可以分享"))

	portal_route = frappe.db.get_value(
		"Portal Menu Item", {"reference_doctype": "Request for Quotation"}, ["route"]
	)
	if not portal_route:
		portal_route = "rfq"

	result = []
	for supplier_row in doc.suppliers:
		supplier_name = supplier_row.supplier
		supplier_detail = frappe.db.get_value(
			"Supplier", supplier_name, ["supplier_name", "email_id", "mobile_no"], as_dict=True
		) or {}

		mobile_no = ""
		email_id = supplier_row.email_id or supplier_detail.get("email_id") or ""

		if supplier_row.contact:
			contact_info = frappe.db.get_value(
				"Contact", supplier_row.contact, ["mobile_no", "phone", "email_id"], as_dict=True
			) or {}
			mobile_no = contact_info.get("mobile_no") or contact_info.get("phone") or ""
			email_id = email_id or contact_info.get("email_id") or ""

		if not mobile_no:
			mobile_no = supplier_detail.get("mobile_no") or ""

		update_password_link = ""
		portal_user_ready = False
		login_identifier = ""
		default_password = ""

		if mobile_no:
			login_identifier = mobile_no
			user_email = mobile_no + "@supplier.local"
			if len(mobile_no) >= 6:
				default_password = mobile_no[-6:]

			user_exists = bool(frappe.db.exists("User", {"email": user_email}))
			if not user_exists:
				user_exists = bool(frappe.db.exists("User", {"mobile_no": mobile_no}))

			if user_exists:
				existing_user = frappe.get_doc("User", {"mobile_no": mobile_no}) or frappe.get_doc("User", user_email)
				if existing_user and existing_user.user_type == "Website User":
					try:
						update_password_link = existing_user._reset_password()
					except Exception:
						update_password_link = ""
					portal_user_ready = _ensure_supplier_portal_user(supplier_name, existing_user.name)
			else:
				try:
					user = frappe.get_doc({
						"doctype": "User",
						"email": user_email,
						"mobile_no": mobile_no,
						"first_name": supplier_detail.get("supplier_name") or supplier_name,
						"user_type": "Website User",
						"send_welcome_email": 0,
						"roles": [{"role": "Supplier"}],
					})
					user.flags.ignore_permissions = True
					user.flags.ignore_password_policy = True
					user.insert(ignore_permissions=True)

					if default_password:
						from frappe.utils.password import update_password
						update_password(user.name, default_password)

					portal_user_ready = _ensure_supplier_portal_user(supplier_name, user.name)
				except Exception:
					frappe.db.rollback()
		elif email_id:
			login_identifier = email_id
			user_exists = bool(frappe.db.exists("User", email_id))

			if user_exists:
				user = frappe.get_doc("User", email_id)
				if user.user_type == "Website User":
					try:
						update_password_link = user._reset_password()
					except Exception:
						update_password_link = ""
					portal_user_ready = _ensure_supplier_portal_user(supplier_name, email_id)
			else:
				try:
					user = frappe.get_doc({
						"doctype": "User",
						"email": email_id,
						"first_name": supplier_detail.get("supplier_name") or supplier_name,
						"user_type": "Website User",
						"send_welcome_email": 0,
						"roles": [{"role": "Supplier"}],
					})
					user.flags.ignore_permissions = True
					user.flags.ignore_password_policy = True
					user.insert(ignore_permissions=True)

					from frappe.utils.password import update_password
					update_password(user.name, frappe.generate_hash(length=12))

					try:
						update_password_link = user._reset_password()
					except Exception:
						update_password_link = ""

					portal_user_ready = _ensure_supplier_portal_user(supplier_name, email_id)
				except Exception:
					frappe.db.rollback()

		result.append({
			"supplier": supplier_name,
			"supplier_name": supplier_detail.get("supplier_name") or supplier_name,
			"email_id": email_id,
			"mobile_no": mobile_no,
			"login_identifier": login_identifier,
			"rfq_route": f"/{portal_route}/{name}",
			"update_password_link": update_password_link,
			"default_password": default_password if default_password else "",
			"user_exists": user_exists if mobile_no else (bool(email_id and frappe.db.exists("User", email_id))),
			"portal_user_ready": portal_user_ready,
		})

	frappe.db.commit()
	return result


def _ensure_supplier_portal_user(supplier_name: str, user_email: str) -> bool:
	try:
		supplier = frappe.get_doc("Supplier", supplier_name)
		existing = any(
			pu.user == user_email for pu in (supplier.get("portal_users") or [])
		)
		if not existing:
			supplier.append("portal_users", {"user": user_email})
			supplier.flags.ignore_permissions = True
			supplier.save(ignore_permissions=True)
		_ensure_supplier_rfq_read_permission()
		return True
	except Exception:
		return False


def _ensure_supplier_rfq_read_permission():
	supplier_perm = frappe.db.get_value(
		"DocPerm", {"parent": "Request for Quotation", "role": "Supplier"}, "read"
	)
	if supplier_perm != 1:
		if supplier_perm is not None:
			frappe.db.set_value(
				"DocPerm", {"parent": "Request for Quotation", "role": "Supplier"}, "read", 1
			)
		else:
			doc = frappe.new_doc("DocPerm")
			doc.parent = "Request for Quotation"
			doc.parenttype = "DocType"
			doc.parentfield = "permissions"
			doc.role = "Supplier"
			doc.read = 1
			doc.write = 0
			doc.create = 0
			doc.delete = 0
			doc.insert(ignore_permissions=True)


@frappe.whitelist()
def get_mobile_rfq_detail(name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	doc = frappe.get_doc("Request for Quotation", name)
	result = doc.as_dict()

	result["suppliers_info"] = []
	for s in doc.suppliers:
		info = frappe.db.get_value(
			"Supplier", s.supplier, ["supplier_name", "email_id"], as_dict=True
		) or {}
		result["suppliers_info"].append({
			"supplier": s.supplier,
			"supplier_name": info.get("supplier_name") or s.supplier,
			"email_id": s.email_id or info.get("email_id") or "",
			"contact": s.contact or "",
		})

	return result


@frappe.whitelist()
def create_mobile_supplier_quotation(rfq_name: str, supplier: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	rfq_name = str(rfq_name or "").strip()
	supplier = str(supplier or "").strip()
	if not rfq_name:
		frappe.throw(_("请指定询价单"))
	if not supplier:
		frappe.throw(_("请指定供应商"))

	rfq = frappe.get_doc("Request for Quotation", rfq_name)
	if cint(rfq.docstatus) != 1:
		frappe.throw(_("询价单必须已提交才能创建供应商报价"))

	supplier_in_rfq = any(s.supplier == supplier for s in rfq.suppliers)
	if not supplier_in_rfq:
		frappe.throw(_("该供应商不在此询价单中"))

	from erpnext.buying.doctype.request_for_quotation.request_for_quotation import (
		make_supplier_quotation_from_rfq,
	)

	sq = make_supplier_quotation_from_rfq(rfq_name, for_supplier=supplier)
	sq.flags.ignore_permissions = True
	sq.insert()

	return {"name": sq.name, "docstatus": cint(sq.docstatus), "supplier": supplier}


@frappe.whitelist()
def submit_mobile_supplier_quotation(name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Supplier Quotation", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的供应商报价可以提交"))

	doc.flags.ignore_permissions = True
	doc.submit()

	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def update_mobile_supplier_quotation_prices(payload: str | dict | None = None, **kwargs) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	data = _normalize_material_request_payload(payload, kwargs)
	name = str(data.get("name") or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Supplier Quotation", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的供应商报价可以修改"))

	items_data = data.get("items") or []
	if isinstance(items_data, str):
		items_data = json.loads(items_data)

	for item_update in items_data:
		item_name = str(item_update.get("name") or "").strip()
		if not item_name:
			continue
		for row in doc.items:
			if row.name == item_name:
				if "rate" in item_update:
					row.rate = flt(item_update["rate"])
				if "qty" in item_update:
					row.qty = flt(item_update["qty"])
				break

	doc.run_method("calculate_taxes_and_totals")
	doc.flags.ignore_permissions = True
	doc.save()

	return {"name": doc.name, "docstatus": cint(doc.docstatus), "grand_total": flt(doc.grand_total)}


@frappe.whitelist()
def create_mobile_purchase_order(sq_name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	sq_name = str(sq_name or "").strip()
	if not sq_name:
		frappe.throw(_("请指定供应商报价"))

	sq = frappe.get_doc("Supplier Quotation", sq_name)
	if cint(sq.docstatus) != 1:
		frappe.throw(_("供应商报价必须已提交才能创建采购订单"))

	from erpnext.buying.doctype.supplier_quotation.supplier_quotation import make_purchase_order

	po = make_purchase_order(sq_name)
	po.flags.ignore_permissions = True
	po.insert()

	return {"name": po.name, "docstatus": cint(po.docstatus), "supplier": po.supplier, "grand_total": flt(po.grand_total)}


@frappe.whitelist()
def submit_mobile_purchase_order(name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Purchase Order", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的采购订单可以提交"))

	doc.flags.ignore_permissions = True
	doc.submit()

	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def create_mobile_purchase_receipt(po_name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	po_name = str(po_name or "").strip()
	if not po_name:
		frappe.throw(_("请指定采购订单"))

	po = frappe.get_doc("Purchase Order", po_name)
	if cint(po.docstatus) != 1:
		frappe.throw(_("采购订单必须已提交才能创建采购收货"))

	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pr = make_purchase_receipt(po_name)
	pr.flags.ignore_permissions = True
	pr.insert()

	return {"name": pr.name, "docstatus": cint(pr.docstatus), "supplier": pr.supplier, "grand_total": flt(pr.grand_total)}


@frappe.whitelist()
def submit_mobile_purchase_receipt(name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Purchase Receipt", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的采购收货可以提交"))

	doc.flags.ignore_permissions = True
	doc.submit()

	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def create_mobile_purchase_invoice(pr_name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	pr_name = str(pr_name or "").strip()
	if not pr_name:
		frappe.throw(_("请指定采购收货"))

	pr = frappe.get_doc("Purchase Receipt", pr_name)
	if cint(pr.docstatus) != 1:
		frappe.throw(_("采购收货必须已提交才能创建采购发票"))

	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

	pi = make_purchase_invoice(pr_name)
	pi.flags.ignore_permissions = True
	pi.insert()

	return {"name": pi.name, "docstatus": cint(pi.docstatus), "supplier": pi.supplier, "grand_total": flt(pi.grand_total)}


@frappe.whitelist()
def submit_mobile_purchase_invoice(name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	name = str(name or "").strip()
	if not name:
		frappe.throw(_("缺少单据名称"))

	doc = frappe.get_doc("Purchase Invoice", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("只有草稿状态的采购发票可以提交"))

	doc.flags.ignore_permissions = True
	doc.submit()

	return {"name": doc.name, "docstatus": cint(doc.docstatus)}


@frappe.whitelist()
def get_mobile_procurement_doc_detail(doctype: str, name: str) -> dict:
	if not _can_manage_procurement():
		frappe.throw(_("您没有采购管理权限"), frappe.PermissionError)

	doctype = str(doctype or "").strip()
	name = str(name or "").strip()
	if not doctype or not name:
		frappe.throw(_("参数不完整"))

	allowed = {
		"Request for Quotation",
		"Supplier Quotation",
		"Purchase Order",
		"Purchase Receipt",
		"Purchase Invoice",
	}
	if doctype not in allowed:
		frappe.throw(_("不支持的单据类型"))

	doc = frappe.get_doc(doctype, name)
	result = doc.as_dict()

	if doctype == "Request for Quotation":
		result["suppliers_info"] = []
		for s in doc.suppliers:
			info = frappe.db.get_value(
				"Supplier", s.supplier, ["supplier_name", "email_id"], as_dict=True
			) or {}
			result["suppliers_info"].append({
				"supplier": s.supplier,
				"supplier_name": info.get("supplier_name") or s.supplier,
				"email_id": s.email_id or info.get("email_id") or "",
			})

	return result
