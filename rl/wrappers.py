import gym
import math
import numpy as np
import cv2
import hashlib
import collections
from gym.envs.atari import AtariEnv
from . import utils

from gym.vector import VectorEnv

from typing import Union

class EpisodicDiscounting(gym.Wrapper):
    """
    Applies discounting at the episode level
    """

    def __init__(self, env: gym.Env, discount_type, discount_gamma):
        super().__init__(env)
        self.env = env
        self.t = 0
        self.discount_type = discount_type
        self.discount_gamma = discount_gamma

    @staticmethod
    def get_discount(i:float, gamma:float, discount_type:str):
        """
        Returns discount (gamma_i) for reward (r_i), with discounting parameter gamma.
        """

        if discount_type == "finite":
            m = 1/(1-gamma)
            discount = 1.0 if i <= m else 0
        elif discount_type == "geometric":
            discount = gamma ** i
        elif discount_type == "quadratic":
            discount = 1 / (i*(i+1))
        elif discount_type == "power": # also called hyperbolic
            epsilon = 1e-1
            discount = i ** (-1-epsilon) # minus epsilon so sequence converges
        elif discount_type == "harmonic":
            discount = 1 / (i * (math.log(i)**2))
        elif discount_type == "none":
            discount = 1.0
        else:
            raise ValueError(f"Invalid discount_type {discount_type}")
        return discount

    @staticmethod
    def get_normalization_constant(k:np.ndarray, gamma:float, discount_type:str):
        if discount_type == "finite":
            m = 1/(1-gamma)
            steps_remaining = (m-k)
            steps_remaining = np.clip(steps_remaining, 0, float('inf')) # make sure steps remaining is not negative
            normalizer = steps_remaining+1
        elif discount_type == "geometric":
            normalizer = (gamma ** k) / (1-gamma)
        elif discount_type == "quadratic":
            normalizer = 1 / k
        elif discount_type == "power": # also called hyperbolic
            epsilon = 1e-1
            normalizer = (1 / epsilon) * (k ** -epsilon)
        elif discount_type == "harmonic":
            normalizer = 1 / np.log(k)
        elif discount_type == "none":
            normalizer = 1.0
        else:
            raise ValueError(f"Invalid discount_type {discount_type}")
        return 1 / normalizer

    def reset(self):
        self.t = 0
        return self.env.reset()

    def step(self, action):
        obs, reward, done, info = self.env.step(action)

        self.t += 1
        discount = EpisodicDiscounting.get_discount(self.t, self.discount_gamma, self.discount_type)
        reward *= discount

        return obs, reward, done, info

    def save_state(self, buffer):
        buffer["t"] = self.t

    def restore_state(self, buffer):
        self.t = buffer["t"]

class NoPassThruWrapper(gym.Wrapper):
    """
    Always returns first state after reset. Can be used to debug performance hit from running environment / wrappers.
    """

    def __init__(self, env: gym.Env):
        super().__init__(env)
        self.first = False

    def reset(self):
        self.obs = self.env.reset()
        self.first = True
        return self.obs

    def step(self, action):
        if self.first:
            self.obs, _, _, self.info = self.env.step(action)
            self.first = False
        return self.obs, 0, False, self.info

class TimeAwareWrapper(gym.Wrapper):
    """
    Includes time on frame of last channel of observation (which is last state if using stacking)
    Observational spaces should be 2d image in format

    [..., C, H, W]
    """

    def __init__(self, env: gym.Env):
        super().__init__(env)

    def reset(self, **kwargs):
        obs = self.env.reset(**kwargs)
        return self._process_obs(obs, 0)

    def _process_obs(self, obs, time_frac):
        assert obs.dtype == np.uint8
        *_, C, H, W = obs.shape

        x_point = 3 + (W-6) * time_frac

        obs[..., 0, -4:, :] = 0
        obs[..., 0, -3:-1, 3:-3] = 64
        obs[..., 0, -3:-1, 3:math.floor(x_point)] = 255
        obs[..., 0, -3:-1, math.floor(x_point)] = 64+int((x_point % 1) * (255-64))
        return obs

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        assert "time_frac" in info, "Must use TimeLimitWrapper."
        time_frac = np.clip(info["time_frac"], 0, 1)
        return self._process_obs(obs, time_frac), reward, done, info


