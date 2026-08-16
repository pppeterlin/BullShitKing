# -*- coding: utf-8 -*-
"""瞎掰王 · 本機局網派對遊戲 伺服器 (零依賴，純標準函式庫)。

執行：
    python3 server.py
然後手機連到與電腦同一個 Wi-Fi，用瀏覽器開啟畫面上顯示的網址即可。
"""

import json
import os
import random
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cards import CARDS

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
PORT = int(os.environ.get("PORT", "8000"))

# 玩家名單／分數的存檔位置。重啟後會自動接回來，手機不用重新加入。
# （只存「跨回合才有意義」的東西：玩家、分數、房主、大聰明輪到誰、第幾回合）
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(HERE, "state.json"))

READING_SECONDS = 60  # 老實人閱讀題目的倒數秒數

CALLOUT_HIT_BONUS = 1      # 瞎掰卡丟中瞎掰者 → 大聰明加分
CALLOUT_MISS_PENALTY = 3   # 瞎掰卡丟到老實人 → 大聰明扣分

# 「聽你在扯淡」即時吐槽：按自己的頭像就會廣播給所有人看（純娛樂，不影響分數）
REACTION_TEXT = "聽你在扯淡！"
REACTION_TTL = 4.0         # 廣播給所有人看的存活秒數（要 > 輪詢間隔才不會漏看）
REACTION_COOLDOWN = 1.5    # 同一人連續吐槽的間隔，防洗版

# 可愛動物 icon + 淡色底（不是純形狀）
AVATARS = [
    ("🐰", "#FFB3C1"),
    ("🐻", "#E0B080"),
    ("🐱", "#FFD9A0"),
    ("🐶", "#FFC48C"),
    ("🦊", "#FF9E6D"),
    ("🐼", "#CBD5D8"),
    ("🐨", "#B7C4CF"),
    ("🐯", "#FFCF6B"),
    ("🦁", "#FFD24C"),
    ("🐷", "#FFB6C8"),
    ("🐸", "#A8E6A1"),
    ("🐵", "#D2A679"),
]

ROLE_HONEST = "honest"   # 老實人
ROLE_GUESSER = "guesser"  # 大聰明
ROLE_BLUFFER = "bluffer"  # 瞎掰者

ROLE_LABELS = {
    ROLE_HONEST: "老實人",
    ROLE_GUESSER: "大聰明",
    ROLE_BLUFFER: "瞎掰者",
}


