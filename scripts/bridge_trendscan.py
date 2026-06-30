#!/usr/bin/env python3
"""
Bridge: trendscan SQLite → ai-news-radar JSON.
Reads trendscan's 15-cluster database and emits the same JSON schema
that the radar frontend + radar skill expect so we keep trendscan's
superior backend and get the v0.7 frontend for free.

Usage:
    python scripts/bridge_trendscan.py \\
        --db C:\\Users\\norman\\trendscan\\data\\raw.db \\
        --output-dir data \\
        --window-hours 24

Can also be used standalone: just point at any trendscan DB and get
radar-compatible JSON out.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── Year extraction heuristic for fake published dates ──
YEAR_PATTERNS = [
    re.compile(r'\b(19\d\d|20[0-2]\d)\b'),  # 1990-2029
    re.compile(r'Volume\s+\d+[,\s]+(?:[A-Z][a-z]+)?\s*(20\d\d)'),
    re.compile(r'\(20\d\d\)'),
]

def extract_year(text: str | None) -> int | None:
    """Try to extract a real publication year from text."""
    if not text:
        return None
    for pat in YEAR_PATTERNS:
        m = pat.search(text)
        if m:
            y = int(m.group(1) if m.lastindex else m.group(0).strip('()'))
            if 1990 <= y <= 2029:
                return y
    return None

def is_fake_date(published: str, collected: str) -> bool:
    """Check if published date appears to be a fake (same as collection time)."""
    if not published or not collected:
        return True
    try:
        p = datetime.fromisoformat(published.replace('Z', '+00:00'))
        c = datetime.fromisoformat(collected.replace('Z', '+00:00'))
        return abs((p - c).total_seconds()) < 120
    except (ValueError, TypeError):
        return True


# ── Cluster → site mapping (matches trendscan's 15 clusters) ──

CLUSTER_TO_SITE = {
    "01": {"site_id": "trendscan_ai4science", "name": "AI+科学", "tier": "ai_media", "tier_rank": 1},
    "02": {"site_id": "trendscan_embodied", "name": "具身智能与机器人", "tier": "ai_media", "tier_rank": 1},
    "03": {"site_id": "trendscan_bci", "name": "脑机接口与神经工程", "tier": "ai_media", "tier_rank": 1},
    "04": {"site_id": "trendscan_fusion", "name": "核聚变与先进储能", "tier": "ai_media", "tier_rank": 1},
    "05": {"site_id": "trendscan_space", "name": "商业航天", "tier": "ai_media", "tier_rank": 1},
    "06": {"site_id": "trendscan_quantum", "name": "量子科技", "tier": "ai_media", "tier_rank": 1},
    "07": {"site_id": "trendscan_synbio", "name": "合成生物学与生物制造", "tier": "ai_media", "tier_rank": 1},
    "08": {"site_id": "trendscan_2dmaterials", "name": "二维材料与原子制造", "tier": "ai_media", "tier_rank": 1},
    "09": {"site_id": "trendscan_6g", "name": "6G与通信", "tier": "ai_media", "tier_rank": 1},
    "10": {"site_id": "trendscan_meddevice", "name": "高端医疗器械", "tier": "ai_media", "tier_rank": 1},
    "11": {"site_id": "trendscan_greenmf", "name": "绿色制造与工业互联网", "tier": "ai_media", "tier_rank": 1},
    "12": {"site_id": "trendscan_deepspace", "name": "深空与深地探测", "tier": "ai_media", "tier_rank": 1},
    "13": {"site_id": "trendscan_infrasw", "name": "基础软件与工业软件", "tier": "ai_media", "tier_rank": 1},
    "14": {"site_id": "trendscan_nano", "name": "纳米技术", "tier": "ai_media", "tier_rank": 1},
    "15": {"site_id": "trendscan_supercon", "name": "超导与量子材料", "tier": "ai_media", "tier_rank": 1},
}

# Map llm_category → radar ai_label
CATEGORY_TO_LABEL = {
    "大语言模型": "model_release",
    "编程工具": "developer_tool",
    "开发者工具": "developer_tool",
    "AI工具": "ai_tech",
    "AI技术": "ai_tech",
    "AI安全": "ai_tech",
    "AI监管": "industry_business",
    "产业政策": "industry_business",
    "金融监管": "industry_business",
    "网络安全": "ai_tech",
    "数据安全": "ai_tech",
    "开源生态": "ai_general",
    "AI产品": "ai_product_update",
    "Agent工作流": "agent_workflow",
    "机器人": "robotics",
    "论文研究": "research_paper",
    "算力": "infra_compute",
}

DEFAULT_LABEL = "ai_general"


def normalize_cluster(cluster_id: str) -> str:
    """Normalize cluster IDs: 'cluster-01' → '01', 'C' → '' (unmapped)."""
    c = cluster_id.removeprefix("cluster-")
    # Old single-letter clusters (C/D/E) → no mapping
    if len(c) == 1:
        return ""
    return c


def classify_label(llm_category_json: str | None) -> str:
    """Map our llm_category to radar's ai_label."""
    if not llm_category_json:
        return DEFAULT_LABEL
    try:
        cats = json.loads(llm_category_json)
        for cat in cats:
            for key, label in CATEGORY_TO_LABEL.items():
                if key in cat:
                    return label
        return DEFAULT_LABEL
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_LABEL


