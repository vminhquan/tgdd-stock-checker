"""
check_stock_tgdd.py
--------------------
Theo dõi hàng máy CŨ trên thegioididong.com và báo Telegram ngay khi có
máy xuất hiện TẠI HÀ NỘI.

VÌ SAO KHÔNG CHỈ CHECK "CÒN HÀNG / HẾT HÀNG"?
Với máy cũ, TGDD bán theo từng con máy cụ thể (mỗi máy có mã riêng gọi
là `oldid`), và mỗi máy được gắn với 1 kho/tỉnh cụ thể (dòng "Có tại:
Tỉnh ..."). Trang sản phẩm dạng:
  https://www.thegioididong.com/may-doi-tra/<danh-muc>/<slug>?pid=XXXX&isimei=1
liệt kê TẤT CẢ các máy đang có hàng trên toàn quốc kèm vị trí của
từng máy. Script này tải trang đó, tìm những máy có dòng "Có tại"
chứa "Hà Nội", và chỉ báo khi có MÁY MỚI xuất hiện ở Hà Nội (không
báo lại máy đã từng thấy để tránh spam).

CÁCH DÙNG
1. Cài thư viện:
     pip install requests beautifulsoup4
2. Sửa PRODUCT_URL bên dưới thành link sản phẩm anh muốn theo dõi
   (dạng ...?pid=XXXX&isimei=1 — lấy nguyên link trên thanh địa chỉ
   khi anh mở trang liệt kê các máy cũ của 1 model, KHÔNG phải link
   của 1 máy cụ thể có &oldid=...).
3. Điền TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (hướng dẫn ở cuối file).
4. Chạy thử 1 lần:
     python check_stock_tgdd.py --once
5. Chạy theo dõi liên tục (mặc định 5 phút/lần):
     python check_stock_tgdd.py

LƯU Ý
- Script CHỈ đọc trang công khai và báo tin, KHÔNG tự đăng nhập,
  KHÔNG tự đặt hàng, KHÔNG động vào OTP hay thanh toán.
- Nếu TGDD đổi cấu trúc trang khiến script không parse đúng nữa,
  cần mở lại trang, xem lại định dạng dòng "Có tại: ..." và chỉnh
  hàm parse_units() cho khớp.
"""

import argparse
import json
import os
import re
import sys
import time

import requests

from dotenv import load_dotenv

load_dotenv()


from bs4 import BeautifulSoup

# ========================== CONFIG ==========================

# Link trang liệt kê máy cũ của 1 model cụ thể (có pid=..., KHÔNG có oldid)
PRODUCT_URL = (
    "https://www.thegioididong.com/may-doi-tra/laptop/"
    "macbook-air-15-inch-m4-16gb-256gb?pid=335372&isimei=1"
)

# Từ khóa vị trí cần lọc. Có thể thêm nhiều khu vực nếu muốn, ví dụ
# ["Hà Nội", "Bắc Ninh"] để canh cả 2 nơi.
TARGET_LOCATION_KEYWORDS = ["Hà Nội"]

# Điền token & chat_id của bot Telegram (xem hướng dẫn ở cuối file)
TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# Khoảng thời gian giữa các lần kiểm tra khi chạy chế độ liên tục (giây)
CHECK_INTERVAL_SECONDS = 180

# File lưu lại các oldid đã từng báo, để không báo trùng
SEEN_FILE = "seen_units.json"

# =============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9",
}

# oldid=<số>&pid=<số>  trong href
DETAIL_LINK_RE = re.compile(r"oldid=(\d+)&pid=(\d+)")
LOCATION_RE = re.compile(r"Có tại:\s*([^\n]+)")
PRICE_RE = re.compile(r"([\d.,]+)\s*₫")


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_units(html: str, base_url: str):
    """
    Trả về danh sách các máy đang có hàng, mỗi máy là dict:
    {oldid, pid, location, price, url, text}
    """
    soup = BeautifulSoup(html, "html.parser")
    units = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = DETAIL_LINK_RE.search(href)
        if not m:
            continue
        text = a.get_text(separator=" ", strip=True)
        loc_m = LOCATION_RE.search(text)
        if not loc_m:
            continue  # link này không phải block liệt kê máy cụ thể
        price_m = PRICE_RE.search(text)
        oldid, pid = m.group(1), m.group(2)
        full_url = href if href.startswith("http") else (
            "https://www.thegioididong.com" + href
        )
        units.append({
            "oldid": oldid,
            "pid": pid,
            "location": loc_m.group(1).strip(),
            "price": price_m.group(1) if price_m else "?",
            "url": full_url,
            "text": text,
        })
    return units


