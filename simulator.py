"""Prosty symulator rynku oparty na orderbooku.

Uzycie:
    python simulator.py --steps 100            # nowa symulacja
    python simulator.py --steps 100 --continue # kontynuacja zapisanej symulacji
    python simulator.py --chart                # symulacja + wykres swiec wolumenowych

Liczba krokow domyslnie pochodzi z config.json ("steps"), a czas symulowany
przypadajacy na krok z "time_per_step". Cztery niezalezne oscylatory
("oscillators" w config.json) modyfikuja w trakcie symulacji spread i sile
percepcji wartosci oraz srednie rozmiary i odchylenia zlecen.
Dane time and sales zapisywane sa w data/trades.csv, a stan symulacji
(w tym orderbook, oscylatory i otwarte pozycje) w data/state.json.
"""

import argparse
import csv
import json
import math
import os
import random
from datetime import datetime, timedelta

CONFIG_PATH = "config.json"
DATA_DIR = "data"
STATE_PATH = os.path.join(DATA_DIR, "state.json")
TRADES_PATH = os.path.join(DATA_DIR, "trades.csv")
PARAMS_NAME = "params.csv"      # parametry krok po kroku, obok pliku transakcji

INITIAL_PRICE = 1000        # poczatkowy best bid
DEPTH = 25                  # liczba poziomow po kazdej stronie ksiazki
MARKET_ORDER_PROB = 0.3     # szansa, ze krok symulacji to zlecenie rynkowe
MAGNET_SCALE = 5.0          # w ilu spreadach odleglosc od fair value tlumi magnes
CASCADE_LIMIT = 100         # zabezpieczenie przed nieskonczona kaskada
SIM_START = datetime(2024, 1, 1, 9, 0)   # poczatek czasu symulowanego


def order_size(avg, std):
    """Losowy rozmiar zlecenia (zawsze co najmniej 1)."""
    return max(1.0, round(random.gauss(avg, std), 2))


def sim_time(step_no, time_per_step):
    """Czas symulowany dla danego kroku."""
    return SIM_START + timedelta(seconds=step_no * time_per_step)


def oscillator_step(value, params):
    """Losowy krok oscylatora: skok, przyciaganie do srodka i granica strefy."""
    if random.random() < params["speed"]:
        low, high = sorted(params["step_per_jump"])    # zakres skoku
        value += random.choice([-1, 1]) * random.uniform(low, high)
        value -= params["strength"] * value
        value = max(-params["spread"], min(params["spread"], value))
    return value


def oscillator_multiplier(value):
    """Dodatnia wartosc mnozy parametr, ujemna dzieli (np. 3 -> x3, -3 -> x1/3)."""
    size = max(abs(value), 1.0)     # blisko zera oscylator nie zmienia parametru
    return size if value > 0 else 1.0 / size


def step_parameters(state, cfg):
    """Aktualizuje oscylatory i zwraca parametry symulacji na najblizszy krok."""
    values = state["oscillators"]
    for name, params in cfg["oscillators"].items():
        values[name] = oscillator_step(values.get(name, 0.0), params)

    effective = dict(cfg)
    effective["spread"] = max(1, round(cfg["spread"] * oscillator_multiplier(values["value_spread"])))
    effective["value_strength"] = min(1.0, max(0.0, cfg["value_strength"]
                                             * oscillator_multiplier(values["value_strength"])))
    for key, name in (("market_order_size", "market_size"), ("limit_order_size", "limit_size")):
        multiplier = oscillator_multiplier(values[name])
        effective[key + "_avg"] = cfg[key + "_avg"] * multiplier
        effective[key + "_std"] = cfg[key + "_std"] * multiplier

    # widocznosc magnesu: ile sily idzie w limity (0 = tylko zlecenia rynkowe)
    zone = cfg["oscillators"]["visibility"]["spread"] or 1.0
    effective["visibility_limit_share"] = min(1.0, max(0.0, 0.5 + values["visibility"] / (2.0 * zone)))
    return effective


