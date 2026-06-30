#!/usr/bin/env python3
"""
Bilibili 采集器 — 通过B站公开API获取热门/搜索内容。

用法:
    python scripts/collect_bilibili.py                  # 采集热门+搜索
    python scripts/collect_bilibili.py --popular-only   # 仅热门
    python scripts/collect_bilibili.py --search "AI Agent"  # 自定义搜索词
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
    "Referer": "https://www.bilibili.com/",
}

# 默认搜索词（AI/科技相关）
DEFAULT_QUERIES = [
    "AI 大模型",
    "人工智能",
    "AI Agent",
    "机器人",
    "科技数码",
]

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
            return {"code": -1, "message": str(e)}

    def get(url, headers=None, timeout=15):
        return _get(url, headers, timeout)
else:

    def get(url, headers=None, timeout=15):
        try:
            r = req.get(url, headers=headers or HEADERS, timeout=timeout)
            return r.json()
        except Exception as e:
            return {"code": -1, "message": str(e)}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def fetch_popular(conn, limit=20):
    """采集B站热门视频"""
    data = get("https://api.bilibili.com/x/web-interface/popular?ps=%d" % limit)
    if data.get("code") != 0:
        print(f"  ❌ 热门API: {data.get('message', data.get('msg', 'unknown'))}")
        return 0

    videos = data.get("data", {}).get("list", [])
    inserted = save_videos(conn, videos, "bili_popular", "17", "热门")
    print(f"  ✅ 热门: {inserted} 条新视频")
    return inserted


def fetch_search(conn, queries=None):
    """搜索B站AI相关视频"""
    queries = queries or DEFAULT_QUERIES
    total = 0
    for q in queries:
        time.sleep(2.0)  # B站风控严格，需要间隔
        encoded_q = q
        if isinstance(q, str):
            import urllib.parse
            encoded_q = urllib.parse.quote(q)
        data = get(
            "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=%s&page=1"
            % encoded_q,
        )
        if data.get("code") != 0:
            # 重试一次
            time.sleep(3.0)
            data = get(
                "https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=%s&page=1"
                % encoded_q,
            )
        if data.get("code") != 0:
            print(f"  ❌ 搜索\"{q}\": {data.get('message', 'fail')}")
            continue
        results = data.get("data", {}).get("result", [])
        if results:
            inserted = save_videos(conn, results, "bili_search", "17", f"搜索:{q}")
            total += inserted
        time.sleep(0.5)  # 限速
    print(f"  ✅ 搜索共 {total} 条新视频")
    return total


def save_videos(conn, videos, source_type, cluster_id, category):
    """保存视频到数据库"""
    cursor = conn.cursor()
    inserted = 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    for v in videos:
        title = v.get("title", "")
        # 清理B站搜索结果的<em>标签
        title = title.replace("<em class=\"keyword\">", "").replace("</em>", "")
        # 热门接口的title没有标签
        if not title:
            continue

        bvid = v.get("bvid", "")
        aid = v.get("aid", 0)
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

        # 摘要
        desc = v.get("desc", v.get("description", ""))[:500]

        # 作者
        owner = v.get("owner", {}) or {}
        author = owner.get("name", "")

        # 统计
        stat = v.get("stat", {}) or {}
        play = stat.get("view", 0)
        like = stat.get("like", 0)
        summary = f"[{play}播放/{like}赞] {desc}"[:500]

        # 发表时间（B站API没有直接返回，用采集时间）
        published = ""  # 未知真实日期

        try:
            cursor.execute("""
                INSERT OR IGNORE INTO articles
                    (source_name, source_priority, cluster, title, url,
                     summary, published, collected, lang, source_type,
                     quality_score, author, category)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?)
            """, (
                f"B站 {category}",
                "important",
                cluster_id,
                title[:300],
                url,
                summary,
                published,
                "ZH",
                "bilibili",
                0.8,
                author[:100],
                category,
            ))
            if cursor.rowcount > 0:
                inserted += 1
        except Exception:
            pass

    conn.commit()
    return inserted


def main():
    import argparse
    parser = argparse.ArgumentParser(description="B站采集器")
    parser.add_argument("--popular-only", action="store_true", help="仅采集热门")
    parser.add_argument("--search", type=str, help="自定义搜索词（逗号分隔）")
    args = parser.parse_args()

    conn = get_connection()
    total = 0

    print("📺 B站采集器")
    print(f"  DB: {DB_PATH}")
    print()

    # 热门
    total += fetch_popular(conn)

    # 搜索
    if not args.popular_only:
        queries = [q.strip() for q in args.search.split(",")] if args.search else DEFAULT_QUERIES
        total += fetch_search(conn, queries)

    conn.close()
    print(f"\n📊 总计: {total} 条新视频")


if __name__ == "__main__":
    main()
