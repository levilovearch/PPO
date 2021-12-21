"""
Experience Replay used for distillation
"""

import numpy as np
import hashlib
import math

class ExperienceReplayBuffer():
    """
    Class to handle experience replay. Buffer is limited to N entries, and holds a uniform sample of all (unique)
    experience passed to it. For performance rollout.obs should be set to readonly.

    The buffer will make sure that no duplicate frames are added (via hashing)

    Usage:
        buffer = ExperienceReplayBuffer(N)

        while True:
            rollout = generate_rollout()
            model.train(rollout)
            buffer.add_experience(rollout.obs)
            process_replay_experience(buffer.data)

    """

    def __init__(self, N:int, obs_shape: tuple, obs_dtype, filter_duplicates:bool = False, mode="uniform"):
        """
        @param N Size of replay
        @param state_shape Shape of states
        @param filter_duplicates Hashes input and filters out any entries that already exist in the replay buffer.
        """
        self.N = N
        self.experience_seen = 0
        self.data = np.zeros([N, *obs_shape], dtype=obs_dtype)
        self.time = np.zeros([N], dtype=np.float32) # this is annoying, maybe there's a better way?
        self.hashes = np.zeros([N], dtype=np.uint64)
        self.filter_duplicates = filter_duplicates
        self.mode = mode

    def save_state(self, force_copy=True):
        return {
            'N': self.N,
            'experience_seen': self.experience_seen,
            'data': self.data.copy() if force_copy else self.data,
            'time': self.time.copy() if force_copy else self.time,
            'hashes': self.hashes.copy() if force_copy else self.hashes,
        }

    def load_state(self, state_dict: dict):
        self.N = state_dict["N"]
        self.experience_seen = state_dict["experience_seen"]
        self.data = state_dict["data"].copy()
        self.time = state_dict["time"].copy()
        self.hashes = state_dict["hashes"].copy()

    def add_experience(self, new_experience: np.ndarray, new_time: np.ndarray = None):
        """
        Adds new experience to the experience replay

        @param new_experience: ndarray of dims [N, *state_shape]
        """

        assert new_experience.shape[1:] == self.data.shape[1:], \
            f"Invalid shape for new experience, expecting {new_experience.shape[1:]} but found {self.data.shape[1:]}."
        assert new_experience.dtype == self.data.dtype, \
            f"Invalid dtype for new experience, expecting {self.data.dtype} but found {self.data.dtype}."

        # 1. filter our examples that have already been seen
        hash_fn = lambda x: int(hashlib.sha256(x.data.tobytes()).hexdigest(), 16) % (2**64)

        ids = np.asarray(range(len(new_experience)))

        if self.filter_duplicates:
            new_hashes = np.asarray([hash_fn(new_experience[i]) for i in range(len(new_experience))], dtype=np.uint64)
            hash_set = set(self.hashes)
            mask = [hash not in hash_set for hash in new_hashes]
            ids = ids[mask]
        else:
            new_hashes = None

        # 2. work out how many new entries we want to use, and resample them
        new_entries = len(ids)

        if self.experience_seen == 0:
            # we initialize the buffer with the first sample of data, even if this means adding duplicates.
            entries_to_add = self.N
        else:
            if self.mode == "uniform":
                entries_to_add = math.ceil((new_entries / (self.experience_seen + new_entries)) * self.N)
            elif self.mode in ["overwrite", "sequential"]:
                entries_to_add = new_entries
            else:
                raise ValueError(f"Invalid mode {self.mode}")

        ids = smart_sample(ids, entries_to_add)
        ids = sorted(ids) # faster to in sequential order.

        # 3. add the new entries
        # note: sometimes we need to add more entries than we have (e.g. when initializing the buffer) in which case
        # I just use sampling with replacement
        if self.mode == "sequential":
            new_spots = np.asarray(range(self.experience_seen, self.experience_seen + entries_to_add))
            new_spots = np.mod(new_spots, self.N)
        else:
            new_spots = smart_sample(range(self.N), entries_to_add)

        for source, destination in zip(ids, new_spots):
            # remove its hash
            # add new entry
            self.data[destination] = new_experience[source]
            if new_hashes is not None:
                self.hashes[destination] = new_hashes[source]
            if new_time is not None:
                self.time[destination] = new_time[source]

        self.experience_seen += new_entries


def smart_sample(x, n):
    """
    Samples n samples from x.
    If x <= len(x) samples ones without replacement
    If x > len(x) samples len(x)//n times without replacement

    This makes sure if n >= len(x) then every x is choosen at least once, and bounds the delta in duplicates to 1.

    """
    if n <= len(x):
        return np.random.choice(x, size=[n], replace=False)
    else:
        return np.concatenate([smart_sample(x, len(x)), smart_sample(x, n-len(x))], axis=0)

