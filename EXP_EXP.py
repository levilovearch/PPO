"""
Exploration experiments

"""

from runner_tools import WORKERS, add_job, random_search, Categorical
from runner_tools import __PPO_reference_args, __DNA_reference_args, __TVF_reference_args, __TVF99_reference_args, __RP1U_reference_args
from runner_tools import RP1U_reference_args
from runner_tools import ROLLOUT_SIZE, ATARI_57, HARD_MODE, EASY_MODE, RAINBOW_MODE, ATARI_57, PPO_reference_args, \
    TVF_reference_args, DNA_reference_args

DEFAULT = __RP1U_reference_args.copy()
DEFAULT.update({
    'use_compression': False,
    'replay_size': 16 * 1024,
    'distil_batch_size': 16 * 1024,
    'policy_mini_batch_size': 2048,   # makes things more stable
    'tvf_return_mode': "geometric",   # seems to work well as a default
    # 'policy_replay_constraint': 1.0,  # also helps with stability
    'agents': 32,                     # 32 agents is mostly for performance reasons
    'n_steps': 512,                   # 1024, works better but 512 is better on memory, and 30-seconds seems right.
})

# remove any obsolete args
del DEFAULT["time_aware"]
del DEFAULT["tvf_exp_gamma"]
del DEFAULT["tvf_mode"]
del DEFAULT["tvf_exp_mode"]
del DEFAULT["tvf_n_step"]

def exp1(priority: int = 0):
    """
    Investigation into exploration options for RND
    """

    for env in ["MontezumaRevenge", "Pitfall", "Seaquest", "PrivateEye"]:
        for seed in [1]:
            COMMON_ARGS = {
                'experiment_name': f"EXP_{env}",
                'env_name': env,
                'seed': seed,
                'hostname': "",
                'epochs': 50,
                'anneal_target_epoch': 50,
                'extrinsic_reward_scale': 1.0,
                'intrinsic_reward_scale': 0.5,
                # replay buffer is mostly used to generate a diversity measure
                'distil_epochs': 1,
                'distil_period': 1,
                'replay_size': 1 * ROLLOUT_SIZE,
                'distil_batch_size': 1 * ROLLOUT_SIZE,
                'gamma': 0.99,
                'tvf_gamma': 0.99,
                'tvf_max_horizon': 300,
                'timeout': 18000,
            }

            # baseline
            add_job(
                run_name=f"reference ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                **COMMON_ARGS,
            )

            # rnd
            add_job(
                run_name=f"rnd ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                use_rnd=True,
                **COMMON_ARGS,
            )

            # by disagreement
            add_job(
                run_name=f"ebd_full ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                use_ebd=True,
                **COMMON_ARGS,
            )
            add_job(
                run_name=f"ebd_simple ({seed})",
                default_params=__TVF_reference_args,
                tvf_force_ext_value_distil=True,
                priority=priority,
                use_ebd=True,
                **COMMON_ARGS,
            )

            # by replay buffer diversity
            add_job(
                run_name=f"erp_mean ({seed})",
                default_params=__TVF_reference_args,
                priority=priority+10,
                erp_reduce='mean',
                use_erp=True,
                **COMMON_ARGS,
            )

            # by replay buffer diversity
            add_job(
                run_name=f"erp_min ({seed})",
                default_params=__TVF_reference_args,
                priority=priority+10,
                erp_reduce='min',
                use_erp=True,
                **COMMON_ARGS,
            )

            if env in ["Seaquest", "MontezumaRevenge"]:
                # this did really well, see if we can repeat the result... (using exact code)
                COMMON_ARGS['experiment_name'] = f"EXP0_{env}"
                COMMON_ARGS["seed"] = 1
                COMMON_ARGS["epochs"] = 10 if env == "MontezumaRevenge" else 20
                add_job(
                    run_name=f"erp_min (1)",
                    default_params=__TVF_reference_args,
                    priority=priority + 30,
                    erp_reduce='min',
                    use_erp=True,
                    **COMMON_ARGS,
                )
                COMMON_ARGS["seed"] = 2
                add_job(
                    run_name=f"erp_min (2)",
                    default_params=__TVF_reference_args,
                    priority=priority + 30,
                    erp_reduce='min',
                    use_erp=True,
                    **COMMON_ARGS,
                )

                # this did really well, see if we can repeat the result...
                COMMON_ARGS["seed"] = 3
                add_job(
                    run_name=f"erp_min (3)",
                    default_params=__TVF_reference_args,
                    priority=priority + 30,
                    erp_reduce='min',
                    use_erp=True,
                    **COMMON_ARGS,
                )

def exp2(priority: int = 0):

    for env in ["MontezumaRevenge", "Seaquest"]:
        for seed in [1]:
            COMMON_ARGS = {
                'experiment_name': f"EXP2_{env}",
                'env_name': env,
                'seed': seed,
                'hostname': "",
                'epochs': 50,
                'anneal_target_epoch': 50,
                'er_scale': 1.0,
                'ir_scale': 1.0,
                'ir_anneal': True,
                # replay
                'distil_epochs': 1,
                'distil_period': 1,
                'replay_size': 1 * ROLLOUT_SIZE,
                'distil_batch_size': 1 * ROLLOUT_SIZE,
                # discounting set to easy mode
                'gamma': 0.99,
                'tvf_gamma': 0.99,
                'tvf_max_horizon': 300,
            }

            # baseline
            add_job(
                run_name=f"reference ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                **COMMON_ARGS,
            )

            # rnd
            add_job(
                run_name=f"rnd ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                use_rnd=True,
                **COMMON_ARGS,
            )

            # by replay buffer diversity
            add_job(
                run_name=f"erp ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10,
                use_erp=True,
                **COMMON_ARGS,
            )

            # by replay buffer diversity
            add_job(
                run_name=f"erp_top5 ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10,
                use_erp=True,
                erp_reduce="k5",
                **COMMON_ARGS,
            )

            # by replay buffer diversity
            add_job(
                run_name=f"erp_whiten ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10,
                erp_reduce='min',
                use_erp=True,
                erp_whiten=True,
                **COMMON_ARGS,
            )

def exp3(priority: int = 0):

    for env in ["MontezumaRevenge", "Seaquest", "Alien"]:
        for seed in [1, 2, 3]:
            COMMON_ARGS = {
                'experiment_name': f"EXP3_{env}",
                'env_name': env,
                'seed': seed,
                'hostname': "",
                'epochs': 10,
                'anneal_target_epoch': 50,
                'er_scale': 1.0,
                'ir_anneal': True,
                # replay
                'distil_epochs': 1,
                'distil_period': 1,
                'replay_size': 1 * ROLLOUT_SIZE,
                'distil_batch_size': 1 * ROLLOUT_SIZE,
                'replay_mode': "uniform",
                # discounting set to easy mode
                'gamma': 0.99,
                'tvf_gamma': 0.99,
                'tvf_max_horizon': 300,
            }

            # baseline
            add_job(
                run_name=f"reference ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                **COMMON_ARGS,
            )

            # rnd
            add_job(
                run_name=f"rnd ({seed})",
                default_params=__TVF_reference_args,
                priority=priority,
                use_rnd=True,
                **COMMON_ARGS,
            )

            # rel
            for ir_scale in [0.1, 0.3, 1.0]:
                # by replay buffer diversity
                add_job(
                    run_name=f"erp_{ir_scale} ({seed})",
                    default_params=__TVF_reference_args,
                    ir_scale=ir_scale,
                    priority=priority + 10,
                    use_erp=True,
                    **COMMON_ARGS,
                )

            add_job(
                run_name=f"erp_whiten ({seed})",
                default_params=__TVF_reference_args,
                erp_white=True,
                priority=priority + 10,
                use_erp=True,
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"erp_raw ({seed})",
                default_params=__TVF_reference_args,
                erp_relu=False,
                priority=priority + 10,
                use_erp=True,
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"erp_top5 ({seed})",
                default_params=__TVF_reference_args,
                erp_reduce="top5",
                priority=priority + 10,
                use_erp=True,
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"erp_no_distil_ir ({seed})",
                default_params=__TVF_reference_args,
                distil_ir=False,
                priority=priority + 10,
                use_erp=True,
                **COMMON_ARGS,
            )


