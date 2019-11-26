import numpy as np
import argparse
import torch
import os
import math

NATS_TO_BITS = 1.0/math.log(2)


class Color:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# -------------------------------------------------------------
# Utils
# -------------------------------------------------------------


def str2bool(v):
    """ Convert from string to boolean.
    """
    if isinstance(v, bool):
       return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def mse(a,b):
    """ returns MSE of a and b. """
    return (np.square(a - b, dtype=np.float32)).mean(dtype=np.float32)


def prod(X):
    y = 1
    for x in X:
        y *= x
    return y


def trace(s):
    print(s)


def entropy(p):
    return -torch.sum(p * p.log2())


def log_entropy(logp):
    """entropy of logits, where logits are in nats."""
    return -(logp.exp() * logp).sum() * (NATS_TO_BITS)


def sample_action_from_logp(logp):
    """ Returns integer [0..len(probs)-1] based on log probabilities. """

    # todo: switch to direct sample from logps
    # this would probably work...
    # u = tf.random_uniform(tf.shape(self.logits), dtype=self.logits.dtype)
    # return tf.argmax(self.logits - tf.log(-tf.log(u)), axis=-1)

    p = np.asarray(np.exp(logp), dtype=np.float64)

    # this shouldn't happen, but sometimes does
    if any(np.isnan(p)):
        raise Exception("Found nans in probabilities", p)

    p /= p.sum()  # probs are sometimes off by a little due to precision error
    return np.random.choice(range(len(p)), p=p)


def smooth(X, alpha=0.98):
    y = X[0]
    results = []
    for x in X:
        y = (1 - alpha) * x + (alpha) * y
        results.append(y)
    return results


def safe_mean(X, rounding=None):
    result = float(np.mean(X)) if len(X) > 0 else None
    if rounding is not None and result is not None:
        return round(result, rounding)
    else:
        return result


def safe_round(x, digits):
    return round(x, digits) if x is not None else x


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


def nice_display(X, title):
    print("{:<20}{}".format(title, [round(float(x),2) for x in X[:5]]))


# -------------------------------------------------------------
# Algorithms
# -------------------------------------------------------------


def dtw(obs1, obs2):
    """
        Calculates the distances between two observation sequences using dynamic time warping.
        obs1, obs2
            np array [N, C, W, H], where N is number of frames (they don't need to mathc), and C is channels which
                                   should be 1.
        ref: https://en.wikipedia.org/wiki/Dynamic_time_warping
    """

    n = obs1.shape[0]
    m = obs2.shape[0]

    DTW = np.zeros((n+1,m+1), dtype=np.float32) + float("inf")
    DTW[0,0] = 0

    obs1 = np.float32(obs1)
    obs2 = np.float32(obs2)

    for i in range(1,n+1):
        for j in range(1,m+1):
            cost = mse(obs1[i-1], obs2[j-1])
            DTW[i,j] = cost + min(
                DTW[i - 1, j],
                DTW[i, j - 1],
                DTW[i - 1, j - 1]
            )

    return DTW[n, m]

# -------------------------------------------------------------
# CUDA
# -------------------------------------------------------------

def get_auto_device():
    """ Returns the best device, CPU if no CUDA, otherwise GPU with most free memory. """
    if not torch.cuda.is_available():
        return "cpu"

    if torch.cuda.device_count() == 1:
        return "cuda"
    else:
        # use the device with the most free memory.
        try:
            os.system('nvidia-smi -q -d Memory |grep -A4 GPU|grep Free >tmp')
            memory_available = [int(x.split()[2]) for x in open('tmp', 'r').readlines()]
            return "cuda:"+str(np.argmax(memory_available))
        except:
            print("Warning: Failed to auto detect best GPU.")
            return "cuda"

