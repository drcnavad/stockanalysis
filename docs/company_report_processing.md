# Company Report Processing

## Overview

Turns quarterly fundamentals into ratios, growth, TTM metrics and a price-based fair value for each stock.

## Input

- **`Reports/balance_sheet.csv`** (from `company_report_autofetch.py`, amounts in millions)
- **Price**: latest Close in `Reports/signal_analysis.csv`; yfinance (batch download) for symbols not tracked there
- **Sector**: `sector_mapping.symbol_sector` (used for peer medians)

## Processing Steps

### 1. Quarterly Ratios & Growth (all quarters)
- Profitability: ROE, ROA, NetProfitMargin, GrossMargin
- Per share: EPS, RevenuePerShare, AssetsPerShare, OperatingIncomePerShare
- Efficiency & health: AssetTurnover, EquityTurnover, DebtToAssets, EquityRatio, EquityMultiplier, ROIC_Approx, OperatingToNetIncome, DebtToEBITDA_Approx
- Growth: Revenue / NetIncome / EPS (QoQ, YoY), OperatingLeverage — matched by date (QoQ ≈ 91 days, YoY ≈ 365 days, ±25%) so gaps in the
  quarterly history never compare the wrong quarters; denominators use |previous| so a loss narrowing counts as positive growth
- Equity-based ratios (ROE, EquityTurnover, EquityMultiplier) are left empty when equity ≤ 0
- Duplicate quarters are dropped; a missing balance item is forward-filled from the previous quarter (max 1)

### 2. TTM Metrics (all quarters)
- Income items: sum of the last 4 consecutive quarters (window ≤ 300 days); with only 2–3 quarters the sum is annualized ×4/n
  (`TTM_Quarters` column); fewer quarters → empty, never 0
- Balance items: average of first and last quarter in the window
- Ratios: TTM_ROE, TTM_ROA, TTM_NetProfitMargin, TTM_OperatingMargin, TTM_GrossMargin, TTM_EPS, TTM_RevenuePerShare, TTM_AssetTurnover, TTM_EquityTurnover

### 3. Fair Value (latest quarter only)
- Ratios: PE, PB, PS, PEG vs sector medians (global median when a sector has < 3 peers)
- Methods: sector P/E, P/B, P/S, growth-adjusted P/E, Graham, owner-earnings DCF
- Composite: weighted mean of methods within 0.25×–4× price (profitable vs unprofitable weight sets)
- Outputs: FairValue_Composite / Low / High, MethodDispersion, Valuation_Confidence, Price_vs_FairValue, MarginOfSafety, Valuation_Signal, MarketCap, EV, EV_Revenue_Ratio, EarningsYield, BookToMarket, POI_Ratio

## Output

**`Reports/complete_company_analysis.xlsx`**
- `1_Historical_All_Quarters`: all quarters, ratios + growth + TTM (no price-based columns)
- `2_Latest_Quarter_Complete`: latest quarter per symbol + all valuation columns (read by scoring, visualization and `app.py`)

`app.py` reads: FairValue_Composite, PE_Ratio, PB_Ratio, RevenueGrowth_YoY, TTM_ROE, TTM_NetProfitMargin, Debt_to_Equity.

## Dependencies

pandas, numpy, yfinance, openpyxl
