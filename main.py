# -*- coding: utf-8 -*-
"""BTC 阈值监控：读 config.json -> 取 Binance 现价 -> 上涨/下跌阈值达成且不在冷却期 -> 飞书单聊提醒。
只用 Python 标准库，无第三方依赖。up=价格涨到>=该值提醒，down=价格跌到<=该值提醒。"""
import json, os, time, urllib.request

BASE = "https://open.feishu.cn/open-apis"
STATE = "last_trigger.json"


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "price-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_post(url, token, payload, timeout=15):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        **({"Authorization": "Bearer " + token} if token else {}),
    })
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


def main():
    cfg = json.load(open("config.json", encoding="utf-8"))
    symbol = cfg["symbol"]
    price = get_price(symbol)
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    cooldown = int(cfg.get("cooldown_hours", 24)) * 3600
    now = time.time()
    t8 = time.strftime("%m-%d %H:%M", time.gmtime(now + 8 * 3600))  # 北京时间
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
