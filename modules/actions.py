"""Backend Actions : trading et suivi de portefeuille via brokers API.

Brokers supportes :
- alpaca_paper : Alpaca Paper Trading (simulation gratuite, US stocks)
- alpaca_live  : Alpaca Live (vrai argent, US stocks, comptes US uniquement)
- binance      : Binance (crypto, mondial)
- none         : aucun broker connecte (mode lecture seule via yfinance)

ARCHITECTURE
============
- Une classe abstraite `Broker` definit l'interface.
- Implementations : `AlpacaBroker`, `BinanceBroker`, `NoneBroker`.
- `get_broker(username)` instancie le bon en fonction de la config DB.
- Cles API : LECTURE depuis env vars > keyring > rien (aucune ecriture
  dans users_db.json — securite + portabilite identique au mail/crypto).
- Toutes les operations sensibles (buy/sell/close) doivent passer par
  permissions_mixin (le module ne fait JAMAIS d'auto-execute lui-meme).

SAFETY RAILS
============
- max_order_eur : montant max par ordre
- max_orders_day : nb max d'ordres par jour
- Verifies par check_safety_rails() avant chaque execution.

DB
==
SQLite finance.db (partagee avec finance.py) — ajoute deux tables :
  - positions       : positions actuelles (snapshot)
  - orders_log      : historique des ordres passes
  - watchlist       : symboles a suivre

PORTABILITE
===========
- Cles API en variables d'env : ASSISTANT_ALPACA_KEY,
  ASSISTANT_ALPACA_SECRET, ASSISTANT_BINANCE_KEY,
  ASSISTANT_BINANCE_SECRET. Cf .env.example.
"""
from __future__ import annotations
import os
import json
import sqlite3
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Optional, List

from modules.logger import get_logger

_log = get_logger("actions")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE_DIR, "finance.db")  # partage avec finance.py


# ===========================================================================
# Modele de donnees
# ===========================================================================

@dataclass
class Position:
    symbol: str = ""
    qty: float = 0.0
    avg_price: float = 0.0
    market_price: float = 0.0
    unrealized_pl: float = 0.0
    unrealized_pl_pct: float = 0.0
    currency: str = "USD"
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Order:
    id: Optional[int] = None
    broker: str = ""
    broker_order_id: str = ""
    symbol: str = ""
    side: str = ""           # "buy" / "sell"
    qty: float = 0.0
    order_type: str = "market"  # "market" / "limit"
    limit_price: float = 0.0
    status: str = "pending"     # "pending" / "filled" / "cancelled" / "rejected"
    filled_price: float = 0.0
    requested_at: str = field(default_factory=lambda: datetime.now().isoformat())
    filled_at: str = ""
    notes: str = ""


# ===========================================================================
# DB
# ===========================================================================

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS orders_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            broker TEXT,
            broker_order_id TEXT,
            symbol TEXT,
            side TEXT,
            qty REAL,
            order_type TEXT,
            limit_price REAL,
            status TEXT,
            filled_price REAL,
            requested_at TEXT,
            filled_at TEXT,
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ord_symbol ON orders_log(symbol);
        CREATE INDEX IF NOT EXISTS idx_ord_status ON orders_log(status);
        CREATE INDEX IF NOT EXISTS idx_ord_date ON orders_log(requested_at);

        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            note TEXT,
            added_at TEXT
        );
        """)


_init_db()


def log_order(o: Order) -> Optional[int]:
    try:
        with _conn() as c:
            cur = c.execute(
                """INSERT INTO orders_log
                   (broker, broker_order_id, symbol, side, qty, order_type,
                    limit_price, status, filled_price, requested_at,
                    filled_at, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (o.broker, o.broker_order_id, o.symbol, o.side, o.qty,
                 o.order_type, o.limit_price, o.status, o.filled_price,
                 o.requested_at, o.filled_at, o.notes))
            return cur.lastrowid
    except Exception as e:
        _log.error("log_order : %s", e)
        return None