class HashWrapper(gym.Wrapper):
    """
    Maps observation onto a random sequence of pixels.
    This is helpful for testing if the agent is simply memorizing the environment, as no generalization between
    states is possible under this observation.
    """

    def __init__(self, env, hash_size, use_time=False):
        """
        Map observation to a hash of observation.
        """
        super().__init__(env)
        self.env = env
        self.use_time = use_time
        self.hash_size = hash_size
        self.counter = 0

    def step(self, action):

        original_obs, reward, done, info = self.env.step(action)

        if self.use_time:
            state_hash = self.counter
        else:
            state_hash = int(hashlib.sha256(original_obs.data.tobytes()).hexdigest(), 16)

        # note: named tensor would help get this shape right...
        w, h, c = original_obs.shape

        rng = np.random.RandomState(state_hash % (2**32)) # ok... this limits us to 32bits.. might be a better way to do this?

        # seed the random generator and create an random 42x42 observation.
        # note: I'm not sure how many bits the numpy random generate will use, it's posiable it's a lot less than
        # 1024. One option is then to break up the observation into parts. Another would be to just assume that the
        # number of reachable states is much much less than this, and that the chance of a collision (alaising) is
        # very low.
        new_obs = rng.randint(0, 1+1, (self.hash_size,self.hash_size), dtype=np.uint8) * 255
        new_obs = cv2.resize(new_obs, (h, w), interpolation=cv2.INTER_NEAREST)
        new_obs = new_obs[:, :, np.newaxis]

        new_obs = np.concatenate([new_obs]*c, axis=2)

        self.counter += 1

        return new_obs, reward, done, info

    def reset(self):
        self.counter = 0
        return self.env.reset()

class EpisodicLifeEnv(gym.Wrapper):
    def __init__(self, env):
        """Make end-of-life == end-of-episode, but only reset on true game over.
        Done by DeepMind for the DQN and co. since it helps value estimation.
        """
        gym.Wrapper.__init__(self, env)
        self.lives = 0
        self.was_real_done  = True

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        self.was_real_done = done
        # check current lives, make loss of life terminal,
        # then update lives to handle bonus lives
        lives = self.env.unwrapped.ale.lives()
        if lives < self.lives and lives > 0:
            # for Qbert sometimes we stay in lives == 0 condition for a few frames
            # so it's important to keep lives > 0, so that we only reset once
            # the environment advertises done.
            done = True
            info['fake_done'] = True
        self.lives = lives
        return obs, reward, done, info

    def reset(self, **kwargs):
        """Reset only when lives are exhausted.
        This way all states are still reachable even though lives are episodic,
        and the learner need not know about any of this behind-the-scenes.
        """
        if self.was_real_done:
            obs = self.env.reset(**kwargs)
        else:
            # no-op step to advance from terminal/lost life state
            obs, _, _, _ = self.env.step(0)
        self.lives = self.env.unwrapped.ale.lives()
        return obs

class FrameSkipWrapper(gym.Wrapper):
    """
    Performs frame skipping with max over last two frames.
    From https://github.com/openai/baselines/blob/7c520852d9cf4eaaad326a3d548efc915dc60c10/baselines/common/atari_wrappers.py
    """
    def __init__(self, env, min_skip=4, max_skip=None, reduce_op=np.max):
        """Return only every `skip`-th frame"""
        gym.Wrapper.__init__(self, env)
        if max_skip is None:
            max_skip = min_skip
        assert env.observation_space.dtype == "uint8"
        assert min_skip >= 1
        assert max_skip >= min_skip
        # most recent raw observations
        self._obs_buffer = np.zeros((2,)+env.observation_space.shape, dtype=np.uint8)
        self._min_skip = min_skip
        self._max_skip = max_skip
        self._reduce_op = reduce_op

    def step(self, action):
        """Repeat action, sum reward, and max over last two observations."""
        total_reward = 0.0
        done = None
        info = {}
        skip = np.random.randint(self._min_skip, self._max_skip+1)

        for i in range(skip):
            obs, reward, done, _info = self.env.step(action)

            # combine infos, with overwriting
            if _info is not None:
                for k, v in _info.items():
                    info[k] = v

            if i == skip - 2:
                self._obs_buffer[0] = obs
            if i == skip - 1:
                self._obs_buffer[1] = obs
            total_reward += reward
            if done:
                break
        # Note that the observation on the done=True frame
        # doesn't matter
        reduce_frame = self._reduce_op(self._obs_buffer, axis=0)

        return reduce_frame, total_reward, done, info

    def reset(self, **kwargs):
        return self.env.reset(**kwargs)


