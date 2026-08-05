import datetime
import json
import re
import time
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

import requests

try:
    from .models import MarketRegime, Portfolio, ScoredStock, StockSignal
    from .risk import LearningEngine
    from .settings import CONFIG, IST, log
    from .stock_intelligence import enrich_exit_prompt
except ImportError:
    if __package__:
        raise
    from models import MarketRegime, Portfolio, ScoredStock, StockSignal
    from risk import LearningEngine
    from settings import CONFIG, IST, log
    from stock_intelligence import enrich_exit_prompt

COUNCIL_SYSTEM = """
You are MAX ALPHA  an elite AI trading council of five legendary investor minds.
You analyse stocks across THREE tiers simultaneously, adapting strategy to market regime.

THE FIVE MINDS:

1. WARREN BUFFETT (Quality & Value)
   - Tier 3 specialist. Loves wide moats, consistent earnings, dividend growers (e.g., ITC, HDFC).
   - Tier 1/2: Only buys on extreme momentum with clear fundamental story.
   - Veto: Overvalued junk, no competitive advantage, poor corporate governance.

2. JIM SIMONS (Quantitative)
   - All tiers. Pure statistical edge  RSI, MACD, volume patterns.
   - Tier 1: RSI 40-65, volume 3x+. Tier 2: RSI 45-68, momentum. Tier 3: RSI 40-62, trend.
   - Veto: RSI > 78 for buys. Volume declining during price rise.

3. PAUL TUDOR JONES (Macro Momentum)
   - All tiers. Never fight the tape. Ride regime trends.
   - BULL regime: overweight tier 1/2. BEAR regime: overweight tier 3/ETF.
   - Veto: Buying into downtrends. Ignoring macro regime.

4. PETER LYNCH (Growth at Reasonable Price)
   - Tier 2 specialist. PEG ratio, earnings acceleration, sector leadership.
   - Tier 1: Only on clear catalysts. Tier 3: Dividend growth stories.
   - Veto: Paying too much for growth, no earnings path.

5. GEORGE SOROS (Reflexivity & Macro)
   - All tiers. Self-reinforcing narratives. Asymmetric opportunities.
   - Tier 1: Short squeezes, SEBI/RBI policy shifts, order book breakouts. Tier 3: Macro Indian regime shifts.
   - Veto: Narrative already priced in, no new catalyst.

REGIME-AWARE RULES:
- BULL regime: Favour tier 1 and tier 2. Be aggressive.
- NEUTRAL regime: Balance across all tiers. Be selective.
- BEAR regime: Favour tier 3 ETFs. Protect capital.
- CRASH regime: ONLY ETFs and cash. Absolute capital preservation.

HARD RULES (always apply regardless of regime):
- RSI > 80: EVERYONE must vote SKIP for buys
- RSI < 20: EVERYONE must vote SKIP for sells  
- No volume confirmation: SKIP
- If CRASH regime: Tier 1 and 2 are automatically vetoed
- For Tier 2 and Tier 3 buys, fundamental_quality must be STRONG or MODERATE.
- LOW or VERY_LOW data_confidence must reduce conviction unless the technical setup is exceptional.
- Treat fundamental output as a quality VIEW only; do not invent financial metrics.
- If strategy_setup is "orb_long_breakout", treat it as an intraday Opening Range Breakout from 9:15-9:45 IST:
  buy only when the long setup is strong, use the supplied ORB stop, T1, T2, and 14:00 hard exit.
- If strategy_setup is "orb_short_breakdown", do not open a fresh long; use it only as bearish context or SELL context.

POSITION SIZING (based on consensus):
- consensus > 0.85: recommend "high" sizing (up to 10% of portfolio)
- consensus 0.75-0.85: recommend "medium" sizing (6-8% of portfolio)
- consensus 0.68-0.75: recommend "low" sizing (4-6% of portfolio)

Return ONLY perfectly formatted JSON like this:
{
  "signals": [
    {
      "ticker": "RELIANCE",
      "tier": 3,
      "action": "BUY",
      "strategy": "blue_chip_momentum_bull_regime",
      "consensus_score": 0.84,
      "sizing": "high",
      "veto": false,
      "votes": [
        {"mind": "buffett", "action": "BUY", "conviction": 0.90, "reasoning": "Dominant GPU moat, pricing power, AI capex tailwind"},
        {"mind": "simons",  "action": "BUY", "conviction": 0.85, "reasoning": "RSI 58, MACD bullish, volume 1.4x avg, uptrend intact"},
        {"mind": "tudor",   "action": "BUY", "conviction": 0.88, "reasoning": "BULL regime, tech sector leading, ride the tape"},
        {"mind": "lynch",   "action": "BUY", "conviction": 0.80, "reasoning": "AI growth story, revenue accelerating, reasonable PEG"},
        {"mind": "soros",   "action": "BUY", "conviction": 0.78, "reasoning": "AI reflexivity loop early innings, narrative driving fundamentals"}
      ]
    }
  ]
}
"""

