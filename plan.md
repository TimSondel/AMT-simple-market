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

UI:
Main window with Charting library - pyqt graph.
This window must allow for saving different simulations, generating simulations and replaying them. After selecting the simulation the user will have an option to replay it(without seeing it at first). When replaying the screen should be updated with each transaction so the forming candle changes multiple times before it reaches target volume and follows the price.
With that a time per step should be added to config.

***Sprint 2***

Randomization:
We will add an oscillating function based on random walk and that will influence the parameters of the simulation. The function must have 3 main parameters: an oscillation speed determining how often the value will change, oscillation strength determining how strong the random jumps will be skewed towards the center, and spread similar to value perception spread determining how wide the random zone will be around the center.

This function will change following parameters of the simulation:
1. value perception spread
2. value perception strength
3. average mkt size and standard diviation
4. average limit size and standard diviation

In the config file we will have 3 function parameters for each of above points giving us total of 12 parameters and 4 independent random functions