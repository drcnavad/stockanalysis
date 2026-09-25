# Company Report Scoring - Fundamental Weight

## Overview

Multi-factor fundamental scoring. Output: `Fundamental_Weight` clipped to **-10 to +10**.

## Input

- **`Reports/complete_company_analysis.xlsx`** (sheet `2_Latest_Quarter_Complete`)
- Required: Symbol, Price_vs_FairValue, Valuation_Confidence, TTM_ROE, TTM_ROA, TTM_NetProfitMargin, RevenueGrowth_YoY, Debt_to_Equity, DebtToAssets, TTM_AssetTurnover, TTM_EquityTurnover, EPSGrowth_YoY, NetIncomeGrowth_YoY, TTM_GrossMargin, TTM_OperatingMargin

## Component Weights (8, total 100%)

| Component | Weight | Metrics |
|-----------|--------|---------|
| Valuation | 25% | Price_vs_FairValue × Valuation_Confidence |
| Profitability | 20% | TTM_ROE, TTM_ROA, TTM_NetProfitMargin |
| Growth | 15% | RevenueGrowth_YoY |
| Debt | 15% | Debt_to_Equity, DebtToAssets (negative Debt_to_Equity = negative equity → worst score, −5) |
| Earnings Quality | 10% | EPSGrowth_YoY, NetIncomeGrowth_YoY |
| Efficiency | 5% | TTM_AssetTurnover, TTM_EquityTurnover |
| Margin Quality | 5% | TTM_GrossMargin, TTM_OperatingMargin |
| Return Quality | 5% | TTM_ROE, TTM_ROA |

## Scoring Logic (summary)

- **Valuation**: < -20% → 10, < -10% → 7, < 10% → 3, >= 10% → -5; multiplied by Valuation_Confidence (default 0.6, floor 0.25 when a fair value exists)
- **Other components**: threshold tiers per metric, each component capped -10 to +10
- **Final**: weighted sum, clipped to -10..+10, rounded to 2 decimals

## Output

**`Reports/balance_sheet_weights.csv`**: Symbol, 8 component scores, Fundamental_Weight (read by `main_signal_analysis.ipynb`)

## Dependencies

pandas, numpy, openpyxl
