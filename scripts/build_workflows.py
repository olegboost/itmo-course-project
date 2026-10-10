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
BRD_TEMPLATE = (ROOT / "templates/brd_template.md").read_text(encoding="utf-8").strip()
_md = (ROOT / "scripts/md2html.js").read_text(encoding="utf-8")
MD2HTML_JS = _md.split("// BEGIN md2html")[1].split("// END md2html")[0].strip()

# ---------------------------------------------------------------------------
# credentials: key -> (credential type, placeholder id, display name)
CREDS = {
    "telegram": ("telegramApi", "__CRED_TELEGRAM__", "Telegram BRD bot"),
    "gdrive": ("googleDriveOAuth2Api", "__CRED_GDRIVE__", "Google Drive OAuth2"),
    "gsheets": ("googleSheetsOAuth2Api", "__CRED_GSHEETS__", "Google Sheets OAuth2"),
    "deepseek": ("deepSeekApi", "__CRED_DEEPSEEK__", "DeepSeek"),
    "openai_cloudru": ("openAiApi", "__CRED_OPENAI_CLOUDRU__", "cloud.ru (OpenAI-compatible)"),
    "pinecone": ("pineconeApi", "__CRED_PINECONE__", "Pinecone"),
    "http_pinecone": ("httpHeaderAuth", "__CRED_HTTP_PINECONE__", "Pinecone API key (header)"),
    "http_cloudru": ("httpHeaderAuth", "__CRED_HTTP_CLOUDRU__", "cloud.ru API key (header)"),
}

WF_ERRORS = "__WF_ERRORS__"
WF_NOTIFY = "__WF_NOTIFY__"
WF_BRD_GENERATOR = "__WF_BRD_GENERATOR__"

SESSIONS_TABLE = "brd_sessions"
KB_TABLE = "kb_files"


def uid():
    return str(uuid.uuid4())


class WF:
    def __init__(self, name, error_workflow=True):
        self.name = name
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

    def add(self, name, type_, version, params, pos, creds=None, **extra):
        node = {
            "parameters": params,
            "id": uid(),
            "name": name,
            "type": type_,
            "typeVersion": version,
            "position": list(pos),
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
  r.timestamp = new Date().toISOString();
  return { json: r };
});
"""), (480, 100))
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
    w.connect("On workflow error", "Config")
    w.connect("Handled error (from workflows)", "Config")
    w.chain("Config", "Normalize error", "Append to errors sheet", "Format alert (with dedup)", "Alert support chat")
    w.note("""## BRD-00 — ошибки и алерты саппорту
**Два входа:**
• **Error Trigger** — любое необработанное падение воркфлоу BRD-01…06 (у них в Settings → Error workflow указан этот воркфлоу).
• **Execute Workflow Trigger** — обработанные ошибки, которые воркфлоу присылают сами (поля `component, node, severity, message, details, chat_id, alert`).

**Дальше:** строка в Google Sheet `errors` для анализа и алерт в Telegram-чат саппорта (`severity` error/critical). Одинаковые алерты подавляются на 15 минут.

Telegram отправляется напрямую, а не через BRD-02, чтобы не возникло петли, если сломан сам Notify.""",
           (-40, -340), w=620, h=300, color=3)
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
        "queryString": "={{ \"'\" + $json.KB_FOLDER_ID + \"' in parents and mimeType != 'application/vnd.google-apps.folder'\" }}",
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
const files = $('List files in KB folder').all().map((i) => i.json).filter((f) => f && f.id);
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
const to_delete = Object.values(state).filter((r) => !seen.has(r.file_id)).map((r) => {
  const n = Math.max(1, Number(r.chunk_count) || 0);
  return { file_id: r.file_id, name: r.name || '', prev_chunk_count: Number(r.chunk_count) || 0,
           ids: Array.from({ length: n }, (_, k) => `${r.file_id}#${String(k).padStart(4, '0')}`) };
});
return [{ json: {
  run_id: $execution.id, started_at: new Date().toISOString(),
  to_process, to_delete, unchanged, unsupported, excluded,
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
if (errors.length) lines.push(`Ошибки (${errors.length}): ` + errors.map((e) => `${e.name} — ${String(e.error).slice(0, 150)}`).join('; '));
const notifications = [{ audience: 'team', kind: errors.length ? 'warning' : 'report',
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


def build_interview():
    w = WF("BRD-03 Interview Agent (Telegram)")
    w.add("Telegram Trigger", "n8n-nodes-base.telegramTrigger", 1.2, {"updates": ["message"], "additionalFields": {}},
          (0, 200), creds=["telegram"])
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "MIN_TURNS_FOR_DONE": 4,
        "SOFT_MAX_TURNS": 30,
        "HISTORY_LIMIT": 60,
        "STT_URL": "__CFG_STT_URL__",
        "STT_MODEL": "__CFG_STT_MODEL__",
    }, include_other=True), (220, 200))
    w.add("Parse message", "n8n-nodes-base.code", 2, code("""
