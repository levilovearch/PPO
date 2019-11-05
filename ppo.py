import numpy as np
import matplotlib.pyplot as plt
import gym
import torch
import torch.nn as nn
import torch.nn.functional as F
import cv2
import time
import itertools
from collections import deque

from mpeg_creator import MPEGCreator

DEVICE = "cuda"

def show_cuda_info():

    global DEVICE
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

    device_id = torch.cuda.current_device()
    print("Device:", DEVICE)
    print(torch.cuda.get_device_name(device_id))

"""
------------------------------------------------------------------------------------------------------------------------
    Wrappers
------------------------------------------------------------------------------------------------------------------------
"""


class NormalizeObservationWrapper(gym.Wrapper):
    def __init__(self, env, clip=5.0):
        """
        Normalize and clip observations.
        """
        super().__init__(env)
        self.clip = clip
        self._n = 1000
        self._update_every = 100
        self.epsilon = 0.00001
        self.counter = 0

        self.observation_space = gym.spaces.Box(
            low = -clip,
            high = clip,
            shape = env.observation_space.shape,
            dtype=np.float32
        )

        self.history = deque(maxlen=self._n)
        self.env = env

    def step(self, action):
        obs, reward, done, info = self.env.step(action)

        self.history.append(obs)

        #note: this will be slow for large observation spaces, would be better to do a running average.
        if (self.counter % self._update_every) == 0:
            self.means = np.mean(np.asarray(self.history), axis=0)
            self.stds = np.std(np.asarray(self.history), axis=0)

        obs = np.asarray(obs, dtype=np.float32)
        obs = np.clip((obs - self.means) / (self.stds + self.epsilon), -self.clip, +self.clip)

        self.counter += 1

        return obs, reward, done, info


class NormalizeRewardWrapper(gym.Wrapper):

    def __init__(self, env, clip=5.0):
        """
        Normalizes rewards
        """
        super().__init__(env)

        self.env = env
        self._n = 10000
        self._update_every = 100
        self.history = deque(maxlen=self._n)
        self.clip = clip
        self.epsilon = 0.00001
        self.counter = 0

    def step(self, action):
        obs, reward, done, info = self.env.step(action)

        self.history.append(reward)

        if (self.counter % (self._update_every)) == 0:
            self.mean = np.mean(self.history, axis=0)
            self.std = np.std(self.history, axis=0)

        last_raw_reward = reward
        reward = np.clip(reward / (self.std + self.epsilon), -self.clip, +self.clip)

        info["raw_reward"] = last_raw_reward

        self.counter += 1

        return obs, reward, done, info


class AtariWrapper(gym.Wrapper):

    def __init__(self, env):
        """
        Stack and do other stuff...
        """
        super().__init__(env)

        self.history = []
        self.env = env

        self.nstacks = 4
        self._width, self._height = 84, 84

        self.observation_space = gym.spaces.Box(
            low=0.0,
            high=1.0,
            shape=(self._height, self._width, self.nstacks),
            dtype=np.float32,
        )

    def _push_raw_obs(self, obs):

        obs = cv2.cvtColor(obs, cv2.COLOR_RGB2GRAY)
        obs = obs[30:-10]
        obs = cv2.resize(obs, (self._width, self._height), interpolation=cv2.INTER_AREA)
        obs = obs[:, :, np.newaxis]

        self.history.append(obs)
        if len(self.history) > self.nstacks:
            self.history = self.history[1:]

    def _get_stacked_obs(self):
        stack = np.concatenate(self.history, axis=2).astype(np.float32) / 255.0
        return stack

    def step(self, action):
        obs, reward, done, info = self.env.step(action)
        self._push_raw_obs(obs)
        info["raw_obs"] = obs
        return self._get_stacked_obs(), reward, done, info

    def reset(self):
        obs = self.env.reset()
        for _ in range(self.nstacks):
            self._push_raw_obs(obs)
        return self._get_stacked_obs()


