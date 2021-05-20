import numpy as np
import matplotlib.pyplot as plt
import os
import seaborn as sns

import t_VAE
import torch
from pytorch_lightning import Trainer
from pytorch_lightning.loggers import TestTubeLogger

sns.set()


def train(
    data, hyper_params, input_checkpoint_path=None, output_checkpoint_path="ar_vae.ckpt"
):
    # TRAINING FUNCTION
    max_epochs = hyper_params["epochs"]
    latent_dim = hyper_params["latent_dim"]
    hidden_dims = hyper_params["hidden_dims"]
    kld_weight = hyper_params["kld_weight"]

    vae_model = t_VAE.AR_VAE(
        latent_dim=latent_dim,
        X=torch.tensor(data).float(),
        hidden_dims=hidden_dims,
        kld_weight=kld_weight,
    ).float()
    if input_checkpoint_path is not None:
        vae_model = t_VAE.AR_VAE.load_from_checkpoint(
            input_checkpoint_path,
            latent_dim=latent_dim,
            X=torch.tensor(data).float(),
            hidden_dims=hidden_dims,
            kld_weight=kld_weight,
        ).float()

    print("Loss Before Training")
    res = vae_model.forward(torch.tensor(data).float())
    print(vae_model.loss_function(*res, M_N=1))

    # Logger
    tt_logger = TestTubeLogger(
        save_dir=os.getcwd(), name="t_VAE_log", debug=False, create_git_tag=False
    )

    # Trainer
    runner = Trainer(
        max_epochs=max_epochs,
        logger=tt_logger,
        log_every_n_steps=50,
        limit_train_batches=2.0,
        limit_val_batches=3.0,
        num_sanity_val_steps=100,
        checkpoint_callback=False,
    )

    runner.fit(vae_model)

    runner.save_checkpoint(output_checkpoint_path)

    print("Loss After Training")
    res = vae_model.forward(torch.tensor(data).float())
    print(vae_model.loss_function(*res, M_N=1))

    return vae_model, runner


# Function for Fetching the Interpretable Transformation Map
def fetch_ITM(vae_model):
    weights = None
    bias = None
    i = 0
    for p in vae_model.final_layer.parameters():
        if i == 0:
            weights = p
        else:
            bias = p
        i += 1
    return weights[:, :], bias[:]


# Function to encode Interventions
def intervene_raw(
    target_idx, feature_idx, bias, intervention, checkpoint_path, hyper_params, data
):
    lag = hyper_params["lag"]
    latent_dim = hyper_params["latent_dim"]
    hidden_dims = hyper_params["hidden_dims"]
    vae_model_intv = t_VAE.AR_VAE.load_from_checkpoint(
        checkpoint_path,
        lag=lag,
        latent_dim=latent_dim,
        hidden_dims=hidden_dims,
        X=torch.tensor(data).float(),
    ).float()
    w_i, b_i = fetch_ITM(vae_model_intv)
    print(w_i.shape)
    print(w_i[target_idx, :][:, feature_idx])
    if bias:
        intv = intervention((w_i[target_idx, :][:, feature_idx], b_i[target_idx]))
    else:
        intv = intervention(w_i[target_idx, :][:, feature_idx])
    if bias:
        b_i[target_idx] = intv[1]
        for i in range(len(target_idx)):
            for j in range(len(feature_idx)):
                w_i[target_idx[i], feature_idx[j]] = intv[0][i, j]
        print((w_i[target_idx, :][:, feature_idx], b_i[target_idx]))
    else:
        for i in range(len(target_idx)):
            for j in range(len(feature_idx)):
                w_i[target_idx[i], feature_idx[j]] = intv[0][i, j]
        print(w_i[target_idx, :][:, feature_idx])
    return vae_model_intv


# Function to generate example samples
def generate_example_sample(vae_model, targets, adjust, T, B, N, latent_values=None, donor_intervals=None, if_output_std_samples=False):

    latent_dim = vae_model.latent_dim

    if latent_values is not None:
        nz = latent_values
    else:
        nz = (torch.rand(1, B, latent_dim) * 4) - 2

    s = vae_model.decode(nz).detach().numpy()
    s = s.squeeze(axis=0)
    targets_var = rescale(s, targets, adjust, T, B, N, donor_intervals=donor_intervals)

    if if_output_std_samples:
        return targets_var, s
    else:
        return targets_var