const m = $json.message || {};
const from = m.from || {};
const chat = m.chat || {};
const text = String(m.text || m.caption || '').trim();
const cm = text.match(/^\\/(\\w+)/);
let kind = (m.voice || m.audio) ? 'voice' : (text ? 'text' : 'other');
if (chat.type && chat.type !== 'private') kind = 'ignore';   // бот состоит и в чатах команды — там не отвечаем
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
          {"rules": {"values": [rule("voice"), rule("text"), rule("other")]}, "options": {}}, (660, 200))
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
    w.add("Load chat sessions", "n8n-nodes-base.dataTable", 1,
          dt_get(SESSIONS_TABLE, [("chat_id", "={{ $json.chat_id }}")]), (1540, 120),
          alwaysOutputData=True, executeOnce=True)
    w.add("Route", "n8n-nodes-base.code", 2, code(f"""
// Маршрутизация: команды, активная сессия, ход агента.
const cfg = $('Config').first().json;
const msg = $('Use transcript').isExecuted ? $('Use transcript').first().json : $('Parse message').first().json;
const rows = $input.all().map((i) => i.json).filter((r) => r && r.session_id);
const open = rows.filter((r) => ['active', 'generating'].includes(r.status))
  .sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)))[0] || null;
const now = new Date().toISOString();
const GREETING = '{GREETING}';
const HELP = '{HELP}';
const hist = (s) => {{ try {{ return JSON.parse(s.history || '[]'); }} catch (e) {{ return []; }} }};
const newSession = () => ({{
  session_id: `${{msg.chat_id}}-${{Date.now()}}`, chat_id: msg.chat_id, user_id: msg.user_id, username: msg.username,
  status: 'active', history: '[]', turn_count: 0, started_at: now, last_activity_at: now,
  reminder_count: 0, last_reminder_at: '', brd_title: '', brd_url: '', error: '',
}});
const res = {{ chat_id: msg.chat_id, username: msg.username, user_text: msg.text, reply: '', saves: [],
              run_agent: false, start_brd: false, session: null, history: [], prompt: '' }};
const cmd = msg.command;

if (!msg.text) {{
  res.reply = 'Не получилось разобрать сообщение. Пожалуйста, напишите текстом.';
}} else if (cmd === 'help') {{
  res.reply = HELP;
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
        "options": {"systemMessage": INTERVIEW_PROMPT, "maxIterations": 6},
    }, (2420, 0), onError="continueErrorOutput")
    w.add("DeepSeek (interview)", "@n8n/n8n-nodes-langchain.lmChatDeepSeek", 1, {
        "model": "deepseek-chat", "options": {"temperature": 0.3, "maxTokens": 2000, "timeout": 120000, "maxRetries": 2},
    }, (2300, 220), creds=["deepseek"])
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
    w.add("Process answer", "n8n-nodes-base.code", 2, code("""
const r = $('Route').first().json;
let out = String($json.output ?? $json.text ?? '').trim();
const ready = out.includes('<<BRD_READY>>');
out = out.replace(/<<BRD_READY>>/g, '').replace(/\\*\\*(.+?)\\*\\*/g, '$1').replace(/^#{1,6}\\s+/gm, '').trim();
if (!out) out = ready ? 'Спасибо!' : 'Извините, не получилось сформулировать ответ. Повторите, пожалуйста, последнее сообщение.';
const now = new Date().toISOString();
const history = r.history.concat([
  { role: 'user', content: r.user_text, ts: now },
  { role: 'assistant', content: out, ts: now },
]);
const s = { ...r.session, history: JSON.stringify(history), turn_count: Number(r.session.turn_count || 0) + 1,
            last_activity_at: now, reminder_count: 0, status: ready ? 'generating' : 'active' };
const reply = ready ? out + '\\n\\nФормирую BRD — это займёт 3–5 минут. Пришлю ссылку на документ.' : out;
return [{ json: { chat_id: r.chat_id, reply, saves: [s], session: s, start_brd: ready } }];
"""), (2860, 0))
    w.add("Agent failed", "n8n-nodes-base.code", 2, code("""
// Ошибка LLM/инструментов: сохраняем реплику заказчика, чтобы не потерять, и извиняемся.
const r = $('Route').first().json;
const now = new Date().toISOString();
const history = r.history.concat([{ role: 'user', content: r.user_text, ts: now }]);
const s = { ...r.session, history: JSON.stringify(history), last_activity_at: now, status: 'active' };
return [{ json: { chat_id: r.chat_id, saves: [s], session: s, start_brd: false, error: $json.error,
  reply: 'Извините, у меня технический сбой — ваше сообщение сохранено, команда поддержки уже уведомлена. Попробуйте повторить ответ через пару минут.' } }];
"""), (2860, 200))
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

    w.chain("Telegram Trigger", "Config", "Parse message", "Message type")
    w.connect("Message type", "Get voice file", out=0)
    w.connect("Message type", "Load chat sessions", out=1)
    w.connect("Message type", "Unsupported message", out=2)
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
    w.connect("DeepSeek (interview)", "Interview agent", kind="ai_languageModel")
    w.connect("KnowledgeBase", "Interview agent", kind="ai_tool")
    w.connect("Embeddings (cloud.ru)", "KnowledgeBase", kind="ai_embedding")
    w.connect("Interview agent", "Process answer", out=0)
    w.connect("Interview agent", "Agent failed", out=1)
    w.connect("Process answer", "Turn result")
    w.connect("Agent failed", "Turn result")
    w.connect("Agent failed", "Agent error payload")
    w.connect("Agent error payload", "Log agent error via BRD-00")
    w.connect("Turn result", "Has rows to save?")
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

    w.note("""## BRD-03 — агент-интервьюер (Telegram)
Заказчик пишет боту в личку (текст или голос → Whisper cloud.ru).
**Состояние** беседы — Data Table **brd_sessions** (история JSON, статус, счётчики).
**Команды:** /start, /new, /status, /done, /cancel, /help. Сообщения в групповых чатах игнорируются.

**Агент** (DeepSeek + инструмент KnowledgeBase → Pinecone) ведёт интервью по правилам скилла mlops-deployment-intake-v2: один вопрос за ход, без приписанных оценок, данные для ИБ только собираются.
Когда заказчик подтверждает резюме, агент ставит маркер `<<BRD_READY>>` → статус `generating` → асинхронный запуск **BRD-04**.

Ошибки (STT, LLM, Data Table, Telegram) → BRD-00 (лист errors + алерт саппорту); заказчик получает вежливое сообщение.""",
           (-40, -440), w=760, h=380, color=6)
    w.dump("BRD-03_interview-agent.json")


# ===========================================================================
# BRD-04 — генерация BRD и публикация в Google Drive
def build_brd_generator():
    w = WF("BRD-04 BRD Generator & Publisher")
    w.add("When called by BRD-03", "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"inputSource": "passthrough"}, (0, 0))
    w.add("Manual test", "n8n-nodes-base.manualTrigger", 1, {}, (0, 200))
    w.add("Test input", "n8n-nodes-base.set", 3.4, p_set({"session_id": "PASTE_SESSION_ID_FROM_brd_sessions"}),
          (220, 200))
    w.add("Config", "n8n-nodes-base.set", 3.4, p_set({
        "BRD_FOLDER_ID": "__CFG_BRD_FOLDER_ID__",
        "LOGS_SHEET_ID": "__CFG_LOGS_SHEET_ID__",
        "BRD_REGISTRY_TAB": "brd_registry",
        "MIN_SECTIONS": 11,
    }, include_other=True), (440, 0))
    w.add("Load session", "n8n-nodes-base.dataTable", 1,
          dt_get(SESSIONS_TABLE, [("session_id", "={{ $json.session_id }}")]), (660, 0),
          alwaysOutputData=True, executeOnce=True)
    tpl_js = json.dumps(BRD_TEMPLATE, ensure_ascii=False)
    w.add("Build BRD request", "n8n-nodes-base.code", 2, code(f"""
const s = $input.all().map((i) => i.json).find((r) => r && r.session_id);
if (!s) throw new Error('Сессия не найдена: ' + $('Config').first().json.session_id);
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
return PARTS.map((p) => ({{ json: {{ session: s, today, turns: Number(s.turn_count) || 0, part: p.part,
  prompt: prompt + '\\n\\nЧАСТЬ ' + p.part + ' из 3 (разделы ' + p.label + '). ' + p.hint }} }}));
"""), (880, 0), onError="continueErrorOutput")
    w.add("BRD writer", "@n8n/n8n-nodes-langchain.agent", 2.2, {
        "promptType": "define", "text": "={{ $json.prompt }}",
        "options": {"systemMessage": BRD_WRITER_PROMPT, "maxIterations": 8},
    }, (1100, 0), onError="continueErrorOutput", retryOnFail=True, maxTries=2, waitBetweenTries=5000)  # runs once per part
    w.add("DeepSeek (BRD)", "@n8n/n8n-nodes-langchain.lmChatDeepSeek", 1, {
        "model": "deepseek-chat", "options": {"temperature": 0.2, "maxTokens": 8000, "timeout": 300000, "maxRetries": 2},
    }, (980, 220), creds=["deepseek"])
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
    w.add("Validate BRD", "n8n-nodes-base.code", 2, code("""
const req = $('Build BRD request').first().json;
// Склеиваем три части (разделы 0–5, 6–7, 8–12). Если какая-то часть упала, её обработает ветка ошибки.
const outs = $input.all().map((i) => String(i.json.output ?? i.json.text ?? '').trim());
if (outs.length < 3) return [];
const clean = (t, k) => {
  t = t.replace(/^```(?:markdown|md)?\\s*\\n/i, '').replace(/\\n```\\s*$/, '').trim();
  if (k > 0) t = t.replace(/^#\\s+BRD:.*\\n+/, '');
  return t;
};
let md = outs.map(clean).join('\\n\\n');
const sections = new Set([...md.matchAll(/^##\\s+(\\d{1,2})\\./gm)].map((m) => Number(m[1])));
const min = Number($('Config').first().json.MIN_SECTIONS) || 11;
let title = (md.match(/^#\\s+BRD:\\s*(.+)$/m) || [])[1] || '';
if (!title) title = ((md.match(/\\|\\s*Название задачи\\/проекта\\s*\\|\\s*([^|]+)\\|/) || [])[1] || '').trim();
title = (title || 'Без названия').replace(/[\\\\/:*?"<>|]/g, ' ').replace(/\\s+/g, ' ').trim().slice(0, 90);
const valid = sections.size >= min && md.length > 1500;
return [{ json: { valid, md, title, sections: sections.size, length: md.length,
  error: valid ? '' : `BRD не прошёл проверку: разделов ${sections.size} (нужно ≥ ${min}), длина ${md.length}`,
  session: req.session, today: req.today, turns: req.turns } }];
"""), (1540, 0))
    w.add("Valid?", "n8n-nodes-base.if", 2, if_true("={{ $json.valid }}"), (1760, 0))
    w.add("Render Google Doc request", "n8n-nodes-base.code", 2, code(f"""
{MD2HTML_JS}

const j = $json;
const cfg = $('Config').first().json;
const who = String(j.session.username || j.session.chat_id).replace(/^@/, '');
const name = `BRD ${{j.today}} — ${{j.title}} (${{who}})`;
const html = md2html(j.md);
const boundary = 'brd_' + Date.now().toString(36);
const meta = {{ name, mimeType: 'application/vnd.google-apps.document', parents: [cfg.BRD_FOLDER_ID],
               description: `BRD по интервью в Telegram. Session ID: ${{j.session.session_id}}` }};
const body = `--${{boundary}}\\r\\nContent-Type: application/json; charset=UTF-8\\r\\n\\r\\n${{JSON.stringify(meta)}}\\r\\n` +
             `--${{boundary}}\\r\\nContent-Type: text/html; charset=UTF-8\\r\\n\\r\\n${{html}}\\r\\n--${{boundary}}--`;
return [{{ json: {{ ...j, doc_name: name, body, content_type: `multipart/related; boundary=${{boundary}}` }} }}];
"""), (1980, -100))
    w.add("Upload to Drive as Google Doc", "n8n-nodes-base.httpRequest", 4.2, {
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
        "rawContentType": "={{ $json.content_type }}",
        "body": "={{ $json.body }}",
        "options": {"timeout": 120000},
    }, (2200, -100), creds=["gdrive"], onError="continueErrorOutput", retryOnFail=True, maxTries=3,
        waitBetweenTries=5000)
    w.add("Published", "n8n-nodes-base.code", 2, code("""
const j = $('Render Google Doc request').first().json;
const url = $json.webViewLink || `https://docs.google.com/document/d/${$json.id}/edit`;
const now = new Date().toISOString();
return [{ json: {
  doc_id: $json.id, doc_url: url, doc_name: j.doc_name, title: j.title, session: j.session, turns: j.turns,
  registry: { created_at: now, title: j.title, customer: j.session.username || '', chat_id: j.session.chat_id,
              session_id: j.session.session_id, turns: j.turns, status: 'Draft', doc_url: url },
  session_row: { session_id: j.session.session_id, status: 'completed', brd_title: j.title, brd_url: url,
                 last_activity_at: now, error: '' },
} }];
"""), (2420, -100))
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
return [
  { json: { audience: 'customer', chat_id: p.session.chat_id, kind: 'success', title: 'BRD готов',
            text: `Документ «${p.title}» сформирован и опубликован:\\n${p.doc_url}\\n\\nКоманда развития ИИ-среды получила уведомление и свяжется с вами для согласования. Начать новую беседу — /new`, source: 'brd_generator' } },
  { json: { audience: 'team', kind: 'info', title: 'Новый BRD',
            text: `«${p.title}»\\nИнициатор: ${p.session.username || p.session.chat_id}\\nОтветов в интервью: ${p.turns}\\nСтатус: Draft\\n${p.doc_url}`, source: 'brd_generator' } },
];
"""), (3520, -200))
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (3740, -200))

    w.add("Generation failed", "n8n-nodes-base.code", 2, code("""
// Неудача: возвращаем сессию в active (заказчик может повторить /done), сообщаем саппорту и заказчику.
let s = {};
try { s = $('Load session').all().map((i) => i.json).find((r) => r && r.session_id) || {}; } catch (e) {}
const j = $input.first().json || {};
const e = j.error;
const msg = (typeof e === 'string' ? e : (e && (e.message || e.description)) || JSON.stringify(e || 'Unknown error')).slice(0, 800);
return [{ json: { session_id: s.session_id || '', chat_id: s.chat_id || '', username: s.username || '', error: msg } }];
"""), (2200, 260))
    w.add("Session row (back to active)", "n8n-nodes-base.code", 2, code("""
if (!$json.session_id) return [];
return [{ json: { session_id: $json.session_id, status: 'active', error: $json.error, last_activity_at: new Date().toISOString() } }];
"""), (2420, 160))
    w.add("Return session to active", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (2640, 160), onError="continueRegularOutput")
    w.add("Failure error payload", "n8n-nodes-base.code", 2, code(error_payload_js(
        "brd_generator", "BRD-04", alert=True, chat_expr="j.chat_id",
        msg_expr="'Не удалось сформировать или опубликовать BRD: ' + j.error", details_expr="'session_id=' + j.session_id")),
        (2420, 360))
    w.add("Log via BRD-00", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_ERRORS, wait=False), (2640, 360))
    w.add("Apology to customer", "n8n-nodes-base.code", 2, code("""
if (!$json.chat_id) return [];
return [{ json: { audience: 'customer', chat_id: $json.chat_id, kind: 'warning', title: 'Не удалось сформировать BRD',
  text: 'Произошла техническая ошибка при формировании документа. Ваша беседа сохранена, команда поддержки уже уведомлена. Попробуйте отправить /done ещё раз через несколько минут.',
  source: 'brd_generator' } }];
"""), (2420, 540))
    w.add("Notify customer via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (2640, 540))

    w.connect("When called by BRD-03", "Config")
    w.connect("Manual test", "Test input")
    w.connect("Test input", "Config")
    w.chain("Config", "Load session", "Build BRD request")
    w.connect("Build BRD request", "BRD writer", out=0)
    w.connect("Build BRD request", "Generation failed", out=1)
    w.connect("DeepSeek (BRD)", "BRD writer", kind="ai_languageModel")
    w.connect("KnowledgeBase", "BRD writer", kind="ai_tool")
    w.connect("Embeddings (cloud.ru)", "KnowledgeBase", kind="ai_embedding")
    w.connect("BRD writer", "Validate BRD", out=0)
    w.connect("BRD writer", "Generation failed", out=1)
    w.connect("Validate BRD", "Valid?")
    w.connect("Valid?", "Render Google Doc request", out=0)
    w.connect("Valid?", "Generation failed", out=1)
    w.connect("Render Google Doc request", "Upload to Drive as Google Doc")
    w.connect("Upload to Drive as Google Doc", "Published", out=0)
    w.connect("Upload to Drive as Google Doc", "Generation failed", out=1)
    w.chain("Published", "Registry row", "Register in BRD registry", "Session row (completed)",
            "Mark session completed", "Success notifications", "Notify via BRD-02")
    w.connect("Generation failed", "Session row (back to active)")
    w.connect("Generation failed", "Failure error payload")
    w.connect("Generation failed", "Apology to customer")
    w.connect("Session row (back to active)", "Return session to active")
    w.connect("Failure error payload", "Log via BRD-00")
    w.connect("Apology to customer", "Notify customer via BRD-02")
    w.note("""## BRD-04 — генерация и публикация BRD
Вызывается асинхронно из BRD-03 (`session_id`). Для отладки — **Manual test** с session_id из brd_sessions.
1. Транскрипт интервью + шаблон BRD (mlops-deployment-intake-v2) → **BRD writer** (DeepSeek + KnowledgeBase) пишет BRD в три приёма: разделы 0–5, 6–7, 8–12 (у DeepSeek лимит 8000 токенов на ответ).
2. Проверка: есть ≥ 11 из 13 разделов.
3. Markdown → HTML → загрузка в Drive с конвертацией в **Google Doc** (таблицы и заголовки сохраняются) в папку `BRD_FOLDER_ID`.
4. Строка в реестре BRD (Google Sheet, лист brd_registry), сессия → `completed`.
5. Ссылка заказчику и в чат команды (через BRD-02).
При ошибке сессия возвращается в `active`, заказчик может повторить /done; саппорт получает алерт.""",
           (-40, -480), w=760, h=360, color=4)
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
    }), (220, 90))
    w.add("Load active sessions", "n8n-nodes-base.dataTable", 1,
          dt_get(SESSIONS_TABLE, [("status", "active")]), (440, 90), alwaysOutputData=True, executeOnce=True)
    w.add("Plan reminders", "n8n-nodes-base.code", 2, code("""
const cfg = $('Config').first().json;
const now = new Date();
const hourMsk = Number(now.toLocaleString('en-GB', { timeZone: 'Europe/Moscow', hour: '2-digit', hour12: false }));
const workTime = hourMsk >= Number(cfg.WORK_HOUR_FROM) && hourMsk < Number(cfg.WORK_HOUR_TO);
const H = 3600 * 1000;
const sessions = $input.all().map((i) => i.json).filter((r) => r && r.session_id && r.status === 'active');
const saves = [], notifications = [];
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
return [{ json: { checked: sessions.length, work_time: workTime, saves, notifications } }];
"""), (660, 90))
    w.add("Session updates", "n8n-nodes-base.code", 2, code("return $json.saves.map((s) => ({ json: s }));"), (880, 0))
    w.add("Update sessions", "n8n-nodes-base.dataTable", 1,
          dt_upsert(SESSIONS_TABLE, "session_id", "={{ $json.session_id }}"), (1100, 0))
    w.add("Messages", "n8n-nodes-base.code", 2, code(
        "return $('Plan reminders').first().json.notifications.map((n) => ({ json: n }));"), (880, 180))
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (1100, 180))
    w.connect("Every hour", "Config")
    w.connect("Manual run", "Config")
    w.chain("Config", "Load active sessions", "Plan reminders")
    w.connect("Plan reminders", "Session updates")
    w.connect("Plan reminders", "Messages")
    w.connect("Session updates", "Update sessions")
    w.connect("Messages", "Notify via BRD-02")
    w.note("""## BRD-05 — напоминания
Каждый час (в :15) проверяет активные беседы в **brd_sessions**:
• нет ответа ≥ 24 ч → напоминание заказчику с последним вопросом (не больше 2 раз, только с 9:00 до 20:00 МСК);
• после 2 напоминаний и ≥ 72 ч тишины → сессия `abandoned`, сообщение заказчику и в чат команды.
Пороги задаются в **Config**.""", (-40, -300), w=620, h=230, color=2)
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
if (active.length) {
  lines.push('', 'Активные беседы:');
  for (const s of active.slice(0, 10)) lines.push(`• ${s.username || s.chat_id}: ответов ${s.turn_count}, последняя активность ${String(s.last_activity_at).slice(0, 16).replace('T', ' ')} UTC`);
}
lines.push('', `База знаний: документов в индексе ${kbOk.length}, фрагментов ${kbOk.reduce((x, r) => x + (Number(r.chunk_count) || 0), 0)}` + (kbErr.length ? `, с ошибкой ${kbErr.length} (${kbErr.map((r) => r.name).join(', ')})` : ''));
lines.push('', errors.length ? `Ошибки за сутки: ${errors.length} — ` + Object.entries(byWf).map(([k, v]) => `${k}: ${v}`).join('; ') : 'Ошибок за сутки нет.');
return [{ json: { audience: 'team', kind: 'report', title: 'Дайджест BRD-агента за сутки', text: lines.join('\\n'), source: 'digest' } }];
"""), (1100, 90))
    w.add("Notify via BRD-02", "n8n-nodes-base.executeWorkflow", 1.2, exec_wf(WF_NOTIFY), (1320, 90))
    w.connect("Daily 09:00", "Config")
    w.connect("Manual run", "Config")
    w.chain("Config", "Load sessions", "Load KB state", "Load errors", "Build digest", "Notify via BRD-02")
    w.note("""## BRD-06 — ежедневный дайджест (09:00 МСК)
Сводка за 24 ч в чат команды: начатые, завершённые и брошенные интервью, ссылки на новые BRD, активные беседы, состояние базы знаний, ошибки по воркфлоу (лист errors).""",
           (-40, -240), w=620, h=180, color=2)
    w.dump("BRD-06_daily-digest.json")


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
    build_kb_sync()
    build_interview()
    build_brd_generator()
    build_reminders()
    build_digest()
    build_setup()
