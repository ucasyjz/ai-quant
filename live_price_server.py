"""
live_price_server.py - 实时价格 API 服务器

为仪表盘提供持仓股票的实时价格查询。
运行在 localhost:8766，独立于静态文件服务器(8765)。

用法：
    python live_price_server.py          # 启动
    python live_price_server.py --port 8766  # 指定端口

API：
    GET /api/prices?codes=688333,603259,002294
    返回: {"688333": {"price": 112.50, "time": "09:51:23"}, ...}
"""

import json
import os
import sys
import time
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 确保能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_fetcher import get_live_price

PRICE_CACHE = {}       # {code: {"price": float, "time": str}}
CACHE_TTL = 15          # 缓存 15 秒，避免频繁调 westock-data
_lock = threading.Lock()


def fetch_prices_batch(codes):
    """批量获取实时价格，带缓存"""
    results = {}
    now = time.time()
    to_fetch = []

    with _lock:
        for code in codes:
            cached = PRICE_CACHE.get(code)
            if cached and (now - cached["_ts"]) < CACHE_TTL:
                results[code] = {"price": cached["price"], "time": cached["time"]}
            else:
                to_fetch.append(code)

    if to_fetch:
        print(f"[价格API] 查询 {len(to_fetch)} 只股票实时价格: {','.join(to_fetch)}")
        for code in to_fetch:
            price = get_live_price(code)
            now_str = datetime.now().strftime("%H:%M:%S")
            if price is not None:
                results[code] = {"price": round(price, 2), "time": now_str}
                with _lock:
                    PRICE_CACHE[code] = {"price": price, "time": now_str, "_ts": time.time()}
            else:
                # 取缓存兜底，哪怕过期
                cached = PRICE_CACHE.get(code)
                if cached:
                    results[code] = {"price": cached["price"], "time": cached["time"] + " (缓存)"}
                else:
                    results[code] = {"price": None, "time": now_str, "error": "查询失败"}

    return results


class PriceHandler(BaseHTTPRequestHandler):
    """价格 API HTTP 处理器"""

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/prices":
            qs = parse_qs(parsed.query)
            codes_str = qs.get("codes", [""])[0]
            if not codes_str:
                self._json({"error": "缺少 codes 参数"}, 400)
                return

            codes = [c.strip() for c in codes_str.split(",") if c.strip()]
            if not codes:
                self._json({"error": "codes 为空"}, 400)
                return

            prices = fetch_prices_batch(codes)
            self._json(prices)

        elif parsed.path == "/api/health":
            self._json({"status": "ok", "time": datetime.now().isoformat()})

        else:
            self._json({"error": "Not Found"}, 404)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # 减少日志噪音
        if "/api/health" not in args[0]:
            print(f"[价格API] {args[0]}")


def start_server(port=8766):
    """启动价格 API 服务器"""
    server = HTTPServer(("127.0.0.1", port), PriceHandler)
    print(f"[价格API] 实时价格服务已启动: http://localhost:{port}/api/prices")
    print(f"[价格API] 示例: http://localhost:{port}/api/prices?codes=688333,603259")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[价格API] 服务已停止")
        server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[2]) if len(sys.argv) >= 3 and sys.argv[1] == "--port" else 8766
    start_server(port)
