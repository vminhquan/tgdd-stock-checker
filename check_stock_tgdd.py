"""
check_stock_tgdd.py
--------------------
Theo dõi hàng máy CŨ / ĐỔI TRẢ trên thegioididong.com và thông báo qua Telegram:
1. Theo dõi MacBook Air 15" M4 16GB 256GB trên toàn quốc:
   - Khi có máy mới xuất hiện: Báo 1 tin nhắn.
   - Nếu máy mới xuất hiện tại HÀ NỘI: Báo 3 lần tin nhắn ping liên tiếp.
   - Kèm thông tin Máy 1, 2, 3..., vị trí, giá tiền và link đặt hàng trực tiếp (oldid).
2. Theo dõi danh mục MacBook đổi trả:
   - Khi có máy có giá DƯỚI 25 TRIỆU (< 25.000.000₫):
   - Báo 3 lần tin nhắn, kèm thông tin máy 1, 2, 3..., vị trí, giá tiền và link đặt trực tiếp.
3. Không gửi trùng: Các máy đã báo sẽ được lưu vào seen_units.json và không bao giờ báo lại.
4. Chu kỳ kiểm tra mặc định: 60 giây (1 phút).
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Set, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ========================== CONFIG ==========================

# 1. URL MacBook Air 15" M4 cần theo dõi biến động
AIR15_M4_URL = (
    "https://www.thegioididong.com/may-doi-tra/laptop/"
    "macbook-air-15-inch-m4-16gb-256gb?pid=335372&isimei=1"
)

# 2. URL danh mục MacBook đổi trả để lọc máy dưới 25 triệu và RAM >= 16GB
CATEGORY_MACBOOK_URL = "https://www.thegioididong.com/may-doi-tra/laptop-apple-macbook"
PRICE_LIMIT_UNDER_25M = 25_000_000
MIN_RAM_GB = 16

# 3. Telegram Bot Token & Chat ID
# Ưu tiên lấy từ file .env hoặc biến môi trường, có thể điền trực tiếp vào đây nếu muốn
TELEGRAM_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

# 4. Chu kỳ kiểm tra (giây) - Mặc định 1 phút
CHECK_INTERVAL_SECONDS = 60

# 5. File lưu trạng thái máy đã báo để chống gửi lặp
SEEN_FILE = "seen_units.json"

# =============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9",
}

DETAIL_LINK_RE = re.compile(r"oldid=(\d+)&pid=(\d+)")
LOCATION_RE = re.compile(r"Có tại:\s*([^\n]+)")
PRICE_RE = re.compile(r"([\d.,]+)\s*₫")


def fetch_page(url: str) -> str:
    """Tải nội dung HTML của trang web."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_price_to_int(price_str: str) -> int:
    """Chuyển đổi chuỗi giá '16.400.000₫' hoặc '16.400.000' thành số nguyên int."""
    cleaned = re.sub(r"[^\d]", "", price_str)
    return int(cleaned) if cleaned else 0


def extract_ram_gb(text: str) -> int:
    """
    Trích xuất dung lượng RAM (GB) từ tên máy hoặc cấu hình.
    Ví dụ:
    - 'MacBook Air 15 inch M4 16GB/256GB' -> 16
    - 'MacBook Pro 16 inch M3 Pro 18GB/512GB' -> 18
    - 'MacBook Pro 14 inch M5 24GB/1TB' -> 24
    - 'MacBook Neo 13 inch A18 Pro 8GB/256GB' -> 8
    """
    # 1. Tìm dạng rõ ràng: 'RAM 16GB' hoặc '16GB RAM'
    ram_explicit = re.search(
        r"(?:RAM\s*[:=]?\s*(\d+)\s*GB|(\d+)\s*GB\s*RAM)", text, re.IGNORECASE
    )
    if ram_explicit:
        val = ram_explicit.group(1) or ram_explicit.group(2)
        return int(val)

    # 2. Dạng chuẩn của laptop TGDD: '<RAM>GB/<SSD>GB' hoặc '<RAM>GB/<SSD>TB'
    slash_match = re.search(
        r"\b(\d+)\s*GB\s*/\s*(?:\d+\s*GB|\d+\s*TB)", text, re.IGNORECASE
    )
    if slash_match:
        return int(slash_match.group(1))

    # 3. Tìm số GB đầu tiên xuất hiện (trong tên laptop TGDD, RAM luôn đứng trước ổ cứng)
    gb_matches = re.findall(r"\b(\d+)\s*GB\b", text, re.IGNORECASE)
    if gb_matches:
        return int(gb_matches[0])

    return 0


