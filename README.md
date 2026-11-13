# International Cost of Capital

A Streamlit calculator for comparing a project's cost of equity, cost of debt, and weighted average cost of capital (WACC) in USD and the selected country's currency.

## Run locally

Use Python 3.11 or later. From the folder containing the three project files, run:

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Use the app

Choose an industry and target country, then set the equity risk premium, debt share, and project tax jurisdiction. The app opens on **Software (System & Application)** and **United States**, with the **U.S. tax rate**, a **6.5% equity risk premium**, and **30% debt financing**.

Use **Manual overrides** to replace a fetched beta, tax rate, bond yield, or BBB borrowing spread. An empty required input prompts for a value. The selected-country spread defaults to the automatic conversion. **Data sources and dates** shows each observation and its fetch time and includes **Refresh data**. **All-industry comparison** applies the same project assumptions to each industry. The CSV contains the selected industry first, followed by the comparison rows.

## Data sources

| Input | Source |
| --- | --- |
| U.S. industry unlevered betas | [Damodaran workbook](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls), with the corresponding [HTML table](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html) as a fallback |
| Corporate income tax rates | [OECD Corporate Income Tax Rates Database](https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html), latest available combined statutory rate by country |
| USD BBB borrowing spread | [Damodaran ratings and spreads](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html), Baa2/BBB for large non-financial firms |
| 10-year government yields | [Trading Economics](https://tradingeconomics.com/bonds), public country quote pages |

Some countries require manual inputs because the providers may lack a tax observation or a 10-year quote. A manual entry can also be used if a source is temporarily unavailable.

## Calculation

For target debt share `d` and tax rate `t`, the app sets `D/E = d / (1 − d)` and relevers the industry beta as `βE = βU × [1 + (1 − t) × D/E]`. USD cost of equity is the U.S. 10-year yield plus `βE × ERP`.

The ratio of gross government yields is `F = (1 + local yield) / (1 + U.S. yield)`. Local cost of equity is `(1 + USD cost of equity) × F − 1`. The automatic local BBB spread is `USD BBB spread × F`. Pretax debt cost is the corresponding government yield plus the BBB spread. In both currencies, `WACC = (1 − d) × cost of equity + d × pretax cost of debt × (1 − t)`.

The model uses government yields as risk-free proxies, applies a single project tax rate to both currencies, and assumes interest deductions are fully usable.

### Colombia example

Select Colombia and enter a beta of 0.80, a 35% tax rate, a 6.5% equity risk premium, 30% debt, a 4% U.S. yield, a 12% Colombia yield, and a 1.5% USD BBB spread. Leave the local spread automatic. Expected WACC is **8.5265% in USD** and **16.0670% in COP**.

## Deploy on Streamlit Community Cloud

1. Put `app.py`, `requirements.txt`, and `README.md` in the root of a GitHub repository.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), create an app and select the repository, branch, and `app.py` as the entry point. Select Python 3.11 or later under **Advanced settings** if needed.
3. Deploy and open the app. Confirm that the default results appear, then select Colombia and use **Refresh data** to check the source dates and inputs.

[Streamlit deployment documentation](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