class ClipRewardWrapper(gym.Wrapper):
    """ Clips reward to given range"""

    def __init__(self, env: gym.Env, clip: float):
        super().__init__(env)
        self.clip = clip

    def step(self, action):
        obs, reward, done, info = self.env.step(action)

        if reward > self.clip or reward < -self.clip:
            info["unclipped_reward"] = reward
            reward = np.clip(reward, -self.clip, +self.clip)

        return obs, reward, done, info


class DeferredRewardWrapper(gym.Wrapper):
    """
    All rewards are delayed until given frame. If frame is -1 then uses terminal state
    """

    def __init__(self, env: gym.Env, time_limit=-1):
        super().__init__(env)
        self.env = env
        self.t = 0
        self.episode_reward = 0
        self.time_limit = time_limit

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        self.t += 1

        give_rewards = (self.t == self.time_limit) or ((self.time_limit == - 1) and done)

        self.episode_reward += reward

        if give_rewards:
            new_reward = self.episode_reward
            self.episode_reward = 0
        else:
            new_reward = 0
        return obs, new_reward, done, info

    def reset(self):
        obs = self.env.reset()
        self.t = 0
        self.episode_reward = 0
        return obs

    def save_state(self, buffer):
        buffer["t"] = self.t
        buffer["episode_reward"] = self.episode_reward

    def restore_state(self, buffer):
        self.t = buffer["t"]
        self.episode_reward = buffer["episode_reward"]


class SaveEnvStateWrapper(gym.Wrapper):
    """
    Enables saving and restoring of the environment state.
    Only support atari at the moment.
    """

    def __init__(self, env: gym.Env):
        super().__init__(env)

    def save_state(self, buffer):
        assert type(self.unwrapped) == AtariEnv, "Only Atari is supported for state saving/loading"
        buffer["atari"] = self.unwrapped.clone_state(include_rng=True)

    def restore_state(self, buffer):
        assert type(self.unwrapped) == AtariEnv, "Only Atari is supported for state saving/loading"
        assert "atari" in buffer, "No state information found for Atari."
        self.unwrapped.restore_state(buffer["atari"])


class SqrtRewardWrapper(gym.Wrapper):
    """ Clips reward to given range"""

    def __init__(self, env: gym.Env, epsilon: float = 1e-3):
        super().__init__(env)
        self.epsilon = epsilon

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        sign = -1 if reward < 0 else +1
        new_reward = sign*(math.sqrt(abs(reward)+1)-1)+self.epsilon*reward
        return obs, new_reward, done, info


class NormalizeObservationsWrapper(gym.Wrapper):
    """
    Normalizes observations.
    """
    def __init__(self, env, clip, shadow_mode=False, initial_state=None):
        super().__init__(env)

        self.env = env
        self.epsilon = 1e-4
        self.clip = clip
        self.obs_rms = utils.RunningMeanStd(shape=())
        self.shadow_mode = shadow_mode
        if initial_state is not None:
            self.obs_rms.restore_state(initial_state)

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        self.obs_rms.update(obs)
        self.mean = self.obs_rms.mean
        self.std = np.sqrt(self.obs_rms.var)

        info["observation_norm_state"] = self.obs_rms.save_state()

        if self.shadow_mode:
            return obs, reward, done, info
        else:
            scaled_obs = (obs - self.mean) / (self.std + self.epsilon)
            scaled_obs = np.clip(scaled_obs, -self.clip, +self.clip)
            scaled_obs = np.asarray(scaled_obs, dtype=np.float32)
            return scaled_obs, reward, done, info

    def save_state(self, buffer):
        buffer["obs_rms"] = self.obs_rms.save_state()

    def restore_state(self, buffer):
        self.obs_rms.restore_state(buffer["obs_rms"])

