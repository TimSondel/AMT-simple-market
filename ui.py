"""UI do symulatora rynku (PyQt5 + pyqtgraph).

Pozwala generowac i zapisywac symulacje oraz odtwarzac je transakcja po transakcji
- formujaca sie swieca zmienia sie wielokrotnie, zanim uzbiera docelowy wolumen.
Pod wykresem ceny rysowany jest wykres skumulowanej delty, zsynchronizowany w osi X.

Uzycie:
    python ui.py
"""

import json
import os
import sys

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QApplication, QGraphicsRectItem, QHBoxLayout,
                             QInputDialog, QLabel, QListWidget, QMainWindow,
                             QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
                             QWidget)

import simulator
from chart import CandleBuilder, read_trades

CONFIG_PATH = "config.json"
SIM_DIR = os.path.join("data", "simulations")

GREEN = pg.mkBrush(0, 170, 0)
RED = pg.mkBrush(200, 0, 0)


class CandleChart:
    """Rysuje swiece na wykresie pyqtgraph, odswiezajac formujaca sie swiecze."""

    def __init__(self, plot, set_x_range=True):
        self.plot = plot
        self.set_x_range = set_x_range      # os X ustawia tylko wykres glowny
        self.items = []                     # pary (knot, korpus) dla kolejnych swiec

    def clear(self):
        for line, body in self.items:
            self.plot.removeItem(line)
            self.plot.removeItem(body)
        self.items = []

    def update(self, candles):
        """Rysuje ostatnia swiece; brakujace wczesniej swiece dokłada."""
        if not candles:
            return
        while len(self.items) < len(candles):
            line = pg.PlotDataItem()
            body = QGraphicsRectItem()
            self.plot.addItem(line)
            self.plot.addItem(body)
            self.items.append((line, body))
        candle = candles[-1]
        i = len(candles) - 1
        line, body = self.items[i]
        color = "g" if candle["close"] >= candle["open"] else "r"
        pen = pg.mkPen(color, width=1)
        line.setData([i, i], [candle["low"], candle["high"]], pen=pen)
        bottom = min(candle["open"], candle["close"])
        body.setRect(i - 0.3, bottom, 0.6, abs(candle["close"] - candle["open"]))
        body.setPen(pen)
        body.setBrush(GREEN if color == "g" else RED)
        if self.set_x_range:
            self.plot.setXRange(0, max(20, len(candles) + 2))
        low = min(c["low"] for c in candles)
        high = max(c["high"] for c in candles)
        pad = max(2.0, (high - low) * 0.1)
        self.plot.setYRange(low - pad, high + pad)


