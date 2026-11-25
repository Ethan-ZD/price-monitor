# -*- coding: utf-8 -*-
"""BTC 阈值监控 + 飞书单聊指令改配置。
每次运行：读单聊指令（改 config）-> 取价 -> 判断阈值 -> 推飞书提醒。
纯 Python 标准库。指令格式：上轨 78000 / 下轨 52000 51000 / 冷却 12
config.json 里的 chat_id = 你和机器人单聊的 chat_id，不要动。"""
import json, os, re, time, urllib.request

BASE = "https://open.feishu.cn/open-apis"
STATE = "last_trigger.json"
CMD_STATE = "cmd_state.json"


def http_get(url, token=None, timeout=15):
    headers = {"User-Agent": "price-monitor/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_post(url, token, payload, timeout=15):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "price-monitor/1.0"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_feishu_token():
    r = http_post(BASE + "/auth/v3/tenant_access_token/internal", None, {
        "app_id": os.environ["FEISHU_APP_ID"],
        "app_secret": os.environ["FEISHU_APP_SECRET"]})
    return r["tenant_access_token"]


def get_price(symbol):
    for host in ("api.binance.com", "data-api.binance.vision"):
        try:
            d = http_get(f"https://{host}/api/v3/ticker/price?symbol={symbol}", timeout=8)
            return float(d["price"])
        except Exception:
            continue
    raise RuntimeError("币价获取失败：Binance 接口不可达，请在 Actions 日志查看")


def send_feishu(text):
    token = get_feishu_token()
    r = http_post(f"{BASE}/im/v1/messages?receive_id_type=open_id", token, {
        "receive_id": os.environ["FEISHU_OPEN_ID"],
        "msg_type": "text",
        "content": json.dumps({"text": text})})
    if r.get("code") != 0:
        raise RuntimeError(f"飞书发送失败 code={r.get('code')} msg={r.get('msg')}")


def load_json(name, default):
    try:
        return json.load(open(name, encoding="utf-8"))
    except Exception:
        return default


# ---------------- 指令处理（读单聊消息，改 config.json）----------------

def apply_command(cfg, text):
    """识别指令并改 cfg。返回改动说明；不是指令返回 None。"""
    m = re.search(r"上轨\s*([\d,，\s]+)", text)
    if m:
        cfg["up"] = [int(x) for x in re.findall(r"\d+", m.group(1))]
        return "上轨 up = " + str(cfg["up"])
    m = re.search(r"下轨\s*([\d,，\s]+)", text)
    if m:
        cfg["down"] = [int(x) for x in re.findall(r"\d+", m.group(1))]
        return "下轨 down = " + str(cfg["down"])
    m = re.search(r"冷却\s*(\d+)", text)
    if m:
        cfg["cooldown_hours"] = int(m.group(1))
        return "冷却 = " + m.group(1) + " 小时"
    return None


def process_commands(cfg):
    """读单聊里你发的新指令，改 config 并回复确认。异常不影响主监控。"""
    chat_id = cfg.get("chat_id")
    if not chat_id:
        print("指令：config.json 缺 chat_id，跳过")
        return
    token = get_feishu_token()
    open_id = os.environ["FEISHU_OPEN_ID"]
    d = http_get(f"{BASE}/im/v1/messages?container_id_type=chat&container_id={chat_id}"
                 f"&max=20&sort_type=ByCreateTimeDesc", token)
    if d.get("code") != 0:
        print(f"指令：读单聊失败 code={d.get('code')}，跳过（检查 im:message.p2p_msg 权限）")
        return
    cmd_state = load_json(CMD_STATE, {})
    done = set(cmd_state.get("done", []))
    applied = 0
    for m in d.get("data", {}).get("items", []):
        mid = m.get("message_id") or ("t" + str(m.get("create_time")))
        if mid in done:
            continue
        if m.get("msg_type") != "text":
            continue
        # 只处理你本人发的：sender.id 是字符串，直接和 open_id 比
        sender_id = (m.get("sender") or {}).get("id", "")
        if sender_id != open_id:
            done.add(mid)
            continue
        raw = m.get("content") or (m.get("body") or {}).get("content") or "{}"
        try:
            text = json.loads(raw).get("text", "").strip()
        except Exception:
            text = ""
        done.add(mid)
        if not text:
            continue
        changed = apply_command(cfg, text)
        if changed:
            json.dump(cfg, open("config.json", "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            send_feishu("✅ 已按指令修改：\n" + changed + "\n下个 5 分钟周期生效")
            print("指令：\"" + text + "\" -> " + changed)
            applied += 1
    cmd_state["done"] = list(done)[-100:]
    json.dump(cmd_state, open(CMD_STATE, "w", encoding="utf-8"), indent=1)
    print(f"指令：扫 {len(d.get('data', {}).get('items', []))} 条，应用 {applied} 条")


# ---------------- 主流程 ----------------

def main():
    cfg = json.load(open("config.json", encoding="utf-8"))
    try:
        process_commands(cfg)
    except Exception as e:
        print(f"指令模块异常（不影响监控）: {e}")

    symbol = cfg["symbol"]
    price = get_price(symbol)
    state = load_json(STATE, {})
    cooldown = int(cfg.get("cooldown_hours", 24)) * 3600
    now = time.time()
    t8 = time.strftime("%m-%d %H:%M", time.gmtime(now + 8 * 3600))
    alerts = 0
    for label in ("up", "down"):
        verb = "已涨到" if label == "up" else "已跌到"
        for L in cfg.get(label, []):
            L = float(L)
            key = f"{label}_{int(L)}"
            hit = (price >= L) if label == "up" else (price <= L)
            if hit and now - state.get(key, {}).get("ts", 0) >= cooldown:
                send_feishu(f"⚠ BTC{verb} {L:,.0f} USDT\n现价 {price:,.2f}\n{t8}（北京时间）\n阈值可在 config.json 修改")
                state[key] = {"ts": now, "price": price}
                alerts += 1
                print(f"{label} {L:,.0f}: {price:,.2f} 命中 -> 已发送")
            else:
                print(f"{label} {L:,.0f}: {price:,.2f} 未命中" if not hit else f"{label} {L:,.0f}: 冷却中，跳过")
    json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1)
    print(f"完成：{alerts} 条提醒")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}")
        raise SystemExit(1)