def extract_policy_str(policy_json: str | None) -> str:
    """Extract a human-readable policy_relevance string."""
    if not policy_json:
        return ""
    try:
        p = json.loads(policy_json)
        score = p.get("relevance_score", 0)
        domains = p.get("policy_domains", [])
        if score >= 5 and domains:
            return f"🏛️{score} ({', '.join(domains[:3])})"
        return ""
    except (json.JSONDecodeError, TypeError):
        return ""


def generate_latest_24h(conn: sqlite3.Connection, hours: int) -> dict:
    """Generate the main latest-24h.json structure."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    year_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    
    rows = conn.execute("""
        SELECT id, title, url, summary, published, collected,
               source_name, source_type, cluster, category,
               lang, trend_score, llm_summary, entities,
               llm_category, policy_relevance
        FROM articles
        WHERE collected >= ?
          AND title IS NOT NULL AND title != ''
          AND (published >= ? OR published IS NULL OR published = '')
        ORDER BY collected DESC
    """, (cutoff, year_cutoff)).fetchall()

    items = []
    site_stats = {}  # site_id: count
    candidate_count = 0

    for row in rows:
        (aid, title, url, summary, published, collected,
         source_name, source_type, cluster_raw, category,
         lang, trend_score, llm_summary, entities,
         llm_category, policy_relevance) = row

        cluster_id = normalize_cluster(cluster_raw)
        site_def = CLUSTER_TO_SITE.get(cluster_id, {})
        site_id = site_def.get("site_id", cluster_raw or "unknown")

        # ── Fake date detection ──
        # When published == collected (AnySearch/RSS don't extract real dates),
        # try to find the real year from title/summary/URL text.
        if is_fake_date(published, collected):
            real_year = (
                extract_year(title) or 
                extract_year(summary) or 
                extract_year(llm_summary) or
                extract_year(url)
            )
            if real_year is not None and real_year < 2025:
                candidate_count += 1
                continue  # Skip pre-2025 articles with no real timestamp
            # Keep it but note the date is uncertain
            published = collected  # Keep collection time as-is

        # Generate a stable item id
        item_id = hashlib.sha1(url.encode()).hexdigest() if url else f"trendscan_{aid}"

        # Score: trend_score or default
        ai_score = trend_score if trend_score and trend_score > 0 else 5.0

        policy_tag = extract_policy_str(policy_relevance)

        # Build AI signals / noise
        ai_signals = []
        ai_noise = []
        if llm_summary:
            ai_signals.append(llm_summary[:150])
        if policy_tag:
            ai_signals.append(policy_tag)

        # Bilingual titles
        title_en = title
        title_zh = ""
        title_bilingual = title

        label = classify_label(llm_category)

        items.append({
            "id": item_id,
            "site_id": site_id,
            "site_name": site_def.get("name", source_name or cluster_raw),
            "source": source_name or cluster_raw,
            "title": title,
            "url": url or "",
            "published_at": published or collected or "",
            "first_seen_at": collected or "",
            "last_seen_at": collected or "",
            "ai_is_related": True,
            "ai_score": ai_score,
            "ai_label": label,
            "ai_relevance_reason": f"trendscan cluster {cluster_raw} | llm tags: {llm_category or 'auto'}",
            "ai_signals": ai_signals,
            "ai_noise": ai_noise,
            "source_tier": site_def.get("tier", "ai_media"),
            "source_tier_label": site_def.get("tier", "AI媒体"),
            "source_tier_rank": site_def.get("tier_rank", 1),
            "title_original": title,
            "title_en": title_en,
            "title_zh": title_zh,
            "title_bilingual": title_bilingual,
            "category": category or "",
            "cluster_id": cluster_raw or "",
            "entities": entities or "",
            "policy_relevance": policy_tag,
        })

        if site_id not in site_stats:
            site_stats[site_id] = 0
        site_stats[site_id] += 1

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": hours,
        "total_items": len(items),
        "total_items_ai_raw": len(items),
        "total_items_raw": len(items),
        "total_items_all_mode": len(items),
        "topic_filter": "ai",
        "ai_relevance_threshold": 0,
        "archive_total": 0,
        "site_count": len(site_stats),
        "items": items,
        "items_ai": items,
        "site_stats": [
            {"site_id": sid, "site_name": CLUSTER_TO_SITE.get(sid.split("_")[0] if len(sid.split("_")) > 0 else "", {}).get("name", sid), "count": cnt}
            for sid, cnt in sorted(site_stats.items(), key=lambda x: -x[1])
        ],
    }
    return result


def generate_source_status(conn: sqlite3.Connection) -> dict:
    """Generate source-status.json from cluster health."""
    rows = conn.execute("""
        SELECT source_name, source_type, cluster, COUNT(*) as cnt
        FROM articles
        GROUP BY source_name, source_type, cluster
        ORDER BY COUNT(*) DESC
    """).fetchall()

    sites = []
    for row in rows:
        source_name, source_type, cluster_raw, cnt = row
        cluster_id = normalize_cluster(cluster_raw)
        site_def = CLUSTER_TO_SITE.get(cluster_id, {})
        
        sites.append({
            "site_id": site_def.get("site_id", source_name or cluster_raw),
            "site_name": site_def.get("name", source_name or cluster_raw),
            "ok": True,
            "item_count": cnt,
            "duration_ms": 0,
            "error": None,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sites": sites,
        "successful_sites": len(sites),
        "failed_sites": 0,
        "zero_item_sites": 0,
        "empty_advanced_sources": [],
        "fetched_raw_items": sum(s["item_count"] for s in sites),
        "items_before_topic_filter": sum(s["item_count"] for s in sites),
        "items_in_24h": max((s["item_count"] for s in sites), default=0),
        "rss_opml": "bridge",
    }


def generate_stories(conn: sqlite3.Connection, hours: int) -> dict:
    """Generate stories-merged.json with cluster-grouped stories."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    year_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    
    rows = conn.execute("""
        SELECT id, title, url, summary, published, collected,
               source_name, cluster, category, llm_summary
        FROM articles
        WHERE collected >= ?
          AND title IS NOT NULL AND title != ''
          AND (published >= ? OR published IS NULL OR published = '')
        ORDER BY collected DESC
    """, (cutoff, year_cutoff)).fetchall()

    # Group by cluster for simple story merging
    clusters = {}
    for row in rows:
        (aid, title, url, summary, published, collected,
         source_name, cluster_raw, category, llm_summary) = row
        if cluster_raw not in clusters:
            clusters[cluster_raw] = []
        clusters[cluster_raw].append({
            "id": aid, "title": title, "url": url, "source_name": source_name,
            "published": published, "collected": collected,
        })

    stories = []
    for cluster_id, items in sorted(clusters.items(), key=lambda x: -len(x[1])):
        cluster_norm = normalize_cluster(cluster_id)
        site_def = CLUSTER_TO_SITE.get(cluster_norm, {})
        display = site_def.get("name", cluster_id)

        primary = items[0]
        story = {
            "story_id": f"trendscan_{cluster_id}",
            "title": primary["title"],
            "url": primary["url"] or "",
            "primary_url": primary["url"] or "",
            "source": primary["source_name"],
            "source_name": primary["source_name"],
            "sources": [i["source_name"] for i in items[:5]],
            "source_count": len(set(i["source_name"] for i in items)),
            "source_names": list(dict.fromkeys(i["source_name"] for i in items)),
            "items": [i["url"] for i in items[:20] if i["url"]],
            "item_count": len(items),
            "duplicate_count": 0,
            "score": len(items) * 0.5,
            "importance": len(items) / 20 if len(items) < 20 else 1.0,
            "importance_score": min(len(items) * 5, 100),
            "importance_label": "high" if len(items) >= 20 else ("medium" if len(items) >= 10 else "low"),
            "importance_breakdown": {
                "source_tier_diversity": min(len(set(i["source_name"] for i in items)) / 3, 1.0),
                "multi_source_confirmation": min(len(items) / 5, 1.0),
                "recent_boost": 0.5,
            },
            "category": "trendscan",
            "reasons": [f"{display} 聚合 {len(items)} 条"],
            "earliest_at": items[-1].get("published") or items[-1].get("collected") or "",
            "latest_at": items[0].get("published") or items[0].get("collected") or "",
            "primary_item": {
                "title": primary["title"],
                "url": primary["url"] or "",
                "source": primary["source_name"],
            },
        }
        stories.append(story)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": hours,
        "total_stories": len(stories),
        "stories": stories,
    }