def exp4(priority: int = 0, hostname="desktop"):

    # copy reference and rnd from before...

    for env in ["MontezumaRevenge", "Seaquest", "Alien", "Pitfall", "Breakout", "PrivateEye"]:

        for seed in [1, 2, 3]: # todo: add seed 5

            if seed > 3 and env not in ["MontezumaRevenge", "PrivateEye", "Pitfall"]:
                # only hard exploration needs extra samples (due to high variance...)
                continue

            COMMON_ARGS = {
                'experiment_name': f"EXP4b_{env}",
                'env_name': env,
                'seed': seed,
                'hostname': hostname,
                'epochs': 10,
                'anneal_target_epoch': 50,
                'er_scale': 1.0,
                # replay
                'distil_epochs': 1,
                'distil_period': 1,
                'replay_size': 1 * ROLLOUT_SIZE,
                'distil_batch_size': 1 * ROLLOUT_SIZE,
                'replay_mode': "uniform",
                # discounting set to easy mode
                'gamma': 0.99,
                'tvf_gamma': 0.99,
                'tvf_max_horizon': 300,
                # faster
                'use_compression': False,
            }

            add_job(
                run_name=f"erp_rnd ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10 + (20 if seed == 1 else 0),
                use_rnd=True,
                use_erp=True,
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"ebd ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 15 + (20 if seed == 1 else 0),
                use_ebd=True,
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"erp_rnd rollout ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 20 + (20 if seed == 1 else 0),
                erp_source="rollout",
                use_rnd=True,
                use_erp=True,
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"erp_rnd_ebd ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10 + (20 if seed == 1 else 0),
                use_rnd=True,
                use_erp=True,
                use_ebd=True,
                **COMMON_ARGS,
            )

            COMMON_ARGS = {
                'experiment_name': f"EXP4_{env}",
                'env_name': env,
                'seed': seed,
                'hostname': hostname,
                'epochs': 10,
                'anneal_target_epoch': 50,
                'er_scale': 1.0,
                # replay
                'distil_epochs': 1,
                'distil_period': 1,
                'replay_size': 1 * ROLLOUT_SIZE,
                'distil_batch_size': 1 * ROLLOUT_SIZE,
                'replay_mode': "uniform",
                # discounting set to easy mode
                'gamma': 0.99,
                'tvf_gamma': 0.99,
                'tvf_max_horizon': 300,
                # erp
                'use_erp': True,
                # faster
                'use_compression': False,
            }

            add_job(
                run_name=f"erp [reference] ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10 + (20 if seed == 1 else 0),
                **COMMON_ARGS,
            )

            add_job(
                run_name=f"rnd [reference] ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10 + (20 if seed == 1 else 0),
                use_rnd=True,
                **{k: v for k, v in COMMON_ARGS.items() if k != "use_erp"},
            )

            add_job(
                run_name=f"rp1u [reference] ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10 + (20 if seed == 1 else 0),
                **{k:v for k,v in COMMON_ARGS.items() if k != "use_erp"},
            )

            # add_job(
            #     run_name=f"erp no_int_prop ({seed})",
            #     default_params=TVF_reference_args,
            #     priority=priority + 10 + (20 if seed == 1 else 0),
            #     intrinsic_reward_propagation=False,
            #     **COMMON_ARGS,
            # )

            # my guess is that this one is bad
            # default is off
            add_job(
                run_name=f"erp distil_ir=True ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + 10 + (20 if seed == 1 else 0),
                distil_ir=True,
                **COMMON_ARGS,
            )

            # default is off
            add_job(
                run_name=f"erp erp_exclude_zero=True ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + (20 if seed == 1 else 0),
                erp_exclude_zero=True,
                **COMMON_ARGS,
            )

            # default is true
            add_job(
                run_name=f"erp erp_relu=False ({seed})",
                default_params=__TVF_reference_args,
                priority=priority + (20 if seed == 1 else 0),
                erp_relu=False,
                **COMMON_ARGS,
            )

            # default is min
            for erp_reduce in ["mean", "top5"]:
                add_job(
                    run_name=f"erp erp_reduce={erp_reduce} ({seed})",
                    default_params=__TVF_reference_args,
                    priority=priority + (20 if seed == 1 else 0),
                    erp_reduce=erp_reduce,
                    **COMMON_ARGS,
                )

            for ir_anneal in ["cos"]:  # default is off
                add_job(
                    run_name=f"erp anneal={ir_anneal} ({seed})",
                    ir_scale=0.6,
                    default_params=__TVF_reference_args,
                    priority=priority + (20 if seed == 1 else 0),
                    ir_anneal=ir_anneal,
                    **COMMON_ARGS,
                )
            for ir_scale in [0.15, 0.6]:
                add_job(
                    run_name=f"erp ir_scale={ir_scale} ({seed})",
                    ir_scale=ir_scale,
                    default_params=__TVF_reference_args,
                    priority=priority + 50 + (20 if seed == 1 else 0),
                    **COMMON_ARGS,
                )

            for erp_source in ["rollout", "both"]:  # default is replay
                add_job(
                    run_name=f"erp erp_source={erp_source} ({seed})",
                    default_params=__TVF_reference_args,
                    priority=priority + 5 + (20 if seed == 1 else 0),
                    erp_source=erp_source,
                    **COMMON_ARGS,
                )

            COMMON_ARGS['experiment_name'] = f"EXP4c_{env}"

            # # this is really erp_bias=none
            # add_job(
            #     run_name=f"erp erp_centered=False ({seed})",
            #     default_params=TVF_reference_args,
            #     priority=priority + 10 + (20 if seed == 1 else 0),
            #     erp_centered=False,
            #     intrinsic_reward_propagation=True,
            #     **COMMON_ARGS,
            # )

            # add_job(
            #     run_name=f"erp erp_centered=False irp=False ({seed})",
            #     default_params=TVF_reference_args,
            #     priority=priority + 10 + (20 if seed == 1 else 0),
            #     erp_centered=False,
            #     intrinsic_reward_propagation=False,
            #     **COMMON_ARGS,
            # )

            # code change to fix normalization when irp as off...
            # also fixed a bug where internal_distance is now more stable... therefore running reference again...
            COMMON_ARGS['experiment_name'] = f"EXP4f_{env}"

            for bias in ["none", "centered", "internal"]:
                for prop in [True, False]:
                    add_job(
                        run_name=f"erp bias={bias}{' irp ' if prop else ' '}({seed})",
                        default_params=__TVF_reference_args,
                        priority=priority + 25 + (20 if seed == 1 else 0),
                        erp_bias=bias,
                        intrinsic_reward_propagation=prop,
                        **COMMON_ARGS,
                    )


