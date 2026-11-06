"""
Gear Pro gateway.

An OpenAI-compatible proxy that sits between the Gear desktop app and
OpenRouter. It is the server side of "Gear Pro": a subscriber gets a key,
the key carries a monthly budget, and every request is metered against it.

Endpoints the app calls (see src/brand.ts in the app):
  POST /v1/chat/completions         main model traffic (streaming or not)
  GET  /v1/models                   list of models the gateway will serve
  GET  /user/info                   budget for the key (LiteLLM-compatible shape)
  GET  /templates                   community templates (JSON array)
  POST /helpchat/v1/chat/completions   the in-app help bot
  POST /logs/generate-upload-url    bug-report log upload, then PUT /logs/put/<name>

Money:
  POST /stripe/webhook              subscription created / renewed / cancelled
  GET  /activate?session_id=...     page shown after Stripe Checkout with the key
  POST /admin/keys                  create a key by hand (X-Admin-Token)
  GET  /admin/keys                  list keys (X-Admin-Token)

Run locally:  python gateway.py   (reads .env next to this file)
Production:   gunicorn -k gthread -w 2 --threads 16 -b 127.0.0.1:5500 gateway:app --timeout 900
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, Response, abort, g, jsonify, request, stream_with_context

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- config
def _load_dotenv() -> None:
    env_path = HERE / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Gear <gear@catalyva.com>")
REPLY_TO = os.environ.get("REPLY_TO", "dan@danplants.com")
PRO_MONTHLY_BUDGET_USD = float(os.environ.get("PRO_MONTHLY_BUDGET_USD", "20"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://gear.167-172-142-0.sslip.io").rstrip("/")
SITE_URL = os.environ.get("SITE_URL", "https://danplants.com/gear")
DOWNLOAD_URL = os.environ.get("DOWNLOAD_URL", "https://danplants.com/gear#download")
DB_PATH = Path(os.environ.get("DB_PATH", str(HERE / "data" / "gateway.db")))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", str(HERE / "data" / "logs")))
MODELS_FILE = Path(os.environ.get("MODELS_FILE", str(HERE / "models.json")))
TEMPLATES_FILE = Path(os.environ.get("TEMPLATES_FILE", str(HERE / "templates.json")))
HELP_FREE_PER_DAY = int(os.environ.get("HELP_FREE_PER_DAY", "30"))
# Credits shown in the app = dollars * CONVERSION_RATIO (the app divides the same way).
CONVERSION_RATIO = 15

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
_db_lock = threading.Lock()


# ---------------------------------------------------------------- db
def db() -> sqlite3.Connection:
    conn = getattr(g, "_db", None)
    if conn is None:
        conn = g._db = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    return conn


@app.teardown_appcontext
def _close_db(_exc):
    conn = getattr(g, "_db", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS keys (
          key TEXT PRIMARY KEY,
          email TEXT,
          max_budget REAL NOT NULL,
          spend REAL NOT NULL DEFAULT 0,
          budget_reset_at TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          stripe_customer TEXT,
          stripe_subscription TEXT UNIQUE,
          note TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          key TEXT NOT NULL,
          model TEXT,
          prompt_tokens INTEGER,
          completion_tokens INTEGER,
          cost REAL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS help_quota (
          ip TEXT NOT NULL,
          day TEXT NOT NULL,
          count INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (ip, day)
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_reset(from_dt: datetime | None = None) -> str:
    base = from_dt or datetime.now(timezone.utc)
    return (base + timedelta(days=30)).isoformat()


def new_key() -> str:
    return "gear_" + secrets.token_hex(16)


def create_key(email: str | None, *, max_budget: float | None = None, stripe_customer=None,
               stripe_subscription=None, note=None, reset_at: str | None = None) -> sqlite3.Row:
    key = new_key()
    with _db_lock:
        db().execute(
            "INSERT INTO keys (key,email,max_budget,spend,budget_reset_at,active,stripe_customer,stripe_subscription,note,created_at)"
            " VALUES (?,?,?,0,?,1,?,?,?,?)",
            (key, email, max_budget if max_budget is not None else PRO_MONTHLY_BUDGET_USD,
             reset_at or next_reset(), stripe_customer, stripe_subscription, note, now_iso()),
        )
        db().commit()
    return db().execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()


def roll_budget_if_due(row: sqlite3.Row) -> sqlite3.Row:
    """Reset spend when the 30-day window has passed (Stripe renewals reset it too)."""
    try:
        reset_at = datetime.fromisoformat(row["budget_reset_at"])
    except Exception:
        reset_at = datetime.now(timezone.utc)
    if datetime.now(timezone.utc) >= reset_at:
        with _db_lock:
            db().execute("UPDATE keys SET spend=0, budget_reset_at=? WHERE key=?", (next_reset(), row["key"]))
            db().commit()
        row = db().execute("SELECT * FROM keys WHERE key=?", (row["key"],)).fetchone()
    return row


def bearer() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


def require_key() -> sqlite3.Row:
    token = bearer()
    if not token or token == "undefined":
        abort(_json_error(401, "Missing Gear Pro key. Add your key in Settings, or subscribe at " + SITE_URL))
    row = db().execute("SELECT * FROM keys WHERE key=?", (token,)).fetchone()
    if row is None:
        abort(_json_error(401, "Invalid Gear Pro key. Check Settings or subscribe at " + SITE_URL))
    if not row["active"]:
        abort(_json_error(402, "This Gear Pro subscription is not active. Manage it at " + SITE_URL))
    row = roll_budget_if_due(row)
    if row["spend"] >= row["max_budget"]:
        abort(_json_error(429, "You have used this month's Gear Pro credits. They reset on "
                               + row["budget_reset_at"][:10] + "."))
    return row


def _json_error(status: int, message: str):
    return Response(json.dumps({"error": {"message": message, "type": "gear_gateway", "code": status}}),
                    status=status, mimetype="application/json")


def add_spend(key: str, model: str, usage: dict | None) -> None:
    if not usage:
        return
    cost = float(usage.get("cost") or 0.0)
    if not cost:
        # OpenRouter sends cost when usage accounting is on; fall back to a token estimate.
        cost = (usage.get("prompt_tokens", 0) * 3 + usage.get("completion_tokens", 0) * 15) / 1_000_000
    with _db_lock:
        db().execute("UPDATE keys SET spend = spend + ? WHERE key=?", (cost, key))
        db().execute(
            "INSERT INTO requests (key,model,prompt_tokens,completion_tokens,cost,created_at) VALUES (?,?,?,?,?,?)",
            (key, model, usage.get("prompt_tokens"), usage.get("completion_tokens"), cost, now_iso()),
        )
        db().commit()


# ---------------------------------------------------------------- models
DEFAULT_MODELS = {
    "aliases": {
        "dyad/auto": "anthropic/claude-sonnet-4.5",
        "dyad/free": "qwen/qwen3-coder:free",
        "dyad/turbo": "x-ai/grok-code-fast-1",
        "dyad/value": "z-ai/glm-4.6",
        "dyad/gpt-5-codex": "openai/gpt-5-codex",
        "dyad/gpt-5": "openai/gpt-5",
        "dyad/gpt-5-mini": "openai/gpt-5-mini",
        "dyad/gpt-5-nano": "openai/gpt-5-nano",
        "anthropic/claude-sonnet-4-5-20250929": "anthropic/claude-sonnet-4.5",
        "anthropic/claude-sonnet-4-20250514": "anthropic/claude-sonnet-4",
        "anthropic/claude-3-7-sonnet-latest": "anthropic/claude-3.7-sonnet",
        "anthropic/claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
        "gemini/gemini-2.5-pro": "google/gemini-2.5-pro",
        "gemini/gemini-flash-latest": "google/gemini-2.5-flash",
        "gear-help": "google/gemini-2.5-flash",
    },
    "prefixes": {"openrouter/": "", "gemini/": "google/", "xai/": "x-ai/", "dyad/": "", "bedrock/": ""},
    "default_prefix": "openai/",
}


def load_models() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_MODELS))
    if MODELS_FILE.exists():
        try:
            user = json.loads(MODELS_FILE.read_text())
            cfg["aliases"].update(user.get("aliases", {}))
            cfg["prefixes"].update(user.get("prefixes", {}))
            cfg["default_prefix"] = user.get("default_prefix", cfg["default_prefix"])
        except Exception as e:  # pragma: no cover
            app.logger.warning("models.json unreadable: %s", e)
    return cfg


def resolve_model(name: str) -> str:
    cfg = load_models()
    if name in cfg["aliases"]:
        return cfg["aliases"][name]
    for prefix, replacement in cfg["prefixes"].items():
        if name.startswith(prefix):
            return replacement + name[len(prefix):]
    if "/" not in name:
        return cfg["default_prefix"] + name
    return name


# ---------------------------------------------------------------- prompt shaping
CODEBASE_PREFIX = "This is my codebase."


def format_files(files: list[dict]) -> str:
    parts = []
    for f in files or []:
        path = f.get("path") or f.get("filepath") or "unknown"
        content = f.get("content") or ""
        parts.append(f'<dyad-file path="{path}">\n{content}\n</dyad-file>\n\n')
    return "".join(parts)


def inject_codebase(messages: list[dict], opts: dict) -> list[dict]:
    """The app leaves the codebase out of the messages when a gateway is in use and
    sends the files separately. Put them back the way the app does in local mode."""
    files = opts.get("files") or []
    mentioned = opts.get("mentioned_apps") or []
    if not files and not mentioned:
        return messages
    extra: list[dict] = []
    if files:
        extra += [
            {"role": "user", "content": f"{CODEBASE_PREFIX} {format_files(files)}"},
            {"role": "assistant", "content": "OK, got it. I'm ready to help"},
        ]
    if mentioned:
        blocks = "".join(
            f"\n\n=== Referenced App: {m.get('appName') or m.get('app_name') or 'app'} ===\n{format_files(m.get('files') or [])}"
            for m in mentioned
        )
        extra += [
            {"role": "user", "content": "\n# Referenced Apps\n\nThese are the other apps that I've mentioned in my prompt. "
                                         "These other apps' codebases are READ-ONLY.\n" + blocks},
            {"role": "assistant", "content": "OK."},
        ]
    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].get("role") == "system":
        insert_at += 1
    return messages[:insert_at] + extra + messages[insert_at:]


ALLOWED_FIELDS = {
    "messages", "stream", "temperature", "max_tokens", "max_completion_tokens", "tools", "tool_choice",
    "stop", "top_p", "stream_options", "response_format", "seed", "frequency_penalty", "presence_penalty",
    "parallel_tool_calls",
}


def shape_request(body: dict, *, force_model: str | None = None) -> tuple[dict, str]:
    opts = body.get("dyad_options") or {}
    out = {k: v for k, v in body.items() if k in ALLOWED_FIELDS}
    requested = force_model or str(body.get("model") or "dyad/auto")
    model = resolve_model(requested)
    out["model"] = model
    out["messages"] = inject_codebase(list(body.get("messages") or []), opts)
    # Reasoning: the app sends provider-specific thinking fields; OpenRouter wants `reasoning`.
    if isinstance(body.get("thinking"), dict) and body["thinking"].get("type") == "enabled":
        budget = body["thinking"].get("budget_tokens")
        out["reasoning"] = {"max_tokens": int(budget)} if isinstance(budget, int) and budget > 0 else {"effort": "medium"}
    elif body.get("reasoning_effort"):
        out["reasoning"] = {"effort": body["reasoning_effort"]}
    if opts.get("enable_web_search"):
        out["plugins"] = [{"id": "web"}]
    out["usage"] = {"include": True}
    return out, requested


def openrouter_headers() -> dict:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": "Gear",
    }


def proxy_chat(body: dict, key_row: sqlite3.Row | None, *, force_model: str | None = None):
    if not OPENROUTER_API_KEY:
        return _json_error(500, "Gateway is missing OPENROUTER_API_KEY")
    payload, requested = shape_request(body, force_model=force_model)
    stream = bool(payload.get("stream"))
    try:
        upstream = requests.post(
            f"{OPENROUTER_BASE}/chat/completions", headers=openrouter_headers(), json=payload,
            stream=stream, timeout=(20, 900),
        )
    except requests.RequestException as e:
        return _json_error(502, f"Upstream model request failed: {e}")

    if upstream.status_code >= 400:
        text = upstream.text[:2000]
        try:
            msg = upstream.json().get("error", {}).get("message") or text
        except Exception:
            msg = text
        return _json_error(upstream.status_code, f"Model provider error ({payload['model']}): {msg}")

    key = key_row["key"] if key_row is not None else None

    if not stream:
        data = upstream.json()
        if key:
            add_spend(key, requested, data.get("usage"))
        return Response(json.dumps(data), status=200, mimetype="application/json")

    def generate():
        usage_seen = None
        try:
            for raw in upstream.iter_lines(decode_unicode=False):
                if raw is None:
                    continue
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                if line.startswith("data: ") and line[6:].strip() not in ("", "[DONE]"):
                    try:
                        chunk = json.loads(line[6:])
                        if chunk.get("usage"):
                            usage_seen = chunk["usage"]
                    except Exception:
                        pass
                yield (line + "\n").encode("utf-8")
        finally:
            upstream.close()
            if key:
                with app.app_context():
                    add_spend(key, requested, usage_seen)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), status=200, mimetype="text/event-stream", headers=headers)


# ---------------------------------------------------------------- routes: app traffic
@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "gear-gateway", "time": now_iso()})


@app.post("/v1/chat/completions")
def chat_completions():
    row = require_key()
    body = request.get_json(force=True, silent=True) or {}
    return proxy_chat(body, row)


@app.get("/v1/models")
def list_models():
    cfg = load_models()
    return jsonify({"object": "list", "data": [{"id": k, "object": "model", "owned_by": "gear"} for k in cfg["aliases"]]})


@app.get("/user/info")
def user_info():
    row = require_key()
    return jsonify({"user_info": {
        "spend": row["spend"],
        "max_budget": row["max_budget"],
        "budget_reset_at": row["budget_reset_at"],
        "email": row["email"],
    }})


@app.get("/templates")
def templates():
    if TEMPLATES_FILE.exists():
        try:
            return Response(TEMPLATES_FILE.read_text(), mimetype="application/json")
        except Exception:
            pass
    return jsonify([])


HELP_SYSTEM_PROMPT = (
    "You are the Gear help assistant. Gear is a desktop app that builds and edits web apps from plain-English "
    "prompts using AI. The code lives on the user's own computer under ~/gear-apps. Users can bring their own "
    "AI API keys (OpenAI, Anthropic, Google, OpenRouter, Ollama, LM Studio) or use a Gear Pro key, which gives them "
    "model access through the Gear gateway with a monthly credit budget. Apps can connect to Supabase with a "
    "personal access token, push to GitHub, and deploy to Vercel. Keep answers short, practical and friendly. "
    f"If something needs a human, point them to {REPLY_TO} or {SITE_URL}."
)


@app.post("/helpchat/v1/chat/completions")
def helpchat():
    token = bearer()
    row = db().execute("SELECT * FROM keys WHERE key=? AND active=1", (token,)).fetchone() if token else None
    if row is None:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with _db_lock:
            cur = db().execute("SELECT count FROM help_quota WHERE ip=? AND day=?", (ip, day)).fetchone()
            count = (cur["count"] if cur else 0) + 1
            db().execute("INSERT INTO help_quota (ip,day,count) VALUES (?,?,?) ON CONFLICT(ip,day) DO UPDATE SET count=?",
                         (ip, day, count, count))
            db().commit()
        if count > HELP_FREE_PER_DAY:
            return _json_error(429, "Help chat limit reached for today. Gear Pro members get unlimited help chat.")
    body = request.get_json(force=True, silent=True) or {}
    msgs = [m for m in body.get("messages", []) if m.get("role") != "system"]
    body["messages"] = [{"role": "system", "content": HELP_SYSTEM_PROMPT}] + msgs
    body.pop("tools", None)
    return proxy_chat(body, row, force_model="gear-help")


@app.post("/logs/generate-upload-url")
def logs_upload_url():
    name = f"{uuid.uuid4()}.json"
    return jsonify({"uploadUrl": f"{PUBLIC_URL}/logs/put/{name}", "filename": name})


@app.put("/logs/put/<name>")
def logs_put(name: str):
    if not name.endswith(".json") or "/" in name or ".." in name:
        abort(400)
    data = request.get_data(cache=False)
    if len(data) > 25 * 1024 * 1024:
        abort(413)
    (LOGS_DIR / name).write_bytes(data)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- routes: money
def send_key_email(email: str, row: sqlite3.Row) -> bool:
    if not RESEND_API_KEY or not email:
        return False
    deep_link = pro_deep_link(row)
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:560px;line-height:1.5">
      <h2 style="margin:0 0 12px">Your Gear Pro key</h2>
      <p>Thanks for joining. Here is your key:</p>
      <p style="font-family:Menlo,monospace;font-size:16px;background:#f3f1ec;padding:12px;border-radius:8px">{row['key']}</p>
      <p><b>1.</b> Install Gear: <a href="{DOWNLOAD_URL}">{DOWNLOAD_URL}</a><br>
         <b>2.</b> Open Gear and click this link to activate: <a href="{deep_link}">Activate Gear Pro</a><br>
         <b>3.</b> Or paste the key under Settings → AI Providers → Gear.</p>
      <p>Your plan includes ${row['max_budget']:.0f} of model usage per month (shown as {int(row['max_budget']*CONVERSION_RATIO)} credits in the app). It resets every month.</p>
      <p>Questions? Reply to this email.</p>
      <p>Dan</p>
    </div>"""
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
            json={"from": FROM_EMAIL, "to": [email], "reply_to": REPLY_TO, "subject": "Your Gear Pro key", "html": html},
            timeout=20,
        )
        return r.status_code < 300
    except requests.RequestException:
        return False