# Function to plot data+samples
def plot_data(vae_model, B=6, **kwargs):
    # Need to add functionality for ploting post_intervention_vae_model
    sns.set(font_scale=1.25)

    targets_var = generate_example_sample(
        vae_model, B=B, **kwargs
    )
    
    fig, ax = plt.subplots(nrows=3, ncols=2, figsize=(35, 20))

    ax[0, 0].plot(np.arange(np.shape(targets_var)[0]), targets_var[:, 0, :], alpha=0.5)
    ax[0, 1].plot(np.arange(np.shape(targets_var)[0]), targets_var[:, 1, :], alpha=0.5)
    ax[1, 0].plot(np.arange(np.shape(targets_var)[0]), targets_var[:, 2, :], alpha=0.5)
    ax[1, 1].plot(np.arange(np.shape(targets_var)[0]), targets_var[:, 3, :], alpha=0.5)
    ax[2, 0].plot(np.arange(np.shape(targets_var)[0]), targets_var[:, 4, :], alpha=0.5)
    ax[2, 1].plot(np.arange(np.shape(targets_var)[0]), targets_var[:, 5, :], alpha=0.5)
    plt.close()
    
    return fig


def plot_latent_space(vae_model, data):
    sns.set(font_scale=1.25)

    _, _, mu, log_var = vae_model.forward(torch.tensor(data).float())
    latent_dim = vae_model.latent_dim
    fig, ax = plt.subplots(nrows=latent_dim, ncols=2, figsize=(35, 20))
    for i in range(latent_dim):
        ax[i, 0].hist(mu[0, :, i].detach().numpy())
        ax[i, 1].hist(np.exp(log_var[0, :, i].detach().numpy()))
    
    return fig


def prepare_input(data, targets, adjust: float = 10, outlier_threshold: float = 5, donor_intervals=None):

    data_norm0, std_t, std_d = normalized_data(data, targets, donor_intervals=donor_intervals)

    # remove outlier
    if outlier_threshold is not None:
        if outlier_threshold <= 0:
            raise ValueError("Outlier threshold must be positive.")
        data_norm0[data_norm0 > outlier_threshold] = outlier_threshold
        data_norm0[data_norm0 < -outlier_threshold] = -outlier_threshold

    # convert 3D to 2D
    data_norm = convert_to_2d(data_norm0)

    # append mean and std
    mean_targets = np.mean(data[:, :, :], axis=0)

    processed_data_norm = np.append(
        np.append(
            np.append(data_norm, np.log(std_t) / adjust, axis=1),
            np.log(std_d) / adjust,
            axis=1,
        ),
        np.log(mean_targets) / adjust,
        axis=1,
    )
    return processed_data_norm

def check_donor_intervals(donor_intervals, targets, N):
    if donor_intervals is None:
        pass
    elif isinstance(donor_intervals, list):
        std_d = []
        for interval in donor_intervals:
            interval_start, interval_end = interval

            if interval_start < targets:
                raise ValueError("donor_intervals cannot overlap with targets.")
            if interval_end > N:
                raise ValueError("donor_intervals cannot exceed total numbers of donors plus targets.")
            if interval_start > interval_end:
                raise ValueError("feature_fitting_window intervals are invalid")
    else:
        raise ValueError('Values for donor_intervals must be None or a list.')


