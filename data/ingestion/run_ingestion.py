import argparse
from datetime import date, timedelta

from config.settings import settings
from data.ingestion.yfinance_client import YFinanceClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull raw price/fundamentals data into data/raw")
    parser.add_argument("--tickers", nargs="+", required=True)
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    client = YFinanceClient()
    end = date.today()
    start = end - timedelta(days=args.lookback_days)

    prices = client.get_prices(args.tickers, start, end)
    fundamentals = client.get_fundamentals(args.tickers)

    prices.to_parquet(settings.raw_data_dir / "prices.parquet")
    fundamentals.to_parquet(settings.raw_data_dir / "fundamentals.parquet")


if __name__ == "__main__":
    main()
