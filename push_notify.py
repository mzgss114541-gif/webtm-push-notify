# -*- coding: utf-8 -*-
"""
贴吧命中推送插件 for WebTiebaManager
=====================================
- monkey-patch Processer.process：WTM 命中规则的那一刻，立即推送
- 每个账户完全独立配置：
    * 微信通道（Server酱 SendKey）— 可选
    * 邮件通道（SMTP：发件邮箱/授权码/服务器 + 收件邮箱）— 可选，供应商可不同
- 管理页面 /push-notify：网页配置，无需改代码/重启
- 不改 WTM 核心源码（运行期包装）

推送内容：贴吧名、帖子标题、内容摘要、命中规则、帖子链接、时间
"""
from __future__ import annotations

import asyncio
import json
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any

import aiohttp
from fastapi.responses import HTMLResponse

from src.api.server import BaseResponse, app
from src.core.constants import BASE_DIR
from src.process import Processer
from src.utils.logging import system_logger

# ---------------------------------------------------------------------------
# 配置存储：plugin_data/push_notify/config.json
# {
#   "accounts": {
#     "账户A": {
#       "sct": "SCTxxx",                 // 微信 SendKey（可选）
#       "smtp_host": "smtp.qq.com",      // 邮件（可选，可留空=该账户不发邮件）
#       "smtp_port": "465",
#       "smtp_user": "example@qq.com",
#       "smtp_code": "授权码",
#       "to": "example@qq.com"           // 收件邮箱
#     },
#     "账户B": { ... }                    // 完全独立，可用不同供应商
#   }
# }
# ---------------------------------------------------------------------------
CONFIG_FILE = BASE_DIR / "plugin_data" / "push_notify" / "config.json"

# 只推送命中真实规则（非白名单）的记录；设为 True 则白名单也推
PUSH_WHITELIST = False
# 推送内容最大长度（字符）
MAX_TEXT_LEN = 200

SERVERCHAN_URL = "https://sctapi.ftqq.com/{key}.send"


def _load_config() -> dict[str, Any]:
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        system_logger.error(f"[push_notify] 读取配置失败: {e}")
    return {}


