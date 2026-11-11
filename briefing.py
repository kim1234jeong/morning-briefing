"""매일 아침 브리핑: 날씨 + 일정 + 미국증시/환율 + 핫딜 + 구글트렌드 + 뉴스 요약 -> 텔레그램 및 웹 연동"""
import json
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests
import yfinance as yf
from urllib.parse import quote

KST = ZoneInfo("Asia/Seoul")
NOW = datetime.now(KST)

# ---------- 설정 ----------
LAT = float(os.getenv("LAT", "37.32"))    # 기본값: 안산
LON = float(os.getenv("LON", "126.83"))
PLACE = os.getenv("PLACE", "안산")

# 키워드별 구글 뉴스 검색 (이름: (검색어, 최대 기사 수))
NEWS_QUERIES = {
    "남양넥스모": ("남양넥스모", 8),
    "자동차 부품": ("자동차 부품 OR 자동차부품 OR 부품사", 6),
    "현대차": ("현대차 OR 현대자동차", 6),
    "기아": ("기아 자동차 OR 기아차", 6),
}
NEWS_WINDOW = "when:1d"

WMO = {0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림", 45: "안개", 48: "안개",
       51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 61: "약한 비", 63: "비",
       65: "강한 비", 71: "약한 눈", 73: "눈", 75: "강한 눈", 80: "소나기",
       81: "소나기", 82: "강한 소나기", 95: "뇌우"}


def get_weather() -> str:
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": LAT, "longitude": LON, "timezone": "Asia/Seoul",
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "forecast_days": 1,
            },
            timeout=15,
        )
        r.raise_for_status()
        d = r.json()
        cur, day = d["current"], d["daily"]
        desc = WMO.get(day["weather_code"][0], "정보 없음")
        
        return (f"{PLACE} 현재 {cur['temperature_2m']:.1f}°C · 오늘 {desc}\n"
                f"최저 {day['temperature_2m_min'][0]:.0f}° / 최고 {day['temperature_2m_max'][0]:.0f}°\n"
                f"강수확률 {day['precipitation_probability_max'][0]}%")
    except Exception as e:
        return f"날씨 정보를 가져오지 못했어요 ({e})"


def get_events() -> str:
    url = os.getenv("ICS_URL")
    if not url:
        return "일정 연동 안 됨 (ICS_URL 미설정)"
    try:
        import icalendar
        import recurring_ical_events

        cal = icalendar.Calendar.from_ical(requests.get(url, timeout=20).content)
        events = recurring_ical_events.of(cal).at((NOW.year, NOW.month, NOW.day))
        rows = []
        for ev in events:
            start = ev.get("DTSTART").dt
            title = str(ev.get("SUMMARY", "(제목 없음)"))
            if isinstance(start, datetime):
                rows.append((start.astimezone(KST).strftime("%H:%M"), title))
            else:
                rows.append(("종일", title))
        if not rows:
            return "오늘 일정 없음"
        rows.sort()
        return "\n".join(f"• {t} {title}" for t, title in rows)
    except Exception as e:
        return f"일정을 가져오지 못했어요 ({e})"


def get_market_data() -> str:
    """미국 마감 증시 및 환율 (원화 환산 포함)"""
    tickers = {"VOO": "VOO", "QQQ": "QQQ", "TQQQ": "TQQQ", "SOXL": "SOXL", "SCHD": "SCHD"}
    lines = []
    exchange_rate = None
    
    # 1. 환율 먼저 조회
    try:
        krw_data = yf.Ticker("USDKRW=X").history(period="1d")
        if not krw_data.empty:
            exchange_rate = krw_data['Close'].iloc[-1]
            lines.append(f"• 환율: {exchange_rate:,.1f}원")
    except Exception:
        pass

    # 2. ETF 조회 및 환율 기반 원화 환산
    for name, ticker in tickers.items():
        try:
            data = yf.Ticker(ticker).history(period="1d")
            if not data.empty:
                close = data['Close'].iloc[-1]
                if exchange_rate:
                    krw_price = close * exchange_rate
                    lines.append(f"• {name}: ${close:,.2f} (약 {krw_price:,.0f}원)")
                else:
                    lines.append(f"• {name}: ${close:,.2f}")
        except Exception:
            pass
    return "\n".join(lines) if lines else "시장 데이터를 가져오지 못했어요."


def get_hot_deals() -> str:
    """뽐뿌 핫딜 게시판 실시간 최신글 TOP 3 (차단 우회)"""
    url = "https://www.ppomppu.co.kr/rss.php?id=ppomppu"
    lines = []
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = requests.get(url, headers=headers, timeout=10)
        feed = feedparser.parse(resp.content)
        
        for entry in feed.entries[:3]:
            title = entry.title.strip()
            lines.append(f"• {title}")
    except Exception as e:
        print(f"핫딜 에러: {e}") 
        pass
    return "\n".join(lines) if lines else "핫딜을 가져오지 못했어요."