# ---------------------------------------------------------------------------
# 遊戲狀態
# ---------------------------------------------------------------------------
class Game:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset_all()

    def reset_all(self):
        self.phase = "lobby"          # lobby / reading / speaking / guessing / result
        self.players = []             # [{id,name,avatar,color,score}]
        self.host_id = None
        self.round_no = 0
        self.last_guesser_id = None    # 上一回合的大聰明，用來依座位順序輪換
        # 每回合狀態
        self.roles = {}               # player_id -> role
        self.card = None
        self.card_deck = []           # 尚未使用的卡片索引
        self.reading_ends_at = 0.0
        self.speaking_order = []      # player_id 列表（不含大聰明）
        self.speaking_index = 0
        self.result = None            # 結算結果 dict
        self.reactions = []           # 即時吐槽 [{id,from_id,text,ts}]，會自動過期

    # -- 存檔 / 讀檔 --------------------------------------------------------
    def save(self):
        """把玩家名單與分數寫到磁碟。寫暫存檔再 rename，避免寫到一半被中斷。"""
        data = {
            "version": 1,
            "saved_at": time.time(),
            "players": self.players,
            "host_id": self.host_id,
            "last_guesser_id": self.last_guesser_id,
            "round_no": self.round_no,
        }
        try:
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            print("⚠️  存檔失敗（遊戲照常進行）：%s" % e)

    def load(self):
        """啟動時把上次的玩家名單與分數接回來。回傳接回幾位玩家。"""
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return 0
        except (OSError, ValueError) as e:
            print("⚠️  存檔讀取失敗，改用空房間：%s" % e)
            return 0

        players = []
        for p in (data.get("players") or []):
            try:
                players.append({
                    "id": str(p["id"]),
                    "name": str(p["name"]),
                    "avatar": str(p["avatar"]),
                    "color": str(p["color"]),
                    "score": int(p["score"]),
                })
            except (KeyError, TypeError, ValueError):
                continue  # 壞掉的那筆跳過就好，不要整個房間讀不進來
        if not players:
            return 0

        ids = {p["id"] for p in players}
        self.players = players
        self.host_id = data.get("host_id") if data.get("host_id") in ids else players[0]["id"]
        lg = data.get("last_guesser_id")
        self.last_guesser_id = lg if lg in ids else None
        try:
            self.round_no = int(data.get("round_no") or 0)
        except (TypeError, ValueError):
            self.round_no = 0
        # 進行到一半的回合不還原（角色/計時器/發言順序跨重啟沒有意義），一律回大廳
        self.phase = "lobby"
        return len(players)

    # -- 玩家 ---------------------------------------------------------------
    def _player(self, pid):
        for p in self.players:
            if p["id"] == pid:
                return p
        return None

    def join(self, name):
        name = (name or "").strip()[:12] or "玩家"
        pid = uuid.uuid4().hex
        used = {p["avatar"] for p in self.players}
        avatar, color = next(
            ((a, c) for a, c in AVATARS if a not in used),
            AVATARS[len(self.players) % len(AVATARS)],
        )
        player = {"id": pid, "name": name, "avatar": avatar, "color": color, "score": 0}
        self.players.append(player)
        if self.host_id is None:
            self.host_id = pid

        # 中途加入：這一回合直接當瞎掰者，並排到發言順序的最後面。
        # （老實人/大聰明在回合開始時就決定了，不能補；他還沒聽到題目也不該當老實人）
        if self.phase in ("preview", "reading", "speaking"):
            self.roles[pid] = ROLE_BLUFFER
            if pid not in self.speaking_order:
                self.speaking_order.append(pid)
        # 猜測/結算階段才進來的人來不及參與，這回合先觀戰，下一回合自動有身分

        self.save()
        return pid

    # -- 回合流程 -----------------------------------------------------------
    def _draw_card(self):
        if not self.card_deck:
            self.card_deck = list(range(len(CARDS)))
            random.shuffle(self.card_deck)
        return CARDS[self.card_deck.pop()]

    def _peek_next_guesser(self):
        """下一位大聰明：依加入順序（座位）輪換，不改變狀態。"""
        ids = [p["id"] for p in self.players]
        if not ids:
            return None
        if self.last_guesser_id in ids:
            return ids[(ids.index(self.last_guesser_id) + 1) % len(ids)]
        return ids[0]  # 第一回合從房主開始

    def _assign_roles(self):
        # 大聰明依座位固定輪換，讓每個人都輪得到；老實人再從其餘人隨機抽
        guesser_id = self._peek_next_guesser()
        self.last_guesser_id = guesser_id
        rest = [p["id"] for p in self.players if p["id"] != guesser_id]
        honest_id = random.choice(rest)
        self.roles = {guesser_id: ROLE_GUESSER, honest_id: ROLE_HONEST}
        for pid in rest:
            if pid != honest_id:
                self.roles[pid] = ROLE_BLUFFER

    def start_round(self):
        if len(self.players) < 3:
            return False, "至少需要 3 位玩家才能開始"
        self.round_no += 1
        self.card = self._draw_card()
        self._assign_roles()
        self.reading_ends_at = 0.0  # 先不倒數，等房主按「開始回合」
        # 發言順序：除了大聰明以外的人，依座位（加入順序）輪流
        self.speaking_order = [
            p["id"] for p in self.players if self.roles[p["id"]] != ROLE_GUESSER
        ]
        random.shuffle(self.speaking_order)
        self.speaking_index = 0
        self.result = None
        self.phase = "preview"  # 題目預覽：先看題目，尚未倒數
        self.save()             # 記下輪到誰當大聰明、第幾回合
        return True, None

    def skip_card(self):
        """換一張題目（例如大家本來就知道答案）。角色不變，仍停在預覽。"""
        if self.phase != "preview":
            return
        self.card = self._draw_card()

    def begin_reading(self):
        """房主按下「開始回合」→ 開始 60 秒倒數，老實人看答案。"""
        if self.phase != "preview":
            return
        self.reading_ends_at = time.time() + READING_SECONDS
        self.phase = "reading"

    def _honest_id(self):
        for pid, r in self.roles.items():
            if r == ROLE_HONEST:
                return pid
        return None

    def _guesser_id(self):
        for pid, r in self.roles.items():
            if r == ROLE_GUESSER:
                return pid
        return None

    def _maybe_advance_reading(self):
        if self.phase == "reading" and time.time() >= self.reading_ends_at:
            self.phase = "speaking"

    def skip_reading(self):
        if self.phase == "reading":
            self.phase = "speaking"

    def advance_speaker(self):
        if self.phase != "speaking":
            return
        self.speaking_index += 1
        if self.speaking_index >= len(self.speaking_order):
            self.phase = "guessing"

    def go_to_guessing(self):
        if self.phase == "speaking":
            self.phase = "guessing"

    def submit_guess(self, guesser_id, suspect_id, callout_id):
        if self.phase != "guessing":
            return False, "現在不是猜測階段"
        if guesser_id != self._guesser_id():
            return False, "只有大聰明可以猜測"
        if suspect_id not in self.roles:
            return False, "請選擇一位玩家"
        honest_id = self._honest_id()
        guesser = self._guesser_id()
        if suspect_id == guesser:
            return False, "不能選自己"
        D = self.card["difficulty"]
        deltas = {p["id"]: 0 for p in self.players}

        # 只有「被大聰明選中的那個人」拿到難度分：
        #   選中老實人 → 老實人 +D，大聰明也 +D（猜對了）
        #   選中瞎掰者 → 那位瞎掰者 +D（成功騙到大聰明），其餘人都不加分
        correct = suspect_id == honest_id
        deltas[suspect_id] += D
        if correct:
            deltas[guesser] += D

        # 瞎掰卡只影響大聰明自己的分數：丟中瞎掰者 +1，冤枉老實人 -3
        callout_effect = None  # None / "hit" / "miss"
        if callout_id and callout_id in self.roles and callout_id != guesser:
            if self.roles[callout_id] == ROLE_BLUFFER:
                deltas[guesser] += CALLOUT_HIT_BONUS
                callout_effect = "hit"   # 抓到瞎掰者
            elif self.roles[callout_id] == ROLE_HONEST:
                deltas[guesser] -= CALLOUT_MISS_PENALTY
                callout_effect = "miss"  # 冤枉老實人
        else:
            callout_id = None

        # 套用分數
        for p in self.players:
            p["score"] += deltas[p["id"]]

        if correct:
            winners = [guesser, honest_id]
            winner_label = "大聰明 & 老實人"
        else:
            winners = [suspect_id]
            sp = self._player(suspect_id)
            winner_label = "瞎掰者 " + sp["name"] if sp else "瞎掰者"

        self.result = {
            "honest_id": honest_id,
            "guesser_id": guesser,
            "suspect_id": suspect_id,
            "callout_id": callout_id,
            "callout_effect": callout_effect,
            "correct": correct,
            "winners": winners,
            "winner_label": winner_label,
            "difficulty": D,
            "deltas": deltas,
            "callout_bonus": CALLOUT_HIT_BONUS,
            "callout_penalty": CALLOUT_MISS_PENALTY,
        }
        self.phase = "result"
        self.save()             # 分數變動了，馬上落地
        return True, None

    def next_round(self):
        if self.phase != "result":
            return False, "現在無法進入下一回合"
        return self.start_round()

    def back_to_lobby(self):
        self.phase = "lobby"
        self.roles = {}
        self.card = None
        self.result = None
        self.round_no = 0
        self.save()

    def reset_scores(self):
        """所有人分數歸零重新計分，但保留房間裡的玩家（不用重新加入）。"""
        for p in self.players:
            p["score"] = 0
        self.phase = "lobby"
        self.roles = {}
        self.card = None
        self.result = None
        self.round_no = 0
        self.last_guesser_id = None  # 大聰明輪換也從頭開始
        self.save()

    # -- 即時吐槽 -----------------------------------------------------------
    def _prune_reactions(self, now):
        self.reactions = [r for r in self.reactions if now - r["ts"] < REACTION_TTL]

    def add_reaction(self, pid):
        """按自己的頭像 → 廣播一則「聽你在扯淡！」給房裡所有人。"""
        if self._player(pid) is None:
            return False, "你還沒加入遊戲"
        now = time.time()
        self._prune_reactions(now)
        # 冷卻期間內重複按就當作沒按（TTL > COOLDOWN，所以還在冷卻的一定沒被清掉）
        for r in self.reactions:
            if r["from_id"] == pid and now - r["ts"] < REACTION_COOLDOWN:
                return True, None
        self.reactions.append({
            "id": uuid.uuid4().hex,
            "from_id": pid,
            "text": REACTION_TEXT,
            "ts": now,
        })
        return True, None

    # -- 產生「針對某位玩家」的畫面資料 -------------------------------------
    def view_for(self, pid):
        with self.lock:
            self._maybe_advance_reading()
            self._prune_reactions(time.time())
            you = self._player(pid)
            in_game = you is not None
            your_role = self.roles.get(pid) if in_game else None

            players_pub = []
            for p in self.players:
                item = {
                    "id": p["id"],
                    "name": p["name"],
                    "avatar": p["avatar"],
                    "color": p["color"],
                    "score": p["score"],
                    "is_host": p["id"] == self.host_id,
                    "is_you": p["id"] == pid,
                }
                # 只有在結算階段才公開所有人的角色
                if self.phase == "result":
                    item["role"] = self.roles.get(p["id"])
                    item["role_label"] = ROLE_LABELS.get(self.roles.get(p["id"]), "觀戰")
                players_pub.append(item)

            view = {
                "phase": self.phase,
                "round_no": self.round_no,
                "in_game": in_game,
                "you": None,
                "players": players_pub,
                "min_players": 3,
                "reading_seconds": READING_SECONDS,
                # 讓大廳／結算畫面預告「下一位大聰明是誰」（依座位輪換）
                "next_guesser_id": self._peek_next_guesser()
                if self.phase in ("lobby", "result") else None,
                # 還沒過期的即時吐槽，前端自己過濾掉已經播過的 id
                "reactions": [
                    {"id": r["id"], "from_id": r["from_id"], "text": r["text"]}
                    for r in self.reactions
                ],
            }

            if in_game:
                # 猜測/結算階段才加入的人，這回合沒有身分 → 觀戰，下一回合自動編入
                spectator = self.phase != "lobby" and pid not in self.roles
                view["you"] = {
                    "id": you["id"],
                    "name": you["name"],
                    "avatar": you["avatar"],
                    "color": you["color"],
                    "score": you["score"],
                    "is_host": pid == self.host_id,
                    "role": your_role,
                    "role_label": ROLE_LABELS.get(your_role),
                    "spectator": spectator,
                }

            # 卡片：正面題目大家都看得到；背面解釋只有老實人（閱讀/發言/猜測）或結算時公開。
            # 預覽階段連老實人都還看不到背面（要按「開始回合」開始倒數才翻面）。
            if self.card and self.phase != "lobby":
                # hints 是給所有人看的瞎掰方向；real_hint 只供審題校對，絕不能送出去
                card = {
                    "topic": self.card["topic"],
                    "difficulty": self.card["difficulty"],
                    "hints": list(self.card["hints"]),
                }
                honest_can_see = your_role == ROLE_HONEST and self.phase in (
                    "reading", "speaking", "guessing",
                )
                reveal_desc = honest_can_see or self.phase == "result"
                if reveal_desc:
                    card["description"] = self.card["description"]
                view["card"] = card

            if self.phase == "preview":
                view["preview"] = {"is_host": pid == self.host_id}

            if self.phase == "reading":
                view["reading"] = {
                    "remaining": max(0, int(round(self.reading_ends_at - time.time()))),
                    "is_honest": your_role == ROLE_HONEST,
                }

            if self.phase == "speaking":
                cur = (
                    self.speaking_order[self.speaking_index]
                    if self.speaking_index < len(self.speaking_order)
                    else None
                )
                view["speaking"] = {
                    "current_id": cur,
                    "index": self.speaking_index,
                    "total": len(self.speaking_order),
                    "is_current": cur == pid,
                    "is_guesser": your_role == ROLE_GUESSER,
                }

            if self.phase == "guessing":
                view["guessing"] = {
                    "is_guesser": your_role == ROLE_GUESSER,
                    "guesser_id": self._guesser_id(),
                    # 可選對象：這回合真的有參與的人（排除大聰明自己與中途才進來的觀戰者）
                    "candidates": [
                        {"id": p["id"], "name": p["name"], "avatar": p["avatar"], "color": p["color"]}
                        for p in self.players
                        if self.roles.get(p["id"]) in (ROLE_HONEST, ROLE_BLUFFER)
                    ],
                }

            if self.phase == "result" and self.result:
                r = self.result
                view["result"] = {
                    "honest_id": r["honest_id"],
                    "guesser_id": r["guesser_id"],
                    "suspect_id": r["suspect_id"],
                    "callout_id": r["callout_id"],
                    "callout_effect": r["callout_effect"],
                    "correct": r["correct"],
                    "winners": r["winners"],
                    "winner_label": r["winner_label"],
                    "difficulty": r["difficulty"],
                    "deltas": r["deltas"],
                    "callout_bonus": r["callout_bonus"],
                    "callout_penalty": r["callout_penalty"],
                    "you_won": pid in r["winners"],
                }

            return view


