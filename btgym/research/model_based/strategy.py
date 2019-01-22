import backtrader.indicators as btind

from gym import spaces
from btgym import DictSpace

import numpy as np

from btgym.research.strategy_gen_6.base import BaseStrategy6
from btgym.research.model_based.model.bivariate import BivariatePriceModel


class MonoSpreadOUStrategy_0(BaseStrategy6):
    """
    Expects spread as single generated data stream.
    """
    # Time embedding period:
    time_dim = 128  # NOTE: changed this --> change Policy  UNREAL for aux. pix control task upsampling params

    # Number of timesteps reward estimation statistics are averaged over, should be:
    # skip_frame_period <= avg_period <= time_embedding_period:
    avg_period = 64

    # Possible agent actions;  Note: place 'hold' first! :
    portfolio_actions = ('hold', 'buy', 'sell', 'close')

    features_parameters = (1, 4, 16, 64, 256, 1024)
    num_features = len(features_parameters)

    params = dict(
        state_shape={
            'external': spaces.Box(low=-10, high=10, shape=(time_dim, 1, num_features*2), dtype=np.float32),
            'internal': spaces.Box(low=-100, high=100, shape=(avg_period, 1, 5), dtype=np.float32),
            'expert': spaces.Box(low=0, high=10, shape=(len(portfolio_actions),), dtype=np.float32),
            'stat': spaces.Box(low=-100, high=100, shape=(3, 1), dtype=np.float32),
            'metadata': DictSpace(
                {
                    'type': spaces.Box(
                        shape=(),
                        low=0,
                        high=1,
                        dtype=np.uint32
                    ),
                    'trial_num': spaces.Box(
                        shape=(),
                        low=0,
                        high=10**10,
                        dtype=np.uint32
                    ),
                    'trial_type': spaces.Box(
                        shape=(),
                        low=0,
                        high=1,
                        dtype=np.uint32
                    ),
                    'sample_num': spaces.Box(
                        shape=(),
                        low=0,
                        high=10**10,
                        dtype=np.uint32
                    ),
                    'first_row': spaces.Box(
                        shape=(),
                        low=0,
                        high=10**10,
                        dtype=np.uint32
                    ),
                    'timestamp': spaces.Box(
                        shape=(),
                        low=0,
                        high=np.finfo(np.float64).max,
                        dtype=np.float64
                    ),
                    # TODO: make generator parameters names standard
                    'generator': DictSpace(
                        {
                            'mu': spaces.Box(
                                shape=(),
                                low=np.finfo(np.float64).min,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            ),
                            'l': spaces.Box(
                                shape=(),
                                low=0,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            ),
                            'sigma': spaces.Box(
                                shape=(),
                                low=0,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            ),
                            'x0': spaces.Box(
                                shape=(),
                                low=np.finfo(np.float64).min,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            )
                        }
                    )
                }
            )
        },
        cash_name='default_cash',
        asset_names=['default_asset'],
        start_cash=None,
        commission=None,
        slippage=None,
        leverage=1.0,
        gamma=0.99,             # fi_gamma, should match MDP gamma decay
        reward_scale=1,         # reward multiplicator
        norm_alpha=0.001,       # renormalisation tracking decay in []0, 1]
        drawdown_call=10,       # finish episode when hitting drawdown treshghold, in percent.
        dataset_stat=None,      # Summary descriptive statistics for entire dataset and
        episode_stat=None,      # current episode. Got updated by server.
        time_dim=time_dim,      # time embedding period
        avg_period=avg_period,  # number of time steps reward estimation statistics are averaged over
        features_parameters=features_parameters,
        num_features=num_features,
        metadata={},
        broadcast_message={},
        trial_stat=None,
        trial_metadata=None,
        portfolio_actions=portfolio_actions,
        skip_frame=1,       # number of environment steps to skip before returning next environment response
        order_size=None,
        initial_action=None,
        initial_portfolio_action=None,
        state_int_scale=1,
        state_ext_scale=1,
    )

    def __init__(self, **kwargs):
        super(MonoSpreadOUStrategy_0, self).__init__(**kwargs)
        self.data.high = self.data.low = self.data.close = self.data.open
        self.current_expert_action = np.zeros(len(self.p.portfolio_actions))
        self.state['metadata'] = self.metadata

        # Combined dataset related, infer OU generator params:
        generator_keys = self.p.state_shape['metadata'].spaces['generator'].spaces.keys()
        if 'generator' not in self.p.metadata.keys() or self.p.metadata['generator'] == {}:
            self.metadata['generator'] = {key: np.asarray(0) for key in generator_keys}

        else:
            # self.metadata['generator'] = {key: self.p.metadata['generator'][key] for key in generator_keys}

            # TODO: clean up this mess, refine names:

            self.metadata['generator'] = {
                'l': self.p.metadata['generator']['ou_lambda'],
                'mu': self.p.metadata['generator']['ou_mu'],
                'sigma': self.p.metadata['generator']['ou_sigma'],
                'x0': 0,
            }

            # Make scalars np arrays to comply gym.spaces.Box specs:
            for k, v in self.metadata['generator'].items():
                self.metadata['generator'][k] = np.asarray(v)

        self.last_delta_total_pnl = 0
        self.last_pnl = 0

        self.log.debug('startegy got broadcast_msg: <<{}>>'.format(self.p.broadcast_message))

    def get_broadcast_message(self):
        return {
            'data_model_psi': np.zeros([2, 3]),
            'iteration': self.iteration
        }

    def set_datalines(self):
        self.data.high = self.data.low = self.data.close = self.data.open

        self.data.std = btind.StdDev(self.data.open, period=self.p.time_dim, safepow=True)
        self.data.std.plotinfo.plot = False

        self.data.features = [
            btind.SimpleMovingAverage(self.data.open, period=period) for period in self.p.features_parameters
        ]
        initial_time_period = np.asarray(self.p.features_parameters).max() + self.p.time_dim
        self.data.dim_sma = btind.SimpleMovingAverage(
            self.datas[0],
            period=initial_time_period
        )
        self.data.dim_sma.plotinfo.plot = False

    def get_external_state(self):
        x_sma = np.stack(
            [
                feature.get(size=self.p.time_dim) for feature in self.data.features
            ],
            axis=-1
        )
        scale = 1 / np.clip(self.data.std[0], 1e-10, None)
        x_sma *= scale  # <-- more or less ok

        # Gradient along features axis:
        dx = np.gradient(x_sma, axis=-1)

        # Add up: gradient  along time axis:
        dx2 = np.gradient(dx, axis=0)

        # TODO: different conv. encoders for these two types of features:
        x = np.concatenate([dx, dx2], axis=-1)

        # Crop outliers:
        x = np.clip(x, -10, 10)
        return x[:, None, :]

    # def get_internal_state(self):
    #
    #     x_broker = np.concatenate(
    #         [
    #             np.asarray(self.broker_stat['value'])[..., None],
    #             np.asarray(self.broker_stat['unrealized_pnl'])[..., None],
    #             np.asarray(self.broker_stat['total_unrealized_pnl'])[..., None],
    #             np.asarray(self.broker_stat['realized_pnl'])[..., None],
    #             np.asarray(self.broker_stat['cash'])[..., None],
    #             np.asarray(self.broker_stat['exposure'])[..., None],
    #         ],
    #         axis=-1
    #     )
    #     x_broker = tanh(np.gradient(x_broker, axis=-1) * self.p.state_int_scale)
    #     return x_broker[:, None, :]

    def get_expert_state(self):
        """
        Not used.
        """
        return np.zeros(len(self.p.portfolio_actions))

    def get_reward(self):
        """
        Shapes reward function as normalized single trade realized profit/loss,
        augmented with potential-based reward shaping functions in form of:
        F(s, a, s`) = gamma * FI(s`) - FI(s);
        Potential FI_1 is current normalized unrealized profit/loss.

        Paper:
            "Policy invariance under reward transformations:
             Theory and application to reward shaping" by A. Ng et al., 1999;
             http://www.robotics.stanford.edu/~ang/papers/shaping-icml99.pdf
        """

        # All sliding statistics for this step are already updated by get_state().

        # Potential-based shaping function 1:
        # based on potential of averaged profit/loss for current opened trade (unrealized p/l):
        unrealised_pnl = np.asarray(self.broker_stat['unrealized_pnl'])
        current_pos_duration = self.broker_stat['pos_duration'][-1]

        # We want to estimate potential `fi = gamma*fi_prime - fi` of current opened position,
        # thus need to consider different cases given skip_fame parameter:
        if current_pos_duration == 0:
            # Set potential term to zero if there is no opened positions:
            fi_1 = 0
            fi_1_prime = 0
        else:
            current_avg_period = min(self.avg_period, current_pos_duration)

            fi_1 = self.last_pnl
            fi_1_prime = np.average(unrealised_pnl[- current_avg_period:])

        # Potential term 1:
        f1 = self.p.gamma * fi_1_prime - fi_1
        self.last_pnl = fi_1_prime

        # Potential-based shaping function 2:
        # based on potential of averaged profit/loss for global unrealized pnl:
        total_pnl = np.asarray(self.broker_stat['total_unrealized_pnl'])
        delta_total_pnl = np.average(total_pnl[-self.p.skip_frame:]) - np.average(total_pnl[:-self.p.skip_frame])

        fi_2 = delta_total_pnl
        fi_2_prime = self.last_delta_total_pnl

        # Potential term 2:
        f2 = self.p.gamma * fi_2_prime - fi_2
        self.last_delta_total_pnl = delta_total_pnl

        # Main reward function: normalized realized profit/loss:
        realized_pnl = np.asarray(self.broker_stat['realized_pnl'])[-self.p.skip_frame:].sum()

        # Weights are subject to tune:
        self.reward = (1.0 * f1 + 0 * f2 + 1.0 * realized_pnl) * self.p.reward_scale
        # self.reward = np.clip(self.reward, -self.p.reward_scale, self.p.reward_scale)
        self.reward = np.clip(self.reward, -1e3, 1e3)

        return self.reward