def update_order_status(order_id: int, status: str, filled_price: float = None,
                        broker_order_id: str = None) -> bool:
    fields = {"status": status}
    if filled_price is not None:
        fields["filled_price"] = float(filled_price)
        fields["filled_at"] = datetime.now().isoformat()
    if broker_order_id:
        fields["broker_order_id"] = broker_order_id
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [order_id]
    try:
        with _conn() as c:
            c.execute(f"UPDATE orders_log SET {sets} WHERE id=?", vals)
        return True
    except Exception as e:
        _log.error("update_order_status : %s", e)
        return False


def list_orders(limit: int = 50, status: str = None) -> List[Order]:
    q = "SELECT * FROM orders_log"
    args = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY requested_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
        return [Order(**dict(r)) for r in rows]


def add_to_watchlist(symbol: str, note: str = "") -> bool:
    if not symbol:
        return False
    try:
        with _conn() as c:
            c.execute("INSERT OR IGNORE INTO watchlist (symbol, note, added_at) "
                      "VALUES (?, ?, ?)",
                      (symbol.upper(), note, datetime.now().isoformat()))
        return True
    except Exception as e:
        _log.error("add_to_watchlist : %s", e)
        return False


def list_watchlist() -> list:
    with _conn() as c:
        rows = c.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()
        return [dict(r) for r in rows]


def remove_from_watchlist(symbol: str) -> bool:
    try:
        with _conn() as c:
            c.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))
        return True
    except Exception:
        return False


# ===========================================================================
# Cles API : env > keyring > absent
# ===========================================================================

def _get_api_key(env_name: str, kr_key: str, username: str) -> str:
    """Cherche une cle API : 1) env var, 2) keyring, 3) ''. """
    # 1. ENV per-user (prioritaire)
    env_user = os.environ.get(f"{env_name}_{(username or '').upper()}")
    if env_user:
        return env_user
    # 2. ENV global
    env_g = os.environ.get(env_name)
    if env_g:
        return env_g
    # 3. Keyring
    try:
        import keyring
        v = keyring.get_password(f"AssistantAI.{kr_key}", username)
        if v:
            return v
    except Exception:
        pass
    return ""


# ===========================================================================
# Brokers : interface + implementations
# ===========================================================================

class Broker:
    """Interface abstraite. Toutes les methodes peuvent renvoyer None /
    soulever en cas de probleme. La couche superieure (UI / mixin) doit
    catcher proprement."""
    name = "abstract"
    is_paper = True

    def is_connected(self) -> bool:
        return False

    def list_positions(self) -> List[Position]:
        return []

    def get_account(self) -> dict:
        """Renvoie un dict {cash, buying_power, equity, currency}."""
        return {"cash": 0.0, "buying_power": 0.0, "equity": 0.0,
                "currency": "USD"}

    def submit_order(self, symbol: str, qty: float, side: str,
                     order_type: str = "market",
                     limit_price: float = 0.0) -> dict:
        """Renvoie {ok: bool, broker_order_id: str, error: str}."""
        return {"ok": False, "broker_order_id": "", "error": "no broker"}

    def cancel_order(self, broker_order_id: str) -> bool:
        return False

    def get_quote(self, symbol: str) -> float:
        """Cours en direct du symbole (0 si indispo)."""
        return 0.0


# ---------------------------------------------------------------- Alpaca

