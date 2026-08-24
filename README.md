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
3. Add valors: enter the **Yahoo Finance symbol** and the **amount** of units
   you hold. Optionally enter a **target share (%)** — the percent of the
   wallet this valor should hold — to enable the deviation sensor.
   Tick *Add another valor* to keep adding, or submit to finish.

### Managing a wallet

Open the config entry and click **Configure** to:

- edit wallet settings (name, base currency, update interval, invested amount),
- add a valor,
- remove a valor,
- edit a valor (amount and target share; clearing the target share removes it).

The sum of target shares may not exceed 100% (a sum below 100% is fine —
the remainder can be assets held outside this integration).

Changes take effect immediately (the wallet reloads automatically).

## Entities

| Entity | State | Useful attributes |
|---|---|---|
| `sensor.<wallet>_<symbol>` | valor value in base currency | `amount`, `unit_price`, `quote_currency`, `fx_rate`, `day_change`, `day_change_pct`, `short_name`, `share`, `target_share`, `share_deviation`, `rebalance_amount` |
| `sensor.<wallet>_<symbol>_deviation` | actual share − target share, in percent points (unavailable when no target is set) | `target_share`, `share`, `value`, `rebalance_amount` |
| `sensor.<wallet>_total` | wallet total in base currency | `valors` (per-valor breakdown incl. `share`, `target_share`), `unavailable_valors` |
| `sensor.<wallet>_invested` | invested amount (unavailable when not set) | — |
| `sensor.<wallet>_profit` | `total − invested` | `invested`, `total` |
| `sensor.<wallet>_profit_pct` | profit as % of the invested amount | `invested`, `total` |

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

## Notes

- Update interval is per wallet; Yahoo may rate-limit very aggressive
  polling, so the minimum interval is 5 minutes.
- If a single symbol fails (delisted, typo), the rest of the wallet still
  updates; the affected sensor becomes unavailable and is listed in the
  total sensor's `unavailable_valors` attribute.

## License

MIT
