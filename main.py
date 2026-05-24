import os
import json
import re
import requests
import feedparser
from openai import OpenAI
from datetime import datetime

# ---------- 配置区 ----------
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
WX_APPID = os.environ["WX_APPID"]
WX_SECRET = os.environ["WX_SECRET"]
PUSHPLUS_TOKEN = os.environ["PUSHPLUS_TOKEN"]

# 这里放你要抓取的药学 RSS 源，可自由增删
RSS_SOURCES = [
    "https://www.drugs.com/feeds/news.xml",                # 英文，可能需要科学上网
    "https://www.fda.gov/about-fda/contact-fda/rss-feeds", # FDA 新闻
    # 国内的例子，如“药智网”等可以自行搜索 RSS 添加
]

# ---------- 工具函数 ----------
def fetch_news():
    """抓取 RSS 新闻，返回合并的文本"""
    items = []
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:  # 每个源取前10条
                items.append(f"标题：{entry.title}\n链接：{entry.link}\n摘要：{entry.summary[:200]}\n")
        except:
            continue
    if not items:
        # 如果抓取失败，用一个测试占位内容保证流程继续
        return "1. 标题：FDA今日未有更新\n摘要：请检查RSS源连接\n"
    return "\n".join(items)

def call_deepseek(news_text):
    """调用 DeepSeek API 生成日报内容和封面关键词"""
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1"
    )
    system_prompt = """你是一位药学情报编辑。根据以下今日新闻，生成一篇可直接发微信公众号的日报。
要求：
1. 首先输出一行“封面关键词：xxx”，用于生成封面图，关键词为英文、药学科技相关，例如"Pharmaceutical news, molecule structure, blue technology background"。
2. 然后输出 Markdown 格式的日报正文：
   - 一级标题：# 药学前沿日报 YYYY-MM-DD
   - 新闻标题用 ###，正文正常段落，点评用 > 引用格式。
   - 每条新闻后标注【配图关键词：xxx】（英文，如"KRAS inhibitor illustration"），用于后续配图，如果没有合适配图则标注“无图”。
3. 风格专业但不枯燥，像“医药魔方”的调性。"""
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

def parse_cover_keyword(daily_text):
    """提取封面关键词"""
    match = re.search(r"封面关键词[：:]\s*(.+)", daily_text)
    if match:
        keyword = match.group(1).strip()
        # 删除关键词行，剩余正文
        body = daily_text.replace(match.group(0), "").strip()
        return keyword, body
    return "Pharmaceutical news abstract background", daily_text

def generate_cover_image(prompt):
    """使用 Pollinations.ai 生成封面图，返回图片二进制数据"""
    url = f"https://image.pollinations.ai/prompt/{prompt}?width=900&height=500&nologo=true"
    resp = requests.get(url, timeout=60)
    if resp.status_code == 200:
        return resp.content
    else:
        raise Exception(f"封面图生成失败: {resp.status_code}")

def get_access_token(appid, secret):
    url = "https://api.weixin.qq.com/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret
    }
    r = requests.get(url, params=params).json()
    return r["access_token"]

def upload_permanent_image(access_token, img_bytes):
    """上传图片作为永久素材，返回 media_id 和 url"""
    url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={access_token}&type=image"
    files = {'media': ('cover.jpg', img_bytes, 'image/jpeg')}
    r = requests.post(url, files=files).json()
    return r["media_id"], r.get("url", "")  # url 需公众号后台或后续查询

def markdown_to_wechat_html(md_text):
    """超级简单的 Markdown 转微信 HTML（仅支持标题、段落、引用）"""
    lines = md_text.split("\n")
    html = '<section style="padding:10px;">\n'
    in_quote = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            html += f'<p style="text-align:center;font-size:20px;font-weight:bold;">{line[2:]}</p >\n'
        elif line.startswith("### "):
            html += f'<p style="font-size:16px;font-weight:bold;margin-top:20px;">{line[4:]}</p >\n'
        elif line.startswith(">"):
            html += f'<p style="color:#888;font-size:14px;border-left:3px solid #1e88e5;padding-left:8px;">{line[1:].strip()}</p >\n'
        else:
            html += f'<p style="font-size:15px;">{line}</p >\n'
    html += '</section>'
    return html

def create_draft(access_token, title, html_content, thumb_media_id):
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={access_token}"
    data = {
        "articles": [{
            "title": title,
            "content": html_content,
            "thumb_media_id": thumb_media_id,
            "need_open_comment": 0,
            "only_fans_can_comment": 0,
            "content_source_url": "",
            "digest": title + "，点击查看详细。",
        }]
    }
    r = requests.post(url, json=data).json()
    return r

def pushplus_notify(token, title, content):
    url = "https://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html"
    }
    requests.post(url, json=data)

# ---------- 主流程 ----------
def main():
    # 1. 抓新闻
    news_text = fetch_news()

    # 2. DeepSeek 生成日报
    daily_raw = call_deepseek(news_text)

    # 3. 提取封面关键词并生成封面图
    cover_keyword, daily_body = parse_cover_keyword(daily_raw)
    cover_img = generate_cover_image(cover_keyword)

    # 4. 获取 access_token
    token = get_access_token(WX_APPID, WX_SECRET)

    # 5. 上传封面素材
    media_id, cover_url = upload_permanent_image(token, cover_img)

    # 6. 转换正文为 HTML
    html_body = markdown_to_wechat_html(daily_body)

    # 7. 创建草稿
    today = datetime.now().strftime("%Y年%m月%d日")
    title = f"药学前沿日报 {today}"
    result = create_draft(token, title, html_body, media_id)
    print("草稿创建结果:", result)

    if "errcode" in result and result["errcode"] != 0:
        notify_msg = f"日报生成失败：{result}"
    else:
        notify_msg = f"<h3>{title}</h3><p>草稿已创建成功，请打开订阅号助手App，在「草稿箱」找到并点击「群发」。</p >"

    # 8. 推送到你的微信
    pushplus_notify(PUSHPLUS_TOKEN, "📰 药学日报已生成", notify_msg)

if __name__ == "__main__":
    main()
