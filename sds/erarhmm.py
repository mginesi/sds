import autograd.numpy as np
import autograd.numpy.random as npr

from sds import rARHMM
from sds.observations import LinearGaussianObservation

from sds.utils import ensure_args_are_viable_lists


class erARHMM(rARHMM):

    def __init__(self, nb_states, dm_obs, dm_act, type='recurrent',
                 learn_dyn=True, learn_ctl=False):
        super(erARHMM, self).__init__(nb_states, dm_obs, dm_act, type)

        self.learn_dyn = learn_dyn
        self.learn_ctl = learn_ctl

        self.controls = LinearGaussianObservation(self.nb_states, self.dm_obs, self.dm_act)

    @ensure_args_are_viable_lists
    def initialize(self, obs, act=None, **kwargs):
        super(erARHMM, self).initialize(obs, act, **kwargs)
        # if self.learn_ctl:
        #     self.controls.initialize(obs, act, **kwargs)

    def log_priors(self):
        logprior = super(erARHMM, self).log_priors()
        if self.learn_ctl:
            logprior += self.controls.log_prior()
        return logprior

    @ensure_args_are_viable_lists
    def log_likelihoods(self, obs, act=None):
        loginit, logtrans, logobs = super(erARHMM, self).log_likelihoods(obs, act)

        if self.learn_ctl:
            total_logobs = []
            logctl = self.controls.log_likelihood(obs, act)
            for _logobs, _logctl in zip(logobs, logctl):
                total_logobs.append(_logobs + _logctl)
            return [loginit, logtrans, total_logobs]
        else:
            return [loginit, logtrans, logobs]

    def mstep(self, gamma, zeta, obs, act=None):
        if self.learn_dyn:
            self.init_state.mstep([_gamma[0, :] for _gamma in gamma])
            self.transitions.mstep(zeta, obs, act, nb_iters=100)
            self.observations.mstep(gamma, obs, act)
        if self.learn_ctl:
            self.controls.mstep(gamma, obs, act)

    def permute(self, perm):
        super(erARHMM, self).permute(perm)
        if self.learn_ctl:
            self.controls.permute(perm)

    @ensure_args_are_viable_lists
    def mean_observation(self, obs, act=None):
        if self.learn_ctl:
            loglikhds = self.log_likelihoods(obs, act)
            alpha = self.forward(loglikhds)
            beta = self.backward(loglikhds)
            gamma = self.marginals(alpha, beta)

            mu_obs = self.observations.smooth(gamma, obs, act)
            mu_ctl = self.controls.smooth(gamma, obs, act)
            return mu_obs, mu_ctl
        else:
            return super(erARHMM, self).mean_observation(obs, act)

    def sample(self, act=None, horizon=None, stoch=True):
        if self.learn_ctl:
            state = []
            obs = []
            act = []

            for n in range(len(horizon)):
                _state = np.zeros((horizon[n],), np.int64)
                _obs = np.zeros((horizon[n], self.dm_obs))
                _act = np.zeros((horizon[n], self.dm_act))

                _state[0] = self.init_state.sample()
                _obs[0, :] = self.init_observation.sample(_state[0], x=None, u=None, stoch=stoch)
                _act[0, :] = self.controls.sample(_state[0], _obs[0, :], stoch=stoch)
                for t in range(1, horizon[n]):
                    _state[t] = self.transitions.sample(_state[t - 1], _obs[t - 1, :], _act[t - 1, :])
                    _obs[t, :] = self.observations.sample(_state[t], _obs[t - 1, :], _act[t - 1, :], stoch=stoch)
                    _act[t, :] = self.controls.sample(_state[t], _obs[t, :], stoch=stoch)

                state.append(_state)
                obs.append(_obs)
                act.append(_act)

            return state, obs, act
        else:
            return super(erARHMM, self).sample(act, horizon, stoch)

    def forcast(self, hist_obs=None, hist_act=None, nxt_act=None,
                horizon=None, stoch=True, infer='viterbi'):
        if self.learn_ctl:
            nxt_state = []
            nxt_obs = []
            nxt_act = []

            for n in range(len(horizon)):
                _hist_obs = hist_obs[n]
                _hist_act = hist_act[n]

                _nxt_act = np.zeros((horizon[n] + 1, self.dm_act))
                _nxt_obs = np.zeros((horizon[n] + 1, self.dm_obs))
                _nxt_state = np.zeros((horizon[n] + 1,), np.int64)

                if infer == 'viterbi':
                    _, _state_seq = self.viterbi(_hist_obs, _hist_act)
                    _state = _state_seq[0][-1]
                else:
                    _belief = self.filter(_hist_obs, _hist_act)
                    _state = npr.choice(n=self.nb_states, p=_belief[0][-1, ...])

                _nxt_state[0] = _state
                _nxt_obs[0, :] = _hist_obs[-1, ...]
                _nxt_act[0, :] = _hist_act[-1, ...]

                for t in range(horizon[n]):
                    _nxt_state[t + 1] = self.transitions.sample(_nxt_state[t], _nxt_obs[t, :], _nxt_act[t, :])
                    _nxt_obs[t + 1, :] = self.observations.sample(_nxt_state[t + 1], _nxt_obs[t, :], _nxt_act[t, :], stoch=stoch)
                    _nxt_act[t + 1, :] = self.controls.sample(_nxt_state[t + 1], _nxt_obs[t + 1, :], stoch=stoch)

                nxt_state.append(_nxt_state)
                nxt_obs.append(_nxt_obs)
                nxt_act.append(_nxt_act)

            return nxt_state, nxt_obs
        else:
            return super(erARHMM, self).forcast(hist_obs, hist_act, nxt_act, horizon, stoch, infer)

    def step(self, hist_obs=None, hist_act=None,
             stoch=True, infer='viterbi'):
        pass

    @ensure_args_are_viable_lists
    def kstep_mse(self, obs, act, horizon=1, stoch=True, infer='viterbi'):
        if not self.learn_ctl:
            return super(erARHMM, self).kstep_mse(obs, act, horizon=horizon, stoch=stoch, infer=infer)
        else:
            from sklearn.metrics import mean_squared_error, r2_score

            mse, norm_mse = [], []
            for _obs, _act in zip(obs, act):
                _hist_obs, _hist_act = [], []
                _target, _prediction = [], []

                _nb_steps = _obs.shape[0] - horizon
                for t in range(_nb_steps):
                    _hist_obs.append(_obs[:t + 1, :])
                    _hist_act.append(_act[:t + 1, :])

                _k = [horizon for _ in range(_nb_steps)]

                _, _obs_hat, _act_hat = self.forcast(hist_obs=_hist_obs, hist_act=_hist_act,
                                                     nxt_act=None, horizon=_k,
                                                     stoch=stoch, infer=infer)

                for t in range(_nb_steps):
                    _target.append(np.hstack((_obs[t + horizon, :], _act[t + horizon, :])))
                    _prediction.append(np.hstack((_obs_hat[t][-1, :], _act_hat[t][-1, :])))

                _target = np.vstack(_target)
                _prediction = np.vstack(_prediction)

                _mse = mean_squared_error(_target, _prediction)
                mse.append(_mse)

                _norm_mse = r2_score(_target, _prediction,
                                     multioutput='variance_weighted')
                norm_mse.append(_norm_mse)

            return np.mean(mse), np.mean(norm_mse)
