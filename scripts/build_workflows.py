#!/usr/bin/env python3
"""Генератор n8n-воркфлоу BRD-агента.

Собирает JSON-файлы в workflows/ из промптов (prompts/), шаблона BRD (templates/)
и общего JS-кода (scripts/md2html.js). В JSON остаются плейсхолдеры:
  __CFG_<NAME>__   — значения конфигурации (подставляет deploy.py из .env)
  __WF_<NAME>__    — ID связанных воркфлоу (подставляет deploy.py после создания)
  __CRED_<NAME>__  — ID credentials (подставляет deploy.py из .env)

Запуск:  python3 scripts/build_workflows.py
"""
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "workflows"

INTERVIEW_PROMPT = (ROOT / "prompts/interview_system.md").read_text(encoding="utf-8").strip()
BRD_WRITER_PROMPT = (ROOT / "prompts/brd_writer_system.md").read_text(encoding="utf-8").strip()
BRD_FIXER_PROMPT = (ROOT / "prompts/brd_fixer_system.md").read_text(encoding="utf-8").strip()
BRD_TEMPLATE = (ROOT / "templates/brd_template.md").read_text(encoding="utf-8").strip()
_md = (ROOT / "scripts/md2html.js").read_text(encoding="utf-8")
MD2HTML_JS = _md.split("// BEGIN md2html")[1].split("// END md2html")[0].strip()

# ---------------------------------------------------------------------------
# credentials: key -> (credential type, placeholder id, display name)
CREDS = {
    "telegram": ("telegramApi", "__CRED_TELEGRAM__", "Telegram BRD bot"),
    "gdrive": ("googleDriveOAuth2Api", "__CRED_GDRIVE__", "Google Drive OAuth2"),
    "gsheets": ("googleSheetsOAuth2Api", "__CRED_GSHEETS__", "Google Sheets OAuth2"),
    "openrouter": ("openRouterApi", "__CRED_OPENROUTER__", "OpenRouter (key with limit)"),
    "http_openrouter": ("httpHeaderAuth", "__CRED_HTTP_OPENROUTER__", "OpenRouter API key (header)"),
    "openai_cloudru": ("openAiApi", "__CRED_OPENAI_CLOUDRU__", "cloud.ru (OpenAI-compatible)"),
    "pinecone": ("pineconeApi", "__CRED_PINECONE__", "Pinecone"),
    "http_pinecone": ("httpHeaderAuth", "__CRED_HTTP_PINECONE__", "Pinecone API key (header)"),
    "http_cloudru": ("httpHeaderAuth", "__CRED_HTTP_CLOUDRU__", "cloud.ru API key (header)"),
}

WF_ERRORS = "__WF_ERRORS__"
WF_NOTIFY = "__WF_NOTIFY__"
WF_BRD_GENERATOR = "__WF_BRD_GENERATOR__"
WF_QUEUE = "__WF_QUEUE__"

SESSIONS_TABLE = "brd_sessions"
KB_TABLE = "kb_files"

# ---------------------------------------------------------------------------
# Control layer: потолки агента. Обоснование чисел — docs/control-layer.md.
# Паспорт инструмента: шаг у человека 240 мин, 5 раз в неделю, 6 прогонов до рабочего результата.
LIMITS = {
    "INTERVIEW_MAX_ITERATIONS": 4,   # шагов агента за реплику: 0–2 запроса к KB + ответ = 3, +1 запас
    "BRD_MAX_ITERATIONS": 6,         # шагов агента за часть BRD: 2–4 запроса к KB + ответ = 5, +1 запас
    "MAX_TURNS": 40,                 # ответов в интервью: ~6 мин на ответ × 40 ≈ 240 мин работы человека
    "SESSION_BUDGET_USD": 1.0,       # оценка расхода на сессию: ~6 прогонов × ~$0.17
    "INTERVIEW_TIMEOUT_SEC": 180,    # таймаут LLM 120 с + Telegram/KB; это и максимум тарифа n8n Cloud (3 мин)
    # BRD-04: таймаут воркфлоу на тарифе не больше 3 мин, а норма генерации 3–5 мин — поэтому сторож в BRD-05.
    "GENERATION_STUCK_MIN": 20,      # норма 3–5 мин × 4; дольше в generating — прогон потерян
    "BRD_ATTEMPTS_PER_SESSION": 2,   # первая попытка + один повтор после сбоя; дальше — человек
    "BRD_PER_CHAT_PER_DAY": 2,       # BRD от одного заказчика за 24 ч
    "BRD_PER_DAY": 5,                # боевых BRD за 24 ч всего = недельная норма (5 в неделю)
    "BRD_EVAL_PER_DAY": 5,           # тестовых BRD (сессии eval-*) за 24 ч = одна серия из пяти прогонов
    # Раундов исправления после валидатора: 2 (перегенерация пропавшей части писателем + точечное исправление).
    # Повтор генерации — класс 1 (наружу ничего не ушло), каждый раунд $0.02–0.1 и под бюджетом сессии; дальше — человек.
    "BRD_REPAIR_ATTEMPTS": 2,
}
# Цена модели (USD за 1M токенов) — для оценки расхода; задаётся в .env вместе с моделью.
CHARS_PER_TOKEN = 2  # русский текст; калибровка cost-attack 2026-10-11: на Claude факт ≈ 2.2 симв./токен, 3 занижало оценку на ~30%

# Общий JS: распознавание пределов и оценка расхода. Вставляется в Code-узлы.
LIMIT_JS = r"""
// Агентная нода v2.x при maxIterations завершается УСПЕШНО с этим текстом; v3.x — падает с ошибкой «Max iterations (N) reached».
const STOP_RE = /Agent stopped due to (max iterations|iteration limit)|Max iterations \(\d+\) reached/i;
// Лимит ключа/баланса провайдера: OpenRouter 402/403 (limit_source openrouter_key_limit), DeepSeek 402 Insufficient Balance.
const MONEY_RE = /\b402\b|insufficient (credits|balance)|payment required|openrouter_key_limit|key limit|budget limit/i;
const errStr = (e) => (!e ? '' : typeof e === 'string' ? e : [e.message, e.description, e.httpCode].filter(Boolean).join(' ') || JSON.stringify(e)).slice(0, 800);
// Оценка стоимости одного прогона агента сверху: каждый вызов LLM получает system + prompt + результаты инструментов.
const estimateRun = (sysChars, promptChars, outText, steps, pIn, pOut, cpt) => {
  steps = Array.isArray(steps) ? steps : [];
  const calls = steps.length + 1;
  const obs = steps.reduce((a, s) => a + String(s.observation ?? '').length, 0);
  const acts = steps.reduce((a, s) => a + JSON.stringify(s.action ?? {}).length, 0);
  const inChars = calls * (sysChars + promptChars) + obs * calls;
  const outChars = String(outText || '').length + acts;
  return { calls, steps: steps.length, usd: (inChars * pIn + outChars * pOut) / cpt / 1e6 };
};
""".strip()


# Валидатор BRD (паттерн sequential с валидатором): правила стандарта, проверяемые кодом.
# Каждое нарушение привязано к разделу — шаг исправления переписывает только эти разделы.
VALIDATOR_JS = r"""
const splitBrd = (md) => {
  const re = /^##\s+(\d{1,2})\.[^\n]*$/gm;
  const hits = [...md.matchAll(re)];
  const secs = {};
  hits.forEach((m, k) => { secs[Number(m[1])] = md.slice(m.index, k + 1 < hits.length ? hits[k + 1].index : md.length).trim(); });
  return { preamble: (hits.length ? md.slice(0, hits[0].index) : md).trim(), secs };
};
const joinBrd = (preamble, secs) => [preamble, ...Object.keys(secs).map(Number).sort((a, b) => a - b).map((n) => secs[n])]
  .filter(Boolean).join('\n\n');
// В JS \w не совпадает с кириллицей — окончания русских слов ловим через [а-яё]*.
const EXTERNAL_RE = /ChatGPT|OpenAI|GPT-?4|YandexGPT|GigaChat|внешн[а-яё]*\s+(LLM|API|модел)/i;
const validateBrd = (md, transcript) => {
  const { preamble, secs } = splitBrd(md);
  const v = [];
  const add = (section, rule, msg) => v.push({ section, rule, msg });
  for (let n = 0; n <= 12; n++) if (!secs[n]) add(n, 'section_missing', `Нет раздела ${n} (заголовок «## ${n}. …» как в шаблоне)`);
  if (!/^#\s+BRD:/m.test(preamble)) add(0, 'title', 'Нет заголовка «# BRD: <название>» в начале документа');
  if (secs[0] && !(/Draft/i.test(secs[0]) && /0\.1/.test(secs[0]))) add(0, 'meta', 'В разделе 0 должны быть «Версия документа: 0.1» и «Статус: Draft»');
  if (secs[4] && !(/DevOps/i.test(secs[4]) && /ИБ/.test(secs[4]))) add(4, 'stakeholders', 'В разделе 4 нет фиксированных ролей: Инфраструктура — DevOps, Безопасность — ИБ');
  if (secs[6] && !/FR-?\d/.test(secs[6])) add(6, 'fr_ids', 'В разделе 6 нет таблицы требований с ID FR-01…');
  if (secs[6] && !/Given|Дано/i.test(secs[6])) add(6, 'fr_acceptance', 'В разделе 6 у требований нет критериев приёмки Given/When/Then');
  if (secs[7]) {
    const s75 = (secs[7].split(/^###\s+7\.5[^\n]*$/m)[1] || '').split(/^###\s/m)[0];
    if (!s75) add(7, 'sec75_missing', 'В разделе 7 нет подраздела «### 7.5 Безопасность и приватность»');
    else if (!/согласовыва[а-яё]*\s+отдельно\s+с\s+ИБ/i.test(s75)) add(7, 'sec75_ib', 'В 7.5 нет строки «Конкретные меры безопасности согласовываются отдельно с ИБ»');
  }
  if (secs[8]) {
    if (!/-\s*\[[ xXх]\][^\n]*MLflow/i.test(secs[8])) add(8, 'dod_mlflow', 'В разделе 8 нет отдельного чек-бокса про модель в MLflow');
    if (!/-\s*\[[ xXх]\][^\n]*GitLab/i.test(secs[8])) add(8, 'dod_gitlab', 'В разделе 8 нет отдельного чек-бокса про код в GitLab');
    if (!/-\s*\[[ xXх]\][^\n]*(ИБ[^\n]*архитект|архитект[^\n]*ИБ)/i.test(secs[8])) add(8, 'dod_ib', 'В разделе 8 нет пункта «Согласование архитектуры с ИБ и архитекторами»');
  }
  const tbd = (md.replace(secs[11] || '', '').match(/\bTBD\b/g) || []).length;
  if (secs[11] && tbd > 0 && !/^\s*(-|\d+\.|\|)/m.test(secs[11].split('\n').slice(1).join('\n')))
    add(11, 'open_questions', `В документе ${tbd} TBD, а раздел 11 «Открытые вопросы» пуст`);
  if (EXTERNAL_RE.test(transcript || '') && !(EXTERNAL_RE.test(secs[9] || '') || EXTERNAL_RE.test(secs[11] || '')))
    add(9, 'external_risk', 'В интервью звучало предложение внешней модели/API, но в разделах 9 и 11 нет риска несоответствия закрытому контуру');
  return v;
};
""".strip()


def llm_config():
    """Поля Config для оценки расхода (значения из .env, приводятся Number() в коде)."""
    return {
        "LLM_MODEL": "__CFG_LLM_MODEL__",
        "LLM_PRICE_IN": "__CFG_LLM_PRICE_IN__",
        "LLM_PRICE_OUT": "__CFG_LLM_PRICE_OUT__",
        "CHARS_PER_TOKEN": CHARS_PER_TOKEN,
        "SESSION_BUDGET_USD": LIMITS["SESSION_BUDGET_USD"],
    }


def openrouter_model(w, name, pos, temperature, max_tokens, timeout, model="__CFG_LLM_MODEL__"):
    return w.add(name, "@n8n/n8n-nodes-langchain.lmChatOpenRouter", 1, {
        "model": model,
        "options": {"temperature": temperature, "maxTokens": max_tokens, "timeout": timeout, "maxRetries": 2},
    }, pos, creds=["openrouter"])


def mark_outcome(w, name, expr, pos):
    """Execution Data: «сделано» и «остановлено по пределу» различимы в списке executions (фильтр outcome)."""
    return w.add(name, "n8n-nodes-base.executionData", 1.1, {
        "dataToSave": {"values": [{"key": "outcome", "value": expr}]},
    }, pos)


def uid():
    return str(uuid.uuid4())


class WF:
    def __init__(self, name, error_workflow=True, timeout=None):
        self.name = name
        self.dx = 0  # сдвиг по X для добавляемых узлов (раскладка на холсте)
        self.nodes = []
        self.connections = {}
        self.settings = {
            "executionOrder": "v1",
            "timezone": "Europe/Moscow",
            "saveManualExecutions": True,
            "saveDataErrorExecution": "all",
            "saveDataSuccessExecution": "all",
        }
        if error_workflow:
            self.settings["errorWorkflow"] = WF_ERRORS
        if timeout:
            self.settings["executionTimeout"] = timeout  # потолок времени прогона, секунды

    def add(self, name, type_, version, params, pos, creds=None, **extra):
        node = {
            "parameters": params,
            "id": uid(),
            "name": name,
            "type": type_,
            "typeVersion": version,
            "position": [pos[0] + self.dx, pos[1]],
        }
        if creds:
            node["credentials"] = {}
            for key in creds:
                ctype, cid, cname = CREDS[key]
                node["credentials"][ctype] = {"id": cid, "name": cname}
        node.update(extra)
        self.nodes.append(node)
        return name

    def connect(self, src, dst, out=0, inp=0, kind="main"):
        outs = self.connections.setdefault(src, {}).setdefault(kind, [])
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": dst, "type": kind, "index": inp})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.connect(a, b)

    def note(self, text, pos, w=420, h=260, color=None):
        params = {"content": text, "width": w, "height": h}
        if color:
            params["color"] = color
        self.add(f"Note {len(self.nodes)}", "n8n-nodes-base.stickyNote", 1, params, pos)

    def dump(self, filename):
        data = {
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": self.settings,
            "pinData": {},
            "meta": {"templateCredsSetupCompleted": False},
        }
        OUT.mkdir(exist_ok=True)
        (OUT / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {filename}: {len([n for n in self.nodes if 'stickyNote' not in n['type']])} nodes")


# ---------------------------------------------------------------------------
# node helpers

def p_set(values, include_other=False):
    assignments = []
    for k, v in values.items():
        t = "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else (
            "boolean" if isinstance(v, bool) else "string")
        assignments.append({"id": uid(), "name": k, "value": v, "type": t})
    params = {"assignments": {"assignments": assignments}, "options": {}}
    if include_other:
        params["includeOtherFields"] = True
    return params


def code(js, each=False):
    p = {"jsCode": js.strip()}
    if each:
        p["mode"] = "runOnceForEachItem"
    return p


def if_true(expr):
    return {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [{"id": uid(), "leftValue": expr, "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
            "combinator": "and",
        },
        "looseTypeValidation": True,
        "options": {},
    }


def switch_on(field, keys):
    """Switch по значению поля: по выходу на каждый ключ (маршрут виден в экспорте)."""
    rule = lambda key: {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{"id": uid(), "leftValue": f"={{{{ $json.{field} }}}}", "rightValue": key,
                            "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"},
        "renameOutput": True, "outputKey": key}
    return {"rules": {"values": [rule(k) for k in keys]}, "options": {}}


def dt_ref(table):
    return {"__rl": True, "mode": "name", "value": table}


def dt_get(table, conditions=None, match="allConditions"):
    p = {"resource": "row", "operation": "get", "dataTableId": dt_ref(table), "returnAll": True}
    if conditions:
        p["matchType"] = match
        p["filters"] = {"conditions": [{"keyName": k, "condition": "eq", "keyValue": v} for k, v in conditions]}
    return p


def dt_upsert(table, key, key_expr):
    return {
        "resource": "row", "operation": "upsert", "dataTableId": dt_ref(table),
        "matchType": "allConditions",
        "filters": {"conditions": [{"keyName": key, "condition": "eq", "keyValue": key_expr}]},
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": [],
                    "attemptToConvertTypes": False, "convertFieldsToString": False},
        "options": {},
    }


def exec_wf(wf_id, wait=True):
    return {
        "workflowId": {"__rl": True, "value": wf_id, "mode": "id"},
        "workflowInputs": {"mappingMode": "defineBelow", "value": {}, "matchingColumns": [], "schema": [],
                           "attemptToConvertTypes": False, "convertFieldsToString": True},
        "mode": "once",
        "options": {"waitForSubWorkflow": wait},
    }


def sheets_append(doc_expr, tab_expr):
    return {
        "authentication": "oAuth2",
        "operation": "append",
        "documentId": {"__rl": True, "value": doc_expr, "mode": "id"},
        "sheetName": {"__rl": True, "value": tab_expr, "mode": "name"},
        "columns": {"mappingMode": "autoMapInputData", "value": {}, "matchingColumns": [], "schema": [],
                    "attemptToConvertTypes": False, "convertFieldsToString": False},
        "options": {"handlingExtraData": "ignoreIt"},
    }


