"""Calculate a project's cost of capital in USD and a second currency."""

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from io import BytesIO, StringIO
from math import isfinite
from urllib.request import Request, urlopen


BETAS_XLS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
BETAS_HTML = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_INFO = "https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html"
OECD_DATA = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/"
    ".A..ST..S13...?dimensionAtObservation=AllDimensions&format=csvfile"
)
DEFAULT_INDUSTRY = "Software (System & Application)"
COUNTRY_SLUGS = {
    "CIV": "ivory-coast", "CZE": "czech-republic", "GBR": "united-kingdom",
    "HKG": "hong-kong", "KOR": "south-korea", "TWN": "taiwan",
    "TUR": "turkey", "USA": "united-states", "VNM": "vietnam",
}
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


@dataclass(frozen=True)
class CurrencyCosts:
    """Annual financing rates for one currency, expressed as decimals."""

    borrowing_spread: float
    cost_of_equity: float
    pretax_cost_of_debt: float
    after_tax_cost_of_debt: float
    wacc: float


@dataclass(frozen=True)
class CostOfCapital:
    debt_to_equity: float
    levered_beta: float
    currency_factor: float
    usd: CurrencyCosts
    local: CurrencyCosts


def calculate_cost_of_capital(
    *,
    unlevered_beta: float,
    tax_rate: float,
    us_yield: float,
    local_yield: float,
    usd_spread: float,
    debt_share: float = 0.30,
    equity_risk_premium: float = 0.065,
    local_spread_override: float | None = None,
) -> CostOfCapital:
    """Use the class's government-yield proxies and gross-rate conversion.

    Pass all rates and shares as decimals. Pass None for an automatic local
    spread or a number (including zero) to use a manual spread.
    """
    inputs = {
        "Unlevered beta": unlevered_beta,
        "Tax rate": tax_rate,
        "U.S. yield": us_yield,
        "Local yield": local_yield,
        "USD spread": usd_spread,
        "Debt share": debt_share,
        "Equity risk premium": equity_risk_premium,
    }
    if local_spread_override is not None:
        inputs["Local spread override"] = local_spread_override
    for label, value in inputs.items():
        if not isfinite(value):
            raise ValueError(f"{label} must be finite.")

    if not 0 <= debt_share < 1:
        raise ValueError("Debt share must be at least 0% and below 100%.")
    if not 0 <= tax_rate <= 1:
        raise ValueError("Tax rate must be between 0% and 100%.")
    if us_yield <= -1 or local_yield <= -1:
        raise ValueError("Both government yields must be above -100%.")

    equity_share = 1 - debt_share
    debt_to_equity = debt_share / equity_share
    levered_beta = unlevered_beta * (1 + (1 - tax_rate) * debt_to_equity)
    currency_factor = (1 + local_yield) / (1 + us_yield)

    usd_equity_premium = levered_beta * equity_risk_premium
    usd_cost_of_equity = us_yield + usd_equity_premium
    # Equivalent to (1 + USD cost of equity) * currency_factor - 1.
    local_cost_of_equity = local_yield + usd_equity_premium * currency_factor
    local_spread = usd_spread * currency_factor
    if local_spread_override is not None:
        local_spread = local_spread_override

    usd = _currency_costs(
        usd_cost_of_equity, us_yield, usd_spread, tax_rate, debt_share
    )
    local = _currency_costs(
        local_cost_of_equity, local_yield, local_spread, tax_rate, debt_share
    )
    return CostOfCapital(debt_to_equity, levered_beta, currency_factor, usd, local)


def _currency_costs(
    cost_of_equity: float,
    government_yield: float,
    borrowing_spread: float,
    tax_rate: float,
    debt_share: float,
) -> CurrencyCosts:
    pretax_cost_of_debt = government_yield + borrowing_spread
    after_tax_cost_of_debt = pretax_cost_of_debt * (1 - tax_rate)
    wacc = (1 - debt_share) * cost_of_equity + debt_share * after_tax_cost_of_debt
    return CurrencyCosts(
        borrowing_spread, cost_of_equity, pretax_cost_of_debt, after_tax_cost_of_debt, wacc
    )


@dataclass(frozen=True)
class SourceData:
    values: dict[str, float]
    dates: dict[str, str]
    url: str
    fetched_at: str
    status: str = ""


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urlopen(request, timeout=7) as response:
        return response.read()