from pykalman import KalmanFilter


class PairSpreadStrategy_0(MonoSpreadOUStrategy_0):
    """
    Expects pair of data streams. Forms spread as only virtual trading asset.
    """
    def __init__(self, **kwargs):
        super(PairSpreadStrategy_0, self).__init__(**kwargs)

        assert len(self.p.asset_names) == 1, 'Only one derivative spread asset is supported'
        if isinstance(self.p.asset_names, str):
            self.p.asset_names = [self.p.asset_names]
        self.action_key = list(self.p.asset_names)[0]

        self.last_action = None

        assert len(self.getdatanames()) == 2, \
            'Expected exactly two input datalines but {} where given'.format(self.getdatanames())

        # Keeps track of virtual spread position
        # long_ spread: >0, short_spread: <0, no positions: 0
        self.spread_position_size = 0

        # Reserve 5% of initial cash when checking if it is possible to add up virtual spread:
        self.margin_reserve = self.env.broker.get_cash() * .05

        # Reward signal filtering:
        self.kf = KalmanFilter(
            initial_state_mean=0,
            transition_covariance=.01,
            observation_covariance=1,
            n_dim_obs=1
        )
        self.kf_state = [0, 0]

    def set_datalines(self):

        self.data.spread = btind.SimpleMovingAverage(self.datas[0] - self.datas[1], period=1)
        self.data.spread.plotinfo.subplot = True
        self.data.spread.plotinfo.plotabove = True
        self.data.spread.plotinfo.plotname = list(self.p.asset_names)[0]

        # Override stat line:
        # self.stat_asset = btind.SimpleMovingAverage((self.datas[0] + self.datas[1]) / 2, period=1)
        # self.stat_asset.plotinfo.plot = False

        self.stat_asset = self.data.spread

        self.data.std = btind.StdDev(self.data.spread, period=self.p.time_dim, safepow=True)
        self.data.std.plotinfo.plot = False

        self.data.features = [
            # btind.SimpleMovingAverage(self.data.spread, period=period) for period in self.p.features_parameters
            btind.EMA(self.data.spread, period=period) for period in self.p.features_parameters
        ]
        initial_time_period = np.asarray(self.p.features_parameters).max() + self.p.time_dim
        self.data.dim_sma = btind.SimpleMovingAverage(
            self.datas[0],
            period=initial_time_period
        )
        self.data.dim_sma.plotinfo.plot = False

    def get_stat_state(self):
        return np.concatenate(
            [np.asarray(self.norm_estimator.get_state()), np.asarray(self.stat_asset.get())[None, :]],
            axis=0
        )

    def get_external_state(self):
        """
        Attempt to include avg decomp. of original normalised spread
        """
        x_sma = np.stack(
            [
                feature.get(size=self.p.time_dim) for feature in self.data.features
            ],
            axis=-1
        )
        scale = 1 / np.clip(self.data.std[0], 1e-10, None)
        x_sma *= scale  # <-- more or less ok

        # Gradient along features axis:
        dx = np.gradient(x_sma, axis=-1)

        # # Add up: gradient  along time axis:
        # dx2 = np.gradient(dx, axis=0)

        # TODO: different conv. encoders for these two types of features:
        x = np.concatenate([x_sma, dx], axis=-1)

        # Crop outliers:
        x = np.clip(x, -10, 10)
        return x[:, None, :]

    def long_spread(self):
        """
        Opens long spread `virtual position`,
        sized 2x minimum single stake_size
        """
        if self.spread_position_size >= 0:
            if not self.can_add_up():
                self.order_failed += 1
                # self.log.warning(
                #     'Adding Long spread to existing {} hit margin, ignored'.format(self.spread_position_size)
                # )
                return

        name1 = self.datas[0]._name
        name2 = self.datas[1]._name

        self.order = self.buy(data=name1, size=self.p.order_size[name1])
        self.order = self.sell(data=name2, size=self.p.order_size[name2])
        self.spread_position_size += 1
        # self.log.warning('long spread submitted, new pos. size: {}'.format(self.spread_position_size))

    def short_spread(self):
        if self.spread_position_size <= 0:
            if not self.can_add_up():
                self.order_failed += 1
                # self.log.warning(
                #     'Adding Short spread to existing {} hit margin, ignored'.format(self.spread_position_size)
                # )
                return

        name1 = self.datas[0]._name
        name2 = self.datas[1]._name

        self.order = self.sell(data=name1, size=self.p.order_size[name1])
        self.order = self.buy(data=name2, size=self.p.order_size[name2])
        self.spread_position_size -= 1
        # self.log.warning('short spread submitted, new pos. size: {}'.format(self.spread_position_size))

    def close_spread(self):
        self.order = self.close(data=self.datas[0]._name)
        self.order = self.close(data=self.datas[1]._name)
        self.spread_position_size = 0
        # self.log.warning('close spread submitted, new pos. size: {}'.format(self.spread_position_size))

    def can_add_up(self):
        """
        Checks if there enough cash left to open synthetic spread position

        Returns:
            True if possible, False otherwise
        """
        # Get full operation cost:
        # TODO: it can be two commissions schemes
        op_cost = [
            self.env.broker.comminfo[None].getoperationcost(
                size=self.p.order_size[name],
                price=self.getdatabyname(name).high[0]
            ) +
            self.env.broker.comminfo[None].getcommission(
                size=self.p.order_size[name],
                price=self.getdatabyname(name).high[0]
            )
            for name in [self.datas[0]._name, self.datas[1]._name]
        ]
        # self.log.warning('op_cost+comm+reserve: {}'.format(np.asarray(op_cost).sum() + self.margin_reserve))
        # self.log.warning('current_cash: {}'.format(self.env.broker.get_cash()))
        if np.asarray(op_cost).sum() + self.margin_reserve >= self.env.broker.get_cash():
            # self.log.warning('add_up check failed')
            return False

        else:
            # self.log.warning('add_up check ok')
            return True

    def get_broker_pos_duration(self, **kwargs):
        """
        Position duration is measured w.r.t. virtual spread position, not broker account exposure
        """
        if self.spread_position_size == 0:
            self.current_pos_duration = 0
            # self.log.warning('zero position')

        else:
            self.current_pos_duration += 1
            # self.log.warning('position duration: {}'.format(self.current_pos_duration))

        return self.current_pos_duration

    def notify_order(self, order):
        """
        Shamelessly taken from backtrader tutorial.
        TODO: better multi data support
        """
        if order.status in [order.Submitted, order.Accepted]:
            # Buy/Sell order submitted/accepted to/by broker - Nothing to do
            return
        # Check if an order has been completed
        # Attention: broker could reject order if not enough cash
        if order.status in [order.Completed]:
            if order.isbuy():
                self.broker_message = 'BUY executed,\nPrice: {:.5f}, Cost: {:.4f}, Comm: {:.4f}'. \
                    format(order.executed.price,
                           order.executed.value,
                           order.executed.comm)
                self.buyprice = order.executed.price
                self.buycomm = order.executed.comm

            else:  # Sell
                self.broker_message = 'SELL executed,\nPrice: {:.5f}, Cost: {:.4f}, Comm: {:.4f}'. \
                    format(order.executed.price,
                           order.executed.value,
                           order.executed.comm)
            self.bar_executed = len(self)

        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.broker_message = 'ORDER FAILED with status: ' + str(order.getstatusname())

        # self.log.warning('BM: {}'.format(self.broker_message))
        self.order = None

    def get_reward(self):
        """
        Shapes reward function as normalized single trade realized profit/loss,
        augmented with potential-based reward shaping functions in form of:
        F(s, a, s`) = gamma * FI(s`) - FI(s);
        Potential FI_1 is current normalized unrealized profit/loss.

        Paper:
            "Policy invariance under reward transformations:
             Theory and application to reward shaping" by A. Ng et al., 1999;
             http://www.robotics.stanford.edu/~ang/papers/shaping-icml99.pdf
        """

        # All sliding statistics for this step are already updated by get_state().

        # Potential-based shaping function 1:
        # based on potential of averaged profit/loss for current opened trade (unrealized p/l):
        unrealised_pnl = np.asarray(self.broker_stat['unrealized_pnl'])
        current_pos_duration = self.broker_stat['pos_duration'][-1]

        # We want to estimate potential `fi = gamma*fi_prime - fi` of current opened position,
        # thus need to consider different cases given skip_fame parameter:
        if current_pos_duration == 0:
            # Set potential term to zero if there is no opened positions:
            fi_1 = 0
            fi_1_prime = 0
            # Reset filter state:
            self.kf_state = [0, 0]
        else:
            fi_1 = self.last_pnl
            # fi_1_prime = np.average(unrealised_pnl[-1])
            self.kf_state = self.kf.filter_update(
                filtered_state_mean=self.kf_state[0],
                filtered_state_covariance=self.kf_state[1],
                observation=unrealised_pnl[-1],
            )
            fi_1_prime = np.squeeze(self.kf_state[0])

        # Potential term 1:
        f1 = self.p.gamma * fi_1_prime - fi_1
        self.last_pnl = fi_1_prime

        # Potential-based shaping function 2:
        # based on potential of averaged profit/loss for global unrealized pnl:
        total_pnl = np.asarray(self.broker_stat['total_unrealized_pnl'])
        delta_total_pnl = np.average(total_pnl[-self.p.skip_frame:]) - np.average(total_pnl[:-self.p.skip_frame])

        fi_2 = delta_total_pnl
        fi_2_prime = self.last_delta_total_pnl

        # Potential term 2:
        f2 = self.p.gamma * fi_2_prime - fi_2
        self.last_delta_total_pnl = delta_total_pnl

        # Potential term 3:
        # f3 = 1 + .5 * np.log(1 + current_pos_duration)
        f3 = 1.0

        # Main reward function: normalized realized profit/loss:
        realized_pnl = np.asarray(self.broker_stat['realized_pnl'])[-self.p.skip_frame:].sum()

        # Weights are subject to tune:
        self.reward = (1.0 * f1 * f3 + 1.0 * realized_pnl) * self.p.reward_scale
        # self.reward = np.clip(self.reward, -self.p.reward_scale, self.p.reward_scale)

        self.reward = np.clip(self.reward, -1e3, 1e3)

        return self.reward

    def _next_discrete(self, action):
        """
        Manages spread virtual positions.

        Args:
            action:     dict, string encoding of btgym.spaces.ActionDictSpace

        """
        # Here we expect action dict to contain single key:
        single_action = action[self.action_key]

        if single_action == 'hold' or self.is_done_enabled:
            pass
        elif single_action == 'buy':
            self.long_spread()
            self.broker_message = 'new {}_LONG created; '.format(self.action_key) + self.broker_message
        elif single_action == 'sell':
            self.short_spread()
            self.broker_message = 'new {}_SHORT created; '.format(self.action_key) + self.broker_message
        elif single_action == 'close':
            self.close_spread()
            self.broker_message = 'new {}_CLOSE created; '.format(self.action_key) + self.broker_message


