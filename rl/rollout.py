import os
import numpy as np
import gym

import torch
import torch.nn as nn
import torch.nn.functional as F

import time
import json
import cv2
import pickle
import gzip
from collections import defaultdict
from typing import Union
import bisect
import math

from .logger import Logger
from . import utils, atari, hybridVecEnv, wrappers, models, compression
from .config import args

import collections

def save_progress(log: Logger):
    """ Saves some useful information to progress.txt. """

    details = {}
    details["max_epochs"] = args.epochs
    details["completed_epochs"] = log["env_step"] / 1e6  # include the current completed step.
    # ep_score could be states, or a float (population based is the group mean which is a float)
    if type(log["ep_score"]) is float:
        details["score"] = log["ep_score"]
    else:
        details["score"] = log["ep_score"][0]
    details["fraction_complete"] = details["completed_epochs"] / details["max_epochs"]
    try:
        details["fps"] = log["fps"]
    except:
        details["fps"] = 0
    frames_remaining = (details["max_epochs"] - details["completed_epochs"]) * 1e6
    details["eta"] = (frames_remaining / details["fps"]) if details["fps"] > 0 else 0
    details["host"] = args.hostname
    details["device"] = args.device
    details["last_modified"] = time.time()
    with open(os.path.join(args.log_folder, "progress.txt"), "w") as f:
        json.dump(details, f, indent=4)


def calculate_tp_returns(dones: np.ndarray, final_tp_estimate: np.ndarray):
    """
    Calculate terminal prediction estimates using bootstrapping
    """

    # todo: make this td(\lambda) style...)

    N, A = dones.shape

    returns = np.zeros([N, A], dtype=np.float32)
    current_return = final_tp_estimate

    gamma = 0.99 # this is a very interesting idea, discount the terminal time.

    for i in reversed(range(N)):
        returns[i] = current_return = 1 + current_return * gamma * (1.0 - dones[i])

    return returns


def calculate_mc_returns(rewards, dones, final_value_estimate, gamma) -> np.ndarray:
    """
    Calculates returns given a batch of rewards, dones, and a final value estimate.
    Input is vectorized so it can calculate returns for multiple agents at once.
    :param rewards: nd array of dims [N,A]
    :param dones:   nd array of dims [N,A] where 1 = done and 0 = not done.
    :param final_value_estimate: nd array [A] containing value estimate of final state after last action.
    :param gamma:   discount rate.
    :return: np array of dims [N,A]
    """

    N, A = rewards.shape

    returns = np.zeros([N, A], dtype=np.float32)
    current_return = final_value_estimate

    if type(gamma) is float:
        gamma = np.ones([N, A], dtype=np.float32) * gamma

    for i in reversed(range(N)):
        returns[i] = current_return = rewards[i] + current_return * gamma[i] * (1.0 - dones[i])

    return returns


def calculate_gae(
        batch_rewards,
        batch_value,
        final_value_estimate,
        batch_terminal,
        gamma: float,
        lamb=1.0,
        normalize=False
    ):
    """
    Calculates GAE based on rollout data.
    """
    N, A = batch_rewards.shape

    batch_advantage = np.zeros_like(batch_rewards, dtype=np.float32)
    prev_adv = np.zeros([A], dtype=np.float32)
    for t in reversed(range(N)):
        is_next_new_episode = batch_terminal[
            t] if batch_terminal is not None else False  # batch_terminal[t] records if prev_state[t] was terminal state)
        value_next_t = batch_value[t + 1] if t != N - 1 else final_value_estimate
        delta = batch_rewards[t] + gamma * value_next_t * (1.0 - is_next_new_episode) - batch_value[t]
        batch_advantage[t] = prev_adv = delta + gamma * lamb * (
                1.0 - is_next_new_episode) * prev_adv
    if normalize:
        batch_advantage = (batch_advantage - batch_advantage.mean()) / (batch_advantage.std() + 1e-8)
    return batch_advantage


def calculate_gae_tvf(
        batch_reward: np.ndarray,
        batch_value: np.ndarray,
        final_value_estimate: np.ndarray,
        batch_terminal: np.ndarray,
        discount_fn = lambda t: 0.999**t,
        lamb: float = 0.95):

    """
    A modified version of GAE that uses truncated value estimates to support any discount function.
    This works by extracting estimated rewards from the value curve via a finite difference.

    batch_reward: [N, A] rewards for each timestep
    batch_value: [N, A, H] value at each timestep, for each horizon (0..max_horizon)
    final_value_estimate: [A, H] value at timestep N+1
    batch_terminal [N, A] terminal signals for each timestep
    discount_fn A discount function in terms of t, the number of timesteps in the future.
    lamb: lambda, as per GAE lambda.
    """

    N, A, H = batch_value.shape


    advantages = np.zeros([N, A], dtype=np.float32)
    values = np.concatenate([batch_value, final_value_estimate[None, :, :]], axis=0)

    # get expected rewards. Note: I'm assuming here that the rewards have not been discounted
    assert args.tvf_gamma == 1, "General discounting function requires TVF estimates to be undiscounted (might fix later)"
    expected_rewards = values[:, :, 0] - batch_value[:, :, 1]

    def calculate_g(t, n:int):
        """ Calculate G^(n) (s_t) """
        # we use the rewards first, then use expected rewards from 'pivot' state onwards.
        # pivot state is either the final state in rollout, or t+n, which ever comes first.
        sum_of_rewards = np.zeros([N], dtype=np.float32)
        discount = np.ones([N], dtype=np.float32) # just used to handle terminals
        pivot_state = min(t+n, N)
        for i in range(H):
            if t+i < pivot_state:
                reward = batch_reward[t+i, :]
                discount *= 1-batch_terminal[t+i, :]
            else:
                reward = expected_rewards[pivot_state, :, (t+i)-pivot_state]
            sum_of_rewards += reward * discount * discount_fn(i)
        return sum_of_rewards

    def get_g_weights(max_n: int):
        """
        Returns the weights for each G estimate, given some lambda.
        """

        # weights are assigned with exponential decay, except that the weight of the final g_return uses
        # all remaining weight. This is the same as assuming that g(>max_n) = g(n)
        # because of this 1/(1-lambda) should be a fair bit larger than max_n, so if a window of length 128 is being
        # used, lambda should be < 0.99 otherwise the final estimate carries a significant proportion of the weight

        weight = (1-lamb)
        weights = []
        for _ in range(max_n-1):
            weights.append(weight)
            weight *= lamb
        weights.append(lamb**max_n)
        weights = np.asarray(weights)

        assert abs(weights.sum() - 1.0) < 1e-6
        return weights

    # advantage^(n) = -V(s_t) + r_t + r_t+1 ... + r_{t+n-1} + V(s_{t+n})

    for t in range(N):
        max_n = N - t
        weights = get_g_weights(max_n)
        for n, weight in zip(range(1, max_n+1), weights):
            if weight <= 1e-6:
                # ignore small or zero weights.
                continue
            advantages[t, :] += weight * calculate_g(t, n)
        advantages[t, :] -= batch_value[t, :, -1]

    return advantages


def calculate_tvf_lambda(
        rewards: np.ndarray,
        dones: np.ndarray,
        values: np.ndarray,
        final_value_estimates: np.ndarray,
        gamma: float,
        lamb: float = 0.95,
):
    # this is a little slow, but calculate each n_step return and combine them.
    # also.. this is just an approximation

    params = (rewards, dones, values, final_value_estimates, gamma)

    if lamb == 0:
        return calculate_tvf_td(*params)
    if lamb == 1:
        return calculate_tvf_mc(*params)

    # can be slow for high n_steps... so we cap it at 100, and use effective horizon as a cap too
    N = int(min(1 / (1 - lamb), args.n_steps, 100))

    g = []
    for i in range(N):
        g.append(calculate_tvf_n_step(*params, n_step=i + 1))

    # this is totally wrong... please fix.

    result = g[0] * (1 - lamb)
    for i in range(1, N):
        result += g[i] * (lamb ** i) * (1 - lamb)

    return result


def calculate_tvf_n_step(
        rewards: np.ndarray,
        dones: np.ndarray,
        values: np.ndarray,
        final_value_estimates: np.ndarray,
        gamma: float,
        n_step: int,
):
    """
    Returns the n_step value estimate.
    This is the old, non sampled version
    """

    N, A, H = values.shape

    returns = np.zeros([N, A, H], dtype=np.float32)

    values = np.concatenate([values, final_value_estimates[None, :, :]], axis=0)

    for t in range(N):

        # first collect the rewards
        discount = np.ones([A], dtype=np.float32)
        reward_sum = np.zeros([A], dtype=np.float32)
        steps_made = 0

        for n in range(1, n_step + 1):
            if (t + n - 1) >= N:
                break
            # n_step is longer than horizon required
            if n >= H:
                break
            this_reward = rewards[t + n - 1]
            reward_sum += discount * this_reward
            discount *= gamma * (1 - dones[t + n - 1])
            steps_made += 1

            # the first n_step returns are just the discounted rewards, no bootstrap estimates...
            returns[t, :, n] = reward_sum

        # note: if we are near the end we might not be able to do a full n_steps, so just a shorter n_step for these

        # next update the remaining horizons based on the bootstrap estimates
        # we do all the horizons in one go, which quite fast for long horizons
        discounted_bootstrap_estimates = discount[:, None] * values[t + steps_made, :, 1:H - steps_made]
        returns[t, :, steps_made + 1:] += reward_sum[:, None] + discounted_bootstrap_estimates

        # this is the non-vectorized code, for reference.
        # for h in range(steps_made+1, H):
        #    bootstrap_estimate = discount * values[t + steps_made, :, h - steps_made] if (h - steps_made) > 0 else 0
        #    returns[t, :, h] = reward_sum + bootstrap_estimate

    return returns


def calculate_tvf_mc(
        rewards: np.ndarray,
        dones: np.ndarray,
        values: None,  # note: values is ignored...
        final_value_estimates: np.ndarray,
        gamma: float
):
    """
    This is really just the largest n_step that will work, but does not require values
    """

    N, A = rewards.shape
    H = final_value_estimates.shape[-1]

    returns = np.zeros([N, A, H], dtype=np.float32)

    n_step = N

    for t in range(N):

        # first collect the rewards
        discount = np.ones([A], dtype=np.float32)
        reward_sum = np.zeros([A], dtype=np.float32)
        steps_made = 0

        for n in range(1, n_step + 1):
            if (t + n - 1) >= N:
                break
            # n_step is longer than horizon required
            if n >= H:
                break
            this_reward = rewards[t + n - 1]
            reward_sum += discount * this_reward
            discount *= gamma * (1 - dones[t + n - 1])
            steps_made += 1

            # the first n_step returns are just the discounted rewards, no bootstrap estimates...
            returns[t, :, n] = reward_sum

        # note: if we are near the end we might not be able to do a full n_steps, so just a shorter n_step for these

        # next update the remaining horizons based on the bootstrap estimates
        # we do all the horizons in one go, which quite fast for long horizons
        discounted_bootstrap_estimates = discount[:, None] * final_value_estimates[:, 1:-steps_made]
        returns[t, :, steps_made + 1:] += reward_sum[:, None] + discounted_bootstrap_estimates

    return returns