def normalized_data(data: np.ndarray, targets, donor_intervals=None):
    N = data.shape[2]  # total number of donors + targets

    check_donor_intervals(donor_intervals, targets, N)

    # Separetly compute standard deviation for largest target and largest donor
    std_t = np.std(data[:, :, targets - 1], axis=0)

    if donor_intervals is None:
        std_d = np.std(data[:, :, N - 1], axis=0)
    else:
        std_d = []
        for interval in donor_intervals:
            interval_start, interval_end = interval
            std_d_i = np.std(data[:, :, interval_end-1], axis=0)
            std_d.append(std_d_i)

    data_norm0t = (
        data[:, :, :targets] - np.mean(data[:, :, :targets], axis=0)
    ) / std_t[None, :, None]

    if donor_intervals is None:
        data_norm0d = (
        data[:, :, targets:] - np.mean(data[:, :, targets:], axis=0)
        ) / std_d[None, :, None]

        data_norm0 = np.append(data_norm0t, data_norm0d, axis=2)
        
        # Convert 1-D arrays of std_d into 2-D array.
        std_d = std_d[:, None]
        
    elif isinstance(donor_intervals, list):
        data_norm0 = data_norm0t
        for interval, std_d_i in zip(donor_intervals, std_d):
            interval_start, interval_end = interval
            data_norm0d_i = (
            data[:, :, interval_start:interval_end] - np.mean(data[:, :, interval_start:interval_end], axis=0)
            ) / std_d_i[None, :, None]

            data_norm0 = np.append(data_norm0, data_norm0d_i, axis=2)
        # Stack 1-D arrays of std_d as columns into a 2-D array.
        std_d = np.column_stack(std_d)

    print(f"Shape: {data_norm0.shape}")
    print(f"\nMean of raw data: {np.mean(data):.3f}")
    print(f"Std. dev of raw data: {np.std(data):.3f}")
    print(f"\nMean of normalized data: {np.mean(data_norm0):.3f}")
    print(f"Std. dev of normalized data: {np.std(data_norm0):.3f}")

    # Convert 1-D arrays of std_d into 2-D array.
    std_t = std_t[:, None]

    return data_norm0, std_t, std_d


def convert_to_2d(data_input):
    B = data_input.shape[1]  # total number of bundles
    T = data_input.shape[0]  # length timeseries
    N = data_input.shape[2]  # total number of donors + targsets

    data_rescale = np.zeros((B, N * T))
    for i in range(0, B):
        for j in range(0, N):
            data_rescale[i, T * j : T * (j + 1)] = data_input[:, i, j]
    return data_rescale


def convert_to_3d(data_input, T, B, N):
    data_input_3d = np.zeros((T, B, N))
    for i in range(0, B):
        for j in range(0, N):
            data_input_3d[:, i, j] = data_input[i, T * j : T * (j + 1)]
    return data_input_3d


def rescale(data, targets, adjust, T, B, N, donor_intervals=None):

    check_donor_intervals(donor_intervals, targets, N)

    ## rescale function currently only works for 1 level hierarchy
    data_3d = convert_to_3d(data, T, B, N)

    if donor_intervals is None:
        n_std = 1
        normalizers = np.exp(data[:, -(N + 1 + n_std) :] * adjust)
        data_rescaled = (
            np.append(
                data_3d[:, :, :targets] * normalizers[:, 0, None],
                data_3d[:, :, targets:] * normalizers[:, 1, None],
                axis=2,
            )
            + normalizers[:, (1 + n_std):]
        )
    else:
        n_std = len(donor_intervals)
        normalizers = np.exp(data[:, -(N + 1 + n_std) :] * adjust)

        data_rescaled_t = data_3d[:, :, :targets] * normalizers[:, 0, None]

        data_rescaled = data_rescaled_t
        for interval, i in zip(donor_intervals, range(n_std)):
            interval_start, interval_end = interval
            data_rescaled_donor_i = data_3d[:, :, interval_start:interval_end] * normalizers[:, 1 + i, None]
            data_rescaled = np.append(data_rescaled,  data_rescaled_donor_i, axis=2)
        
        data_rescaled = data_rescaled + normalizers[:, (1 + n_std):]
    
    neg_adj = np.min(data_rescaled, axis=0)
    neg_adj[neg_adj > 0] = 0
    data_rescaled = data_rescaled - neg_adj

    return data_rescaled


def exclude_small_targets(data: np.ndarray, targets: int, threshold: float = 200):
    median_targets = np.median(data[:, :, :], axis=0)
    # Exclude Bundles with small targets
    data = np.delete(
        data, np.argwhere(median_targets[:, :targets] < threshold)[:, 0], 1
    )
    return data