def generate_daily_brief(stories: dict) -> dict:
    """Generate daily-brief.json from the top stories."""
    top_stories = sorted(stories["stories"], key=lambda s: -s["importance_score"])[:20]
    return {
        "generated_at": stories["generated_at"],
        "window_hours": stories["window_hours"],
        "total_items": len(top_stories),
        "items": top_stories,
    }


def main():
    parser = argparse.ArgumentParser(description="Bridge trendscan SQLite → radar JSON")
    parser.add_argument("--db", default=os.environ.get("TRENDSCAN_DB", ""),
                        help="Path to trendscan raw.db")
    parser.add_argument("--output-dir", default="data",
                        help="Output directory for JSON files")
    parser.add_argument("--window-hours", type=int, default=24,
                        help="Hours of data to include (default: 24)")
    args = parser.parse_args()

    db_path = args.db
    if not db_path:
        # Auto-detect common locations
        candidates = [
            r"C:\Users\norman\trendscan\data\raw.db",
            os.path.expanduser("~/trendscan/data/raw.db"),
            "/c/Users/norman/trendscan/data/raw.db",
        ]
        for c in candidates:
            if os.path.exists(c):
                db_path = c
                break
        if not db_path:
            print("ERROR: Cannot find trendscan DB. Pass --db or set TRENDSCAN_DB.", file=sys.stderr)
            sys.exit(1)

    if not os.path.exists(db_path):
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📡 Bridge: {db_path}")
    conn = sqlite3.connect(db_path)

    latest = generate_latest_24h(conn, args.window_hours)
    print(f"  latest-24h: {latest['total_items']} items, {latest['site_count']} sites")

    sources = generate_source_status(conn)
    print(f"  source-status: {sources['successful_sites']} sites")

    stories = generate_stories(conn, args.window_hours)
    print(f"  stories-merged: {stories['total_stories']} stories")

    brief = generate_daily_brief(stories)
    print(f"  daily-brief: {brief['total_items']} top stories")

    conn.close()

    # Write output
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    files = {
        "latest-24h.json": latest,
        "latest-24h-all.json": latest,  # Same content for now
        "source-status.json": sources,
        "stories-merged.json": stories,
        "daily-brief.json": brief,
    }

    for fname, data in files.items():
        path = output / fname
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"  ✅ {fname} ({size_mb:.1f} MB)")

    print(f"\n✅ Bridge complete → {output.resolve()}")


if __name__ == "__main__":
    main()
