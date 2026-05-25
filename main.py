import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import requests
import feedparser
from openai import OpenAI
import xie
from datetime import datetime

# ---------- 配置区 (从 GitHub Secrets 读取) ----------
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
SMTP_SERVER = os.environ["SMTP_SERVER"]        # 例如 smtp.qq.com
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SENDER_EMAIL = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]  # 邮箱授权码
RECEIVER_EMAIL = os.environ["RECEIVER_EMAIL"]

# 药学 RSS 源，可自由增删（注意：国外源可能需要科学上网，国内源更稳妥）
RSS_SOURCES = [
    # 中文药学源（示例，请换成真实 RSS 地址）
    "https://www.dxy.cn/bbs/feed/rss/2",            # 丁香园药学频道（示例）
    # "https://news.yaozh.com/rss.xml",             # 药智网新闻（需确认真实地址）
    # 若没有合适的国内源，可以先用固定占位文本测试
]

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
    """使用 Xie 将 Markdown 转换为微信合规的 HTML"""
    # xie.convert 返回完整的 HTML 文档，我们只需要 body 里的内容
    full_html = xie.convert(md_text)
    # 提取 body 部分（去掉 html/head/body 标签）
    body_match = re.search(r"<body[^>]*>(.*?)</body>", full_html, re.DOTALL)
    if body_match:
        return body_match.group(1).strip()
    else:
        # 如果失败，回退为简单替换
        return md_text.replace("\n", "<br>")

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