def _load_accounts_cfg() -> dict[str, dict[str, str]]:
    """读取 账户名 -> 完整配置 dict"""
    cfg = _load_config()
    acc = cfg.get("accounts", {})
    if not isinstance(acc, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for name, val in acc.items():
        if isinstance(val, dict):
            result[str(name)] = {str(k): str(v) for k, v in val.items() if v}
    return result


def _save_config(cfg: dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


async def _send_serverchan(send_key: str, title: str, desp: str) -> bool:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(
                SERVERCHAN_URL.format(key=send_key),
                data={"title": title, "desp": desp},
            ) as resp:
                body = await resp.text()
                if resp.status == 200:
                    system_logger.success(f"[push_notify] 微信已推送: {title[:30]}")
                    return True
                system_logger.error(f"[push_notify] 微信推送失败 HTTP {resp.status}: {body[:150]}")
                return False
    except Exception as e:
        system_logger.error(f"[push_notify] 微信推送异常: {e}")
        return False


def _send_mail_sync(acct: dict[str, str], subject: str, body: str) -> str:
    """用账户自己的 SMTP 发送，返回错误信息（空=成功）"""
    host = acct.get("smtp_host", "")
    port = int(acct.get("smtp_port") or 465)
    user = acct.get("smtp_user", "")
    code = acct.get("smtp_code", "")
    to_addr = acct.get("to", "")
    if not (host and user and code and to_addr):
        return "账户邮件配置不完整"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr((str(Header("贴吧监控", "utf-8")), user))
        msg["To"] = to_addr
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
        server.login(user, code)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()
        return ""
    except Exception as e:
        return str(e)


async def _push_hit(user: str, rule_name: str, is_whitelist: bool, obj: Any) -> None:
    """构造消息并推送（实时读取配置）"""
    if is_whitelist and not PUSH_WHITELIST:
        return

    content = obj.content
    fname = getattr(content, "fname", "?")
    title = getattr(content, "title", "") or ""
    text = (getattr(content, "text", "") or "")[:MAX_TEXT_LEN]
    pid = getattr(content, "pid", "")

    msg_title = f"⚠️ 贴吧命中: {fname}"
    desp = f"贴吧: {fname}\n"
    desp += f"规则: {rule_name}\n"
    if title:
        desp += f"标题: {title}\n"
    if text:
        desp += f"内容: {text}\n"
    desp += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    if pid:
        desp += f"链接: http://tieba.baidu.com/p/{pid}"

    system_logger.info(f"[push_notify] 命中: user={user} rule={rule_name} fname={fname}")

    acct = _load_accounts_cfg().get(user)
    if not acct:
        return

    # 微信通道（该账户自己的 SendKey）
    if acct.get("sct"):
        sc_desp = (
            f"**贴吧**: {fname}\n**规则**: {rule_name}\n"
            + (f"**标题**: {title}\n" if title else "")
            + (f"**内容**: {text}\n" if text else "")
            + f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            + (f"**链接**: http://tieba.baidu.com/p/{pid}" if pid else "")
        )
        asyncio.get_event_loop().create_task(_send_serverchan(acct["sct"], msg_title, sc_desp))

    # 邮件通道（该账户自己的 SMTP + 收件邮箱）
    if acct.get("smtp_host") and acct.get("to"):
        asyncio.get_event_loop().create_task(
            asyncio.to_thread(_send_mail_sync, acct, msg_title, desp)
        )


# ---------------------------------------------------------------------------
# monkey-patch：包装 Processer.process，命中时推送
# ---------------------------------------------------------------------------
_original_process = Processer.process


async def _patched_process(self, obj: Any):
    result_rule = await _original_process(self, obj)
    if result_rule is not None:
        user = getattr(self.config, "user", None)
        username = getattr(user, "username", None) or getattr(self.config, "username", None) or "unknown"
        await _push_hit(username, result_rule.name, result_rule.whitelist, obj)
    return result_rule


Processer.process = _patched_process


# ---------------------------------------------------------------------------
# 管理页面 /push-notify
# ---------------------------------------------------------------------------
PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>推送设置</title>
<style>
:root{--bg:#f5f7fa;--card:#fff;--t1:#303133;--t2:#606266;--t3:#909399;--bd:#dcdfe6;--pri:#409eff;--dng:#f56c6c;--suc:#67c23a;--war:#e6a23c;--rad:8px}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Helvetica Neue",Helvetica,"PingFang SC","Microsoft YaHei",Arial,sans-serif;background:var(--bg);color:var(--t1);line-height:1.6}
.app{max-width:860px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;font-weight:600;margin-bottom:4px}
h2{font-size:16px;font-weight:600;margin-bottom:16px}
.card{background:var(--card);border-radius:var(--rad);padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06);border:1px solid var(--bd)}
.row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px}
.col{flex:1;min-width:180px}
label{display:block;font-size:13px;color:var(--t2);margin-bottom:4px;font-weight:500}
input{width:100%;padding:8px 12px;border:1px solid var(--bd);border-radius:6px;font-size:14px;font-family:inherit;color:var(--t1);background:#fff;outline:none}
input:focus{border-color:var(--pri)}
button{padding:8px 20px;border:none;border-radius:6px;font-size:14px;font-weight:500;cursor:pointer;transition:opacity .2s}
.btn-pri{background:var(--pri);color:#fff}.btn-pri:hover{opacity:.85}
.btn-suc{background:var(--suc);color:#fff}.btn-suc:hover{opacity:.85}
.btn-dng{background:var(--dng);color:#fff}.btn-dng:hover{opacity:.85}
.btn-out{background:#fff;color:var(--t1);border:1px solid var(--bd)}.btn-out:hover{border-color:var(--pri);color:var(--pri)}
.btn-sm{padding:4px 12px;font-size:12px}
.tag-inline{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;margin:2px}
.tag-info{background:#ecf5ff;color:var(--pri)}.tag-suc{background:#f0f9eb;color:var(--suc)}.tag-dng{background:#fef0f0;color:var(--dng)}
.toast{position:fixed;top:16px;right:16px;padding:10px 20px;border-radius:6px;font-size:14px;z-index:999;display:none;box-shadow:0 4px 12px rgba(0,0,0,.15)}
.toast.ok{background:#f0f9eb;color:var(--suc);border:1px solid #c2e7b0;display:block}
.toast.err{background:#fef0f0;color:var(--dng);border:1px solid #fbc4c4;display:block}
.hint{font-size:12px;color:var(--t3);margin-top:4px}
.acct{background:#fafbfc;border:1px solid var(--bd);border-radius:8px;padding:14px;margin-bottom:12px}
.acct h3{font-size:14px;font-weight:600;margin-bottom:10px;color:var(--t1)}
.acct .sec{font-size:12px;color:var(--t3);margin:8px 0 6px;font-weight:500}
</style>
</head>
<body>
<div class="app">
<h1>推送设置</h1>
<div style="color:var(--t3);font-size:13px;margin-bottom:20px">
  贴吧命中敏感词规则时推送通知。<b>每个账户独立配置</b>：微信（Server酱）和邮件（SMTP，可用不同邮箱供应商）都可选。
</div>

<div id="accounts"></div>

<div class="card">
  <div class="row">
    <div><label>&nbsp;</label><button class="btn-suc" id="btn-save" onclick="saveAll()">保存全部</button></div>
  </div>
</div>
</div>
<div id="toast" class="toast"></div>
<script>
var B="/api/plugin/push-notify";
function toast(m,c){var e=document.getElementById("toast");e.textContent=m;e.className="toast "+c;setTimeout(function(){e.className="toast"},3000)}
function api(m,p,b){var o={method:m,headers:{}};if(b){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(b)}return fetch(B+p,o).then(function(r){return r.json().then(function(d){if(!r.ok)throw new Error(d.detail||r.statusText);return d})})}
function esc(s){if(typeof s!=="string")return"";return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}
function acctCard(name,c){var d=document.createElement("div");d.className="acct";d.id="acct-"+name;
var h='<h3>账户: '+esc(name)+'</h3>';
h+='<div class="sec">微信推送（Server酱，可选）</div>';
h+='<div class="row"><div class="col"><label>SendKey（sct.ftqq.com 获取）</label><input class="f-sct" value="'+esc(c.sct||"")+'"></div></div>';
h+='<div class="sec">邮件推送（SMTP，可选，可留空）</div>';
h+='<div class="row"><div class="col"><label>SMTP 服务器</label><input class="f-host" placeholder="如 smtp.qq.com" value="'+esc(c.smtp_host||"")+'"></div><div class="col" style="max-width:100px"><label>端口</label><input class="f-port" placeholder="465" value="'+esc(c.smtp_port||"")+'"></div></div>';
h+='<div class="row"><div class="col"><label>发件邮箱</label><input class="f-user" value="'+esc(c.smtp_user||"")+'"></div><div class="col"><label>SMTP 授权码</label><input class="f-code" type="password" value="'+esc(c.smtp_code||"")+'"></div></div>';
h+='<div class="row"><div class="col"><label>收件邮箱</label><input class="f-to" value="'+esc(c.to||"")+'"></div><div><label>&nbsp;</label><button class="btn-out btn-sm" onclick="testAcct(this.getAttribute(&quot;data-u&quot;))" data-u="'+esc(name)+'">测试此账户</button></div></div>';
d.innerHTML=h;document.getElementById("accounts").appendChild(d)}
function loadConfig(){api("GET","/config").then(function(d){var a=d.data.accounts||{};document.getElementById("accounts").innerHTML="";Object.keys(a).forEach(function(n){acctCard(n,a[n]||{})})}).catch(function(e){toast(e.message,"err")})}
function saveAll(){var b=document.getElementById("btn-save");b.disabled=true;b.textContent="保存中...";var obj={accounts:{}};document.querySelectorAll(".acct").forEach(function(c){var n=c.id.replace("acct-","");obj.accounts[n]={sct:c.querySelector(".f-sct").value.trim(),smtp_host:c.querySelector(".f-host").value.trim(),smtp_port:c.querySelector(".f-port").value.trim(),smtp_user:c.querySelector(".f-user").value.trim(),smtp_code:c.querySelector(".f-code").value.trim(),to:c.querySelector(".f-to").value.trim()}});api("PUT","/config",obj).then(function(){toast("已保存","ok");loadConfig()}).catch(function(e){toast(e.message,"err")}).finally(function(){b.disabled=false;b.textContent="保存全部"})}
function testAcct(n){var c=document.getElementById("acct-"+n);var cfg={sct:c.querySelector(".f-sct").value.trim(),smtp_host:c.querySelector(".f-host").value.trim(),smtp_port:c.querySelector(".f-port").value.trim(),smtp_user:c.querySelector(".f-user").value.trim(),smtp_code:c.querySelector(".f-code").value.trim(),to:c.querySelector(".f-to").value.trim()};api("POST","/test",{user:n,config:cfg}).then(function(d){var r=d.data;var msg=[];if(r.wechat)msg.push("微信OK");if(r.mail===true)msg.push("邮件OK");else if(typeof r.mail==="string")msg.push("邮件失败:"+r.mail);toast(msg.length?msg.join(" "):"未配置任何通道","ok")}).catch(function(e){toast(e.message,"err")})}
loadConfig();
</script>
</body>
</html>"""


@app.get("/push-notify", response_class=HTMLResponse)
async def push_page():
    return PAGE


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/plugin/push-notify", tags=["push-notify"])


@router.get("/config")
async def get_config() -> BaseResponse:
    """返回 WTM 账户（联动）+ 每账户独立配置"""
    saved = _load_accounts_cfg()
    user_dir = BASE_DIR / "users"
    accounts: dict[str, dict[str, str]] = {}
    if user_dir.exists():
        for d in sorted(user_dir.iterdir()):
            if d.is_dir() and (d / "config.yaml").exists():
                accounts[d.stem] = saved.get(d.stem, {})
    for name, cfg in saved.items():
        accounts.setdefault(name, cfg)
    return BaseResponse(data={"accounts": accounts})


@router.put("/config")
async def put_config(req: dict) -> BaseResponse:
    cfg = _load_config()
    accounts: dict[str, dict[str, str]] = {}
    for name, val in (req.get("accounts") or {}).items():
        if not isinstance(val, dict):
            continue
        clean = {str(k): str(v) for k, v in val.items() if v}
        if not clean:
            continue
        accounts[str(name)] = clean
    cfg["accounts"] = accounts
    _save_config(cfg)
    system_logger.info(f"[push_notify] 配置已更新: {list(accounts.keys())}")
    return BaseResponse(data={"accounts": accounts})


class TestReq(BaseModel):
    user: str
    config: dict[str, str] = {}


@router.post("/test")
async def test_push(req: TestReq) -> BaseResponse:
    """测试某账户的推送：微信 + 邮件 都测"""
    acct = req.config or _load_accounts_cfg().get(req.user, {})
    result: dict[str, Any] = {"wechat": False, "mail": False}
    # 微信
    sct = acct.get("sct", "")
    if sct:
        title = "✅ 推送测试: " + req.user
        desp = "如果你收到这条消息，说明 Server酱 推送配置正常。\n\n(由 WTM 推送插件测试页发送)"
        result["wechat"] = await _send_serverchan(sct, title, desp)
    # 邮件
    if acct.get("smtp_host") and acct.get("to"):
        subject = "✅ 邮件推送测试: " + req.user
        body = "如果你收到这封邮件，说明 SMTP 邮箱推送配置正常。\n\n(由 WTM 推送插件测试页发送)"
        result["mail"] = await asyncio.to_thread(_send_mail_sync, acct, subject, body) or True
    return BaseResponse(data=result)


app.include_router(router)
system_logger.info("[push_notify] 已挂钩 Processer.process，管理页: /push-notify")