def get_google_trends() -> str:
    """구글 대한민국 일별 트렌드 검색어 TOP 3 (링크 제거)"""
    url = "https://trends.google.com/trending/rss?geo=KR"
    lines = []
    try:
        for idx, entry in enumerate(feedparser.parse(url).entries[:3], 1):
            lines.append(f"{idx}. {entry.title}")
    except Exception:
        pass
    return "\n".join(lines) if lines else "트렌드를 가져오지 못했어요."


def get_news() -> dict:
    news, seen = {}, set()
    for name, (query, limit) in NEWS_QUERIES.items():
        url = ("https://news.google.com/rss/search?q="
               + quote(f"{query} {NEWS_WINDOW}")
               + "&hl=ko&gl=KR&ceid=KR:ko")
        items = []
        try:
            for e in feedparser.parse(url).entries:
                title = e.get("title", "").strip()
                key = title.rsplit(" - ", 1)[0]
                if not title or key in seen:
                    continue
                seen.add(key)
                items.append({"title": title, "summary": "", "link": e.get("link", "")})
                if len(items) >= limit:
                    break
        except Exception:
            pass
        news[name] = items
    return news


def summarize_news(news: dict) -> str:
    raw = "\n".join(
        f"[{src}] {it['title']}"
        for src, items in news.items() for it in items
    )
    if not raw.strip():
        return "뉴스를 가져오지 못했어요."

    # 변경점: 클로드 API 키가 없어서 텍스트만 뽑아올 때도 무조건 각 섹션당 1개만 노출
    if not os.getenv("ANTHROPIC_API_KEY"):
        return "\n".join(
            f"[{src}]\n• {items[0]['title']}"
            for src, items in news.items() if items
        )

    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-latest"),
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": (
                    "아래는 최근 24시간 동안의 관련 뉴스 헤드라인입니다(남양넥스모, 자동차 부품, 현대차, 기아). "
                    "다음 형식으로 한국어로 정리해 주세요.\n"
                    "1) '[남양넥스모]' 섹션을 맨 위에 두고, 관련 기사가 없으면 '오늘 관련 기사 없음'이라고 한 줄만 쓰기\n"
                    "2) 그다음 '[자동차 부품]', '[현대차·기아]' 섹션을 순서대로 작성하되, 각 섹션별로 가장 핵심적인 기사를 **오직 1개씩만** 골라서 '• '로 시작하는 한 줄 요약으로 작성하기 (절대로 섹션당 2개 이상 작성하지 말 것)\n"
                    "3) 웹 UI에 노출되므로 마크다운 기호(**, # 등)를 절대 사용하지 말고 순수 텍스트로만 작성할 것.\n"
                    "4) 제목에 없는 내용은 추측하지 말고 사실만 쓰기. 다른 설명이나 인사말은 붙이지 않기.\n\n" + raw
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"AI 뉴스 요약 중 오류가 발생했습니다: {e}"


def send_telegram(text: str) -> None:
    token, chat_id = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4000],
              "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def write_data(weather: str, events: str, market: str, hotdeals: str, trends: str, summary: str, news: dict) -> None:
    """앱(docs/index.html)이 읽는 data.json 생성"""
    def split(title: str):
        t, _, src = title.rpartition(" - ")
        return (t, src) if t else (title, "")

    data = {
        "updated": NOW.isoformat(timespec="minutes"),
        "weather": weather,
        "events": events if os.getenv("PUBLISH_EVENTS") == "1" else None,
        "market": market,
        "hotdeals": hotdeals,
        "trends": trends,
        "summary": summary,
        "news": {
            k: [dict(zip(("title", "source"), split(it["title"])), link=it["link"])
                for it in v]
            for k, v in news.items()
        },
    }
    out = Path(__file__).parent / "docs" / "data.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    weekday = "월화수목금토일"[NOW.weekday()]
    
    # 1. 데이터 수집
    weather = get_weather()
    events = get_events()
    market = get_market_data()
    hotdeals = get_hot_deals()
    trends = get_google_trends()
    news = get_news()
    summary = summarize_news(news)
    
    # 2. 웹페이지용 JSON 데이터 쓰기
    write_data(weather, events, market, hotdeals, trends, summary, news)

    # 3. 텔레그램 메시지 조립 및 전송
    message = (
        f"☀️ {NOW:%Y.%m.%d}({weekday}) 아침 브리핑\n\n"
        f"🌤 날씨\n{weather}\n\n"
        f"📅 오늘 일정\n{events}\n\n"
        f"📈 증시 & 환율\n{market}\n\n"
        f"🔥 실시간 핫딜\n{hotdeals}\n\n"
        f"🔍 인기 검색어\n{trends}\n\n"
        f"📰 주요 뉴스 요약\n{summary}"
    )
    
    if os.getenv("DRY_RUN"):
        print(message)
    else:
        send_telegram(message)


if __name__ == "__main__":
    main()