class Orderbook:
    """Ksiazka zlecen z limitami po obu stronach i zleceniami rynkowymi."""

    def __init__(self, cfg, best_bid, bids=None, asks=None):
        self.cfg = cfg
        self.spread = cfg["spread"]
        self.skew = 0.0         # skos ksiazki od magnesu fair value
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

    def side_multiplier(self, side):
        """Strona blizej fair value jest ciensza, przeciwna grubsza."""
        # skew > 0 (cena ma rosnac): bids grubsze, asks ciensze
        return 1 - self.skew if side == "sell" else 1 + self.skew

    def limit_size(self, side):
        return order_size(self.cfg["limit_order_size_avg"],
                          self.cfg["limit_order_size_std"]) * self.side_multiplier(side)

    def fill(self):
        """Uzupelnia puste miejsca w ksiedze i usuwa poziomy poza zasiegiem."""
        for price in range(self.best_bid, self.best_bid - DEPTH, -1):
            if price not in self.bids:
                self.bids[price] = self.limit_size("buy")
        for price in list(self.bids):
            if not self.best_bid - DEPTH < price <= self.best_bid:
                del self.bids[price]
        for price in range(self.best_ask, self.best_ask + DEPTH):
            if price not in self.asks:
                self.asks[price] = self.limit_size("sell")
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


def simulation_step(ob, state, cfg, log, log_params=None):
    """Jeden krok symulacji: jedno losowe zlecenie i obsluga kaskady."""
    state["step"] += 1
    step_no = state["step"]

    # 0. oscylatory zmieniaja parametry symulacji na ten krok
    cfg = step_parameters(state, cfg)
    if cfg["spread"] != ob.spread:
        ob.spread = cfg["spread"]
        ob.fill()       # nowy spread przesuwa poziom cen w ksiedze

    # 1. percepcja wartosci zmienia sie losowo o losowy zakres tickow
    if random.random() < cfg["value_change_ratio"]:
        low, high = sorted(cfg["fair_value_spread"])
        state["fair_value"] += random.choice([-1, 1]) * random.uniform(low, high)

    # 2. magnes fair value: czesc sily idzie w zlecenia rynkowe, czesc w limity.
    # Widocznosc (cfg["visibility_limit_share"]) dzieli te sile, a skos ksiazki
    # jest druga strona magnesu - strona blizej fair value jest ciensza.
    distance = (state["fair_value"] - ob.mid) / (MAGNET_SCALE * ob.spread)
    pull = math.tanh(distance)
    limit_share = cfg["visibility_limit_share"]
    p_buy = 0.5 + 0.5 * cfg["value_strength"] * (1 - limit_share) * pull
    p_buy = min(max(p_buy, 0.01), 0.99)
    ob.skew = min(0.8, max(-0.8, cfg["value_strength"] * limit_share * pull))

    # 3. zlecenie rynkowe (z stoplossem i targetem) albo limitowe
    if random.random() < MARKET_ORDER_PROB:
        side = "buy" if random.random() < p_buy else "sell"
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
        # na losowym poziomie (wycenia go od nowa, wiec ksiazka nie puchnie).
        # O kierunku decyduje los, o grubosci strony - skos od magnesu.
        side = "buy" if random.random() < 0.5 else "sell"
        size = ob.limit_size(side)
        if side == "buy":
            ob.bids[ob.best_bid - random.randint(0, DEPTH - 1)] = size
        else:
            ob.asks[ob.best_ask + random.randint(0, DEPTH - 1)] = size

    # 4. kaskada po ruchu ceny
    cascade(ob, state["positions"], step_no, log)

    # 5. zapis parametrow, ktore ksztaltowaly ten krok
    if log_params:
        log_params(step_no, ob.mid, state["fair_value"], cfg, state["oscillators"])


def load_state(state_path=STATE_PATH):
    if not os.path.exists(state_path):
        return None
    with open(state_path) as f:
        return json.load(f)


