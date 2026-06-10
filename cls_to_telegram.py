#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财联社电报 → Telegram 频道 推送脚本
跑在 GitHub Actions 上，每 5 分钟拉一次最新电报，只推送上次之后的新内容。
状态(last_ctime)存到 state.json 并提交回仓库，保证不重复、不漏推。
"""

import os
import sys
import json
import time
import hashlib
import requests
from datetime import datetime, timezone, timedelta

# ====== 配置（通过 GitHub Secrets 注入）======
BOT_TOKEN = os.environ["TG_BOT_TOKEN"].strip()        # @BotFather 给的 token
CHAT_ID   = os.environ["TG_CHAT_ID"].strip()          # 频道，如 @my_cls_channel 或 -100xxxxxxxxxx

STATE_FILE = "state.json"
FETCH_COUNT = 30                                       # 每次拉多少条（够覆盖5分钟的量）
BEIJING_TZ = timezone(timedelta(hours=8))

# ====== 财联社 API ======
V1_URL = "https://www.cls.cn/v1/roll/get_roll_list"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Referer": "https://www.cls.cn/telegraph",
    "Accept": "application/json, text/plain, */*",
}
# 标红关键词：命中则在推送里加 🔴 标记，方便你做超短线快速扫描
RED_KEYWORDS = ["利好", "利空", "重要", "突发", "紧急", "涨停", "跌停", "大涨", "大跌", "突破"]


def make_sign(params: dict) -> str:
    """参数按 key 排序拼接 → SHA1 → MD5"""
    raw = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sha1 = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1.encode("utf-8")).hexdigest()


def fetch_latest(count=FETCH_COUNT):
    """拉取最新电报列表"""
    params = {
        "app": "CailianpressWeb",
        "os": "web",
        "sv": "8.4.6",            # ★ 接口失效时，多半是这个版本号过期，去 cls.cn 抓最新值替换
        "refresh_type": "1",
        "rn": str(count),
        "last_time": str(int(time.time())),
        "category": "",
    }
    params["sign"] = make_sign(params)
    for attempt in range(3):
        try:
            r = requests.get(V1_URL, params=params, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
            if data.get("errno") not in (None, 0, "0"):
                print(f"[warn] API 返回错误: {data.get('errno')} {data.get('error')}")
                return []
            return data.get("data", {}).get("roll_data", []) or []
        except Exception as e:
            print(f"[warn] 拉取失败(第{attempt+1}次): {e}")
            time.sleep(5)
    return []


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def build_message(item):
    ctime = int(item.get("ctime", 0))
    hhmm = datetime.fromtimestamp(ctime, BEIJING_TZ).strftime("%H:%M") if ctime else ""
    title = (item.get("title") or "").strip()
    content = (item.get("content") or item.get("brief") or "").strip()
    body = content or title
    text = f"{title}\n{content}".strip()
    is_red = bool(item.get("is_ad") is False) and any(k in text for k in RED_KEYWORDS)
    flag = "🔴 " if is_red else "📰 "
    item_id = item.get("id")
    url = f"https://www.cls.cn/detail/{item_id}" if item_id else ""
    head = f"{flag}[{hhmm}] 财联社电报" if hhmm else f"{flag}财联社电报"
    msg = f"{head}\n\n{body}"
    if url:
        msg += f"\n\n{url}"
    return msg


def send_to_telegram(text):
    api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(api, data=payload, timeout=15)
    if r.status_code != 200:
        print(f"[warn] Telegram 推送失败 {r.status_code}: {r.text[:200]}")
    return r.status_code == 200


def main():
    state = load_state()
    last_ctime = int(state.get("last_ctime", 0))

    items = fetch_latest()
    if not items:
        print("[info] 未获取到电报（可能被风控或接口版本过期）")
        sys.exit(0)

    # 首次运行：不刷屏历史，只记录当前最新时间点，下一轮起开始推
    if last_ctime == 0:
        newest = max(int(i.get("ctime", 0)) for i in items)
        save_state({"last_ctime": newest})
        print(f"[info] 首次初始化，基准时间 = {newest}，本轮不推送历史。")
        return

    # 只保留比上次更新的，按时间从旧到新发送，保证频道里顺序正确
    new_items = [i for i in items if int(i.get("ctime", 0)) > last_ctime]
    new_items.sort(key=lambda i: int(i.get("ctime", 0)))

    if not new_items:
        print("[info] 无新电报。")
        return

    pushed_max = last_ctime
    sent = 0
    for it in new_items:
        if it.get("is_ad"):       # 跳过广告
            continue
        if send_to_telegram(build_message(it)):
            sent += 1
            pushed_max = max(pushed_max, int(it.get("ctime", 0)))
            time.sleep(1.2)       # 避开 Telegram 频率限制
        else:
            break                 # 推送失败就停，下轮重试，避免漏推

    if pushed_max > last_ctime:
        save_state({"last_ctime": pushed_max})
    print(f"[info] 本轮推送 {sent} 条，last_ctime 更新为 {pushed_max}")


if __name__ == "__main__":
    main()