def rc():
    for env in ["Breakout", "Alien", "CrazyClimber"]:
        for replay_constraint in [0, 1.0, 10.0, 3.0, 0.1, 0.01]:
            add_job(
                experiment_name="RC1",
                run_name=f"env={env} rc={replay_constraint}",
                env_name=env,
                seed=1,
                default_params=__RP1U_reference_args,
                replay_constraint=replay_constraint,
                epochs=50,
                priority=100,
            )
        replay_constraint = 10.0
        add_job(
            experiment_name="RC2",
            run_name=f"env={env} rc={replay_constraint} (linear_inc)",
            env_name=env,
            seed=1,
            default_params=__RP1U_reference_args,
            replay_constraint=replay_constraint,
            replay_constraint_anneal="linear_inc",
            epochs=50,
            priority=100,
            use_compression=False,
        )
        add_job(
            experiment_name="RC2",
            run_name=f"env={env} rc={replay_constraint} (quad_inc)",
            env_name=env,
            seed=1,
            default_params=__RP1U_reference_args,
            replay_constraint=replay_constraint,
            replay_constraint_anneal="quad_inc",
            epochs=50,
            priority=100,
            use_compression=False,
        )
        if env == "CrazyClimber":
            # RC3 gives us advantages.
            # maybe if advantages are very low we shouldn't apply an update? or should update more slowly? or beta should be higher?

            ERP_ARGS = {
                'erp_exclude_zero': False, # excluding zero adds a lot of bias especially when agent gets stuck.
                'use_compression': False,
                'use_erp': True,
                'erp_source': "both",
                'epochs': 10,
                'anneal_target_epoch': 50,
                'priority': 110,
                'seed': 1,
                'env_name': env,
                'default_params': __RP1U_reference_args,
            }

            add_job(
                experiment_name="RC3",
                run_name=f"env={env} erp_off", # we can use this one to monitor the rollout_internal distance
                **ERP_ARGS,
                ir_scale=0.0,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} erp_totally_off",  # we can use this one to monitor the rollout_internal distance
                erp_exclude_zero=False,  # excluding zero adds a lot of bias especially when agent gets stuck.
                use_compression=False,
                use_erp=True,
                erp_source="both",
                epochs=10,
                anneal_target_epoch=50,
                priority=110,
                seed=1,
                env_name=env,
                default_params=__RP1U_reference_args,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} erp_on",
                **ERP_ARGS,
            )
            # some random tests to see what's going on here...
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} toll",  # we can use this one to monitor the rollout_internal distance
                **ERP_ARGS,
                ir_scale=0.0,
                terminal_on_loss_of_life=True,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} reward_clipping",  # we can use this one to monitor the rollout_internal distance
                **ERP_ARGS,
                ir_scale=0.0,
                reward_clipping=1,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} gamma=0.99",
                **ERP_ARGS,
                ir_scale=0.0,
                gamma=0.99,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} ent=0.01",
                **ERP_ARGS,
                ir_scale=0.0,
                entropy_bonus=0.01,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} ent=0.0001",
                **ERP_ARGS,
                ir_scale=0.0,
                entropy_bonus=0.0001,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} warmpup=10k",
                **ERP_ARGS,
                ir_scale=0.0,
                warmup_period=10000,
            )
            add_job(
                experiment_name="RC3",
                run_name=f"env={env} warmpup=0",
                **ERP_ARGS,
                ir_scale=0.0,
                warmup_period=0,
            )
    # rc4, see if we can fix things with modifications to advantage normalization
    # the idea here is that as advantages get small the relative weight betweeen policy updates and entropy bonus /
    # RC changes. That is, because advantages are scaled 50x, entropy bonus is realtively 50x smaller, therefore
    # entropy will collapse.
    for env in ["CrazyClimber"]:

        RC4 = __RP1U_reference_args.copy()
        RC4.update({
            'use_compression': False,
            'epochs': 10,
            'anneal_target_epoch': 50,
            'priority': 120,
            'seed': 1,
            'env_name': env,
        })

        add_job(
            experiment_name="RC4",
            run_name=f"env={env} ref",
            default_params=RC4,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} ppo",
            default_params=__PPO_reference_args,
            use_compression= False,
            epochs=10,
            anneal_target_epoch=50,
            priority=120,
            seed=1,
            env_name=env,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} slow",
            epochs=20,
            value_lr=1e-4,
            policy_lr=1e-4,
            distil_lr=1e-4,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} slow2",
            epochs=20,
            value_lr=2.5e-4,
            policy_lr=1e-4,
            distil_lr=1e-4,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} ref 99",
            gamma=0.99,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} ref 999",
            gamma=0.999,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} ref 9999",
            gamma=0.9999,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} clan",
            default_params=RC4,
            advantage_clipping=5,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} nan",
            epochs=20,
            default_params=RC4,
            normalize_advantages="off",
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} can",
            default_params=RC4,
            normalize_advantages="center",
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} san",
            default_params=RC4,
            normalize_advantages="norm",
            advantage_epsilon=0.1,
        )

        add_job(
            experiment_name="RC4",
            run_name=f"env={env} san mixing",
            default_params=RC4,
            replay_mixing=True,
            advantage_epsilon=0.1,
        )
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} san no_distil",
            default_params=RC4,
            distil_epochs=0,
            advantage_epsilon=0.1,
        )
        # add entropy bonus tests and rc tests
        for rc in [0.1, 0.3, 1, 3]:
            add_job(
                experiment_name="RC4",
                run_name=f"env={env} san rc={rc}",
                default_params=RC4,
                epochs=30,
                advantage_epsilon=0.1,
                replay_constraint=rc,
            )
        for eb in [0, 0.01, 0.0001]: # expect lower to be better with SAN
            add_job(
                experiment_name="RC4",
                run_name=f"env={env} san eb={eb}",
                default_params=RC4,
                advantage_epsilon=0.1,
                entropy_bonus=eb,
            )
        # rc instead of entropy bonus
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} san eb=0 rc=1.0",
            default_params=RC4,
            advantage_epsilon=0.1,
            entropy_bonus=0,
            replay_constraint=1.0,
        )
        # adjusting for advantages now being 3x smaller
        add_job(
            experiment_name="RC4",
            run_name=f"env={env} san eb=0.0003 rc=1.0",
            default_params=RC4,
            advantage_epsilon=0.1,
            epochs=50,
            entropy_bonus=0.0003,
            replay_constraint=1.0,
        )

        # these are the delta-v experiments
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} ref",
            log_delta_v=True,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} hs=128",
            log_delta_v=True,
            tvf_horizon_samples=128,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} hs=16",  # default was 32... I expect 16 to have problems
            log_delta_v=True,
            tvf_horizon_samples=16,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} hs=8",  # default was 32... I expect 16 to have problems
            log_delta_v=True,
            tvf_horizon_samples=8,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} ref 99",
            log_delta_v=True,
            gamma=0.99,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} ref 99x99",
            gamma=0.99,
            tvf_gamma=0.99,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} ref 99_short",
            log_delta_v=True,
            gamma=0.99,
            tvf_gamma=0.99,
            tvf_max_horizon=300,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} huber",
            log_delta_v=True,
            tvf_loss_fn="huber", # see if this makes things more stable
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} exp=1.25",
            log_delta_v=True,
            epochs=20,
            tvf_exp_gamma=1.25,  # if this works, make exp faster...
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} exp=2.0",
            log_delta_v=True,
            epochs=20,
            tvf_exp_gamma=2.0,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} wider",
            log_delta_v=True,
            use_compression=True,
            epochs=20,
            agents=512,
            replay_size=128 * 512,
            distil_batch_size=128 * 512,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} agents=256",
            log_delta_v=True,
            use_compression=True,
            epochs=20,
            agents=256,
            replay_size=128 * 256,
            distil_batch_size=128 * 256,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} longer",
            log_delta_v=True,
            use_compression=True,
            epochs=50,
            n_steps=512,
            replay_size=128*512,
            distil_batch_size=128*512,
            default_params=RC4,
        )

        # add_job(
        #     experiment_name="RC5",
        #     run_name=f"env={env} agents=32 n_steps=512 adv (2x)",
        #     log_delta_v=True,
        #     use_compression=True,
        #     epochs=30,
        #     agents=32,
        #     n_steps=1024,
        #     replay_size=64 * 512,
        #     distil_batch_size=64 * 512,
        #     policy_mini_batch_size=2048,
        #     policy_replay_constraint=1.0,  # this is just so we don't do too much damage with those bad updates...
        #     entropy_bonus=0.0003,  # prc helps like an entropy bonus
        #     default_params=RC4,
        # )

        # add_job(
        #     experiment_name="RC5",
        #     run_name=f"env={env} agents=32 n_steps=512 adv (1x)",
        #     log_delta_v=True,
        #     use_compression=True,
        #     epochs=30,
        #     agents=32,
        #     n_steps=1024,
        #     replay_size=32*512,
        #     distil_batch_size=32*512,
        #     policy_mini_batch_size=2048,
        #     policy_replay_constraint=1.0,  # this is just so we don't do too much damage with those bad updates...
        #     entropy_bonus=0.0003,  # prc helps like an entropy bonus
        #     default_params=RC4,
        # )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} n_steps=256",
            log_delta_v=True,
            use_compression=True,
            epochs=20,
            n_steps=256,
            replay_size=128 * 256,
            distil_batch_size=128 * 256,
            default_params=RC4,
        )
        add_job(
            experiment_name="RC5",
            run_name=f"env={env} mbs=2k",
            log_delta_v=True,
            use_compression=True,
            epochs=20,
            policy_mini_batch_size=2048,
            value_mini_batch_size=2048,
            distil_mini_batch_size=2048,
            default_params=RC4,
        )

        for vrc in [0, 0.01, 0.03, 0.1, 0.3, 1, 3]:
            add_job(
                experiment_name="RC5",
                run_name=f"env={env} san prc=1 vrc={vrc}",
                default_params=RC4,
                epochs=30,
                log_delta_v=True,
                policy_replay_constraint=1.0,
                value_replay_constraint=vrc,
                advantage_epsilon=0.1,
            )

        # action encoding
        RC4['priority'] = 142
        add_job(
            experiment_name="RC6",
            run_name=f"env={env} action_aware",
            log_delta_v=True,
            action_aware=True,
            default_params=RC4,
        )

        del RC4["time_aware"]

        # checking why we are predicting negative values (with better logging)
        add_job(
            experiment_name="RC7",
            run_name=f"env={env} ref",
            default_params=RC4,
        )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} rediscount=99",
            gamma=0.99,
            default_params=RC4,
        )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} rediscount2=99",
            gamma=0.99,
            override_reward_normalization_gamma=0.99997,
            default_params=RC4,
        )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} short",
            gamma=0.99,
            tvf_gamma=0.99,
            tvf_max_horizon=300,
            default_params=RC4,
        )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} hs=128",
            tvf_horizon_samples=128,
            default_params=RC4,
        )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} hs=512",
            tvf_horizon_samples=512,
            default_params=RC4,
        )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} tvf_hu=512",
            tvf_hidden_units=512,
            default_params=RC4,
        )

        for distribution in ["geometric", "linear", "fixed_linear"]:
            add_job(
                experiment_name="RC7",
                run_name=f"env={env} dist={distribution}",
                tvf_value_distribution=distribution,
                tvf_horizon_distribution=distribution,
                default_params=RC4,
            )

        add_job(
            experiment_name="RC7",
            run_name=f"env={env} av_reward",
            default_params=RC4,
            tvf_value_scale_fn="linear",
        )
        add_job(
            experiment_name="RC7",
            run_name=f"env={env} log_reward",
            default_params=RC4,
            tvf_value_scale_fn="log",
        )

        # RC 8: try extra hard to get cc stable
        # also see if we can get ev_10 good on rediscounting

        add_job(
            experiment_name="RC8",
            run_name=f"env={env} rd ref",
            use_compression=True,
            gamma=0.99,
            epochs=10,
            default_params=RC4,
        )

        add_job(
            experiment_name="RC8",
            run_name=f"env={env} rd max",
            use_compression=True,
            tvf_horizon_samples=128,
            tvf_value_samples=256,
            agents=32,
            n_steps=1024,
            tvf_value_distribution="geometric",
            tvf_horizon_distribution="geometric",
            gamma=0.99,
            epochs=10,
            default_params=RC4,
        )

        for agents in [32, 64]:
            for n_steps in [256, 512, 1024]:
                for adv in [True, False]:
                    adv_options = {
                        'policy_replay_constraint': 1.0,  # this is just so we don't do too much damage with those bad updates...
                        'entropy_bonus': 0.0003,  # prc helps like an entropy bonus
                    }
                    add_job(
                        experiment_name="RC8",
                        run_name=f"env={env} agents={agents} n_steps={n_steps} {'adv' if adv else ''}",
                        use_compression=True,
                        epochs=50 if n_steps==1024 else 30,
                        agents=agents,
                        n_steps=n_steps,
                        replay_size=agents*n_steps,
                        distil_batch_size=agents*n_steps,
                        **(adv_options if adv else {}),
                        default_params=RC4,
                    )
        add_job(
            experiment_name="RC8",
            run_name=f"env={env} agents={128} n_steps={128} [ref]",
            use_compression=True,
            epochs=30,
            agents=128,
            n_steps=128,
            replay_size=agents * n_steps,
            distil_batch_size=agents * n_steps,
            default_params=RC4,
        )

        RC9 = RC4.copy()
        RC9.update({
            'n_steps': 1024,
            'agents': 32,
            'replay_size': 2 * 32 * 1024,
            'distil_batch_size': 2 * 32 * 1024,
            'use_compression': True,
            'epochs': 30,
            'priority': 150,
        })

        # Make sure the new exp mode works, and compare against the old one...
        for tvf_mode in ["exponential", "exponential_old", "exponential_masked"]:
            add_job(
                experiment_name="RC9",
                run_name=f"env={env} {tvf_mode} 32x1024 2u",
                tvf_mode=tvf_mode,
                default_params=RC9,
            )
        # see if DNA is stable as well
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u (dna)",
            use_tvf=False,
            default_params=RC9,
        )
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 (ppo)",
            use_tvf=False,
            architecture="single",
            replay_size=0,
            distil_epochs=0,
            default_params=RC9,
        )
        # this is a bit of a guess for fast stable learning...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 16x1024 2u rc=1.0",
            n_steps=1024,
            agents=16,
            replay_size=2 * 16 * 1024,
            distil_batch_size=2 * 16 * 1024,
            policy_replay_constraint=1.0,
            epochs=30,
            default_params=RC9,
        )
        # see if low exp_gamma helps...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u tvf_exp_gamma=1.1",
            tvf_exp_gamma=1.1,
            default_params=RC9,
        )
        # see if rc helps...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u rc=1.0",
            policy_replay_constraint=1.0,
            default_params=RC9,
        )
        # see if policy batch_size helps...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u p_mbs=2k",
            policy_mini_batch_size=2048,
            default_params=RC9,
        )
        # see if value batch_size helps...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u v_mbs=2k",
            value_mini_batch_size=2048,
            default_params=RC9,
        )
        # see if less distil helps...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u dbs=1x",
            distil_batch_size=1 * 32 * 1024,
            default_params=RC9,
        )
        # see if less replay is ok...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 1u",
            replay_size=1 * 32 * 1024,
            distil_batch_size=1 * 32 * 1024,
            default_params=RC9,
        )
        # see if high exp_gamma helps...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 32x1024 2u tvf_exp_gamma=2.0",
            tvf_exp_gamma=2.0,
            default_params=RC9,
        )
        # see if very fat works...
        add_job(
            experiment_name="RC9",
            run_name=f"env={env} 128x1024 1u ",
            policy_replay_constraint=1.0,
            ppo_epsilon=0.3,
            replay_size=1 * 128 * 1024,
            distil_batch_size=1 * 128 * 1024,
            default_params=RC9,
        )

