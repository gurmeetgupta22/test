import logging
import os
import sys

import pytz
from dotenv import load_dotenv

# On Android, Chaquopy installs the Python package read-only. Runtime state must
# live in the application-private HOME directory instead. Desktop behaviour is
# deliberately unchanged.
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.environ.get("MAX_ALPHA_RUNTIME_DIR", _PACKAGE_ROOT)
if sys.platform == "android":
    PROJECT_ROOT = os.environ.get("HOME", PROJECT_ROOT)
os.makedirs(PROJECT_ROOT, exist_ok=True)


def project_path(path: str) -> str:
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(project_path("max_alpha_v4.log"), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("MaxAlphaV4")
UTC = pytz.utc
IST = pytz.timezone("Asia/Kolkata")

load_dotenv(project_path(".env"))

CONFIG = {
    # API keys
    "dhan_client_id":    os.getenv("DHAN_CLIENT_ID",    "YOUR_DHAN_CLIENT_ID"),
    "dhan_access_token": os.getenv("DHAN_ACCESS_TOKEN", "YOUR_DHAN_ACCESS_TOKEN"),
    "broker_paper":      os.getenv("BROKER_PAPER", "true").lower() == "true",
    "execution_mode":    os.getenv("EXECUTION_MODE", "broker").strip().lower(),  # broker | signals_only
    "paper_signal_file": project_path(os.getenv("PAPER_SIGNAL_FILE", "paper_signals.json")),
    "anthropic_key": os.getenv("ANTHROPIC_API_KEY", "YOUR_KEY"),
    "openrouter_key": os.getenv("OPENROUTER_API_KEY", os.getenv("ANTHROPIC_API_KEY", "YOUR_KEY")),
    "openrouter_model": os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-r1:free"),
    "openrouter_model_fallback": os.getenv("OPENROUTER_MODEL_FALLBACK", "meta-llama/llama-3.3-70b-instruct:free"),
    "groq_key":   os.getenv("GROQ_API_KEY", ""),
    "groq_model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    "openrouter_candidate_limit": int(os.getenv("OPENROUTER_CANDIDATE_LIMIT", "8")),
    "openrouter_max_tokens": int(os.getenv("OPENROUTER_MAX_TOKENS", "3200")),
    "council_mode": os.getenv("COUNCIL_MODE", "dual").strip().lower(),  # dual | local | openrouter
    "broad_market_scan": os.getenv("BROAD_MARKET_SCAN", "true").lower() == "true",
    "broad_scan_limit": int(os.getenv("BROAD_SCAN_LIMIT", "80")),
    "allow_weak_fundamental_buys": os.getenv("ALLOW_WEAK_FUNDAMENTAL_BUYS", "false").lower() == "true",
    "gemini_key":    os.getenv("GEMINI_API_KEY", ""),
    "groq_key":      os.getenv("GROQ_API_KEY", ""),
    "claude_model":  os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
    "local_council_only": os.getenv("LOCAL_COUNCIL_ONLY", "false").lower() == "true",

    # â”€â”€ DYNAMIC CAPITAL  all sizing is % of live account, never hardcoded â”€â”€
    "trading_budget_min":   float(os.getenv("TRADING_BUDGET_MIN", "1000")),
    "trading_budget_max":   float(os.getenv("TRADING_BUDGET_MAX", "50000")),
    "trading_budget_default": float(os.getenv("TRADING_BUDGET", os.getenv("TRADING_BUDGET_DEFAULT", "50000"))),
    "max_position_pct":     float(os.getenv("MAX_POSITION_PCT", "0.20")),
    "position_size_scale":  float(os.getenv("POSITION_SIZE_SCALE", "1.0")),
    "max_tier1_allocation": 0.60,   # Max 60% of portfolio in penny stocks
    "max_tier2_allocation": 0.50,   # Max 50% in small caps
    "max_tier3_allocation": 0.80,   # Max 80% in blue chip/ETF (safe)
    "min_cash_reserve":     float(os.getenv("MIN_CASH_RESERVE", "0.05")),
    "max_open_positions":   int(os.getenv("MAX_OPEN_POSITIONS", "7")),
    "allow_allocation_flex": os.getenv("ALLOW_ALLOCATION_FLEX", "false").lower() == "true",
    "min_trade_value":      float(os.getenv("MIN_TRADE_VALUE", "1000")),
    "desired_daily_trades": int(os.getenv("DESIRED_DAILY_TRADES", "10")),
    "max_new_positions_per_cycle": int(os.getenv("MAX_NEW_POSITIONS_PER_CYCLE", "1")),
    "max_new_deploy_pct_per_cycle": float(os.getenv("MAX_NEW_DEPLOY_PCT_PER_CYCLE", "0.25")),
    "allow_add_to_existing_positions": os.getenv("ALLOW_ADD_TO_EXISTING_POSITIONS", "false").lower() == "true",
    "allow_probe_buys":     os.getenv("ALLOW_PROBE_BUYS", "true").lower() == "true",
    "max_probe_buys_per_cycle": int(os.getenv("MAX_PROBE_BUYS_PER_CYCLE", "1")),
    "probe_min_edge":       float(os.getenv("PROBE_MIN_EDGE", "0.52")),
    "probe_min_alpha":      float(os.getenv("PROBE_MIN_ALPHA", "0.62")),
    "probe_position_pct":   float(os.getenv("PROBE_POSITION_PCT", "0.03")),
    "reconcile_sim_cash":   os.getenv("RECONCILE_SIM_CASH", "true").lower() == "true",

    # â”€â”€ RISK PER TIER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    "tier1_take_profit":    float(os.getenv("TIER1_TAKE_PROFIT", "0.08")),
    "tier1_take_profit_min": float(os.getenv("TIER1_TAKE_PROFIT_MIN", "0.05")),
    "tier1_take_profit_max": float(os.getenv("TIER1_TAKE_PROFIT_MAX", "0.08")),
    "tier1_trail_stop":     float(os.getenv("TIER1_TRAIL_STOP", "0.010")),
    "tier2_take_profit":    float(os.getenv("TIER2_TAKE_PROFIT", "0.08")),
    "tier2_take_profit_min": float(os.getenv("TIER2_TAKE_PROFIT_MIN", "0.05")),
    "tier2_take_profit_max": float(os.getenv("TIER2_TAKE_PROFIT_MAX", "0.08")),
    "tier2_trail_stop":     float(os.getenv("TIER2_TRAIL_STOP", "0.008")),
    "tier3_take_profit":    float(os.getenv("TIER3_TAKE_PROFIT", "0.08")),
    "tier3_take_profit_min": float(os.getenv("TIER3_TAKE_PROFIT_MIN", "0.05")),
    "tier3_take_profit_max": float(os.getenv("TIER3_TAKE_PROFIT_MAX", "0.08")),
    "tier3_trail_stop":     float(os.getenv("TIER3_TRAIL_STOP", "0.006")),
    "take_profit_sell_fraction": float(os.getenv("TAKE_PROFIT_SELL_FRACTION", "1.0")),
    "profit_floor_pct":     float(os.getenv("PROFIT_FLOOR_PCT", "0.01")),
    "variable_profit_min_pct": float(os.getenv("VARIABLE_PROFIT_MIN_PCT", "0.05")),
    "variable_profit_max_pct": float(os.getenv("VARIABLE_PROFIT_MAX_PCT", "0.08")),
    "profit_book_first_pct": float(os.getenv("PROFIT_BOOK_FIRST_PCT", "0.04")),
    "profit_book_runner_pct": float(os.getenv("PROFIT_BOOK_RUNNER_PCT", "0.08")),
    "profit_book_first_sell_fraction": float(os.getenv("PROFIT_BOOK_FIRST_SELL_FRACTION", "0.50")),
    "profit_book_runner_sell_fraction": float(os.getenv("PROFIT_BOOK_RUNNER_SELL_FRACTION", "0.50")),
    "runner_max_peak_pullback_pct": float(os.getenv("RUNNER_MAX_PEAK_PULLBACK_PCT", "0.03")),
    "runner_max_rsi":       float(os.getenv("RUNNER_MAX_RSI", "76")),
    "runner_min_weekly_perf_pct": float(os.getenv("RUNNER_MIN_WEEKLY_PERF_PCT", "0.0")),
    "profit_target_min_rupees": float(os.getenv("PROFIT_TARGET_MIN_RUPEES", "2000")),
    "profit_target_max_rupees": float(os.getenv("PROFIT_TARGET_MAX_RUPEES", "5000")),
    "profit_target_cap_pct": float(os.getenv("PROFIT_TARGET_CAP_PCT", "0.50")),
    "chart_breakdown_confirm_cycles": int(os.getenv("CHART_BREAKDOWN_CONFIRM_CYCLES", "2")),
    "chart_breakdown_peak_drop_pct": float(os.getenv("CHART_BREAKDOWN_PEAK_DROP_PCT", "0.08")),
    "chart_breakdown_weekly_drop_pct": float(os.getenv("CHART_BREAKDOWN_WEEKLY_DROP_PCT", "-5.0")),
    "chart_breakdown_monthly_drop_pct": float(os.getenv("CHART_BREAKDOWN_MONTHLY_DROP_PCT", "-10.0")),
    "catastrophic_loss_mult": float(os.getenv("CATASTROPHIC_LOSS_MULT", "1.5")),
    "loss_hold_grace_pct":  float(os.getenv("LOSS_HOLD_GRACE_PCT", "0.012")),
    "trend_hold_min_days":  int(os.getenv("TREND_HOLD_MIN_DAYS", "3")),
    "last_new_entry_hour":  int(os.getenv("LAST_NEW_ENTRY_HOUR", "14")),
    "last_new_entry_min":   int(os.getenv("LAST_NEW_ENTRY_MIN", "45")),
    "require_uptrend_for_buys": os.getenv("REQUIRE_UPTREND_FOR_BUYS", "true").lower() == "true",
    "buy_min_rsi":          float(os.getenv("BUY_MIN_RSI", "50")),
    "buy_max_rsi":          float(os.getenv("BUY_MAX_RSI", "74")),
    "min_recovery_score":   float(os.getenv("MIN_RECOVERY_SCORE", "0.45")),
    "max_downtrend_risk":   float(os.getenv("MAX_DOWNTREND_RISK", "0.62")),

    # ── QUALITY FILTERS ──────────────────────────────────────────────────
    "tier1_min_rel_volume": 1.5,
    "tier1_min_volume":     500_000,
    "tier2_min_volume":     1_000_000,
    "tier3_min_volume":     2_000_000,
    "min_consensus":        float(os.getenv("MIN_CONSENSUS", "0.70")),

    # ── REGIME THRESHOLDS ────────────────────────────────────────────────

    "bull_threshold":       0.65,   # Score > 0.65 = BULL
    "bear_threshold":       0.35,   # Score < 0.35 = BEAR
    "crash_threshold":      0.20,   # Score < 0.20 = CRASH (go defensive)

    # â”€â”€ SCHEDULING â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    "power_open_interval":  300,    # 5 min during first 90 min
    "mid_session_interval": 600,    # 10 min mid-session (more active scanning)
    "power_close_interval": 300,    # 5 min last 30 min

    # Opening Range Breakout strategy from the supplied notes.
    "orb_enabled":          os.getenv("ORB_ENABLED", "false").lower() == "true",
    "orb_interval":         int(os.getenv("ORB_INTERVAL", "15")),
    "orb_end_hour":         int(os.getenv("ORB_END_HOUR", "9")),
    "orb_end_min":          int(os.getenv("ORB_END_MIN", "45")),
    "orb_hard_exit_hour":   int(os.getenv("ORB_HARD_EXIT_HOUR", "14")),
    "orb_hard_exit_min":    int(os.getenv("ORB_HARD_EXIT_MIN", "0")),
    "orb_min_range":        float(os.getenv("ORB_MIN_RANGE", "40.0")),
    "orb_min_range_pct":    float(os.getenv("ORB_MIN_RANGE_PCT", "0.003")),
    "orb_t1_mult":          float(os.getenv("ORB_T1_MULT", "1.0")),
    "orb_t2_mult":          float(os.getenv("ORB_T2_MULT", "1.5")),
    "orb_stop_buffer":      float(os.getenv("ORB_STOP_BUFFER", "0.05")),
    "orb_volume_lookback":  int(os.getenv("ORB_VOLUME_LOOKBACK", "3")),
    "orb_vwap_filter":      os.getenv("ORB_VWAP_FILTER", "true").lower() == "true",
    "orb_volume_filter":    os.getenv("ORB_VOLUME_FILTER", "true").lower() == "true",
    "orb_alpha_boost":      float(os.getenv("ORB_ALPHA_BOOST", "0.12")),
    "orb_atr_filter":       os.getenv("ORB_ATR_FILTER", "true").lower() == "true",
    "orb_atr_lookback":     int(os.getenv("ORB_ATR_LOOKBACK", "14")),
    "orb_atr_expansion":    float(os.getenv("ORB_ATR_EXPANSION", "1.05")),
    "orb_ema_filter":       os.getenv("ORB_EMA_FILTER", "true").lower() == "true",
    "orb_ema_fast":         int(os.getenv("ORB_EMA_FAST", "9")),
    "orb_ema_slow":         int(os.getenv("ORB_EMA_SLOW", "21")),
    "orb_min_body_pct":     float(os.getenv("ORB_MIN_BODY_PCT", "0.45")),
    "orb_breakout_buffer":  float(os.getenv("ORB_BREAKOUT_BUFFER", "0.05")),
    "orb_entry_start_hour": int(os.getenv("ORB_ENTRY_START_HOUR", "9")),
    "orb_entry_start_min":  int(os.getenv("ORB_ENTRY_START_MIN", "30")),
    "orb_entry_end_hour":   int(os.getenv("ORB_ENTRY_END_HOUR", "10")),
    "orb_entry_end_min":    int(os.getenv("ORB_ENTRY_END_MIN", "45")),
    "orb_gap_filter":       os.getenv("ORB_GAP_FILTER", "false").lower() == "true",
    "orb_min_gap_pct":      float(os.getenv("ORB_MIN_GAP_PCT", "0.004")),
    "orb_skip_high_vix":    os.getenv("ORB_SKIP_HIGH_VIX", "true").lower() == "true",
    "orb_trade_lots":       int(os.getenv("ORB_TRADE_LOTS", "4")),
    "orb_lot_size":         int(os.getenv("ORB_LOT_SIZE", "1")),
    "max_orb_trades_per_day": int(os.getenv("MAX_ORB_TRADES_PER_DAY", "3")),
    "place_broker_target_orders": os.getenv("PLACE_BROKER_TARGET_ORDERS", "false").lower() == "true",

    # Professional risk manager.
    "risk_state_file":      project_path(os.getenv("RISK_STATE_FILE", "risk_state.json")),
    "learning_state_file":  project_path(os.getenv("LEARNING_STATE_FILE", "learning_state.json")),
    "learning_min_trades":  int(os.getenv("LEARNING_MIN_TRADES", "3")),
    "learning_penalty":     float(os.getenv("LEARNING_PENALTY", "0.12")),
    "learning_bonus":       float(os.getenv("LEARNING_BONUS", "0.03")),
    "min_reliable_win_rate": float(os.getenv("MIN_RELIABLE_WIN_RATE", "0.45")),
    "loss_reentry_cooldown_days": int(os.getenv("LOSS_REENTRY_COOLDOWN_DAYS", "1")),
    "teacher_learning_bonus": float(os.getenv("TEACHER_LEARNING_BONUS", "0.025")),
    "teacher_learning_max": float(os.getenv("TEACHER_LEARNING_MAX", "0.06")),
    "learning_reentry_alpha": float(os.getenv("LEARNING_REENTRY_ALPHA", "0.68")),
    "learning_reentry_rel_volume": float(os.getenv("LEARNING_REENTRY_REL_VOLUME", "1.2")),
    "sqlite_trade_log":     project_path(os.getenv("SQLITE_TRADE_LOG", "trade_log.sqlite3")),
    "max_daily_profit_lock": float(os.getenv("MAX_DAILY_PROFIT_LOCK", "600")),
    "max_daily_loss":       float(os.getenv("MAX_DAILY_LOSS", "8000")),
    "weekly_drawdown_limit": float(os.getenv("WEEKLY_DRAWDOWN_LIMIT", "0.10")),
    "max_consecutive_losses": int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3")),
    "loss_cooldown_minutes": int(os.getenv("LOSS_COOLDOWN_MINUTES", "20")),

    # Optional trade alerts. Telegram is used when both values are set; ntfy is a zero-secret fallback.
    "telegram_bot_token":   os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id":     os.getenv("TELEGRAM_CHAT_ID", ""),
    "ntfy_topic":           os.getenv("NTFY_TOPIC", ""),
}

PORTFOLIO_STATE_FILE = project_path(os.getenv("PORTFOLIO_STATE_FILE", "portfolio_state_v4.json"))

PERFORMANCE_FILE = project_path(os.getenv("PERFORMANCE_FILE", "performance_v4.json"))

TIER3_UNIVERSE = {
    "etf_broad":   ["NIFTYBEES.NS", "BANKBEES.NS", "JUNIORBEES.NS"],
    "etf_sector":  ["ITBEES.NS", "PHARMABEES.NS", "AUTOBEES.NS"],
    "etf_hedge":   ["GOLDBEES.NS", "SILVERBEES.NS", "LIQUIDBEES.NS"],
    "bluechip":    ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ITC.NS", 
                    "SBIN.NS", "BHARTIARTL.NS", "LT.NS"],
}