def calculate_tvf_td(
        rewards: np.ndarray,
        dones: np.ndarray,
        values: np.ndarray,
        final_value_estimates: np.ndarray,
        gamma: float,
):
    """
    Calculate return targets using value function horizons.
    This involves finding targets for each horizon being learned

    rewards: np float32 array of shape [N, A]
    dones: np float32 array of shape [N, A]
    values: np float32 array of shape [N, A, H]
    final_value_estimates: np float32 array of shape [A, H]

    returns: returns for each time step and horizon, np array of shape [N, A, H]

    """

    N, A, H = values.shape

    returns = np.zeros([N, A, H], dtype=np.float32)

    # note: this webpage helped a lot with writing this...
    # https://amreis.github.io/ml/reinf-learn/2017/11/02/reinforcement-learning-eligibility-traces.html

    values = np.concatenate([values, final_value_estimates[None, :, :]], axis=0)

    for t in range(N):
        for h in range(1, H):
            reward_sum = rewards[t + 1 - 1]
            discount = gamma * (1 - dones[t + 1 - 1])
            bootstrap_estimate = discount * values[t + 1, :, h - 1] if (h - 1) > 0 else 0
            estimate = reward_sum + bootstrap_estimate
            returns[t, :, h] = estimate
    return returns


def _interpolate(horizons, values, target_horizon: int):
    """
    Returns linearly interpolated value from source_values

    horizons: sorted ndarray of shape[K] of horizons, must be in *strictly* ascending order
    values: ndarray of shape [*shape, K] where values[...,h] corresponds to horizon horizons[h]
    target_horizon: the horizon we would like to know the interpolated value of

    """

    if target_horizon <= 0:
        # by definition value of a 0 horizon is 0.
        return values[..., 0] * 0

    index = bisect.bisect_left(horizons, target_horizon)
    if index == 0:
        return values[..., 0]
    if index == len(horizons):
        return values[..., -1]
    value_pre = values[..., index - 1]
    value_post = values[..., index]
    dx = (horizons[index] - horizons[index - 1])
    if dx == 0:
        # this happens if there are repeated values, in this case just take leftmost result
        return value_pre
    factor = (target_horizon - horizons[index - 1]) / dx
    return value_pre * (1 - factor) + value_post * factor


class SimulatedAnnealing:
    """
    Handles simulated annealing

    usage

    sa = SimulatedAnnealing

    for i in range(100):
        eval_score = eval(sa.value)
        sa.process(eval_score)

    """
    def __init__(self, initial_value:float=0):
        self.value = initial_value
        self.neighbour = initial_value
        self.prev_score = float('-inf')
        self._generate_neighbour()

    def _generate_neighbour(self):
        """
        Returns new candidate value
        """
        if np.random.rand() < 0.5:
            self.neighbour = self.value * 1.05
        else:
            self.neighbour = self.value / 1.05

    def process(self, score):
        # todo: do this properly with temperature...
        if (score > self.prev_score) or (np.random.rand() < 0.05):
            # accept
            self.value = self.neighbour
            self.prev_score = score
        else:
            # reject
            pass
        self._generate_neighbour()