def http_json(method, url, body_expr=None, cred="http_pinecone", headers=None, predefined=None, batching=None):
    p = {"method": method, "url": url}
    if predefined:
        p["authentication"] = "predefinedCredentialType"
        p["nodeCredentialType"] = predefined
    else:
        p["authentication"] = "genericCredentialType"
        p["genericAuthType"] = "httpHeaderAuth"
    if headers:
        p["sendHeaders"] = True
        p["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}
    if body_expr is not None:
        p["sendBody"] = True
        p["specifyBody"] = "json"
        p["jsonBody"] = body_expr
    p["options"] = {}
    if batching:
        p["options"]["batching"] = {"batch": {"batchSize": 1, "batchInterval": batching}}
    return p


# Код, который строит payload для подворкфлоу ошибок (WF00).
def error_payload_js(component, node, severity="error", alert=True, chat_expr="''", msg_expr="'Unknown error'",
                     details_expr="''"):
    return f"""
const items = $input.all();
const errText = (j) => {{
  const e = j && j.error;
  if (!e) return '';
  if (typeof e === 'string') return e;
  return e.message || e.description || JSON.stringify(e).slice(0, 500);
}};
return items.map((it) => {{
  const j = it.json || {{}};
  return {{ json: {{
    component: '{component}',
    workflow: $workflow.name,
    workflow_id: $workflow.id,
    node: '{node}',
    severity: '{severity}',
    alert: {str(alert).lower()},
    chat_id: String({chat_expr} || ''),
    message: String({msg_expr} || errText(j) || 'Unknown error').slice(0, 1000),
    details: String({details_expr} || '').slice(0, 1500),
    execution_id: $execution.id,
  }} }};
}});
"""


# ===========================================================================
# BRD-00 — обработчик ошибок и алерты саппорту
def build_errors():
    w = WF("BRD-00 Errors & Support Alerts", error_workflow=False)
    w.add("On workflow error", "n8n-nodes-base.errorTrigger", 1, {}, (0, 0))
    w.add("Handled error (from workflows)", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
          {"inputSource": "passthrough"}, (0, 200))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "ERRORS_SHEET_ID": "__CFG_LOGS_SHEET_ID__",
        "ERRORS_SHEET_TAB": "errors",
        "SUPPORT_CHAT_ID": "__CFG_SUPPORT_CHAT_ID__",
        "N8N_BASE_URL": "__CFG_N8N_BASE_URL__",
        "ALERT_DEDUP_MINUTES": 15,
    }, include_other=True), (240, 100))
    w.add("Normalize error", "n8n-nodes-base.code", 2, code("""
const cfg = $('Config').first().json;
const base = String(cfg.N8N_BASE_URL || '').replace(/\\/$/, '');
const cut = (s, n) => String(s == null ? '' : s).slice(0, n);
return $input.all().map((it) => {
  const j = it.json || {};
  let r;
  if (j.execution || j.trigger) {
    // Error Trigger: необработанное падение любого воркфлоу, у которого BRD-00 указан как Error workflow
    const ex = j.execution || {};
    const err = ex.error || (j.trigger && j.trigger.error) || {};
    const wf = j.workflow || {};
    r = {
      source: 'unhandled', severity: 'critical',
      workflow: wf.name || '', workflow_id: wf.id || '',
      node: ex.lastNodeExecuted || (err.node && err.node.name) || '',
      component: wf.name || '',
      message: cut(err.message || 'Unknown error', 1000),
      details: cut(err.description || err.stack || '', 1500),
      chat_id: '', execution_id: ex.id || '', execution_url: ex.url || '', alert: true,
    };
  } else {
    // Обработанная ошибка, присланная другим воркфлоу через Execute Workflow
    const sev = j.severity || 'error';
    r = {
      source: 'handled', severity: sev,
      workflow: j.workflow || '', workflow_id: j.workflow_id || '',
      node: j.node || '', component: j.component || '',
      message: cut(j.message, 1000),
      details: cut(typeof j.details === 'string' ? j.details : JSON.stringify(j.details || ''), 1500),
      chat_id: j.chat_id ? String(j.chat_id) : '',
      execution_id: j.execution_id || '',
      execution_url: j.execution_url || '',
      alert: j.alert !== false && ['error', 'critical'].includes(sev),
    };
  }
  if (!r.execution_url && r.execution_id && r.workflow_id && base && !base.startsWith('__')) {
    r.execution_url = `${base}/workflow/${r.workflow_id}/executions/${r.execution_id}`;
  }
  // Потолки, сработавшие как необработанное падение (таймаут прогона, лимит ключа) → ручная очередь.
  // Обработанные пределы воркфлоу отправляют в очередь сами, здесь их не дублируем.
  r.limit_type = '';
  if (r.source === 'unhandled') {
    const t = `${r.message} ${r.details}`;
    if (/timed? ?out|timeout|max(imum)? execution time/i.test(t)) r.limit_type = 'time';
    else if (MONEY_RE.test(t)) r.limit_type = 'cost_key';
  }
  r.timestamp = new Date().toISOString();
  return { json: r };
});
""".replace("const cfg = $('Config')", LIMIT_JS + "\nconst cfg = $('Config')", 1)), (480, 100))
    w.add("Append to errors sheet", "n8n-nodes-base.googleSheets", 4.5, sheets_append(
        "={{ $('Config').first().json.ERRORS_SHEET_ID }}", "={{ $('Config').first().json.ERRORS_SHEET_TAB }}"),
        (720, 100), creds=["gsheets"], onError="continueRegularOutput")
    w.add("Format alert (with dedup)", "n8n-nodes-base.code", 2, code("""
// Алерт в чат саппорта. Одинаковые ошибки (workflow + узел + текст) не чаще раза в ALERT_DEDUP_MINUTES.
const cfg = $('Config').first().json;
const sd = $getWorkflowStaticData('global');
sd.alerts = sd.alerts || {};
const now = Date.now();
const win = (Number(cfg.ALERT_DEDUP_MINUTES) || 15) * 60000;
for (const k of Object.keys(sd.alerts)) if (now - sd.alerts[k].ts > win * 4) delete sd.alerts[k];
const rows = $('Normalize error').all().map((i) => i.json);
const out = [];
for (const r of rows) {
  if (!r.alert) continue;
  const key = `${r.workflow}|${r.node}|${String(r.message).slice(0, 120)}`;
  const prev = sd.alerts[key];
  if (prev && now - prev.ts < win) { prev.suppressed = (prev.suppressed || 0) + 1; continue; }
  const suppressed = prev ? (prev.suppressed || 0) : 0;
  sd.alerts[key] = { ts: now, suppressed: 0 };
  const lines = [
    r.severity === 'critical' ? '🚨 СБОЙ WORKFLOW (необработанная ошибка)' : '⚠️ Ошибка в работе BRD-агента',
    `Workflow: ${r.workflow || '—'}`,
    `Узел: ${r.node || '—'}`,
    `Компонент: ${r.component || '—'}`,
    `Ошибка: ${r.message}`,
  ];
  if (r.chat_id) lines.push(`Чат заказчика: ${r.chat_id}`);
  if (r.execution_url) lines.push(`Execution: ${r.execution_url}`);
  else if (r.execution_id) lines.push(`Execution ID: ${r.execution_id}`);
  if (suppressed) lines.push(`(+${suppressed} таких же ошибок подавлено за ${cfg.ALERT_DEDUP_MINUTES} мин)`);
  lines.push(`Время (UTC): ${r.timestamp}`);
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  out.push({ json: { chat_id: String(cfg.SUPPORT_CHAT_ID), text: esc(lines.join('\\n').slice(0, 3800)) } });
}
return out;
"""), (960, 100))
    w.add("Alert support chat", "n8n-nodes-base.telegram", 1.2, {
        "chatId": "={{ $json.chat_id }}", "text": "={{ $json.text }}",
        "additionalFields": {"appendAttribution": False, "disable_web_page_preview": True, "parse_mode": "HTML"},
    }, (1200, 100), creds=["telegram"], onError="continueRegularOutput")
    w.add("Limit-class errors", "n8n-nodes-base.code", 2, code("""
return $('Normalize error').all().map((i) => i.json).filter((r) => r.limit_type).map((r) => ({ json: {
  limit_type: r.limit_type, limit_value: '', observed: r.message, workflow: r.workflow, workflow_id: r.workflow_id,
  execution_id: r.execution_id, execution_url: r.execution_url, session_id: '', chat_id: r.chat_id || '', username: '',
  details: `Необработанное падение в узле «${r.node}». Сессию (если есть) найдёт BRD-05 по статусу generating.`,
} }));
"""), (960, 300))
    w.add("Escalate via BRD-07", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_QUEUE, wait=False), (1200, 300))
    w.connect("On workflow error", "Config")
    w.connect("Handled error (from workflows)", "Config")
    w.chain("Config", "Normalize error", "Append to errors sheet", "Format alert (with dedup)", "Alert support chat")
    w.connect("Append to errors sheet", "Limit-class errors")
    w.connect("Limit-class errors", "Escalate via BRD-07")
    w.note("""## BRD-00 — ошибки и алерты саппорту
**Два входа:**
• **Error Trigger** — любое необработанное падение воркфлоу BRD-01…06 (у них в Settings → Error workflow указан этот воркфлоу).
• **Execute Workflow Trigger** — обработанные ошибки, которые воркфлоу присылают сами (поля `component, node, severity, message, details, chat_id, alert`).

**Дальше:** строка в Google Sheet `errors` для анализа и алерт в Telegram-чат саппорта (`severity` error/critical). Одинаковые алерты подавляются на 15 минут.

Необработанные падения класса «потолок» (таймаут прогона, лимит ключа OpenRouter 402/403) дополнительно уходят в ручную очередь **BRD-07**.

Telegram отправляется напрямую, а не через BRD-02, чтобы не возникло петли, если сломан сам Notify.""",
           (-40, -380), w=620, h=340, color=3)
    w.dump("BRD-00_errors-and-alerts.json")


