"""Static watchlists. This is not the full US market; free data cannot scan every listing."""

from __future__ import annotations

BIOTECH = [
    ("SAVA", "Cassava Sciences"),
    ("VKTX", "Viking Therapeutics"),
    ("IOVA", "Iovance Biotherapeutics"),
    ("CRSP", "CRISPR Therapeutics"),
    ("NTLA", "Intellia Therapeutics"),
    ("EDIT", "Editas Medicine"),
    ("BEAM", "Beam Therapeutics"),
    ("RXRX", "Recursion Pharmaceuticals"),
    ("TGTX", "TG Therapeutics"),
    ("CYTK", "Cytokinetics"),
    ("ARWR", "Arrowhead Pharmaceuticals"),
    ("RCKT", "Rocket Pharmaceuticals"),
    ("NVAX", "Novavax"),
    ("IMVT", "Immunovant"),
    ("RVMD", "Revolution Medicines"),
    ("MRNA", "Moderna"),
    ("BMRN", "BioMarin"),
    ("INCY", "Incyte"),
    ("EXEL", "Exelixis"),
    ("IONS", "Ionis"),
    ("GOSS", "Gossamer Bio"),
    ("LYEL", "Lyell Immunopharma"),
    ("CGEM", "Cullinan Therapeutics"),
    ("ANAB", "AnaptysBio"),
]

LARGE = [
    ("AAPL", "Apple"),
    ("MSFT", "Microsoft"),
    ("NVDA", "NVIDIA"),
    ("TSLA", "Tesla"),
    ("AMZN", "Amazon"),
    ("META", "Meta"),
    ("GOOGL", "Alphabet"),
    ("AMD", "AMD"),
]

ETFS = [
    ("SPY", "S&P 500"),
    ("QQQ", "Nasdaq 100"),
    ("IWM", "Russell 2000"),
    ("DIA", "Dow"),
    ("TQQQ", "ProShares UltraPro QQQ"),
    ("GLD", "SPDR Gold"),
    ("XBI", "SPDR Biotech"),
    ("IBB", "iShares Biotech"),
    ("XLF", "Financials"),
    ("XLK", "Technology"),
    ("XLE", "Energy"),
    ("XLV", "Health care"),
]

BREADTH = ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLK", "XLE", "XBI", "XLV", "IBB"]


def _rows(pairs: list[tuple[str, str]], sector: str) -> list[dict]:
    return [{"ticker": ticker, "name": name, "sector": sector} for ticker, name in pairs]


UNIVERSES: dict[str, list[dict]] = {
    "us_smallcap": _rows(BIOTECH, "Biotech"),
    "us_smallcap_biotech": _rows(BIOTECH, "Biotech"),
    "us_largecap": _rows(LARGE, "Large cap"),
    "etf": _rows(ETFS, "ETF"),
}


def universe(name: str) -> list[dict]:
    try:
        return UNIVERSES[name]
    except KeyError as exc:
        known = ", ".join(sorted(UNIVERSES))
        raise KeyError(f"Unknown universe {name}. Known: {known}") from exc


def tickers(name: str) -> list[str]:
    return [row["ticker"] for row in universe(name)]


def meta_map(name: str) -> dict[str, dict]:
    return {row["ticker"]: row for row in universe(name)}