class Runner:

    def __init__(self, model: models.TVFModel, log, name="agent"):
        """ Setup our rollout runner. """

        self.name = name
        self.model = model
        self.step = 0
        self.horizon_sa = SimulatedAnnealing(1000) # this ends up being one third, so a horizon of ~300

        optimizer_params = {
            'eps': args.adam_epsilon
        }

        optimizer = torch.optim.Adam

        self.policy_optimizer = optimizer(model.policy_net.parameters(), lr=self.policy_lr, **optimizer_params)
        self.value_optimizer = optimizer(model.value_net.parameters(), lr=self.value_lr, **optimizer_params)
        if args.distill_epochs > 0:
            self.distill_optimizer = optimizer(model.policy_net.parameters(), lr=self.distill_lr,
                                                    **optimizer_params)
        else:
            self.distill_optimizer = None

        if args.use_rnd:
            self.rnd_optimizer = optimizer(model.prediction_net.parameters(), lr=self.rnd_lr, **optimizer_params)
        else:
            self.rnd_optimizer = None

        self.vec_env = None
        self.log = log

        self.N = N = args.n_steps
        self.A = A = args.agents

        self.state_shape = model.input_dims
        self.rnn_state_shape = [2, 512]  # records h and c for LSTM units.
        self.policy_shape = [model.actions]

        self.batch_counter = 0

        self.episode_score = np.zeros([A], dtype=np.float32)
        self.episode_len = np.zeros([A], dtype=np.int32)
        self.obs = np.zeros([A, *self.state_shape], dtype=np.uint8)
        self.time = np.zeros([A], dtype=np.float32)
        # includes final state as well, which is needed for final value estimate
        if args.use_compression:
            # states must be decompressed with .decompress before use.
            print(f"Compression [{utils.Color.OKGREEN}enabled{utils.Color.ENDC}]")
            self.all_obs = np.zeros([N + 1, A], dtype=np.object)
        else:
            print(f"Compression [disabled]")
            self.all_obs = np.zeros([N + 1, A, *self.state_shape], dtype=np.uint8)
        self.all_time = np.zeros([N + 1, A], dtype=np.float32)
        self.actions = np.zeros([N, A], dtype=np.int64)
        self.ext_rewards = np.zeros([N, A], dtype=np.float32)
        self.log_policy = np.zeros([N, A, *self.policy_shape], dtype=np.float32)
        self.ext_advantage = np.zeros([N, A], dtype=np.float32)
        self.terminals = np.zeros([N, A], dtype=np.bool)  # indicates prev_state was a terminal state.

        # intrinsic rewards
        self.int_rewards = np.zeros([N, A], dtype=np.float32)
        self.int_value = np.zeros([N, A], dtype=np.float32)

        # log optimal
        self.sqr_value = np.zeros([N, A], dtype=np.float32)
        self.sqr_returns = np.zeros([N, A], dtype=np.float32)

        # returns generation
        self.ext_returns = np.zeros([N, A], dtype=np.float32)

        self.int_returns_raw = np.zeros([N, A], dtype=np.float32)
        self.advantage = np.zeros([N, A], dtype=np.float32)

        # terminal prediction
        self.tp_final_value_estimate = np.zeros([A], dtype=np.float32)
        self.tp_returns = np.zeros([N, A], dtype=np.float32)  # terminal state predictions are generated the same was as returns.

        self.int_final_value_estimate = np.zeros([A], dtype=np.float32)

        self.intrinsic_returns_rms = utils.RunningMeanStd(shape=())
        self.ems_norm = np.zeros([args.agents])

        # outputs tensors when clip loss is very high.
        self.log_high_grad_norm = True

        self.game_crashes = 0
        self.reward_clips = 0
        self.ep_count = 0
        self.episode_length_buffer = collections.deque(maxlen=1000)

        #  these horizons will always be generated and their scores logged.
        self.tvf_debug_horizons = [h for h in [1, 3, 10, 30, 100, 300, 1000, 3000, 10000, 30000] if
                                   h <= args.tvf_max_horizon]
        if args.tvf_max_horizon not in self.tvf_debug_horizons:
            self.tvf_debug_horizons.append(args.tvf_max_horizon)
        self.tvf_debug_horizons.sort()

        # quick check to make sure value transform is correct
        for x in [3.1415, -3.1415, 0, 10, -10, 1, -1, 10000, -10000]:
            new_x = ValueTransform.H(ValueTransform.H_inv(x))
            assert abs(new_x - x) < 1e-6, f"Expected H(H^-1({x})) to be {x} but was {new_x}"

    def anneal(self, x, enable=True):
        return x * np.clip(1-(self.step / (args.epochs * 1e6)), 0, 1) if enable else x

    @property
    def value_lr(self):
        return self.anneal(args.value_lr, enable=args.value_lr_anneal)

    @property
    def policy_lr(self):
        return self.anneal(args.policy_lr, enable=args.policy_lr_anneal)

    @property
    def distill_lr(self):
        return self.anneal(args.distill_lr, enable=args.distill_lr_anneal)

    @property
    def rnd_lr(self):
        return args.value_lr

    def update_learning_rates(self):
        """
        Update learning rates for all optimizers
        Also log learning rates
        """

        self.log.watch("lr_policy", self.policy_lr, display_width=0)
        for g in self.policy_optimizer.param_groups:
            g['lr'] = self.policy_lr

        self.log.watch("lr_value", self.value_lr, display_width=0)
        for g in self.value_optimizer.param_groups:
            g['lr'] = self.value_lr

        if self.distill_optimizer is not None:
            self.log.watch("lr_distill", self.distill_lr, display_width=0)
            for g in self.distill_optimizer.param_groups:
                g['lr'] = self.distill_lr

        if self.rnd_optimizer is not None:
            self.log.watch("lr_rnd", self.rnd_lr, display_width=0)
            for g in self.rnd_optimizer.param_groups:
                g['lr'] = self.rnd_lr


    def create_envs(self):
        """ Creates environments for runner"""
        base_seed = args.seed
        if base_seed is None or base_seed < 0:
            base_seed = np.random.randint(0, 9999)
        env_fns = [lambda i=i: atari.make(args=args, seed=base_seed+(i*997)) for i in range(args.agents)]

        if args.sync_envs:
            self.vec_env = gym.vector.SyncVectorEnv(env_fns)
        else:
            self.vec_env = hybridVecEnv.HybridAsyncVectorEnv(
                env_fns,
                copy=False,
                max_cpus=args.workers,
                verbose=True
            )

        if args.reward_normalization:
            self.vec_env = wrappers.VecNormalizeRewardWrapper(
                self.vec_env,
                # todo: this won't work with auto adjusting gamma, hmm... also. code uses discounting
                #       in a really strange way...
                gamma=args.reward_normalization_gamma,
                scale=args.reward_scale,
                returns_transform=lambda x: ValueTransform.H(x)
            )

        self.log.important("Generated {} agents ({}) using {} ({}) model.".
                           format(args.agents, "async" if not args.sync_envs else "sync", self.model.name,
                                  self.model.dtype))

    def save_checkpoint(self, filename, step):

        data = {
            'step': step,
            'ep_count': self.ep_count,
            'episode_length_buffer' : self.episode_length_buffer,
            'current_horizon': self.current_horizon,
            'model_state_dict': self.model.state_dict(),
            'logs': self.log,
            'env_state': utils.save_env_state(self.vec_env),
            'policy_optimizer_state_dict': self.policy_optimizer.state_dict(),
            'value_optimizer_state_dict': self.value_optimizer.state_dict()
        }

        if args.auto_strategy[:2] == "sa":
            data['horizon_sa'] = self.horizon_sa

        if args.use_rnd:
            data['rnd_optimizer_state_dict'] = self.rnd_optimizer.state_dict()

        if args.use_intrinsic_rewards:
            data['ems_norm'] = self.ems_norm
            data['intrinsic_returns_rms'] = self.intrinsic_returns_rms

        if args.normalize_observations:
            data["observation_norm_state"] = self.model.obs_rms.save_state()

        torch.save(data, filename)

    def get_checkpoints(self, path):
        """ Returns list of (epoch, filename) for each checkpoint in given folder. """
        results = []
        if not os.path.exists(path):
            return []
        for f in os.listdir(path):
            if f.startswith("checkpoint") and f.endswith(".pt"):
                epoch = int(f[11:14])
                results.append((epoch, f))
        results.sort(reverse=True)
        return results

    def load_checkpoint(self, checkpoint_path):
        """ Restores model from checkpoint. Returns current env_step"""
        checkpoint = torch.load(checkpoint_path, map_location=args.device)

        self.model.load_state_dict(checkpoint['model_state_dict'])

        self.policy_optimizer.load_state_dict(checkpoint['policy_optimizer_state_dict'])
        self.value_optimizer.load_state_dict(checkpoint['value_optimizer_state_dict'])
        if "rnd_optimizer_state_dict" in checkpoint:
            self.rnd_optimizer = checkpoint['rnd_optimizer_state_dict']

        if args.auto_strategy[:2] == "sa":
            self.horizon_sa = checkpoint['horizon_sa']

        step = checkpoint['step']
        self.log = checkpoint['logs']
        self.step = step
        self.ep_count = checkpoint.get('ep_count', 0)
        self.episode_length_buffer = checkpoint['episode_length_buffer']

        if args.use_intrinsic_rewards:
            self.ems_norm = checkpoint['ems_norm']
            self.intrinsic_returns_rms = checkpoint['intrinsic_returns_rms']

        utils.restore_env_state(self.vec_env, checkpoint['env_state'])

        if args.normalize_observations:
            self.model.obs_rms.restore_state(checkpoint["observation_norm_state"])

        return step

    def reset(self):

        assert self.vec_env is not None, "Please call create_envs first."

        # initialize agent
        self.obs = self.vec_env.reset()
        self.episode_score *= 0
        self.episode_len *= 0
        self.step = 0
        self.game_crashes = 0
        self.reward_clips = 0
        self.batch_counter = 0
        self.episode_length_buffer.clear()
        # so that there is something in the buffer to start with.
        self.episode_length_buffer.append(1000)

    def run_random_agent(self, iterations):
        self.log.info("Warming up model with random agent...")

        # collect experience
        self.reset()

        for iteration in range(iterations):
            self.generate_rollout(is_warmup=True)

    def forward(self, obs:np.ndarray, aux_features=None, max_batch_size=None, **kwargs):
        """ Forward states through model, returns output, which is a dictionary containing
            "log_policy" etc.
            obs: np array of dims [B, *state_shape]
        """
        max_batch_size = max_batch_size or args.max_micro_batch_size

        # state_shape will be empty_list if compression is enabled
        B, *state_shape = obs.shape
        assert type(obs) == np.ndarray, f"Obs was of type {type(obs)}, expecting np.ndarray"
        assert tuple(state_shape) in [tuple(), tuple(self.state_shape)]

        # break large forwards into batches (note: would be better to just run multiple max_size batches + one last
        # small one than to subdivide)
        if B > max_batch_size:

            mid_point = B // 2

            if aux_features is not None:
                a = self.forward(
                    obs[:mid_point],
                    aux_features=aux_features[:mid_point],
                    max_batch_size=max_batch_size,
                    **kwargs
                )
                b = self.forward(
                    obs[mid_point:],
                    aux_features=aux_features[mid_point:],
                    max_batch_size=max_batch_size,
                    **kwargs
                )
            else:
                a = self.forward(obs[:mid_point], max_batch_size=max_batch_size, **kwargs)
                b = self.forward(obs[mid_point:], max_batch_size=max_batch_size, **kwargs)
            result = {}
            for k in a.keys():
                result[k] = torch.cat(tensors=[a[k], b[k]], dim=0)
            return result
        else:
            if obs.dtype == np.object:
                obs = np.asarray([obs[i].decompress() for i in range(len(obs))])
            return self.model.forward(obs, aux_features=aux_features, **kwargs)

    def _calculate_n_step_sampled_returns(
            self,
            n_step:int,
            gamma:float,
            rewards: np.ndarray,
            dones: np.ndarray,
            required_horizons: np.ndarray,
            value_sample_horizons: np.ndarray,
            value_samples: np.ndarray,
        ):
        """
        This is a fancy n-step sampled returns calculation

        n_step: n-step to use in calculation
        gamma: discount to use
        reward: nd array of dims [N, A]
        dones: nd array of dims [N, A]
        required_horizons: nd array of dims [K]
        value_samples: nd array of dims [N, A, K], where value_samples[n,a,k] is the value of the nth timestep ath agent
            for horizon required_horizons[k]

        If n_step td_lambda is negative it is taken as
        """

        assert value_sample_horizons[0] == 0 and value_sample_horizons[-1] == self.current_horizon, "First and value horizon are required."

        N, A = rewards.shape
        H = self.current_horizon
        K = len(required_horizons)

        # this allows us to map to our 'sparse' returns table
        h_lookup = {}
        for index, h in enumerate(required_horizons):
            if h not in h_lookup:
                h_lookup[h] = [index]
            else:
                h_lookup[h].append(index)

        returns = np.zeros([N, A, K], dtype=np.float32) + ValueTransform.H(0)

        # generate return estimates using n-step returns
        for t in range(N):

            # first collect the rewards
            discount = np.ones([A], dtype=np.float32)
            reward_sum = np.zeros([A], dtype=np.float32)
            steps_made = 0

            for n in range(1, n_step + 1):
                if (t + n - 1) >= N:
                    break
                # n_step is longer than horizon required
                if n >= H:
                    break
                this_reward = rewards[t + n - 1, :]
                reward_sum += discount * this_reward
                discount *= gamma * (1 - dones[t + n - 1, :])
                steps_made += 1

                # the first n_step returns are just the discounted rewards, no bootstrap estimates...
                if n in h_lookup:
                    returns[t, :, h_lookup[n]] = ValueTransform.H(reward_sum)

            for h_index, h in enumerate(required_horizons):
                if h-steps_made <= 0:
                    # these are just the accumulated sums and don't need horizon bootstrapping
                    continue
                interpolated_value = _interpolate(value_sample_horizons, value_samples[t + steps_made, :], h - steps_made)
                returns[t, :, h_index] = ValueTransform.H(reward_sum + ValueTransform.H_inv(interpolated_value) * discount)

        return returns

    def _calculate_lambda_sampled_returns(
            self,
            dims:tuple,
            td_lambda: float,
            n_step_func: callable,
            required_horizons,
    ):
        """
        Calculate td_lambda returns using sampling
        """

        N, A, K = dims

        if td_lambda == 0:
            return n_step_func(1, required_horizons)

        if td_lambda == 1:
            return n_step_func(N, required_horizons)

        # first calculate the weight for each return
        current_weight = (1-td_lambda)
        weights = np.zeros([N], dtype=np.float32)
        for n in range(N):
            weights[n] = current_weight
            current_weight *= td_lambda
        # use last n-step for remaining weight
        weights[-1] = 1.0 - np.sum(weights[:-1])

        returns = np.zeros([N, A, K], dtype=np.float32)
        if args.tvf_lambda_samples == -1:
            # if we have disabled sampling just generate them all and weight them accordingly
            for n, weight in zip(range(N), weights):
                returns += weight * n_step_func(n+1, required_horizons)
            return returns
        else:
            # otherwise sample randomly from n_steps with replacement
            for _ in range(args.tvf_lambda_samples):
                sampled_n_step = np.random.choice(range(N), p=weights) + 1
                returns += n_step_func(sampled_n_step, required_horizons) / args.tvf_lambda_samples
            return returns


    def _calculate_exp_sampled_returns(
            self,
            dims:tuple,
            n_step_func: callable,
            required_horizons,
    ):
        """
        Calculate returns using n_steps at powers of two.
        This allows information to transform more quickly from short horizons to long
        But does not require many calculations, and should work fine for large n_steps
        """

        # note: this averages over the transformed values, which is just fine as it means that we will be more
        # robust to outliers. (assuming the transform compresses the value function)

        n_steps = self.get_exponential_n_steps()

        if args.tvf_exp_mode == "masked":
            # this is the masked version, not sure if it's right...
            # the advantage of this method is that it puts less weight on the highest n-step.
            # i.e. for h=5 the n-step values would be [1, 2, 4, 5, 5, 5, 5, 5, 5, 5]
            # which means we don't get (much) bootstrapping...
            returns = n_step_func(n_steps[0], required_horizons)
            count = np.ones_like(required_horizons)
            for n in n_steps[1:]:
                # mask out returns that have nsteps > horizon
                mask = n <= required_horizons
                count += mask
                returns += n_step_func(n, required_horizons) * mask
            returns *= 1/count
        elif args.tvf_exp_mode == "default":
            returns = n_step_func(n_steps[0], required_horizons)
            for n in n_steps[1:]:
                returns += n_step_func(n, required_horizons)
            returns *= 1/(len(n_steps))
        elif args.tvf_exp_mode == "transformed":
            returns = ValueTransform.H_inv(n_step_func(n_steps[0], required_horizons))
            for n in n_steps[1:]:
                returns += ValueTransform.H_inv(n_step_func(n, required_horizons))
            returns *= 1/(len(n_steps))
            returns = ValueTransform.H(returns)
        else:
            raise ValueError(f"Invalid exp mode {args.tvf_exp_mode}")

        return returns

    def get_adaptive_n_step(self, h):
        # the 0.5 makes adaptive tvf_n_step line up better with normal n_step returns
        # i.e. the both like approximately 40 n_steps
        return max(1, int(0.5 * args.tvf_n_step * h / self.current_horizon))

    def get_exponential_n_steps(self):
        """
        Returns a list of horizons spaced out exponentially for given horizon.
        In some cases horizons might be duplicated (in which case they should have extra weighting)
        """
        results = []
        current_h = 1
        while True:
            results.append(round(current_h))
            current_h *= args.tvf_exp_gamma
            if current_h > args.n_steps:
                break
        return results

    def _calculate_adaptive_sampled_returns(
            self,
            n_step_func: callable,
            required_horizons,
    ):
        """
        Calculate returns where n_steps depends on horizon
        """

        required_n_steps = [self.get_adaptive_n_step(h) for h in required_horizons]
        n_step_lookup = defaultdict(list)
        for n, h in zip(required_n_steps, required_horizons):
            n_step_lookup[n].append(h)

        results = []
        for n, hs in n_step_lookup.items():
            results.append(n_step_func(n, hs))
        returns = np.concatenate(results, axis=-1)

        return returns


    def calculate_sampled_returns(
            self,
            value_sample_horizons: Union[list, np.ndarray],
            required_horizons: Union[list, np.ndarray, int],
            obs=None,
            time=None,
            rewards=None,
            dones=None,
            tvf_mode=None,
            tvf_n_step=None
    ):
        """
        Calculates and returns the (tvf_gamma discounted) (transformed) return estimates for given rollout.

        prev_states: ndarray of dims [N+1, B, *state_shape] containing prev_states
        rewards: float32 ndarray of dims [N, B] containing reward at step n for agent b
        value_sample_horizons: int32 ndarray of dims [K] indicating horizons to generate value estimates at.
        required_horizons: int32 ndarray of dims [K] indicating the horizons for which we want a return estimate.
        """

        assert utils.is_sorted(required_horizons), "Required horizons must be sorted"

        if type(value_sample_horizons) is list:
            value_sample_horizons = np.asarray(value_sample_horizons)
        if type(required_horizons) is list:
            required_horizons = np.asarray(required_horizons)
        if type(required_horizons) in [float, int]:
            required_horizons = np.asarray([required_horizons])

        # setup
        obs = obs if obs is not None else self.all_obs
        time = time if time is not None else self.all_time
        rewards = rewards if rewards is not None else self.ext_rewards
        dones = dones if dones is not None else self.terminals
        tvf_mode = tvf_mode or args.tvf_mode
        tvf_n_step = tvf_n_step or args.tvf_n_step

        N, A, *state_shape = obs[:-1].shape

        assert obs.shape == (N + 1, A, *state_shape)
        assert rewards.shape == (N, A)
        assert dones.shape == (N, A)

        # step 1:
        # use our model to generate the value estimates required
        # for MC this is just an estimate at the end of the window
        assert value_sample_horizons[0] == 0 and value_sample_horizons[-1] == self.current_horizon, "First and value horizon are required."

        value_samples = self.get_value_estimates(obs=obs, time=time, horizons=value_sample_horizons, return_transformed=True)

        n_step_func = lambda x, y: self._calculate_n_step_sampled_returns(
            n_step=x,
            gamma=self.tvf_gamma,
            rewards=rewards,
            dones=dones,
            required_horizons=y,
            value_sample_horizons=value_sample_horizons,
            value_samples=value_samples,
        )

        if tvf_mode == "exponential":
            returns = self._calculate_exp_sampled_returns(
                dims=(N, A, len(required_horizons)),
                n_step_func=n_step_func,
                required_horizons=required_horizons,
            )
        elif tvf_mode == "adaptive":
            returns = self._calculate_adaptive_sampled_returns(
                n_step_func=n_step_func,
                required_horizons=required_horizons,
            )
        elif tvf_mode == "lambda":
            returns = self._calculate_lambda_sampled_returns(
                dims=(N, A, len(required_horizons)),
                td_lambda=args.tvf_lambda,
                n_step_func=n_step_func,
                required_horizons=required_horizons,
            )
        elif tvf_mode == "nstep":
            returns = self._calculate_n_step_sampled_returns(
                n_step=tvf_n_step,
                gamma=self.tvf_gamma,
                rewards=rewards,
                dones=dones,
                required_horizons=required_horizons,
                value_sample_horizons=value_sample_horizons,
                value_samples=value_samples,
            )
        else:
            raise ValueError("Invalid tvf_mode")

        return returns


    @torch.no_grad()
    def export_movie(self, filename, include_rollout=False, include_video=True, max_frames=60 * 60 * 15):
        """ Exports a movie of agent playing game.
            include_rollout: save a copy of the rollout (may as well include policy, actions, value etc)
        """

        scale = 2

        env = atari.make(monitor_video=True)
        _ = env.reset()
        action = 0
        state, reward, done, info = env.step(0)
        rendered_frame = info.get("monitor_obs", state)

        # work out our height
        first_frame = utils.compose_frame(state, rendered_frame)
        height, width, channels = first_frame.shape
        width = (width * scale) // 4 * 4  # make sure these are multiples of 4
        height = (height * scale) // 4 * 4

        # create video recorder, note that this ends up being 2x speed when frameskip=4 is used.
        if include_video:
            video_out = cv2.VideoWriter(filename + ".mp4", cv2.VideoWriter_fourcc(*'mp4v'), 30, (width, height),
                                        isColor=True)
        else:
            video_out = None

        state = env.reset()

        frame_count = 0

        history = defaultdict(list)

        # play the game...
        while not done:

            additional_params = {}

            model_out = self.model.forward(state[np.newaxis], **additional_params)

            log_probs = model_out["log_policy"][0].detach().cpu().numpy()

            if np.any(np.isnan(log_probs)):
                self.log.important("Nans found in policy, halting...")
                raise Exception("Nans found in policy, halting...")

            action = utils.sample_action_from_logp(log_probs)

            if include_rollout:
                history["logprobs"].append(log_probs)
                history["actions"].append(action)
                history["states"].append(state)

            state, reward, done, info = env.step(action)

            channels = info.get("channels", None)
            rendered_frame = info.get("monitor_obs", state)

            agent_layers = state.copy()

            frame = utils.compose_frame(agent_layers, rendered_frame, channels)

            if frame.shape[0] != width or frame.shape[1] != height:
                frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_NEAREST)

            # show current state
            assert frame.shape[1] == width and frame.shape[0] == height, "Frame should be {} but is {}".format(
                (width, height, 3), frame.shape)

            if video_out is not None:
                video_out.write(frame)

            frame_count += 1

            if frame_count >= max_frames:
                break

        if video_out is not None:
            video_out.release()

        if include_rollout:
            np_history = {}
            for k, v in history.items():
                np_history[k] = np.asarray(v)
            pickle.dump(np_history, gzip.open(filename + ".hst.gz", "wb", compresslevel=9))

    def export_debug_frames(self, filename, obs, marker=None):
        # obs will be [N, 4, 84, 84]
        if type(obs) is torch.Tensor:
            obs = obs.cpu().detach().numpy()
        N, C, H, W = obs.shape
        import matplotlib.pyplot as plt
        obs = np.concatenate([obs[:, i] for i in range(4)], axis=-2)
        # obs will be [N, 4*84, 84]
        obs = np.concatenate([obs[i] for i in range(N)], axis=-1)
        # obs will be [4*84, N*84]
        if marker is not None:
            obs[:, marker*W] = 255
        plt.figure(figsize=(N, 4), dpi=84*2)
        plt.imshow(obs, interpolation='nearest')
        plt.savefig(filename)
        plt.close()

    def export_debug_value(self, filename, value):
        pass

    @torch.no_grad()
    def generate_rollout(self, is_warmup=False):

        assert self.vec_env is not None, "Please call create_envs first."

        # times are...
        # Forward: 1.1ms
        # Step: 33ms
        # Compress: 2ms
        # everything else should be minimal.

        for t in range(self.N):

            prev_obs = self.obs.copy()
            prev_time = self.time.copy()

            # forward state through model, then detach the result and convert to numpy.
            model_out = self.forward(self.obs, output="policy")

            log_policy = model_out["log_policy"].cpu().numpy()

            # during warm-up we simply collect experience through a uniform random policy.
            if is_warmup:
                actions = np.random.randint(0, self.model.actions, size=[self.A], dtype=np.int32)
            else:
                # sample actions and run through environment.
                actions = np.asarray([utils.sample_action_from_logp(prob) for prob in log_policy], dtype=np.int32)

            self.obs, ext_rewards, dones, infos = self.vec_env.step(actions)

            # time fraction
            self.time = np.asarray([info["time_frac"] for info in infos])

            # per step reward noise
            if args.per_step_reward_noise > 0:
                ext_rewards += np.random.normal(0, args.per_step_reward_noise, size=ext_rewards.shape)

            # work out our intrinsic rewards
            if args.use_intrinsic_rewards:
                value_int = model_out["int_value"].detach().cpu().numpy()

                int_rewards = np.zeros_like(ext_rewards)

                if args.use_rnd:
                    if is_warmup:
                        # in random mode just update the normalization constants
                        self.model.perform_normalization(self.obs)
                    else:
                        # reward is prediction error on state we land inn.
                        loss_rnd = self.model.prediction_error(self.obs).detach().cpu().numpy()
                        int_rewards += loss_rnd
                else:
                    assert False, "No intrinsic rewards set."

                self.int_rewards[t] = int_rewards
                self.int_value[t] = value_int

            # save raw rewards for monitoring the agents progress
            raw_rewards = np.asarray([info.get("raw_reward", ext_rewards) for reward, info in zip(ext_rewards, infos)],
                                     dtype=np.float32)

            self.episode_score += raw_rewards
            self.episode_len += 1

            for i, (done, info) in enumerate(zip(dones, infos)):
                if done:

                    # this should be always updated, even if it's just a loss of life terminal
                    self.episode_length_buffer.append(info["ep_length"])

                    if "fake_done" in info:
                        # this is a fake reset on loss of life...
                        continue

                    # reset is handled automatically by vectorized environments
                    # so just need to keep track of book-keeping
                    if not is_warmup:
                        self.ep_count += 1
                        self.log.watch_full("ep_score", info["ep_score"], history_length=100)
                        self.log.watch_full("ep_length", info["ep_length"])
                        self.log.watch_mean("ep_count", self.ep_count, history_length=1)

                        if "game_freeze" in info:
                            self.game_crashes += 1
                        if "reward_clip" in info:
                            self.reward_clips += 1

                    self.episode_score[i] = 0
                    self.episode_len[i] = 0

            if args.use_compression:
                prev_obs = np.asarray([compression.BufferSlot(prev_obs[i]) for i in range(len(prev_obs))])

            self.all_obs[t] = prev_obs

            self.all_time[t] = prev_time
            self.actions[t] = actions

            self.ext_rewards[t] = ext_rewards
            self.log_policy[t] = log_policy
            self.terminals[t] = dones

        # save the last state
        if args.use_compression:
            last_obs = np.asarray([compression.BufferSlot(self.obs[i]) for i in range(len(self.obs))])
        else:
            last_obs = self.obs
        self.all_obs[-1] = last_obs
        self.all_time[-1] = self.time

        if args.debug_terminal_logging:
            for t in range(self.N):

                first_frame = max(t-2, 0)
                last_frame = t+2
                for i in range(self.A):
                    if self.terminals[t, i]:
                        time_1 = round(self.all_time[t, i]*args.timeout)
                        time_2 = round(self.all_time[t+1, i] * args.timeout)
                        self.export_debug_frames(
                            f"{args.log_folder}/{self.batch_counter:04}-{i:04}-{t:03} [{time_1:04}-{time_2:04}].png",
                            self.all_obs[first_frame:last_frame + 1, i].decompress(),
                            marker=t-first_frame+1
                        )

    @torch.no_grad()
    def get_value_estimates(self, obs: np.ndarray, time: Union[None, np.ndarray]=None,
                            horizons: Union[None, np.ndarray, int] = None,
                            return_transformed: bool = False,
                            ) -> np.ndarray:
        """
        Returns value estimates for each given observation
        If horizons are none current_horizon is used.
        obs: np array of dims [N, A, *state_shape]
        time: np array of dims [N, A]
        horizons:
            ndarray of dims [K] (returns NAK)
            integer (returns NA for horizon given
            none (returns NA for current horizon
        return_transformed: By default returns the true value, but can optionally return transformed value estimates.


        returns: ndarray of dims [N, A, K] if horizons was array else [N, A]
        """

        N, A, *state_shape = obs.shape

        if args.use_tvf:
            horizons = horizons if horizons is not None else self.current_horizon

            assert time is not None and time.shape == (N, A)
            if type(horizons) == int:
                pass
            elif type(horizons) is np.ndarray:
                assert len(horizons.shape) == 1, f"Invalid horizon shape {horizons.shape}"
            else:
                raise ValueError("Invalid horizon type {type(horizons)}")

            if type(horizons) in [int, float]:
                scalar_output = True
                horizons = np.asarray([horizons])
            else:
                scalar_output = False

            horizons = np.repeat(horizons[None, :], N * A, axis=0)
            time = time.reshape(N*A)

            model_out = self.forward(
                obs=obs.reshape([N * A, *state_shape]),
                aux_features=package_aux_features(horizons, time),
                output="value",
            )

            values = model_out["tvf_value"]

            if scalar_output:
                result = values.reshape([N, A]).cpu().numpy()
            else:
                result = values.reshape([N, A, horizons.shape[-1]]).cpu().numpy()
        else:
            assert horizons is None, "PPO only supports max horizon value estimates"
            model_out = self.forward(
                obs=obs.reshape([N * A, *state_shape]),
                output="value",
            )
            result = model_out["ext_value"].reshape([N, A]).cpu().numpy()

        if return_transformed:
            return result
        else:
            return ValueTransform.H_inv(result)


    @torch.no_grad()
    def log_value_quality(self, samples):
        """
        Writes value quality stats to log
        """

        if args.use_tvf:

            # first we generate the value estimates, then we calculate the returns required for each debug horizon
            # because we use sampling it is not guaranteed that these horizons will be included so we need to
            # recalculate everything

            agent_sample_count = np.clip(samples, 1, args.agents)
            agent_filter = np.random.choice(args.agents, agent_sample_count, replace=False)

            values = self.get_value_estimates(
                obs=self.prev_obs[:, agent_filter],
                time=self.prev_time[:, agent_filter],
                horizons=np.asarray(self.tvf_debug_horizons)
            )

            value_samples = self.generate_horizon_sample(
                self.current_horizon,
                args.tvf_value_samples,
                distribution=args.tvf_value_distribution,
                force_first_and_last=True,
            )

            targets = ValueTransform.H_inv(self.calculate_sampled_returns(
                value_sample_horizons=value_samples,
                required_horizons=self.tvf_debug_horizons,
                obs=self.all_obs[:, agent_filter],
                time=self.all_time[:, agent_filter],
                rewards=self.ext_rewards[:, agent_filter],
                dones=self.terminals[:, agent_filter],
                tvf_mode="nstep", # <-- MC is the least bias method we can do...
                tvf_n_step=self.current_horizon,
            ))

            total_not_explained_var = 0
            total_var = 0
            for index, h in enumerate(self.tvf_debug_horizons):
                value = values[:, :, index].reshape(-1)
                target = targets[:, :, index].reshape(-1)

                this_var = np.var(target)
                this_not_explained_var = np.var(target - value)
                total_var += this_var
                total_not_explained_var += this_not_explained_var

                ev = 0 if (this_var == 0) else np.clip(1 - this_not_explained_var / this_var, -1, 1)

                self.log.watch_mean(
                    f"ev_{h:04d}",
                    ev,
                    display_width=8 if h < 100 or h == args.tvf_max_horizon else 0,
                    history_length=1
                )
                # raw is RMS on unscaled error
                self.log.watch_mean(f"raw_{h:04d}", np.mean(np.square(self.reward_scale * (value - target)) ** 0.5),
                                    display_width=0, history_length=1)
                self.log.watch_mean(f"mse_{h:04d}", np.mean(np.square(value - target)),
                                    display_width=0, history_length=1)

            self.log.watch_mean(
                f"ev_average",
                0 if (total_var == 0) else np.clip(1 - total_not_explained_var / total_var, -1, 1),
                display_width=8,
                history_length=1
            )

        else:
            targets = calculate_mc_returns(
                self.ext_rewards, self.terminals, self.ext_value[self.N], self.gamma
            )
            values = self.ext_value[:self.N]
            self.log.watch_mean("ev_ext", utils.explained_variance(values.ravel(), targets.ravel()), history_length=1)

    @property
    def prev_obs(self):
        """
        Returns prev_obs with size [N,A] (i.e. missing final state)
        """
        return self.all_obs[:-1]

    @property
    def final_obs(self):
        """
        Returns final observation
        """
        return self.all_obs[-1]

    @property
    def prev_time(self):
        """
        Returns prev_time with size [N,A] (i.e. missing final state)
        """
        return self.all_time[:-1]

    def final_time(self):
        """
        Returns final time
        """
        return self.all_time[-1]

    def calculate_second_moment_estimate(
            self,
            rewards:np.ndarray,
            first_moment_estimates:np.ndarray,
            second_moment_estimates:np.ndarray,
            gamma: float
    ):
        """
        rewards: ndarray of dims N, A
        first_moment_estimates: ndarray of dims N+1, A
        second_moment_estimates: ndarray of dims N+1, A
        gamma: float
        """

        # based on https://jmlr.org/papers/volume17/14-335/14-335.pdf
        # the idea here is to learn the second moment, then use the first and second moments to estimate variance.
        # this is simply a td update, might extend to n-steps later on, if I can...

        return rewards**2 + 2*gamma*rewards * first_moment_estimates[1:] + (gamma**2)*second_moment_estimates[1:]

    def get_tvf_rediscounted_value_estimates(self):

        N, A, *state_shape = self.prev_obs.shape

        # if gamma's match we only need to generate the final horizon
        # if they don't we need to generate them all and rediscount
        if abs(self.tvf_gamma - self.gamma) < 1e-6:
            return self.get_value_estimates(obs=self.all_obs, time=self.all_time)
        else:
            # work out a range and skip to use so that we never use more than around 100 samples and we don't waste
            # samples on heavily discounted rewards
            if self.gamma >= 1:
                effective_horizon = round(self.current_horizon)
            else:
                effective_horizon = round(3 / (1 - self.gamma))

            # note: we could down sample the value estimates and adjust the gamma calculations if
            # this ends up being too slow..
            step_skip = math.ceil(effective_horizon / 100)

            # going backwards makes sure that final horizon is always included
            horizons = np.asarray(range(effective_horizon, 0, -step_skip))[::-1]

            value_estimates = self.get_value_estimates(
                obs=self.all_obs,
                time=self.all_time,
                horizons=horizons
            )

            return get_rediscounted_value_estimate(
                values=value_estimates.reshape([(N + 1) * A, -1]),
                old_gamma=self.tvf_gamma,
                new_gamma=self.gamma,
                horizons=horizons
            ).reshape([(N + 1), A])


    def calculate_returns(self):

        N, A, *state_shape = self.prev_obs.shape

        # generate a candidate gamma (if needed) that will be used to calculate this rounds advantages...
        # for score I'm using average reward. Using some return estimate is problematic as it will include gamma
        # and gamma has changed. This might make the algorithm prefer long horizons over short. Maybe this is ok though?
        if args.auto_strategy == "sa_reward":
            score = np.mean(self.ext_rewards)
            self.horizon_sa.process(score)
        if args.auto_strategy == "sa_return":
            # note: we can't use the return estimates below as these require a decision on gamma, which we need
            # to make before we calculate them. So instead we run through the rewards, discount then, then
            # add the final value.

            # note2: this might prefer longer horizons due to increased bootstrap value estimate and increased
            # rewards, but I'm ok with that I think, as it reduces the bias of the value estimates, and so long
            # as the update doesn't cause problems with the policy its fine.

            if args.use_tvf:
                batch_value_estimates = self.get_tvf_rediscounted_value_estimates()
            else:
                # in this case just generate ext value estimates from model
                batch_value_estimates = self.get_value_estimates(obs=self.all_obs)

            ext_advantage = calculate_gae(
                self.ext_rewards,
                batch_value_estimates[:N],
                batch_value_estimates[N],
                self.terminals,
                self.gamma,
                args.gae_lambda
            )

            batch_returns = ext_advantage + batch_value_estimates[:N]

            score = np.mean(batch_returns)
            self.horizon_sa.process(score)

        # 1. first we calculate the ext_value estimate
        if args.use_tvf:
            ext_value_estimates = self.get_tvf_rediscounted_value_estimates()
        else:
            # in this case just generate ext value estimates from model
            ext_value_estimates = self.get_value_estimates(obs=self.all_obs)

        if args.use_log_optimal:
            assert not args.use_tvf, "TVF not supported with log-optimal yet."
            # get square value estimates..., would be better to not have to forward this a second time

            with torch.no_grad():
                model_out = self.model.forward(self.all_obs.reshape([(N+1)*A, *state_shape]), output='value')
                sqr_value = model_out['sqr_value'].reshape([(N+1), A]).cpu().numpy()

            self.sqr_value = sqr_value[:-1]  # exclude final value estimate
            self.sqr_returns = self.calculate_second_moment_estimate(
                rewards=self.ext_rewards,
                first_moment_estimates=ext_value_estimates,
                second_moment_estimates=sqr_value,
                gamma=args.gamma
            )

        self.ext_value = ext_value_estimates

        # GAE requires inputs to be true value not transformed value...
        if args.tvf_gae and args.use_tvf:
            self.ext_advantage = calculate_gae_tvf(
                self.ext_rewards,
                ext_value_estimates[:N],
                ext_value_estimates[N],
                self.terminals,
                self.gamma,
                args.gae_lambda
            )
        else:
            self.ext_advantage = calculate_gae(
                self.ext_rewards,
                ext_value_estimates[:N],
                ext_value_estimates[N],
                self.terminals,
                self.gamma,
                args.gae_lambda
            )


        # calculate ext_returns for PPO targets
        self.ext_returns = ValueTransform.H(self.ext_advantage + ext_value_estimates[:N])

        # log-optimal adjustment
        if args.use_log_optimal:
            assert args.value_transform == "identity", "Log optimal will (probably) not work with value transforms."
            sqr_exp = (self.ext_value[:-1] ** 2)
            var = np.clip(self.sqr_value - sqr_exp, 0, float('inf'))

            ratios = var / (sqr_exp+1e-6)
            ratios = np.clip(ratios, -5, 5) # just so we don't get an overflow

            alpha = self.anneal(args.lo_alpha, enable=args.lo_alpha_anneal)

            rho = np.exp(-0.5 * alpha * ratios)

            self.log.watch("lo_rho_mean", np.mean(rho))
            self.log.watch("lo_std_mean", np.std(rho))

            rho = np.clip(rho, -5, 5)
            self.ext_advantage *= rho

        if args.use_intrinsic_rewards:
            # calculate the returns, but let returns propagate through terminal states.
            self.int_returns_raw = calculate_mc_returns(
                self.int_rewards,
                args.intrinsic_reward_propagation * self.terminals,
                self.int_final_value_estimate,
                args.gamma_int
            )

            if args.normalize_intrinsic_rewards:

                # normalize returns using EMS
                for t in range(self.N):
                    self.ems_norm = 0.99 * self.ems_norm + self.int_rewards[t, :]
                    self.intrinsic_returns_rms.update(self.ems_norm.reshape(-1))

                # normalize the intrinsic rewards
                # we multiply by 0.4 otherwise the intrinsic returns sit around 1.0, and we want them to be more like 0.4,
                # which is approximately where normalized returns will sit.
                self.intrinsic_reward_norm_scale = (1e-5 + self.intrinsic_returns_rms.var ** 0.5)
                self.int_rewards = self.int_rewards / self.intrinsic_reward_norm_scale * 0.4
            else:
                self.intrinsic_reward_norm_scale = 1

            self.int_returns = calculate_mc_returns(
                self.int_rewards,
                args.intrinsic_reward_propagation * self.terminals,
                self.int_final_value_estimate,
                args.gamma_int
            )

            self.int_advantage = calculate_gae(self.int_rewards, self.int_value, self.int_final_value_estimate, None,
                                               args.gamma_int)

        self.advantage = args.extrinsic_reward_scale * self.ext_advantage
        if args.use_intrinsic_rewards:
            self.advantage += args.intrinsic_reward_scale * self.int_advantage

        if args.normalize_observations:
            self.log.watch_mean("norm_scale_obs_mean", np.mean(self.model.obs_rms.mean), display_width=0)
            self.log.watch_mean("norm_scale_obs_var", np.mean(self.model.obs_rms.var), display_width=0)

        self.log.watch_mean("reward_scale", self.reward_scale, display_width=0)
        self.log.watch_mean("entropy_bonus", self.current_entropy_bonus, display_width=0, history_length=1)

        self.log.watch_mean("adv_mean", np.mean(self.advantage), display_width=0)
        self.log.watch_mean("adv_std", np.std(self.advantage), display_width=0)
        self.log.watch_mean("adv_max", np.max(self.advantage), display_width=0)
        self.log.watch_mean("adv_min", np.min(self.advantage), display_width=0)
        self.log.watch_mean("batch_reward_ext", np.mean(self.ext_rewards), display_name="rew_ext", display_width=0)
        self.log.watch_mean("batch_return_ext", np.mean(self.ext_returns), display_name="ret_ext")
        self.log.watch_mean("batch_return_ext_std", np.std(self.ext_returns), display_name="ret_ext_std",
                            display_width=0)
        # self.log.watch_mean("value_est_ext", np.mean(self.ext_value), display_name="est_v_ext", display_width=0)
        # self.log.watch_mean("value_est_ext_std", np.std(self.ext_value), display_name="est_v_ext_std", display_width=0)

        self.log.watch("game_crashes", self.game_crashes, display_width=0 if self.game_crashes == 0 else 8)
        self.log.watch("reward_clips", self.reward_clips, display_width=0 if self.reward_clips == 0 else 8)

        if args.use_tvf:
            self.log.watch("tvf_horizon", self.current_horizon)
            self.log.watch("tvf_gamma", self.tvf_gamma)

        self.log.watch("gamma", self.gamma, display_width=0)

        if not args.disable_ev:
            self.log_value_quality(samples=64)

        if args.use_intrinsic_rewards:
            self.log.watch_mean("batch_reward_int", np.mean(self.int_rewards), display_name="rew_int", display_width=0)
            self.log.watch_mean("batch_reward_int_std", np.std(self.int_rewards), display_name="rew_int_std",
                                display_width=0)
            self.log.watch_mean("batch_return_int", np.mean(self.int_returns), display_name="ret_int")
            self.log.watch_mean("batch_return_int_std", np.std(self.int_returns), display_name="ret_int_std",
                                display_width=0)
            self.log.watch_mean("batch_return_int_raw_mean", np.mean(self.int_returns_raw),
                                display_name="ret_int_raw_mu",
                                display_width=0)
            self.log.watch_mean("batch_return_int_raw_std", np.std(self.int_returns_raw),
                                display_name="ret_int_raw_std",
                                display_width=0)

            self.log.watch_mean("value_est_int", np.mean(self.int_value), display_name="est_v_int", display_width=0)
            self.log.watch_mean("value_est_int_std", np.std(self.int_value), display_name="est_v_int_std",
                                display_width=0)
            self.log.watch_mean("ev_int", utils.explained_variance(self.int_value.ravel(), self.int_returns.ravel()))
            if args.use_rnd:
                self.log.watch_mean("batch_reward_int_unnorm", np.mean(self.int_rewards), display_name="rew_int_unnorm",
                                    display_width=0, display_priority=-2)
                self.log.watch_mean("batch_reward_int_unnorm_std", np.std(self.int_rewards),
                                    display_name="rew_int_unnorm_std",
                                    display_width=0)

        if args.normalize_intrinsic_rewards:
            self.log.watch_mean("norm_scale_int", self.intrinsic_reward_norm_scale, display_width=0)

    def train_rnd_minibatch(self, data, zero_grad=True, apply_update=True, loss_scale=1.0):

        raise Exception("Not implemented yet")

        # mini_batch_size = len(data["prev_state"])
        #
        # # -------------------------------------------------------------------------
        # # Calculate loss_rnd
        # # -------------------------------------------------------------------------
        #
        # if args.use_rnd:
        #     # learn prediction slowly by only using some of the samples... otherwise it learns too quickly.
        #     predictor_proportion = np.clip(32 / args.agents, 0.01, 1)
        #     n = int(len(prev_states) * predictor_proportion)
        #     loss_rnd = -self.model.prediction_error(prev_states[:n]).mean()
        #     loss = loss + loss_rnd
        #
        #     self.log.watch_mean("loss_rnd", loss_rnd)
        #
        #     self.log.watch_mean("feat_mean", self.model.features_mean, display_width=0)
        #     self.log.watch_mean("feat_var", self.model.features_var, display_width=10)
        #     self.log.watch_mean("feat_max", self.model.features_max, display_width=10, display_precision=1)

    def optimizer_step(self, optimizer: torch.optim.Optimizer, label: str = "opt"):

        # get parameters
        parameters = []
        for group in optimizer.param_groups:
            for p in group['params']:
                if p.grad is not None:
                    parameters.append(p)

        if args.max_grad_norm is not None and args.max_grad_norm != 0:
            grad_norm = nn.utils.clip_grad_norm_(parameters, args.max_grad_norm)
        else:
            # even if we don't clip the gradient we should at least log the norm. This is probably a bit slow though.
            # we could do this every 10th step, but it's important that a large grad_norm doesn't get missed.
            grad_norm = 0
            for p in parameters:
                param_norm = p.grad.data.norm(2)
                grad_norm += param_norm.item() ** 2
            grad_norm = grad_norm ** 0.5

        self.log.watch_mean(f"grad_{label}", grad_norm)

        optimizer.step()

        return float(grad_norm)

    def calculate_value_loss(self, targets:torch.tensor, predictions:torch.tensor, horizons:torch.tensor = None):
        """
        Calculate loss between predicted value and actual value

        targets: tensor of dims [N, A, H]
        predictions: tensor of dims [N, A, H]
        horizons: tensor of dims [N, A, H] of type int (optional)

        returns: loss as a tensor of dims [N, A]

        """

        if args.tvf_loss_fn == "MSE":
            # MSE loss, sum across samples
            return torch.square(targets - predictions)
        elif args.tvf_loss_fn == "huber":
            if args.huber_loss_delta == 0:
                loss = torch.abs(targets - predictions)
            else:
                # Smooth huber loss
                # see https://en.wikipedia.org/wiki/Huber_loss
                errors = targets - predictions
                loss = args.huber_loss_delta ** 2 * (
                        torch.sqrt(1 + (errors / args.huber_loss_delta) ** 2) - 1
                )
            return loss
        elif args.tvf_loss_fn == "h_weighted":

            assert args.value_transform == "identity", "h_weighting will not work properly with transformed value."

            if horizons is None:
                # assume h = args.tvf_max_horizon for all elements.
                return torch.square(targets - predictions) / args.tvf_max_horizon

            # we need to remove all zero horizons from this calculation as they will result in NaN
            horizons = horizons[0]
            assert torch.abs(horizons - horizons[np.newaxis, :]).sum() == 0, "Batch entries must have matched horizons."
            non_zero_horizon_ids = [i for i in range(len(horizons)) if horizons[i] != 0]
            horizons = horizons[non_zero_horizon_ids]

            av_targets = targets[:, non_zero_horizon_ids] / (horizons / args.tvf_max_horizon)
            av_predictions = predictions[:, non_zero_horizon_ids] / (horizons / args.tvf_max_horizon)
            # scale so that loss scale is roughly the same as MSE loss
            return torch.square(av_targets - av_predictions) / args.tvf_max_horizon
        else:
            raise ValueError(f"Invalid tvf_loss_fn {args.tvf_loss_fn}")

    def train_distill_minibatch(self, data, loss_scale=1.0):

        loss: torch.Tensor = torch.tensor(0, dtype=torch.float32, device=self.model.device)

        if not args.use_tvf or args.tvf_force_ext_value_distill:
            # method 1. just learn the final value
            model_out = self.model.forward(data["prev_state"], output="policy")
            targets = data["value_targets"]
            predictions = model_out["ext_value"]
            loss = loss + 0.5 * self.calculate_value_loss(targets, predictions).mean()
        else:
            # method 2. try to learn the entire curve...
            aux_features = package_aux_features(
                data["tvf_horizons"],
                data["tvf_time"]
            )
            model_out = self.model.forward(data["prev_state"], output="policy", aux_features=aux_features)
            targets = data["value_targets"]
            predictions = model_out["tvf_value"]
            loss = loss + 0.5 * self.calculate_value_loss(targets, predictions, data["tvf_horizons"]).mean()

        old_policy_logprobs = data["old_log_policy"]
        logps = model_out["log_policy"]

        # KL loss
        kl_true = F.kl_div(old_policy_logprobs, logps, log_target=True, reduction="batchmean")
        loss = loss + args.distill_beta * kl_true

        # -------------------------------------------------------------------------
        # Generate Gradient
        # -------------------------------------------------------------------------

        opt_loss = loss * loss_scale
        opt_loss.backward()

        self.log.watch_mean("loss_distill", loss, history_length=64, display_width=8)

    @property
    def value_heads(self):
        """
        Returns a list containing value heads that need to be calculated.
        """
        value_heads = []
        if not args.use_tvf:
            value_heads.append("ext")
        if args.use_intrinsic_rewards:
            value_heads.append("int")
        if args.use_log_optimal:
            value_heads.append("sqr")
        return value_heads


    def train_value_minibatch(self, data, loss_scale=1.0):

        loss: torch.Tensor = torch.tensor(0, dtype=torch.float32, device=self.model.device)

        # create additional args if needed
        kwargs = {}
        if args.use_tvf:
            # horizons are B, H, and times is B so we need to adjust them
            kwargs['aux_features'] = package_aux_features(
                data["tvf_horizons"],
                data["tvf_time"]
            )

        model_out = self.model.forward(data["prev_state"], output="value", **kwargs)

        # -------------------------------------------------------------------------
        # Calculate loss_value_function_horizons
        # -------------------------------------------------------------------------

        if args.use_tvf:
            # targets "tvf_returns" are [B, K]
            # predictions "tvf_value" are [B, K]
            # predictions need to be generated... this could take a lot of time so just sample a few..
            targets = data["tvf_returns"]
            value_predictions = model_out["tvf_value"]

            if args.tvf_soft_anchor > 0:
                assert torch.all(data["tvf_horizons"][:, 0] == 0)
                anchor_loss = 0.5 * args.tvf_soft_anchor * torch.mean(torch.square(value_predictions[:,  0]))
                self.log.watch_mean("loss_anchor", anchor_loss, display_width=8)
                loss = loss + anchor_loss

            tvf_loss = self.calculate_value_loss(targets, value_predictions, data["tvf_horizons"])

            # zero out loss for 0 horizon in this special case
            if args.tvf_soft_anchor == -1:
                tvf_loss = tvf_loss * torch.not_equal(data["tvf_horizons"], 0)

            tvf_loss = tvf_loss.mean(dim=-1)

            tvf_loss = 0.5 * args.tvf_coef * tvf_loss.mean()
            loss = loss + tvf_loss

            self.log.watch_mean("loss_tvf", tvf_loss, history_length=64, display_width=8)

        # -------------------------------------------------------------------------
        # Calculate loss_value
        # -------------------------------------------------------------------------

        loss = loss + self.train_value_heads(model_out, data)

        # -------------------------------------------------------------------------
        # Generate Gradient
        # -------------------------------------------------------------------------

        opt_loss = loss * loss_scale
        opt_loss.backward()

        # -------------------------------------------------------------------------
        # Logging
        # -------------------------------------------------------------------------

        self.log.watch_mean("loss_value", loss)

        return {}

    def generate_horizon_sample(
            self,
            max_value: int,
            samples: int,
            distribution: str = "linear",
            force_first_and_last: bool = False) -> np.ndarray:
        """
        generates random samples from 0 to max (inclusive) using sampling with replacement
        and always including the first and last value
        distribution is the distribution to sample from
        force_first_and_last: if true horizon 0 and horizon max_value will always be included.
        output is always sorted.

        Note: fixed_geometric may return less than the expected number of samples.

        """

        if samples == -1 or samples >= (max_value + 1):
            return np.arange(0, max_value + 1)

        # these distributions don't require random sampling, and always include first and last by default.
        if distribution == "fixed_linear":
            samples = np.linspace(0, max_value, num=samples, endpoint=True)
        elif distribution == "fixed_geometric":
            # note: round is needed here otherwise last value will sometimes be off by 1.
            samples = np.geomspace(1, 1+max_value, num=samples, endpoint=True)-1
        elif distribution == "linear":
            samples = np.random.choice(range(1, max_value), size=samples, replace=False)
        elif distribution == "geometric":
            samples = np.random.uniform(np.log(1), np.log(max_value+1), size=samples)
            samples = np.exp(samples)-1
        else:
            raise Exception(f"Invalid distribution {distribution}")

        if force_first_and_last:
            samples[0] = 0
            samples[-1] = max_value
        samples.sort()
        return np.rint(samples).astype(int)

    def generate_all_returns(self):
        """
        Generates return estimates for current batch of data.
        This is the old algorithm with no sampling, and locked to nstep returns.
        """

        assert args.tvf_mode == "nstep", "Only nstep returns supported with tvf_value_samples=-1 at the moment."

        value_estimates = self.get_value_estimates(
            obs=self.all_obs,
            time=self.all_time,
            horizons=np.arange(0, self.current_horizon + 1),
            return_transformed=True,
        )

        returns = calculate_tvf_n_step(
                rewards=self.ext_rewards,
                dones=self.terminals,
                values=value_estimates[:-1],
                final_value_estimates=value_estimates[-1],
                gamma=self.tvf_gamma,
                n_step=args.tvf_n_step,
        )

        horizons = np.arange(0, self.current_horizon+1)[None, None, :]
        horizons = np.repeat(horizons, self.N, axis=0)
        horizons = np.repeat(horizons, self.A, axis=1)

        return returns, horizons


    @torch.no_grad()
    def generate_return_sample(self, force_first_and_last: bool = False):
        """
        Generates return estimates for current batch of data.

        force_first_and_last: if true always includes first and last horizon

        Note: could roll this into calculate_sampled_returns, and just have it return the horizons aswell?

        returns:
            returns: ndarray of dims [N,A,K] containing the return estimates using tvf_gamma discounting
            horizon_samples: ndarray of dims [N,A,K] containing the horizons used
        """


        # max horizon to train on
        H = self.current_horizon
        N, A, *state_shape = self.prev_obs.shape

        horizon_samples = self.generate_horizon_sample(
            H,
            args.tvf_horizon_samples,
            distribution=args.tvf_horizon_distribution,
            force_first_and_last=force_first_and_last,
        )
        if args.tvf_value_samples == -1:
            # this uses the old algorithm with no value sampling
            returns, horizons = self.generate_all_returns()
            return returns[..., horizon_samples], horizons[..., horizon_samples]

        value_samples = self.generate_horizon_sample(
            H,
            args.tvf_value_samples,
            distribution=args.tvf_value_distribution,
            force_first_and_last=True
        )

        returns = self.calculate_sampled_returns(
            value_sample_horizons=value_samples,
            required_horizons=horizon_samples,
        )

        horizon_samples = horizon_samples[None, None, :]
        horizon_samples = np.repeat(horizon_samples, N, axis=0)
        horizon_samples = np.repeat(horizon_samples, A, axis=1)
        return returns, horizon_samples

    @property
    def current_entropy_bonus(self):
        t = self.step / 10e6
        return args.entropy_bonus * 10 ** (args.eb_alpha * -math.cos(args.eb_theta*t*math.pi*2) + args.eb_beta * t)

    def train_value_heads(self, model_out, data):
        """
        Calculates loss for each value head, then returns their sum.
        """
        loss = torch.tensor(0, dtype=torch.float32, device=self.model.device)
        for value_head in self.value_heads:
            value_prediction = model_out["{}_value".format(value_head)]
            returns = data["{}_returns".format(value_head)]

            if args.use_clipped_value_loss:
                old_pred_values = data["{}_value".format(value_head)]
                # is is essentially trust region for value learning, and seems to help a lot.
                value_prediction_clipped = old_pred_values + torch.clamp(value_prediction - old_pred_values,
                                                                         -args.ppo_epsilon, +args.ppo_epsilon)
                vf_losses1 = (value_prediction - returns).pow(2)
                vf_losses2 = (value_prediction_clipped - returns).pow(2)
                loss_value = torch.mean(torch.max(vf_losses1, vf_losses2))
            else:
                # simpler version, just use MSE.
                vf_losses1 = (value_prediction - returns).pow(2)
                loss_value = torch.mean(vf_losses1)
            loss_value = loss_value * args.vf_coef
            self.log.watch_mean("loss_v_" + value_head, loss_value, history_length=64)
            loss = loss + loss_value
        return loss

    def train_policy_minibatch(self, data, loss_scale=1.0):

        mini_batch_size = len(data["prev_state"])

        loss = torch.tensor(0, dtype=torch.float32, device=self.model.device)

        prev_states = data["prev_state"]
        actions = data["actions"].to(torch.long)
        old_policy_logprobs = data["log_policy"]
        advantages = data["advantages"]

        model_out = self.model.forward(prev_states, output="policy")

        # -------------------------------------------------------------------------
        # Calculate loss_pg
        # -------------------------------------------------------------------------

        logps = model_out["log_policy"]

        logpac = logps[range(mini_batch_size), actions]
        old_logpac = old_policy_logprobs[range(mini_batch_size), actions]
        ratio = torch.exp(logpac - old_logpac)

        if args.use_tanh_clipping:
            # soft clipping
            factor = 1/args.ppo_epsilon
            clipped_ratio = torch.tanh(factor*(ratio-1))/factor + 1.0
        else:
            clipped_ratio = torch.clamp(ratio, 1 - args.ppo_epsilon, 1 + args.ppo_epsilon)

        loss_clip = torch.min(ratio * advantages, clipped_ratio * advantages)
        loss_clip_mean = loss_clip.mean()
        loss = loss + loss_clip_mean

        # approx kl
        # this is from https://stable-baselines.readthedocs.io/en/master/_modules/stable_baselines/ppo2/ppo2.html
        # but https://github.com/openai/spinningup/blob/master/spinup/algos/pytorch/ppo/ppo.py
        # uses approx_kl = (b_logprobs[minibatch_ind] - newlogproba).mean() which I think is wrong
        # anyway, why not just calculate the true kl?

        # ok, I figured this out, we want
        # sum_x P(x) log(P/Q)
        # our actions were sampled from the policy so we have
        # pi(expected_action) = sum_x P(x) then just mulitiply this by log(P/Q) which is log(p)-log(q)
        # this means the spinning up version is right.

        with torch.no_grad():
            clip_frac = torch.gt(torch.abs(ratio - 1.0), args.ppo_epsilon).float().mean()
            kl_approx = (old_logpac - logpac).mean()
            kl_true = F.kl_div(old_policy_logprobs, logps, log_target=True, reduction="batchmean")

        # -------------------------------------------------------------------------
        # Value learning for PPO mode
        # -------------------------------------------------------------------------

        if args.architecture == "single":
            # negative because we're doing gradient ascent.
            loss = loss - self.train_value_heads(model_out, data)

        # -------------------------------------------------------------------------
        # Calculate loss_entropy
        # -------------------------------------------------------------------------

        entropy = -(logps.exp() * logps).sum(axis=1)
        entropy = entropy.mean()
        loss_entropy = entropy * self.current_entropy_bonus
        loss = loss + loss_entropy

        # -------------------------------------------------------------------------
        # Calculate gradients
        # -------------------------------------------------------------------------

        opt_loss = loss * -loss_scale
        opt_loss.backward()

        # -------------------------------------------------------------------------
        # Generate log values
        # -------------------------------------------------------------------------

        self.log.watch_mean("loss_pg", loss_clip_mean, history_length=64)
        self.log.watch_mean("kl_approx", kl_approx, display_width=0)
        self.log.watch_mean("kl_true", kl_true, display_width=8)
        self.log.watch_mean("clip_frac", clip_frac, display_width=8)
        self.log.watch_mean("entropy", entropy)
        self.log.watch_mean("loss_ent", loss_entropy)
        self.log.watch_mean("loss_policy", loss)

        return {
            'kl_approx': float(kl_approx.detach()),  # make sure we don't pass the graph through.
            'kl_true': float(kl_true.detach()),
            'clip_frac': float(clip_frac.detach()),
        }

    @property
    def training_fraction(self):
        return (self.step / 1e6) / args.epochs

    @property
    def episode_length_mean(self):
        return np.mean(self.episode_length_buffer)

    @property
    def episode_length_std(self):
        return np.std(self.episode_length_buffer)

    @property
    def agent_age(self):
        """
        Approximate age of agent in terms of environment steps.
        Measure individual agents age, so if 128 agents each walk 10 steps, agents will be 10 steps old, not 1280.
        """
        return self.step / args.agents

    @property
    def _auto_horizon(self):
        if args.auto_strategy == "episode_length":
            if len(self.episode_length_buffer) == 0:
                auto_horizon = 0
            else:
                auto_horizon = self.episode_length_mean + (2 * self.episode_length_std)
            return auto_horizon
        elif args.auto_strategy == "agent_age_slow":
            return (1/1000) * self.step # todo make this a parameter
        elif args.auto_strategy in ["sa_return", "sa_reward"]:
            return self.horizon_sa.neighbour
        else:
            raise ValueError(f"Invalid auto_strategy {args.auto_strategy}")

    @property
    def _auto_gamma(self):
        horizon = float(np.clip(self._auto_horizon, 10, float("inf")))
        return 1 - (1 / horizon)

    @property
    def current_horizon(self):
        if args.auto_horizon:
            min_horizon = max(128, args.tvf_horizon_samples, args.tvf_value_samples)
            return int(np.clip(self._auto_horizon*3, min_horizon, args.tvf_max_horizon))
        else:
            return int(args.tvf_max_horizon)

    @property
    def gamma(self):
        if args.auto_gamma in ["gamma", "both"]:
            return self._auto_gamma
        else:
            return args.gamma

    @property
    def tvf_gamma(self):
        if args.auto_gamma in ["tvf", "both"]:
            return self._auto_gamma
        else:
            return args.tvf_gamma

    @property
    def reward_scale(self):
        """ The amount rewards have been scaled by. """
        if args.reward_normalization:
            norm_wrapper = wrappers.get_wrapper(self.vec_env, wrappers.VecNormalizeRewardWrapper)
            return norm_wrapper.std
        else:
            return 1.0


    def train_policy(self):

        # ----------------------------------------------------
        # policy phase

        batch_data = {}
        B = args.batch_size
        N, A, *state_shape = self.prev_obs.shape

        batch_data["prev_state"] = self.prev_obs.reshape([B, *state_shape])
        batch_data["actions"] = self.actions.reshape(B).astype(np.long)
        batch_data["log_policy"] = self.log_policy.reshape([B, *self.policy_shape])

        if args.architecture == "single":
            # ppo trains value during policy update
            batch_data["ext_returns"] = self.ext_returns.reshape([B])
            if args.use_log_optimal:
                batch_data["sqr_returns"] = self.sqr_returns.reshape([B])

        if args.normalize_advantages:
            # we should normalize at the mini_batch level, but it's so much easier to do this at the batch level.
            advantages = self.advantage.reshape(B)
            batch_data["advantages"] = (advantages - advantages.mean()) / (advantages.std() + 1e-8)
        else:
            batch_data["advantages"] = self.advantage.reshape(B)

        if args.use_intrinsic_rewards:
            batch_data["int_returns"] = self.int_returns.reshape(B)
            batch_data["int_value"] = self.int_value.reshape(B)

        policy_epochs = 0
        for _ in range(args.policy_epochs):
            results = self.train_batch(
                batch_data=batch_data,
                mini_batch_func=self.train_policy_minibatch,
                mini_batch_size=args.policy_mini_batch_size,
                optimizer=self.policy_optimizer,
                label="policy",
                hooks={
                    'after_mini_batch': lambda x: x["outputs"][-1]["kl_approx"] > 1.5 * args.target_kl
                } if args.target_kl > 0 else {}
            )
            expected_mini_batches = (args.batch_size / args.policy_mini_batch_size)
            policy_epochs += results["mini_batches"] / expected_mini_batches
            if "did_break" in results:
                break
        self.log.watch_full("policy_epochs", policy_epochs, display_width=8)

    def train_value(self):

        # ----------------------------------------------------
        # value phase

        batch_data = {}
        B = args.batch_size
        N, A, *state_shape = self.prev_obs.shape

        batch_data["prev_state"] = self.prev_obs.reshape([B, *state_shape])
        batch_data["ext_returns"] = self.ext_returns.reshape(B)

        if args.use_intrinsic_rewards:
            batch_data["int_returns"] = self.int_returns.reshape(B)
            batch_data["int_value"] = self.int_value.reshape(B)

        if args.use_log_optimal:
            batch_data["sqr_returns"] = self.sqr_returns.reshape(B)
            batch_data["sqr_value"] = self.sqr_value.reshape(B)

        if (not args.use_tvf) and args.use_clipped_value_loss:
            # these are needed if we are using the clipped value objective...
            batch_data["ext_value"] = self.get_value_estimates(obs=self.prev_obs).reshape(B)

        for value_epoch in range(args.value_epochs):

            if args.use_tvf:
                # we do this once at the start, generate one returns estimate for each epoch on different horizons then
                # during epochs take a random mixture from this. This helps shuffle the horizons, and also makes sure that
                # we don't drift, as the updates will modify our model and change the value estimates.
                # it is possible that instead we should be updating our return estimates as we go though
                if value_epoch == 0:
                    returns, horizons = self.generate_return_sample(force_first_and_last=True)
                    batch_data["tvf_returns"] = returns.reshape([B, -1])
                    batch_data["tvf_horizons"] = horizons.reshape([B, -1])
                    batch_data["tvf_time"] = self.prev_time.reshape([B])

            self.train_batch(
                batch_data=batch_data,
                mini_batch_func=self.train_value_minibatch,
                mini_batch_size=args.value_mini_batch_size,
                optimizer=self.value_optimizer,
                label="value",
            )

    def train_distill(self):

        # ----------------------------------------------------
        # distill phase

        if args.distill_epochs == 0:
            return

        batch_data = {}
        B = args.batch_size
        N, A, *state_shape = self.prev_obs.shape

        batch_data["prev_state"] = self.prev_obs.reshape([B, *state_shape])

        if args.use_tvf and not args.tvf_force_ext_value_distill:
            horizons = self.generate_horizon_sample(self.current_horizon, args.tvf_horizon_samples,
                                                    args.tvf_horizon_distribution)
            H = len(horizons)
            target_values = self.get_value_estimates(
                obs=self.prev_obs,
                time=self.prev_time,
                horizons=horizons,
                return_transformed=True,
            ).reshape([B, H])

            batch_data["tvf_horizons"] = expand_to_na(N, A, horizons).reshape([B, H])
            batch_data["tvf_time"] = self.prev_time.reshape([B])
            batch_data["value_targets"] = target_values.reshape([B, H])
        else:
            target_values = self.get_value_estimates(
                obs=self.prev_obs, time=self.prev_time,
                return_transformed=True,
            ).reshape([B])
            batch_data["value_targets"] = target_values.reshape([B])

        with torch.no_grad():
            model_out = self.forward(
                obs=self.prev_obs.reshape([B, *state_shape]), output="policy"
            )
        batch_data["old_log_policy"] = model_out["log_policy"].detach().cpu().numpy()

        for distill_epoch in range(args.distill_epochs):
            # we do this here so it is only run if there is at least one epoch
            # also regenerating targets each epoch is a good idea.

            self.train_batch(
                batch_data=batch_data,
                mini_batch_func=self.train_distill_minibatch,
                mini_batch_size=args.value_mini_batch_size,
                optimizer=self.distill_optimizer,
                label="distill",
            )

    def train(self, step):

        self.step = step

        self.update_learning_rates()

        self.train_policy()

        if args.architecture == "dual":
            # value learning is handled with policy in PPO mode.
            self.train_value()
            self.train_distill()

        # todo: include rnd ...

        self.batch_counter += 1

    def train_batch(
            self,
            batch_data,
            mini_batch_func,
            mini_batch_size,
            optimizer: torch.optim.Optimizer,
            label,
            hooks: Union[dict, None] = None) -> dict:
        """
        Trains agent policy on current batch of experience
        Returns context with
            'mini_batches' number of mini_batches completed
            'outputs' output from each mini_batch update
            'did_break'=True (only if training terminated early)
        """

        mini_batches = args.batch_size // mini_batch_size
        micro_batch_size = min(args.max_micro_batch_size, mini_batch_size)
        micro_batches = mini_batch_size // micro_batch_size

        ordering = list(range(args.batch_size))
        np.random.shuffle(ordering)

        micro_batch_counter = 0
        outputs = []

        context = {}

        for j in range(mini_batches):

            optimizer.zero_grad(set_to_none=True)

            for k in range(micro_batches):
                # put together a micro_batch.
                batch_start = micro_batch_counter * micro_batch_size
                batch_end = (micro_batch_counter + 1) * micro_batch_size
                sample = ordering[batch_start:batch_end]
                micro_batch_counter += 1

                minibatch_data = {}
                for var_name, var_value in batch_data.items():
                    data = var_value[sample]
                    if data.dtype == np.object:
                        # handle decompression
                        data = np.asarray([data[i].decompress() for i in range(len(data))])
                    minibatch_data[var_name] = torch.from_numpy(data).to(self.model.device)

                outputs.append(mini_batch_func(
                    minibatch_data, loss_scale=1 / micro_batches
                ))

            context = {
                'mini_batches': j + 1,
                'outputs': outputs
            }

            if hooks is not None and "after_mini_batch" in hooks:
                if hooks["after_mini_batch"](context):
                    context["did_break"] = True
                    break

            self.optimizer_step(optimizer=optimizer, label=label)

        return context