# ===========================================================================
# BRD-02 — уведомления в Telegram (подворкфлоу)
def build_notify():
    w = WF("BRD-02 Notify (Telegram)")
    w.add("When called by another workflow", "n8n-nodes-base.executeWorkflowTrigger", 1.1,
          {"inputSource": "passthrough"}, (0, 0))
    w.add("Manual test", "n8n-nodes-base.manualTrigger", 1, {}, (0, 200))
    w.add("Test message", "n8n-nodes-base.set", 3.4, p_set({
        "audience": "team", "kind": "info", "title": "Тест уведомлений",
        "text": "Если вы видите это сообщение — BRD-02 Notify настроен правильно.",
    }), (220, 200))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "TEAM_CHAT_ID": "__CFG_TEAM_CHAT_ID__",
        "SUPPORT_CHAT_ID": "__CFG_SUPPORT_CHAT_ID__",
    }, include_other=True), (440, 0))
    w.add("Prepare messages", "n8n-nodes-base.code", 2, code("""
// Вход (по одному item на сообщение):
//   audience: team | support | customer   — кому
//   chat_id:  явный чат (обязателен для customer)
//   kind:     info | reminder | success | warning | alert | report
//   title, text
const cfg = $('Config').first().json;
const ICON = { info: 'ℹ️', reminder: '⏰', success: '✅', warning: '⚠️', alert: '🚨', report: '📊' };
const MAX = 3900;
const out = [];
for (const it of $input.all()) {
  const j = it.json || {};
  const audience = j.audience || 'team';
  let chat_id = j.chat_id;
  if (!chat_id) chat_id = audience === 'support' ? cfg.SUPPORT_CHAT_ID : (audience === 'team' ? cfg.TEAM_CHAT_ID : '');
  if (!chat_id || String(chat_id).startsWith('__')) throw new Error(`Notify: не задан chat_id для audience=${audience}`);
  const icon = ICON[j.kind] || '';
  const head = j.title ? `${icon ? icon + ' ' : ''}${j.title}` : '';
  let s = [head, j.text || ''].filter(Boolean).join('\\n\\n').trim();
  if (!head && icon) s = `${icon} ${s}`;
  if (!s) s = '(пустое сообщение)';
  const parts = [];
  while (s.length > MAX) {
    let c = s.lastIndexOf('\\n', MAX);
    if (c < MAX * 0.5) c = MAX;
    parts.push(s.slice(0, c));
    s = s.slice(c).replace(/^\\s+/, '');
  }
  parts.push(s);
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  parts.forEach((p, i) => out.push({ json: {
    chat_id: String(chat_id), text: esc(p), audience, kind: j.kind || 'info', source: j.source || '',
    part: i + 1, parts: parts.length,
  } }));
}
return out;
"""), (660, 0))
    w.add("Send to Telegram", "n8n-nodes-base.telegram", 1.2, {
        "chatId": "={{ $json.chat_id }}", "text": "={{ $json.text }}",
        "additionalFields": {"appendAttribution": False, "disable_web_page_preview": True, "parse_mode": "HTML"},
    }, (880, 0), creds=["telegram"], onError="continueErrorOutput")
    w.add("Sent", "n8n-nodes-base.noOp", 1, {}, (1100, -80))
    w.add("Error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "notify", "Send to Telegram", alert=True,
        chat_expr="$('Prepare messages').all()[0]?.json.chat_id",
        msg_expr="'Не удалось отправить уведомление в Telegram: ' + errText(j)",
        details_expr="$('Prepare messages').all()[0]?.json.text")), (1100, 100))
    w.add("Log via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False), (1320, 100))
    w.connect("When called by another workflow", "Config")
    w.connect("Manual test", "Test message")
    w.connect("Test message", "Config")
    w.chain("Config", "Prepare messages", "Send to Telegram", "Sent")
    w.connect("Send to Telegram", "Error payload", out=1)
    w.connect("Error payload", "Log via BRD-00")
    w.note("""## BRD-02 — уведомления в Telegram
Единая точка отправки сообщений. Вызывается из BRD-01/04/05/06 через **Execute Workflow**.

**Вход:** `audience` (team | support | customer), `chat_id` (для customer), `kind` (info, reminder, success, warning, alert, report), `title`, `text`.

Чаты команды и саппорта задаются в узле **Config**. Длинные сообщения режутся на части по 3900 символов.
**Manual test** — проверить бота и чат команды.""",
           (-40, -330), w=640, h=280, color=4)
    w.dump("BRD-02_notify.json")


# ===========================================================================
# BRD-07 — ручная очередь: исход любого сработавшего потолка (fallback к человеку)
LIMIT_NAMES = {
    "steps": "Шаги агента за прогон (maxIterations)",
    "turns": "Длина интервью (ответов заказчика)",
    "cost_session": "Бюджет сессии, USD (оценка)",
    "cost_key": "Лимит ключа OpenRouter, USD в неделю",
    "time": "Время прогона (таймаут)",
    "brd_attempts": "Попытки генерации BRD на сессию",
    "artifacts_chat": "BRD от одного заказчика за 24 ч",
    "artifacts_day": "BRD всего за 24 ч (боевые и тестовые считаются раздельно)",
    "validator": "Проверка BRD по стандарту после исправления",
}


def build_queue():
    w = WF("BRD-07 Manual Queue (limits)")
    w.add("When a limit is hit", "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"inputSource": "passthrough"}, (0, 0))
    w.add("Manual test", "n8n-nodes-base.manualTrigger", 1, {}, (0, 200))
    w.add("Test limit", "n8n-nodes-base.set", 3.4, p_set({
        "limit_type": "steps", "limit_value": LIMITS["INTERVIEW_MAX_ITERATIONS"], "observed": "тест",
        "workflow": "BRD-07 Manual test", "session_id": "", "chat_id": "", "username": "",
        "details": "Проверка ручной очереди: строка в manual_queue и сообщение в чат команды.",
    }), (220, 200))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "LOGS_SHEET_ID": "__CFG_LOGS_SHEET_ID__",
        "QUEUE_TAB": "manual_queue",
        "ASSIGNEE": "Команда ИИ-среды",
        "N8N_BASE_URL": "__CFG_N8N_BASE_URL__",
    }, include_other=True), (440, 0))
    names_js = json.dumps(LIMIT_NAMES, ensure_ascii=False)
    w.add("Queue rows", "n8n-nodes-base.code", 2, code(f"""
// Вход: limit_type, limit_value, observed, workflow, workflow_id, execution_id, session_id, chat_id, username, details.
// Исход у всех потолков один: строка в очереди к роли с правом решить. Перезапуска нет.
const cfg = $('Config').first().json;
const NAMES = {names_js};
const base = String(cfg.N8N_BASE_URL || '').replace(/\\/$/, '');
const now = new Date().toISOString();
return $input.all().map((it, k) => {{
  const j = it.json || {{}};
  let url = j.execution_url || '';
  if (!url && j.execution_id && j.workflow_id && base && !base.startsWith('__')) url = `${{base}}/workflow/${{j.workflow_id}}/executions/${{j.execution_id}}`;
  return {{ json: {{
    queued_at: now,
    queue_id: `Q-${{Date.now().toString(36)}}-${{k}}`,
    limit_type: j.limit_type || 'unknown',
    limit_name: NAMES[j.limit_type] || String(j.limit_type || 'неизвестный потолок'),
    limit_value: String(j.limit_value ?? ''),
    observed: String(j.observed ?? '').slice(0, 300),
    workflow: j.workflow || '',
    session_id: j.session_id || '',
    chat_id: String(j.chat_id || ''),
    customer: j.username || '',
    details: String(j.details || '').slice(0, 1000),
    execution_url: url,
    outcome: 'остановлено по пределу → ручная очередь (без перезапуска)',
    assignee: cfg.ASSIGNEE,
    status: 'new',
    resolution: '',
    resolved_at: '',
  }} }};
}});
"""), (660, 0))
    w.add("Append to manual_queue", "n8n-nodes-base.googleSheets", 4.5, sheets_append(
        "={{ $('Config').first().json.LOGS_SHEET_ID }}", "={{ $('Config').first().json.QUEUE_TAB }}"),
        (880, 0), creds=["gsheets"], onError="continueErrorOutput")
    w.add("Team message", "n8n-nodes-base.code", 2, code("""
// Сообщение в чат команды отправляется, даже если запись в Sheets не удалась: прогон не должен потеряться.
return $('Queue rows').all().map((i) => i.json).map((q) => ({ json: {
  audience: 'team', kind: 'warning', title: 'Остановлено по пределу → ручная очередь',
  text: [
    `Потолок: ${q.limit_name} = ${q.limit_value || '—'} (факт: ${q.observed || '—'})`,
    `Workflow: ${q.workflow || '—'}`,
    q.customer || q.chat_id ? `Заказчик: ${q.customer || q.chat_id}` : '',
    q.session_id ? `Session ID: ${q.session_id}` : '',
    q.details ? `Детали: ${q.details}` : '',
    q.execution_url ? `Execution: ${q.execution_url}` : '',
    `Очередь: лист manual_queue, ${q.queue_id}. Разбирает: ${q.assignee}. Перезапуск не выполнялся.`,
  ].filter(Boolean).join('\\n'),
  source: 'manual_queue' } }));
"""), (1100, 0))
    w.add("Notify team via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (1320, 0))
    w.add("Sheet error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "manual_queue", "Append to manual_queue", alert=True,
        msg_expr="'Не удалось записать прогон в ручную очередь (manual_queue): ' + errText(j)")), (1100, 200))
    w.add("Log via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False), (1320, 200))
    w.connect("When a limit is hit", "Config")
    w.connect("Manual test", "Test limit")
    w.connect("Test limit", "Config")
    w.chain("Config", "Queue rows", "Append to manual_queue", "Team message", "Notify team via BRD-02")
    w.connect("Append to manual_queue", "Sheet error payload", out=1)
    w.connect("Append to manual_queue", "Team message", out=1)
    w.connect("Sheet error payload", "Log via BRD-00")
    w.note("""## BRD-07 — ручная очередь (fallback к человеку)
Сюда приходит **любой** сработавший потолок control layer: шаги агента, длина интервью, бюджет сессии, лимит ключа OpenRouter, таймаут, лимиты на число BRD.

**Исход:** строка в листе `manual_queue` (таблица «BRD agent — logs») со статусом `new` + предупреждение в чат команды ИИ-среды. Сессия заказчика заморожена (`needs_review`) — это делает вызывающий воркфлоу.

**Перезапуска нет:** повтор после предела кормит разгон. Решение принимает команда: продолжить (`status=active` в brd_sessions), сформировать BRD вручную (BRD-04 → Manual test) или закрыть (`cancelled`). В очереди: `status` → `resolved`, `resolution`, `resolved_at`.

**Manual test** — проверить очередь и уведомление.""",
           (-40, -400), w=700, h=350, color=5)
    w.dump("BRD-07_manual-queue.json")


# ===========================================================================
# BRD-01 — синхронизация базы знаний Google Drive -> Pinecone
CHUNKER_JS = r"""
// Нарезка текста на фрагменты ~CHUNK_SIZE токенов с перекрытием (по границам пунктов/абзацев).
// Google Sheets / CSV режем по строкам таблицы: «Колонка: значение; ...».
const cfg = $('Config').first().json;
const f = $('Prepare export').first().json;
const CHUNK = Number(cfg.CHUNK_SIZE_TOKENS) || 500;
const OVERLAP = Number(cfg.CHUNK_OVERLAP_TOKENS) || 100;
const CPT = Number(cfg.CHARS_PER_TOKEN) || 2.5;
const est = (s) => Math.max(1, Math.ceil(s.length / CPT));
let text = String($('Collect text').first().json.text || '');
const isTable = f.kind === 'gsheet' || f.kind === 'csv';

function parseCsv(src) {
  const rows = []; let row = []; let cur = ''; let q = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (q) {
      if (c === '"' && src[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') q = false;
      else cur += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
    else if (c !== '\r') cur += c;
  }
  if (cur || row.length) { row.push(cur); rows.push(row); }
  return rows.filter((r) => r.some((v) => String(v).trim()));
}

let blocks = [];
if (isTable) {
  const rows = parseCsv(text);
  const head = (rows.shift() || []).map((h) => String(h).trim());
  blocks = rows.map((r) => r.map((v, k) => (String(v).trim() ? `${head[k] || 'Колонка ' + (k + 1)}: ${String(v).trim()}` : ''))
    .filter(Boolean).join('; '));
} else {
  text = text.replace(/\r\n/g, '\n').replace(/ /g, ' ').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  const isBoundary = (l) => /^\s*\d+(\.\d+){0,3}[.)]?\s+\S/.test(l) || /^\s*#{1,6}\s+\S/.test(l) ||
    /^\s*(Часть|Раздел|Приложение|Глава)\b/i.test(l) || /^\s*В:\s/.test(l);
  let cur = [];
  for (const l of text.split('\n')) {
    if (isBoundary(l) && cur.join('').trim()) { blocks.push(cur.join('\n').trim()); cur = []; }
    cur.push(l);
  }
  if (cur.join('').trim()) blocks.push(cur.join('\n').trim());
  if (blocks.length < 3) blocks = text.split(/\n{2,}/);
}
blocks = blocks.map((b) => String(b).trim()).filter(Boolean);

const chunks = [];
let buf = []; let tok = 0;
const flush = () => {
  if (!buf.length) return;
  chunks.push(buf.join('\n\n'));
  const keep = []; let t = 0;
  for (let i = buf.length - 1; i >= 0 && t < OVERLAP && !isTable; i--) { keep.unshift(buf[i]); t += est(buf[i]); }
  buf = keep.length === buf.length ? [] : keep; tok = buf.reduce((x, b) => x + est(b), 0);
};
for (const b of blocks) {
  const parts = est(b) > CHUNK ? b.split(/(?<=[.!?…;])\s+|\n/).filter((s) => s.trim()) : [b];
  for (const p of parts) {
    if (tok + est(p) > CHUNK && buf.length) flush();
    buf.push(p); tok += est(p);
  }
}
if (buf.length) chunks.push(buf.join('\n\n'));

const header = `[${f.doc_code ? f.doc_code + ' — ' : ''}${f.name}]`;
if (!chunks.length) return [{ json: { _empty: true } }];
return chunks.map((c, i) => ({ json: {
  id: `${f.file_id}#${String(i).padStart(4, '0')}`,
  text: `${header}\n${c}`,
  metadata: {
    source_id: f.file_id, doc_code: f.doc_code, title: f.name, mime_type: f.mime_type,
    source_url: f.web_link, modified_time: f.modified_time,
    chunk_index: i, chunk_count: chunks.length, token_estimate: est(c),
  },
} }));
"""


def build_kb_sync():
    w = WF("BRD-01 Knowledge Base Sync (Drive → Pinecone)")
    y = 0
    w.add("Daily 03:00", "n8n-nodes-base.scheduleTrigger", 1.2,
          {"rule": {"interval": [{"field": "cronExpression", "expression": "0 3 * * *"}]}}, (0, y))
    w.add("Manual run", "n8n-nodes-base.manualTrigger", 1, {}, (0, y + 180))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "KB_FOLDER_ID": "__CFG_KB_FOLDER_ID__",
        "KB_EXCLUDE_REGEX": "^(_|~)",
        "KB_LOG_SHEET_ID": "__CFG_KB_LOG_SHEET_ID__",
        "KB_LOG_SHEET_TAB": "kb_sync_log",
        "PINECONE_INDEX_HOST": "__CFG_PINECONE_INDEX_HOST__",
        "PINECONE_NAMESPACE": "__CFG_PINECONE_NAMESPACE__",
        "PINECONE_API_VERSION": "2024-10",
        "EMBEDDING_URL": "__CFG_EMBEDDING_URL__",
        "EMBEDDING_MODEL": "__CFG_EMBEDDING_MODEL__",
        "EMBED_BATCH_SIZE": 48,
        "CHUNK_SIZE_TOKENS": 500,
        "CHUNK_OVERLAP_TOKENS": 100,
        "CHARS_PER_TOKEN": 2.5,
    }), (220, y + 90))
    w.add("List files in KB folder", "n8n-nodes-base.googleDrive", 3, {
        "authentication": "oAuth2",
        "resource": "fileFolder",
        "searchMethod": "query",
        # trashed = false обязателен: Drive API по умолчанию отдаёт и файлы из корзины,
        # а n8n сам добавляет его, только если заполнен Filter (у нас он пустой).
        "queryString": "={{ \"'\" + $json.KB_FOLDER_ID + \"' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed = false\" }}",
        "returnAll": True,
        "filter": {},
        "options": {"fields": ["*"]},
    }, (440, y + 90), creds=["gdrive"], alwaysOutputData=True)
    w.add("Load KB state", "n8n-nodes-base.dataTable", 1, dt_get(KB_TABLE), (660, y + 90),
          alwaysOutputData=True, executeOnce=True)
    w.add("Plan changes", "n8n-nodes-base.code", 2, code("""
// Сравниваем содержимое папки с состоянием в Data Table kb_files:
// новые / изменённые (modifiedTime) / удалённые / без изменений / неподдерживаемые.
const cfg = $('Config').first().json;
const exclude = cfg.KB_EXCLUDE_REGEX ? new RegExp(cfg.KB_EXCLUDE_REGEX) : null;
// Страховка к trashed = false в запросе: файлы из корзины в индекс не попадают никогда.
const files = $('List files in KB folder').all().map((i) => i.json).filter((f) => f && f.id && f.trashed !== true);
const state = {};
for (const r of $('Load KB state').all().map((i) => i.json)) if (r && r.file_id) state[r.file_id] = r;

const KIND = {
  'application/vnd.google-apps.document': 'gdoc',
  'application/vnd.google-apps.spreadsheet': 'gsheet',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'office',
  'application/msword': 'office',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'office',
  'application/vnd.ms-excel': 'office',
  'application/pdf': 'pdf',
  'text/plain': 'text', 'text/markdown': 'text', 'text/x-markdown': 'text', 'text/csv': 'csv',
};
const officeTarget = (m) => (/sheet|excel/.test(m) ? 'application/vnd.google-apps.spreadsheet' : 'application/vnd.google-apps.document');

const to_process = [], unchanged = [], unsupported = [], excluded = [];
const seen = new Set();
for (const f of files) {
  if (exclude && exclude.test(f.name)) { excluded.push(f.name); continue; }
  const kind = KIND[f.mimeType] || (/\\.(md|txt)$/i.test(f.name) ? 'text' : null);
  if (!kind) { unsupported.push({ file_id: f.id, name: f.name, mime_type: f.mimeType, link: f.webViewLink || '' }); continue; }
  seen.add(f.id);
  const prev = state[f.id];
  const changed = !prev || prev.modified_time !== f.modifiedTime || prev.status !== 'ok';
  if (!changed) { unchanged.push(f.name); continue; }
  to_process.push({
    file_id: f.id, name: f.name, mime_type: f.mimeType, kind,
    office_target: kind === 'office' ? officeTarget(f.mimeType) : '',
    modified_time: f.modifiedTime || '', web_link: f.webViewLink || '',
    doc_code: ((f.name.match(/DOC-\\d+/i) || [''])[0]).toUpperCase(),
    action: prev ? (prev.status === 'ok' ? 'updated' : 'retried') : 'added',
    prev_chunk_count: Number(prev && prev.chunk_count) || 0,
  });
}
// Два файла с одним кодом DOC-xx (например, .docx и его Google-копия) — дубли в индексе.
// Не удаляем сами: какой файл главный, решает человек; предупреждаем в отчёте.
const byCode = {};
for (const f of files) {
  if (!seen.has(f.id)) continue;
  const code = ((f.name.match(/DOC-\\d+/i) || [''])[0]).toUpperCase();
  if (code) (byCode[code] = byCode[code] || []).push(f.name);
}
const duplicates = Object.entries(byCode).filter(([, names]) => names.length > 1).map(([code, names]) => ({ code, names }));
const to_delete = Object.values(state).filter((r) => !seen.has(r.file_id)).map((r) => {
  const n = Math.max(1, Number(r.chunk_count) || 0);
  return { file_id: r.file_id, name: r.name || '', prev_chunk_count: Number(r.chunk_count) || 0,
           ids: Array.from({ length: n }, (_, k) => `${r.file_id}#${String(k).padStart(4, '0')}`) };
});
return [{ json: {
  run_id: $execution.id, started_at: new Date().toISOString(),
  to_process, to_delete, unchanged, unsupported, excluded, duplicates,
} }];
"""), (880, y + 90))
    w.add("Any removed files?", "n8n-nodes-base.if", 2, if_true("={{ $json.to_delete.length > 0 }}"), (1100, y + 90))
    w.add("Explode removed", "n8n-nodes-base.code", 2, code(
        "return $('Plan changes').first().json.to_delete.map((d) => ({ json: d }));"), (1320, y - 60))
    cfg = "$('Config').first().json"
    w.add("Pinecone: delete removed", "n8n-nodes-base.httpRequest", 4.2, http_json(
        "POST", f"={{{{ {cfg}.PINECONE_INDEX_HOST }}}}/vectors/delete",
        f"={{{{ JSON.stringify({{ ids: $json.ids, namespace: {cfg}.PINECONE_NAMESPACE }}) }}}}",
        headers={"X-Pinecone-API-Version": f"={{{{ {cfg}.PINECONE_API_VERSION }}}}"}),
        (1540, y - 60), creds=["http_pinecone"], onError="continueRegularOutput")
    w.add("Forget removed", "n8n-nodes-base.dataTable", 1, {
        "resource": "row", "operation": "deleteRows", "dataTableId": dt_ref(KB_TABLE),
        "matchType": "allConditions",
        "filters": {"conditions": [{"keyName": "file_id", "condition": "eq",
                                    "keyValue": "={{ $('Explode removed').item.json.file_id }}"}]},
        "options": {},
    }, (1760, y - 60), onError="continueRegularOutput")
    w.add("Back to plan", "n8n-nodes-base.code", 2, code("return [$('Plan changes').first()];"), (1980, y - 60))
    w.add("Anything to index?", "n8n-nodes-base.if", 2,
          if_true("={{ $('Plan changes').first().json.to_process.length > 0 }}"), (2200, y + 90))
    w.add("Explode files", "n8n-nodes-base.code", 2, code(
        "return $('Plan changes').first().json.to_process.map((f) => ({ json: f }));"), (2420, y + 20))
    w.add("For each file", "n8n-nodes-base.splitInBatches", 3, {"options": {}}, (2640, y + 20))

    # --- тело цикла
    ly = y + 380
    w.add("Office file?", "n8n-nodes-base.if", 2, if_true("={{ $json.kind === 'office' }}"), (2860, ly))
    w.add("Convert to Google format (temp copy)", "n8n-nodes-base.httpRequest", 4.2, http_json(
        "POST", "=https://www.googleapis.com/drive/v3/files/{{ $json.file_id }}/copy?supportsAllDrives=true&fields=id,name",
        "={{ JSON.stringify({ name: '_tmp_convert_' + $json.name, mimeType: $json.office_target }) }}",
        predefined="googleDriveOAuth2Api"), (3080, ly - 100), creds=["gdrive"], onError="continueErrorOutput")
    w.add("Prepare export", "n8n-nodes-base.code", 2, code("""
const f = $('For each file').first().json;
const isOffice = f.kind === 'office';
const export_id = isOffice ? $json.id : f.file_id;
const kind = isOffice ? (f.office_target.includes('spreadsheet') ? 'gsheet' : 'gdoc') : f.kind;
return [{ json: { ...f, kind, export_id, tmp_copy_id: isOffice ? $json.id : '' } }];
"""), (3300, ly))
    w.add("Download / export text", "n8n-nodes-base.googleDrive", 3, {
        "authentication": "oAuth2",
        "operation": "download",
        "fileId": {"__rl": True, "value": "={{ $json.export_id }}", "mode": "id"},
        "options": {"googleFileConversion": {"conversion": {"docsToFormat": "text/plain", "sheetsToFormat": "text/csv"}}},
    }, (3520, ly), creds=["gdrive"], onError="continueErrorOutput")
    w.add("PDF?", "n8n-nodes-base.if", 2, if_true("={{ $('Prepare export').first().json.kind === 'pdf' }}"), (3740, ly))
    w.add("Extract PDF text", "n8n-nodes-base.extractFromFile", 1, {
        "operation": "pdf", "binaryPropertyName": "data", "options": {"joinPages": True},
    }, (3960, ly - 100), onError="continueErrorOutput")
    w.add("Collect text", "n8n-nodes-base.code", 2, code("""
let text = '';
const items = $input.all();
for (let i = 0; i < items.length; i++) {
  const it = items[i];
  if (it.json && typeof it.json.text === 'string') text += it.json.text + '\\n';
  else if (it.binary && it.binary.data) text += (await this.helpers.getBinaryDataBuffer(i, 'data')).toString('utf-8') + '\\n';
}
text = text.replace(/^\\uFEFF/, '');
return [{ json: { text, length: text.length } }];
"""), (4180, ly))
    w.add("Temp copy to remove?", "n8n-nodes-base.if", 2,
          if_true("={{ !!$('Prepare export').first().json.tmp_copy_id }}"), (4400, ly))
    w.add("Delete temp copy", "n8n-nodes-base.httpRequest", 4.2, http_json(
        "DELETE", "=https://www.googleapis.com/drive/v3/files/{{ $('Prepare export').first().json.tmp_copy_id }}?supportsAllDrives=true",
        predefined="googleDriveOAuth2Api"), (4620, ly - 100), creds=["gdrive"], onError="continueRegularOutput")
    w.add("Split into chunks", "n8n-nodes-base.code", 2, code(CHUNKER_JS), (4840, ly))
    w.add("Build embedding batches", "n8n-nodes-base.code", 2, code("""
const B = Number($('Config').first().json.EMBED_BATCH_SIZE) || 48;
const rows = $input.all().map((i) => i.json).filter((r) => r && r.id && r.text);
const out = [];
for (let s = 0; s < rows.length; s += B) {
  const slice = rows.slice(s, s + B);
  out.push({ json: { batch_index: out.length, ids: slice.map((c) => c.id), texts: slice.map((c) => c.text),
                     metas: slice.map((c) => ({ ...c.metadata, text: c.text })) } });
}
if (!out.length) out.push({ json: { batch_index: 0, ids: [], texts: [], metas: [] } });
return out;
"""), (5060, ly))
    w.add("Has chunks?", "n8n-nodes-base.if", 2, if_true("={{ $json.ids.length > 0 }}"), (5280, ly))
    w.add("Create embeddings (cloud.ru)", "n8n-nodes-base.httpRequest", 4.2, http_json(
        "POST", f"={{{{ {cfg}.EMBEDDING_URL }}}}",
        f"={{{{ JSON.stringify({{ model: {cfg}.EMBEDDING_MODEL, input: $json.texts }}) }}}}",
        batching=1500), (5500, ly - 100), creds=["http_cloudru"], onError="continueErrorOutput",
        retryOnFail=True, maxTries=3, waitBetweenTries=3000)
    w.add("Assemble vectors", "n8n-nodes-base.code", 2, code("""
const batches = $('Build embedding batches').all().map((i) => i.json).filter((b) => b.ids.length);
const resp = $input.all().map((i) => i.json);
const out = [];
for (let b = 0; b < batches.length; b++) {
  const data = (resp[b] && resp[b].data) || [];
  if (data.length !== batches[b].ids.length) throw new Error(`Эмбеддинги: ожидалось ${batches[b].ids.length}, получено ${data.length}`);
  const vectors = batches[b].ids.map((id, i) => ({ id, values: data[i].embedding, metadata: batches[b].metas[i] }));
  out.push({ json: { vectors, count: vectors.length } });
}
return out;
"""), (5720, ly - 100), onError="continueErrorOutput")
    w.add("Pinecone: upsert", "n8n-nodes-base.httpRequest", 4.2, http_json(
        "POST", f"={{{{ {cfg}.PINECONE_INDEX_HOST }}}}/vectors/upsert",
        f"={{{{ JSON.stringify({{ vectors: $json.vectors, namespace: {cfg}.PINECONE_NAMESPACE }}) }}}}",
        headers={"X-Pinecone-API-Version": f"={{{{ {cfg}.PINECONE_API_VERSION }}}}"}),
        (5940, ly - 100), creds=["http_pinecone"], onError="continueErrorOutput",
        retryOnFail=True, maxTries=3, waitBetweenTries=3000)
    w.add("File indexed", "n8n-nodes-base.code", 2, code("""
// Успех: старые фрагменты с теми же ID перезаписаны upsert'ом; «хвост» (если фрагментов стало меньше) удаляем.
const f = $('Prepare export').first().json;
const chunks = $('Split into chunks').all().filter((i) => i.json && i.json.id).length;
const stale_ids = [];
for (let k = chunks; k < f.prev_chunk_count; k++) stale_ids.push(`${f.file_id}#${String(k).padStart(4, '0')}`);
return [{ json: {
  file_id: f.file_id, name: f.name, mime_type: f.mime_type, doc_code: f.doc_code,
  modified_time: f.modified_time, web_link: f.web_link,
  chunk_count: chunks, status: chunks ? 'ok' : 'empty', error: '',
  ingested_at: new Date().toISOString(), action: f.action, stale_ids,
} }];
"""), (6160, ly))
    w.add("Stale tail?", "n8n-nodes-base.if", 2, if_true("={{ $json.stale_ids.length > 0 }}"), (6380, ly))
    w.add("Pinecone: delete stale tail", "n8n-nodes-base.httpRequest", 4.2, http_json(
        "POST", f"={{{{ {cfg}.PINECONE_INDEX_HOST }}}}/vectors/delete",
        f"={{{{ JSON.stringify({{ ids: $json.stale_ids, namespace: {cfg}.PINECONE_NAMESPACE }}) }}}}",
        headers={"X-Pinecone-API-Version": f"={{{{ {cfg}.PINECONE_API_VERSION }}}}"}),
        (6600, ly - 100), creds=["http_pinecone"], onError="continueRegularOutput")
    w.add("File failed", "n8n-nodes-base.code", 2, code("""
// Любая ошибка обработки файла: старые фрагменты в Pinecone остаются, modified_time сбрасываем —
// файл будет повторно обработан при следующем запуске.
const f = $('For each file').first().json;
const j = $input.first().json || {};
const e = j.error;
const msg = !e ? 'Unknown error' : (typeof e === 'string' ? e : (e.message || e.description || JSON.stringify(e))).slice(0, 800);
return [{ json: {
  file_id: f.file_id, name: f.name, mime_type: f.mime_type, doc_code: f.doc_code,
  modified_time: '', web_link: f.web_link,
  chunk_count: f.prev_chunk_count, status: 'error', error: msg,
  ingested_at: new Date().toISOString(), action: 'error', stale_ids: [],
} }];
"""), (6160, ly + 260))
    w.add("Error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "kb_sync", "File failed", alert=False,
        msg_expr="`${j.name}: ${j.error}`", details_expr="j.file_id")), (6380, ly + 380))
    w.add("Log via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False), (6600, ly + 380))
    w.add("State row", "n8n-nodes-base.code", 2, code("""
const r = $('File failed').isExecuted ? $('File failed').first().json : $('File indexed').first().json;
const { stale_ids, action, ...row } = r;
return [{ json: row }];
"""), (6820, ly))
    w.add("Save file state", "n8n-nodes-base.dataTable", 1, dt_upsert(KB_TABLE, "file_id", "={{ $json.file_id }}"),
          (7040, ly), onError="continueRegularOutput")
    w.add("Loop result", "n8n-nodes-base.code", 2, code("""
const r = $('File failed').isExecuted ? $('File failed').first().json : $('File indexed').first().json;
return [{ json: { _result: true, file_id: r.file_id, name: r.name, action: r.action, status: r.status,
                  chunks: r.chunk_count, error: r.error, link: r.web_link } }];
"""), (7260, ly))

    # --- итог
    w.add("Run summary", "n8n-nodes-base.code", 2, code("""
const plan = $('Plan changes').first().json;
const results = $input.all().map((i) => i.json).filter((r) => r && r._result);
const ts = new Date().toISOString();
const rows = [];
for (const r of results) rows.push({ timestamp: ts, run_id: plan.run_id, action: r.action, file_name: r.name,
  file_id: r.file_id, status: r.status, chunks: r.chunks, error: r.error || '', link: r.link || '' });
for (const d of plan.to_delete) rows.push({ timestamp: ts, run_id: plan.run_id, action: 'deleted', file_name: d.name,
  file_id: d.file_id, status: 'ok', chunks: 0, error: '', link: '' });
for (const u of plan.unsupported) rows.push({ timestamp: ts, run_id: plan.run_id, action: 'unsupported', file_name: u.name,
  file_id: u.file_id, status: 'skipped', chunks: 0, error: 'Неподдерживаемый формат: ' + u.mime_type, link: u.link });
const cnt = (a) => results.filter((r) => r.action === a && r.status !== 'error').length;
const errors = results.filter((r) => r.status === 'error');
const s = {
  added: cnt('added'), updated: cnt('updated') + cnt('retried'), deleted: plan.to_delete.length,
  unchanged: plan.unchanged.length, unsupported: plan.unsupported.length, errors: errors.length,
  chunks: results.reduce((x, r) => x + (r.status === 'error' ? 0 : Number(r.chunks) || 0), 0),
};
rows.push({ timestamp: ts, run_id: plan.run_id, action: 'run_summary', file_name: '', file_id: '',
  status: errors.length ? 'with_errors' : 'ok', chunks: s.chunks,
  error: `added=${s.added} updated=${s.updated} deleted=${s.deleted} unchanged=${s.unchanged} unsupported=${s.unsupported} errors=${s.errors}`, link: '' });
const changed = s.added + s.updated + s.deleted;
const lines = [
  `Новых: ${s.added}, обновлённых: ${s.updated}, удалённых: ${s.deleted}, без изменений: ${s.unchanged}`,
  `Фрагментов записано в Pinecone: ${s.chunks}`,
];
if (s.unsupported) lines.push(`Пропущено (формат не поддерживается): ${plan.unsupported.map((u) => u.name).join(', ')}`);
const dups = plan.duplicates || [];
if (dups.length) lines.push(`⚠️ Один документ лежит в папке несколько раз — в индексе дубли, оставьте один файл: ` +
  dups.map((d) => `${d.code}: ${d.names.join(' / ')}`).join('; '));
if (errors.length) lines.push(`Ошибки (${errors.length}): ` + errors.map((e) => `${e.name} — ${String(e.error).slice(0, 150)}`).join('; '));
const notifications = [{ audience: 'team', kind: errors.length || dups.length ? 'warning' : 'report',
  title: 'Обновление базы знаний' + (changed || errors.length ? '' : ' — без изменений'), text: lines.join('\\n'), source: 'kb_sync' }];
if (errors.length) notifications.push({ audience: 'support', kind: 'alert', title: 'Ошибки синхронизации базы знаний',
  text: lines.join('\\n') + `\\nДетали — лист kb_sync_log, run_id=${plan.run_id}`, source: 'kb_sync' });
return [{ json: { summary: s, rows, notifications } }];
"""), (2860, y - 200))
    w.add("Log rows", "n8n-nodes-base.code", 2, code("return $json.rows.map((r) => ({ json: r }));"), (3080, y - 200))
    w.add("Append to KB sync log", "n8n-nodes-base.googleSheets", 4.5, sheets_append(
        f"={{{{ {cfg}.KB_LOG_SHEET_ID }}}}", f"={{{{ {cfg}.KB_LOG_SHEET_TAB }}}}"),
        (3300, y - 200), creds=["gsheets"], onError="continueErrorOutput")
    w.add("Sheet error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "kb_sync", "Append to KB sync log", alert=True,
        msg_expr="'Не удалось записать журнал синхронизации в Google Sheets: ' + errText(j)")), (3520, y - 60))
    w.add("Log sheet error via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False),
          (3740, y - 60))
    w.add("Notifications", "n8n-nodes-base.code", 2, code(
        "return $('Run summary').first().json.notifications.map((n) => ({ json: n }));"), (3520, y - 200),
        executeOnce=True)
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (3740, y - 200))

    w.connect("Daily 03:00", "Config")
    w.connect("Manual run", "Config")
    w.chain("Config", "List files in KB folder", "Load KB state", "Plan changes", "Any removed files?")
    w.connect("Any removed files?", "Explode removed", out=0)
    w.connect("Any removed files?", "Anything to index?", out=1)
    w.chain("Explode removed", "Pinecone: delete removed", "Forget removed", "Back to plan", "Anything to index?")
    w.connect("Anything to index?", "Explode files", out=0)
    w.connect("Anything to index?", "Run summary", out=1)
    w.connect("Explode files", "For each file")
    w.connect("For each file", "Run summary", out=0)
    w.connect("For each file", "Office file?", out=1)
    w.connect("Office file?", "Convert to Google format (temp copy)", out=0)
    w.connect("Office file?", "Prepare export", out=1)
    w.connect("Convert to Google format (temp copy)", "Prepare export", out=0)
    w.connect("Convert to Google format (temp copy)", "File failed", out=1)
    w.connect("Prepare export", "Download / export text")
    w.connect("Download / export text", "PDF?", out=0)
    w.connect("Download / export text", "File failed", out=1)
    w.connect("PDF?", "Extract PDF text", out=0)
    w.connect("PDF?", "Collect text", out=1)
    w.connect("Extract PDF text", "Collect text", out=0)
    w.connect("Extract PDF text", "File failed", out=1)
    w.connect("Collect text", "Temp copy to remove?")
    w.connect("Temp copy to remove?", "Delete temp copy", out=0)
    w.connect("Temp copy to remove?", "Split into chunks", out=1)
    w.connect("Delete temp copy", "Split into chunks")
    w.chain("Split into chunks", "Build embedding batches", "Has chunks?")
    w.connect("Has chunks?", "Create embeddings (cloud.ru)", out=0)
    w.connect("Has chunks?", "File indexed", out=1)
    w.connect("Create embeddings (cloud.ru)", "Assemble vectors", out=0)
    w.connect("Create embeddings (cloud.ru)", "File failed", out=1)
    w.connect("Assemble vectors", "Pinecone: upsert", out=0)
    w.connect("Assemble vectors", "File failed", out=1)
    w.connect("Pinecone: upsert", "File indexed", out=0)
    w.connect("Pinecone: upsert", "File failed", out=1)
    w.connect("File indexed", "Stale tail?")
    w.connect("Stale tail?", "Pinecone: delete stale tail", out=0)
    w.connect("Stale tail?", "State row", out=1)
    w.connect("Pinecone: delete stale tail", "State row")
    w.connect("File failed", "State row")
    w.connect("File failed", "Error payload")
    w.connect("Error payload", "Log via BRD-00")
    w.chain("State row", "Save file state", "Loop result", "For each file")
    w.chain("Run summary", "Log rows", "Append to KB sync log", "Notifications", "Notify via BRD-02")
    w.connect("Append to KB sync log", "Sheet error payload", out=1)
    w.connect("Sheet error payload", "Log sheet error via BRD-00")
    w.connect("Sheet error payload", "Notifications")

    w.note("""## BRD-01 — синхронизация базы знаний (Google Drive → Pinecone)
Раз в сутки (03:00 МСК) и вручную.
1. Список файлов папки `KB_FOLDER_ID` сравнивается с Data Table **kb_files** по `modifiedTime`.
2. **Удалённые** из папки файлы удаляются из Pinecone и из kb_files.
3. **Новые и изменённые** обрабатываются по одному: Google Docs → text, Google Sheets → CSV (построчно), DOCX/XLSX → временная копия в формате Google и экспорт, PDF/TXT/MD/CSV — напрямую.
4. Нарезка (≈500 токенов, перекрытие 100) → эмбеддинги cloud.ru (1536) → upsert в Pinecone с ID `<fileId>#0000`.
5. Ошибка по файлу не останавливает прогон: файл помечается `error` и обрабатывается повторно в следующий раз; старые фрагменты остаются в индексе.
6. Итог: журнал в Google Sheet `kb_sync_log`, отчёт в чат команды; при ошибках — алерт саппорту.""",
           (-40, -560), w=760, h=400, color=5)
    w.dump("BRD-01_kb-sync.json")