def _document(payload: bytes):
    from lxml import html

    return html.fromstring(payload)


def _text(element) -> str:
    return " ".join(" ".join(element.itertext()).split())


def _number(value) -> float:
    number = float(str(value).strip().replace("%", "").replace(",", "").replace("−", "-"))
    if not isfinite(number):
        raise ValueError("Non-finite source value.")
    return number


def _beta_rows(rows) -> dict[str, float]:
    name_index = beta_index = None
    betas = {}
    for row in rows:
        cells = [" ".join(str(cell).lower().split()) for cell in row]
        if "industry name" in cells and "unlevered beta" in cells:
            name_index = cells.index("industry name")
            beta_index = cells.index("unlevered beta")
        elif name_index is not None and len(row) > max(name_index, beta_index):
            name = str(row[name_index]).strip()
            if name and name.lower() != "nan":
                try:
                    betas[name] = _number(row[beta_index])
                except ValueError:
                    continue
    if not betas:
        raise ValueError("Industry beta column is unavailable.")
    return betas


def _analysis_date(text: str) -> str:
    match = re.search(rf"as of\s+({MONTH}(?:\s+\d{{1,2}},)?\s+\d{{4}})", text, re.I)
    return match.group(1) if match else ""


def _get_betas():
    try:
        import pandas as pd
        from xlrd.biffh import XLRDError

        try:
            sheets = pd.read_excel(BytesIO(_download(BETAS_XLS)), sheet_name=None, header=None)
        except XLRDError as exc:
            raise ValueError("The beta workbook could not be parsed.") from exc
        betas = {}
        date = ""
        for sheet in sheets.values():
            date = date or _analysis_date(" ".join(map(str, sheet.head(12).to_numpy().ravel())))
            try:
                betas = _beta_rows(sheet.itertuples(index=False, name=None))
            except ValueError:
                continue
            break
        if not betas:
            raise ValueError("No industry data in workbook.")
        return betas, dict.fromkeys(betas, date), BETAS_XLS
    except (ImportError, OSError, ValueError):
        doc = _document(_download(BETAS_HTML))
        betas = _beta_rows(
            [_text(cell) for cell in row.xpath("./th|./td")] for row in doc.xpath("//tr")
        )
        return betas, dict.fromkeys(betas, _analysis_date(_text(doc))), BETAS_HTML


def _get_tax_rates():
    reader = csv.DictReader(StringIO(_download(OECD_DATA).decode("utf-8-sig")))
    if not {"REF_AREA", "TIME_PERIOD", "OBS_VALUE"} <= set(reader.fieldnames or ()):
        raise ValueError("OECD tax data columns are unavailable.")
    rates, dates = {}, {}
    for row in reader:
        country, year = row["REF_AREA"], row["TIME_PERIOD"]
        if country and year and year.isdecimal() and year >= dates.get(country, ""):
            try:
                rate = _number(row["OBS_VALUE"]) / 100
            except (TypeError, ValueError):
                continue
            if 0 <= rate <= 1:
                rates[country], dates[country] = rate, year
    if not rates:
        raise ValueError("No statutory tax rates in the OECD response.")
    return rates, dates, OECD_INFO


def _get_usd_spread():
    doc = _document(_download(RATINGS_URL))
    for row in doc.xpath("//tr"):
        cells = [_text(cell) for cell in row.xpath("./th|./td")]
        # The large non-financial firms are in the left half of Damodaran's table.
        if len(cells) >= 4 and cells[2].replace(" ", "").upper() == "BAA2/BBB":
            return (
                {"USD": _number(cells[3]) / 100},
                {"USD": _analysis_date(_text(doc))},
                RATINGS_URL,
            )
    raise ValueError("Large non-financial Baa2/BBB spread is unavailable.")


