from strategies.round_1_ash import Trader as BaseTrader

PARAM_OVERRIDES = {"aggressive_take_threshold":3.0,"center_widening_ticks":3.0}

class Trader(BaseTrader):
    def __init__(self):
        super().__init__(config_overrides=PARAM_OVERRIDES)