def get_rediscounted_value_estimate(values: Union[np.ndarray, torch.Tensor], old_gamma: float, new_gamma: float, horizons):
    """
    Returns rediscounted return at horizon H

    values: float tensor of shape [B, H]
    returns float tensor of shape [B]
    """

    B, H = values.shape

    if old_gamma == new_gamma:
        return values[:, -1]

    if type(values) is np.ndarray:
        values = torch.from_numpy(values)
        is_numpy = True
    else:
        is_numpy = False

    device = values.device
    prev = torch.zeros([B], dtype=torch.float32, device=device)
    discounted_reward_sum = torch.zeros([B], dtype=torch.float32, device=device)
    for i, h in enumerate(horizons):
        reward = (values[:, i] - prev) / (old_gamma ** h)
        prev = values[:, i]
        discounted_reward_sum += reward * (new_gamma ** h)

    return discounted_reward_sum.numpy() if is_numpy else discounted_reward_sum

def package_aux_features(horizons: Union[np.ndarray, torch.Tensor], time: Union[np.ndarray, torch.Tensor]):
    """
    Return aux features for given horizons and time fraction.

    horizons: [B, H]
    time: [B]

    """

    B, H = horizons.shape
    assert time.shape == (B,)

    # horizons might be int16, so cast it to float.
    if type(horizons) is np.ndarray:
        assert type(time) == np.ndarray
        horizons = horizons.astype(np.float32)
        aux_features = np.concatenate([
            horizons.reshape([B, H, 1]),
            np.repeat(time.reshape([B, 1, 1]), H, axis=1)
        ], axis=-1)
    elif type(horizons) is torch.Tensor:
        assert type(time) == torch.Tensor
        horizons = horizons.to(dtype=torch.float32)
        aux_features = torch.cat([
            horizons.reshape([B, H, 1]),
            torch.repeat_interleave(time.reshape([B, 1, 1]), H, dim=1)
        ], dim=-1)
    else:
        raise TypeError("Input must be of type np.ndarray or torch.Tensor")

    return aux_features