def save_state(ob, state, state_path=STATE_PATH):
    state["best_bid"] = ob.best_bid
    state["bids"] = [[p, s] for p, s in ob.bids.items()]
    state["asks"] = [[p, s] for p, s in ob.asks.items()]
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


PARAM_COLUMNS = ["time", "step", "price", "fair_value", "spread", "value_strength",
                 "market_order_size_avg", "market_order_size_std",
                 "limit_order_size_avg", "limit_order_size_std",
                 "osc_value_spread", "osc_value_strength",
                 "osc_market_size", "osc_limit_size",
                 "osc_visibility", "visibility_limit_share"]


def params_row(step_no, price, fair_value, cfg, oscillators):
    """Wiersz z parametrami uzytymi w danym kroku symulacji."""
    return [sim_time(step_no, cfg["time_per_step"]).isoformat(timespec="seconds"), step_no, round(price, 4),
            round(fair_value, 2), cfg["spread"], round(cfg["value_strength"], 4),
            round(cfg["market_order_size_avg"], 4), round(cfg["market_order_size_std"], 4),
            round(cfg["limit_order_size_avg"], 4), round(cfg["limit_order_size_std"], 4),
            round(oscillators["value_spread"], 4), round(oscillators["value_strength"], 4),
            round(oscillators["market_size"], 4), round(oscillators["limit_size"], 4),
            round(oscillators["visibility"], 4), round(cfg["visibility_limit_share"], 4)]


def run(cfg, steps, trades_path=TRADES_PATH, state_path=STATE_PATH, cont=False):
    """Uruchamia symulacje i zapisuje transakcje, parametry oraz stan."""
    os.makedirs(os.path.dirname(trades_path) or ".", exist_ok=True)
    params_path = os.path.join(os.path.dirname(trades_path) or ".", PARAMS_NAME)
    state = load_state(state_path) if cont else None
    if state is None:
        state = {"step": 0, "best_bid": INITIAL_PRICE, "fair_value": INITIAL_PRICE,
                 "bids": [], "asks": [], "positions": []}
        ob = Orderbook(cfg, state["best_bid"])
        trades_file = open(trades_path, "w", newline="")
        params_file = open(params_path, "w", newline="")
        fresh = True
    else:
        ob = Orderbook(cfg, state["best_bid"],
                       {int(p): s for p, s in state["bids"]},
                       {int(p): s for p, s in state["asks"]})
        trades_file = open(trades_path, "a", newline="")
        params_file = open(params_path, "a", newline="")
        fresh = False
    state.setdefault("oscillators", {name: 0.0 for name in cfg["oscillators"]})

    writer = csv.writer(trades_file)
    params_writer = csv.writer(params_file)
    if fresh:
        writer.writerow(["time", "step", "price", "size", "side", "kind"])
        params_writer.writerow(PARAM_COLUMNS)

    def log(step_no, price, size, side, kind):
        writer.writerow([sim_time(step_no, cfg["time_per_step"]).isoformat(timespec="seconds"),
                         step_no, price, size, side, kind])

    def log_params(step_no, price, fair_value, cfg, oscillators):
        params_writer.writerow(params_row(step_no, price, fair_value, cfg, oscillators))

    try:
        for _ in range(steps):
            simulation_step(ob, state, cfg, log, log_params)
    finally:
        trades_file.close()
        params_file.close()
        save_state(ob, state, state_path)
    return state, ob


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

    try:
        state, ob = run(cfg, steps, cont=args.cont)
    except KeyboardInterrupt:
        print("Przerwano przez uzytkownika.")
        state, ob = load_state(), None

    if ob is not None:
        print("Krok: %d | mid: %s | fair value: %s | otwarte pozycje: %d"
              % (state["step"], ob.mid, state["fair_value"], len(state["positions"])))
    print("Zapisano: %s, %s" % (TRADES_PATH, STATE_PATH))

    if args.chart:
        from chart import VolumeChart
        print("Zapisano wykres: %s" % VolumeChart(cfg["candle_volume"]).draw())


if __name__ == "__main__":
    main()
