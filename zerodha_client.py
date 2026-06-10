import os
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from kiteconnect import KiteConnect, KiteTicker
from kiteconnect.exceptions import (
    TokenException, InputException, NetworkException, GeneralException
)

from config import (
    KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN,
    NIFTY_TOKEN, SENSEX_TOKEN, NFO, BFO, NSE, BSE,
    get_strike_range, get_nearest_expiry, get_expiry_list, STRIKE_INTERVALS
)

logger = logging.getLogger(__name__)


class ZerodhaClient:
    """
    Full-featured Zerodha KiteConnect wrapper for F&O analysis.
    Handles session management, market data, option chain, and order placement.
    """

    def __init__(self, api_key: str = None, api_secret: str = None, access_token: str = None):
        self.api_key = api_key or KITE_API_KEY
        self.api_secret = api_secret or KITE_API_SECRET
        self._access_token = access_token or KITE_ACCESS_TOKEN

        self.kite = KiteConnect(api_key=self.api_key)
        if self._access_token:
            self.kite.set_access_token(self._access_token)

        # Instrument cache: exchange -> list of instruments
        self._instrument_cache: Dict[str, List[Dict]] = {}
        self._instrument_cache_date: Optional[date] = None

    # -------------------------------------------------------------------------
    # Authentication
    # -------------------------------------------------------------------------

    def get_login_url(self) -> str:
        """Return Zerodha Kite login URL for OAuth flow."""
        return self.kite.login_url()

    def generate_session(self, request_token: str) -> Dict[str, Any]:
        """
        Exchange request_token for access_token.
        Stores the access_token internally and returns session data.
        """
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            self._access_token = data["access_token"]
            self.kite.set_access_token(self._access_token)
            logger.info("Session generated successfully for user: %s", data.get("user_id"))
            return {
                "access_token": data["access_token"],
                "user_id": data.get("user_id"),
                "user_name": data.get("user_name"),
                "email": data.get("email"),
                "user_type": data.get("user_type"),
                "login_time": data.get("login_time"),
            }
        except TokenException as e:
            logger.error("Token error during session generation: %s", e)
            raise
        except Exception as e:
            logger.error("Error generating session: %s", e)
            raise

    def is_authenticated(self) -> bool:
        """Check if client has a valid access token."""
        return bool(self._access_token)

    def logout(self) -> bool:
        """
        Invalidate the current access token on Zerodha and clear it locally.
        Returns True if Zerodha confirmed the invalidation.
        """
        invalidated = False
        try:
            if self._access_token:
                self.kite.invalidate_access_token()
                invalidated = True
                logger.info("Zerodha access token invalidated")
        except Exception as e:
            logger.warning("Error invalidating access token (clearing locally anyway): %s", e)
        finally:
            self._access_token = None
            self.kite.set_access_token(None)
        return invalidated

    def get_access_token(self) -> Optional[str]:
        return self._access_token

    # -------------------------------------------------------------------------
    # Market Quotes
    # -------------------------------------------------------------------------

    def get_quote(self, symbols: List[str]) -> Dict[str, Any]:
        """
        Fetch full quote (LTP, OHLC, volume, OI) for given symbols.
        symbols format: ["NSE:NIFTY 50", "BSE:SENSEX"]
        Returns dict keyed by symbol.
        """
        try:
            return self.kite.quote(symbols)
        except TokenException:
            logger.warning("Token expired, cannot fetch quote")
            raise
        except Exception as e:
            logger.error("Error fetching quote for %s: %s", symbols, e)
            raise

    def get_ltp(self, symbols: List[str]) -> Dict[str, float]:
        """Fetch only LTP for the given symbols. Returns {symbol: ltp}."""
        try:
            data = self.kite.ltp(symbols)
            return {sym: info["last_price"] for sym, info in data.items()}
        except Exception as e:
            logger.error("Error fetching LTP: %s", e)
            raise

    def get_index_quote(self, index: str) -> Dict[str, Any]:
        """
        Get quote for NIFTY or SENSEX index.
        Returns normalized dict with spot, change, change_pct, high, low, open.
        """
        symbol_map = {
            "NIFTY": "NSE:NIFTY 50",
            "SENSEX": "BSE:SENSEX",
            "BANKNIFTY": "NSE:NIFTY BANK",
            "VIX": "NSE:INDIA VIX",
        }
        symbol = symbol_map.get(index.upper())
        if not symbol:
            raise ValueError(f"Unknown index: {index}")

        quote = self.get_quote([symbol])
        data = quote.get(symbol, {})
        ohlc = data.get("ohlc", {})

        return {
            "symbol": index.upper(),
            "last_price": data.get("last_price", 0),
            "change": data.get("net_change", 0),
            "change_pct": round(
                (data.get("net_change", 0) / ohlc.get("close", 1)) * 100, 2
            ) if ohlc.get("close") else 0,
            "open": ohlc.get("open", 0),
            "high": ohlc.get("high", 0),
            "low": ohlc.get("low", 0),
            "close": ohlc.get("close", 0),
            "volume": data.get("volume", 0),
            "oi": data.get("oi", 0),
            "timestamp": str(data.get("timestamp", "")),
        }

    # -------------------------------------------------------------------------
    # Instruments
    # -------------------------------------------------------------------------

    def get_instruments(self, exchange: str = NFO) -> List[Dict]:
        """
        Fetch and cache instrument list for the given exchange.
        Refreshes cache daily.
        """
        today = date.today()
        if (
            exchange in self._instrument_cache
            and self._instrument_cache_date == today
        ):
            return self._instrument_cache[exchange]

        try:
            instruments = self.kite.instruments(exchange)
            self._instrument_cache[exchange] = instruments
            self._instrument_cache_date = today
            return instruments
        except Exception as e:
            logger.error("Error fetching instruments for %s: %s", exchange, e)
            raise

    def get_nfo_instruments(self, underlying: str) -> List[Dict]:
        """
        Filter NFO instruments for a given underlying (e.g., 'NIFTY', 'BANKNIFTY').
        Returns only options and futures.
        """
        instruments = self.get_instruments(NFO)
        return [
            i for i in instruments
            if i.get("name", "").upper() == underlying.upper()
        ]

    def get_bfo_instruments(self, underlying: str) -> List[Dict]:
        """Filter BFO instruments for SENSEX."""
        instruments = self.get_instruments(BFO)
        return [
            i for i in instruments
            if i.get("name", "").upper() == underlying.upper()
        ]

    def find_instrument(
        self,
        exchange: str,
        trading_symbol: str
    ) -> Optional[Dict]:
        """Find instrument by exchange and trading symbol."""
        instruments = self.get_instruments(exchange)
        for inst in instruments:
            if inst.get("tradingsymbol") == trading_symbol:
                return inst
        return None

    def get_option_instruments(
        self,
        index: str,
        expiry: str,
        option_type: str = None
    ) -> List[Dict]:
        """
        Get all option instruments for given index, expiry, and optionally type (CE/PE).
        expiry: 'YYYY-MM-DD'
        """
        exchange = BFO if index.upper() == "SENSEX" else NFO
        instruments = self.get_instruments(exchange)
        expiry_date = date.fromisoformat(expiry)

        result = []
        for inst in instruments:
            if inst.get("name", "").upper() != index.upper():
                continue
            if inst.get("instrument_type") not in ("CE", "PE"):
                continue
            if inst.get("expiry") != expiry_date:
                continue
            if option_type and inst.get("instrument_type") != option_type:
                continue
            result.append(inst)

        return sorted(result, key=lambda x: x.get("strike", 0))

    # -------------------------------------------------------------------------
    # Option Chain
    # -------------------------------------------------------------------------

    def get_option_chain(self, index: str, expiry: str) -> Dict[str, Any]:
        """
        Build full option chain for NIFTY or SENSEX for given expiry.
        Returns dict with strikes data containing CE and PE sides.
        """
        # Get spot price first
        try:
            spot_data = self.get_index_quote(index)
            spot = spot_data["last_price"]
        except Exception:
            spot = 22000 if index.upper() == "NIFTY" else 73000

        # Get all option instruments for this expiry
        options = self.get_option_instruments(index, expiry)
        if not options:
            return self._empty_option_chain(index, spot, expiry)

        # Build symbol list for quotes
        exchange = BFO if index.upper() == "SENSEX" else NFO
        symbols = [f"{exchange}:{inst['tradingsymbol']}" for inst in options]

        # Fetch quotes in batches (Kite allows max 500 per request)
        batch_size = 400
        all_quotes = {}
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i: i + batch_size]
            try:
                quotes = self.kite.quote(batch)
                all_quotes.update(quotes)
            except Exception as e:
                logger.warning("Error fetching batch quotes: %s", e)

        # Organize by strike
        chain = {}
        for inst in options:
            strike = inst["strike"]
            itype = inst["instrument_type"]  # CE or PE
            sym = f"{exchange}:{inst['tradingsymbol']}"
            q = all_quotes.get(sym, {})
            ohlc = q.get("ohlc", {})

            current_oi = q.get("oi", 0) or 0
            # oi_day_low ≈ opening OI (closest proxy to prev-day close available in quotes)
            prev_oi = q.get("oi_day_low") or current_oi
            entry = {
                "tradingsymbol": inst["tradingsymbol"],
                "instrument_token": inst["instrument_token"],
                "ltp": q.get("last_price", 0),
                "last_price": q.get("last_price", 0),  # alias for compatibility
                "open": ohlc.get("open", 0),
                "high": ohlc.get("high", 0),
                "low": ohlc.get("low", 0),
                "close": ohlc.get("close", 0),
                "volume": q.get("volume", 0),
                "oi": current_oi,
                "oi_change": current_oi - prev_oi,
                "bid": q.get("depth", {}).get("buy", [{}])[0].get("price", 0) if q.get("depth") else 0,
                "ask": q.get("depth", {}).get("sell", [{}])[0].get("price", 0) if q.get("depth") else 0,
            }

            if strike not in chain:
                chain[strike] = {"CE": None, "PE": None}
            chain[strike][itype] = entry

        # Format result
        chain_list = []
        for strike in sorted(chain.keys()):
            chain_list.append({
                "strike": strike,
                "CE": chain[strike].get("CE"),
                "PE": chain[strike].get("PE"),
            })

        return {
            "index": index.upper(),
            "spot": spot,
            "expiry": expiry,
            "chain": chain_list,
        }

    def _empty_option_chain(self, index: str, spot: float, expiry: str) -> Dict:
        """Return empty option chain structure when no data is available."""
        strikes = get_strike_range(spot, index)
        chain_list = [
            {"strike": s, "CE": None, "PE": None}
            for s in strikes
        ]
        return {
            "index": index.upper(),
            "spot": spot,
            "expiry": expiry,
            "chain": chain_list,
        }

    # -------------------------------------------------------------------------
    # Historical Data
    # -------------------------------------------------------------------------

    def get_historical_data(
        self,
        instrument_token: int,
        interval: str = "5minute",
        days: int = 5,
        from_date: datetime = None,
        to_date: datetime = None,
    ) -> List[Dict]:
        """
        Fetch OHLCV historical candles.
        Returns list of dicts: {date, open, high, low, close, volume}.
        """
        if to_date is None:
            to_date = datetime.now()
        if from_date is None:
            from_date = to_date - timedelta(days=days)

        try:
            records = self.kite.historical_data(
                instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                continuous=False,
                oi=True,
            )
            return [
                {
                    "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"]),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume": r.get("volume", 0),
                    "oi": r.get("oi", 0),
                }
                for r in records
            ]
        except Exception as e:
            logger.error(
                "Error fetching historical data for token %s: %s",
                instrument_token, e
            )
            raise

    def get_index_historical(
        self,
        index: str,
        interval: str = "5minute",
        days: int = 5,
    ) -> List[Dict]:
        """Fetch historical OHLCV for NIFTY or SENSEX index."""
        token_map = {
            "NIFTY": NIFTY_TOKEN,
            "SENSEX": SENSEX_TOKEN,
        }
        token = token_map.get(index.upper())
        if not token:
            raise ValueError(f"Unknown index: {index}")
        return self.get_historical_data(token, interval=interval, days=days)

    # -------------------------------------------------------------------------
    # Portfolio
    # -------------------------------------------------------------------------

    def get_positions(self) -> Dict[str, List]:
        """
        Fetch current open positions.
        Returns dict with 'net' and 'day' positions.
        """
        try:
            return self.kite.positions()
        except TokenException:
            raise
        except Exception as e:
            logger.error("Error fetching positions: %s", e)
            raise

    def get_orders(self) -> List[Dict]:
        """Fetch all orders for the day."""
        try:
            return self.kite.orders()
        except Exception as e:
            logger.error("Error fetching orders: %s", e)
            raise

    def get_trades(self) -> List[Dict]:
        """Fetch all executed trades for the day."""
        try:
            return self.kite.trades()
        except Exception as e:
            logger.error("Error fetching trades: %s", e)
            raise

    # -------------------------------------------------------------------------
    # Order Placement
    # -------------------------------------------------------------------------

    def place_order(self, params: Dict[str, Any]) -> str:
        """
        Place an order on Zerodha.
        params must include:
          - tradingsymbol: str
          - exchange: str (NSE/BSE/NFO/BFO)
          - transaction_type: 'BUY' or 'SELL'
          - quantity: int
          - order_type: 'MARKET' | 'LIMIT' | 'SL' | 'SL-M'
          - product: 'MIS' | 'NRML' | 'CNC'
        Optional:
          - price: float (for LIMIT orders)
          - trigger_price: float (for SL orders)
          - validity: 'DAY' | 'IOC'
          - tag: str (max 20 chars)
        Returns order_id on success.
        """
        required = ["tradingsymbol", "exchange", "transaction_type", "quantity", "order_type", "product"]
        for key in required:
            if key not in params:
                raise ValueError(f"Missing required order param: {key}")

        order_params = {
            "tradingsymbol": params["tradingsymbol"],
            "exchange": params["exchange"],
            "transaction_type": params["transaction_type"].upper(),
            "quantity": int(params["quantity"]),
            "order_type": params["order_type"].upper(),
            "product": params["product"].upper(),
            "validity": params.get("validity", "DAY"),
        }

        if params.get("price"):
            order_params["price"] = float(params["price"])
        if params.get("trigger_price"):
            order_params["trigger_price"] = float(params["trigger_price"])
        if params.get("tag"):
            order_params["tag"] = str(params["tag"])[:20]
        if params.get("disclosed_quantity"):
            order_params["disclosed_quantity"] = int(params["disclosed_quantity"])

        try:
            variety = params.get("variety", KiteConnect.VARIETY_REGULAR)
            order_id = self.kite.place_order(variety=variety, **order_params)
            logger.info("Order placed successfully: %s", order_id)
            return str(order_id)
        except InputException as e:
            logger.error("Invalid order input: %s", e)
            raise
        except TokenException:
            logger.error("Token expired while placing order")
            raise
        except Exception as e:
            logger.error("Error placing order: %s", e)
            raise

    def cancel_order(self, order_id: str, variety: str = "regular") -> str:
        """Cancel a pending order."""
        try:
            result = self.kite.cancel_order(variety=variety, order_id=order_id)
            return str(result)
        except Exception as e:
            logger.error("Error cancelling order %s: %s", order_id, e)
            raise

    def modify_order(self, order_id: str, params: Dict, variety: str = "regular") -> str:
        """Modify an existing order."""
        try:
            result = self.kite.modify_order(variety=variety, order_id=order_id, **params)
            return str(result)
        except Exception as e:
            logger.error("Error modifying order %s: %s", order_id, e)
            raise

    # -------------------------------------------------------------------------
    # WebSocket / Ticker
    # -------------------------------------------------------------------------

    def create_ticker(
        self,
        tokens: List[int],
        on_ticks=None,
        on_connect=None,
        on_close=None,
        on_error=None,
    ) -> KiteTicker:
        """
        Create a KiteTicker instance subscribed to given instrument tokens.
        Caller provides callbacks for ticks, connect, close, error events.
        """
        ticker = KiteTicker(self.api_key, self._access_token)

        def _on_connect(ws, response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_FULL, tokens)
            if on_connect:
                on_connect(ws, response)

        ticker.on_ticks = on_ticks or (lambda ws, ticks: None)
        ticker.on_connect = _on_connect
        ticker.on_close = on_close or (lambda ws, code, reason: None)
        ticker.on_error = on_error or (lambda ws, code, reason: None)

        return ticker


# Module-level singleton (lazy init)
_client_instance: Optional[ZerodhaClient] = None


def get_client() -> ZerodhaClient:
    """Return the global ZerodhaClient instance, creating it if needed."""
    global _client_instance
    if _client_instance is None:
        _client_instance = ZerodhaClient()
    return _client_instance


def set_access_token(access_token: str):
    """Update the global client's access token (called after OAuth callback)."""
    global _client_instance
    if _client_instance is None:
        _client_instance = ZerodhaClient(access_token=access_token)
    else:
        _client_instance._access_token = access_token
        _client_instance.kite.set_access_token(access_token)