class SSAStrategy_0(PairSpreadStrategy_0):
    """
    TimeSeriesModel decomposition based.
    """
    time_dim = 128
    avg_period = 32
    model_time_dim = 16
    portfolio_actions = ('hold', 'buy', 'sell', 'close')
    features_parameters = None
    num_features = 3

    params = dict(
        state_shape={
            'external': DictSpace(
                {
                    'ssa': spaces.Box(low=-100, high=100, shape=(time_dim, 1, num_features), dtype=np.float32),
                    'model': spaces.Box(low=-100, high=100, shape=(model_time_dim, 1, 12), dtype=np.float32),
                }
            ),
            'internal': spaces.Box(low=-100, high=100, shape=(avg_period, 1, 5), dtype=np.float32),
            'expert': spaces.Box(low=0, high=10, shape=(len(portfolio_actions),), dtype=np.float32),  # not used
            'stat': spaces.Box(low=-1e6, high=1e6, shape=(3, 1), dtype=np.float32),  # debug
            'metadata': DictSpace(
                {
                    'type': spaces.Box(
                        shape=(),
                        low=0,
                        high=1,
                        dtype=np.uint32
                    ),
                    'trial_num': spaces.Box(
                        shape=(),
                        low=0,
                        high=10 ** 10,
                        dtype=np.uint32
                    ),
                    'trial_type': spaces.Box(
                        shape=(),
                        low=0,
                        high=1,
                        dtype=np.uint32
                    ),
                    'sample_num': spaces.Box(
                        shape=(),
                        low=0,
                        high=10 ** 10,
                        dtype=np.uint32
                    ),
                    'first_row': spaces.Box(
                        shape=(),
                        low=0,
                        high=10 ** 10,
                        dtype=np.uint32
                    ),
                    'timestamp': spaces.Box(
                        shape=(),
                        low=0,
                        high=np.finfo(np.float64).max,
                        dtype=np.float64
                    ),
                    'generator': DictSpace(
                        {
                            'mu': spaces.Box(
                                shape=(),
                                low=np.finfo(np.float64).min,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            ),
                            'l': spaces.Box(
                                shape=(),
                                low=0,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            ),
                            'sigma': spaces.Box(
                                shape=(),
                                low=0,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            ),
                            'x0': spaces.Box(
                                shape=(),
                                low=np.finfo(np.float64).min,
                                high=np.finfo(np.float64).max,
                                dtype=np.float64
                            )
                        }
                    )
                }
            )
        },
        data_model_params=dict(
            alpha=.001,
            norm_alpha=.0001,
            filter_alpha=.05,
            max_length=time_dim * 2,
            analyzer_window=10,
            analyzer_grouping=[[0, 1], [1, 2], [2, 3], [3, None]],
        ),
        cash_name='default_cash',
        asset_names=['default_asset'],
        start_cash=None,
        commission=None,
        slippage=None,
        leverage=1.0,
        gamma=0.99,             # fi_gamma, should match MDP gamma decay
        reward_scale=1,         # reward multiplicator
        norm_alpha=0.001,       # renormalisation tracking decay in []0, 1]
        drawdown_call=10,       # finish episode when hitting drawdown treshghold, in percent.
        dataset_stat=None,      # Summary descriptive statistics for entire dataset and
        episode_stat=None,      # current episode. Got updated by server.
        time_dim=time_dim,      # time embedding period
        avg_period=avg_period,  # number of time steps reward estimation statistics are averaged over
        features_parameters=features_parameters,
        num_features=num_features,
        metadata={},
        broadcast_message={},
        trial_stat=None,
        trial_metadata=None,
        portfolio_actions=portfolio_actions,
        skip_frame=1,  # number of environment steps to skip before returning next environment response
        order_size=None,
        initial_action=None,
        initial_portfolio_action=None,
        state_int_scale=1,
        state_ext_scale=1,
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Bivariate model:
        self.data_model = BivariatePriceModel(**self.p.data_model_params)

        # Accumulators for 'model' observation mode:
        self.external_model_state = np.zeros([self.model_time_dim, 1, 12])

    def set_datalines(self):

        # Here spread is for plotting and norm. tracking:
        self.data.spread = btind.SimpleMovingAverage(self.datas[0] - self.datas[1], period=1)
        self.data.spread.plotinfo.subplot = True
        self.data.spread.plotinfo.plotabove = True
        self.data.spread.plotinfo.plotname = list(self.p.asset_names)[0]

        # Override stat line:
        self.stat_asset = self.data.spread

        initial_time_period = self.p.time_dim
        self.data.dim_sma = btind.SimpleMovingAverage(
            self.datas[0],
            period=initial_time_period
        )
        self.data.dim_sma.plotinfo.plot = False

    def nextstart(self):
        self.inner_embedding = self.data.close.buflen()
        self.log.debug('Inner time embedding: {}'.format(self.inner_embedding))
        x_init = np.stack(
            [
                np.asarray(self.datas[0].get(size=self.inner_embedding)),
                np.asarray(self.datas[1].get(size=self.inner_embedding))
            ],
            axis=0
        )
        self.data_model.reset(x_init)

    def get_external_state(self):
        return dict(
            ssa=self.get_external_ssa_state(),
            model=self.get_external_model_state(),
        )

    def get_external_ssa_state(self):
        """
        Spread SSA decomposition.
        """
        x_upd = np.stack(
            [
                np.asarray(self.datas[0].get(size=self.p.skip_frame)),
                np.asarray(self.datas[1].get(size=self.p.skip_frame))
            ],
            axis=0
        )
        # self.log.warning('x_upd: {}'.format(x_upd.shape))
        self.data_model.update(x_upd)

        x_ssa = self.data_model.s.transform(size=self.p.time_dim).T

        # Gradient along features axis:
        # dx = np.gradient(x_ssa, axis=-1)
        #
        # # Add up: gradient  along time axis:
        # # dx2 = np.gradient(dx, axis=0)
        #
        # # TODO: different conv. encoders for these two types of features:
        # x = np.concatenate([x_ssa_bank, dx], axis=-1)

        # Crop outliers:
        x_ssa = np.clip(x_ssa, -10, 10)
        return x_ssa[:, None, :-1]

    def get_external_model_state(self):
        """
         Spread stochastic model parameters.
        """
        # TODO: cov to corr
        state = self.data_model.s.process.get_state()
        update = np.concatenate(
            [
                state.filtered.mean.flatten(),
                state.filtered.covariance.flatten()
            ]
        )
        self.external_model_state = np.concatenate(
            [
                self.external_model_state[1:, :, :],
                update[None, None, :]
            ],
            axis=0
        )
        return self.external_model_state