class DiscretizeActionWrapper(gym.Wrapper):

    def __init__(self, env, bins=10):
        """
        Convert continious action space into discrete.
        """
        super().__init__(env)
        self.env = env

        assert isinstance(env.action_space, gym.spaces.Box)
        assert len(env.action_space.shape) == 1

        dims = env.action_space[0]

        self.action_map = []

        spans = [np.linspace(env.action_space.low[d], env.action_space.high[d], bins) for d in range(dims)]

        self.action_map = list(itertools.product(*spans))

        self.action_space = gym.spaces.Discrete(len(self.action_map))

    def step(self, action):
        return self.env.step(self.action_map[action])


"""
------------------------------------------------------------------------------------------------------------------------
    Utilities
------------------------------------------------------------------------------------------------------------------------
"""


def make_environment(env_name):
    """ Construct environment of given name, including any required """
    env = gym.make(env_name)
    if "Pong" in env_name:
        env = AtariWrapper(env)
        env = NormalizeRewardWrapper(env)
    elif "CartPole" in env_name:
        env = NormalizeObservationWrapper(env)
    if isinstance(env.action_space, gym.spaces.Box):
        env = DiscretizeActionWrapper(env)
    return env


def prod(X):
    y = 1
    for x in X:
        y *= x
    return y


def trace(s):
    print(s)

def sample_action(p):
    """ Returns integer [0..len(probs)-1] based on probabilities. """
    p = np.asarray(p, dtype=np.float64)
    p /= p.sum()  # probs are sometimes off by a little due to precision error
    return np.random.choice(range(len(p)), p=p)


def write_cv2_video(filename, frames):
    height, width, channels = frames[0].shape

    # Define the codec and create VideoWriter object.The output is stored in 'outpy.avi' file.
    out = cv2.VideoWriter(filename, cv2.VideoWriter_fourcc(*'mp4v'), 30, (width, height), isColor=True)

    for frame in frames:
        out.write(frame)

    out.release()


class MLPModel(nn.Module):
    """ A very simple Multi Layer Perceptron """

    def __init__(self, input_dims, actions):
        super(MLPModel, self).__init__()
        self.actions = actions
        self.d = prod(input_dims)
        self.fc1 = nn.Linear(self.d, 64)
        self.fc2 = nn.Linear(64, 64)
        self.fc_policy = nn.Linear(64, actions)
        self.fc_value = nn.Linear(64, 1)
        self.to(DEVICE)

    def forward(self, x):
        x = torch.from_numpy(x).float().to(DEVICE)
        x = x.reshape(-1, self.d)
        x = torch.tanh(self.fc1(x))
        x = torch.tanh(self.fc2(x))
        return x

    def policy(self, x):
        x = self.forward(x)
        x = self.fc_policy(x)
        x = F.softmax(x, dim=1)
        return x

    def value(self, x):
        x = self.forward(x)
        x = self.fc_value(x).squeeze(dim=1)
        return x


class CNNModel(nn.Module):
    """ Nature paper inspired CNN """

    def __init__(self, input_dims, actions):
        super(CNNModel, self).__init__()
        self.actions = actions
        h, w, c = input_dims
        self.conv1 = nn.Conv2d(c, 32, kernel_size=8, stride=4)
        self.conv2 = nn.Conv2d(32, 64, kernel_size=4, stride=2)
        self.conv3 = nn.Conv2d(64, 64, kernel_size=3, stride=1)
        self.d = prod((64, 7, 7))
        self.fc = nn.Linear(self.d, 256)
        self.fc_policy = nn.Linear(256, actions)
        self.fc_value = nn.Linear(256, 1)
        self.to(DEVICE)

    def forward(self, x):
        # need NCHW, but input is HWC
        if len(x.shape) == 3:
            # make a batch of 1 for a single example.
            x = x[np.newaxis, :, :, :]
        x = np.swapaxes(x, 1, 3)
        x = torch.from_numpy(x).float().to(DEVICE)
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.relu(self.fc(x.view(-1, self.d)))
        return x

    def policy(self, x):
        x = self.forward(x)
        x = self.fc_policy(x)
        x = F.softmax(x, dim=1)
        return x

    def value(self, x):
        x = self.forward(x)
        x = self.fc_value(x).squeeze(dim=1)
        return x