def abs(priority=50):
    """
    adaptive batch size
    """

    for env in ["CrazyClimber", "Breakout", "Alien", "Pong"]: # also alien...
        for abs in ["shadow", "on"]:
            add_job(
                env_name=env,
                experiment_name="ABS2",
                run_name=f"env={env} abs={abs}",
                abs_mode=abs,
                use_compression=False,
                priority=priority,
                hostname='desktop',
                default_params=__RP1U_reference_args,
            )
        # this is just to see if hard coding the found values works better...
        add_job(
            env_name=env,
            experiment_name="ABS2",
            run_name=f"env={env} 321 2048-128-128",
            use_compression=False,
            priority=priority,
            policy_mini_batch_size=2048,
            value_mini_batch_size=128,
            distil_mini_batch_size=128,
            hostname='desktop',
            epochs=20,
            default_params=__RP1U_reference_args,
        )
        add_job(
            env_name=env,
            experiment_name="ABS2",
            run_name=f"env={env} 621 8096-128-128",
            use_compression=False,
            priority=priority,
            policy_epochs=6,
            policy_mini_batch_size=8192,
            value_mini_batch_size=128,
            distil_mini_batch_size=128,
            hostname='desktop',
            epochs=20,
            default_params=__RP1U_reference_args,
        )