GAME = Game()


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 安靜一點
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path):
        ext = os.path.splitext(path)[1]
        ctype = STATIC_TYPES.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            return self._send_file(os.path.join(STATIC_DIR, "index.html"))
        if path == "/api/state":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            pid = (qs.get("pid") or [""])[0]
            return self._send_json(GAME.view_for(pid))
        # 靜態檔案
        safe = os.path.normpath(path).lstrip("/")
        candidate = os.path.join(HERE, safe)
        if candidate.startswith(STATIC_DIR) and os.path.isfile(candidate):
            return self._send_file(candidate)
        if path.startswith("/static/"):
            f = os.path.join(STATIC_DIR, os.path.basename(path))
            if os.path.isfile(f):
                return self._send_file(f)
        self.send_response(404)
        self.end_headers()

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        data = self._read_body()
        with GAME.lock:
            if path == "/api/join":
                pid = GAME.join(data.get("name"))
                return self._send_json({"ok": True, "pid": pid})

            pid = data.get("pid")
            is_host = pid == GAME.host_id

            if path == "/api/react":
                ok, err = GAME.add_reaction(pid)
                return self._send_json({"ok": ok, "error": err})

            if path == "/api/start":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以開始"}, 403)
                ok, err = GAME.start_round()
                return self._send_json({"ok": ok, "error": err})

            if path == "/api/begin_reading":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以開始回合"}, 403)
                GAME.begin_reading()
                return self._send_json({"ok": True})

            if path == "/api/skip_card":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以跳過"}, 403)
                GAME.skip_card()
                return self._send_json({"ok": True})

            if path == "/api/skip_reading":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以跳過"}, 403)
                GAME.skip_reading()
                return self._send_json({"ok": True})

            if path == "/api/advance":
                # 房主或目前發言者可以換下一位
                cur = None
                if GAME.phase == "speaking" and GAME.speaking_index < len(GAME.speaking_order):
                    cur = GAME.speaking_order[GAME.speaking_index]
                if not (is_host or pid == cur):
                    return self._send_json({"ok": False, "error": "沒有權限"}, 403)
                GAME.advance_speaker()
                return self._send_json({"ok": True})

            if path == "/api/to_guessing":
                if not (is_host or pid == GAME._guesser_id()):
                    return self._send_json({"ok": False, "error": "沒有權限"}, 403)
                GAME.go_to_guessing()
                return self._send_json({"ok": True})

            if path == "/api/guess":
                ok, err = GAME.submit_guess(
                    pid, data.get("suspect_id"), data.get("callout_id")
                )
                return self._send_json({"ok": ok, "error": err})

            if path == "/api/next_round":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以繼續"}, 403)
                ok, err = GAME.next_round()
                return self._send_json({"ok": ok, "error": err})

            if path == "/api/back_to_lobby":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以操作"}, 403)
                GAME.back_to_lobby()
                return self._send_json({"ok": True})

            if path == "/api/reset_scores":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以重置分數"}, 403)
                GAME.reset_scores()
                return self._send_json({"ok": True})

            if path == "/api/reset":
                if not is_host:
                    return self._send_json({"ok": False, "error": "只有房主可以操作"}, 403)
                GAME.reset_all()
                GAME.save()   # 清空也要落地，不然重啟又把舊名單接回來
                return self._send_json({"ok": True})

        self.send_response(404)
        self.end_headers()