class MainWindow(QMainWindow):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.trades = []
        self.index = 0
        self.builder = None

        self.setWindowTitle("Symulator rynku")
        self.resize(1200, 800)

        # wykres ceny (70% wysokosci) i skumulowanej delty (30%), wspolna os X.
        # Dolny wykres zajmuje miejsce na wlasna os i podpis, dlatego 7:5 daje
        # w praktyce ok. 70/30 widocznego pola wykresow.
        self.graphics = pg.GraphicsLayoutWidget()
        self.plot = self.graphics.addPlot(row=0, col=0)
        self.delta_plot = self.graphics.addPlot(row=1, col=0)
        self.graphics.ci.layout.setRowStretchFactor(0, 7)
        self.graphics.ci.layout.setRowStretchFactor(1, 5)
        self.delta_plot.setXLink(self.plot)
        for plot in (self.plot, self.delta_plot):
            plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("left", "cena")
        self.delta_plot.setLabel("left", "cumulative delta")
        self.delta_plot.setLabel("bottom", "swieca (kazda po %s wolumenu)" % cfg["candle_volume"])
        self.chart = CandleChart(self.plot)
        self.delta_chart = CandleChart(self.delta_plot, set_x_range=False)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.replay)
        self.list.itemSelectionChanged.connect(self.show_info)

        self.info = QLabel("Kroki: -   |   Wolumen: -")
        self.info.setFrameShape(QLabel.StyledPanel)
        self.info.setMargin(6)

        self.steps_box = QSpinBox()
        self.steps_box.setRange(1, 10000000)
        self.steps_box.setValue(cfg["steps"])

        self.speed_box = QSpinBox()
        self.speed_box.setRange(1, 2000)
        self.speed_box.setValue(20)
        self.speed_box.setSuffix(" ms")
        self.speed_box.valueChanged.connect(self.set_speed)

        self.generate_button = QPushButton("Generuj symulacje")
        self.generate_button.clicked.connect(self.generate)
        self.replay_button = QPushButton("Odtworz")
        self.replay_button.clicked.connect(self.replay)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.toggle)
        self.refresh_button = QPushButton("Odswiez liste")
        self.refresh_button.clicked.connect(self.refresh)

        self.status = QLabel("Wybierz symulacje i kliknij Odtworz.")

        panel = QVBoxLayout()
        panel.addWidget(QLabel("Zapisane symulacje:"))
        panel.addWidget(self.list)
        panel.addWidget(self.info)
        panel.addWidget(QLabel("Kroki nowej symulacji:"))
        panel.addWidget(self.steps_box)
        panel.addWidget(self.generate_button)
        panel.addWidget(self.refresh_button)
        panel.addWidget(QLabel("Predkosc odtwarzania:"))
        panel.addWidget(self.speed_box)
        panel.addWidget(self.replay_button)
        panel.addWidget(self.stop_button)

        layout = QHBoxLayout()
        layout.addLayout(panel)
        layout.addWidget(self.graphics, 1)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.refresh()

    def simulations(self):
        """Nazwy zapisanych symulacji (katalogi z plikiem trades.csv)."""
        if not os.path.isdir(SIM_DIR):
            return []
        return sorted(name for name in os.listdir(SIM_DIR)
                      if os.path.exists(os.path.join(SIM_DIR, name, "trades.csv")))

    def refresh(self):
        current = self.list.currentItem()
        current = current.text() if current else None
        self.list.clear()
        for name in self.simulations():
            self.list.addItem(name)
        if current:
            matches = self.list.findItems(current, Qt.MatchExactly)
            if matches:
                self.list.setCurrentItem(matches[0])

    def show_info(self):
        """Pokazuje liczbe krokow i wolumen wybranej symulacji."""
        item = self.list.currentItem()
        if item is None:
            self.info.setText("Kroki: -   |   Wolumen: -")
            return
        folder = os.path.join(SIM_DIR, item.text())
        with open(os.path.join(folder, "state.json")) as f:
            state = json.load(f)
        volume = sum(size for _, size, _ in read_trades(os.path.join(folder, "trades.csv")))
        self.info.setText("Kroki: %d   |   Wolumen: %.1f" % (state["step"], volume))

    def generate(self):
        name, ok = QInputDialog.getText(self, "Nowa symulacja", "Nazwa symulacji:")
        name = name.strip()
        if not ok or not name:
            return
        folder = os.path.join(SIM_DIR, name)
        if os.path.exists(os.path.join(folder, "trades.csv")):
            answer = QMessageBox.question(self, "Nadpisac?",
                                          "Symulacja '%s' juz istnieje. Nadpisac?" % name)
            if answer != QMessageBox.Yes:
                return
        simulator.run(self.cfg, self.steps_box.value(),
                      os.path.join(folder, "trades.csv"),
                      os.path.join(folder, "state.json"))
        self.refresh()
        matches = self.list.findItems(name, Qt.MatchExactly)
        if matches:
            self.list.setCurrentItem(matches[0])
        self.status.setText("Zapisano symulacje '%s' (%d krokow)."
                            % (name, self.steps_box.value()))

    def replay(self, *args):
        item = self.list.currentItem()
        if item is None:
            QMessageBox.information(self, "Brak symulacji", "Najpierw wybierz symulacje z listy.")
            return
        self.trades = read_trades(os.path.join(SIM_DIR, item.text(), "trades.csv"))
        self.chart.clear()
        self.delta_chart.clear()
        self.builder = CandleBuilder(self.cfg["candle_volume"])
        self.index = 0
        self.set_speed(self.speed_box.value())
        self.stop_button.setText("Stop")
        self.status.setText("Odtwarzanie '%s': 0/%d transakcji." % (item.text(), len(self.trades)))
        self.timer.start()

    def toggle(self):
        """Zatrzymuje odtwarzanie (przycisk zmienia sie na Start) albo wznawia je."""
        if self.timer.isActive():
            self.timer.stop()
            self.stop_button.setText("Start")
            self.status.setText("Zatrzymano na transakcji %d/%d."
                                % (self.index, len(self.trades)))
            return
        if not self.trades or self.index >= len(self.trades):
            self.status.setText("Brak odtwarzania do wznowienia - uzyj Odtworz.")
            return
        self.set_speed(self.speed_box.value())
        self.stop_button.setText("Stop")
        self.timer.start()

    def set_speed(self, value):
        self.timer.setInterval(value)

    def tick(self):
        if self.index >= len(self.trades):
            self.timer.stop()
            self.stop_button.setText("Start")
            self.status.setText("Koniec odtwarzania (%d transakcji)." % len(self.trades))
            return
        price, size, side = self.trades[self.index]
        self.index += 1
        candle = self.builder.add(price, size, side)
        self.chart.update(self.builder.candles)
        self.delta_chart.update(self.builder.delta_candles)
        self.status.setText("Transakcja %d/%d | cena %s | swieca: %.1f/%s wolumenu | delta: %.1f"
                            % (self.index, len(self.trades), price, candle["volume"],
                               self.cfg["candle_volume"], self.builder.delta))


def main():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    app = QApplication(sys.argv)
    window = MainWindow(cfg)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