def re1(priority=50):

    # return estimation

    for env in ["CrazyClimber", "Alien", "Breakout"]:

        # this is designed to be fast (both in terms of learning speed, and computation)
        RE1 = __RP1U_reference_args.copy()
        RE1.update({
            'use_compression': False,
            'epochs': 30,
            'priority': 100 if env == "CrazyClimber" else 95,
            'seed': 1,
            'env_name': env,
            'ppo_epsilon': 0.1,               # 0.2 was a bad idea... 0.15 is probably the best...
            'replay_size': 16 * 1024,
            'distil_batch_size': 16 * 1024,
            'policy_mini_batch_size': 2048,   # makes things more stable
            'tvf_return_mode': "geometric",   # seems to work well as a default
            # 'policy_replay_constraint': 1.0,  # also helps with stability
            'agents': 32,                     # 32 agents is mostly for performance reasons
            'n_steps': 512,                   # 1024, works better but 512 is better on memory, and 30-seconds seems right.
        })

        # remove any obsolete args
        del RE1["time_aware"]
        del RE1["tvf_exp_gamma"]
        del RE1["tvf_mode"]
        del RE1["tvf_exp_mode"]
        del RE1["tvf_n_step"]

        # first lets just get a quick feel for the return estimators with their default settings (plus adaptive)
        for mode in ["fixed", "uniform", "linear", "exponential", "geometric"]:
            for adaptive in [True, False]:
                if mode == "geometric" and adaptive:
                    # this mode does not exist
                    continue
                add_job(
                    experiment_name="RE1",
                    run_name=f"env={env} mode={mode}{' (adaptive)' if adaptive else ''}",
                    tvf_return_mode=mode,
                    tvf_return_adaptive=adaptive,
                    default_params=RE1,
                )

def detailed_value_quality(priority: int):


    AGENTS = 128
    NSTEPS = 512

    DVQ = RP1U_reference_args.copy()
    DVQ.update({
        'use_compression': True,
        'warmup_period': 10 * 1000,

        'agents': AGENTS,
        'n_steps': NSTEPS,
        'replay_size': AGENTS * NSTEPS,
        'distil_batch_size': AGENTS * NSTEPS,
        'policy_mini_batch_size': 2048,  # makes things more stable

        'log_detailed_value_quality': True,
        'learn_second_moment': True,
        'dvq_samples': 64,
        'dvq_freq': 64,
        'dvq_rollout_length': 1024 * 16,

        'priority': 276,
        'hostname': "desktop",
        'workers': 16,  # faster..
        'seed': 1,
        'epochs': 50,
    })

    # V3
    # increased max rollout length, but decreased frequency by 4x.
    # blosc+zlib compression
    # second moment :)
    for env in ["CrazyClimber", "Alien", "Breakout"]:
        # run next one on my machine... in fact, move them all to my machine...
        add_job(
            experiment_name="DVQ1",
            env_name=env,
            run_name=f"env={env}",
            default_params=DVQ,
        )

def second_moment(priority=0):
    """
    Tests to make sure we can learn second moment for each horizon.
    Also want to know if we can learn this by directly squaring the return.
    """

    SML = RP1U_reference_args.copy()
    SML.update(
        {
            # this should be more stable
            'agents': 64,
            'n_steps': 512,
            'replay_size': 64 * 512,
            'distil_batch_size': 64 * 512,
            'policy_mini_batch_size': 2048,
            'epochs': 20,
            'seed': 1,
            'learn_second_moment': True,
            'hostname': 'desktop',
            'return_estimator_mode': 'default',
            "tvf_return_mode": "exponential", # default
            "tvf_return_n_step": 80, # default
        }
    )

    for env in ["Alien", "CrazyClimber", "Breakout"]:
        add_job(
            experiment_name="SML5",
            run_name=f"env={env} (ref)",
            env_name=env,
            learn_second_moment=False,
            default_params=SML,
            priority=-100,
            seed=2,
        )
        add_job(
            experiment_name="SML5",
            run_name=f"env={env} sml exp=512",
            env_name=env,
            tvf_return_mode="exponential",
            tvf_return_n_step=512,
            sqr_return_mode="exponential",
            sqr_return_n_step=512,
            priority=10 if env == "Alien" else -100,
            default_params=SML,
        )
        add_job(
            experiment_name="SML5",
            run_name=f"env={env} sml exp=256_128",
            env_name=env,
            tvf_return_mode="exponential",
            tvf_return_n_step=256,
            sqr_return_mode="exponential",
            sqr_return_n_step=128, # try difference...
            priority=10 if env == "Alien" else -100,
            default_params=SML,
        )
        add_job(
            experiment_name="SML5",
            run_name=f"env={env} sml n_step=512",
            env_name=env,
            tvf_return_mode="fixed",
            tvf_return_n_step=512,
            sqr_return_mode="fixed",
            sqr_return_n_step=512,
            priority=10 if env == "Alien" else -100,
            default_params=SML,
        )
        for n_step in [16, 32, 80]:
            add_job(
                experiment_name="SML5",
                run_name=f"env={env} sml sqr_exp={n_step}",
                env_name=env,
                sqr_return_mode="exponential",
                sqr_return_n_step=n_step,
                priority=10 if env == "Alien" else -100,
                default_params=SML,
            )

    for env in ["Alien"]:
        add_job(
            experiment_name="SML6",
            run_name=f"env={env} (ref)",
            env_name=env,
            learn_second_moment=False,
            default_params=SML,
            priority=0,
            seed=1,
        )
        add_job(
            experiment_name="SML6",
            run_name=f"env={env} (ref_64)", # see if the improvement is simply due to the increased effective samples.
            env_name=env,
            learn_second_moment=False,
            tvf_return_samples=64,
            default_params=SML,
            priority=0,
            seed=1,
        )
        add_job(
            experiment_name="SML6",
            run_name=f"env={env} joint",
            env_name=env,
            learn_second_moment=True,
            sqr_return_mode='joint',
            default_params=SML,
            priority=0,
            seed=1,
        )
        add_job(
            experiment_name="SML6",
            run_name=f"env={env} matched",
            env_name=env,
            learn_second_moment=True,
            sqr_return_mode="exponential",
            sqr_return_n_step=80,
            default_params=SML,
            priority=0,
            seed=1,
        )
        add_job(
            experiment_name="SML6",
            run_name=f"env={env} exp_20",
            env_name=env,
            learn_second_moment=True,
            sqr_return_mode="exponential",
            sqr_return_n_step=20,
            default_params=SML,
            priority=0,
            seed=1,
        )