def entropy(p):
    return (-p * p.log2()).sum()


def log_entropy(logp):
    p = logp.exp()
    return (-p * p.log2()).sum()


def smooth(X, alpha=0.95):
    y = X[0]
    results = []
    for x in X:
        y = (1 - alpha) * x + (alpha) * y
        results.append(y)
    return results


def export_video(filename, frames, scale=4):

    if len(frames) == 0:
        return

    if (frames[0].dtype != np.uint8):
        print("Video files must be in uint8 format")
    if (len(frames[0].shape) != 3):
        print("Video frames must have dims 3, (shape is {})".format(frames[0].shape))
    if (frames[0].shape[-1] not in [1,3]):
        print("Video frames must have either 3 or 1 channels (shape {})".format(frames[0]))

    height, width, channels = frames[0].shape

    processed_frames = []

    for frame in frames:

        # convert single channel grayscale to rgb grayscale
        if channels == 1:
            frame = np.concatenate([frame] * 3, axis=2)

        if scale != 1:
            frame = cv2.resize(frame, (height * scale, width * scale), interpolation=cv2.INTER_NEAREST)

        processed_frames.append(frame)

    write_cv2_video(filename, processed_frames)


def safe_mean(X):
    return np.mean(X) if len(X) > 0 else None


def inspect(x):
    if isinstance(x, int):
        print("Python interger")
    elif isinstance(x, float):
        print("Python float")
    elif isinstance(x, np.ndarray):
        print("Numpy", x.shape, x.dtype)
    elif isinstance(x, torch.Tensor):
        print("{:<10}{:<25}{:<18}{:<14}".format("torch", str(x.shape), str(x.dtype), str(x.device)))
    else:
        print(type(x))


def train_minibatch(model, optimizer, epsilon, vf_coef, ent_bonus, prev_states, actions, rewards, returns, policy_probs, advantages):

    optimizer.zero_grad()

    policy_probs = torch.tensor(policy_probs, dtype=torch.float32).to(DEVICE)
    advantages = torch.tensor(advantages, dtype=torch.float32).to(DEVICE)
    rewards = torch.tensor(rewards, dtype=torch.float32).to(DEVICE)
    returns = torch.tensor(returns, dtype=torch.float32).to(DEVICE)

    mini_batch_size = len(prev_states)

    forward = model.policy(prev_states)

    ratio = forward[range(mini_batch_size), actions] / policy_probs[range(mini_batch_size), actions]

    loss_clip = torch.sum(torch.min(ratio * advantages, torch.clamp(ratio, 1 - epsilon, 1 + epsilon) * advantages))

    td = model.value(prev_states) - returns

    # people do this in different ways, I'm going to go for huber loss.
    #loss_value = - vf_coef * torch.sum(td * td)

    loss_value = - vf_coef * torch.sum(torch.min(td * td, torch.abs(td)))

    loss_entropy = ent_bonus * entropy(forward).sum()

    reward_sum = torch.sum(rewards)

    loss = -(loss_clip + loss_value + loss_entropy) / mini_batch_size # gradient ascent.

    loss.backward()
    optimizer.step()

    return (float(x) for x in [-loss, loss_clip/ mini_batch_size, loss_value/ mini_batch_size, loss_entropy/ mini_batch_size, reward_sum])


