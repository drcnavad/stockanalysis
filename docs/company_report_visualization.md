# Company Report Visualization

## Overview

Plotly charts for one symbol plus a per-sector fair value overview. Pick the stock with the `COMPANY_SYMBOL` environment variable (e.g. `COMPANY_SYMBOL=NVDA jupyter nbconvert --execute --inplace company_report_visualization.ipynb`) or edit the default in the Single Stock cell. An unknown symbol falls back to the first available one and lists the valid symbols.

## Data Source

`Reports/complete_company_analysis.xlsx`
- `1_Historical_All_Quarters`: line charts (clipped to the 2nd/98th percentile)
- `2_Latest_Quarter_Complete`: price, fair value, P/E, P/B, Sector

## Charts

**Single stock**
1. Revenue & Net Income ($M)
2. Revenue Growth YoY (%)
3. Gross Margin (%)
4. Balance Sheet: assets, liabilities, equity ($M)
5. Fair Value vs Current Price
6. P/E, P/B vs sector trimmed-mean averages

**By sector**: horizontal Fair Value vs Current Price bars for every stock in each sector.

## Helpers

- `layout(fig, title, ...)`: shared styling + display
- `line_chart(d, traces, title, yaxis_title, hline=False)`: traces = [(column, label, color)]
- `bar_chart(d, traces, title, value_title, horizontal=False)`

## Dependencies

numpy, pandas, plotly, IPython.display