class RewardScaleWrapper(gym.Wrapper):

    def __init__(self, env:gym.Env, scale:float):
        super().__init__(env)
        self.scale = scale

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        return obs, reward*self.scale, done, info

class VecNormalizeRewardWrapper(gym.Wrapper):
    """
    Normalizes rewards such that returns are unit normal.
    Vectorized version.
    Also clips rewards
    """

    def __init__(
            self,
            env: VectorEnv,
            initial_state=None,
            gamma: float = 1.0,
            clip: float = 10.0,
            scale: float = 1.0,
            returns_transform=lambda x: x,
    ):
        """
        Normalizes returns
        """
        super().__init__(env)

        self.clip = clip
        self.epsilon = 1e-8
        self.current_returns = np.zeros([env.num_envs], dtype=np.float32)
        self.ret_rms = utils.RunningMeanStd(shape=())
        self.gamma = gamma
        self.scale = scale
        self.returns_transform = returns_transform
        if initial_state is not None:
            self.ret_rms.restore_state(initial_state)

    def reset(self):
        self.current_returns *= 0
        return self.env.reset()

    def step(self, actions):
        obs, rewards, dones, infos = self.env.step(actions)

        # the self.gamma here doesn't make sense to me as we are discounting into the future rather than from the past
        # but it is what OpenAI does...
        self.current_returns = rewards + self.gamma * self.current_returns * (1-dones)

        self.ret_rms.update(self.returns_transform(self.current_returns))

        scaled_rewards = rewards / self.std
        if self.clip is not None:
            rewards_copy = scaled_rewards.copy()
            scaled_rewards = np.clip(scaled_rewards, -self.clip, +self.clip)
            if not (rewards_copy == scaled_rewards).all():
                # log if clipping occurred.
                infos[0]["reward_clip"] = True

        scaled_rewards *= self.scale

        return obs, scaled_rewards, dones, infos

    @property
    def mean(self):
        return self.ret_rms.mean

    @property
    def std(self):
        return math.sqrt(self.ret_rms.var + self.epsilon)

    def save_state(self, buffer):
        buffer["ret_rms"] = self.ret_rms.save_state()
        buffer["current_returns"] = self.current_returns

    def restore_state(self, buffer):
        self.ret_rms.restore_state(buffer["ret_rms"])
        self.current_returns = buffer["current_returns"]

class MonitorWrapper(gym.Wrapper):
    """
    Records a copy of the current observation and reward into info.
    This can be helpful to retain an unmodified copy of the input.
    """

    def __init__(self, env: gym.Env, monitor_video=False):
        super().__init__(env)
        self.monitor_video = monitor_video

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        if self.monitor_video:
            info["monitor_obs"] = obs.copy()
        info["raw_reward"] = reward
        return obs, reward, done, info

class FrameCropWrapper(gym.Wrapper):
    """
    Crops input frame.
    """

    def __init__(self, env: gym.Env, x1, x2, y1, y2):
        super().__init__(env)
        self.cropping = (slice(y1, y2, 1), slice(x1, x2, 1))

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        obs = obs[self.cropping]
        return obs, reward, done, info

class TimeLimitWrapper(gym.Wrapper):
    """
    From https://github.com/openai/baselines/blob/master/baselines/common/wrappers.py
    """
    def __init__(self, env, max_episode_steps=None):
        super().__init__(env)
        self._max_episode_steps = max_episode_steps
        self._elapsed_steps = 0

    def step(self, ac):
        observation, reward, done, info = self.env.step(ac)
        self._elapsed_steps += 1
        if self._elapsed_steps >= self._max_episode_steps:
            done = True
            info['TimeLimit.truncated'] = True
        # when a done occurs we will reset and the observation returned will be the first frame of a new
        # episode, so time_frac should be 0. Remember time_frac is the time of the state we *land in* not
        # of the state we started from.
        info['time_frac'] = (self._elapsed_steps / self._max_episode_steps) if not done else 0
        return observation, reward, done, info

    def reset(self, **kwargs):
        self._elapsed_steps = 0
        return self.env.reset(**kwargs)

    def save_state(self, buffer):
        buffer["_elapsed_steps"] = self._elapsed_steps

    def restore_state(self, buffer):
        self._elapsed_steps= buffer["_elapsed_steps"]


