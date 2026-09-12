"""Prosty symulator rynku oparty na orderbooku.

Uzycie:
    python simulator.py --steps 100            # nowa symulacja
    python simulator.py --steps 100 --continue # kontynuacja zapisanej symulacji
    python simulator.py --chart                # symulacja + wykres swiec wolumenowych

Liczba krokow domyslnie pochodzi z config.json ("steps").
Dane time and sales zapisywane sa w data/trades.csv, a stan symulacji
(w tym orderbook i otwarte pozycje) w data/state.json.
"""

import argparse
import csv
import json
import math
import os
import random
from datetime import datetime

CONFIG_PATH = "config.json"
DATA_DIR = "data"
STATE_PATH = os.path.join(DATA_DIR, "state.json")
TRADES_PATH = os.path.join(DATA_DIR, "trades.csv")

INITIAL_PRICE = 1000        # poczatkowy best bid
DEPTH = 10                  # liczba poziomow po kazdej stronie ksiazki
MARKET_ORDER_PROB = 0.3     # szansa, ze krok symulacji to zlecenie rynkowe
MAGNET_SCALE = 5.0          # w ilu spreadach odleglosc od fair value tlumi magnes
CASCADE_LIMIT = 100         # zabezpieczenie przed nieskonczona kaskada


def order_size(avg, std):
    """Losowy rozmiar zlecenia (zawsze co najmniej 1)."""
    return max(1.0, round(random.gauss(avg, std), 2))


class Orderbook:
    """Ksiazka zlecen z limitami po obu stronach i zleceniami rynkowymi."""

    def __init__(self, cfg, best_bid, bids=None, asks=None):
        self.cfg = cfg
        self.spread = cfg["spread"]
        self.best_bid = best_bid
        self.bids = dict(bids) if bids else {}
        self.asks = dict(asks) if asks else {}
        self.fill()

    @property
    def best_ask(self):
        return self.best_bid + self.spread

    @property
    def mid(self):
        return (self.best_bid + self.best_ask) / 2

    def limit_size(self):
        return order_size(self.cfg["limit_order_size_avg"], self.cfg["limit_order_size_std"])

    def fill(self):
        """Uzupelnia puste miejsca w ksiedze i usuwa poziomy poza zasiegiem."""
        for price in range(self.best_bid, self.best_bid - DEPTH, -1):
            if price not in self.bids:
                self.bids[price] = self.limit_size()
        for price in list(self.bids):
            if not self.best_bid - DEPTH < price <= self.best_bid:
                del self.bids[price]
        for price in range(self.best_ask, self.best_ask + DEPTH):
            if price not in self.asks:
                self.asks[price] = self.limit_size()
        for price in list(self.asks):
            if not self.best_ask <= price < self.best_ask + DEPTH:
                del self.asks[price]

    def market_order(self, side, size):
        """Realizuje zlecenie rynkowe, zwraca liste wypelnien (price, size)."""
        fills = []
        left = size
        while left > 0:
            if side == "buy":
                price, book = self.best_ask, self.asks
            else:
                price, book = self.best_bid, self.bids
            available = book.get(price, 0)
            if available <= 0:          # puste miejsce - uzupelnij ksiazke
                self.fill()
                available = book.get(price, 0)
                if available <= 0:
                    break
            taken = min(available, left)
            book[price] = available - taken
            left -= taken
            fills.append((price, taken))
            if book[price] <= 0:        # poziom wyczerpany - cena sie przesuwa
                del book[price]
                if side == "buy":
                    self.best_bid = price - self.spread + 1
                else:
                    self.best_bid = price - 1
                self.fill()
        return fills


def triggered(ob, position):
    """Zwraca 'stop' albo 'target', jesli cena osiagnela poziom pozycji."""
    price = ob.mid
    if position["side"] == "buy":
        if price <= position["stop"]:
            return "stop"
        if price >= position["target"]:
            return "target"
    else:
        if price >= position["stop"]:
            return "stop"
        if price <= position["target"]:
            return "target"
    return None


def cascade(ob, positions, step_no, log):
    """Kaskada: aktywuje stoplossy, usuwa pozycje po osiagnieciu targetu."""
    for _ in range(CASCADE_LIMIT):
        for position in list(positions):
            kind = triggered(ob, position)
            if kind is None:
                continue
            positions.remove(position)
            if kind == "target":
                continue
            # stoploss zamyka pozycje zleceniem rynkowym w przeciwna strone
            side = "sell" if position["side"] == "buy" else "buy"
            for price, size in ob.market_order(side, position["size"]):
                log(step_no, price, size, side, "stop")
            break       # cena mogla sie ruszyc - sprawdzamy ksiazke od nowa
        else:
            return