TIER2_UNIVERSE = [
    "BSE", "CDSL", "MCX", "LTF", "PFC", "RECLTD", "IRFC", "HUDCO",
    "INDIGO", "POLYCAB", "DIXON", "TATACOMM", "COFORGE", "PERSISTENT",
    "KPITTECH", "MOTHERSON", "TATAPOWER", "CUMMINSIND", "ABB", "SIEMENS",
    "APOLLOHOSP", "MAXHEALTH", "FORTIS", "PIDILITIND", "HAVELLS", "TORNTPHARM",
    "TRENT", "JUBLFOOD", "INDHOTEL", "DLF", "GODREJPROP", "SRF",
]

TIER1_SCAN_UNIVERSE = [
    "SUZLON", "YESBANK", "RPOWER", "IDEA", "RVNL", "IRCON", "NBCC",
    "JPPOWER", "TRIDENT", "UCOBANK", "CENTRALBK", "IDBI", "IFCI",
    "IEX", "HFCL", "PRAJIND", "REDINGTON", "JWL", "BLS", "MAPMYINDIA",
    "NETWORK18", "ZEEL", "RBLBANK", "CAMPUS", "EASEMYTRIP", "ANGELONE",
    "UNIONBANK", "PNB", "BANDHANBNK", "IDFCFIRSTB", "FEDERALBNK",
    "GMRINFRA", "JINDALSTEL", "SAIL", "NATIONALUM", "HINDCOPPER",
    "NMDC", "VEDL", "COALINDIA", "TATACHEM", "BHEL", "BEL",
    "IRB", "PVRINOX", "NYKAA", "PAYTM", "DELHIVERY", "ETERNAL",
    "POLICYBZR", "MGL", "IIFL", "GLENMARK", "AUBANK", "CESC"
]

REGIME_BREADTH_UNIVERSE = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "TCS", "INFY", "LT", "SBIN",
    "BHARTIARTL", "AXISBANK", "KOTAKBANK", "BAJFINANCE", "ITC",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "ULTRACEMCO", "TITAN",
    "NTPC", "POWERGRID", "TATAMOTORS"
]

REGIME_ALLOCATIONS = {
    "BULL":    {"tier1": 0.60, "tier2": 0.30, "tier3": 0.10},
    "NEUTRAL": {"tier1": 0.35, "tier2": 0.40, "tier3": 0.25},
    "BEAR":    {"tier1": 0.10, "tier2": 0.30, "tier3": 0.60},
    "CRASH":   {"tier1": 0.00, "tier2": 0.00, "tier3": 1.00},
}