def parse_units(html: str, base_url: str = "") -> List[Dict]:
    """
    Phân tích các máy cụ thể có trong trang sản phẩm.
    Trả về danh sách dict: {oldid, pid, location, price, price_num, url, text}
    """
    soup = BeautifulSoup(html, "html.parser")
    units = []
    seen_oldids = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = DETAIL_LINK_RE.search(href)
        if not m:
            continue
        oldid, pid = m.group(1), m.group(2)
        if oldid in seen_oldids:
            continue

        text = a.get_text(separator=" ", strip=True)
        loc_m = LOCATION_RE.search(text)
        if not loc_m:
            continue

        price_m = PRICE_RE.search(text)
        price_str = price_m.group(1) if price_m else "?"
        price_num = parse_price_to_int(price_str)
        location_str = loc_m.group(1).strip()

        full_url = href if href.startswith("http") else (
            "https://www.thegioididong.com" + href
        )

        seen_oldids.add(oldid)
        units.append({
            "oldid": oldid,
            "pid": pid,
            "location": location_str,
            "price": price_str,
            "price_num": price_num,
            "url": full_url,
            "text": text,
        })
    return units


def parse_category_models(html: str) -> List[Dict]:
    """
    Phân tích trang danh mục máy đổi trả (vd: /may-doi-tra/laptop-apple-macbook).
    Trả về danh sách các model: [{name, min_price, min_price_num, url, qty_str}]
    """
    soup = BeautifulSoup(html, "html.parser")
    models = []
    items = soup.find_all(class_="prdItem")

    for it in items:
        a_tag = it.find("a", href=True)
        if not a_tag:
            continue
        href = a_tag["href"]
        full_url = (
            href if href.startswith("http") else "https://www.thegioididong.com" + href
        )

        name_el = it.find(class_="prdName")
        name = name_el.get_text(strip=True) if name_el else "MacBook"
        ram_gb = extract_ram_gb(name)

        price_el = it.find(class_="price")
        price_strong = price_el.find("strong") if price_el else None
        price_str = price_strong.get_text(strip=True) if price_strong else ""
        price_num = parse_price_to_int(price_str)

        qty_el = it.find(class_="quantity")
        qty_str = qty_el.get_text(strip=True) if qty_el else ""

        models.append({
            "name": name,
            "ram_gb": ram_gb,
            "price_str": price_str,
            "price_num": price_num,
            "url": full_url,
            "qty_str": qty_str,
        })
    return models


# ===================== STATE MANAGEMENT =====================


def load_seen() -> Dict[str, Set[str]]:
    """
    Đọc seen_units.json.
    Cấu trúc: {"air15_m4": set(...), "under_25m": set(...)}
    """
    default_state = {"air15_m4": set(), "under_25m": set()}
    if not os.path.exists(SEEN_FILE):
        return default_state

    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                # Tương thích ngược phiên bản cũ lưu list
                return {"air15_m4": set(data), "under_25m": set()}
            if isinstance(data, dict):
                return {
                    "air15_m4": set(data.get("air15_m4", [])),
                    "under_25m": set(data.get("under_25m", [])),
                }
    except Exception as e:
        print(f"[CẢNH BÁO] Không thể đọc {SEEN_FILE}: {e}")
    return default_state


def save_seen(seen: Dict[str, Set[str]]) -> None:
    """Lưu danh sách oldid đã xử lý vào seen_units.json."""
    data = {
        "air15_m4": sorted(list(seen.get("air15_m4", set()))),
        "under_25m": sorted(list(seen.get("under_25m", set()))),
    }
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===================== TELEGRAM SENDER =====================