# ===========================================================================
# BRD-03 — агент-интервьюер в Telegram
GREETING = (
    "Здравствуйте! Я BRD-ассистент команды развития ИИ-среды. Помогу сформулировать требования к вашей задаче "
    "(вывод ИИ-модели или ИИ-сервиса в эксплуатацию) и подготовлю по ним BRD.\\n\\n"
    "Я буду задавать вопросы по одному — отвечать можно текстом или голосовым. Если чего-то не знаете, так и скажите: "
    "я зафиксирую это как открытый вопрос.\\n\\n"
    "Команды: /done — завершить и сформировать BRD, /status — статус беседы, /cancel — отменить, /new — начать заново.\\n\\n"
    "Для начала расскажите в двух словах: какую задачу вы хотите решить?"
)
HELP = (
    "Я собираю требования к задачам по выводу ИИ-моделей и ИИ-сервисов и готовлю по ним BRD.\\n\\n"
    "/new — начать новую беседу\\n/status — статус текущей беседы\\n/done — завершить интервью и сформировать BRD\\n"
    "/cancel — отменить беседу\\n/help — эта справка\\n\\nМожно писать текстом или отправлять голосовые сообщения."
)
QUALITY_REPLY = (
    "Не удалось автоматически собрать документ по стандарту команды. Черновик и ваши ответы переданы команде "
    "развития ИИ-среды — они доработают документ и пришлют ссылку. Повторять /done не нужно."
)
LIMIT_REPLY = (
    "Я остановил беседу: сработало ограничение агента. Ваши ответы сохранены, беседа передана команде развития "
    "ИИ-среды — с вами свяжутся и решат, как продолжить. Повторять сообщения не нужно."
)