def _get_yield(url: str):
    doc = _document(_download(url))
    heading = " ".join(_text(element) for element in doc.xpath("//h1"))
    for element in doc.xpath("//script|//style"):
        element.drop_tree()
    body = _text(doc)
    ten_year = r"\b10[\s-]*(?:y(?:r|ear)?|years?)\b"
    if re.search(r"\b\d+[\s-]*(?:y(?:r|ear)?|years?)\b", heading, re.I) and not re.search(
        ten_year, heading, re.I
    ):
        raise ValueError("The page identifies a different bond maturity.")
    if not re.search(ten_year, heading, re.I) and not re.search(
        r"\b10[\s-]*(?:y(?:r|ear)?|years?)\s+(?:government\s+)?(?:bond|note)\s+yield\b",
        body, re.I,
    ):
        raise ValueError("The page does not identify a 10-year government yield.")
    match = re.search(
        r"\bActual\s+([+−-]?\d+(?:\.\d+)?)\s*%?\s+Daily Change\b", body, re.I
    )
    if not match:
        raise ValueError("The current 10-year quote is unavailable.")
    date = re.search(rf"last updated on\s+({MONTH}\s+\d{{1,2}}(?:,|\s+of)?\s+\d{{4}})", body, re.I)
    return {"yield": _number(match.group(1)) / 100}, {"yield": date.group(1) if date else ""}, url


def _fetch_source(source: str, url: str) -> SourceData:
    from lxml.etree import LxmlError

    loaders = {"betas": _get_betas, "tax": _get_tax_rates, "spread": _get_usd_spread}
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    try:
        values, dates, actual_url = _get_yield(url) if source == "yield" else loaders[source]()
    except (OSError, ValueError, LxmlError, csv.Error):
        return SourceData({}, {}, url, fetched_at, "Unavailable")
    return SourceData(values, dates, actual_url, fetched_at)


def _country_name(country) -> str:
    display_names = {
        "BOL": "Bolivia", "GBR": "United Kingdom", "IRN": "Iran", "KOR": "South Korea",
        "MDA": "Moldova", "RUS": "Russia", "TWN": "Taiwan", "TZA": "Tanzania",
        "USA": "United States", "VEN": "Venezuela", "VNM": "Vietnam",
    }
    return display_names.get(country.alpha_3, country.name)


def _yield_url(country) -> str:
    from unicodedata import normalize

    name = normalize("NFKD", _country_name(country)).encode("ascii", "ignore").decode()
    slug = COUNTRY_SLUGS.get(country.alpha_3, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
    return f"https://tradingeconomics.com/{slug}/government-bond-yield"


def _override(st, label: str, value: float | None, key: str, *, rate: bool = True):
    unit = " (%)" if rate else ""
    if not st.checkbox(f"Override {label}", key=f"override_{key}", value=value is None):
        display = "Unavailable" if value is None else format(value, ".4%" if rate else ".4f")
        st.caption(f"{label}: {display}")
        return value
    displayed = value * (100 if rate else 1) if value is not None else None
    manual = st.number_input(
        f"{label}{unit}", value=displayed, format="%.4f", step=0.01, key=f"input_{key}"
    )
    return manual / (100 if rate else 1) if manual is not None else None


METRICS = {
    "Cost of equity": "cost_of_equity",
    "Pretax cost of debt": "pretax_cost_of_debt",
    "After-tax cost of debt": "after_tax_cost_of_debt",
    "WACC": "wacc",
}


def _comparison_rows(betas: dict[str, float], inputs: dict, currency: str) -> list[dict]:
    rows = []
    for industry, beta in sorted(betas.items()):
        result = calculate_cost_of_capital(**(inputs | {"unlevered_beta": beta}))
        row = {"Industry": industry, "Unlevered beta": beta}
        for label, attribute in METRICS.items():
            row[f"USD {label} (%)"] = getattr(result.usd, attribute) * 100
            row[f"{currency} {label} (%)"] = getattr(result.local, attribute) * 100
        rows.append(row)
    return rows


def _export_csv(rows: list[dict], scenario: dict) -> str:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(scenario) + list(rows[0]))
    writer.writeheader()
    writer.writerows(scenario | row for row in rows)
    return buffer.getvalue()