class AlpacaBroker(Broker):
    """Alpaca Markets (US stocks).

    Paper trading : https://paper-api.alpaca.markets — gratuit, sans risque.
    Live trading  : https://api.alpaca.markets   — vrai argent, compte US.
    """
    name = "alpaca"

    def __init__(self, api_key: str, secret: str, paper: bool = True):
        self.api_key = api_key
        self.secret = secret
        self.is_paper = paper
        self.base = ("https://paper-api.alpaca.markets" if paper
                     else "https://api.alpaca.markets")
        self.data_base = "https://data.alpaca.markets"

    def _headers(self):
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json",
        }

    def is_connected(self) -> bool:
        return bool(self.api_key and self.secret)

    def get_account(self) -> dict:
        if not self.is_connected():
            return super().get_account()
        try:
            import requests as _rq
            r = _rq.get(f"{self.base}/v2/account",
                        headers=self._headers(), timeout=10)
            if r.status_code == 200:
                a = r.json()
                return {
                    "cash": float(a.get("cash", 0) or 0),
                    "buying_power": float(a.get("buying_power", 0) or 0),
                    "equity": float(a.get("equity", 0) or 0),
                    "currency": a.get("currency", "USD"),
                }
        except Exception as e:
            _log.warning("Alpaca account : %s", e)
        return super().get_account()

    def list_positions(self) -> List[Position]:
        if not self.is_connected():
            return []
        try:
            import requests as _rq
            r = _rq.get(f"{self.base}/v2/positions",
                        headers=self._headers(), timeout=10)
            if r.status_code == 200:
                out = []
                for p in r.json():
                    out.append(Position(
                        symbol=p.get("symbol", ""),
                        qty=float(p.get("qty", 0) or 0),
                        avg_price=float(p.get("avg_entry_price", 0) or 0),
                        market_price=float(p.get("current_price", 0) or 0),
                        unrealized_pl=float(p.get("unrealized_pl", 0) or 0),
                        unrealized_pl_pct=(
                            float(p.get("unrealized_plpc", 0) or 0) * 100),
                        currency="USD",
                    ))
                return out
        except Exception as e:
            _log.warning("Alpaca positions : %s", e)
        return []

    def submit_order(self, symbol: str, qty: float, side: str,
                     order_type: str = "market",
                     limit_price: float = 0.0) -> dict:
        if not self.is_connected():
            return {"ok": False, "broker_order_id": "",
                    "error": "Alpaca non connecte"}
        try:
            import requests as _rq
            payload = {
                "symbol": symbol.upper(),
                "qty": str(qty),
                "side": side,
                "type": order_type,
                "time_in_force": "day",
            }
            if order_type == "limit" and limit_price:
                payload["limit_price"] = str(limit_price)
            r = _rq.post(f"{self.base}/v2/orders",
                         headers=self._headers(),
                         json=payload, timeout=15)
            if r.status_code in (200, 201):
                d = r.json()
                return {"ok": True, "broker_order_id": d.get("id", ""),
                        "error": ""}
            return {"ok": False, "broker_order_id": "",
                    "error": f"HTTP {r.status_code} : {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "broker_order_id": "", "error": str(e)}

    def cancel_order(self, broker_order_id: str) -> bool:
        if not self.is_connected() or not broker_order_id:
            return False
        try:
            import requests as _rq
            r = _rq.delete(f"{self.base}/v2/orders/{broker_order_id}",
                           headers=self._headers(), timeout=10)
            return r.status_code in (200, 204)
        except Exception as e:
            _log.warning("Alpaca cancel : %s", e)
            return False

    def get_quote(self, symbol: str) -> float:
        # Tente Alpaca data API d'abord, fallback yfinance
        try:
            import requests as _rq
            r = _rq.get(
                f"{self.data_base}/v2/stocks/{symbol.upper()}/quotes/latest",
                headers=self._headers(), timeout=8)
            if r.status_code == 200:
                q = r.json().get("quote") or {}
                bid = float(q.get("bp", 0) or 0)
                ask = float(q.get("ap", 0) or 0)
                if bid and ask:
                    return (bid + ask) / 2.0
                if ask:
                    return ask
        except Exception as e:
            _log.info("Alpaca quote : %s", e)
        return _quote_yfinance(symbol)


# ---------------------------------------------------------------- Binance

class BinanceBroker(Broker):
    """Binance (crypto). Endpoints REST publics pour cours, signes pour
    ordres. Pour rester simple/portable : le mode 'lecture seule' est
    actif sans cle (pour quotes), trading necessite cle + secret."""
    name = "binance"
    is_paper = False  # par defaut live (Binance n'a pas de "testnet" trivial)

    def __init__(self, api_key: str = "", secret: str = ""):
        self.api_key = api_key
        self.secret = secret
        self.base = "https://api.binance.com"

    def is_connected(self) -> bool:
        return bool(self.api_key and self.secret)

    def get_account(self) -> dict:
        if not self.is_connected():
            return super().get_account()
        try:
            import requests as _rq
            import time as _time
            import hmac as _hmac
            import hashlib as _hash
            ts = int(_time.time() * 1000)
            qs = f"timestamp={ts}"
            sig = _hmac.new(self.secret.encode(), qs.encode(),
                            _hash.sha256).hexdigest()
            r = _rq.get(f"{self.base}/api/v3/account?{qs}&signature={sig}",
                        headers={"X-MBX-APIKEY": self.api_key}, timeout=10)
            if r.status_code == 200:
                a = r.json()
                # Conversion : USDT free + locked dispo. Equity = somme.
                bal = a.get("balances", [])
                cash = 0.0
                for b in bal:
                    if b.get("asset") == "USDT":
                        cash = float(b.get("free", 0) or 0) \
                               + float(b.get("locked", 0) or 0)
                        break
                return {"cash": cash, "buying_power": cash,
                        "equity": cash, "currency": "USDT"}
        except Exception as e:
            _log.warning("Binance account : %s", e)
        return super().get_account()

    def submit_order(self, symbol: str, qty: float, side: str,
                     order_type: str = "market",
                     limit_price: float = 0.0) -> dict:
        if not self.is_connected():
            return {"ok": False, "broker_order_id": "",
                    "error": "Binance non connecte"}
        try:
            import requests as _rq
            import time as _time
            import hmac as _hmac
            import hashlib as _hash
            ts = int(_time.time() * 1000)
            params = {
                "symbol": symbol.upper(),
                "side": side.upper(),
                "type": "MARKET" if order_type == "market" else "LIMIT",
                "quantity": str(qty),
                "timestamp": ts,
            }
            if order_type == "limit":
                params["price"] = str(limit_price)
                params["timeInForce"] = "GTC"
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            sig = _hmac.new(self.secret.encode(), qs.encode(),
                            _hash.sha256).hexdigest()
            r = _rq.post(
                f"{self.base}/api/v3/order?{qs}&signature={sig}",
                headers={"X-MBX-APIKEY": self.api_key}, timeout=15)
            if r.status_code == 200:
                d = r.json()
                return {"ok": True,
                        "broker_order_id": str(d.get("orderId", "")),
                        "error": ""}
            return {"ok": False, "broker_order_id": "",
                    "error": f"HTTP {r.status_code} : {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "broker_order_id": "", "error": str(e)}

    def get_quote(self, symbol: str) -> float:
        try:
            import requests as _rq
            r = _rq.get(
                f"{self.base}/api/v3/ticker/price?symbol={symbol.upper()}",
                timeout=8)
            if r.status_code == 200:
                return float(r.json().get("price", 0) or 0)
        except Exception as e:
            _log.info("Binance quote : %s", e)
        return 0.0


# ---------------------------------------------------------------- Aucun

class NoneBroker(Broker):
    """Pas de broker : seules les fonctions read-only via yfinance marchent."""
    name = "none"
    is_paper = True

    def is_connected(self) -> bool:
        return False

    def get_quote(self, symbol: str) -> float:
        return _quote_yfinance(symbol)


# ===========================================================================
# Quote fallback : yfinance (gratuit, sans cle) ou stooq
# ===========================================================================

def _quote_yfinance(symbol: str) -> float:
    """Recupere le dernier cours via yfinance (paquet pip yfinance) si
    installe. Renvoie 0.0 sinon."""
    try:
        import yfinance as yf  # type: ignore
        t = yf.Ticker(symbol)
        h = t.history(period="1d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception as e:
        _log.info("yfinance quote %s : %s", symbol, e)
    # Fallback secondaire : Stooq via CSV public
    try:
        import requests as _rq
        r = _rq.get(
            f"https://stooq.com/q/l/?s={symbol.lower()}.us&f=sd2t2ohlcv&h&e=csv",
            timeout=8)
        if r.status_code == 200:
            lines = r.text.strip().splitlines()
            if len(lines) >= 2:
                cols = lines[1].split(",")
                if len(cols) >= 7:
                    try:
                        return float(cols[6])
                    except Exception:
                        pass
    except Exception:
        pass
    return 0.0


# ===========================================================================
# Factory : instancie le bon broker en fonction de la config DB
# ===========================================================================

def get_broker(username: str):
    """Renvoie une instance Broker en fonction de la config DB de l'utilisateur."""
    import database
    cfg = database.get_broker_config(username) or {}
    code = cfg.get("broker", "none")
    if code in ("alpaca_paper", "alpaca_live"):
        api = _get_api_key("ASSISTANT_ALPACA_KEY", "alpaca_key", username)
        sec = _get_api_key("ASSISTANT_ALPACA_SECRET", "alpaca_secret",
                           username)
        if not api or not sec:
            _log.warning("Alpaca cles manquantes (env / keyring) — fallback NoneBroker")
            return NoneBroker()
        return AlpacaBroker(api, sec, paper=(code == "alpaca_paper"))
    if code == "binance":
        api = _get_api_key("ASSISTANT_BINANCE_KEY", "binance_key", username)
        sec = _get_api_key("ASSISTANT_BINANCE_SECRET", "binance_secret",
                           username)
        return BinanceBroker(api, sec)
    return NoneBroker()


# ===========================================================================
# Safety rails (verifies AVANT execution d'un ordre)
# ===========================================================================

def check_safety_rails(username: str, symbol: str, qty: float,
                       side: str, est_price: float = 0.0) -> dict:
    """Verifie les garde-fous avant execution. Renvoie {ok, reason}.

    Regles :
    - max_order_eur : si cfg defini, refus si qty * est_price > max
    - max_orders_day : si cfg defini, refus si nb d'ordres aujourd'hui >= max
    """
    import database
    cfg = database.get_broker_config(username) or {}
    # 1. Montant
    max_eur = float(cfg.get("max_order_eur") or 0)
    if max_eur > 0 and est_price > 0:
        notional = abs(qty * est_price)
        if notional > max_eur:
            return {"ok": False,
                    "reason": f"Ordre {notional:.0f}€ > limite {max_eur:.0f}€"}
    # 2. Nb d'ordres aujourd'hui
    max_day = int(cfg.get("max_orders_day") or 0)
    if max_day > 0:
        today = date.today().isoformat()
        with _conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM orders_log "
                "WHERE requested_at >= ?", (today,)).fetchone()[0]
        if n >= max_day:
            return {"ok": False,
                    "reason": f"Limite {max_day} ordres/jour atteinte ({n})"}
    return {"ok": True, "reason": ""}


# ===========================================================================
# Operations haut niveau (utilisees par finance_mixin / permissions_mixin)
# ===========================================================================

def execute_order(username: str, symbol: str, qty: float,
                  side: str, order_type: str = "market",
                  limit_price: float = 0.0) -> dict:
    """Execute un ordre apres verification des safety rails. Logge le
    resultat. Renvoie le dict {ok, broker_order_id, error, order_db_id}."""
    broker = get_broker(username)
    if not broker.is_connected():
        return {"ok": False, "error": "broker non configure",
                "broker_order_id": "", "order_db_id": None}
    # Estimation prix (pour la safety rail)
    est = limit_price if (order_type == "limit" and limit_price) \
        else broker.get_quote(symbol)
    safety = check_safety_rails(username, symbol, qty, side, est)
    if not safety["ok"]:
        # Logge l'ordre refuse
        o = Order(broker=broker.name, symbol=symbol.upper(), side=side,
                  qty=qty, order_type=order_type, limit_price=limit_price,
                  status="rejected", notes=safety["reason"])
        log_order(o)
        return {"ok": False, "error": safety["reason"],
                "broker_order_id": "", "order_db_id": None}
    # Ordre reel
    o = Order(broker=broker.name, symbol=symbol.upper(), side=side,
              qty=qty, order_type=order_type, limit_price=limit_price,
              status="pending")
    db_id = log_order(o)
    res = broker.submit_order(symbol, qty, side, order_type, limit_price)
    if res.get("ok"):
        update_order_status(db_id, "submitted",
                            broker_order_id=res.get("broker_order_id"))
    else:
        update_order_status(db_id, "rejected")
    return {"ok": res.get("ok"),
            "broker_order_id": res.get("broker_order_id"),
            "error": res.get("error"), "order_db_id": db_id}


def portfolio_snapshot(username: str) -> dict:
    """Renvoie {account, positions, total_value, day_pl} pour la vue."""
    broker = get_broker(username)
    acc = broker.get_account()
    positions = broker.list_positions()
    total_value = float(acc.get("equity", 0) or 0)
    day_pl = sum(p.unrealized_pl for p in positions)
    return {
        "account": acc,
        "positions": positions,
        "total_value": total_value,
        "day_pl": day_pl,
        "broker_name": broker.name,
        "is_paper": getattr(broker, "is_paper", True),
        "is_connected": broker.is_connected(),
    }