def build_interview():
    w = WF("BRD-03 Interview Agent (Telegram)", timeout=LIMITS["INTERVIEW_TIMEOUT_SEC"])
    w.add("Telegram Trigger", "n8n-nodes-base.telegramTrigger", 1.2, {"updates": ["message"], "additionalFields": {}},
          (0, 200), creds=["telegram"])
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "MIN_TURNS_FOR_DONE": 4,
        "SOFT_MAX_TURNS": 30,
        "MAX_TURNS": LIMITS["MAX_TURNS"],
        "MAX_ITERATIONS": LIMITS["INTERVIEW_MAX_ITERATIONS"],  # справочно; сам потолок — в узле Interview agent
        # Scope пилота кодом: через запятую chat_id разрешённых пользователей; пусто — без ограничения (демо)
        "ALLOWED_CHAT_IDS": "__CFG_ALLOWED_CHAT_IDS__",
        "HISTORY_LIMIT": 60,
        "STT_URL": "__CFG_STT_URL__",
        "STT_MODEL": "__CFG_STT_MODEL__",
        **llm_config(),
    }, include_other=True), (220, 200))
    w.add("Parse message", "n8n-nodes-base.code", 2, code("""
const m = $json.message || {};
const from = m.from || {};
const chat = m.chat || {};
const text = String(m.text || m.caption || '').trim();
const cm = text.match(/^\\/(\\w+)/);
let kind = (m.voice || m.audio) ? 'voice' : (text ? 'text' : 'other');
if (chat.type && chat.type !== 'private') kind = 'ignore';   // бот состоит и в чатах команды — там не отвечаем
// Scope пилота ограничен кодом, а не обещанием: вне списка — отказ без распознавания голоса и без вызова LLM.
const allowRaw = String($('Config').first().json.ALLOWED_CHAT_IDS || '');
const allow = allowRaw.startsWith('__') ? [] : allowRaw.split(',').map((x) => x.trim()).filter(Boolean);
if (kind !== 'ignore' && allow.length && !allow.includes(String(chat.id ?? ''))) kind = 'forbidden';
return [{ json: {
  chat_id: String(chat.id ?? ''),
  user_id: String(from.id ?? ''),
  username: from.username ? '@' + from.username : ([from.first_name, from.last_name].filter(Boolean).join(' ') || String(from.id || '')),
  message_id: m.message_id,
  text, command: cm ? cm[1].toLowerCase() : '',
  voice_file_id: (m.voice && m.voice.file_id) || (m.audio && m.audio.file_id) || '',
  kind, from_voice: false,
} }];
"""), (440, 200))
    rule = lambda key: {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{"id": uid(), "leftValue": "={{ $json.kind }}", "rightValue": key,
                            "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"},
        "renameOutput": True, "outputKey": key}
    w.add("Message type", "n8n-nodes-base.switch", 3.2,
          {"rules": {"values": [rule("voice"), rule("text"), rule("other"), rule("forbidden")]}, "options": {}}, (660, 200))
    w.add("Get voice file", "n8n-nodes-base.telegram", 1.2, {
        "resource": "file", "fileId": "={{ $json.voice_file_id }}", "additionalFields": {},
    }, (880, 0), creds=["telegram"], onError="continueErrorOutput")
    w.add("Transcribe (cloud.ru Whisper)", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": "={{ $('Config').first().json.STT_URL }}",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "sendBody": True, "contentType": "multipart-form-data",
        "bodyParameters": {"parameters": [
            {"parameterType": "formBinaryData", "name": "file", "inputDataFieldName": "data"},
            {"name": "model", "value": "={{ $('Config').first().json.STT_MODEL }}"},
            {"name": "response_format", "value": "json"},
            {"name": "language", "value": "ru"},
        ]},
        "options": {"response": {"response": {"responseFormat": "json"}}, "timeout": 120000},
    }, (1100, 0), creds=["http_cloudru"], onError="continueErrorOutput")
    w.add("Use transcript", "n8n-nodes-base.code", 2, code("""
const msg = $('Parse message').first().json;
return [{ json: { ...msg, text: String($json.text || '').trim(), from_voice: true, kind: 'text' } }];
"""), (1320, 0))
    w.add("Voice failed", "n8n-nodes-base.code", 2, code("""
const msg = $('Parse message').first().json;
return [{ json: { chat_id: msg.chat_id, reply: 'Не получилось распознать голосовое сообщение. Пожалуйста, повторите его или напишите текстом.',
                  saves: [], start_brd: false, error: $json.error } }];
"""), (1320, 160))
    w.add("Voice error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "interview_stt", "Transcribe (cloud.ru Whisper)", severity="warning", alert=False,
        chat_expr="j.chat_id", msg_expr="'Ошибка распознавания голоса: ' + errText(j)")), (1540, 340))
    w.add("Log voice error via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False),
          (1760, 340))
    w.add("Unsupported message", "n8n-nodes-base.code", 2, code(f"""
const msg = $json;
return [{{ json: {{ chat_id: msg.chat_id, reply: 'Я понимаю текстовые и голосовые сообщения. Напишите, пожалуйста, текстом.\\n\\n{HELP}', saves: [], start_brd: false }} }}];
"""), (880, 420))
    w.add("Not in pilot", "n8n-nodes-base.code", 2, code("""
return [{ json: { chat_id: $json.chat_id, saves: [], start_brd: false,
  reply: 'Бот работает в пилоте для ограниченного круга заказчиков. Чтобы подключиться, напишите команде AIOps.' } }];
"""), (880, 600))
    w.add("Load chat sessions", "n8n-nodes-base.dataTable", 1,
          dt_get(SESSIONS_TABLE, [("chat_id", "={{ $json.chat_id }}")]), (1540, 120),
          alwaysOutputData=True, executeOnce=True)
    w.add("Route", "n8n-nodes-base.code", 2, code(f"""
// Маршрутизация: команды, активная сессия, ход агента.
const cfg = $('Config').first().json;
const msg = $('Use transcript').isExecuted ? $('Use transcript').first().json : $('Parse message').first().json;
const rows = $input.all().map((i) => i.json).filter((r) => r && r.session_id);
const open = rows.filter((r) => ['active', 'generating', 'needs_review'].includes(r.status))
  .sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)))[0] || null;
const now = new Date().toISOString();
const GREETING = '{GREETING}';
const HELP = '{HELP}';
const hist = (s) => {{ try {{ return JSON.parse(s.history || '[]'); }} catch (e) {{ return []; }} }};
const newSession = () => ({{
  session_id: `${{msg.chat_id}}-${{Date.now()}}`, chat_id: msg.chat_id, user_id: msg.user_id, username: msg.username,
  status: 'active', history: '[]', turn_count: 0, started_at: now, last_activity_at: now,
  reminder_count: 0, last_reminder_at: '', brd_title: '', brd_url: '', error: '',
  cost_usd: 0, limit_hit: '', brd_attempts: 0, brd_created_at: '',
}});
const res = {{ chat_id: msg.chat_id, username: msg.username, user_text: msg.text, reply: '', saves: [],
              run_agent: false, start_brd: false, session: null, history: [], prompt: '', limit: null }};
const cmd = msg.command;
// Потолок сработал до вызова LLM: сохраняем реплику, замораживаем сессию, отдаём в ручную очередь.
const stopByLimit = (s, h, type, value, observed) => {{
  const hh = h.concat([{{ role: 'user', content: msg.text, ts: now }}]);
  const st = {{ ...s, history: JSON.stringify(hh), status: 'needs_review', last_activity_at: now,
               limit_hit: `${{type}}: ${{observed}} / ${{value}}` }};
  res.saves.push(st); res.session = st;
  res.limit = {{ limit_type: type, limit_value: value, observed }};
  res.reply = LIMIT_REPLY;
}};
const LIMIT_REPLY = '{LIMIT_REPLY}';

if (!msg.text) {{
  res.reply = 'Не получилось разобрать сообщение. Пожалуйста, напишите текстом.';
}} else if (cmd === 'help') {{
  res.reply = HELP;
}} else if (open && open.status === 'needs_review') {{
  // Повтор после предела запрещён: новую беседу не начинаем, пока команда не разберёт очередь.
  res.reply = 'Беседа остановлена по ограничению агента и передана команде развития ИИ-среды — с вами свяжутся. ' +
              'Ваши ответы сохранены, повторять их не нужно.';
}} else if (open && open.status === 'generating') {{
  res.reply = 'BRD по вашей задаче уже формируется — пришлю ссылку, как только документ будет готов (обычно 3–5 минут).';
}} else if (cmd === 'start' || cmd === 'new') {{
  if (open) res.saves.push({{ ...open, status: 'cancelled', last_activity_at: now }});
  const s = newSession();
  s.history = JSON.stringify([{{ role: 'assistant', content: GREETING, ts: now }}]);
  res.saves.push(s);
  res.reply = (open ? 'Предыдущая беседа закрыта.\\n\\n' : '') + GREETING;
}} else if (cmd === 'cancel') {{
  if (open) {{ res.saves.push({{ ...open, status: 'cancelled', last_activity_at: now }}); res.reply = 'Беседа отменена. Начать новую — /new'; }}
  else res.reply = 'Активной беседы нет. Начать — /new';
}} else if (cmd === 'status') {{
  res.reply = open
    ? `Идёт беседа (начата ${{String(open.started_at).slice(0, 16).replace('T', ' ')}} UTC), ваших ответов: ${{open.turn_count}}.\\n/done — завершить и сформировать BRD, /cancel — отменить.`
    : 'Активной беседы нет. Начать — /new';
}} else if (cmd === 'done') {{
  if (!open) res.reply = 'Активной беседы нет. Начать — /new';
  else if (Number(open.turn_count) < Number(cfg.MIN_TURNS_FOR_DONE)) res.reply = 'Пока информации слишком мало для BRD — ответьте ещё на несколько вопросов. Отменить беседу — /cancel.';
  else {{
    const s = {{ ...open, status: 'generating', last_activity_at: now }};
    res.saves.push(s); res.session = s; res.start_brd = true;
    res.reply = 'Принято! Формирую BRD по итогам беседы — это займёт 3–5 минут. Пришлю ссылку на документ.';
  }}
}} else if (cmd) {{
  res.reply = 'Неизвестная команда.\\n\\n' + HELP;
}} else {{
  // Обычная реплика: если активной беседы нет — начинаем новую с этого сообщения.
  const s = open || newSession();
  const h = hist(s);
  const limit = Number(cfg.HISTORY_LIMIT) || 60;
  const recent = h.slice(-limit);
  const turn = Number(s.turn_count || 0) + 1;
  const spent = Number(s.cost_usd) || 0;
  if (turn > Number(cfg.MAX_TURNS)) {{
    stopByLimit(s, h, 'turns', cfg.MAX_TURNS, turn);
    return [{{ json: res }}];
  }}
  if (spent >= Number(cfg.SESSION_BUDGET_USD)) {{
    stopByLimit(s, h, 'cost_session', cfg.SESSION_BUDGET_USD, '$' + spent.toFixed(3));
    return [{{ json: res }}];
  }}
  const dialog = recent.map((m) => (m.role === 'user' ? 'Заказчик: ' : 'Интервьюер: ') + m.content).join('\\n\\n');
  res.session = s; res.history = h; res.run_agent = true;
  res.prompt = [
    `Служебно: ответ заказчика №${{turn}}; ориентир по длине интервью — ${{cfg.SOFT_MAX_TURNS}} ответов` +
      (turn >= Number(cfg.SOFT_MAX_TURNS) ? ' — ориентир достигнут, переходи к резюме и подтверждению.' : '.') +
      ` Собеседник в Telegram: ${{msg.username}}.` + (msg.from_voice ? ' Сообщение распознано из голосового — возможны ошибки распознавания.' : ''),
    'ИСТОРИЯ ДИАЛОГА:\\n' + (dialog || '(диалог только начался)') + (h.length > recent.length ? '\\n(более ранние реплики опущены)' : ''),
    'НОВОЕ СООБЩЕНИЕ ЗАКАЗЧИКА:\\n' + msg.text,
    'Ответь следующей репликой интервьюера.',
  ].join('\\n\\n');
}}
return [{{ json: res }}];
"""), (1760, 120))
    w.add("Run agent?", "n8n-nodes-base.if", 2, if_true("={{ $json.run_agent }}"), (1980, 120))
    w.add("Typing…", "n8n-nodes-base.telegram", 1.2, {
        "operation": "sendChatAction", "chatId": "={{ $json.chat_id }}", "action": "typing",
    }, (2200, 0), creds=["telegram"], onError="continueRegularOutput")
    w.add("Interview agent", "@n8n/n8n-nodes-langchain.agent", 2.2, {
        "promptType": "define",
        "text": "={{ $('Route').first().json.prompt }}",
        "options": {"systemMessage": INTERVIEW_PROMPT, "maxIterations": LIMITS["INTERVIEW_MAX_ITERATIONS"],
                    "returnIntermediateSteps": True},
    }, (2420, 0), onError="continueErrorOutput")
    openrouter_model(w, "LLM (interview)", (2300, 220), temperature=0.3, max_tokens=2000, timeout=120000)
    w.add("KnowledgeBase", "@n8n/n8n-nodes-langchain.vectorStorePinecone", 1.1, {
        "mode": "retrieve-as-tool", "toolName": "KnowledgeBase",
        "toolDescription": "Поиск во внутренней базе знаний команды ИИ-среды: регламенты вывода ИИ-сервисов в ПЭ, runbook'и online/batch, стандарты именования и упаковки MLflow, политика ИБ и классы данных, чек-лист готовности, FAQ, реестр действующих ИИ-сервисов, стандарт и примеры BRD. Запрос — короткая фраза на русском.",
        "pineconeIndex": {"__rl": True, "value": "__CFG_PINECONE_INDEX_NAME__", "mode": "id"},
        "topK": 6,
        "options": {"pineconeNamespace": "__CFG_PINECONE_NAMESPACE__"},
    }, (2480, 220), creds=["pinecone"])
    w.add("Embeddings (cloud.ru)", "@n8n/n8n-nodes-langchain.embeddingsOpenAi", 1.2, {
        "model": "__CFG_EMBEDDING_MODEL__", "options": {"dimensions": 1536},
    }, (2560, 400), creds=["openai_cloudru"])
    w.add("Process answer", "n8n-nodes-base.code", 2, code(LIMIT_JS + f"""
const r = $('Route').first().json;
const cfg = $('Config').first().json;
const raw = String($json.output ?? $json.text ?? '').trim();
const now = new Date().toISOString();
// Расход прогона (оценка сверху) копится в сессии — это бюджет сессии.
const run = estimateRun({len(INTERVIEW_PROMPT)}, String(r.prompt || '').length, raw, $json.intermediateSteps,
  Number(cfg.LLM_PRICE_IN) || 0, Number(cfg.LLM_PRICE_OUT) || 0, Number(cfg.CHARS_PER_TOKEN) || 2);
const spent = (Number(r.session.cost_usd) || 0) + run.usd;
const budget = Number(cfg.SESSION_BUDGET_USD);
const LIMIT_REPLY = '{LIMIT_REPLY}';
// Предел без исхода — тихая потеря: служебный текст агента заказчику НЕ отправляем.
let limit = null;
if (STOP_RE.test(raw)) limit = {{ limit_type: 'steps', limit_value: cfg.MAX_ITERATIONS, observed: `${{run.steps}} шагов, ответа нет` }};
else if (spent >= budget) limit = {{ limit_type: 'cost_session', limit_value: budget, observed: '$' + spent.toFixed(3) }};
if (limit) {{
  const history = r.history.concat([{{ role: 'user', content: r.user_text, ts: now }}]);
  const s = {{ ...r.session, history: JSON.stringify(history), last_activity_at: now, status: 'needs_review', cost_usd: spent,
              limit_hit: `${{limit.limit_type}}: ${{limit.observed}} / ${{limit.limit_value}}` }};
  return [{{ json: {{ chat_id: r.chat_id, reply: LIMIT_REPLY, saves: [s], session: s, start_brd: false, limit,
                     run_cost_usd: run.usd, run_steps: run.steps }} }}];
}}
const ready = raw.includes('<<BRD_READY>>');
let out = raw.replace(/<<BRD_READY>>/g, '').replace(/\\*\\*(.+?)\\*\\*/g, '$1').replace(/^#{{1,6}}\\s+/gm, '').trim();
if (!out) out = ready ? 'Спасибо!' : 'Извините, не получилось сформулировать ответ. Повторите, пожалуйста, последнее сообщение.';
const history = r.history.concat([
  {{ role: 'user', content: r.user_text, ts: now }},
  {{ role: 'assistant', content: out, ts: now }},
]);
const s = {{ ...r.session, history: JSON.stringify(history), turn_count: Number(r.session.turn_count || 0) + 1,
            last_activity_at: now, reminder_count: 0, status: ready ? 'generating' : 'active', cost_usd: spent }};
const reply = ready ? out + '\\n\\nФормирую BRD — это займёт 3–5 минут. Пришлю ссылку на документ.' : out;
return [{{ json: {{ chat_id: r.chat_id, reply, saves: [s], session: s, start_brd: ready, limit: null,
                   run_cost_usd: run.usd, run_steps: run.steps }} }}];
"""), (2860, 0))
    w.add("Agent failed", "n8n-nodes-base.code", 2, code(LIMIT_JS + f"""
// Ошибка LLM/инструментов: сохраняем реплику заказчика, чтобы не потерять.
// Исчерпан лимит ключа (402/403) или агент v3 упал по maxIterations — это предел, а не сбой: в ручную очередь.
const r = $('Route').first().json;
const cfg = $('Config').first().json;
const now = new Date().toISOString();
const e = errStr($json.error);
const history = r.history.concat([{{ role: 'user', content: r.user_text, ts: now }}]);
let limit = null;
if (MONEY_RE.test(e)) limit = {{ limit_type: 'cost_key', limit_value: 'лимит ключа OpenRouter', observed: e.slice(0, 200) }};
else if (STOP_RE.test(e)) limit = {{ limit_type: 'steps', limit_value: cfg.MAX_ITERATIONS, observed: e.slice(0, 200) }};
if (limit) {{
  const s = {{ ...r.session, history: JSON.stringify(history), last_activity_at: now, status: 'needs_review',
              limit_hit: `${{limit.limit_type}}: ${{limit.observed.slice(0, 80)}}` }};
  return [{{ json: {{ chat_id: r.chat_id, saves: [s], session: s, start_brd: false, limit, reply: '{LIMIT_REPLY}' }} }}];
}}
const s = {{ ...r.session, history: JSON.stringify(history), last_activity_at: now, status: 'active' }};
return [{{ json: {{ chat_id: r.chat_id, saves: [s], session: s, start_brd: false, error: $json.error, limit: null,
  reply: 'Извините, у меня технический сбой — ваше сообщение сохранено, команда поддержки уже уведомлена. Попробуйте повторить ответ через пару минут.' }} }}];
"""), (2860, 200))
    w.add("Real error?", "n8n-nodes-base.if", 2, if_true("={{ !$json.limit }}"), (3080, 260))
    w.add("Agent error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "interview_agent", "Interview agent", alert=True, chat_expr="j.chat_id",
        msg_expr="'Сбой агента-интервьюера: ' + errText(j)")), (3080, 380))
    w.add("Log agent error via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False),
          (3300, 380))
    w.add("Turn result", "n8n-nodes-base.noOp", 1, {}, (3300, 120))
    w.add("Has rows to save?", "n8n-nodes-base.if", 2, if_true("={{ ($json.saves || []).length > 0 }}"), (3520, 120))
    w.add("Rows to save", "n8n-nodes-base.code", 2, code("""
// Убираем служебные поля Data Table и приводим типы.
return $json.saves.map((s) => {
  const { id, createdAt, updatedAt, ...row } = s;
  row.turn_count = Number(row.turn_count) || 0;
  row.reminder_count = Number(row.reminder_count) || 0;
  row.cost_usd = Math.round((Number(row.cost_usd) || 0) * 1e6) / 1e6;
  row.brd_attempts = Number(row.brd_attempts) || 0;
  return { json: row };
});
"""), (3740, 40))
    w.add("Save session", "n8n-nodes-base.dataTable", 1, dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"),
          (3960, 40), onError="continueErrorOutput")
    w.add("Save error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "interview_state", "Save session", alert=True, chat_expr="$('Turn result').first().json.chat_id",
        msg_expr="'Не удалось сохранить сессию в Data Table: ' + errText(j)")), (4180, 260))
    w.add("Log save error via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False),
          (4400, 260))
    w.add("Reply parts", "n8n-nodes-base.code", 2, code("""
const t = $('Turn result').first().json;
const MAX = 3900;
let s = String(t.reply || '').trim() || '…';
const parts = [];
while (s.length > MAX) {
  let c = s.lastIndexOf('\\n', MAX);
  if (c < MAX * 0.5) c = s.lastIndexOf(' ', MAX);
  if (c < MAX * 0.5) c = MAX;
  parts.push(s.slice(0, c)); s = s.slice(c).replace(/^\\s+/, '');
}
parts.push(s);
// parse_mode=HTML: экранируем, чтобы _ * [ в ответах агента не ломали отправку
const esc = (x) => String(x).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
return parts.map((p) => ({ json: { chat_id: t.chat_id, text: esc(p) } }));
"""), (4180, 120), executeOnce=True)
    w.add("Send reply", "n8n-nodes-base.telegram", 1.2, {
        "chatId": "={{ $json.chat_id }}", "text": "={{ $json.text }}",
        "additionalFields": {"appendAttribution": False, "disable_web_page_preview": True, "parse_mode": "HTML"},
    }, (4400, 120), creds=["telegram"], onError="continueErrorOutput")
    w.add("Send error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "interview_reply", "Send reply", alert=True, chat_expr="$('Turn result').first().json.chat_id",
        msg_expr="'Не удалось отправить ответ заказчику в Telegram: ' + errText(j)")), (4620, 300))
    w.add("Log send error via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False),
          (4840, 300))
    w.add("Start BRD?", "n8n-nodes-base.if", 2, if_true("={{ $('Turn result').first().json.start_brd === true }}"),
          (4620, 120), executeOnce=True)
    w.add("BRD job", "n8n-nodes-base.code", 2, code("""
const t = $('Turn result').first().json;
return [{ json: { session_id: t.session.session_id, chat_id: t.chat_id } }];
"""), (4840, 40))
    w.add("Start BRD-04 (async)", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_BRD_GENERATOR, wait=False),
          (5060, 40))
    mark_outcome(w, "Mark outcome", "={{ $json.limit ? 'limit_stopped' : ($json.error ? 'error' : 'done') }}",
                 (3520, -120))
    w.add("Limit hit?", "n8n-nodes-base.if", 2, if_true("={{ !!$json.limit }}"), (3740, -120))
    w.add("Queue payload", "n8n-nodes-base.code", 2, code("""
const t = $json;
return [{ json: { ...t.limit, workflow: $workflow.name, workflow_id: $workflow.id, execution_id: $execution.id,
  session_id: t.session?.session_id || '', chat_id: t.chat_id, username: t.session?.username || '',
  details: `Сессия заморожена (needs_review), ответов: ${t.session?.turn_count ?? '—'}, расход (оценка): $${(Number(t.session?.cost_usd) || 0).toFixed(3)}` } }];
"""), (3960, -200))
    w.add("Escalate via BRD-07", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_QUEUE, wait=False), (4180, -200))

    w.chain("Telegram Trigger", "Config", "Parse message", "Message type")
    w.connect("Message type", "Get voice file", out=0)
    w.connect("Message type", "Load chat sessions", out=1)
    w.connect("Message type", "Unsupported message", out=2)
    w.connect("Message type", "Not in pilot", out=3)
    w.connect("Not in pilot", "Turn result")
    w.connect("Get voice file", "Transcribe (cloud.ru Whisper)", out=0)
    w.connect("Get voice file", "Voice failed", out=1)
    w.connect("Transcribe (cloud.ru Whisper)", "Use transcript", out=0)
    w.connect("Transcribe (cloud.ru Whisper)", "Voice failed", out=1)
    w.connect("Use transcript", "Load chat sessions")
    w.connect("Voice failed", "Turn result")
    w.connect("Voice failed", "Voice error payload")
    w.connect("Voice error payload", "Log voice error via BRD-00")
    w.connect("Unsupported message", "Turn result")
    w.chain("Load chat sessions", "Route", "Run agent?")
    w.connect("Run agent?", "Typing…", out=0)
    w.connect("Run agent?", "Turn result", out=1)
    w.connect("Typing…", "Interview agent")
    w.connect("LLM (interview)", "Interview agent", kind="ai_languageModel")
    w.connect("KnowledgeBase", "Interview agent", kind="ai_tool")
    w.connect("Embeddings (cloud.ru)", "KnowledgeBase", kind="ai_embedding")
    w.connect("Interview agent", "Process answer", out=0)
    w.connect("Interview agent", "Agent failed", out=1)
    w.connect("Process answer", "Turn result")
    w.connect("Agent failed", "Turn result")
    w.connect("Agent failed", "Real error?")
    w.connect("Real error?", "Agent error payload", out=0)
    w.connect("Agent error payload", "Log agent error via BRD-00")
    w.connect("Turn result", "Has rows to save?")
    w.connect("Turn result", "Mark outcome")
    w.chain("Mark outcome", "Limit hit?", "Queue payload", "Escalate via BRD-07")
    w.connect("Has rows to save?", "Rows to save", out=0)
    w.connect("Has rows to save?", "Reply parts", out=1)
    w.connect("Rows to save", "Save session")
    w.connect("Save session", "Reply parts", out=0)
    w.connect("Save session", "Save error payload", out=1)
    w.connect("Save error payload", "Log save error via BRD-00")
    w.connect("Save error payload", "Reply parts")
    w.connect("Reply parts", "Send reply")
    w.connect("Send reply", "Start BRD?", out=0)
    w.connect("Send reply", "Send error payload", out=1)
    w.connect("Send error payload", "Log send error via BRD-00")
    w.connect("Start BRD?", "BRD job", out=0)
    w.connect("BRD job", "Start BRD-04 (async)")

    w.note(f"""## BRD-03 — агент-интервьюер (Telegram)
Заказчик пишет боту в личку (текст или голос → Whisper cloud.ru).
**Состояние** беседы — Data Table **brd_sessions** (история JSON, статус, счётчики).
**Команды:** /start, /new, /status, /done, /cancel, /help. Сообщения в групповых чатах игнорируются.

**Агент** (OpenRouter + инструмент KnowledgeBase → Pinecone) ведёт интервью по правилам скилла mlops-deployment-intake-v2: один вопрос за ход, без приписанных оценок, данные для ИБ только собираются.
Когда заказчик подтверждает резюме, агент ставит маркер `<<BRD_READY>>` → статус `generating` → асинхронный запуск **BRD-04**.

**Control layer** (числа — docs/control-layer.md):
• шаги: `maxIterations = {LIMITS["INTERVIEW_MAX_ITERATIONS"]}` в узле Interview agent; текст «Agent stopped…» заказчику не уходит;
• длина интервью: > {LIMITS["MAX_TURNS"]} ответов → стоп;
• деньги: бюджет сессии ${LIMITS["SESSION_BUDGET_USD"]} (оценка в `cost_usd`) + лимит ключа OpenRouter (402/403);
• время: Settings → Timeout {LIMITS["INTERVIEW_TIMEOUT_SEC"]} с.
Исход любого потолка: сессия `needs_review`, заказчику — сообщение, прогон → **BRD-07** (ручная очередь). Перезапуска нет. В executions: `outcome = done | limit_stopped | error`.

Ошибки (STT, LLM, Data Table, Telegram) → BRD-00 (лист errors + алерт саппорту); заказчик получает вежливое сообщение.""",
           (-40, -560), w=760, h=500, color=6)
    w.dump("BRD-03_interview-agent.json")