def _results_table(result: CostOfCapital, local_header: str) -> str:
    rows = []
    for label, metric in METRICS.items():
        wacc = label == "WACC"
        highlight = ""
        if wacc:
            highlight = ' style="background:color-mix(in srgb, #4b91de 16%, transparent);font-weight:bold"'
        rows.append(
            f"<tr{highlight}><th scope='row'>{label}</th>"
            f"<td style='text-align:right'>{getattr(result.usd, metric):.4%}</td>"
            f"<td style='text-align:right'>{getattr(result.local, metric):.4%}</td></tr>"
        )
    return (
        "<table style='width:100%;font-variant-numeric:tabular-nums' "
        "aria-label='Annual financing costs by currency'><thead><tr>"
        f"<th scope='col'>Annual rate</th><th scope='col' style='text-align:right'>USD</th>"
        f"<th scope='col' style='text-align:right'>{escape(local_header)}</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def main():
    import pandas as pd
    import pycountry
    import streamlit as st
    from babel.numbers import get_territory_currencies

    st.set_page_config(page_title="International Cost of Capital", layout="wide")

    fetch = st.cache_data(ttl=900, show_spinner=False)(_fetch_source)
    st.title("International Cost of Capital")
    countries_by_code = {country.alpha_3: country for country in pycountry.countries}
    country_names = {code: _country_name(country) for code, country in countries_by_code.items()}
    country_codes = sorted(country_names, key=country_names.__getitem__)
    us = countries_by_code["USA"]
    with st.spinner("Loading industry and tax data…"):
        beta_source = fetch("betas", BETAS_HTML)
        tax_source = fetch("tax", OECD_INFO)

    with st.sidebar:
        st.header("Project inputs")
        industries = sorted(beta_source.values) or [DEFAULT_INDUSTRY]
        default_industry = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
        industry = st.selectbox("Industry", industries, index=default_industry, key="industry")
        target_code = st.selectbox(
            "Target country", country_codes, index=country_codes.index("USA"),
            format_func=country_names.__getitem__, key="target_country_code",
        )
        target = countries_by_code[target_code]
        target_name = country_names[target_code]
        erp = st.number_input("Equity risk premium (%)", value=6.50, step=0.25, format="%.2f")
        debt = st.number_input(
            "Target debt share (%)", min_value=0.0, max_value=99.99, value=30.0,
            step=1.0, format="%.2f",
        )
        tax_choice = st.radio(
            "Project tax rate", ["U.S.", "Selected country"], horizontal=True,
            format_func=lambda choice: target_name if choice == "Selected country" else choice,
            disabled=target == us,
        )
        tax_code = target.alpha_3 if tax_choice == "Selected country" else "USA"
        tax_default = tax_source.values.get(tax_code)
        tax_note = st.empty()

    with st.spinner("Loading government yields and the BBB spread…"):
        spread_source = fetch("spread", RATINGS_URL)
        us_source = fetch("yield", _yield_url(us))
        local_source = us_source if target == us else fetch("yield", _yield_url(target))

    currency_codes = get_territory_currencies(target.alpha_2, tender=True)
    currency = currency_codes[0] if currency_codes else "Local"
    local_header = f"{currency} (selected)" if currency == "USD" else currency
    with st.sidebar.expander("Manual overrides"):
        beta = _override(st, "Unlevered beta", beta_source.values.get(industry), f"beta_{industry}", rate=False)
        tax = _override(st, "Corporate tax rate", tax_default, f"tax_{tax_code}")
        us_yield = _override(st, "U.S. 10-year yield", us_source.values.get("yield"), "us_yield")
        if target == us:
            local_yield = us_yield
            st.caption("The selected U.S. scenario uses the same 10-year yield.")
        else:
            local_yield = _override(st, f"{target_name} 10-year yield", local_source.values.get("yield"), f"yield_{target.alpha_3}")
        usd_spread = _override(st, "USD BBB spread", spread_source.values.get("USD"), "usd_spread")
        manual_local = st.checkbox("Override selected-country BBB spread", key=f"local_spread_{target.alpha_3}")
        local_spread = None
        if manual_local:
            local_spread_value = st.number_input(
                "Selected-country BBB spread (%)", value=None, step=0.01, format="%.4f",
                key=f"local_spread_value_{target.alpha_3}",
            )
            local_spread = local_spread_value / 100 if local_spread_value is not None else None
        spread_note = st.empty()
    if tax is not None:
        tax_note.caption(f"{tax:.2%} applied to both currencies")

    effective = {
        "unlevered_beta": beta, "tax_rate": tax, "us_yield": us_yield,
        "local_yield": local_yield, "usd_spread": usd_spread,
        "debt_share": debt / 100, "equity_risk_premium": erp / 100,
        "local_spread_override": local_spread,
    }
    labels = {
        "unlevered_beta": "unlevered beta", "tax_rate": "tax rate",
        "us_yield": "U.S. yield", "local_yield": "selected-country yield", "usd_spread": "USD BBB spread",
    }
    missing = [label for key, label in labels.items() if effective[key] is None]
    if target == us:
        missing = [label for label in missing if label != "selected-country yield"]
    if manual_local and local_spread is None:
        missing.append("selected-country BBB spread")
    result = None
    if missing:
        st.warning("Enter the following values under Manual overrides to calculate results: " + ", ".join(missing) + ".")
    else:
        try:
            result = calculate_cost_of_capital(**effective)
        except ValueError as exc:
            st.error(str(exc))

    result_title, download_slot = st.columns([3, 1])
    result_title.subheader("Currency comparison")
    if result is not None:
        if not manual_local:
            spread_note.caption(f"Automatic {currency} BBB spread: {result.local.borrowing_spread:.4%}")
        st.markdown(_results_table(result, local_header), unsafe_allow_html=True)
        rows = _comparison_rows(beta_source.values | {industry: beta}, effective, local_header)
        scenario = {
            "Selected country": target_name, "Selected currency": currency, "Selected industry": industry,
            "Tax jurisdiction": "United States" if tax_code == "USA" else target_name,
            "Project tax rate (%)": tax * 100, "Target debt share (%)": debt,
            "Equity risk premium (%)": erp, "USD 10-year yield (%)": us_yield * 100,
            "Selected-country 10-year yield (%)": local_yield * 100, "USD BBB spread (%)": usd_spread * 100,
            "Selected-country BBB spread (%)": result.local.borrowing_spread * 100,
            "Selected-country BBB spread mode": "Manual" if manual_local else "Automatic",
        }
        export_rows = sorted(rows, key=lambda row: row["Industry"] != industry)
        download_slot.download_button(
            "Download CSV", _export_csv(export_rows, scenario), "cost_of_capital.csv", "text/csv"
        )

    with st.expander("All-industry comparison"):
        if result is not None:
            if not beta_source.values:
                st.warning("The industry dataset is unavailable. Showing the selected manual beta only.")
            st.caption("All industries use the project assumptions above. A manual beta applies to the selected industry.")
            st.dataframe(pd.DataFrame(rows).round(4), hide_index=True)
        else:
            st.caption("Complete the project inputs to compare industries.")

    with st.expander("Calculation assumptions"):
        st.markdown(
            "U.S. industry betas proxy for project risk. The app relevers beta using the project "
            "tax rate and target debt-to-equity ratio, then applies CAPM in USD. Government yields "
            "serve as risk-free proxies and may include sovereign and term premiums. The ratio of "
            "gross government yields converts USD equity costs and the automatic BBB spread. "
            "Both currencies use the same project tax rate, target financing mix, and equity risk "
            "premium. Interest deductions are assumed fully usable."
        )
        if result is not None:
            st.caption(
                f"Relevered beta {result.levered_beta:.4f} · "
                f"Debt-to-equity {result.debt_to_equity:.2%} · Currency factor {result.currency_factor:.6f}"
            )

    with st.expander("Data sources and dates"):
        st.button("Refresh data", on_click=fetch.clear, help="Fetch the latest source values.")
        observations = [
            ("U.S. industry beta", beta_source, industry, False),
            ("U.S. statutory tax rate", tax_source, "USA", True),
            ("USD BBB spread", spread_source, "USD", True),
            ("U.S. 10-year yield", us_source, "yield", True),
        ]
        if target != us:
            observations.extend([
                (f"{target_name} statutory tax rate", tax_source, target.alpha_3, True),
                (f"{target_name} 10-year yield", local_source, "yield", True),
            ])
        for label, source, key, rate in observations:
            item = source.values.get(key)
            value = source.status or "Unavailable for this selection"
            if item is not None:
                value = f"{item:.4%}" if rate else f"{item:.4f}"
                value += f" · {source.dates.get(key) or 'Source date unavailable'}"
            st.markdown(f"[{label}]({source.url}) · {value} · Fetch attempted {source.fetched_at}")


if __name__ == "__main__":
    main()