def simulation_step(ob, state, cfg, log):
    """Jeden krok symulacji: jedno losowe zlecenie i obsluga kaskady."""
    state["step"] += 1
    step_no = state["step"]

    # 1. percepcja wartosci zmienia sie losowo
    if random.random() < cfg["value_change_ratio"]:
        state["fair_value"] += random.choice([-1, 1]) * ob.spread

    # 2. kierunek zlecenia - fair value dziala jak magnes dla ceny
    distance = (state["fair_value"] - ob.mid) / (MAGNET_SCALE * ob.spread)
    p_buy = 0.5 + 0.5 * cfg["value_strength"] * math.tanh(distance)
    p_buy = min(max(p_buy, 0.01), 0.99)
    side = "buy" if random.random() < p_buy else "sell"

    # 3. zlecenie rynkowe (z stoplossem i targetem) albo limitowe
    if random.random() < MARKET_ORDER_PROB:
        size = order_size(cfg["market_order_size_avg"], cfg["market_order_size_std"])
        fills = ob.market_order(side, size)
        if fills:
            for price, filled in fills:
                log(step_no, price, filled, side, "market")
            entry = sum(p * s for p, s in fills) / sum(s for _, s in fills)
            stop = order_size(cfg["stop_loss_avg"], cfg["stop_loss_std"])
            target = order_size(cfg["target_avg"], cfg["target_std"])
            if side == "buy":
                state["positions"].append({"side": side, "size": size, "entry": entry,
                                           "stop": entry - stop, "target": entry + target})
            else:
                state["positions"].append({"side": side, "size": size, "entry": entry,
                                           "stop": entry + stop, "target": entry - target})
    else:
        # zlecenie limitowe nie ma stoplossow ani targetow - odswieza plynnosc
        # na losowym poziomie (wycenia go od nowa, wiec ksiazka nie puchnie)
        size = order_size(cfg["limit_order_size_avg"], cfg["limit_order_size_std"])
        if side == "buy":
            ob.bids[ob.best_bid - random.randint(0, DEPTH - 1)] = size
        else:
            ob.asks[ob.best_ask + random.randint(0, DEPTH - 1)] = size

    # 4. kaskada po ruchu ceny
    cascade(ob, state["positions"], step_no, log)


def load_state():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH) as f:
        return json.load(f)


def save_state(ob, state):
    state["best_bid"] = ob.best_bid
    state["bids"] = [[p, s] for p, s in ob.bids.items()]
    state["asks"] = [[p, s] for p, s in ob.asks.items()]
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Prosty symulator rynku")
    parser.add_argument("--steps", type=int, default=None,
                        help="liczba krokow symulacji (domyslnie z config.json)")
    parser.add_argument("--continue", dest="cont", action="store_true",
                        help="kontynuuj symulacje z data/state.json")
    parser.add_argument("--chart", action="store_true",
                        help="narysuj wykres swiec wolumenowych po symulacji")
    args = parser.parse_args()

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    steps = args.steps if args.steps is not None else cfg["steps"]
    os.makedirs(DATA_DIR, exist_ok=True)

    state = load_state() if args.cont else None
    if state is None:
        state = {"step": 0, "best_bid": INITIAL_PRICE, "fair_value": INITIAL_PRICE,
                 "bids": [], "asks": [], "positions": []}
        ob = Orderbook(cfg, state["best_bid"])
        trades_file = open(TRADES_PATH, "w", newline="")
        fresh = True
    else:
        ob = Orderbook(cfg, state["best_bid"],
                       {int(p): s for p, s in state["bids"]},
                       {int(p): s for p, s in state["asks"]})
        trades_file = open(TRADES_PATH, "a", newline="")
        fresh = False

    writer = csv.writer(trades_file)
    if fresh:
        writer.writerow(["time", "step", "price", "size", "side", "kind"])

    def log(step_no, price, size, side, kind):
        writer.writerow([datetime.now().isoformat(timespec="seconds"),
                         step_no, price, size, side, kind])

    try:
        for _ in range(steps):
            simulation_step(ob, state, cfg, log)
    except KeyboardInterrupt:
        print("Przerwano przez uzytkownika.")
    finally:
        trades_file.close()
        save_state(ob, state)

    print("Krok: %d | mid: %s | fair value: %s | otwarte pozycje: %d"
          % (state["step"], ob.mid, state["fair_value"], len(state["positions"])))
    print("Zapisano: %s, %s" % (TRADES_PATH, STATE_PATH))

    if args.chart:
        from chart import VolumeChart
        print("Zapisano wykres: %s" % VolumeChart(cfg["candle_volume"]).draw())


if __name__ == "__main__":
    main()