# ===========================================================================
# BRD-04 — генерация BRD и публикация в Google Drive
def build_brd_generator():
    w = WF("BRD-04 BRD Generator & Publisher")  # без executionTimeout: максимум тарифа (3 мин) короче нормы
    w.add("When called by BRD-03", "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"inputSource": "passthrough"}, (0, 0))
    w.add("Manual test", "n8n-nodes-base.manualTrigger", 1, {}, (0, 200))
    w.add("Test input", "n8n-nodes-base.set", 3.4, p_set({"session_id": "PASTE_SESSION_ID_FROM_brd_sessions"}),
          (220, 200))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "BRD_FOLDER_ID": "__CFG_BRD_FOLDER_ID__",
        "LOGS_SHEET_ID": "__CFG_LOGS_SHEET_ID__",
        "BRD_REGISTRY_TAB": "brd_registry",
        "RUNS_TAB": "brd_runs",
        "N8N_BASE_URL": "__CFG_N8N_BASE_URL__",
        "MAX_ITERATIONS": LIMITS["BRD_MAX_ITERATIONS"],  # справочно; сам потолок — в узле BRD writer
        "MAX_REPAIRS": LIMITS["BRD_REPAIR_ATTEMPTS"],     # справочно; одно исправление заложено в саму цепочку
        # модель на шаг: писатель — сильная (CFG_WRITER_*), исправление — лёгкая (CFG_LLM_*)
        "WRITER_MODEL": "__CFG_WRITER_MODEL__",
        "WRITER_PRICE_IN": "__CFG_WRITER_PRICE_IN__",
        "WRITER_PRICE_OUT": "__CFG_WRITER_PRICE_OUT__",
        "BRD_ATTEMPTS_PER_SESSION": LIMITS["BRD_ATTEMPTS_PER_SESSION"],
        "BRD_PER_CHAT_PER_DAY": LIMITS["BRD_PER_CHAT_PER_DAY"],
        "BRD_PER_DAY": LIMITS["BRD_PER_DAY"],
        "BRD_EVAL_PER_DAY": LIMITS["BRD_EVAL_PER_DAY"],
        **llm_config(),
    }, include_other=True), (440, 0))
    w.add("Load session", "n8n-nodes-base.dataTable", 1,
          dt_get(SESSIONS_TABLE, [("session_id", "={{ $json.session_id }}")]), (660, 0),
          alwaysOutputData=True, executeOnce=True)
    w.add("Load all sessions", "n8n-nodes-base.dataTable", 1, dt_get(SESSIONS_TABLE), (880, 0),
          alwaysOutputData=True, executeOnce=True)
    w.add("Check limits", "n8n-nodes-base.code", 2, code("""
// Потолки до генерации: попытки на сессию, число BRD за 24 ч (на заказчика и всего), бюджет сессии.
const cfg = $('Config').first().json;
const s = $('Load session').all().map((i) => i.json).find((r) => r && r.session_id);
if (!s) throw new Error('Сессия не найдена: ' + cfg.session_id);
const all = $input.all().map((i) => i.json).filter((r) => r && r.session_id);
const since = Date.now() - 24 * 3600 * 1000;
// Тестовые (eval-*) и боевые BRD — раздельные потолки: тестовая серия не съедает лимит настоящих заказчиков.
const isEval = (r) => String(r.chat_id || '').startsWith('eval-');
const evalRun = isEval(s);
const dayCap = Number(evalRun ? cfg.BRD_EVAL_PER_DAY : cfg.BRD_PER_DAY);
const recent = all.filter((r) => r.brd_created_at && new Date(r.brd_created_at).getTime() >= since && isEval(r) === evalRun);
const byChat = recent.filter((r) => String(r.chat_id) === String(s.chat_id)).length;
const attempt = (Number(s.brd_attempts) || 0) + 1;
const spent = Number(s.cost_usd) || 0;
let limit = null;
if (attempt > Number(cfg.BRD_ATTEMPTS_PER_SESSION)) limit = { limit_type: 'brd_attempts', limit_value: cfg.BRD_ATTEMPTS_PER_SESSION, observed: attempt };
else if (byChat >= Number(cfg.BRD_PER_CHAT_PER_DAY)) limit = { limit_type: 'artifacts_chat', limit_value: cfg.BRD_PER_CHAT_PER_DAY, observed: byChat + 1 };
else if (recent.length >= dayCap) limit = { limit_type: 'artifacts_day', limit_value: `${dayCap} (${evalRun ? 'тестовые' : 'боевые'})`, observed: recent.length + 1 };
else if (spent >= Number(cfg.SESSION_BUDGET_USD)) limit = { limit_type: 'cost_session', limit_value: cfg.SESSION_BUDGET_USD, observed: '$' + spent.toFixed(3) };
return [{ json: { ok: !limit, limit, session: s, attempt } }];
"""), (1100, 0), onError="continueErrorOutput")
    w.add("Within limits?", "n8n-nodes-base.if", 2, if_true("={{ $json.ok }}"), (1320, 0))
    w.add("Attempt row", "n8n-nodes-base.code", 2, code("""
// Попытку фиксируем ДО вызова LLM: даже если прогон упадёт по таймауту, повтор будет посчитан.
return [{ json: { session_id: $json.session.session_id, brd_attempts: $json.attempt } }];
"""), (1540, -100))
    w.add("Record attempt", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (1760, -100))
    # Цена прогона = разница расхода ключа OpenRouter до и после (факт провайдера, все шаги паттерна).
    w.add("Key usage (before)", "n8n-nodes-base.httpRequest", 4.2,
          http_json("GET", "https://openrouter.ai/api/v1/key"), (1980, -100), creds=["http_openrouter"],
          alwaysOutputData=True, executeOnce=True, onError="continueRegularOutput")
    w.add("Saved draft?", "n8n-nodes-base.if", 2,
          if_true("={{ !!$('Check limits').first().json.session.brd_draft }}"), (2200, -100))
    w.add("Draft to publish", "n8n-nodes-base.code", 2, code("""
// Прошлая попытка составила BRD, но не смогла его опубликовать — публикуем сохранённый текст без генерации.
const s = $('Check limits').first().json.session;
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' });
return [{ json: { md: s.brd_draft, title: s.brd_draft_title || 'Без названия', session: s, today,
  turns: Number(s.turn_count) || 0, cost_usd: Number(s.cost_usd) || 0, from_draft: true, valid: true, violations: [] } }];
"""), (2420, -300))
    tpl_js = json.dumps(BRD_TEMPLATE, ensure_ascii=False)
    w.dx = 1320  # основной конвейер — правее проверки потолков
    w.add("Build BRD request", "n8n-nodes-base.code", 2, code(f"""
const s = $('Check limits').first().json.session;
let history = [];
try {{ history = JSON.parse(s.history || '[]'); }} catch (e) {{ throw new Error('Повреждена история сессии ' + s.session_id); }}
const transcript = history.map((m) => (m.role === 'user' ? 'ЗАКАЗЧИК: ' : 'ИНТЕРВЬЮЕР: ') + m.content).join('\\n\\n');
const today = new Date().toLocaleDateString('sv-SE', {{ timeZone: 'Europe/Moscow' }});
const TEMPLATE = {tpl_js};
const prompt = [
  'ВХОДНЫЕ МЕТАДАННЫЕ:',
  `- Дата создания: ${{today}}`,
  `- Автор: BRD-агент (n8n) по итогам интервью с ${{s.username || s.chat_id}}`,
  `- Инициатор (Telegram): ${{s.username || s.chat_id}}`,
  `- Session ID: ${{s.session_id}}`,
  '',
  'ШАБЛОН BRD:',
  TEMPLATE,
  '',
  'ТРАНСКРИПТ ИНТЕРВЬЮ:',
  transcript,
  '',
  'Составь полный BRD по шаблону.',
].join('\\n');
// BRD длиннее лимита ответа DeepSeek (8000 токенов), поэтому пишем его в три приёма; агент обрабатывает items по очереди.
const PARTS = [
  {{ part: 1, label: '0–5', hint: 'Сейчас напиши ТОЛЬКО разделы 0–5 (начни со строки `# BRD: <название>`).' }},
  {{ part: 2, label: '6–7', hint: 'Сейчас напиши ТОЛЬКО разделы 6 и 7 (начни с `## 6.`).' }},
  {{ part: 3, label: '8–12', hint: 'Сейчас напиши ТОЛЬКО разделы 8–12 (начни с `## 8.`).' }},
];
return PARTS.map((p) => ({{ json: {{ session: s, today, turns: Number(s.turn_count) || 0, part: p.part, transcript,
  prompt: prompt + '\\n\\nЧАСТЬ ' + p.part + ' из 3 (разделы ' + p.label + '). ' + p.hint }} }}));
"""), (880, 0), onError="continueErrorOutput")
    w.add("BRD writer", "@n8n/n8n-nodes-langchain.agent", 2.2, {
        "promptType": "define", "text": "={{ $json.prompt }}",
        "options": {"systemMessage": BRD_WRITER_PROMPT, "maxIterations": LIMITS["BRD_MAX_ITERATIONS"],
                    "returnIntermediateSteps": True},
    }, (1100, 0), onError="continueErrorOutput", retryOnFail=True, maxTries=2, waitBetweenTries=5000)  # runs once per part
    # retryOnFail — повтор только после СБОЯ (v2.x при maxIterations не падает, а возвращает текст — его ловит Validate BRD).
    openrouter_model(w, "LLM (writer)", (980, 220), temperature=0.2, max_tokens=8000, timeout=300000,
                     model="__CFG_WRITER_MODEL__")
    w.add("KnowledgeBase", "@n8n/n8n-nodes-langchain.vectorStorePinecone", 1.1, {
        "mode": "retrieve-as-tool", "toolName": "KnowledgeBase",
        "toolDescription": "Поиск во внутренней базе знаний: регламенты вывода ИИ-сервисов в ПЭ, runbook'и, стандарты, политика ИБ, чек-лист готовности, FAQ, реестр ИИ-сервисов, стандарт BRD. Используй, чтобы сослаться на применимые требования.",
        "pineconeIndex": {"__rl": True, "value": "__CFG_PINECONE_INDEX_NAME__", "mode": "id"},
        "topK": 6,
        "options": {"pineconeNamespace": "__CFG_PINECONE_NAMESPACE__"},
    }, (1160, 220), creds=["pinecone"])
    w.add("Embeddings (cloud.ru)", "@n8n/n8n-nodes-langchain.embeddingsOpenAi", 1.2, {
        "model": "__CFG_EMBEDDING_MODEL__", "options": {"dimensions": 1536},
    }, (1240, 400), creds=["openai_cloudru"])
    w.add("Validate BRD", "n8n-nodes-base.code", 2, code(LIMIT_JS + f"""
const req = $('Build BRD request').first().json;
const cfg = $('Config').first().json;
// Склеиваем три части (разделы 0–5, 6–7, 8–12). Если какая-то часть упала, её обработает ветка ошибки.
const items = $input.all();
const outs = items.map((i) => String(i.json.output ?? i.json.text ?? '').trim());
if (outs.length < 3) return [];
// Расход трёх прогонов (оценка сверху) и проверка потолка шагов по каждой части.
const prompts = $('Build BRD request').all().map((i) => String(i.json.prompt || '').length);
let usd = 0, stopped = null;
items.forEach((it, k) => {{
  const run = estimateRun({len(BRD_WRITER_PROMPT)}, prompts[k] || 0, outs[k], it.json.intermediateSteps,
    Number(cfg.WRITER_PRICE_IN) || 0, Number(cfg.WRITER_PRICE_OUT) || 0, Number(cfg.CHARS_PER_TOKEN) || 2);
  usd += run.usd;
  if (!stopped && STOP_RE.test(outs[k])) stopped = {{ part: k + 1, steps: run.steps }};
}});
const cost_usd = (Number(req.session.cost_usd) || 0) + usd;
if (stopped) return [{{ json: {{ valid: false, session: req.session, cost_usd,
  limit: {{ limit_type: 'steps', limit_value: cfg.MAX_ITERATIONS, observed: `часть ${{stopped.part}} из 3: ${{stopped.steps}} шагов, текста нет` }} }} }}];
const clean = (t, k) => {{
  t = t.replace(/^```(?:markdown|md)?\\s*\\n/i, '').replace(/\\n```\\s*$/, '').trim();
  if (k > 0) t = t.replace(/^#\\s+BRD:.*\\n+/, '');
  return t;
}};
let md = outs.map(clean).join('\\n\\n');
// Какие запросы к базе знаний писатель решил сделать сам — по частям (доказательство «инструмент выбирает агент»).
const q = (st) => {{ const t = st?.action?.toolInput; return typeof t === 'string' ? t : (t?.input ?? t?.query ?? JSON.stringify(t ?? '')); }};
const kbSteps = items.flatMap((it, k) => (it.json.intermediateSteps || []).map((st) => `ч${{k + 1}}: ${{q(st)}}`));
let title = (md.match(/^#\\s+BRD:\\s*(.+)$/m) || [])[1] || '';
if (!title) title = ((md.match(/\\|\\s*Название задачи\\/проекта\\s*\\|\\s*([^|]+)\\|/) || [])[1] || '').trim();
title = (title || 'Без названия').replace(/[\\\\/:*?"<>|]/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 90);
// Нет строки «# BRD: …» — это форматирование, а не содержание: дописываем кодом, модель не зовём.
if (!/^#\\s+BRD:/m.test(splitBrd(md).preamble)) md = `# BRD: ${{title}}\\n\\n` + md;
const violations = validateBrd(md, req.transcript);
const valid = violations.length === 0;
return [{{ json: {{ valid, md, title, violations, first_violations: violations, cost_usd, limit: null,
  kb_calls: kbSteps.length, kb_queries: kbSteps.join(' | ').slice(0, 1500), repaired: false,
  error: valid ? '' : 'BRD не прошёл проверку: ' + violations.map((x) => x.msg).join('; '),
  session: req.session, transcript: req.transcript, today: req.today, turns: req.turns }} }}];
""".replace("const req = $('Build BRD request')", VALIDATOR_JS + "\nconst req = $('Build BRD request')", 1)), (1540, 0))
    w.add("Stopped by limit?", "n8n-nodes-base.if", 2, if_true("={{ !!$json.limit }}"), (1760, 100))

    # ---- Ветка «плохой ответ модели» (качество): петля до MAX_REPAIRS раундов, повтор безопасен (класс 1).
    w.add("Quality gate", "n8n-nodes-base.code", 2, code("""
// Решение по документу после каждой проверки: публиковать / ещё раунд исправления / отдать человеку.
const j = $json;
const cfg = $('Config').first().json;
const round = Number(j.round) || 0;
const next = j.valid ? 'publish' : (round >= Number(cfg.MAX_REPAIRS) ? 'give_up' : 'repair');
const out = { ...j, round, next };
if (next === 'give_up') out.limit = { limit_type: 'validator', limit_value: `${cfg.MAX_REPAIRS} раунда исправления`,
  observed: j.violations.map((x) => `р.${x.section} ${x.rule}`).join(', ').slice(0, 250) };
return [{ json: out }];
"""), (1980, 100))
    w.add("Route by quality", "n8n-nodes-base.switch", 3.2, switch_on("next", ["publish", "repair", "give_up"]), (2200, 100))
    w.add("Repair plan", "n8n-nodes-base.code", 2, code("""
// Пропал целый раздел — перегенерируем всю его часть писателем (сильная модель + база знаний);
// иначе — точечное исправление разделов с нарушениями (лёгкая модель).
const j = $json;
const PARTS = [{ part: 1, from: 0, to: 5 }, { part: 2, from: 6, to: 7 }, { part: 3, from: 8, to: 12 }];
const missing = j.violations.filter((v) => v.rule === 'section_missing').map((v) => v.section);
const regen_parts = PARTS.filter((p) => missing.some((n) => n >= p.from && n <= p.to)).map((p) => p.part);
const round = (Number(j.round) || 0) + 1;
const action = regen_parts.length ? 'regenerate' : 'fix';
const note = action === 'regenerate' ? `р${round}: перегенерация части ${regen_parts.join(', ')}`
  : `р${round}: исправление разделов ${[...new Set(j.violations.map((v) => v.section))].join(', ')}`;
return [{ json: { ...j, round, action, regen_parts, actions: [...(j.actions || []), note] } }];
"""), (2420, 200))
    w.add("Regenerate parts?", "n8n-nodes-base.if", 2, if_true("={{ $json.action === 'regenerate' }}"), (2640, 200))
    w.add("Regen requests", "n8n-nodes-base.code", 2, code("""
// Писателю — исходный запрос части + текст ошибки проверки (как просил ревьювер: «не хватает разделов …»).
const j = $json;
const req = $('Build BRD request').all().map((i) => i.json);
const errs = j.violations.map((v) => `- раздел ${v.section}: ${v.msg}`).join('\\n');
return j.regen_parts.map((p) => ({ json: { part: p, prompt: req[p - 1].prompt +
  '\\n\\nПРЕДЫДУЩИЙ ВАРИАНТ ДОКУМЕНТА НЕ ПРОШЁЛ АВТОМАТИЧЕСКУЮ ПРОВЕРКУ СТАНДАРТА:\\n' + errs +
  '\\nНапиши эту часть заново целиком, со всеми её разделами.' } }));
"""), (2860, 120))
    w.add("BRD writer (regen)", "@n8n/n8n-nodes-langchain.agent", 2.2, {
        "promptType": "define", "text": "={{ $json.prompt }}",
        "options": {"systemMessage": BRD_WRITER_PROMPT, "maxIterations": LIMITS["BRD_MAX_ITERATIONS"],
                    "returnIntermediateSteps": True},
    }, (3080, 120), onError="continueErrorOutput", retryOnFail=True, maxTries=2, waitBetweenTries=5000)
    openrouter_model(w, "LLM (writer, regen)", (2960, 340), temperature=0.2, max_tokens=8000, timeout=300000,
                     model="__CFG_WRITER_MODEL__")
    w.add("KnowledgeBase (regen)", "@n8n/n8n-nodes-langchain.vectorStorePinecone", 1.1, {
        "mode": "retrieve-as-tool", "toolName": "KnowledgeBase",
        "toolDescription": "Поиск во внутренней базе знаний: регламенты вывода ИИ-сервисов в ПЭ, runbook'и, стандарты, политика ИБ, чек-лист готовности, FAQ, реестр ИИ-сервисов, стандарт BRD. Используй, чтобы сослаться на применимые требования.",
        "pineconeIndex": {"__rl": True, "value": "__CFG_PINECONE_INDEX_NAME__", "mode": "id"},
        "topK": 6,
        "options": {"pineconeNamespace": "__CFG_PINECONE_NAMESPACE__"},
    }, (3140, 340), creds=["pinecone"])
    w.add("Embeddings (regen)", "@n8n/n8n-nodes-langchain.embeddingsOpenAi", 1.2, {
        "model": "__CFG_EMBEDDING_MODEL__", "options": {"dimensions": 1536},
    }, (3220, 520), creds=["openai_cloudru"])
    w.add("Merge regen", "n8n-nodes-base.code", 2, code(VALIDATOR_JS + "\n" + LIMIT_JS + f"""
// Вклеиваем перегенерированные части (только разделы своей части) в документ.
const j = $('Repair plan').first().json;
const cfg = $('Config').first().json;
const req = $('Build BRD request').all().map((i) => i.json);
const RANGE = {{ 1: [0, 5], 2: [6, 7], 3: [8, 12] }};
const doc = splitBrd(j.md);
let usd = 0;
$input.all().forEach((it, k) => {{
  const part = j.regen_parts[k];
  const t = String(it.json.output ?? it.json.text ?? '').replace(/^```(?:markdown|md)?\\s*\\n/i, '').replace(/\\n```\\s*$/, '').trim();
  usd += estimateRun({len(BRD_WRITER_PROMPT)}, String(req[part - 1]?.prompt || '').length, t, it.json.intermediateSteps,
    Number(cfg.WRITER_PRICE_IN) || 0, Number(cfg.WRITER_PRICE_OUT) || 0, Number(cfg.CHARS_PER_TOKEN) || 2).usd;
  if (STOP_RE.test(t)) return;  // писатель упёрся в maxIterations — часть не заменяем, проверка это покажет
  const got = splitBrd(t);
  for (const [n, sec] of Object.entries(got.secs)) if (n >= RANGE[part][0] && n <= RANGE[part][1]) doc.secs[n] = sec;
  if (part === 1 && /^#\\s+BRD:/m.test(got.preamble)) doc.preamble = got.preamble;
}});
return [{{ json: {{ ...j, md: joinBrd(doc.preamble, doc.secs), cost_usd: (Number(j.cost_usd) || 0) + usd }} }}];
"""), (3300, 120))
    # Точечное исправление (лёгкая модель, без инструментов).
    tpl_secs_js = json.dumps(BRD_TEMPLATE, ensure_ascii=False)
    w.add("Repair request", "n8n-nodes-base.code", 2, code(VALIDATOR_JS + f"""
const j = $json;
const TEMPLATE = {tpl_secs_js};
const tpl = splitBrd(TEMPLATE).secs;
const {{ secs }} = splitBrd(j.md);
const nums = [...new Set(j.violations.map((x) => x.section))].sort((a, b) => a - b);
// Фигурные скобки — переменные шаблона промпта LangChain; в данных их заменяем.
const safe = (t) => String(t || '').replace(/[{{}}]/g, (c) => (c === '{{' ? '(' : ')'));
const tbdLines = j.md.split('\\n').filter((l) => /\\bTBD\\b/.test(l)).slice(0, 40).join('\\n');
const prompt = [
  'НАРУШЕНИЯ СТАНДАРТА (исправь их):', ...j.violations.map((x) => `- раздел ${{x.section}}: ${{x.msg}}`), '',
  'РАЗДЕЛЫ ДЛЯ ИСПРАВЛЕНИЯ: ' + nums.join(', '), '',
  'ШАБЛОН ЭТИХ РАЗДЕЛОВ:', ...nums.map((n) => tpl[n] || `## ${{n}}.`), '',
  'ТЕКУЩИЙ ТЕКСТ ЭТИХ РАЗДЕЛОВ:', ...nums.map((n) => secs[n] || `(раздела ${{n}} нет — напиши его по шаблону и транскрипту)`), '',
  nums.includes(11) ? 'СТРОКИ ДОКУМЕНТА С TBD (для раздела 11):\\n' + tbdLines + '\\n' : '',
  'ТРАНСКРИПТ ИНТЕРВЬЮ:', j.transcript,
].join('\\n');
return [{{ json: {{ ...j, fix_sections: nums, fix_prompt: safe(prompt) }} }}];
"""), (2860, 420))
    w.add("BRD fixer", "@n8n/n8n-nodes-langchain.chainLlm", 1.7, {
        "promptType": "define", "text": "={{ $json.fix_prompt }}",
        "messages": {"messageValues": [{"type": "SystemMessagePromptTemplate",
                                        "message": BRD_FIXER_PROMPT.replace("{", "(").replace("}", ")")}]},
    }, (3080, 420), onError="continueErrorOutput")
    openrouter_model(w, "LLM (fixer)", (3080, 640), temperature=0.1, max_tokens=8000, timeout=180000)
    w.add("Apply fixes", "n8n-nodes-base.code", 2, code(VALIDATOR_JS + "\n" + LIMIT_JS + f"""
// Вклеиваем исправленные разделы по номерам.
const j = $('Repair request').first().json;
const cfg = $('Config').first().json;
const out = String($json.text ?? $json.output ?? '').replace(/^```(?:markdown|md)?\\s*\\n/i, '').replace(/\\n```\\s*$/, '').trim();
const fixed = splitBrd(out).secs;
const doc = splitBrd(j.md);
for (const n of j.fix_sections) if (fixed[n]) doc.secs[n] = fixed[n];
const run = estimateRun({len(BRD_FIXER_PROMPT)}, String(j.fix_prompt).length, out, [],
  Number(cfg.LLM_PRICE_IN) || 0, Number(cfg.LLM_PRICE_OUT) || 0, Number(cfg.CHARS_PER_TOKEN) || 2);
const {{ fix_prompt, ...rest }} = j;
return [{{ json: {{ ...rest, md: joinBrd(doc.preamble, doc.secs), cost_usd: (Number(j.cost_usd) || 0) + run.usd }} }}];
"""), (3300, 420))
    w.add("Revalidate", "n8n-nodes-base.code", 2, code(VALIDATOR_JS + """
// Проверяем ВЕСЬ документ тем же валидатором после каждого раунда и возвращаемся в Quality gate.
const j = $json;
let md = j.md;
if (!/^#\\s+BRD:/m.test(splitBrd(md).preamble)) md = `# BRD: ${j.title}\\n\\n` + md;  // заголовок — форматирование, правим кодом
const violations = validateBrd(md, j.transcript);
return [{ json: { ...j, md, violations, valid: violations.length === 0 } }];
"""), (3520, 260))

    # ---- Публикация (класс 3: создаёт документ). Без слепого повтора: перед повтором ищем документ по appProperties.
    w.add("Render Google Doc request", "n8n-nodes-base.code", 2, code(f"""
{MD2HTML_JS}

const j = $json;
const cfg = $('Config').first().json;
const who = String(j.session.username || j.session.chat_id).replace(/^@/, '');
const name = `BRD ${{j.today}} — ${{j.title}} (${{who}})`;
const html = md2html(j.md);
// Ключ документа: сессия + номер попытки генерации. По нему перед повтором ищем, не создан ли документ уже.
const brd_key = `${{j.session.session_id}}#${{$('Check limits').first().json.attempt}}`;
const boundary = 'brd_' + Date.now().toString(36);
const meta = {{ name, mimeType: 'application/vnd.google-apps.document', parents: [cfg.BRD_FOLDER_ID],
               appProperties: {{ brd_key }},
               description: `BRD по интервью в Telegram. Session ID: ${{j.session.session_id}}` }};
const body = `--${{boundary}}\\r\\nContent-Type: application/json; charset=UTF-8\\r\\n\\r\\n${{JSON.stringify(meta)}}\\r\\n` +
             `--${{boundary}}\\r\\nContent-Type: text/html; charset=UTF-8\\r\\n\\r\\n${{html}}\\r\\n--${{boundary}}--`;
return [{{ json: {{ ...j, brd_key, doc_name: name, body, content_type: `multipart/related; boundary=${{boundary}}` }} }}];
"""), (2420, -160))
    upload = lambda: {
        "method": "POST",
        "url": "https://www.googleapis.com/upload/drive/v3/files",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleDriveOAuth2Api",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "uploadType", "value": "multipart"},
            {"name": "supportsAllDrives", "value": "true"},
            {"name": "fields", "value": "id,name,webViewLink"},
        ]},
        "sendBody": True,
        "contentType": "raw",
        "rawContentType": "={{ $('Render Google Doc request').first().json.content_type }}",
        "body": "={{ $('Render Google Doc request').first().json.body }}",
        "options": {"timeout": 120000},
    }
    w.add("Upload to Drive as Google Doc", "n8n-nodes-base.httpRequest", 4.2, upload(),
          (2640, -160), creds=["gdrive"], onError="continueErrorOutput")  # без retryOnFail: создание документа — класс 3
    w.add("Find uploaded doc", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "GET",
        "url": "https://www.googleapis.com/drive/v3/files",
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleDriveOAuth2Api",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "q", "value": "={{ \"appProperties has { key='brd_key' and value='\" + $('Render Google Doc request').first().json.brd_key + \"' } and trashed = false\" }}"},
            {"name": "fields", "value": "files(id,name,webViewLink)"},
            {"name": "supportsAllDrives", "value": "true"},
            {"name": "includeItemsFromAllDrives", "value": "true"},
        ]},
        "options": {"timeout": 30000},
    }, (2860, -40), creds=["gdrive"], onError="continueErrorOutput")
    w.add("Doc already created?", "n8n-nodes-base.if", 2, if_true("={{ ($json.files || []).length > 0 }}"), (3080, -40))
    w.add("Use found doc", "n8n-nodes-base.code", 2, code("""
// Загрузка вернула ошибку, но документ создан (например, ответ потерян по таймауту) — публикуем найденный, дубль не создаём.
const f = $json.files[0];
return [{ json: { id: f.id, webViewLink: f.webViewLink, found_existing: true } }];
"""), (3300, -120))
    w.add("Wait before retry (10 s)", "n8n-nodes-base.wait", 1.1, {"resume": "timeInterval", "amount": 10, "unit": "seconds"},
          (3300, 40))
    w.add("Retry upload (once)", "n8n-nodes-base.httpRequest", 4.2, upload(), (3520, 40), creds=["gdrive"],
          onError="continueErrorOutput")
    w.add("Published", "n8n-nodes-base.code", 2, code("""
const j = $('Render Google Doc request').first().json;
const url = $json.webViewLink || `https://docs.google.com/document/d/${$json.id}/edit`;
const now = new Date().toISOString();
return [{ json: {
  doc_id: $json.id, doc_url: url, doc_name: j.doc_name, title: j.title, session: j.session, turns: j.turns,
  from_draft: !!j.from_draft, found_existing: !!$json.found_existing,
  registry: { created_at: now, title: j.title, customer: j.session.username || '', chat_id: j.session.chat_id,
              session_id: j.session.session_id, turns: j.turns, status: 'Draft', doc_url: url },
  session_row: { session_id: j.session.session_id, status: 'completed', brd_title: j.title, brd_url: url,
                 last_activity_at: now, error: '', brd_created_at: now, brd_draft: '', brd_draft_title: '',
                 cost_usd: Math.round((Number(j.cost_usd) || 0) * 1e6) / 1e6 },
} }];
"""), (3740, -160))
    w.add("Registry row", "n8n-nodes-base.code", 2, code("return [{ json: $json.registry }];"), (2640, -200))
    w.add("Register in BRD registry", "n8n-nodes-base.googleSheets", 4.5, sheets_append(
        "={{ $('Config').first().json.LOGS_SHEET_ID }}", "={{ $('Config').first().json.BRD_REGISTRY_TAB }}"),
        (2860, -200), creds=["gsheets"], onError="continueRegularOutput")
    w.add("Session row (completed)", "n8n-nodes-base.code", 2, code(
        "return [{ json: $('Published').first().json.session_row }];"), (3080, -200))
    w.add("Mark session completed", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (3300, -200),
          onError="continueRegularOutput")
    w.add("Success notifications", "n8n-nodes-base.code", 2, code("""
const p = $('Published').first().json;
// Тестовые сессии (eval-*) заказчика в Telegram не имеют — им не пишем.
const isEval = String(p.session.chat_id).startsWith('eval-');
return [
  ...(isEval ? [] : [{ json: { audience: 'customer', chat_id: p.session.chat_id, kind: 'success', title: 'BRD готов',
            text: `Документ «${p.title}» сформирован и опубликован:\\n${p.doc_url}\\n\\nКоманда развития ИИ-среды получила уведомление и свяжется с вами для согласования. Начать новую беседу — /new`, source: 'brd_generator' } }]),
  { json: { audience: 'team', kind: 'info', title: isEval ? 'Новый BRD (тестовый прогон)' : 'Новый BRD',
            text: `«${p.title}»\\nИнициатор: ${p.session.username || p.session.chat_id}\\nОтветов в интервью: ${p.turns}\\nСтатус: Draft\\n${p.doc_url}`, source: 'brd_generator' } },
];
"""), (3520, -200))
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (3740, -200))

    w.add("Generation failed", "n8n-nodes-base.code", 2, code(LIMIT_JS + """
// Неудача: возвращаем сессию в active (заказчик может повторить /done — это вторая попытка), сообщаем саппорту и заказчику.
// Лимит ключа (402/403) или падение агента v3 по maxIterations — это предел, а не сбой: уходит в ветку Limit stop.
// Технический сбой (LLM/инфраструктура/запись). Готовый, но не опубликованный текст BRD сохраняем в сессии:
// повторный /done только опубликует его — без новой генерации и новых денег.
let s = {};
try { s = $('Load session').all().map((i) => i.json).find((r) => r && r.session_id) || {}; } catch (e) {}
let draft = null;
try { if ($('Render Google Doc request').isExecuted) { const r = $('Render Google Doc request').first().json; draft = { md: r.md, title: r.title }; } } catch (e) {}
const j = $input.first().json || {};
const e = j.error;
const msg = (typeof e === 'string' ? e : (e && (e.message || e.description)) || JSON.stringify(e || 'Unknown error')).slice(0, 800);
const cfg = $('Config').first().json;
let limit = null;
if (MONEY_RE.test(msg)) limit = { limit_type: 'cost_key', limit_value: 'лимит ключа OpenRouter', observed: msg.slice(0, 200) };
else if (STOP_RE.test(msg)) limit = { limit_type: 'steps', limit_value: cfg.MAX_ITERATIONS, observed: msg.slice(0, 200) };
return [{ json: { session_id: s.session_id || '', chat_id: s.chat_id || '', username: s.username || '', error: msg,
  limit, session: s, draft, stage: draft ? 'publish' : 'generate' } }];
"""), (2200, 260))
    w.add("Provider limit?", "n8n-nodes-base.if", 2, if_true("={{ !!$json.limit }}"), (2200, 460))
    w.add("Session row (back to active)", "n8n-nodes-base.code", 2, code("""
if (!$json.session_id) return [];
const row = { session_id: $json.session_id, status: 'active', error: $json.error, last_activity_at: new Date().toISOString() };
if ($json.draft) Object.assign(row, { brd_draft: $json.draft.md, brd_draft_title: $json.draft.title });
return [{ json: row }];
"""), (2420, 160))
    w.add("Return session to active", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (2640, 160), onError="continueRegularOutput")
    w.add("Failure error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "brd_generator", "BRD-04", alert=True, chat_expr="j.chat_id",
        msg_expr="(j.stage === 'publish' ? 'Не удалось опубликовать готовый BRD в Drive (текст сохранён в сессии): ' : 'Технический сбой при формировании BRD: ') + j.error",
        details_expr="'session_id=' + j.session_id")),
        (2420, 360))
    w.add("Log via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False), (2640, 360))
    w.add("Apology to customer", "n8n-nodes-base.code", 2, code("""
if (!$json.chat_id || String($json.chat_id).startsWith('eval-')) return [];
return [{ json: { audience: 'customer', chat_id: $json.chat_id, kind: 'warning', title: 'Не удалось сформировать BRD',
  text: $json.draft
    ? 'Документ уже составлен, но из-за технической ошибки не удалось опубликовать его в wiki. Текст сохранён, команда поддержки уведомлена. Отправьте /done через несколько минут — документ будет опубликован без повторного составления.'
    : 'Произошла техническая ошибка при формировании документа. Ваша беседа сохранена, команда поддержки уже уведомлена. Попробуйте отправить /done ещё раз через несколько минут.',
  source: 'brd_generator' } }];
"""), (2420, 540))
    w.add("Notify customer via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (2640, 540))

    # Исход любого потолка BRD-04: сессия needs_review + сообщение заказчику + ручная очередь. Повтора нет.
    w.add("Limit stop", "n8n-nodes-base.code", 2, code(f"""
const j = $input.first().json;
const s = j.session || {{}};
const l = j.limit;
const now = new Date().toISOString();
const row = {{ session_id: s.session_id, status: 'needs_review', last_activity_at: now,
              limit_hit: `${{l.limit_type}}: ${{String(l.observed).slice(0, 80)}} / ${{l.limit_value}}` }};
if (j.cost_usd !== undefined) row.cost_usd = Math.round(Number(j.cost_usd) * 1e6) / 1e6;
return [{{ json: {{ session_row: row, limit: l, session: s,
  customer: {{ audience: 'customer', chat_id: s.chat_id, kind: 'warning', title: 'BRD не сформирован автоматически',
              text: l.limit_type === 'validator' ? '{QUALITY_REPLY}' : '{LIMIT_REPLY}', source: 'brd_generator' }},
  queue: {{ ...l, workflow: $workflow.name, workflow_id: $workflow.id, execution_id: $execution.id,
           session_id: s.session_id || '', chat_id: s.chat_id || '', username: s.username || '',
           details: `Генерация BRD остановлена, сессия needs_review. Ответов в интервью: ${{s.turn_count ?? '—'}}, попыток генерации: ${{s.brd_attempts ?? 0}}.` }},
}} }}];
"""), (3080, 460))
    mark_outcome(w, "Outcome: limit_stopped", "limit_stopped", (3300, 460))
    w.add("Session row (needs_review)", "n8n-nodes-base.code", 2, code(
        "const j = $('Limit stop').first().json;\nreturn j.session_row.session_id ? [{ json: j.session_row }] : [];"),
        (3520, 380))
    w.add("Freeze session", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (3740, 380), onError="continueRegularOutput")
    w.add("Customer limit message", "n8n-nodes-base.code", 2, code(
        "const j = $('Limit stop').first().json;\nconst c = String(j.customer.chat_id || '');\n"
        "return c && !c.startsWith('eval-') ? [{ json: j.customer }] : [];"), (3520, 540))
    w.add("Notify customer (limit) via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (3740, 540))
    w.add("Quality log payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "brd_quality", "Quality gate", severity="warning", alert=False, chat_expr="j.session?.chat_id",
        msg_expr="'BRD не прошёл проверку стандарта после исправлений (плохой ответ модели, не сбой): ' + j.limit.observed",
        details_expr="'session_id=' + (j.session?.session_id || '')").replace(
        "const items = $input.all();", "const items = $input.all().filter((i) => i.json.limit?.limit_type === 'validator');")),
        (3520, 860))
    w.add("Log quality via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False), (3740, 860))
    w.add("Queue payload", "n8n-nodes-base.code", 2, code("return [{ json: $('Limit stop').first().json.queue }];"),
          (3520, 700))
    w.add("Escalate via BRD-07", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_QUEUE, wait=False), (3740, 700))
    # Журнал прогонов паттерна (лист brd_runs): исход, вердикт валидатора, запросы к KB, фактическая цена.
    w.add("Run: published", "n8n-nodes-base.code", 2, code(
        "const p = $('Published').first().json;\nreturn [{ json: { outcome: 'published', session: p.session, title: p.title, doc_url: p.doc_url,\n"
        "  from_draft: !!p.from_draft, found_existing: !!p.found_existing } }];"),
        (2640, -480))
    w.add("Run: stopped", "n8n-nodes-base.code", 2, code(
        "const j = $('Limit stop').first().json;\nreturn [{ json: { outcome: 'limit_stopped', limit_type: j.limit.limit_type, session: j.session } }];"),
        (3520, 860))
    w.add("Run: failed", "n8n-nodes-base.code", 2, code(
        "return [{ json: { outcome: 'error', error: $json.error, session: $json.session || {} } }];"), (2640, 880))
    w.add("Wait for usage (15 s)", "n8n-nodes-base.wait", 1.1, {"resume": "timeInterval", "amount": 15, "unit": "seconds"},
          (3740, 1000))
    w.add("Key usage (after)", "n8n-nodes-base.httpRequest", 4.2,
          http_json("GET", "https://openrouter.ai/api/v1/key"), (3960, 1000), creds=["http_openrouter"],
          alwaysOutputData=True, onError="continueRegularOutput")
    w.add("Run row", "n8n-nodes-base.code", 2, code("""
const r = $('Wait for usage (15 s)').first().json;
const cfg = $('Config').first().json;
const node = (n) => { try { return $(n).isExecuted ? ($(n).first()?.json ?? null) : null; } catch (e) { return null; } };
const before = node('Key usage (before)')?.data?.usage;
const after = $json.data?.usage;
const cost = before != null && after != null ? Math.round((after - before) * 1e6) / 1e6 : '';
const val = node('Validate BRD');
const fix = node('Revalidate');  // последний раунд исправления (петля: $() берёт последнее выполнение)
const verdict = (x) => (!x || !Array.isArray(x.violations) ? '' : x.violations.length ? 'fail' : 'pass');
const rules = (x) => (x && Array.isArray(x.violations) ? x.violations.map((v) => `р.${v.section} ${v.rule}`).join(', ') : '');
const fin = fix || val;
const base = String(cfg.N8N_BASE_URL || '').replace(/\\/$/, '');
const s = r.session || {};
return [{ json: {
  finished_at: new Date().toISOString(), session_id: s.session_id || '', chat_id: s.chat_id || '', title: r.title || val?.title || '',
  outcome: r.outcome, limit_type: r.limit_type || '',
  validator_first: verdict(val), violations_first: rules(val),
  repaired: fix ? 'yes' : 'no', repair_rounds: fix ? fix.round : 0, repair_actions: (fix?.actions || []).join('; '),
  validator_final: verdict(fin), violations_final: rules(fin),
  from_draft: r.from_draft ? 'yes' : '', found_existing_doc: r.found_existing ? 'yes' : '',
  kb_calls: val?.kb_calls ?? '', kb_queries: val?.kb_queries ?? '',
  writer_model: cfg.WRITER_MODEL, fixer_model: (fix?.actions || []).some((a) => a.includes('исправление')) ? cfg.LLM_MODEL : '',
  run_cost_usd: cost, cost_estimate_usd: fin ? Math.round((Number(fin.cost_usd) - (Number(s.cost_usd) || 0)) * 1e6) / 1e6 : '',
  doc_url: r.doc_url || '', error: String(r.error ? (r.error.message || r.error) : '').slice(0, 300),
  execution_url: base && !base.startsWith('__') ? `${base}/workflow/${$workflow.id}/executions/${$execution.id}` : String($execution.id),
} }];
"""), (4180, 1000))
    w.add("Append to brd_runs", "n8n-nodes-base.googleSheets", 4.5, sheets_append(
        "={{ $('Config').first().json.LOGS_SHEET_ID }}", "={{ $('Config').first().json.RUNS_TAB }}"),
        (4400, 1000), creds=["gsheets"], onError="continueRegularOutput")
    mark_outcome(w, "Outcome: done", "done", (2640, -360))
    mark_outcome(w, "Outcome: error", "error", (2640, 720))

    w.connect("When called by BRD-03", "Config")
    w.connect("Manual test", "Test input")
    w.connect("Test input", "Config")
    w.chain("Config", "Load session", "Load all sessions", "Check limits", "Within limits?")
    w.connect("Check limits", "Generation failed", out=1)
    w.chain("Within limits?", "Attempt row", "Record attempt", "Key usage (before)", "Saved draft?")
    w.connect("Saved draft?", "Draft to publish", out=0)
    w.connect("Saved draft?", "Build BRD request", out=1)
    w.connect("Within limits?", "Limit stop", out=1)
    w.connect("Build BRD request", "BRD writer", out=0)
    w.connect("Build BRD request", "Generation failed", out=1)
    w.connect("LLM (writer)", "BRD writer", kind="ai_languageModel")
    w.connect("KnowledgeBase", "BRD writer", kind="ai_tool")
    w.connect("Embeddings (cloud.ru)", "KnowledgeBase", kind="ai_embedding")
    w.connect("BRD writer", "Validate BRD", out=0)
    w.connect("BRD writer", "Generation failed", out=1)
    w.connect("Validate BRD", "Stopped by limit?")
    w.connect("Stopped by limit?", "Limit stop", out=0)
    w.connect("Stopped by limit?", "Quality gate", out=1)
    w.chain("Quality gate", "Route by quality")
    w.connect("Route by quality", "Render Google Doc request", out=0)
    w.connect("Route by quality", "Repair plan", out=1)
    w.connect("Route by quality", "Limit stop", out=2)
    w.chain("Repair plan", "Regenerate parts?")
    w.connect("Regenerate parts?", "Regen requests", out=0)
    w.connect("Regenerate parts?", "Repair request", out=1)
    w.chain("Regen requests", "BRD writer (regen)", "Merge regen", "Revalidate")
    w.connect("BRD writer (regen)", "Generation failed", out=1)
    w.connect("LLM (writer, regen)", "BRD writer (regen)", kind="ai_languageModel")
    w.connect("KnowledgeBase (regen)", "BRD writer (regen)", kind="ai_tool")
    w.connect("Embeddings (regen)", "KnowledgeBase (regen)", kind="ai_embedding")
    w.chain("Repair request", "BRD fixer", "Apply fixes", "Revalidate")
    w.connect("LLM (fixer)", "BRD fixer", kind="ai_languageModel")
    w.connect("BRD fixer", "Generation failed", out=1)
    w.connect("Revalidate", "Quality gate")  # петля: следующий раунд или публикация
    w.connect("Draft to publish", "Render Google Doc request")
    w.connect("Render Google Doc request", "Upload to Drive as Google Doc")
    w.connect("Upload to Drive as Google Doc", "Published", out=0)
    w.connect("Upload to Drive as Google Doc", "Find uploaded doc", out=1)
    w.connect("Find uploaded doc", "Doc already created?", out=0)
    w.connect("Find uploaded doc", "Generation failed", out=1)
    w.connect("Doc already created?", "Use found doc", out=0)
    w.connect("Doc already created?", "Wait before retry (10 s)", out=1)
    w.connect("Use found doc", "Published")
    w.chain("Wait before retry (10 s)", "Retry upload (once)")
    w.connect("Retry upload (once)", "Published", out=0)
    w.connect("Retry upload (once)", "Generation failed", out=1)
    w.chain("Published", "Registry row", "Register in BRD registry", "Session row (completed)",
            "Mark session completed", "Success notifications", "Notify via BRD-02")
    w.connect("Published", "Outcome: done")
    w.connect("Generation failed", "Provider limit?")
    w.connect("Provider limit?", "Limit stop", out=0)
    w.connect("Provider limit?", "Session row (back to active)", out=1)
    w.connect("Provider limit?", "Failure error payload", out=1)
    w.connect("Provider limit?", "Apology to customer", out=1)
    w.connect("Provider limit?", "Outcome: error", out=1)
    w.connect("Session row (back to active)", "Return session to active")
    w.connect("Failure error payload", "Log via BRD-00")
    w.connect("Apology to customer", "Notify customer via BRD-02")
    w.connect("Limit stop", "Outcome: limit_stopped")
    w.connect("Outcome: limit_stopped", "Session row (needs_review)")
    w.connect("Outcome: limit_stopped", "Customer limit message")
    w.connect("Outcome: limit_stopped", "Queue payload")
    w.connect("Outcome: limit_stopped", "Quality log payload")
    w.connect("Quality log payload", "Log quality via BRD-00")
    w.connect("Session row (needs_review)", "Freeze session")
    w.connect("Customer limit message", "Notify customer (limit) via BRD-02")
    w.connect("Queue payload", "Escalate via BRD-07")
    w.connect("Published", "Run: published")
    w.connect("Outcome: limit_stopped", "Run: stopped")
    w.connect("Provider limit?", "Run: failed", out=1)
    for n in ("Run: published", "Run: stopped", "Run: failed"):
        w.connect(n, "Wait for usage (15 s)")
    w.chain("Wait for usage (15 s)", "Key usage (after)", "Run row", "Append to brd_runs")
    w.dx = 0
    w.note(f"""## BRD-04 — генерация и публикация BRD
Вызывается асинхронно из BRD-03 (`session_id`). Для отладки — **Manual test** с session_id из brd_sessions.
0. **Check limits** — потолки до генерации: попыток на сессию ≤ {LIMITS["BRD_ATTEMPTS_PER_SESSION"]}, BRD от заказчика за 24 ч < {LIMITS["BRD_PER_CHAT_PER_DAY"]}, всего за 24 ч < {LIMITS["BRD_PER_DAY"]} боевых и < {LIMITS["BRD_EVAL_PER_DAY"]} тестовых (eval-*) — раздельно, расход сессии < ${LIMITS["SESSION_BUDGET_USD"]}.
1. Транскрипт интервью + шаблон BRD (mlops-deployment-intake-v2) → **BRD writer** (OpenRouter + KnowledgeBase, `maxIterations = {LIMITS["BRD_MAX_ITERATIONS"]}`) пишет BRD в три приёма: разделы 0–5, 6–7, 8–12 (лимит 8000 токенов на ответ).
2. Проверка: ни одна часть не остановлена по пределу шагов; есть ≥ 11 из 13 разделов.
3. Markdown → HTML → загрузка в Drive с конвертацией в **Google Doc** (таблицы и заголовки сохраняются) в папку `BRD_FOLDER_ID`.
4. Строка в реестре BRD (Google Sheet, лист brd_registry), сессия → `completed`.
5. Ссылка заказчику и в чат команды (через BRD-02).
**Сбой** → сессия в `active`, заказчик может повторить /done (вторая попытка); саппорт получает алерт.
**Потолок** (шаги, попытки, число BRD, бюджет, лимит ключа 402/403) → **Limit stop**: сессия `needs_review`, сообщение заказчику, ручная очередь **BRD-07**. Повтора нет. Потолок времени — сторож в BRD-05: > {LIMITS["GENERATION_STUCK_MIN"]} мин в `generating` → ручная очередь (таймаут воркфлоу на тарифе ≤ 3 мин, короче нормы генерации).""",
           (-40, -620), w=820, h=500, color=4)
    w.dump("BRD-04_brd-generator.json")


# ===========================================================================
# BRD-05 — напоминания заказчикам о незавершённых интервью
def build_reminders():
    w = WF("BRD-05 Reminders (unfinished interviews)")
    w.add("Every hour", "n8n-nodes-base.scheduleTrigger", 1.2,
          {"rule": {"interval": [{"field": "cronExpression", "expression": "15 * * * *"}]}}, (0, 0))
    w.add("Manual run", "n8n-nodes-base.manualTrigger", 1, {}, (0, 180))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "REMIND_AFTER_HOURS": 24,
        "MAX_REMINDERS": 2,
        "CLOSE_AFTER_HOURS": 72,
        "WORK_HOUR_FROM": 9,
        "WORK_HOUR_TO": 20,
        # потолок времени генерации BRD-04 (обоснование — LIMITS)
        "GENERATION_STUCK_MIN": LIMITS["GENERATION_STUCK_MIN"],
    }), (220, 90))
    w.add("Load active sessions", "n8n-nodes-base.dataTable", 1,
          dt_get(SESSIONS_TABLE, [("status", "active"), ("status", "generating")], match="anyCondition"),
          (440, 90), alwaysOutputData=True, executeOnce=True)
    w.add("Plan reminders", "n8n-nodes-base.code", 2, code("""
const cfg = $('Config').first().json;
const now = new Date();
const hourMsk = Number(now.toLocaleString('en-GB', { timeZone: 'Europe/Moscow', hour: '2-digit', hour12: false }));
const workTime = hourMsk >= Number(cfg.WORK_HOUR_FROM) && hourMsk < Number(cfg.WORK_HOUR_TO);
const H = 3600 * 1000;
const all = $input.all().map((i) => i.json).filter((r) => r && r.session_id);
const sessions = all.filter((r) => r.status === 'active');
const saves = [], notifications = [], escalations = [];
// Сторож потолка времени: BRD-04 мог быть снят по таймауту и не успеть записать исход.
for (const s of all.filter((r) => r.status === 'generating')) {
  const min = (now - new Date(s.last_activity_at)) / 60000;
  if (min < Number(cfg.GENERATION_STUCK_MIN)) continue;
  saves.push({ session_id: s.session_id, status: 'needs_review', last_activity_at: now.toISOString(),
               limit_hit: `time: ${Math.round(min)} мин в generating / ${cfg.GENERATION_STUCK_MIN}` });
  notifications.push({ audience: 'customer', chat_id: s.chat_id, kind: 'warning', title: 'BRD не сформирован автоматически',
    text: 'LIMIT_REPLY_JS', source: 'reminders' });
  escalations.push({ limit_type: 'time', limit_value: `${cfg.GENERATION_STUCK_MIN} мин`, observed: `${Math.round(min)} мин в статусе generating`,
    workflow: 'BRD-04 BRD Generator & Publisher (найдено BRD-05)', session_id: s.session_id, chat_id: s.chat_id, username: s.username || '',
    details: 'Генерация не завершилась и не записала исход (вероятно, снята по таймауту). Сессия переведена в needs_review.' });
}
for (const s of sessions) {
  const idleH = (now - new Date(s.last_activity_at)) / H;
  const sinceRemH = s.last_reminder_at ? (now - new Date(s.last_reminder_at)) / H : Infinity;
  const sent = Number(s.reminder_count) || 0;
  let lastQ = '';
  try { const h = JSON.parse(s.history || '[]'); lastQ = ([...h].reverse().find((m) => m.role === 'assistant') || {}).content || ''; } catch (e) {}
  if (sent >= Number(cfg.MAX_REMINDERS) && idleH >= Number(cfg.CLOSE_AFTER_HOURS)) {
    saves.push({ session_id: s.session_id, status: 'abandoned', last_activity_at: now.toISOString() });
    notifications.push({ audience: 'customer', chat_id: s.chat_id, kind: 'info', title: 'Беседа закрыта',
      text: 'Мы так и не закончили интервью, поэтому я закрыл беседу. Когда будете готовы — напишите /new, и начнём заново.', source: 'reminders' });
    notifications.push({ audience: 'team', kind: 'info', title: 'Интервью брошено',
      text: `Инициатор ${s.username || s.chat_id} не завершил интервью (ответов: ${s.turn_count}, напоминаний: ${sent}). Сессия закрыта как abandoned.`, source: 'reminders' });
  } else if (workTime && sent < Number(cfg.MAX_REMINDERS) && idleH >= Number(cfg.REMIND_AFTER_HOURS) && sinceRemH >= Number(cfg.REMIND_AFTER_HOURS)) {
    saves.push({ session_id: s.session_id, reminder_count: sent + 1, last_reminder_at: now.toISOString() });
    notifications.push({ audience: 'customer', chat_id: s.chat_id, kind: 'reminder', title: 'Продолжим сбор требований?',
      text: 'Наша беседа по вашей задаче не завершена.' + (lastQ ? `\\n\\nПоследний вопрос был такой:\\n«${lastQ.slice(0, 600)}»` : '') +
            '\\n\\nПросто ответьте на него. Если информации уже достаточно — /done, если задача неактуальна — /cancel.', source: 'reminders' });
  }
}
return [{ json: { checked: sessions.length, work_time: workTime, saves, notifications, escalations } }];
""".replace("LIMIT_REPLY_JS", LIMIT_REPLY)), (660, 90))
    w.add("Session updates", "n8n-nodes-base.code", 2, code("return $json.saves.map((s) => ({ json: s }));"), (880, 0))
    w.add("Update sessions", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (1100, 0))
    w.add("Messages", "n8n-nodes-base.code", 2, code(
        "return $('Plan reminders').first().json.notifications.map((n) => ({ json: n }));"), (880, 180))
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (1100, 180))
    w.add("Escalations", "n8n-nodes-base.code", 2, code(
        "return $('Plan reminders').first().json.escalations.map((e) => ({ json: e }));"), (880, 360))
    w.add("Escalate via BRD-07", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_QUEUE, wait=False), (1100, 360))
    w.connect("Every hour", "Config")
    w.connect("Manual run", "Config")
    w.chain("Config", "Load active sessions", "Plan reminders")
    w.connect("Plan reminders", "Session updates")
    w.connect("Plan reminders", "Messages")
    w.connect("Session updates", "Update sessions")
    w.connect("Messages", "Notify via BRD-02")
    w.connect("Plan reminders", "Escalations")
    w.connect("Escalations", "Escalate via BRD-07")
    w.note("""## BRD-05 — напоминания и сторож генерации
Каждый час (в :15) проверяет беседы в **brd_sessions**:
• нет ответа ≥ 24 ч → напоминание заказчику с последним вопросом (не больше 2 раз, только с 9:00 до 20:00 МСК);
• после 2 напоминаний и ≥ 72 ч тишины → сессия `abandoned`, сообщение заказчику и в чат команды;
• **сторож потолка времени:** сессия в `generating` дольше `GENERATION_STUCK_MIN` → `needs_review` + ручная очередь **BRD-07**.
Сессии `needs_review` не трогает: их разбирает команда. Пороги задаются в **Config**.""", (-40, -340), w=640, h=270, color=2)
    w.dump("BRD-05_reminders.json")