def pro_deep_link(row: sqlite3.Row) -> str:
    from urllib.parse import urlencode
    return "gear://gear-pro-return?" + urlencode({
        "key": row["key"], "max_budget": row["max_budget"], "budget_reset_at": row["budget_reset_at"],
    })


def _stripe():
    import stripe  # imported lazily so the gateway runs without Stripe configured
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def key_for_subscription(sub_id: str) -> sqlite3.Row | None:
    return db().execute("SELECT * FROM keys WHERE stripe_subscription=?", (sub_id,)).fetchone()


def period_end_iso(sub) -> str | None:
    try:
        end = sub["current_period_end"]
    except Exception:
        end = None
    if not end:
        try:
            end = sub["items"]["data"][0]["current_period_end"]
        except Exception:
            end = None
    return datetime.fromtimestamp(end, tz=timezone.utc).isoformat() if end else None


def provision_from_session(session) -> sqlite3.Row:
    sub_id = session.get("subscription")
    email = (session.get("customer_details") or {}).get("email") or session.get("customer_email")
    customer = session.get("customer")
    existing = key_for_subscription(sub_id) if sub_id else None
    if existing:
        return existing
    reset_at = None
    if sub_id and STRIPE_SECRET_KEY:
        try:
            reset_at = period_end_iso(_stripe().Subscription.retrieve(sub_id))
        except Exception:
            reset_at = None
    row = create_key(email, stripe_customer=customer, stripe_subscription=sub_id, note="stripe", reset_at=reset_at)
    send_key_email(email, row)
    return row