class AtariWrapper(gym.Wrapper):
    """
    Applies Atari frame warping, optional gray-scaling, and frame stacking as per nature paper.
    Note: unlike Nature the initial frame cropping is disabled by default.

    input: 160x210x3 uint8 RGB frames
    output: 84x84x1 uint8 grayscale frame (by default)

    """

    def __init__(self, env: gym.Env, grayscale=True, width=84, height=84, interpolation=cv2.INTER_AREA):
        """
        Stack and do other stuff...
        Input should be (210, 160, 3)
        Output is a stack of shape (nstacks, width, height)
        """

        super().__init__(env)

        self._width, self._height = width, height

        assert len(env.observation_space.shape) == 3, "Invalid shape {}".format(env.observation_space.shape)
        assert env.observation_space.shape[-1] == 3, "Invalid shape {}".format(env.observation_space.shape)
        assert env.observation_space.dtype == np.uint8, "Invalid dtype {}".format(env.observation_space.dtype)

        self.grayscale = grayscale
        self.n_channels = 1 if self.grayscale else 3
        self.interpolation = interpolation

        self.observation_space = gym.spaces.Box(
            low=0,
            high=255,
            shape=(self.n_channels, self._width, self._height),
            dtype=np.uint8,
        )

    def _process_frame(self, obs):

        if self.grayscale:
            obs = cv2.cvtColor(obs, cv2.COLOR_RGB2GRAY)
            obs = obs[:, :, np.newaxis]

        width, height, channels = obs.shape

        if (width, height) != (self._width, self._height):
            obs = cv2.resize(obs, (self._height, self._width), interpolation=self.interpolation)

        if len(obs.shape) == 2:
            obs = obs[:, :, np.newaxis]

        return obs

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        info["channels"] = ["Gray"] if self.grayscale else ["ColorR", "ColorG", "ColorB"]
        return self._process_frame(obs), reward, done, info

    def reset(self):
        obs = self.env.reset()
        return self._process_frame(obs)

class NullActionWrapper(gym.Wrapper):
    """
    Allows passing of a negative action to indicate not to proceed the environment forward.
    Observation, frozen, info empty, and reward will be 0, done will be false
    Child environment will not be stepped.
    Helpful for vectorized environments.
    """

    def __init__(self, env):
        gym.Wrapper.__init__(self, env)
        self._prev_obs = None
        self._prev_time_frac = 0.0

    def step(self, action:int):
        if action < 0:
            return self._prev_obs, 0, False, {'time_frac': self._prev_time_frac}
        else:
            obs, reward, done, info = self.env.step(action)
            self._prev_obs = obs
            if "time_frac" in info:
                self._prev_time_frac = info["time_frac"]
            return obs, reward, done, info

    def reset(self, **kwargs):
        obs = self.env.reset(**kwargs)
        self._prev_obs = obs
        return obs


class EpisodeScoreWrapper(gym.Wrapper):
    """
    Records episode length and score
    """

    def __init__(self, env):
        super().__init__(env)
        self.ep_score = 0
        self.ep_length = 0

    def step(self, action:int):
        obs, reward, done, info = self.env.step(action)
        self.ep_score += reward
        self.ep_length += 1
        info["ep_score"] = self.ep_score
        info["ep_length"] = self.ep_length
        return obs, reward, done, info

    def reset(self, **kwargs):
        obs = self.env.reset(**kwargs)
        self.ep_score = 0
        self.ep_length = 0
        return obs

    def save_state(self, buffer):
        buffer["ep_score"] = self.ep_score
        buffer["ep_length"] = self.ep_length

    def restore_state(self, buffer):
        self.ep_score = buffer["ep_score"]
        self.ep_length = buffer["ep_length"]