def _scale_function(x, method):
    if method == "default":
        return x / args.tvf_max_horizon
    elif method == "zero":
        return x*0
    elif method == "log":
        return (1+x).log2()
    elif method == "sqrt":
        return x.sqrt()
    elif method == "centered":
        # this will be roughly unit normal
        return ((x / args.tvf_max_horizon) - 0.5) * 3.0
    elif method == "wide":
        # this will be roughly 10x normal
        return ((x / args.tvf_max_horizon) - 0.5) * 30.0
    elif method == "wider":
        # this will be roughly 30x normal
        return ((x / args.tvf_max_horizon) - 0.5) * 100.0
    else:
        raise ValueError(f"Invalid scale mode {method}. Please use [zero|log|sqrt|centered|wide|wider]")

def horizon_scale_function(x):
    return _scale_function(x, args.tvf_horizon_scale)

def time_scale_function(x):
    return _scale_function(x, args.tvf_time_scale)

def expand_to_na(n,a,x):
    """
    takes 1d input and returns it duplicated [N,A] times
    in form [n, a, *]
    """
    x = x[None, None, :]
    x = np.repeat(x, n, axis=0)
    x = np.repeat(x, a, axis=1)
    return x

def expand_to_h(h,x):
    """
    takes 2d input and returns it duplicated [H] times
    in form [*, *, h]
    """
    x = x[:, :, None]
    x = np.repeat(x, h, axis=2)
    return x

class ValueTransform():

    TRANSFORM_EPSILON = 1e-2

    @staticmethod
    def H(x):
        if args.value_transform == "identity":
            return x
        elif args.value_transform == "sqrt":
            # formula was designed to handle large returns, but mine are scaled, so scale up by 1000
            # as ext_value is ~1.2, but returns in games are usually ~1000
            return np.sign(x) * (np.sqrt(np.abs(x) + 1) - 1) + ValueTransform.TRANSFORM_EPSILON * x

    @staticmethod
    def H_inv(x):
        if args.value_transform == "identity":
            return x
        elif args.value_transform == "sqrt":
            # from https://openreview.net/pdf?id=Sye57xStvB (but with corrected square...)
            return np.sign(x) * (((np.sqrt(
                1 + (4 * ValueTransform.TRANSFORM_EPSILON) * (np.abs(x) + 1 + ValueTransform.TRANSFORM_EPSILON)) - 1) / (
                                              2 * ValueTransform.TRANSFORM_EPSILON)) ** 2 - 1)
