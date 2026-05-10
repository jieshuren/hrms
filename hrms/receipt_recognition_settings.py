"""集中加载「报销票据 AI」相关配置：bench 项目根目录 receipt_recognition.json + 站点 receipt_recognition 覆盖 + 旧版 hunyuan_* 回退。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any

import frappe
from frappe.utils import get_bench_path


RECEIPT_RECOGNITION_CONFIG_FILENAME = "receipt_recognition.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
	if not override:
		return base
	out = copy.deepcopy(base)
	for key, val in override.items():
		if key in out and isinstance(out[key], dict) and isinstance(val, dict):
			out[key] = _deep_merge(out[key], val)
		else:
			out[key] = val
	return out


def get_receipt_recognition_config_path() -> str:
	"""与 sites、apps 同级的 bench 根目录下的配置文件路径。"""
	return os.path.join(get_bench_path(), RECEIPT_RECOGNITION_CONFIG_FILENAME)


def _load_file_defaults() -> dict[str, Any]:
	path = get_receipt_recognition_config_path()
	if not os.path.isfile(path):
		frappe.throw(
			f"未找到项目根目录下的 {RECEIPT_RECOGNITION_CONFIG_FILENAME}：{path}（应与 sites、apps 目录同级）"
		)
	with open(path, encoding="utf-8") as f:
		return json.load(f)


def get_receipt_ai_config() -> dict[str, Any]:
	"""合并顺序：配置文件默认值 < frappe.conf.receipt_recognition < 旧版 hunyuan_*（仅补 vision 空缺）。"""
	cfg = _load_file_defaults()
	site_block = frappe.conf.get("receipt_recognition")
	if isinstance(site_block, dict):
		cfg = _deep_merge(cfg, site_block)

	va = cfg.setdefault("vision_api", {})
	if not str(va.get("api_key") or "").strip():
		legacy = str(frappe.conf.get("hunyuan_api_key") or "").strip()
		if legacy:
			va["api_key"] = legacy
	if not str(va.get("endpoint") or "").strip():
		legacy_ep = str(frappe.conf.get("hunyuan_endpoint") or "").strip()
		if legacy_ep:
			va["endpoint"] = legacy_ep
	if not str(va.get("model_default") or "").strip():
		legacy_m = str(frappe.conf.get("hunyuan_model") or "").strip()
		if legacy_m:
			va["model_default"] = legacy_m

	tc = cfg.setdefault("text_classification", {})
	if not str(tc.get("endpoint") or "").strip():
		tc["endpoint"] = str(va.get("endpoint") or "").strip()
	if not str(tc.get("api_key") or "").strip():
		tc["api_key"] = str(va.get("api_key") or "").strip()
	# 站点仅配置了 text_classification.api_key 时，回填到 vision
	if not str(va.get("api_key") or "").strip() and str(tc.get("api_key") or "").strip():
		va["api_key"] = str(tc["api_key"]).strip()
	if not str(tc.get("api_key") or "").strip() and str(va.get("api_key") or "").strip():
		tc["api_key"] = str(va["api_key"]).strip()

	return cfg


def build_receipt_vision_prompt(cfg: dict[str, Any], doc_kind: str, category_hierarchy: str | None) -> str:
	p = cfg.get("receipt_prompts") or {}
	intro = str(p.get("intro_template") or "").replace("{doc_kind}", doc_kind)
	schema = str(p.get("schema_block") or "")
	rules = str(p.get("rules_block") or "")
	parts = [intro, schema, rules]
	if category_hierarchy:
		tmpl = str(p.get("category_instruction_template") or "")
		parts.append(tmpl.replace("{category_hierarchy}", category_hierarchy))
	return "\n\n".join(s for s in parts if s)


def build_classification_prompt(cfg: dict[str, Any], text_content: str, category_hierarchy: str) -> str:
	tc = cfg.get("text_classification") or {}
	tmpl = str(tc.get("prompt_template") or "")
	return tmpl.replace("{category_hierarchy}", category_hierarchy).replace("{text_content}", text_content)


def _merge_extra_headers(*blocks: dict[str, Any] | None) -> dict[str, str]:
	out: dict[str, str] = {}
	for block in blocks:
		if not isinstance(block, dict):
			continue
		for hk, hv in block.items():
			if hk and hv is not None:
				out[str(hk)] = str(hv)
	return out


def vision_http_headers(cfg: dict[str, Any]) -> dict[str, str]:
	va = cfg.get("vision_api") or {}
	token = str(va.get("api_key") or "").strip()
	scheme = str(va.get("authorization_scheme") or "Bearer").strip() or "Bearer"
	headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
	if token:
		headers["Authorization"] = f"{scheme} {token}".strip()
	headers.update(_merge_extra_headers(va.get("extra_headers")))
	return headers


def classification_http_headers(cfg: dict[str, Any]) -> dict[str, str]:
	va = cfg.get("vision_api") or {}
	tc = cfg.get("text_classification") or {}
	token = str(tc.get("api_key") or va.get("api_key") or "").strip()
	scheme = str(tc.get("authorization_scheme") or va.get("authorization_scheme") or "Bearer").strip() or "Bearer"
	headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
	if token:
		headers["Authorization"] = f"{scheme} {token}".strip()
	headers.update(_merge_extra_headers(va.get("extra_headers"), tc.get("extra_headers")))
	return headers