class NoopResetWrapper(gym.Wrapper):
    """
    Applies a random number of no-op actions before agent can start playing.
    From https://github.com/openai/baselines/blob/7c520852d9cf4eaaad326a3d548efc915dc60c10/baselines/common/atari_wrappers.py
    """
    def __init__(self, env, noop_max=30):
        """Sample initial states by taking random number of no-ops on reset.
        No-op is assumed to be action 0.
        """
        gym.Wrapper.__init__(self, env)
        self.noop_max = noop_max
        self.override_num_noops = None
        self.noop_action = 0
        self.noop_given = None
        assert env.unwrapped.get_action_meanings()[0] == 'NOOP'

    def reset(self, **kwargs):
        """ Do no-op action for up to noop_max steps.
            Note: this differs from openAI's implementation in that theirs  would perform at least one noop, but
            this one may sometimes perform 0. This means a noop trained agent will do well if tested on no noop.

            Actually: if we don't do at least 1 the obs will be wrong, as obs on reset is incorrect for some reason...
            one of the wrappers makes a note of this (the stacking one I think). Because of this I always noop for
            atleast one action.

        """
        obs = self.env.reset(**kwargs)
        if self.override_num_noops is not None:
            noops = self.override_num_noops
            print(f"Forcing {noops} NOOPs.")
        else:
            noops = self.unwrapped.np_random.randint(1, self.noop_max+1)

        assert noops >= 0

        self.noop_given = noops

        for _ in range(noops):
            obs, _, done, _ = self.env.step(self.noop_action)
            if done:
                obs = self.env.reset(**kwargs)
        return obs

    def step(self, ac):
        obs, reward, done, info = self.env.step(ac)
        if self.noop_given is not None:
            info['noop_start'] = self.noop_given
            self.noop_given = None
        return obs, reward, done, info

class FrameStack(gym.Wrapper):
    """ This is the original frame stacker that works by making duplicates of the frames,
        For large numbers of frames this can be quite slow.
    """

    def __init__(self, env, n_stacks=4):

        super().__init__(env)

        assert len(env.observation_space.shape) == 3, "Invalid shape {}".format(env.observation_space.shape)
        assert env.observation_space.dtype == np.uint8, "Invalid dtype {}".format(env.observation_space.dtype)

        c,h,w = env.observation_space.shape

        assert c in [1, 3], "Invalid shape {}".format(env.observation_space.shape)

        self.n_stacks = n_stacks
        self.original_channels = c
        self.n_channels = self.n_stacks * self.original_channels

        self.stack = np.zeros((self.n_channels, h, w), dtype=np.uint8)

        self.observation_space = gym.spaces.Box(
            low=0,
            high=255,
            shape=(self.n_channels, h, w),
            dtype=np.uint8,
        )

    def _push_obs(self, obs):
        # note, in this case slot 0 is the oldest observation, not the newest.
        assert self.original_channels == 1, "Stacking does not support color at the moment."
        self.stack = np.roll(self.stack, shift=-1, axis=0)
        self.stack[-1:, :, :] = obs[:, :, 0]

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        self._push_obs(obs)
        if "channels" in info:
            info["channels"] = info["channels"] * self.n_stacks
        return self.stack, reward, done, info

    def reset(self):
        obs = self.env.reset()
        for _ in range(self.n_stacks):
            self._push_obs(obs)
        return self.stack

    def save_state(self, buffer):
        buffer["stack"] = self.stack

    def restore_state(self, buffer):
        self.stack = buffer["stack"]