def send_telegram_message(text: str, repeat: int = 1) -> None:
    """
    Gửi tin nhắn Telegram qua bot.
    - text: nội dung tin nhắn.
    - repeat: số lần lặp lại (ví dụ gửi 3 lần cho kèo hot hoặc ping HN).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[CẢNH BÁO] Chưa cấu hình TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID!")
        print(f"[GIẢ LẬP GỬI TELEGRAM ({repeat} lần)]:\n{text}\n")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Chia nhỏ tin nhắn nếu vượt quá giới hạn 4000 ký tự của Telegram
    chunks = []
    if len(text) <= 4000:
        chunks = [text]
    else:
        current_chunk = []
        current_len = 0
        for block in text.split("\n\n"):
            if current_len + len(block) + 2 > 4000:
                if current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                current_chunk = [block]
                current_len = len(block)
            else:
                current_chunk.append(block)
                current_len += len(block) + 2
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

    for idx in range(repeat):
        for chunk in chunks:
            try:
                payload = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "disable_web_page_preview": False,
                }
                r = requests.post(url, data=payload, timeout=10)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"[LỖI] Không thể gửi tin nhắn Telegram (lần {idx+1}/{repeat}): {e}")
        if repeat > 1:
            time.sleep(0.5)  # Tránh Telegram rate limit


# ===================== CHECK LOGIC =====================


def check_air15_m4(seen_set: Set[str]) -> Tuple[Set[str], int]:
    """
    Kiểm tra biến động MacBook Air 15" M4 trên toàn quốc:
    - Báo 1 tin nhắn khi có máy mới ở các tỉnh khác.
    - Báo 3 lần tin nhắn ping khi có máy mới tại HÀ NỘI.
    Trả về: (tập hợp oldid mới phát hiện, tổng số máy đang có)
    """
    try:
        html = fetch_page(AIR15_M4_URL)
    except requests.RequestException as e:
        print(f"[LỖI] Không tải được trang Air 15 M4: {e}")
        return set(), 0

    all_units = parse_units(html, AIR15_M4_URL)
    new_units = [u for u in all_units if u["oldid"] not in seen_set]
    total_count = len(all_units)

    locations_summary = [u["location"] for u in all_units] or ["(không có máy nào)"]
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [Air 15 M4] "
        f"Tổng: {total_count} máy. Mới: {len(new_units)} máy. Vị trí: {', '.join(locations_summary)}"
    )

    if not new_units:
        return set(), total_count

    # Kiểm tra xem có máy nào mới tại Hà Nội không
    has_hanoi = any("hà nội" in u["location"].lower() for u in new_units)
    repeat_times = 3 if has_hanoi else 1

    # Tạo nội dung thông báo
    lines = []
    if has_hanoi:
        lines.append("🚨🚨🚨 [HÀ NỘI CÓ HÀNG] MACBOOK AIR 15\" M4!")
        lines.append(f"Phát hiện {len(new_units)} máy mới (Trong đó có máy tại HÀ NỘI)!")
    else:
        lines.append("🔔 [TGDD] MACBOOK AIR 15\" M4 CÓ BIẾN ĐỘNG HÀNG MỚI!")
        lines.append(f"Phát hiện {len(new_units)} máy mới trên toàn quốc (Tổng hiện có: {total_count} máy):")

    lines.append("")
    for i, u in enumerate(new_units, 1):
        lines.append(f"💻 Máy {i}:")
        lines.append(f"💵 Giá: {u['price']}₫")
        lines.append(f"📍 Vị trí: {u['location']}")
        lines.append(f"👉 Đặt ngay: {u['url']}")
        lines.append("")

    msg = "\n".join(lines).strip()
    print(f"[THÔNG BÁO Air 15 M4 (gửi {repeat_times} lần)]:\n{msg}\n")
    send_telegram_message(msg, repeat=repeat_times)

    new_oldids = {u["oldid"] for u in new_units}
    return new_oldids, total_count


def check_macbook_under_25m(seen_set: Set[str]) -> Set[str]:
    """
    Kiểm tra danh mục MacBook đổi trả tìm máy dưới 25 triệu VÀ RAM >= 16GB:
    - Báo 3 lần tin nhắn khi có máy mới thỏa điều kiện.
    - Kèm thông tin máy 1, 2, 3..., vị trí, giá tiền và link đặt trực tiếp.
    Trả về: tập hợp oldid mới phát hiện
    """
    try:
        cat_html = fetch_page(CATEGORY_MACBOOK_URL)
    except requests.RequestException as e:
        print(f"[LỖI] Không tải được danh mục MacBook: {e}")
        return set()

    models = parse_category_models(cat_html)
    # Lọc các model có giá hiển thị < 25 triệu VÀ RAM >= 16GB
    under_25m_models = [
        m for m in models
        if 0 < m["price_num"] < PRICE_LIMIT_UNDER_25M and m.get("ram_gb", 0) >= MIN_RAM_GB
    ]

    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [MacBook < 25tr & RAM >= {MIN_RAM_GB}GB] "
        f"Tìm thấy {len(under_25m_models)} model thỏa mãn điều kiện."
    )

    new_under_25m_units = []

    for m in under_25m_models:
        try:
            model_html = fetch_page(m["url"])
            units = parse_units(model_html, m["url"])
            for u in units:
                # Kiểm tra giá thực tế của con máy đó < 25 triệu và RAM >= 16GB
                unit_ram = extract_ram_gb(m["name"] + " " + u.get("text", ""))
                if (
                    u["oldid"] not in seen_set
                    and 0 < u["price_num"] < PRICE_LIMIT_UNDER_25M
                    and unit_ram >= MIN_RAM_GB
                ):
                    u["model_name"] = m["name"]
                    u["ram_gb"] = unit_ram
                    new_under_25m_units.append(u)
        except requests.RequestException as e:
            print(f"[CẢNH BÁO] Không tải được trang model {m['url']}: {e}")

    if not new_under_25m_units:
        return set()

    # Soạn tin nhắn thông báo (gửi 3 lần theo yêu cầu)
    lines = [
        f"🔥 [TGDD - KÈO THƠM] PHÁT HIỆN MACBOOK RAM >= {MIN_RAM_GB}GB DƯỚI 25 TRIỆU!",
        f"Có {len(new_under_25m_units)} máy mới thỏa điều kiện RAM >= {MIN_RAM_GB}GB & Giá < 25tr:",
        "",
    ]
    for i, u in enumerate(new_under_25m_units, 1):
        lines.append(f"💻 Máy {i}: {u.get('model_name', 'MacBook')}")
        lines.append(f"🧠 RAM: {u.get('ram_gb', MIN_RAM_GB)}GB")
        lines.append(f"💵 Giá: {u['price']}₫")
        lines.append(f"📍 Vị trí: {u['location']}")
        lines.append(f"👉 Đặt ngay: {u['url']}")
        lines.append("")

    msg = "\n".join(lines).strip()
    print(f"[THÔNG BÁO MacBook < 25M (gửi 3 lần)]:\n{msg}\n")
    send_telegram_message(msg, repeat=3)

    return {u["oldid"] for u in new_under_25m_units}


def run_check_cycle(seen_state: Dict[str, Set[str]]) -> bool:
    """Chạy 1 chu kỳ kiểm tra đầy đủ cho cả 2 mục tiêu. Trả về True nếu có biến động mới."""
    has_changes = False

    # 1. Kiểm tra MacBook Air 15" M4
    new_air15, _ = check_air15_m4(seen_state["air15_m4"])
    if new_air15:
        seen_state["air15_m4"].update(new_air15)
        has_changes = True

    # 2. Kiểm tra danh mục MacBook < 25 triệu
    new_25m = check_macbook_under_25m(seen_state["under_25m"])
    if new_25m:
        seen_state["under_25m"].update(new_25m)
        has_changes = True

    if has_changes:
        save_seen(seen_state)

    return has_changes


# ===================== MAIN ENTRY =====================


def main():
    parser = argparse.ArgumentParser(
        description="Theo dõi máy cũ TGDD: MacBook Air 15 M4 & MacBook dưới 25 triệu"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Chỉ kiểm tra 1 lần rồi thoát (dùng cho GitHub Actions / Cron)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=CHECK_INTERVAL_SECONDS,
        help=f"Khoảng thời gian lặp kiểm tra (giây), mặc định: {CHECK_INTERVAL_SECONDS}s",
    )
    parser.add_argument(
        "--init-seen",
        action="store_true",
        help="Chỉ ghi nhận các máy hiện tại vào seen mà không gửi tin nhắn (dùng khi khởi tạo lần đầu tránh nhận dồn dập)",
    )
    args = parser.parse_args()

    seen_state = load_seen()

    if args.init_seen:
        print("[KHỞI TẠO] Đang quét tất cả máy hiện có để lưu vào seen_units.json...")
        try:
            air15_html = fetch_page(AIR15_M4_URL)
            air_units = parse_units(air15_html, AIR15_M4_URL)
            seen_state["air15_m4"].update(u["oldid"] for u in air_units)

            cat_html = fetch_page(CATEGORY_MACBOOK_URL)
            models = parse_category_models(cat_html)
            for m in models:
                if (
                    0 < m["price_num"] < PRICE_LIMIT_UNDER_25M
                    and m.get("ram_gb", 0) >= MIN_RAM_GB
                ):
                    m_html = fetch_page(m["url"])
                    u_list = parse_units(m_html, m["url"])
                    seen_state["under_25m"].update(
                        u["oldid"]
                        for u in u_list
                        if 0 < u["price_num"] < PRICE_LIMIT_UNDER_25M
                        and extract_ram_gb(m["name"] + " " + u.get("text", "")) >= MIN_RAM_GB
                    )

            save_seen(seen_state)
            print(
                f"[THÀNH CÔNG] Đã lưu {len(seen_state['air15_m4'])} máy Air 15 M4 "
                f"và {len(seen_state['under_25m'])} máy RAM >= {MIN_RAM_GB}GB dưới 25M vào {SEEN_FILE}."
            )
        except Exception as e:
            print(f"[LỖI] Khởi tạo seen thất bại: {e}")
        sys.exit(0)

    if args.once:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Chạy kiểm tra 1 lần (--once)...")
        run_check_cycle(seen_state)
        sys.exit(0)

    print("=" * 60)
    print("BẮT ĐẦU BOT THEO DÕI MÁY CŨ THẾ GIỚI DI ĐỘNG")
    print("- Mục tiêu 1: MacBook Air 15 M4 toàn quốc (Ping HN 3 lần, tỉnh khác 1 lần)")
    print(f"- Mục tiêu 2: MacBook RAM >= {MIN_RAM_GB}GB và dưới 25 triệu (Báo 3 lần)")
    print(f"- Chu kỳ kiểm tra: {args.interval} giây (1 phút)")
    print(f"- File lưu trạng thái: {SEEN_FILE}")
    print("=" * 60 + "\n")

    while True:
        try:
            run_check_cycle(seen_state)
        except Exception as e:
            print(f"[LỖI NGOẠI LỆ] Chu kỳ kiểm tra gặp lỗi: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()

# =====================================================================
# HƯỚNG DẪN CẬP NHẬT TELEGRAM KHI ĐỔI TÀI KHOẢN MỚI
# =====================================================================
# 1. LẤY CHAT ID MỚI:
#    - Mở Telegram trên điện thoại/máy tính.
#    - Tìm kiếm bot "@userinfobot" -> Bấm Start (hoặc gửi /start).
#    - Bot sẽ phản hồi thông tin của bạn, tìm dòng:
#      Id: 1234567890 (dãy số này chính là TELEGRAM_CHAT_ID).
#
# 2. MỞ BOT VÀ BẤM START:
#    - Tìm con bot của bạn trên tài khoản Telegram mới.
#    - Bấm START (hoặc gửi /start).
#    - BẮT BUỘC: Nếu không Start, Telegram sẽ chặn bot gửi tin (Error 403).
#
# 3. NƠI CẬP NHẬT:
#    A. Chạy trên máy (Local):
#       Tạo hoặc mở file .env ở cùng thư mục script này:
#       TG_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
#       TG_CHAT_ID="1234567890"
#
#    B. Chạy qua GitHub Actions:
#       Vào GitHub Repo -> Settings -> Secrets and variables -> Actions
#       Cập nhật 2 secrets: TG_BOT_TOKEN và TG_CHAT_ID.
# =====================================================================