# ===========================================================================
# BRD-06 — ежедневный дайджест
def build_digest():
    w = WF("BRD-06 Daily Digest")
    w.add("Daily 09:00", "n8n-nodes-base.scheduleTrigger", 1.2,
          {"rule": {"interval": [{"field": "cronExpression", "expression": "0 9 * * *"}]}}, (0, 0))
    w.add("Manual run", "n8n-nodes-base.manualTrigger", 1, {}, (0, 180))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "LOGS_SHEET_ID": "__CFG_LOGS_SHEET_ID__",
        "ERRORS_SHEET_TAB": "errors",
        "PERIOD_HOURS": 24,
    }), (220, 90))
    w.add("Load sessions", "n8n-nodes-base.dataTable", 1, dt_get(SESSIONS_TABLE), (440, 90),
          alwaysOutputData=True, executeOnce=True)
    w.add("Load KB state", "n8n-nodes-base.dataTable", 1, dt_get(KB_TABLE), (660, 90),
          alwaysOutputData=True, executeOnce=True)
    w.add("Load errors", "n8n-nodes-base.googleSheets", 4.5, {
        "authentication": "oAuth2", "operation": "read",
        "documentId": {"__rl": True, "value": "={{ $('Config').first().json.LOGS_SHEET_ID }}", "mode": "id"},
        "sheetName": {"__rl": True, "value": "={{ $('Config').first().json.ERRORS_SHEET_TAB }}", "mode": "name"},
        "options": {},
    }, (880, 90), creds=["gsheets"], alwaysOutputData=True, executeOnce=True, onError="continueRegularOutput")
    # Расход по ключу OpenRouter (денежный потолок живёт у провайдера) — GET /api/v1/key.
    w.add("OpenRouter key usage", "n8n-nodes-base.httpRequest", 4.2,
          http_json("GET", "https://openrouter.ai/api/v1/key"), (1100, 90), creds=["http_openrouter"],
          alwaysOutputData=True, executeOnce=True, onError="continueRegularOutput")
    w.add("Build digest", "n8n-nodes-base.code", 2, code("""
const cfg = $('Config').first().json;
const since = Date.now() - (Number(cfg.PERIOD_HOURS) || 24) * 3600 * 1000;
const inPeriod = (t) => t && new Date(t).getTime() >= since;
const sessions = $('Load sessions').all().map((i) => i.json).filter((r) => r && r.session_id);
const kb = $('Load KB state').all().map((i) => i.json).filter((r) => r && r.file_id);
const errors = $('Load errors').all().map((i) => i.json).filter((r) => r && r.timestamp && inPeriod(r.timestamp));

const started = sessions.filter((s) => inPeriod(s.started_at));
const completed = sessions.filter((s) => s.status === 'completed' && inPeriod(s.last_activity_at));
const abandoned = sessions.filter((s) => s.status === 'abandoned' && inPeriod(s.last_activity_at));
const active = sessions.filter((s) => s.status === 'active');
const generating = sessions.filter((s) => s.status === 'generating');
const review = sessions.filter((s) => s.status === 'needs_review');
let key = null;
try { key = $('OpenRouter key usage').first().json.data || null; } catch (e) {}

const byWf = {};
for (const e of errors) { const k = e.workflow || e.component || '—'; byWf[k] = (byWf[k] || 0) + 1; }
const kbOk = kb.filter((r) => r.status === 'ok');
const kbErr = kb.filter((r) => r.status === 'error');

const lines = [];
lines.push(`Интервью: начато ${started.length}, завершено BRD ${completed.length}, брошено ${abandoned.length}; сейчас активно ${active.length}` + (generating.length ? `, формируется ${generating.length}` : ''));
if (completed.length) {
  lines.push('', 'Новые BRD:');
  for (const s of completed) lines.push(`• ${s.brd_title || 'Без названия'} — ${s.username || s.chat_id}\\n  ${s.brd_url}`);
}
if (review.length) {
  lines.push('', `Остановлено по пределу, ждут решения команды (needs_review, лист manual_queue): ${review.length}`);
  for (const s of review.slice(0, 10)) lines.push(`• ${s.username || s.chat_id}: ${s.limit_hit || 'предел'}`);
}
if (active.length) {
  lines.push('', 'Активные беседы:');
  for (const s of active.slice(0, 10)) lines.push(`• ${s.username || s.chat_id}: ответов ${s.turn_count}, последняя активность ${String(s.last_activity_at).slice(0, 16).replace('T', ' ')} UTC`);
}
lines.push('', `База знаний: документов в индексе ${kbOk.length}, фрагментов ${kbOk.reduce((x, r) => x + (Number(r.chunk_count) || 0), 0)}` + (kbErr.length ? `, с ошибкой ${kbErr.length} (${kbErr.map((r) => r.name).join(', ')})` : ''));
if (key) {
  const usd = (v) => (v == null ? '—' : '$' + Number(v).toFixed(2));
  lines.push('', `OpenRouter: расход за сутки ${usd(key.usage_daily)}, за неделю ${usd(key.usage_weekly)}; лимит ключа ${usd(key.limit)}` +
    (key.limit_reset ? ` (${key.limit_reset})` : '') + (key.limit_remaining != null ? `, осталось ${usd(key.limit_remaining)}` : ''));
}
lines.push('', errors.length ? `Ошибки за сутки: ${errors.length} — ` + Object.entries(byWf).map(([k, v]) => `${k}: ${v}`).join('; ') : 'Ошибок за сутки нет.');
return [{ json: { audience: 'team', kind: 'report', title: 'Дайджест BRD-агента за сутки', text: lines.join('\\n'), source: 'digest' } }];
"""), (1320, 90))
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (1540, 90))
    w.connect("Daily 09:00", "Config")
    w.connect("Manual run", "Config")
    w.chain("Config", "Load sessions", "Load KB state", "Load errors", "OpenRouter key usage", "Build digest",
            "Notify via BRD-02")
    w.note("""## BRD-06 — ежедневный дайджест (09:00 МСК)
Сводка за 24 ч в чат команды: начатые, завершённые и брошенные интервью, ссылки на новые BRD, сессии, остановленные по пределу (`needs_review`), активные беседы, расход и лимит ключа OpenRouter, состояние базы знаний, ошибки по воркфлоу (лист errors).""",
           (-40, -240), w=620, h=180, color=2)
    w.dump("BRD-06_daily-digest.json")