class EMAFrameStack(gym.Wrapper):
    """
        Maintain EMA of previous states with different alpha values.
    """

    def __init__(self, env, n_stacks=4, gamma=2.0):

        super().__init__(env)

        assert len(env.observation_space.shape) == 3, "Invalid shape {}".format(env.observation_space.shape)
        assert env.observation_space.dtype == np.uint8, "Invalid dtype {}".format(env.observation_space.dtype)

        c,h,w = env.observation_space.shape

        assert c in [1, 3], "Invalid shape {}".format(env.observation_space.shape)

        self.n_stacks = n_stacks
        self.original_channels = c
        self.n_channels = self.n_stacks * self.original_channels
        self.gamma = gamma

        self.stack = np.zeros((self.n_channels, h, w), dtype=np.float32)

        self.observation_space = gym.spaces.Box(
            low=0,
            high=255,
            shape=(self.n_channels, h, w),
            dtype=np.uint8,
        )

    def _push_obs(self, obs):
        assert self.original_channels == 1, "Stacking does not support color at the moment."
        # alpha is ema
        for i in range(self.n_stacks):
            alpha = 1/(self.gamma ** i)
            self.stack[i] = self.stack[i] * (1-alpha) + obs[:, :, 0] * alpha

    def _get_obs(self):
        return np.clip(self.stack, 0, 255).astype(np.uint8)

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        self._push_obs(obs)
        if "channels" in info:
            info["channels"] = info["channels"] * self.n_stacks
        return self._get_obs(), reward, done, info

    def reset(self):
        obs = self.env.reset()
        for i in range(self.n_stacks):
            self.stack[i] = obs[:, :, 0]
        return self._get_obs()

    def save_state(self, buffer):
        buffer["stack"] = self.stack

    def restore_state(self, buffer):
        self.stack = buffer["stack"]


class FrameStack_Lazy(gym.Wrapper):
    # taken from https://github.com/openai/baselines/blob/master/baselines/common/atari_wrappers.py
    # modified for channels first.

    def __init__(self, env, k):
        """Stack k last frames.
        Returns lazy array, which is much more memory efficient.
        See Also
        --------
        baselines.common.atari_wrappers.LazyFrames
        """
        gym.Wrapper.__init__(self, env)
        self.k = k
        self.frames = collections.deque([], maxlen=k)

        new_shape = list(env.observation_space.shape)
        new_shape[0] *= k
        new_shape = tuple(new_shape)

        self.observation_space = gym.spaces.Box(low=0, high=255, shape=new_shape, dtype=env.observation_space.dtype)

    def reset(self):
        ob = self.env.reset()
        for _ in range(self.k):
            self.frames.append(ob)
        return self._get_ob()

    def step(self, action):
        ob, reward, done, info = self.env.step(action)
        self.frames.append(ob)
        return self._get_ob(), reward, done, info

    def _get_ob(self):
        assert len(self.frames) == self.k
        result = LazyFrames(list(self.frames))
        return result

class LazyFrames(object):
    # taken from https://github.com/openai/baselines/blob/master/baselines/common/atari_wrappers.py
    def __init__(self, frames):
        """This object ensures that common frames between the observations are only stored once.
        It exists purely to optimize memory usage which can be huge for DQN's 1M frames replay
        buffers.
        This object should only be converted to numpy array before being passed to the model.
        You'd not believe how complex the previous solution was."""
        self._frames = frames
        self._out = None

    def _force(self):
        if self._out is None:
            self._out = np.concatenate(self._frames, axis=0)
            self._frames = None
        return self._out

    def __array__(self, dtype=None):
        out = self._force()
        if dtype is not None:
            out = out.astype(dtype)
        return out

    def __len__(self):
        return len(self._force())

    def __getitem__(self, i):
        return self._force()[i]

    def count(self):
        frames = self._force()
        return frames.shape[frames.ndim - 1]

    def frame(self, i):
        return self._force()[..., i]

def cast_down(x: Union[str, float, int]):
    """
    Try to convert string / float into an integer, float, or string, in that order...
    """
    try:
        if int(x) == x:
            return int(x)
    except:
        pass
    try:
        if float(x) == x:
            return float(x)
    except:
        pass
    return str(x)


def get_wrapper(env, wrapper_type) -> Union[gym.Wrapper, None]:
    """
    Returns first wrapper matching type in environment, or none.
    """
    while True:
        if type(env) == wrapper_type:
            return env
        try:
            env = env.env
        except:
            return None