def run_agents(n_steps, model, envs, states, episode_score, episode_len, score_history, len_history, global_step, video_frames):
    """
    Runs agents given number of steps, using a single thread.
    :param envs:
    :return:
    """

    batch_prev_state = []
    batch_action = []
    batch_next_state = []
    batch_reward = []
    batch_policy = []
    batch_terminal = []

    for i, env in enumerate(envs):

        state = states[i]

        for t in range(n_steps):

            probs = model.policy(state)[0].detach().cpu().numpy()
            action = sample_action(probs)

            prev_state = state.copy()

            state, reward, done, info = env.step(action)

            raw_reward = info.get("raw_reward", reward)

            batch_prev_state.append(prev_state)
            batch_action.append(action)
            batch_next_state.append(state)
            batch_reward.append(reward)
            batch_policy.append(probs)
            batch_terminal.append(done)

            episode_score[i] += raw_reward
            episode_len[i] += 1

            if i == 0:

                frame = state[:,:,3:3+1]

                if frame.dtype == np.float32:
                    frame = np.asarray(frame * 255, np.uint8)

                video_frames.append(frame)

            if done:
                _ = env.reset()
                score_history.append(episode_score[i])
                len_history.append(episode_len[i])
                episode_score[i] = 0
                episode_len[i] = 0
                if i == 0:
                    export_video("video {}.mp4".format(global_step), video_frames)
                    video_frames.clear()

        states[i] = state

    return batch_prev_state, batch_action, batch_next_state, batch_reward, batch_policy, batch_terminal


def with_default(x, default):
    return x if x is not None else default


