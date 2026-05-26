import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import requests
import feedparser
import time
from typing import List, Dict, Optional
from openai import OpenAI
import markdown2
import re
from datetime import datetime

# ---------- 配置区 (从 GitHub Secrets 读取) ----------
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
SMTP_SERVER = os.environ["SMTP_SERVER"]        # 例如 smtp.qq.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]  # 邮箱授权码
RECEIVER_EMAIL = os.environ["RECEIVER_EMAIL"]

# --------------------------
# 扩充后 RSS 源列表（国际顶刊+行业媒体+监管+国内医药）
# --------------------------
RSS_SOURCES = [
    # 国际权威期刊
    {
        "name": "Nature Reviews Drug Discovery",
        "url": "https://www.nature.com/nrd.rss"
    },
    {
        "name": "The Lancet Pharmacology",
        "url": "https://www.thelancet.com/rss/originalArticles?series=pharmacology"
    },
    {
        "name": "PubMed 药学资讯",
        "url": "https://www.nlm.nih.gov/rss/auto/PubMedNews.rss"
    },
    {
        "name": "APSB 药学学报英文版",
        "url": "https://www.apsb.org/rss"
    },
    # 国际医药行业媒体
    {
        "name": "FiercePharma",
        "url": "https://www.fiercepharma.com/rss"
    },
    {
        "name": "PharmaTimes",
        "url": "https://www.pharmatimes.com/rss"
    },
    {
        "name": "STAT News 医药板块",
        "url": "https://www.statnews.com/feed/category/pharma/"
    },
    {
        "name": "Medscape 临床药学",
        "url": "https://www.medscape.com/cx/rssfeeds/2704.html"
    },
    # 欧美药品监管机构
    {
        "name": "FDA 药品安全警示",
        "url": "https://www.drugs.com/fda_alerts.rss"
    },
    {
        "name": "EMA 欧洲药管局新闻",
        "url": "https://www.ema.europa.eu/en/rss-feeds/news"
    },
    {
        "name": "DailyMed 药品说明书更新",
        "url": "https://dailymed.nlm.nih.gov/dailymed/rss-updates.cfm"
    },
    # 国内医药资讯
    {
        "name": "药智网行业动态",
        "url": "https://www.yaozh.com/news/rss/"
    },
    {
        "name": "医药魔方资讯",
        "url": "https://www.pharmcube.com/news/rss"
    }
]

# 请求头（模拟浏览器，防止拦截）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# 采集配置
MAX_RETRIES = 2    # 单源最大重试次数
TIMEOUT = 10       # 单次请求超时(秒)
LIMIT_NEWS = 10    # 最终展示最大新闻条数


# --------------------------
# 单源RSS采集函数（带重试、异常捕获）
# --------------------------
def fetch_rss_feed(source: Dict) -> Optional[List[Dict]]:
    url = source["url"]
    name = source["name"]

    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()

            feed = feedparser.parse(resp.text)
            if feed.bozo != 0:
                raise ValueError(f"RSS解析异常，错误码：{feed.bozo}")

            entries = []
            for entry in feed.entries:
                entries.append({
                    "title": entry.get("title", "无标题"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", ""),
                    "published": entry.get("published", ""),
                    "source": name
                })

            print(f"✅ [{name}] 采集成功，共 {len(entries)} 条")
            return entries

        except Exception as e:
            print(f"⚠️ [{name}] 第 {attempt+1} 次失败：{str(e)}")
            time.sleep(1)

    print(f"❌ [{name}] 多次重试后采集失败")
    return []


# --------------------------
# 多源汇总主函数
# --------------------------
def fetch_all_pharma_news() -> List[Dict]:
    all_news = []
    for source in RSS_SOURCES:
        res = fetch_rss_feed(source)
        if res:
            all_news.extend(res)

    if not all_news:
        print("❌ 全部RSS源采集失败，今日无药学新闻")
    else:
        print(f"✅ 总计采集到 {len(all_news)} 条药学相关新闻")
    return all_news


# --------------------------
# 生成日报Markdown（兼容原有逻辑）
# --------------------------
def generate_daily_md(news_list: List[Dict]) -> str:
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    if not news_list:
        md = f"""
# 药学前沿日报 ({today})

今日新闻抓取异常：RSS源待检

**摘要**：本日本未成功抓取到任何药学相关新闻。系统提示RSS源可能失效或内容缺失，建议调整源配置或手动补充来源。

💡 点评：新闻源的空窗期提醒我们，数据采集的稳定性是信息工作的基石。建议尽快检查RSS源可用性，或临时切换至备用源。

【配图关键词：无图】
        """.strip()
        return md

    # 截取指定条数新闻
    show_news = news_list[:LIMIT_NEWS]
    md = f"# 药学前沿日报 ({today})\n\n今日共采集到 {len(news_list)} 条药学相关资讯，精选如下：\n\n"
    for item in show_news:
        md += f"## [{item['title']}]({item['link']})\n"
        md += f"来源：{item['source']} | 发布时间：{item['published']}\n\n"
        md += f"{item['summary']}\n\n---\n\n"
    return md


# --------------------------
# 入口调用（直接对接原有转换、发邮件逻辑）
# --------------------------
if __name__ == "__main__":
    news_data = fetch_all_pharma_news()
    md_content = generate_daily_md(news_data)
    # 下方继续衔接你原有的 markdown_to_wechat_html、发送邮件代码即可
    print("Markdown 日报生成完成")

# ---------- 工具函数 ----------
def fetch_news():
    """抓取 RSS 新闻，返回合并的文本"""
    items = []
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                summary = entry.get("summary", "") or entry.get("description", "")
                items.append(f"标题：{entry.title}\n链接：{entry.link}\n摘要：{summary[:200]}\n")
        except Exception:
            continue
    if not items:
        # 占位内容，防止流程中断，同时提醒你检查 RSS
        return "1. 标题：今日无新闻抓取\n摘要：请检查RSS源是否可用，或替换为真实中文源。\n"
    return "\n".join(items)

def call_deepseek(news_text):
    """调用 DeepSeek API 生成 Markdown 日报和封面关键词"""
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1"
    )
    system_prompt = """你是一位药学情报编辑。根据以下今日新闻，生成一篇可直接发微信公众号的 Markdown 格式日报。
要求：
1. 第一行单独输出：封面关键词：<英文描述，用于生成封面图，如"Pharmaceutical news, molecular structure, blue technology background">。
2. 之后输出日报正文：
   - 一级标题：# 药学前沿日报 (YYYY-MM-DD)
   - 每条新闻用 ### 标题，正文正常段落，点评用 > 引用格式（前面加“💡 点评：”）。
   - 每条新闻后添加【配图关键词：xxx】（英文，若无合适图则标注“无图”）。
3. 语言专业但不枯燥，类似“医药魔方”风格。"""
    today = datetime.now().strftime("%Y-%m-%d")
    user_message = f"今天是 {today}，以下是今日新闻素材：\n{news_text}"

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=0.7,
        max_tokens=2500
    )
    return response.choices[0].message.content

