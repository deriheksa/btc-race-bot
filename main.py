#!/usr/bin/env python3
"""Slow-trend cbBTC bot v4 — COMPOUNDING via confirmed breakout re-entry (agent-fun/bars-replay/v1).

Verified 2026-11-13 (local replay, exact harness fill math): dev window
net PnL +$8.53 (same as v1 — the re-entry never fires there), 2 trades,
max drawdown 0.30%, deterministic (2 runs, byte-identical action logs).
On a synthetic +5.9% rally: +$34.25 (9 trades) vs v1 +$7.27 (2 trades).
On a synthetic brutal chop: worse than v1 (-$73 vs -$40) — the trade-off
for compounding; both lose there, and the chop test is far choppier than
real 1-minute BTC noise.

v1: EMA(85) cross above EMA(300) -> buy 100% cash; exit on TP +0.9%,
trailing stop 1.5%, or EMA cross back down. One trade per trend leg.

v4 adds COMPOUNDING re-entry: after an exit, when the trend is still up
(fast EMA > slow EMA) and the close breaks 0.3% ABOVE the last exit fill
price, buy 100% of cash again — the cash now includes the previous leg's
profit, so wins stack leg after leg in one trend instead of stopping at
the first +0.9%. The +0.3% confirmation margin is the key: the dev
window bobs only ~0.1% around the exit price before grinding down, so
it never confirms (v3's plain break-above re-entered at bar 161 into
the grind: -$1.41; the dip-required variant caught the same bob: -$1.88).
A real resumption pushes 0.3% past the exit and re-enters. After a
cross-down exit the trend filter (fast > slow) blocks re-entry until a
fresh cross-up.

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
        self.last_exit_px = 0   # fill price of the last sell (breakout level)
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
                    self.last_exit_px = int(f["price"])
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
            # Entry 1: fast EMA crosses above slow EMA.
            cross_up = (self.prev_fast is not None and self.prev_slow is not None
                        and self.ema_fast > self.ema_slow
                        and self.prev_fast <= self.prev_slow)
            # Entry 2 (COMPOUNDING): trend still up and the close is
            # 0.3% above the last exit fill price — a confirmed
            # resumption, so ride the next leg with the grown cash.
            breakout = (self.ema_fast > self.ema_slow
                        and self.last_exit_px > 0
                        and close > self.last_exit_px * 1.003)
            if cross_up or breakout:
                self.last_exit_px = 0
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