def adaptive(priority=0):
    """
    See if we get better results using an adaptive approach to return estimation.
    """

    AVE = RP1U_reference_args.copy()
    AVE.update(
        {
            # this should be more stable
            'agents': 128,
            'n_steps': 512,
            'replay_size': 64 * 512,
            'distil_batch_size': 64 * 512,
            'policy_mini_batch_size': 2048,
            'epochs': 20,
            'seed': 1,
            'learn_second_moment': False, # might try this later
            'hostname': '',
            'return_estimator_mode': 'default',
            "tvf_return_mode": "exponential", # default
            "tvf_return_n_step": 80, # default
            "max_micro_batch_size": 256, # needed now for some reason... maybe it's ABS?
            "use_compression": True,
            "abs_mode": "shadow", # will be useful later, just to see if we can control the variancew.
        }
    )

    for env in ["Alien", "Breakout", "CrazyClimber"]:
        for tvf_return_mode in ['exponential', 'exponential_cap', 'adaptive']:
            add_job(
                experiment_name="AVE1",
                run_name=f"env={env} mode={tvf_return_mode}",
                tvf_return_mode=tvf_return_mode,
                env_name=env,
                default_params=AVE,
                priority=0,
                seed=1,
            )

    for env in ["CrazyClimber"]:
        for seed in (1, 2, 3):
            for tvf_return_mode in ['exponential', 'exponential_cap', 'adaptive', 'adaptive_cap']:
                add_job(
                    experiment_name="AVE2",
                    run_name=f"env={env} mode={tvf_return_mode} ({seed})",
                    tvf_return_mode=tvf_return_mode,
                    epochs=30,
                    seed=seed,
                    learn_second_moment=True,  # might try this later
                    env_name=env,
                    default_params=AVE,
                    priority=30 if seed == 1 else -30,
                )

    # see if we can tune adaptive a bit more
    for env in ["CrazyClimber", "Alien"]:
        for seed in (1, 2):
            for n_step in [8, 16, 32, 64, 128]:
                for tvf_return_mode in ['adaptive', "exponential"]:
                    add_job(
                        experiment_name="AVE3",
                        run_name=f"env={env} mode={tvf_return_mode} n_step={n_step} ({seed})",
                        tvf_return_mode=tvf_return_mode,
                        tvf_return_n_step=n_step,
                        epochs=30,
                        seed=seed,
                        learn_second_moment=True,
                        env_name=env,
                        default_params=AVE,
                        priority=30 if seed == 1 else -30,
                    )

    # see if saturated works (and also random sampling)
    for env in ["CrazyClimber"]:
        for seed in (1, 2):
            for samples in [4, 8, 16, 32, 64, 128]:
                for distribution in ['fixed_geometric', 'geometric', 'saturated_fixed_geometric', 'saturated_geometric']:
                    add_job(
                        experiment_name="AVE4",
                        run_name=f"env={env} distribution={distribution} samples={samples} ({seed})",
                        tvf_horizon_samples=samples,
                        tvf_horizon_distribution=distribution,
                        epochs=30,
                        seed=seed,
                        env_name=env,
                        default_params=AVE,
                        priority=30 if seed == 1 else -30,
                    )

    # quick check of log interpolation (doesn't really belong here...)
    # see if saturated works (and also random sampling)
    for env in ["CrazyClimber", "Alien"]:
        for seed in (1,):
            for samples in [4, 64]:
                for log_interpolation in [True, False]:
                    add_job(
                        experiment_name="AVE_LO",
                        run_name=f"env={env} samples={samples} log_interpolation={log_interpolation} ({seed})",
                        tvf_horizon_samples=samples,
                        tvf_horizon_distribution="saturated_geometric",
                        epochs=20,
                        seed=seed,
                        env_name=env,
                        default_params=AVE,
                        tvf_return_use_log_interpolation=log_interpolation,
                        priority=30 if seed == 1 else -30,
                    )


def adaptive_gae(priority=0):

    GAE = RP1U_reference_args.copy()
    GAE.update(
        {
            # this should be more stable
            'agents': 128,
            'n_steps': 512,
            'replay_size': 64 * 512,
            'distil_batch_size': 64 * 512,
            'policy_mini_batch_size': 2048,
            'epochs': 20,
            'seed': 1,
            'learn_second_moment': False,  # might try this later
            'hostname': 'ML',
            'return_estimator_mode': 'default',
            "tvf_return_mode": "exponential",  # default
            "tvf_return_n_step": 80,  # default
            "max_micro_batch_size": 256,  # needed now for some reason... maybe it's ABS?
            "use_compression": True,
            "abs_mode": "shadow",  # will be useful later, just to see if we can control the variancew.
        }
    )

    # first look into adaptive GAE, want to get an handle on metrics that indicate what GAE lambda should be (and see if it makes a difference)
    # asterix should need short horizon, tennis a long one.
    for env in ["CrazyClimber", "Alien", "Asterix", "Tennis"]:
        for seed in (1,):
            for inv_lamb in [1.125, 2.5, 5, 10, 20, 40]:
                add_job(
                    experiment_name="AGAE_1",
                    run_name=f"env={env} gae_horizon={inv_lamb} ({seed})",
                    gae_lambda=1-(1/inv_lamb),
                    epochs=20,
                    seed=seed,
                    env_name=env,
                    default_params=GAE,
                    priority=(30 if seed == 1 else -30)+(30 if env == "CrazyClimber" else -30),
                )

    # quick look to see early variance for each game
    for env in ATARI_57:
        add_job(
            experiment_name="AGAE_NOISE",
            run_name=f"env={env}",
            epochs=5,
            seed=1,
            env_name=env,
            default_params=GAE,
            priority=200,
        )

    # investigation into entropy scaling
    for env in ["CrazyClimber"]:
        for seed in (1,):
            for inv_lamb in [2.5, 5, 10, 20, 40, 80]:
                add_job(
                    experiment_name="AGAE_2",
                    run_name=f"env={env} gae_horizon={inv_lamb} ({seed})",
                    gae_lambda=1 - (1 / inv_lamb),
                    epochs=20,
                    seed=seed,
                    entropy_scaling=True,
                    env_name=env,
                    default_params=GAE,
                    priority=250,
                )