# ===========================================================================
# BRD-98 — тестовые входы для «Таблицы пяти прогонов»: транскрипты eval/transcripts → brd_sessions
def build_eval_seed():
    w = WF("BRD-98 Eval: seed test sessions")
    w.add("Run once per series", "n8n-nodes-base.manualTrigger", 1, {}, (0, 0))
    transcripts = [json.loads(f.read_text(encoding="utf-8"))
                   for f in sorted((ROOT / "eval/transcripts").glob("*.json"))]
    w.add("Test sessions", "n8n-nodes-base.code", 2, code(f"""
// Каждая серия — новые session_id (eval-<прогон>-<время>): прошлые BRD остаются в учёте потолков артефактов.
const T = {json.dumps(transcripts, ensure_ascii=False)};
const now = new Date().toISOString();
const stamp = Date.now().toString(36);
return T.map((t) => ({{ json: {{
  session_id: `eval-${{t.run}}-${{stamp}}`, chat_id: `eval-${{t.run}}`, user_id: '', username: t.username,
  status: 'eval_ready', history: JSON.stringify(t.history.map((m) => ({{ ...m, ts: now }}))),
  turn_count: t.history.filter((m) => m.role === 'user').length, started_at: now, last_activity_at: now,
  reminder_count: 0, last_reminder_at: '', brd_title: '', brd_url: '', error: '',
  cost_usd: 0, limit_hit: '', brd_attempts: 0, brd_created_at: '', brd_draft: '', brd_draft_title: '',
}} }}));
"""), (220, 0))
    w.add("Save test sessions", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (440, 0))
    w.add("Session IDs for BRD-04", "n8n-nodes-base.code", 2, code("""
// Скопируйте session_id по одному в BRD-04 → Test input → Manual test (один прогон = одно выполнение).
return $('Test sessions').all().map((i) => ({ json: { run: i.json.chat_id, session_id: i.json.session_id, username: i.json.username } }));
"""), (660, 0))
    w.chain("Run once per series", "Test sessions", "Save test sessions", "Session IDs for BRD-04")
    w.note("""## BRD-98 — входы для таблицы пяти прогонов
Записывает 5 тестовых интервью из `eval/transcripts/` в **brd_sessions** (chat_id `eval-1…5`, статус `eval_ready`).
Дальше каждый прогон: **BRD-04 → Test input** = session_id из узла *Session IDs for BRD-04* → **Manual test**.
Тестовым сессиям BRD-04 не пишет заказчику в Telegram; результат каждого прогона — строка в листе **brd_runs**.
Потолки не отключены: 5 BRD за 24 ч всего, 2 попытки на сессию.""", (-40, -260), w=640, h=220, color=7)
    w.dump("BRD-98_eval-seed.json")


# ===========================================================================
# BRD-99 — разовая настройка: создание Data Tables
def build_setup():
    w = WF("BRD-99 Setup (create data tables)", error_workflow=False)
    w.add("Run once", "n8n-nodes-base.manualTrigger", 1, {}, (0, 0))

    def table(name, cols, pos):
        w.add(f"Create {name}", "n8n-nodes-base.dataTable", 1, {
            "resource": "table", "operation": "create", "tableName": name,
            "columns": {"column": [{"name": c, "type": t} for c, t in cols]},
            "options": {"createIfNotExists": True},
        }, pos, executeOnce=True)

    table(SESSIONS_TABLE, [
        ("session_id", "string"), ("chat_id", "string"), ("user_id", "string"), ("username", "string"),
        ("status", "string"), ("history", "string"), ("turn_count", "number"), ("started_at", "string"),
        ("last_activity_at", "string"), ("reminder_count", "number"), ("last_reminder_at", "string"),
        ("brd_title", "string"), ("brd_url", "string"), ("error", "string"),
        # control layer
        ("cost_usd", "number"), ("limit_hit", "string"), ("brd_attempts", "number"), ("brd_created_at", "string"),
        # готовый, но не опубликованный BRD (технический сбой записи) — повторный /done только публикует
        ("brd_draft", "string"), ("brd_draft_title", "string"),
    ], (220, 0))
    table(KB_TABLE, [
        ("file_id", "string"), ("name", "string"), ("mime_type", "string"), ("doc_code", "string"),
        ("modified_time", "string"), ("web_link", "string"), ("chunk_count", "number"), ("status", "string"),
        ("error", "string"), ("ingested_at", "string"),
    ], (440, 0))
    w.chain("Run once", f"Create {SESSIONS_TABLE}", f"Create {KB_TABLE}")
    w.note("""## BRD-99 — разовая настройка
Запустите один раз вручную: создаёт Data Tables **brd_sessions** (состояние интервью) и **kb_files** (состояние базы знаний). Если таблицы уже есть, они не пересоздаются.""",
           (-40, -220), w=560, h=170, color=7)
    w.dump("BRD-99_setup-data-tables.json")


if __name__ == "__main__":
    print("Building workflows →", OUT)
    build_errors()
    build_notify()
    build_queue()
    build_kb_sync()
    build_interview()
    build_brd_generator()
    build_reminders()
    build_digest()
    build_eval_seed()
    build_setup()