@app.post("/stripe/webhook")
def stripe_webhook():
    if not STRIPE_WEBHOOK_SECRET:
        return _json_error(500, "STRIPE_WEBHOOK_SECRET not set")
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(request.get_data(), request.headers.get("Stripe-Signature", ""), STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        return _json_error(400, f"bad signature: {e}")
    typ = event["type"]
    obj = event["data"]["object"]
    if typ == "checkout.session.completed" and obj.get("mode") == "subscription":
        provision_from_session(obj)
    elif typ == "invoice.paid":
        sub_id = obj.get("subscription")
        row = key_for_subscription(sub_id) if sub_id else None
        if row is not None:
            reset_at = None
            try:
                reset_at = period_end_iso(stripe.Subscription.retrieve(sub_id))
            except Exception:
                pass
            with _db_lock:
                db().execute("UPDATE keys SET spend=0, active=1, budget_reset_at=? WHERE key=?", (reset_at or next_reset(), row["key"]))
                db().commit()
    elif typ in ("customer.subscription.deleted", "customer.subscription.updated"):
        row = key_for_subscription(obj.get("id"))
        if row is not None:
            active = 1 if (typ == "customer.subscription.updated" and obj.get("status") in ("active", "trialing")) else 0
            with _db_lock:
                db().execute("UPDATE keys SET active=? WHERE key=?", (active, row["key"]))
                db().commit()
    return jsonify({"received": True})


ACTIVATE_HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gear Pro: you're in</title>
<style>body{margin:0;background:#1b1a17;color:#ede6d8;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;line-height:1.5}
.wrap{max-width:620px;margin:0 auto;padding:48px 20px}h1{font-size:34px;margin:0 0 8px}p{margin:10px 0}
.key{font-family:Menlo,monospace;font-size:17px;background:#2a2824;padding:14px;border-radius:10px;word-break:break-all;border:1px solid #3a3731}
.btn{display:inline-block;background:#d9822b;color:#1b1a17;font-weight:700;padding:14px 22px;border-radius:10px;text-decoration:none;margin:8px 8px 8px 0}
.btn.alt{background:transparent;color:#ede6d8;border:1px solid #6b665c}small{color:#a9a393}</style></head><body><div class="wrap">
<h1>You're in.</h1><p>This is your Gear Pro key. It was also emailed to <b>{email}</b>.</p>
<div class="key">{key}</div>
<p><a class="btn" href="{deep_link}">Activate in Gear</a> <a class="btn alt" href="{download}">Download Gear</a></p>
<p><b>No Gear yet?</b> Download it, open it, then click Activate above (or paste the key under Settings → AI Providers → Gear).</p>
<p><small>{budget_line}</small></p>
<p><small>Questions: {reply_to}</small></p></div></body></html>"""


@app.get("/activate")
def activate():
    session_id = request.args.get("session_id", "")
    if not session_id or not STRIPE_SECRET_KEY:
        return Response("<h1>Missing session</h1>", status=400, mimetype="text/html")
    try:
        session = _stripe().checkout.Session.retrieve(session_id)
    except Exception as e:
        return Response(f"<h1>Could not load your checkout session</h1><p>{e}</p>", status=400, mimetype="text/html")
    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return Response("<h1>Payment not completed yet</h1>", status=402, mimetype="text/html")
    row = provision_from_session(session)
    html = ACTIVATE_HTML.replace("{email}", str(row["email"] or "")).replace("{key}", row["key"]) \
        .replace("{deep_link}", pro_deep_link(row)).replace("{download}", DOWNLOAD_URL) \
        .replace("{budget_line}", f"${row['max_budget']:.0f} of model usage per month ({int(row['max_budget']*CONVERSION_RATIO)} credits), resets {row['budget_reset_at'][:10]}.") \
        .replace("{reply_to}", REPLY_TO)
    return Response(html, mimetype="text/html")


# ---------------------------------------------------------------- routes: admin
def require_admin() -> None:
    if not ADMIN_TOKEN or request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        abort(_json_error(403, "admin token required"))


@app.post("/admin/keys")
def admin_create_key():
    require_admin()
    body = request.get_json(force=True, silent=True) or {}
    row = create_key(body.get("email"), max_budget=body.get("max_budget"), note=body.get("note") or "manual")
    if body.get("send_email"):
        send_key_email(row["email"], row)
    return jsonify(dict(row))


@app.get("/admin/keys")
def admin_list_keys():
    require_admin()
    rows = db().execute("SELECT * FROM keys ORDER BY created_at DESC LIMIT 500").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/admin/keys/<key>/deactivate")
def admin_deactivate(key: str):
    require_admin()
    with _db_lock:
        db().execute("UPDATE keys SET active=0 WHERE key=?", (key,))
        db().commit()
    return jsonify({"ok": True})


@app.get("/admin/usage")
def admin_usage():
    require_admin()
    rows = db().execute(
        "SELECT key, COUNT(*) AS requests, COALESCE(SUM(cost),0) AS cost, MAX(created_at) AS last_seen"
        " FROM requests GROUP BY key ORDER BY cost DESC LIMIT 200").fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5500"))
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
