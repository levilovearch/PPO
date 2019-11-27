import uuid
import socket
import argparse
import torch
from . import utils

class Config:

    def __init__(self, **kwargs):
        # put these here just so IDE can detect common parameters...
        self.environment = ""
        self.experiment_name = ""
        self.run_name = ""
        self.agents = 0
        self.filter = ""

        self.hash_size = 0
        self.restore = False

        self.gamma = 0.0
        self.gae_lambda = 0.0
        self.ppo_epsilon = 0.0
        self.vf_coef = 0.0
        self.max_grad_norm = 0.0

        self.input_crop = False
        self.learning_rate = 0.0
        self.workers = 0
        self.n_steps = 0
        self.epochs = 0
        self.limit_epochs = 0
        self.batch_epochs = 0
        self.reward_clip = 0.0
        self.mini_batch_size = 0
        self.sync_envs = False
        self.resolution = ""
        self.color = False
        self.entropy_bonus = 0.0
        self.threads = 0
        self.dtype = ""
        self.export_video = False
        self.device = ""
        self.save_checkpoints = False
        self.output_folder = ""
        self.hostname = ""
        self.sticky_actions = False
        self.model = None
        self.guid = ""
        self.memorize_cards = 0

        self.log_folder = ""

        self.__dict__.update(kwargs)

    def update(self, **kwargs):
        self.__dict__.update(kwargs)

LOCK_KEY = str(uuid.uuid4().hex)

# debugging variables.
PROFILE_INFO = False
VERBOSE = True
PRINT_EVERY = 10
SAVE_LOG_EVERY = 50
args = Config()

def parse_args():

    parser = argparse.ArgumentParser(description="Trainer for PPO2")

    parser.add_argument("environment")

    parser.add_argument("--experiment_name", type=str, default="Run", help="Name of the experiment.")
    parser.add_argument("--run_name", type=str, default="run", help="Name of the run within the experiment.")

    parser.add_argument("--agents", type=int, default=8)

    parser.add_argument("--filter", type=str, default="none",
                        help="Add filter to agent observation ['none', 'hash']")
    parser.add_argument("--hash_size", type=int, default=42, help="Adjusts the hash tempalte generator size.")
    parser.add_argument("--restore", type=utils.str2bool, default=False,
                        help="Restores previous model if it exists. If set to false and new run will be started.")

    parser.add_argument("--gamma", type=float, default=0.99, help="Discount rate.")
    parser.add_argument("--gae_lambda", type=float, default=0.95, help="GAE parameter.")
    parser.add_argument("--ppo_epsilon", type=float, default=0.1, help="PPO epsilon parameter.")
    parser.add_argument("--vf_coef", type=float, default=0.5, help="Value function coefficient.")
    parser.add_argument("--max_grad_norm", type=float, default=0.5, help="Clip gradients during training to this.")

    parser.add_argument("--input_crop", type=utils.str2bool, default=False, help="Enables atari input cropping.")
    parser.add_argument("--learning_rate", type=float, default=2.5e-4, help="Learning rate for adam optimizer")
    parser.add_argument("--workers", type=int, default=-1, help="Number of CPU workers, -1 uses number of CPUs")
    parser.add_argument("--n_steps", type=int, default=128, help="Number of environment steps per training step.")
    parser.add_argument("--epochs", type=int, default=200,
                        help="Each epoch represents 1 million environment interactions.")
    parser.add_argument("--limit_epochs", type=int, default=None, help="Train only up to this many epochs.")
    parser.add_argument("--batch_epochs", type=int, default=4, help="Number of training epochs per training batch.")
    parser.add_argument("--reward_clip", type=float, default=5.0)
    parser.add_argument("--mini_batch_size", type=int, default=1024)
    parser.add_argument("--sync_envs", type=utils.str2bool, nargs='?', const=True, default=False,
                        help="Enables synchronous environments (slower).")
    parser.add_argument("--resolution", type=str, default="standard", help="['full', 'standard', 'half']")
    parser.add_argument("--color", type=utils.str2bool, nargs='?', const=True, default=False)
    parser.add_argument("--entropy_bonus", type=float, default=0.01)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--dtype", type=str, default=torch.float)
    parser.add_argument("--export_video", type=utils.str2bool, default=True)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--save_checkpoints", type=utils.str2bool, default=True)
    parser.add_argument("--output_folder", type=str, default="./")
    parser.add_argument("--hostname", type=str, default=socket.gethostname())
    parser.add_argument("--sticky_actions", type=utils.str2bool, default=False)
    parser.add_argument("--model", type=str, default="cnn", help="['cnn', 'improved_cnn']")
    parser.add_argument("--guid", type=str, default=None)

    parser.add_argument("--memorize_cards", type=int, default=100, help="Memorize environment: Number of cards in the game.")

    args.update(**parser.parse_args().__dict__)
