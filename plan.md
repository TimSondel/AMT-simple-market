Market simulator based on orderbook, random order generation and fair value perception. Simulator will have few simplificaitons to make its fnction easier.

Orderbook: Allows for placing market with stoplosses and targets. If market order is placed and price moves the orderbook must act on cascade - activating stoplosses and deleting orders after hitting targets. For simplification - limit orders will not have targets or stoplosses. After market order is made and limit orders are taken out, resulting in a move in price, the orderbook will refill the empty spots in the book.

Simuation: will use orderbook to simulate the orderflow. in one simulation step one order with random direction and size will be placed. the orderbook will serve it and after all actions on cascade the next step can be performed.

Fair value perception: randomly set level that works like a magnet for the price

Storage: Stores time and sales data from simulations in csv files. Simulation should have an option to be continued 

Config file: allow to modify:
1. avg market order size and standard deviation
2. avg limit order size and standard deviation
3. avg market order stoploss and target and their standard deviation
4. value perception change ratio, spread and strength


***Sprint 1***