def _is_real_lan(ip):
    """是否為真正可讓手機連的私有區網位址（排除 loopback / link-local / VPN 常用段）。"""
    if ip.startswith("127.") or ip.startswith("169.254."):
        return False
    if ip.startswith("198.18.") or ip.startswith("198.19."):
        return False  # RFC2544 benchmark 段，很多 VPN 拿來當虛擬介面
    if ip.startswith("192.168."):
        return True
    if ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            return 16 <= second <= 31
        except (ValueError, IndexError):
            return False
    return False


def list_lan_ips():
    """列出所有候選區網位址，優先 192.168，再來 10 / 172。"""
    ips = []

    # 1) 解析 ifconfig / ip addr（macOS、Linux）
    for cmd in ("ifconfig", "ip -4 addr"):
        try:
            import subprocess
            out = subprocess.run(
                cmd.split(), capture_output=True, text=True, timeout=3
            ).stdout
        except Exception:
            continue
        import re
        for m in re.findall(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out):
            if m not in ips:
                ips.append(m)
        if ips:
            break

    # 2) 後備：連外 socket 取得來源 IP（VPN 開啟時可能不準）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        guess = s.getsockname()[0]
        s.close()
        if guess not in ips:
            ips.append(guess)
    except Exception:
        pass

    real = [ip for ip in ips if _is_real_lan(ip)]

    def rank(ip):
        return (0 if ip.startswith("192.168.") else 1 if ip.startswith("10.") else 2)

    real.sort(key=rank)
    return real


def main():
    ips = list_lan_ips()
    restored = GAME.load()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 48)
    print("  瞎掰王 已啟動！")
    if restored:
        names = "、".join(
            "%s %s(%d分)" % (p["avatar"], p["name"], p["score"]) for p in GAME.players
        )
        print("  已接回上次的房間：%d 位玩家" % restored)
        print("    %s" % names)
        print("    手機不用重新加入，等下一次輪詢就會自己接上")
    print(f"  本機：      http://localhost:{PORT}")
    if ips:
        print(f"  手機（同網）：http://{ips[0]}:{PORT}")
        for extra in ips[1:]:
            print(f"     其他候選：  http://{extra}:{PORT}")
    else:
        print("  手機（同網）：找不到區網位址（可能開了 VPN）")
        print("     請自行查電腦的 192.168.x.x 位址，用它加上 :{} 連線".format(PORT))
    print("  （手機需與這台電腦連到同一個 Wi-Fi，且手機不要開 VPN）")
    print("  按 Ctrl+C 結束")
    print("=" * 48)
    sys.stdout.flush()   # 導向檔案時 stdout 是全緩衝，不 flush 會看不到這段
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n再見！")


if __name__ == "__main__":
    main()
