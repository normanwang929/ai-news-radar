#!/usr/bin/env python3
"""
知乎采集器 — 通过知乎热榜 + 搜索API获取内容。

用法:
    python scripts/collect_zhihu.py                          # 热榜+搜索
    python scripts/collect_zhihu.py --hot-only               # 仅热榜
    python scripts/collect_zhihu.py --search "AI 大模型"     # 自定义搜索

环境变量:
    ZHIHU_COOKIE   — 知乎登录Cookie（含__zse_ck）
"""

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

TRENDSCAN_HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(TRENDSCAN_HOME, "data", "raw.db")
sys.path.insert(0, TRENDSCAN_HOME)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.zhihu.com/",
}

try:
    import requests as req
except ImportError:
    import urllib.request as req_urllib
    import urllib.error

    def _get(url, headers=None, timeout=15):
        r = req_urllib.Request(url, headers=headers or HEADERS)
        try:
            with req_urllib.urlopen(r, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return {"error": str(e)}

    def get(url, headers=None, timeout=15):
        return _get(url, headers, timeout)
else:

    def get(url, headers=None, timeout=15):
        try:
            r = req.get(url, headers=headers or HEADERS, timeout=timeout)
            return r.json()
        except Exception as e:
            return {"error": str(e)}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_cookie():
    """获取知乎Cookie：优先环境变量，其次env文件"""
    cookie = os.environ.get("ZHIHU_COOKIE", "")
    if cookie:
        return cookie
    # 回退到env文件
    env_path = os.path.join(os.path.dirname(TRENDSCAN_HOME), "AppData", "Local", "hermes", "scripts", "zhihu_cookie.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("export ZHIHU_COOKIE="):
                    cookie = line.split("=", 1)[1].strip().strip("'\"")
                    return cookie
    return ""


def get_headers():
    """返回带Cookie的请求头"""
    h = HEADERS.copy()
    cookie = get_cookie()
    if cookie:
        h["Cookie"] = cookie
    return h


def fetch_hot(conn, limit=20):
    """采集知乎热榜"""
    headers = get_headers()
    data = get("https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=%d" % limit, headers)
    
    if "error" in data:
        print(f"  ❌ 热榜: {data.get('error')}")
        return 0
    if "data" not in data:
        print(f"  ❌ 热榜: 需要Cookie（设置 ZHIHU_COOKIE）")
        return 0

    items = data.get("data", [])
    inserted = 0
    cursor = conn.cursor()

    for item in items:
        target = item.get("target", {})
        title = target.get("title", "")
        url = target.get("url", "")
        # 转换知乎内部URL为公开链接
        qid = url.split("/")[-1] if "/" in url else ""
        public_url = f"https://www.zhihu.com/question/{qid}" if qid else url

        excerpt = target.get("excerpt", "")[:300]
        answer_count = target.get("answer_count", 0)
        follower_count = target.get("follower_count", 0)
        summary = f"[{answer_count}回答/{follower_count}关注] {excerpt}"[:500]

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO articles
                    (source_name, source_priority, cluster, title, url,
                     summary, published, collected, lang, source_type,
                     quality_score, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)
            """, (
                "知乎热榜",
                "important",
                "18",
                title[:300],
                public_url,
                summary,
                "",  # 未知发布日期
                "ZH",
                "zhihu",
                0.8,
                "社区热议",
            ))
            if cursor.rowcount > 0:
                inserted += 1
        except Exception:
            pass

    conn.commit()
    print(f"  ✅ 热榜: {inserted} 条")
    return inserted


def fetch_search(conn, queries=None):
    """搜索知乎内容"""
    queries = queries or ["AI", "大模型", "人工智能", "机器人"]
    headers = get_headers()
    total = 0
    cursor = conn.cursor()

    for q in queries:
        time.sleep(1.5)
        data = get(
            "https://www.zhihu.com/api/v4/search_v3?t=general&q=%s&limit=10" % q,
            headers,
        )
        if "data" not in data:
            print(f"  ❌ 搜索\"{q}\": no data (need cookie?)")
            continue

        for item in data.get("data", []):
            obj = item.get("object", {})
            title = obj.get("title", "")
            # 清理<em>标签
            title = title.replace("<em>", "").replace("</em>", "")
            if not title:
                continue
            url = obj.get("url", "")
            excerpt = obj.get("excerpt", "")[:300]

            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO articles
                        (source_name, source_priority, cluster, title, url,
                         summary, published, collected, lang, source_type,
                         quality_score, category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?)
                """, (
                    f"知乎搜索:{q}",
                    "important",
                    "18",
                    title[:300],
                    url[:500],
                    excerpt,
                    "",  # 未知日期
                    "ZH",
                    "zhihu",
                    0.6,
                    "社区热议",
                ))
                if cursor.rowcount > 0:
                    total += 1
            except Exception:
                pass

        time.sleep(1.0)

    conn.commit()
    print(f"  ✅ 搜索: {total} 条")
    return total


def main():
    import argparse
    parser = argparse.ArgumentParser(description="知乎采集器")
    parser.add_argument("--hot-only", action="store_true", help="仅热榜")
    parser.add_argument("--search", type=str, help="搜索词（逗号分隔）")
    args = parser.parse_args()

    if not os.environ.get("ZHIHU_COOKIE"):
        print("⚠️  需要设置 ZHIHU_COOKIE 环境变量（浏览器F12从zhihu.com复制Cookie）")
        print("   格式: export ZHIHU_COOKIE='__zse_ck=xxx; ...'")
        print()

    conn = get_connection()
    total = 0

    print("📕 知乎采集器")
    print(f"  DB: {DB_PATH}")
    print()

    total += fetch_hot(conn)
    if not args.hot_only:
        queries = [q.strip() for q in args.search.split(",")] if args.search else None
        total += fetch_search(conn, queries)

    conn.close()
    print(f"\n📊 总计: {total} 条")


if __name__ == "__main__":
    main()