def train(env_name, model: nn.Module):
    trace("Training started.")

    """
    Default parameters from stable baselines
    
    https://stable-baselines.readthedocs.io/en/master/modules/ppo2.html
    
    gamma             0.99
    n_steps            128
    ent_coef          0.01
    learning_rate   2.5e-4
    vf_coef            0.5
    max_grad_norm      0.5 (not used...)
    lam               0.95
    nminibatches         4
    noptepoch            4
    cliprange          0.1 
    
    atari usually requires ~10M steps 
    
    """

    n_steps = 128  # steps per update
    gamma = 0.99  # discount (0.99)
    lam = 0.95  # GAE parameter (0.95)
    n_batches = 4
    epsilon = 0.1
    vf_coef = 0.5  # how much loss to take from value function
    agents = 8
    epochs = 4
    ent_bonus = 0.01
    alpha = 2.5e-4

    batch_size = (n_steps * agents)
    mini_batch_size = batch_size // n_batches

    optimizer = torch.optim.Adam(model.parameters(), lr=alpha)

    envs = [make_environment(env_name) for _ in range(agents)]

    # initialize agent
    states = [env.reset() for env in envs]

    episode_score = [0 for _ in envs]
    episode_len = [0 for _ in envs]

    training_log = []

    score_history = []
    len_history = []

    video_frames = []

    for step in range(100000):

        start_time = time.time()

        # collect experience

        batch_prev_state, batch_action, batch_next_state, batch_reward, batch_policy, batch_terminal = run_agents(
            n_steps, model, envs, states, episode_score, episode_len, score_history, len_history, step, video_frames)

        # calculate returns and advantages

        # generate advantage estimates
        # note, this can be done much faster, but this will do for the moment.

        batch_value = model.value(np.asarray(batch_prev_state)).detach().cpu().numpy()
        batch_advantage = np.zeros([batch_size], dtype=np.float32)

        # we calculate the advantages by going backwards..
        # estimated return is the estimated return being in state i
        # this is largely based off https://github.com/hill-a/stable-baselines/blob/master/stable_baselines/ppo2/ppo2.py
        final_state = batch_next_state[-1]
        value_next_i = model.value(final_state)[0].detach().cpu()
        terminal_next_i = False
        prev_adv = 0
        for i in reversed(range(batch_size)):
            delta = batch_reward[i] + gamma * value_next_i * (1.0-terminal_next_i) - batch_value[i]

            batch_advantage[i] = prev_adv = delta + gamma * lam * (1.0-terminal_next_i) * prev_adv

            value_next_i = batch_value[i]
            terminal_next_i = batch_terminal[i]

        batch_returns = batch_advantage + batch_value

        # normalize batch advantages
        batch_advantage = (batch_advantage - batch_advantage.mean()) / (batch_advantage.std() + 1e-8)

        total_loss_clip = 0
        total_loss_value = 0
        total_loss_entropy = 0
        total_loss = 0
        total_reward_sum = 0

        batch_arrays = [
            np.asarray(batch_prev_state),
            np.asarray(batch_action),
            np.asarray(batch_reward),
            np.asarray(batch_returns),
            np.asarray(batch_policy),
            np.asarray(batch_advantage)
        ]

        for i in range(epochs):

            ordering = list(range(batch_size))
            np.random.shuffle(ordering)

            for j in range(n_batches):

                # put together a minibatch.
                batch_start = j * mini_batch_size
                batch_end = (j + 1) * mini_batch_size
                sample = ordering[batch_start:batch_end]

                slices = (x[sample] for x in batch_arrays)

                loss, loss_clip, loss_value, loss_entropy, reward_sum = train_minibatch(
                    model, optimizer, epsilon, vf_coef, ent_bonus, *slices)

                total_loss_clip += loss_clip
                total_loss_value += loss_value
                total_loss_entropy += loss_entropy
                total_loss += loss
                total_reward_sum += reward_sum

            history_string = "{}".format(
                [round(float(x), 2) for x in score_history[-5:]]
            )

            training_log.append(
                (float(total_loss),
                 float(total_loss_clip),
                 float(total_loss_value),
                 float(total_loss_entropy),
                 safe_mean(score_history[-100:]),
                 safe_mean(len_history[-100:]),
                 history_string)
            )

        step_time = (time.time() - start_time) / batch_size

        if step == 0:
            print("Training at {:.1f}fps".format(1.0 / step_time))

        if step % 100 == 0:
            print("{:<11}{:<11}{:<11}{:<11}{:<11}{:<11}{:<11}".format("step", "loss", "loss_clip", "loss_value",
                                                                      "loss_ent", "ep_score", "ep_len"))
        if step % 10 == 0:
            print("{:<11}{:<11.3f}{:<11.3f}{:<11.3f}{:<11.3f}{:<11.3f}{:<11.3f}{:<11}".format(
                str(step) + " " + str(step * n_steps * agents // 1000) + "K",
                training_log[-1][0],
                training_log[-1][1],
                training_log[-1][2],
                training_log[-1][3],
                with_default(training_log[-1][4], 0),
                with_default(training_log[-1][5], 0),
                with_default(training_log[-1][6], 0)
            ))

        if step % 50 == 0:

            xs = [x * batch_size for x in range(len(training_log))]
            plt.figure(figsize=(8, 8))
            plt.plot(xs, smooth([x[0] for x in training_log]), label='loss')
            plt.plot(xs, smooth([x[1] for x in training_log]), label='loss_clip')
            plt.plot(xs, smooth([x[2] for x in training_log]), label='loss_value')
            plt.plot(xs, smooth([x[3] for x in training_log]), label='loss_entropy')
            plt.legend()
            plt.ylabel("Loss")
            plt.xlabel("Step")
            plt.savefig('loss.png')
            plt.close()

            xs = []
            rewards = []
            for i, x in enumerate(training_log):
                if x[4] is None:
                    continue
                xs.append(i * batch_size)
                rewards.append(x[4])

            if len(rewards) > 10:
                plt.figure(figsize=(8, 8))
                plt.plot(xs, smooth(rewards), label='reward')
                plt.ylabel("Reward")
                plt.xlabel("Step")
                plt.savefig('reward.png')
                plt.close()

    return training_log


def run_experiment(env_name, Model):

    env = make_environment(env_name)
    n_actions = env.action_space.n
    obs_space = env.observation_space.shape

    print("Playing {} with {} obs_space and {} actions.".format(env_name, obs_space, n_actions))

    model = Model(obs_space, n_actions)
    log = train(env_name, model)
    xs = range(len(log))
    plt.plot(xs, smooth([x for x, y, z, u, v in log]),label='loss')
    plt.plot(xs, smooth([y for x, y, z, u, v in log]),label='loss_clip')
    plt.plot(xs, smooth([z for x, y, z, u, v in log]),label='loss_value')
    plt.legend()
    plt.show()
    plt.plot(xs, smooth([u for x, y, z, u, v in log]), label='reward')
    plt.legend()
    plt.show()

if __name__ == "__main__":
    show_cuda_info()
    run_experiment("Pong-v4", CNNModel)
    #run_experiment("CartPole-v0", MLPModel)

