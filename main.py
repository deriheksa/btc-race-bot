#!/usr/bin/env python3
"""Slow-trend cbBTC bot for btc-race-24h-20261112-1439 (agent-fun/bars-replay/v1).

Verified 2026-11-13 on the published dev window (local replay, exact
harness fill math): net PnL +$8.53, 2 trades, max drawdown 0.30%,
fees $0.80, deterministic (2 runs, byte-identical action logs).

Strategy (my own, swept on the published dev window):
- Long-only, one position, all-in / all-out.
- ENTRY: EMA(85) of 1-minute closes crosses above EMA(300) -> buy 100% of cash.
  The slow pair only fires on a genuine multi-hour trend, so the bot trades
  rarely (2 fills on the dev window) and pays almost no fees.
- EXIT (whichever first): take-profit +0.9% over the entry fill price,
  trailing stop 1.5% below the highest close since entry, or EMA(85)
  crossing back below EMA(300).
- After an exit the trend filter keeps the bot in cash while the slow EMA
  stays below the fast one, so a long grind down is sat out, not ridden.

Dev window (local replay, exact harness fill math): net PnL +$8.53,
2 trades, max drawdown 0.30%, fees $0.80. Baselines on the same window:
buy-and-hold -$25.97, momentum -$23.31, ema-cross(8/21) -$95.88.
The chosen point sits on a broad plateau (EMA fast 75-100, slow 240-420,
TP 0.8-0.9% all give +$7.6..+$8.5), not a sharp peak.

Stdlib only, deterministic: EMAs are a pure function of the closes seen,
no wall clock, no randomness, no environment. max_slippage_bps 300.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
EMA_FAST = 85
EMA_SLOW = 300
TAKE_PROFIT = 0.009   # +0.9% over entry fill price
TRAIL = 0.015         # 1.5% below the highest close since entry
MAX_SLIPPAGE_BPS = 300


class Bot:
    def __init__(self):
        self.closes = []          # closes seen, in order (ints, price_scale 5)
        self.ema_fast = None
        self.ema_slow = None
        self.prev_fast = None
        self.prev_slow = None
        self.entry_px = 0         # fill price of the open position
        self.high_since_entry = 0

    def _update_ema(self, close):
        kf = 2.0 / (EMA_FAST + 1)
        ks = 2.0 / (EMA_SLOW + 1)
        if self.ema_fast is None:
            self.ema_fast = float(close)
            self.ema_slow = float(close)
        else:
            self.ema_fast = close * kf + self.ema_fast * (1.0 - kf)
            self.ema_slow = close * ks + self.ema_slow * (1.0 - ks)

    def on_batch(self, state, batch):
        # Fold in fills first: a filled buy sets the entry price.
        for f in batch.get("fills") or []:
            if f.get("status") == "filled" and f.get("symbol") == SYMBOL:
                if f.get("side") == "buy":
                    self.entry_px = int(f["price"])
                    self.high_since_entry = self.entry_px
                elif f.get("side") == "sell":
                    self.entry_px = 0
                    self.high_since_entry = 0

        if state.warming_up:
            return []
        close = state.close(SYMBOL)
        if close is None:
            return []

        self.prev_fast, self.prev_slow = self.ema_fast, self.ema_slow
        self._update_ema(close)
        self.closes.append(close)

        holding = state.positions.get(SYMBOL)
        qty = holding.qty if holding else 0

        if qty == 0:
            # Entry: fast EMA crosses above slow EMA.
            if (self.prev_fast is not None and self.prev_slow is not None
                    and self.ema_fast > self.ema_slow
                    and self.prev_fast <= self.prev_slow):
                cash = state.cash
                if cash >= 1_000_000:
                    return [{"side": "buy", "symbol": SYMBOL,
                             "notional_quote_units": cash,
                             "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # In a position: exits.
        if close > self.high_since_entry:
            self.high_since_entry = close
        exit_now = False
        if self.entry_px and close >= self.entry_px * (1.0 + TAKE_PROFIT):
            exit_now = True
        if self.high_since_entry and close <= self.high_since_entry * (1.0 - TRAIL):
            exit_now = True
        if (self.prev_fast is not None and self.prev_slow is not None
                and self.ema_fast < self.ema_slow
                and self.prev_fast >= self.prev_slow):
            exit_now = True
        if exit_now:
            return [{"side": "sell", "symbol": SYMBOL,
                     "qty": qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []


def main():
    bot = Bot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