def parse_cover_keyword(daily_raw):
    """提取封面关键词和正文 Markdown"""
    match = re.search(r"封面关键词[：:]\s*(.+)", daily_raw)
    if match:
        keyword = match.group(1).strip()
        body = daily_raw.replace(match.group(0), "").strip()
        return keyword, body
    return "Pharmaceutical news abstract background", daily_raw

def generate_cover_image(prompt):
    """使用 Pollinations.ai 生成封面图，返回二进制数据"""
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=900&height=500&nologo=true"
    resp = requests.get(url, timeout=60)
    if resp.status_code == 200:
        return resp.content
    else:
        raise Exception(f"封面图生成失败: {resp.status_code}")

def markdown_to_wechat_html(md_text):
    """使用 markdown2 将 Markdown 转换为微信兼容的 HTML"""
    # 转换为基础 HTML
    full_html = markdown2.markdown(md_text, extras=["tables", "fenced-code-blocks", "footnotes"])
    
    # 提取 body 部分（如果需要的话）
    body_match = re.search(r"<body[^>]*>(.*?)</body>", full_html, re.DOTALL)
    if body_match:
        return body_match.group(1).strip()
    else:
        return full_html.replace("\n", "<br>")

def send_email(html_content, cover_img_bytes, date_str):
    """构建并发送邮件"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"药学前沿日报 {date_str}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL

    # 纯文本备用（如果邮件客户端不支持 HTML）
    text_part = MIMEText("今日药学日报，请查看 HTML 版本。", "plain", "utf-8")
    msg.attach(text_part)

    # HTML 正文，直接封装
    html_part = MIMEText(html_content, "html", "utf-8")
    msg.attach(html_part)

    # 封面图附件
    attachment = MIMEBase("image", "jpeg")
    attachment.set_payload(cover_img_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header(
        "Content-Disposition",
        f"attachment; filename=cover_{date_str}.jpg"
    )
    msg.attach(attachment)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)

# ---------- 主流程 ----------
def main():
    # 1. 抓新闻
    news_text = fetch_news()

    # 2. DeepSeek 生成 Markdown 日报
    daily_raw = call_deepseek(news_text)

    # 3. 提取封面关键词并生成封面图
    cover_keyword, daily_md = parse_cover_keyword(daily_raw)
    cover_img = generate_cover_image(cover_keyword)

    # 4. 用 Xie 转换为微信合规 HTML
    wechat_html = markdown_to_wechat_html(daily_md)

    # 5. 发送邮件（HTML 正文 + 封面图附件）
    today_str = datetime.now().strftime("%Y年%m月%d日")
    send_email(wechat_html, cover_img, today_str)

    print(f"日报已发送至 {RECEIVER_EMAIL}，请查收。")

if __name__ == "__main__":
    main()