EXIT_SYSTEM = """
You are MAX ALPHA EXIT COUNCIL — five legendary investors deciding whether to HOLD or SELL open positions.
Your job: Let winners RUN. Cut losers FAST. Never sell a winner just because profit looks big.

THE FIVE MINDS:

1. WARREN BUFFETT: "Our favourite holding period is forever." Hold if fundamentals intact and uptrend continues. Sell only if thesis is broken.
2. JIM SIMONS: Pure data. If momentum is strong (RSI 50-75, uptrend, volume rising) → HOLD. If momentum reversing → SELL.
3. PAUL TUDOR JONES: "Never hold a loser." Cut losses at -2 to -3%. But let winners compound. Trail, don't cap.
4. PETER LYNCH: "Selling your winners and holding your losers is like cutting the flowers and watering the weeds." HOLD winners.
5. GEORGE SOROS: Exit when the narrative breaks. If trend and story intact → HOLD. If chart breaks down → SELL.

RULES:
- If position is PROFITABLE and trend is UP → almost always HOLD. Let it run to 20%, 40%, 50%+.
- If position is PROFITABLE and trend is REVERSING (RSI dropping fast, SMA break) → SELL to lock profit.
- If position is at a LOSS and still falling → SELL immediately. Cut losers fast.
- If position is at a LOSS but stabilizing or bouncing → HOLD for recovery.
- NEVER sell just because profit is "high enough". Only sell when trend reverses.

Return ONLY valid JSON:
{
  "exits": [
    {
      "ticker": "XXXX",
      "action": "HOLD" or "SELL",
      "confidence": 0.0-1.0,
      "reasoning": "short reason under 15 words",
      "minds_agree": 3
    }
  ]
}
"""

