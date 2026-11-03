# My Wallet for Home Assistant

[![Validate](https://github.com/no-time-for-good-name/ha-my-wallet/actions/workflows/validate.yml/badge.svg)](https://github.com/no-time-for-good-name/ha-my-wallet/actions/workflows/validate.yml)

A [HACS](https://hacs.xyz) custom integration that tracks investment wallets in
Home Assistant. Each wallet is a config entry that holds a list of **valors**
(market instruments) with configurable amounts, valued live via
**Yahoo Finance**.

## Features

- **Multiple wallets** — each wallet is created separately via
  *Settings → Devices & Services → Add Integration → My Wallet*.
- **Valors with amounts** — any Yahoo Finance symbol works: stocks
  (`AAPL`, `VWAGY`), ETFs (`VWCE.DE`, `SPY`), crypto (`BTC-USD`),
  indices (^DJI), funds, etc.
- **Per-wallet update schedule** — configurable update interval
  (5–1440 minutes) for each wallet independently.
- **Currency conversion** — each wallet has a base currency; valors quoted in
  a foreign currency are converted using live Yahoo FX rates
  (e.g. `USDPLN=X`).
- **Invested amount & profit** — optionally set how much you invested in a
  wallet (in its base currency) to get *Invested*, *Profit*, and *Profit %*
  sensors alongside the total.
- **Free cash tracking & trading** — optionally track the wallet's free cash
  in the base currency (a *Free cash* sensor). Buy/sell valors with the
  `my_wallet.trade` service (or from the wallet's *Configure* menu) and the
  transaction value is automatically subtracted from / added to the free
  cash; deposits and withdrawals move the cash as well.
- **Target allocation** — optionally set a target share (in %) for each valor
  to see how far its actual share deviates from the target, plus a rebalancing
  hint in the base currency.
- **Sensors** — one sensor per valor (value, unit price, FX rate, day change)
  plus a wallet *Total* sensor, all grouped under one device per wallet.
- **`my_wallet.refresh` service** — force an immediate update of selected
  wallets without waiting for the schedule.
- **Translations** — English, Polish, and Czech.

No external Python dependencies — prices are fetched directly from Yahoo
Finance's chart API using `aiohttp`.

## Installation

### HACS (recommended)

1. In HACS, go to **⋯ → Custom repositories**.
2. Add this repository with category **Integration**.
3. Find **My Wallet** in HACS and download it.
4. Restart Home Assistant.

### Manual

Copy `custom_components/my_wallet` into the `custom_components` directory of
your Home Assistant configuration and restart.

## Configuration

1. Go to **Settings → Devices & Services → Add Integration** and search for
   **My Wallet**.
2. Fill in the wallet settings:
   - **Wallet name**
   - **Base currency** — currency the wallet total is displayed in
   - **Update interval** — minutes between Yahoo Finance updates
   - **Invested amount** *(optional)* — how much you invested, in the base
     currency; enables the profit sensors
   - **Free cash** *(optional)* — tracked free cash in the base currency;
     enables the *Free cash* sensor and cash-aware trading
3. Add valors: enter the **Yahoo Finance symbol** and the **amount** of units
   you hold. Optionally enter a **target share (%)** — the percent of the
   wallet this valor should hold — to enable the deviation sensor.
   Tick *Add another valor* to keep adding, or submit to finish.

### Managing a wallet

Open the config entry and click **Configure** to:

- edit wallet settings (name, base currency, update interval, invested
  amount, free cash),
- add a valor,
- remove a valor,
- edit a valor (amount and target share; clearing the target share removes it),
- adjust the invested amount (deposit / withdraw / set),
- adjust the free cash (deposit / withdraw / set),
- buy or sell a valor (quantity + optional unit price).

The sum of target shares may not exceed 100% (a sum below 100% is fine —
the remainder can be assets held outside this integration).

Changes take effect immediately (the wallet reloads automatically).

## Entities

| Entity | State | Useful attributes |
|---|---|---|
| `sensor.<wallet>_<symbol>` | valor value in base currency | `amount`, `unit_price`, `quote_currency`, `fx_rate`, `day_change`, `day_change_pct`, `short_name`, `share`, `target_share`, `share_deviation`, `rebalance_amount`, `rebalance_units`, `rebalance_precision` |
| `sensor.<wallet>_<symbol>_deviation` | actual share − target share, in percent points (unavailable when no target is set) | `target_share`, `share`, `value`, `rebalance_amount`, `rebalance_units`, `rebalance_precision` |
| `sensor.<wallet>_total` | wallet total in base currency | `valors` (per-valor breakdown incl. `share`, `target_share`), `unavailable_valors` |
| `sensor.<wallet>_invested` | invested amount (unavailable when not set) | — |
| `sensor.<wallet>_cash` | tracked free cash (unavailable when not enabled) | — |
| `sensor.<wallet>_profit` | `total − invested` (free cash counts towards the profit when tracking is enabled) | `invested`, `total`, `cash` |
| `sensor.<wallet>_profit_pct` | profit as % of the invested amount | `invested`, `total`, `cash` |

The invested amount is entered in the wallet's base currency and is **not**
re-converted automatically — if you change the base currency later, update the
invested amount yourself.

### Target allocation

For each valor you can optionally define a **target share** — the percentage
of the wallet it should represent. The integration then computes:

- `share` — the actual share, `valor value / wallet total × 100`,
- `share_deviation` — `share − target_share` in percent points;
  **positive means the valor is overweight** (consider selling),
- `rebalance_amount` — `wallet total × target% − valor value` in the base
  currency; **positive is the amount to buy** to reach the target, negative
  the amount to sell.
- `rebalance_units` — the same rebalance expressed in **units of the valor**:
  `−1` means *sell 1 unit*, `2` means *buy 2 units*. It is computed in the
  context of the whole wallet (other valors' target shares included) and
  rounded to the wallet's **rebalance precision** — a per-wallet setting
  (0–3 decimal places, default 0) in the wallet settings. With precision 0
  the wallet only rebalances in whole units; 1–3 allow fractional buys/sells.

Shares are calculated against the total of currently *available* valors —
if one symbol temporarily fails to update, the remaining shares are relative
to the available total.

## Service

```yaml
service: my_wallet.refresh
target:
  entity_id: sensor.my_wallet_total
```

Refreshes the whole wallet that owns the targeted entity, immediately.

### Adjust invested amount

```yaml
service: my_wallet.adjust_invested
data:
  entity_id: sensor.my_wallet_total
  operation: deposit   # deposit | withdraw | set
  amount: 1000.0
```

Updates the wallet's invested amount and stores the result in its
configuration:

- `deposit` — adds `amount` to the current invested amount,
- `withdraw` — subtracts `amount` (rejected if the result would be negative),
- `set` — replaces the invested amount with `amount`.

Any sensor of the wallet can be used as `entity_id`. Profit and profit %
sensors update immediately after the call.

The same operation is available from the UI: *Configure* →
**Adjust the invested amount** — pick the operation (deposit / withdraw /
set), enter the amount, and the form shows the current invested amount.

Deposits and withdrawals of the invested amount **also move the tracked free
cash** (when enabled) — real money in and out of the wallet changes both the
invested total and the free cash. *Set* only replaces the invested amount.

### Free cash

Enable **Free cash** in the wallet settings to track uninvested cash in the
base currency. The *Free cash* sensor shows the current amount, and:

```yaml
service: my_wallet.adjust_cash
data:
  entity_id: sensor.my_wallet_total
  operation: deposit   # deposit | withdraw | set
  amount: 500.0
```

updates it (also available from *Configure* → **Adjust the free cash**).
The same rules apply as for the invested amount: withdrawals that would make
the cash negative are rejected.

When cash tracking is enabled, the *Profit* and *Profit %* sensors count the
free cash towards the wallet value: `profit = total + cash − invested`.

### Trading valors

```yaml
service: my_wallet.trade
data:
  entity_id: sensor.my_wallet_total
  operation: buy       # buy | sell
  symbol: AAPL
  quantity: 10
  unit_price: 195.50   # optional; defaults to the latest live quote
```

- `quantity` is rounded to the wallet's **rebalance precision**
  (0 decimal places ⇒ whole units only); a quantity that rounds to zero is
  rejected.
- The symbol must already exist in the wallet; buying a new valor requires
  adding it first (with amount 0).
- The transaction value `quantity × unit_price` (in the base currency; the
  live quote is FX-converted when needed) is **subtracted from the free cash
  on buy** and **added to it on sell**.
- Buying more than the tracked free cash, or selling more units than held,
  is rejected — nothing is changed.
- The same trade form is available from the UI: *Configure* →
  **Buy or sell a valor**.

## Notes

- Update interval is per wallet; Yahoo may rate-limit very aggressive
  polling, so the minimum interval is 5 minutes.
- If a single symbol fails (delisted, typo), the rest of the wallet still
  updates; the affected sensor becomes unavailable and is listed in the
  total sensor's `unavailable_valors` attribute.

## License

MIT
