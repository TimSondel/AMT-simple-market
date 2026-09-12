"""Wykres swiec wolumenowych z danych time and sales.

Kazda swieca zbiera stala ilosc wolumenu podana w config.json ("candle_volume").
"""

import csv
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

TRADES_PATH = os.path.join("data", "trades.csv")
CHART_PATH = os.path.join("data", "chart.png")


class VolumeChart:
    """Rysuje swiece wolumenowe na podstawie zapisanych transakcji."""

    def __init__(self, candle_volume, trades_path=TRADES_PATH):
        self.candle_volume = candle_volume
        self.trades_path = trades_path

    def candles(self):
        """Grupuje transakcje w swiece, z ktorych kazda ma stala ilosc wolumenu."""
        candles = []
        volume = 0
        with open(self.trades_path) as f:
            for row in csv.DictReader(f):
                price = int(row["price"])
                size = float(row["size"])
                if volume == 0:
                    candles.append({"open": price, "high": price, "low": price,
                                    "close": price, "volume": 0.0})
                candle = candles[-1]
                candle["high"] = max(candle["high"], price)
                candle["low"] = min(candle["low"], price)
                candle["close"] = price
                candle["volume"] += size
                volume += size
                if volume >= self.candle_volume:
                    volume = 0
        return candles

    def draw(self, output_path=CHART_PATH):
        """Rysuje swiece i zapisuje wykres do pliku."""
        candles = self.candles()
        if not candles:
            raise ValueError("brak transakcji w %s" % self.trades_path)
        fig, ax = plt.subplots(figsize=(12, 6))
        for i, candle in enumerate(candles):
            color = "green" if candle["close"] >= candle["open"] else "red"
            ax.vlines(i, candle["low"], candle["high"], color=color)
            bottom = min(candle["open"], candle["close"])
            ax.add_patch(Rectangle((i - 0.3, bottom), 0.6,
                                   abs(candle["close"] - candle["open"]), color=color))
        ax.set_xlabel("swieca (kazda po %s wolumenu)" % self.candle_volume)
        ax.set_ylabel("cena")
        ax.set_title("Swiece wolumenowe")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(output_path)
        plt.close(fig)
        return output_path


if __name__ == "__main__":
    with open("config.json") as f:
        cfg = json.load(f)
    print("Zapisano wykres: %s" % VolumeChart(cfg["candle_volume"]).draw())