class CouncilOfFive:

    def __init__(self):
        self.learning = LearningEngine()

    def vote_exits(self, positions: dict, regime: MarketRegime) -> dict:
        """
        Ask the AI council whether to HOLD or SELL each open position.
        Uses 3-year backtest intelligence + current trend for smart decisions.
        Falls back to rule-based trailing stop if LLM unavailable.
        """
        if not positions:
            return {}

        # Build rich prompt with 3-year backtest data
        try:
            prompt = enrich_exit_prompt(positions, regime.state)
        except Exception as e:
            log.debug(f"Backtest enrichment failed: {e}, using basic prompt")
            lines = []
            for ticker, pos in positions.items():
                pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost) * 100
                lines.append(f"{ticker}: pnl={pnl_pct:+.2f}%, tier={pos.tier}")
            prompt = f"Regime: {regime.state}\nPositions:\n" + "\n".join(lines)

        result = self._ai_exit_vote(prompt, list(positions.keys()))
        if result:
            log.info(f"  AI Exit Council (backtest-informed) reviewed {len(result)} positions")
            return result

        log.info("  AI Exit unavailable — rule-based trailing stop fallback")
        return self._rule_based_exits(positions)

    def _ai_exit_vote(self, prompt: str, tickers: list) -> Optional[dict]:
        """Try Groq (FREE, fast) first, then OpenRouter free models as fallback"""
        raw = self._mobile_provider_chat(EXIT_SYSTEM, prompt, 800)
        if raw:
            result = self._parse_exit_response(raw, tickers)
            if result:
                return result
        result = self._groq_call(prompt, tickers)
        if result is not None:
            return result

        key = str(CONFIG.get("openrouter_key", "") or "").strip()
        if key and not key.startswith("YOUR"):
            for model in [
                str(CONFIG.get("openrouter_model", "deepseek/deepseek-r1:free")),
                str(CONFIG.get("openrouter_model_fallback", "meta-llama/llama-3.3-70b-instruct:free")),
            ]:
                result = self._openrouter_call(key, model, prompt, tickers)
                if result is not None:
                    return result

        log.warning("  All AI models failed — using rule-based fallback")
        return None

    def _groq_call(self, prompt: str, tickers: list) -> Optional[dict]:
        """Groq FREE API — llama-3.3-70b, 14,400 req/day, <1s response"""
        key = str(CONFIG.get("groq_key", "") or "").strip()
        if not key or key.startswith("YOUR"):
            return None
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": str(CONFIG.get("groq_model", "llama-3.3-70b-versatile")),
                "max_tokens": 800,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": EXIT_SYSTEM},
                    {"role": "user",   "content": prompt + "\n\nRespond ONLY with valid JSON."}
                ]
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                log.debug(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            result = self._parse_exit_response(raw, tickers)
            if result:
                log.info(f"  🤖 Groq AI (FREE) made exit decisions for {len(result)} positions")
            return result
        except Exception as e:
            log.debug(f"Groq call error: {e}")
            return None

    def _openrouter_call(self, key: str, model: str, prompt: str, tickers: list) -> Optional[dict]:
        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "MaxAlpha-Exits",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "max_tokens": 800,
                "messages": [
                    {"role": "system", "content": EXIT_SYSTEM},
                    {"role": "user",   "content": prompt + "\n\nRespond ONLY with valid JSON."}
                ]
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code in (402, 429, 503):
                return None  # signal caller to try next model
            if resp.status_code != 200:
                log.debug(f"OpenRouter {model} HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return self._parse_exit_response(raw, tickers)
        except Exception as e:
            log.debug(f"OpenRouter call error ({model}): {e}")
            return None

    def _parse_exit_response(self, raw: str, tickers: list) -> Optional[dict]:
        """Parse exit JSON from any LLM response"""
        try:
            parsed = json.loads(raw)
            exits  = parsed.get("exits", [])
            result = {}
            for item in exits:
                t = item.get("ticker", "")
                if t in tickers:
                    action = item.get("action", "HOLD").upper()
                    result[t] = {
                        "action":     action,
                        "confidence": float(item.get("confidence", 0.5)),
                        "reasoning":  item.get("reasoning", ""),
                        "minds_agree": int(item.get("minds_agree", 3))
                    }
                    icon = "🔴 SELL" if action == "SELL" else "✅ HOLD"
                    log.info(f"  AI Exit {icon} {t}: {result[t]['reasoning']}")
            return result if result else None
        except Exception as e:
            log.debug(f"Exit response parse error: {e}")
            return None

    def _rule_based_exits(self, positions: dict) -> dict:
        """Trailing stop fallback when LLM unavailable — lets winners run"""
        trails    = {1: 8.0,  2: 7.0,  3: 6.0}
        activates = {1: 5.0,  2: 4.0,  3: 3.0}
        result    = {}
        for ticker, pos in positions.items():
            tier    = pos.tier if hasattr(pos, 'tier') else 3
            pnl_pct = ((pos.current_price - pos.avg_cost) / pos.avg_cost) * 100
            peak    = pos.peak_price if hasattr(pos, 'peak_price') else pos.current_price
            trail   = trails.get(tier, 7.0)
            act     = activates.get(tier, 4.0)
            if pnl_pct >= act:
                trail_price = peak * (1 - trail / 100)
                if pos.current_price <= trail_price:
                    locked = ((trail_price - pos.avg_cost) / pos.avg_cost) * 100
                    result[ticker] = {"action": "SELL", "confidence": 0.90,
                                      "reasoning": f"Trailing stop fired, locked +{locked:.1f}%", "minds_agree": 4}
                else:
                    result[ticker] = {"action": "HOLD", "confidence": 0.85,
                                      "reasoning": f"Trailing HOLD P&L {pnl_pct:+.1f}% — riding!", "minds_agree": 4}
            else:
                result[ticker] = {"action": "HOLD", "confidence": 0.70,
                                  "reasoning": f"HOLD P&L {pnl_pct:+.1f}; wait for chart confirmation", "minds_agree": 3}
        return result

    def vote(self, candidates: List[ScoredStock], portfolio: Portfolio,
             regime: MarketRegime) -> List[StockSignal]:
        if not candidates:
            return []

        log.info(f"  Council voting on {len(candidates)} candidates (regime: {regime.state})...")
        local_signals = self._local_vote(candidates, portfolio, regime)

        mode = str(CONFIG.get("council_mode", "dual")).lower()
        if CONFIG.get("local_council_only", False) or mode == "local":
            return local_signals

        openrouter_candidates = self._openrouter_candidate_slice(candidates, portfolio, regime)
        prompt = self._build_prompt(openrouter_candidates, portfolio, regime)
        openrouter_signals = self._openrouter_vote(prompt, openrouter_candidates, portfolio, regime)
        if openrouter_signals is None:
            log.info("  OpenRouter Council unavailable - following Local Council fallback")
            return local_signals
        if mode == "openrouter":
            return openrouter_signals or local_signals

        return self._compare_councils(local_signals, openrouter_signals, candidates, portfolio, regime)

    def _openrouter_vote(self, prompt: str, candidates: List[ScoredStock], portfolio: Portfolio,
                         regime: MarketRegime) -> Optional[List[StockSignal]]:
        raw = self._mobile_provider_chat(
            COUNCIL_SYSTEM + self._compact_output_rules(), prompt,
            max(self._max_tokens_for_candidates(len(candidates)), int(CONFIG.get("openrouter_max_tokens", 3200))),
        )
        if raw:
            try:
                parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())
                items = parsed.get("signals", parsed) if isinstance(parsed, dict) else parsed
                return self._build_signals(items, candidates, portfolio, regime)
            except (ValueError, TypeError) as e:
                log.error(f"Mobile AI Council JSON error: {e}")

        # Try Groq first (FREE, fast, reliable)
        result = self._groq_entry_vote(prompt, candidates, portfolio, regime)
        if result is not None:
            return result

        # Fallback: OpenRouter
        key = str(CONFIG.get("openrouter_key", "") or "").strip()
        if not key or key.startswith("YOUR"):
            log.info("  No AI API available - using Local Council fallback")
            return None

        try:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "MaxAlpha",
                "Content-Type": "application/json"
            }
            max_tokens = max(
                self._max_tokens_for_candidates(len(candidates)),
                int(CONFIG.get("openrouter_max_tokens", 3200)),
            )
            payload = {
                "model": str(CONFIG.get("openrouter_model", "deepseek/deepseek-r1:free")),
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": COUNCIL_SYSTEM + self._compact_output_rules()},
                    {"role": "user",   "content": prompt}
                ]
            }

            for attempt in range(4):
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                if resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    log.warning(f"OpenRouter rate limited (Attempt {attempt+1}/4) — retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                if resp.status_code == 402:
                    reduced = self._reduced_max_tokens_from_error(resp.text, payload["max_tokens"])
                    if reduced < 300:
                        log.warning("OpenRouter credits too low for Council response")
                        return None
                    if reduced < payload["max_tokens"]:
                        payload["max_tokens"] = reduced
                        log.warning(f"OpenRouter credit limit hit - retrying with max_tokens={reduced}")
                        continue
                break

            if resp.status_code != 200:
                log.error(f"OpenRouter Error: {resp.text[:300]}")
                return None

            data = resp.json()
            raw  = data["choices"][0]["message"]["content"].strip()
            raw  = raw.replace("```json", "").replace("```", "").strip()

            parsed = json.loads(raw)
            items  = parsed.get("signals", parsed) if isinstance(parsed, dict) else parsed
            return self._build_signals(items, candidates, portfolio, regime)

        except json.JSONDecodeError as e:
            log.error(f"Council JSON error: {e}")
            return None
        except Exception as e:
            log.error(f"Council API error: {e}")
            return None

    def _mobile_provider_chat(self, system: str, prompt: str, max_tokens: int) -> Optional[str]:
        """Use the model selected from a mobile user's own API-key prefix.

        This code is dormant for desktop use: mobile_gateway alone sets these
        config values. Failure is intentionally non-fatal so the original
        OpenRouter/Groq/local fallback order remains intact.
        """
        provider = str(CONFIG.get("mobile_ai_provider", "")).lower()
        key = str(CONFIG.get("mobile_ai_key", "") or "").strip()
        model = str(CONFIG.get("mobile_ai_model", "") or "").strip()
        if not provider or not key or not model:
            return None
        try:
            if provider == "anthropic":
                response = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": prompt}]},
                    timeout=120,
                )
                if response.status_code == 200:
                    return response.json()["content"][0]["text"].strip()
            elif provider == "openai":
                response = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "max_tokens": max_tokens, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
                    timeout=120,
                )
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"].strip()
            return None
        except Exception as e:
            log.debug(f"Mobile {provider} API error: {e}")
            return None

    def _groq_entry_vote(self, prompt: str, candidates: List[ScoredStock], portfolio: Portfolio,
                         regime: MarketRegime) -> Optional[List[StockSignal]]:
        """Groq FREE — llama-3.3-70b for entry signal voting"""
        key = str(CONFIG.get("groq_key", "") or "").strip()
        if not key or key.startswith("YOUR"):
            return None
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "model": str(CONFIG.get("groq_model", "llama-3.3-70b-versatile")),
                "max_tokens": 1200,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": COUNCIL_SYSTEM + self._compact_output_rules()},
                    {"role": "user",   "content": prompt + "\n\nRespond ONLY with valid JSON."}
                ]
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                log.debug(f"Groq entry vote HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)
            items  = parsed.get("signals", parsed) if isinstance(parsed, dict) else parsed
            signals = self._build_signals(items, candidates, portfolio, regime)
            if signals:
                log.info(f"  🤖 Groq AI (FREE) generated {len(signals)} entry signals")
            return signals
        except Exception as e:
            log.debug(f"Groq entry vote error: {e}")
            return None

    @staticmethod
    def _max_tokens_for_candidates(candidate_count: int) -> int:
        """Keep Council responses compact enough for low-credit OpenRouter balances."""
        return max(1200, min(3600, 900 + candidate_count * 320))

    @staticmethod
    def _compact_output_rules() -> str:
        return """

OUTPUT COMPACTNESS:
- Return at most 6 signals.
- Reasoning strings must be 12 words or fewer.
- Each vote reasoning must be 10 words or fewer.
- Do not include markdown, commentary, or invented fields.
- Prefer fewer valid signals over long JSON.
"""

    def _openrouter_candidate_slice(
        self,
        candidates: List[ScoredStock],
        portfolio: Portfolio,
        regime: MarketRegime,
    ) -> List[ScoredStock]:
        limit = max(1, int(CONFIG.get("openrouter_candidate_limit", 8)))
        ranked = sorted(
            candidates,
            key=lambda x: self._local_edge_score(x, regime, portfolio),
            reverse=True,
        )
        return ranked[:limit]

    @staticmethod
    def _reduced_max_tokens_from_error(text: str, current: int) -> int:
        match = re.search(r"can only afford\s+(\d+)", text or "", flags=re.IGNORECASE)
        if match:
            return max(0, min(current - 100, int(match.group(1)) - 50))
        return max(0, current - 500)

    def _compare_councils(
        self,
        local_signals: List[StockSignal],
        openrouter_signals: List[StockSignal],
        candidates: List[ScoredStock],
        portfolio: Portfolio,
        regime: MarketRegime,
    ) -> List[StockSignal]:
        if not openrouter_signals:
            log.info("  OpenRouter Council available, but no signal survived validation - following Local Council fallback")
            return local_signals

        cmap = {c.ticker: c for c in candidates}
        best_by_ticker: Dict[str, Tuple[str, StockSignal, float]] = {}
        for source, signals in (("local", local_signals), ("openrouter", openrouter_signals)):
            for signal in signals:
                c = cmap.get(signal.ticker)
                if not c:
                    continue
                quality = self._decision_quality(signal, c, portfolio, regime)
                current = best_by_ticker.get(signal.ticker)
                if current is None or quality > current[2]:
                    best_by_ticker[signal.ticker] = (source, signal, quality)

        available_slots = max(0, CONFIG["max_open_positions"] - portfolio.open_count)
        local_tickers = {s.ticker for s in local_signals}
        openrouter_tickers = {s.ticker for s in openrouter_signals}
        majority_tickers = local_tickers & openrouter_tickers
        majority = [
            item for item in best_by_ticker.values()
            if item[1].ticker in majority_tickers
        ]
        single_source = [
            item for item in best_by_ticker.values()
            if item[1].ticker not in majority_tickers
        ]
        selected = sorted(majority, key=lambda item: item[2], reverse=True)
        selected += sorted(single_source, key=lambda item: item[2], reverse=True)
        final = [signal for _, signal, _ in selected[:available_slots]]
        openrouter_wins = 0
        local_wins = 0
        for source, signal, quality in selected[:available_slots]:
            if source == "openrouter":
                openrouter_wins += 1
                self.learning.record_teacher_decision(signal.ticker, signal.strategy, source, quality)
                local_strategy = cmap[signal.ticker].strategy_setup or f"local_{regime.state.lower()}_alpha"
                self.learning.record_teacher_decision(signal.ticker, local_strategy, source, quality)
            else:
                local_wins += 1

        log.info(
            f"  Council comparison selected {len(final)} signals "
            f"(majority {len(majority_tickers)}, OpenRouter {openrouter_wins}, Local {local_wins})"
        )
        final.sort(key=lambda s: s.consensus * s.alpha_score, reverse=True)
        return final or local_signals

    def _decision_quality(
        self,
        signal: StockSignal,
        candidate: ScoredStock,
        portfolio: Portfolio,
        regime: MarketRegime,
    ) -> float:
        strategy = signal.strategy or candidate.strategy_setup or f"local_{regime.state.lower()}_alpha"
        local_edge = self._local_edge_score(candidate, regime, portfolio)
        learn_adj = self.learning.adjustment(candidate.ticker, strategy)
        teacher_adj = self.learning.teacher_adjustment(candidate.ticker, strategy)
        trend_bonus = 0.04 if candidate.trend == "uptrend" else -0.08
        recovery_bonus = (candidate.recovery_score * 0.08) - (candidate.downtrend_risk * 0.08)
        quality_bonus = {
            "STRONG": 0.04,
            "MODERATE": 0.02,
            "WEAK": -0.06,
        }.get(candidate.fundamental.overall_quality, 0.0)
        score = (
            signal.consensus * 0.45
            + signal.alpha_score * 0.25
            + local_edge * 0.20
            + trend_bonus
            + recovery_bonus
            + quality_bonus
            + learn_adj
            + teacher_adj
        )
        if signal.action == "SELL":
            score -= 0.05
        return max(0.0, min(1.0, score))

    @staticmethod
    def _adaptive_take_profit_pct(
        tier: int,
        consensus: float,
        alpha_score: float,
        trade_value: float = 0.0,
    ) -> float:
        lo = float(CONFIG.get(f"tier{tier}_take_profit_min", CONFIG[f"tier{tier}_take_profit"]))
        hi = float(CONFIG.get(f"tier{tier}_take_profit_max", CONFIG[f"tier{tier}_take_profit"]))
        strength = max(0.0, min(1.0, ((consensus - CONFIG["min_consensus"]) / 0.35) * 0.6 + alpha_score * 0.4))
        technical_target = lo if hi <= lo else lo + (hi - lo) * strength
        if trade_value <= 0:
            return technical_target
        min_profit = float(CONFIG.get("profit_target_min_rupees", 2000))
        max_profit = float(CONFIG.get("profit_target_max_rupees", 5000))
        rupee_target = min_profit + max(0.0, max_profit - min_profit) * strength
        rupee_target_pct = rupee_target / max(1.0, trade_value)
        cap = float(CONFIG.get("profit_target_cap_pct", 0.50))
        return max(
            float(CONFIG.get("profit_floor_pct", 0.01)),
            min(cap, min(max(technical_target, rupee_target_pct), hi)),
        )

    def _local_vote(self, candidates: List[ScoredStock], portfolio: Portfolio,
                    regime: MarketRegime) -> List[StockSignal]:
        """Deterministic professional council. No external API keys required."""
        log.info("  Local Council active - professional rules, no external API required")
        items = []
        available_slots = max(0, CONFIG["max_open_positions"] - portfolio.open_count)
        if available_slots <= 0:
            return []

        ranked = sorted(
            candidates,
            key=lambda x: self._local_edge_score(x, regime, portfolio),
            reverse=True,
        )
        probe_buys = 0
        for c in ranked:
            if len(items) >= available_slots:
                break
            if c.ticker in portfolio.positions and not CONFIG.get("allow_add_to_existing_positions", False):
                continue
            if regime.state == "CRASH" and c.tier < 3:
                continue
            if c.rsi > 80:
                continue
            if c.strategy_setup != "orb_long_breakout":
                if c.history_pattern == "falling_downtrend" or c.downtrend_risk > float(CONFIG["max_downtrend_risk"]):
                    log.info(
                        f"  {c.ticker}: skipped - falling history "
                        f"({c.history_pattern}, risk {c.downtrend_risk:.2f})"
                    )
                    continue
                if c.recovery_score < float(CONFIG["min_recovery_score"]):
                    log.info(
                        f"  {c.ticker}: skipped - recovery not strong enough "
                        f"({c.history_pattern}, score {c.recovery_score:.2f})"
                    )
                    continue
            if c.rsi < float(CONFIG.get("buy_min_rsi", 42)) or c.rsi > float(CONFIG.get("buy_max_rsi", 74)):
                log.info(f"  {c.ticker}: skipped - RSI {c.rsi:.0f} outside buy band")
                continue
            if c.tier < 3 and c.rel_volume < 0.7 and c.strategy_setup != "orb_long_breakout":
                continue
            if c.strategy_setup == "orb_short_breakdown":
                continue
            if c.strategy_setup == "orb_long_breakout" and CONFIG["orb_skip_high_vix"] and regime.vix_level in ("high", "extreme"):
                continue
            if (
                c.strategy_setup != "orb_long_breakout"
                and not CONFIG.get("allow_weak_fundamental_buys", False)
            ):
                if c.fundamental.overall_quality == "WEAK":
                    log.info(f"  {c.ticker}: skipped - fundamental quality WEAK")
                    continue
                if c.fundamental.data_confidence == "VERY_LOW":
                    log.info(f"  {c.ticker}: skipped - fundamental data confidence VERY_LOW")
                    continue

            strategy = c.strategy_setup or f"local_{regime.state.lower()}_alpha"
            learn_adj = self.learning.adjustment(c.ticker, strategy)
            reliability_penalty = self.learning.reliability_penalty(c.ticker, strategy)
            if learn_adj < 0:
                log.info(
                    f"  {c.ticker}: learning caution {learn_adj:+.2f}; "
                    f"requiring stronger confirmation, not blacklisting"
                )
            if reliability_penalty > 0:
                log.info(
                    f"  {c.ticker}: reliability penalty {reliability_penalty:.2f}; "
                    f"recent win rate is below target"
                )
            edge_score = self._local_edge_score(c, regime, portfolio)
            threshold = 0.56 if c.strategy_setup == "orb_long_breakout" else {
                "BULL": 0.62,
                "NEUTRAL": 0.66,
                "BEAR": 0.70,
                "CRASH": 0.76,
            }.get(regime.state, 0.58)
            threshold += max(0.0, -learn_adj) + reliability_penalty
            if edge_score < threshold:
                probe_ok = (
                    CONFIG.get("allow_probe_buys", True)
                    and probe_buys < int(CONFIG.get("max_probe_buys_per_cycle", 1))
                    and not items
                    and regime.state in ("BULL", "NEUTRAL")
                    and edge_score >= float(CONFIG.get("probe_min_edge", 0.52))
                    and c.alpha_score >= float(CONFIG.get("probe_min_alpha", 0.62))
                    and c.fundamental.overall_quality in ("STRONG", "MODERATE", "DATA_UNAVAILABLE")
                    and c.fundamental.data_confidence != "VERY_LOW"
                    and 45 <= c.rsi <= 68
                )
                if not probe_ok:
                    continue
                log.info(
                    f"  {c.ticker}: probe buy allowed - edge {edge_score:.2f} below "
                    f"strict threshold {threshold:.2f}; testing small size first"
                )
                consensus = CONFIG["min_consensus"]
                sizing = "probe"
                probe_buys += 1
            else:
                consensus = min(0.92, max(CONFIG["min_consensus"], edge_score + 0.16 + learn_adj))
                if consensus >= 0.82:
                    sizing = "high"
                elif consensus >= 0.74:
                    sizing = "medium"
                else:
                    sizing = "low"

            reason = (
                f"Local council: edge {edge_score:.2f}, alpha {c.alpha_score:.2f}, "
                f"{c.history_pattern}, recovery {c.recovery_score:.2f}, "
                f"downtrend risk {c.downtrend_risk:.2f}, learn {learn_adj:+.2f}"
            )
            items.append({
                "ticker": c.ticker,
                "tier": c.tier,
                "action": "BUY",
                "strategy": strategy,
                "consensus_score": round(consensus, 3),
                "sizing": sizing,
                "veto": False,
                "votes": self._local_votes(c, consensus, reason, regime),
            })

        return self._build_signals(items, candidates, portfolio, regime)

    def _local_edge_score(self, c: ScoredStock, regime: MarketRegime, portfolio: Portfolio) -> float:
        strategy = c.strategy_setup or f"local_{regime.state.lower()}_alpha"
        learn_adj = self.learning.adjustment(c.ticker, strategy)
        teacher_adj = self.learning.teacher_adjustment(c.ticker, strategy)
        regime_bonus = {"BULL": 0.04, "NEUTRAL": 0.0, "BEAR": -0.06, "CRASH": -0.18}.get(regime.state, 0.0)
        tier_bonus = 0.03 if regime.state == "BULL" and c.tier in (1, 2) else 0.02 if c.tier == 3 and regime.state in ("BEAR", "CRASH") else 0.0
        recovery_component = (c.recovery_score * 0.26) - (c.downtrend_risk * 0.22)
        technical_component = c.alpha_score * 0.56
        volume_component = min(c.rel_volume, 3.0) / 3.0 * 0.06
        rsi_component = 0.06 if 45 <= c.rsi <= 68 else 0.02 if 42 <= c.rsi <= 74 else -0.06
        pattern_bonus = {
            "confirmed_uptrend": 0.06,
            "recovery_after_pullback": 0.05,
            "long_uptrend_pullback_recovering": 0.04,
            "sideways_or_unproven": -0.03,
            "falling_downtrend": -0.16,
        }.get(c.history_pattern, -0.04)
        score = technical_component + recovery_component + volume_component + rsi_component + pattern_bonus + regime_bonus + tier_bonus + learn_adj + teacher_adj
        if c.strategy_setup == "orb_long_breakout":
            score += 0.05
        return max(0.0, min(1.0, score))

    def _local_votes(self, c: ScoredStock, consensus: float, reason: str, regime: MarketRegime) -> list:
        action = "BUY"
        votes = [
            {
                "mind": "buffett",
                "action": action,
                "conviction": round(max(0.50, consensus - (0.08 if c.fundamental.overall_quality == "WEAK" else 0.02)), 3),
                "reasoning": f"Quality {c.fundamental.overall_quality}; avoid weak fundamentals unless momentum compensates.",
            },
            {
                "mind": "simons",
                "action": action,
                "conviction": round(consensus, 3),
                "reasoning": f"Alpha {c.alpha_score:.2f}, RSI {c.rsi:.0f}, relVol {c.rel_volume:.1f}x.",
            },
            {
                "mind": "tudor",
                "action": action,
                "conviction": round(max(0.50, consensus + (0.03 if regime.state == "BULL" else -0.05)), 3),
                "reasoning": f"{regime.state} regime; {c.history_pattern} with downtrend risk {c.downtrend_risk:.2f}.",
            },
            {
                "mind": "lynch",
                "action": action,
                "conviction": round(max(0.50, consensus - 0.03), 3),
                "reasoning": f"Recovery score {c.recovery_score:.2f}; buy only if rebound has evidence.",
            },
            {
                "mind": "soros",
                "action": action,
                "conviction": round(max(0.50, consensus - 0.02), 3),
                "reasoning": reason,
            },
        ]
        return votes

    def _build_prompt(self, candidates, portfolio, regime) -> str:
        port = {
            "total_usd":    round(portfolio.total_usd, 2),
            "cash_usd":     round(portfolio.cash_usd, 2),
            "deployable":   round(portfolio.deployable_cash, 2),
            "open_positions": portfolio.open_count,
            "tier1_value":  round(portfolio.tier1_value, 2),
            "tier2_value":  round(portfolio.tier2_value, 2),
            "tier3_value":  round(portfolio.tier3_value, 2),
            "positions":    list(portfolio.positions.keys()),
        }
        reg = {
            "state":      regime.state,
            "score":      regime.score,
            "spy_trend":  regime.spy_trend,
            "spy_rsi":    regime.spy_rsi,
            "vix":        regime.vix_level,
            "momentum":   regime.momentum,
            "allocation": regime.allocation,
            "reasoning":  regime.reasoning,
        }
        stocks = [asdict(c) for c in candidates]
        return f"""
Date: {datetime.date.today().isoformat()} | {datetime.datetime.now(IST).strftime('%H:%M IST')}

MARKET REGIME:
{json.dumps(reg, indent=2)}

PORTFOLIO (live balance  add money anytime, sizing adjusts automatically):
{json.dumps(port, indent=2)}

CANDIDATES ({len(stocks)} stocks across tiers):
{json.dumps(stocks, indent=2)}

Vote on each stock. Apply regime rules strictly.
Only output BUY/SELL where consensus >= {CONFIG['min_consensus']:.0%}.
Available slots: {CONFIG['max_open_positions'] - portfolio.open_count} more positions.
"""

    def _build_signals(self, items, candidates, portfolio, regime) -> List[StockSignal]:
        cmap = {c.ticker: c for c in candidates}
        signals = []
        remaining_cash = float(portfolio.deployable_cash)
        remaining_cycle_deploy = float(portfolio.total_usd) * float(CONFIG.get("max_new_deploy_pct_per_cycle", 0.25))
        max_new_positions = int(CONFIG.get("max_new_positions_per_cycle", 2))
        emitted_buys = 0
        tier_remaining = {
            1: portfolio.tier_capacity(1, regime),
            2: portfolio.tier_capacity(2, regime),
            3: portfolio.tier_capacity(3, regime),
        }

        for item in items:
            ticker  = item.get("ticker")
            c       = cmap.get(ticker)
            if not c: continue

            consensus = float(item.get("consensus_score", 0))
            veto      = bool(item.get("veto", False))
            action    = item.get("action", "SKIP")
            tier      = int(item.get("tier", c.tier))

            if consensus < CONFIG["min_consensus"] or veto: continue
            if action not in ("BUY", "SELL"): continue
            if action == "BUY" and emitted_buys >= max_new_positions:
                continue
            proposed_strategy = c.strategy_setup or item.get("strategy","unknown")
            if (
                action == "BUY"
                and c.strategy_setup != "orb_long_breakout"
                and (c.history_pattern == "falling_downtrend" or c.downtrend_risk > float(CONFIG["max_downtrend_risk"]))
            ):
                log.info(
                    f"  {ticker}: buy vetoed because history is falling "
                    f"({c.history_pattern}, risk {c.downtrend_risk:.2f})"
                )
                continue
            if (
                action == "BUY"
                and c.strategy_setup != "orb_long_breakout"
                and c.recovery_score < float(CONFIG["min_recovery_score"])
            ):
                log.info(
                    f"  {ticker}: buy vetoed because recovery score is weak "
                    f"({c.history_pattern}, score {c.recovery_score:.2f})"
                )
                continue
            if action == "BUY" and (
                c.rsi < float(CONFIG.get("buy_min_rsi", 42))
                or c.rsi > float(CONFIG.get("buy_max_rsi", 74))
            ):
                log.info(f"  {ticker}: buy vetoed because RSI {c.rsi:.0f} is outside buy band")
                continue
            if (
                action == "BUY"
                and c.strategy_setup == "orb_long_breakout"
                and CONFIG["orb_skip_high_vix"]
                and regime.vix_level in ("high", "extreme")
            ):
                log.info(f"  {ticker}: ORB skipped because India VIX is {regime.vix_level}")
                continue

            # Crash regime: no tier 1 or 2 buys
            if regime.state == "CRASH" and tier < 3 and action == "BUY":
                log.info(f"  {ticker}: CRASH regime veto  no tier {tier} buys")
                continue

            # Fundamental quality gate. By default, weak fundamentals are blocked for all tiers.
            if (
                action == "BUY"
                and c.strategy_setup != "orb_long_breakout"
                and not CONFIG.get("allow_weak_fundamental_buys", False)
            ):
                if c.fundamental.overall_quality == "WEAK":
                    log.info(f"  {ticker}: fundamental quality WEAK - buy vetoed")
                    continue
                if c.fundamental.data_confidence == "VERY_LOW":
                    log.info(f"  {ticker}: fundamental data confidence VERY_LOW - buy vetoed")
                    continue

            # Tier capacity check. In strong/neutral markets, allow idle cash to flex across tiers
            # when preferred tiers have no candidates, while still respecting per-position sizing.
            capacity = tier_remaining.get(tier, portfolio.tier_capacity(tier, regime))
            if capacity <= 0 and action == "BUY":
                if CONFIG.get("allow_allocation_flex", True) and regime.state in ("BULL", "NEUTRAL"):
                    capacity = min(remaining_cash, remaining_cycle_deploy)
                else:
                    log.info(f"  {ticker}: Tier {tier} at allocation limit for {regime.state} regime")
                    continue

            price     = c.price
            sizing    = item.get("sizing", "medium")
            
            # Dynamically adjust position sizes for smaller accounts to ensure capital is actually deployed
            if sizing == "probe":
                size_pct = float(CONFIG.get("probe_position_pct", 0.03))
            elif portfolio.total_usd <= 25000:
                size_pct = {"high": 0.12, "medium": 0.08, "low": 0.05}.get(sizing, 0.08)
            elif portfolio.total_usd <= 150000:
                size_pct = {"high": 0.12, "medium": 0.08, "low": 0.05}.get(sizing, 0.08)
            else:
                size_pct = {"high": 0.10, "medium": 0.07, "low": 0.04}.get(sizing, 0.07)
            if sizing != "probe":
                size_pct *= float(CONFIG.get("position_size_scale", 1.0))
            size_pct = min(size_pct, float(CONFIG.get("max_position_pct", 0.10)))
            trade_usd = min(
                portfolio.total_usd * size_pct,
                remaining_cash * 0.90,
                remaining_cycle_deploy,
                capacity if action=="BUY" else portfolio.total_usd,
            )
            if trade_usd < float(CONFIG.get("min_trade_value", 1000)): continue

            if c.strategy_setup == "orb_long_breakout":
                qty = max(1, int(CONFIG["orb_trade_lots"]) * int(CONFIG["orb_lot_size"]))
                trade_usd = qty * price
            else:
                qty = max(1, int(trade_usd / price))
            actual_value = round(qty * price, 2)
            if action == "BUY" and actual_value > remaining_cash:
                continue
            if action == "BUY" and actual_value < float(CONFIG.get("min_trade_value", 1000)):
                continue

            # Per-tier profit params. Fixed loss exits are disabled; exits are chart-health based.
            if action == "BUY" and c.strategy_setup == "orb_long_breakout":
                stop_loss = c.strategy_stop_loss
                take_profit = c.strategy_take_profit
                target_1 = c.strategy_target_1
                trail_stop = c.strategy_stop_loss
            else:
                tp_pct = self._adaptive_take_profit_pct(tier, consensus, c.alpha_score, actual_value)
                stop_loss = 0.0
                take_profit = round(price*(1+tp_pct), 4)
                target_1 = 0.0
                trail_stop = 0.0

            reason = "; ".join([v.get("reasoning","")[:60]
                                 for v in item.get("votes",[])[:2]])

            signals.append(StockSignal(
                ticker=ticker, tier=tier, action=action,
                consensus=consensus, price=price, qty=qty,
                value_usd=actual_value,
                stop_loss=round(stop_loss, 4),
                take_profit=round(take_profit, 4),
                trail_stop=round(trail_stop, 4),
                strategy=c.strategy_setup or item.get("strategy","unknown"),
                reasoning=reason, alpha_score=c.alpha_score,
                fundamental_quality=c.fundamental.overall_quality,
                data_confidence=c.fundamental.data_confidence,
                target_1=round(target_1, 4),
                hard_exit_time=c.strategy_hard_exit_time if c.strategy_setup == "orb_long_breakout" else "",
            ))
            if action == "BUY":
                remaining_cash = max(0.0, remaining_cash - actual_value)
                remaining_cycle_deploy = max(0.0, remaining_cycle_deploy - actual_value)
                tier_remaining[tier] = max(0.0, tier_remaining.get(tier, 0.0) - actual_value)
                emitted_buys += 1

        signals.sort(key=lambda s: s.consensus * s.alpha_score, reverse=True)
        return signals