def noise(priority: int = 0):

    # note: we drop montezuma, and just assume it's zero
    ATARI_5 = ['BankHeist', 'BattleZone', 'NameThisGame', 'UpNDown']

    N_STEPS = 512  # helps with stability
    AGENTS = 128
    ROLLOUT_SIZE = N_STEPS * AGENTS

    UPGRADED_ARGS = {
        'n_steps': 512,
        'agents': 128,
        'policy_mini_batch_size': 2048,  # helps with stability
        'entropy_scaling': True,  # handles changes in stocasticity better.
        'tvf_value_distribution': 'saturated_geometric',
        'tvf_horizon_distribution': 'saturated_geometric',  # pay more attention to short horizons
        'tvf_horizon_samples': 64,  # more samples gives better ev
        'tvf_return_n_step': 20,  # this is much lower than I expected

        'anneal_target_epoch': 50,

        # new replay setting for larger buffer...
        'distil_epochs': 1,
        'distil_period': 1,
        'replay_size': 1 * ROLLOUT_SIZE,
        'distil_batch_size': 1 * ROLLOUT_SIZE,
        'replay_mode': "uniform",

    }

    # just want to see how well new TVF handles noise compared to DNA
    for env in ATARI_5:

        COMMON_ARGS = {
            'env_name': env,
            'seed': 1,
            'hostname': "",
            'epochs': 10,
            'abs_mode': 'shadow',  # just to monitor noise levels for value / policy
        }

        COMMON_ARGS.update(HARD_MODE)
        COMMON_ARGS.update(UPGRADED_ARGS)
        del COMMON_ARGS['repeat_action_probability']

        for repeat_probability in [0, 0.25, 0.5, 0.75]:
            add_job(
                f"NOISE_1",
                run_name=f"game={env} dna rap={repeat_probability} (1)",
                default_params=DNA_reference_args,
                priority=priority,
                repeat_action_probability=repeat_probability,
                **COMMON_ARGS,
            )

            add_job(
                f"NOISE_1",
                run_name=f"game={env} tvf rap={repeat_probability} (1)",
                default_params=RP1U_reference_args,
                priority=priority,
                repeat_action_probability=repeat_probability,
                **COMMON_ARGS,
            )

        for per_step_termination_probability in [0.002]:
            # tuning
            COMMON_ARGS['tvf_max_horizon'] = round(3/per_step_termination_probability)
            COMMON_ARGS['tvf_gamma'] = 1 - per_step_termination_probability
            COMMON_ARGS['gamma'] = 1 - per_step_termination_probability
            COMMON_ARGS['abs_mode'] = "shadow"
            COMMON_ARGS['epochs'] = 10 # 10 is enough for the short games, but with 0.001 we might need 20...
            add_job(
                f"NOISE_2",
                run_name=f"game={env} dna pstp={per_step_termination_probability} (1)",
                default_params=DNA_reference_args,
                priority=priority,
                per_step_termination_probability=per_step_termination_probability/4,
                **COMMON_ARGS,
            )
            add_job(
                f"NOISE_2",
                run_name=f"game={env} tvf pstp={per_step_termination_probability} (1)",
                default_params=RP1U_reference_args,
                priority=priority,
                per_step_termination_probability=per_step_termination_probability/4,
                **COMMON_ARGS,
            )

            add_job(
                f"NOISE_2",
                run_name=f"game={env} dna tl={per_step_termination_probability} (1)",
                default_params=DNA_reference_args,
                priority=priority,
                timeout=round(4/per_step_termination_probability),
                **COMMON_ARGS,
            )
            add_job(
                f"NOISE_2",
                run_name=f"game={env} tvf tl={per_step_termination_probability} (1)",
                default_params=RP1U_reference_args,
                priority=priority,
                timeout=round(4/per_step_termination_probability),
                **COMMON_ARGS,
            )

    # check if we can improve ev/ score on battlesize with repeat horizon of 4.
    env = "BattleZone"

    COMMON_ARGS = RP1U_reference_args.copy()

    COMMON_ARGS.update({
        'env_name': env,
        'seed': 1,
        'hostname': "",
        'epochs': 10,
        'abs_mode': 'shadow',  # just to monitor noise levels for value / policy
    })

    COMMON_ARGS.update(HARD_MODE)
    COMMON_ARGS.update(UPGRADED_ARGS)
    COMMON_ARGS['repeat_action_probability'] = 0.75
    COMMON_ARGS['n_steps'] = 128
    COMMON_ARGS['replay_size'] = 128*128
    COMMON_ARGS['distil_batch_size'] = 128*128
    COMMON_ARGS['tvf_value_distribution'] = 'saturated_fixed_geometric'
    COMMON_ARGS['tvf_horizon_distribution'] = 'saturated_fixed_geometric'

    for samples in [32, 64, 128, 256]:
        add_job(
            f"NOISE_3",
            run_name=f"game={env} tvf samples={samples} (1)",

            # see if this helps with the noise
            gamma=0.999,
            tvf_gamma=0.999,
            tvf_max_horizon=3000,

            tvf_horizon_samples=samples,
            tvf_value_samples=samples,  # the idea is we use the sample horizons we learn...

            default_params=COMMON_ARGS,
            priority=-20,
        )

def breakout(priority: int = 0):

    N_STEPS = 512  # helps with stability
    AGENTS = 128
    ROLLOUT_SIZE = N_STEPS * AGENTS

    UPGRADED_ARGS = {
        'n_steps': N_STEPS,
        'agents': AGENTS,
        'policy_mini_batch_size': 2048,  # helps with stability
        'entropy_scaling': True,  # handles changes in stocasticity better.
        'tvf_value_distribution': 'saturated_geometric',
        'tvf_horizon_distribution': 'saturated_geometric',  # pay more attention to short horizons
        'tvf_horizon_samples': 64,  # more samples gives better ev
        'tvf_return_n_step': 20,  # this is much lower than I expected

        'anneal_target_epoch': 50,

        # new replay setting for larger buffer...
        'distil_epochs': 1,
        'distil_period': 1,
        'replay_size': 1 * ROLLOUT_SIZE,
        'distil_batch_size': 1 * ROLLOUT_SIZE,
        'replay_mode': "uniform",

    }

    seed = 1

    COMMON_ARGS = {
        'env_name': 'Breakout',
        'seed': seed,
        'hostname': "",
        'epochs': 30,
        'priority': priority,
    }

    PPO_ARGS = COMMON_ARGS
    PPO_ARGS.update(PPO_reference_args)
    PPO_ARGS.update(HARD_MODE)
    PPO_ARGS.update(UPGRADED_ARGS)

    DNA_ARGS = COMMON_ARGS
    DNA_ARGS.update(DNA_reference_args)
    DNA_ARGS.update(HARD_MODE)
    DNA_ARGS.update(UPGRADED_ARGS)

    TVF_ARGS = COMMON_ARGS.copy()
    TVF_ARGS.update(RP1U_reference_args)
    TVF_ARGS.update(HARD_MODE)
    TVF_ARGS.update(UPGRADED_ARGS)

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf default ({seed})",
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"ppo default ({seed})",
        default_params=PPO_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"dna default ({seed})",
        default_params=DNA_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf no_replay ({seed})",
        default_params=TVF_ARGS,
        replay_size=0,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf mixed_replay ({seed})",
        default_params=TVF_ARGS,
        replay_mixing=True,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf simple_distil ({seed})",
        tvf_force_ext_value_distil=True,
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf exp=80 ({seed})",
        tvf_return_n_step=80,
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf exp=40 ({seed})",
        tvf_return_n_step=40,
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf no_entropy_scaling ({seed})",
        entropy_scaling=False,
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT",
        run_name=f"tvf n_step=128 ({seed})",
        n_steps=128,
        replay_size=128*128,
        distil_batch_size=128*128,
        default_params=TVF_ARGS,
    )

    for gvm in [0, 0.5, 0.9]:
        add_job(
            experiment_name="BREAKOUT_2",
            run_name=f"tvf gvm={gvm} ({seed})",
            gae_value_multiplier=gvm,
            default_params=TVF_ARGS,
        )

    for distribution in ['saturated_geometric', 'geometric', 'fixed_geometric', 'saturated_fixed_geometric']:
        add_job(
            experiment_name="BREAKOUT",
            run_name=f"tvf distribution={distribution} ({seed})",
            tvf_value_distribution=distribution,
            tvf_horizon_distribution=distribution,
            default_params=TVF_ARGS,
        )

    for samples in [16, 64, 128, 256, 512]:
        add_job(
            experiment_name="BREAKOUT",
            run_name=f"tvf samples={samples} ({seed})",
            tvf_horizon_samples=samples,
            # value samples should be fixed to 128...
            default_params=TVF_ARGS,
        )

    add_job(
            experiment_name="BREAKOUT",
            run_name=f"tvf matched_128 ({seed})",
            tvf_horizon_samples=128,
            tvf_value_samples=128, # the idea is we use the sample horizons we learn...
            tvf_value_distribution='saturated_fixed_geometric',
            tvf_horizon_distribution='saturated_fixed_geometric',
            default_params=TVF_ARGS,
        )

    # quick check kof new code
    add_job(
        experiment_name="CHECK",
        run_name=f"tvf default ({seed})",
        resolution="nature",
        max_repeated_actions=0,
        priority=200,
        hostname="ML",
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="CHECK",
        run_name=f"tvf mra ({seed})",
        resolution="nature",
        max_repeated_actions=100,
        priority=200,
        hostname="ML",
        default_params=TVF_ARGS,
    )

    #for rap in [0, 0.1, 0.5, 1.0]:
    for rap in [1.0]: # others can copy and paste from 30M
        add_job(
            experiment_name="CHECK",
            run_name=f"tvf rap={rap} ({seed})",
            resolution="half",
            repeated_action_penalty=rap,
            epochs=30,
            priority=200,
            hostname="",
            default_params=TVF_ARGS,
        )

    add_job(
        experiment_name="CHECK",
        run_name=f"tvf half ({seed})",
        resolution="half",
        priority=200,
        hostname="ML",
        default_params=TVF_ARGS,
    )