def matches_target_location(location: str) -> bool:
    return any(kw.lower() in location.lower() for kw in TARGET_LOCATION_KEYWORDS)


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)


def send_telegram_message(text: str) -> None:
    if "DAN_" in TELEGRAM_BOT_TOKEN or "DAN_" in TELEGRAM_CHAT_ID:
        print("[CẢNH BÁO] Chưa điền TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, "
              "bỏ qua gửi thông báo. Nội dung lẽ ra gửi:")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, data=payload, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[LỖI] Không gửi được Telegram: {e}")


def check_once(url: str, seen: set) -> set:
    """Kiểm tra 1 lần. Trả về set oldid MỚI vừa được báo (để cập nhật seen)."""
    try:
        html = fetch_page(url)
    except requests.RequestException as e:
        print(f"[LỖI] Không tải được trang: {e}")
        return set()

    units = parse_units(html, url)
    hanoi_units = [u for u in units if matches_target_location(u["location"])]
    all_locations = [u["location"] for u in units] or ["(không có máy nào)"]

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
          f"Tổng {len(units)} máy đang có hàng. Vị trí: {', '.join(all_locations)}")

    newly_notified = set()
    for u in hanoi_units:
        if u["oldid"] in seen:
            continue  # đã báo máy này rồi, bỏ qua
        msg = (
            f"🔔 Có máy TẠI HÀ NỘI rồi anh ơi!\n"
            f"Giá: {u['price']}₫\n"
            f"Vị trí: {u['location']}\n"
            f"{u['url']}\n"
            f"Vào web/app đặt ngay kẻo hết 👉"
        )
        print(msg)
        send_telegram_message(msg)
        newly_notified.add(u["oldid"])

    return newly_notified


def main():
    parser = argparse.ArgumentParser(description="Theo dõi máy cũ TGDD theo khu vực")
    parser.add_argument("--once", action="store_true",
                         help="Chỉ kiểm tra 1 lần rồi thoát (dùng cho cron / "
                              "GitHub Actions)")
    parser.add_argument("--url", default=PRODUCT_URL,
                         help="Ghi đè link sản phẩm cần theo dõi")
    args = parser.parse_args()

    seen = load_seen()

    if args.once:
        newly = check_once(args.url, seen)
        if newly:
            seen |= newly
            save_seen(seen)
        sys.exit(0)

    print(f"Bắt đầu theo dõi: {args.url}")
    print(f"Khu vực cần lọc: {', '.join(TARGET_LOCATION_KEYWORDS)}")
    print(f"Chu kỳ kiểm tra: mỗi {CHECK_INTERVAL_SECONDS} giây\n")
    while True:
        newly = check_once(args.url, seen)
        if newly:
            seen |= newly
            save_seen(seen)
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

# =====================================================================
# HƯỚNG DẪN LẤY TELEGRAM_BOT_TOKEN VÀ TELEGRAM_CHAT_ID (làm 1 lần)
# =====================================================================
# 1. Mở Telegram, tìm "@BotFather" -> gõ /newbot -> đặt tên bot tùy ý
#    -> BotFather trả về chuỗi dạng
#    "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" => đó là
#    TELEGRAM_BOT_TOKEN.
# 2. Bấm Start / gửi 1 tin bất kỳ cho bot vừa tạo.
# 3. Mở trình duyệt, vào:
#    https://api.telegram.org/bot<TOKEN_CUA_BAN>/getUpdates
#    Tìm số trong "chat":{"id": ...} => đó là TELEGRAM_CHAT_ID.
# 4. Dán 2 giá trị vào CONFIG ở đầu file rồi chạy lại.
# =====================================================================