def stable(priority:int = 0):
    # see if we can find stable settings for breakout, crazyclimber, and alien

    AGENTS = 128
    N_STEPS = 128

    UPGRADED_ARGS = {
        'n_steps': N_STEPS,
        'agents': AGENTS,
        'policy_mini_batch_size': 2048,  # helps with stability
        'entropy_scaling': True,  # handles changes in stocasticity better.
        'tvf_value_distribution': 'saturated_fixed_geometric',
        'tvf_horizon_distribution': 'saturated_fixed_geometric',  # pay more attention to short horizons
        'tvf_horizon_samples': 256,  # more samples gives better ev
        'tvf_value_samples': 256,  # best to match these when using fixed mode.
        'tvf_return_n_step': 20,  # this is much lower than I expected
        'resolution': "half",
        'repeated_action_penalty': 0.5,

        'anneal_target_epoch': 50,

        # new replay setting for larger buffer...
        'distil_epochs': 1,
        'distil_period': 1,
        'replay_size': 1 * ROLLOUT_SIZE,
        'distil_batch_size': 1 * ROLLOUT_SIZE,
        'replay_mode': "uniform",

    }

    seed = 1

    COMMON_ARGS = {
        'env_name': 'Breakout',
        'seed': seed,
        'hostname': "",
        'epochs': 20, # stub 30
        'priority': priority,
    }

    TVF_ARGS = COMMON_ARGS.copy()
    TVF_ARGS.update(RP1U_reference_args)
    TVF_ARGS.update(HARD_MODE)
    TVF_ARGS.update(UPGRADED_ARGS)

    for env in ['Breakout', 'Alien', 'CrazyClimber']:
        add_job(
            experiment_name="STABLE",
            run_name=f"game={env} tvf new ({seed})",
            env_name=env,
            default_params=TVF_ARGS,
        )

        if env == 'CrazyClimber':
            # see if we can solve the repeated action problem... usually happens with 10M

            CC_ARGS = {
                'repeated_action_penalty': 0,  # < so that I can catch it happening...
                'epochs': 10,
                'env_name': env,
            }
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf default ({seed})",
                **CC_ARGS,
                default_params=TVF_ARGS,
            )
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf entropy=high ({seed})",
                entropy_bonus=3e-3,
                **CC_ARGS,
                default_params=TVF_ARGS,
            )
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf entropy=low ({seed})", # see if we can make things worse...
                entropy_bonus=3e-4,
                **CC_ARGS,
                default_params=TVF_ARGS,
            )
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf reduced_actions ({seed})",  # see if we can make things worse...
                full_action_space=False,
                **CC_ARGS,
                default_params=TVF_ARGS,
            )
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf no_entropy_scaling ({seed})",  # see if we can make things worse...
                entropy_scaling=False,
                **CC_ARGS,
                default_params=TVF_ARGS,
            )
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf no_repeat_prob ({seed})",  # see if we can make things worse...
                repeat_action_probability=0.0,
                **CC_ARGS,
                default_params=TVF_ARGS,
            )

        if env == 'Breakout':
            # just a bit of a search on breakout first
            # n_step=1 is just to make sure this actually changes something
            for n_step in [1, 20, 40, 80]:
                if n_step != 20:
                    add_job(
                        experiment_name="STABLE",
                        run_name=f"game={env} tvf tvf_return_n_step={n_step} ({seed})",
                        env_name=env,
                        tvf_return_n_step=n_step,
                        default_params=TVF_ARGS,
                    )
                add_job(
                    experiment_name="BREAKOUT_99",
                    run_name=f"game={env} tvf tvf_return_n_step={n_step} ({seed})",
                    gamma=0.99,
                    tvf_gamma=0.99,
                    tvf_max_horizon=300,
                    env_name=env,
                    tvf_return_n_step=n_step,
                    default_params=TVF_ARGS,
                )
            add_job(
                experiment_name="STABLE",
                run_name=f"game={env} tvf best ({seed})",
                env_name=env,
                tvf_return_n_step=40,
                tvf_horizon_samples=256,
                tvf_value_samples=256,  # the idea is we use the sample horizons we learn...
                tvf_value_distribution='saturated_fixed_geometric',
                tvf_horizon_distribution='saturated_fixed_geometric',
                replay_size=0,
                default_params=TVF_ARGS,
            )
    env='Breakout'
    add_job(
        experiment_name="BREAKOUT_EASY", # these settings were known to work well before
        run_name=f"game={env} tvf 9999 ({seed})",
        gamma=0.9999,
        tvf_gamma=0.9999,
        tvf_max_horizon=30000,

        # easy mode
        full_action_space=False,
        repeat_action_probability=0.0,
        priority=300,

        env_name=env,
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT_EASY",  # these settings were known to work well before
        run_name=f"game={env} tvf default ({seed})",

        # easy mode
        full_action_space=False,
        repeat_action_probability=0.0,
        priority=300,

        env_name=env,
        default_params=TVF_ARGS,
    )

    add_job(
        experiment_name="BREAKOUT_EASY",  # these settings were known to work well before
        run_name=f"game={env} tvf default rap ({seed})",

        # easy mode
        full_action_space=False,
        repeat_action_probability=0.25,
        priority=300,

        env_name=env,
        default_params=TVF_ARGS,
    )


def dna(priority:int=0):
    AGENTS = 128
    N_STEPS = 128

    UPGRADED_ARGS = {
        'n_steps': N_STEPS,
        'agents': AGENTS,
        'policy_mini_batch_size': 2048,  # helps with stability
        'entropy_scaling': True,  # handles changes in stocasticity better.
        'tvf_value_distribution': 'saturated_fixed_geometric',
        'tvf_horizon_distribution': 'saturated_fixed_geometric',  # pay more attention to short horizons
        'tvf_horizon_samples': 256,  # more samples gives better ev
        'tvf_value_samples': 256,  # best to match these when using fixed mode.
        'tvf_return_n_step': 20,  # this is much lower than I expected
        'resolution': "half",
        'repeated_action_penalty': 0.5,

        'anneal_target_epoch': 50,

    }

    seed = 1

    COMMON_ARGS = {
        'env_name': 'Breakout',
        'seed': seed,
        'hostname': "",
        'epochs': 20,
        'priority': priority,
    }

    DNA_ARGS = COMMON_ARGS.copy()
    DNA_ARGS.update(DNA_reference_args)
    DNA_ARGS.update(HARD_MODE)
    DNA_ARGS.update(UPGRADED_ARGS)

    for env in ['Qbert']:
        for gae in [10, 20, 40, 80]:
            for value in [10, 20, 40, 80]:
                add_job(
                    experiment_name="TDLAMBDA",
                    run_name=f"game={env} dna gae={gae} value={value}  ({seed})",
                    gae_lambda=1-(1 / gae),
                    td_lambda=1-(1 / value),
                    env_name=env,
                    default_params=DNA_ARGS,
                )



def setup(priority_modifier=0):
    # Initial experiments to make sure code it working, and find reasonable range for the hyperparameters.
    # detailed_value_quality(priority=50)
    # second_moment(0)
    # adaptive(20)
    # adaptive_gae()
    noise(-10)
    stable(200)
    breakout(50)
    dna(-10)

