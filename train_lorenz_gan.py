from lorenz_gan.lorenz import run_lorenz96_truth, process_lorenz_data, save_lorenz_output
from lorenz_gan.gan import generator_conv, generator_dense, discriminator_conv, discriminator_dense
from lorenz_gan.gan import train_gan, initialize_gan, normalize_data
from keras.optimizers import Adam
import numpy as np
import pandas as pd
import yaml
import argparse
from os.path import exists, join
from os import mkdir


def main():
    """
    This script runs the Lorenz '96 model and then trains a generative adversarial network
    to parameterize the unresolved Y values. The script requires a config file as input.
    The config file is formatted in the yaml format with the following information included.

    lorenz: # The Lorenz model subsection
        K: 8 # number of X variables
        J: 32 # number of Y variables per X variable
        h: 1 # coupling constant
        b: 10 # spatial-scale ratio
        c: 10 # time scale ratio
        F: 30 # forcing term
        time_step: 0.001 # time step of Lorenz truth model in MTU
        num_steps: 1000000 # number of integration time steps
        skip: 5 # number of steps to skip when saving out the model
        burn_in: 2000 # number of steps to remove from the beginning of the integration
    gan: # The GAN subsection
        structure: conv # type of GAN neural network, options are conv or dense
        t_skip: 10 # number of time steps to skip when saving data for training
        x_skip: 1 # number of X variables to skip
        output: sample # Train the neural network to output a "sample" of Ys or the "mean" of the Ys
        generator:
            num_cond_inputs: 3 # number of conditional X values
            num_random_inputs: 13 # number of random values
            num_outputs: 32 # number of output variables (should match J)
            activation: relu # activation function
            min_conv_filters: 32 # number of convolution filters in the last layer of the generator
            min_data_width: 4 # width of the data array after the dense layer in the generator
            filter_width: 4 # Size of the convolution filters
        discriminator:
            num_cond_inputs: 3 # number of conditional X values
            num_sample_inputs: 32 # number of Y values
            activation: relu # Activation function
            min_conv_filters: 32 # number of convolution filters in the first layer of the discriminator
            min_data_width: 4 # width of the data array before the dense layer in the discriminator
            filter_width: 4 # width of the convolution filters
        gan_path: ./exp # path where GAN files are saved
        batch_size: 64 # Number of examples per training batch
        gan_index: 0 # GAN configuration number
        loss: binary_crossentropy # Loss function for the GAN
        num_epochs: [1, 5, 10] # Epochs after which the GAN model is saved
        metrics: ["accuracy"] # Metrics to calculate along with the loss
    output_nc_file: ./exp/lorenz_output.nc # Where Lorenz 96 data is output
    output_csv_file: ./exp/lorenz_combined_output.csv # Where flat file formatted data is saved

    Returns:

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("config", default="lorenz.yaml", help="Config yaml file")
    parser.add_argument("-r", "--reload", action="store_true", default=False, help="Reload netCDF and csv files")
    args = parser.parse_args()
    config_file = args.config
    with open(config_file) as config_obj:
        config = yaml.load(config_obj)
    if not exists(config["gan"]["gan_path"]):
        mkdir(config["gan"]["gan_path"])
    if args.reload:
        print("Reloading csv data")
        combined_data = pd.read_csv(config["output_csv_file"])
    else:
        X_out, Y_out, times, steps = generate_lorenz_data(config["lorenz"])
        combined_data = process_lorenz_data(X_out, Y_out, times, steps,
                                            config["gan"]["generator"]["num_cond_inputs"],
                                            config["lorenz"]["J"], config["gan"]["x_skip"],
                                            config["gan"]["t_skip"])
        save_lorenz_output(X_out, Y_out, times, steps, config["lorenz"], config["output_nc_file"])
        combined_data.to_csv(config["output_csv_file"], index=False)
        print(combined_data)
    train_lorenz_gan(config, combined_data)
    return


def generate_lorenz_data(config):
    """
    Run the Lorenz '96 truth model

    Args:
        config:

    Returns:

    """
    X = np.zeros(config["K"], dtype=np.float32)
    Y = np.zeros(config["J"] * config["K"], dtype=np.float32)
    X[0] = 1
    Y[0] = 1
    skip = config["skip"]
    X_out, Y_out, times, steps = run_lorenz96_truth(X, Y, config["h"], config["F"], config["b"],
                                                    config["c"], config["time_step"], config["num_steps"])
    return (X_out[config['burn_in']::skip], Y_out[config["burn_in"]::skip],
            times[config["burn_in"]::skip], steps[config["burn_in"]::skip])


def train_lorenz_gan(config, combined_data):
    """
    Train GAN on Lorenz data

    Args:
        config:
        combined_data:

    Returns:

    """
    x_time_lags = np.arange(config["gan"]["generator"]["num_cond_inputs"])
    x_cols = []
    for t in x_time_lags:
        if t == 0:
            x_cols.append("X_t")
        else:
            x_cols.append("X_t-{0:d}".format(t))
    X_series = np.expand_dims(combined_data[x_cols].values, axis=-1)
    Y_series = np.expand_dims(combined_data[["Y_{0:d}".format(y) for y in range(config["lorenz"]["J"])]].values,
                              axis=-1)
    X_norm, X_scaling_values = normalize_data(X_series)
    if config["gan"]["output"].lower() == "mean":
        Y_norm, Y_scaling_values = normalize_data(np.expand_dims(Y_series.mean(axis=1), axis=-1))
    else:
        Y_norm, Y_scaling_values = normalize_data(Y_series)
    X_scaling_values.to_csv(join(config["gan"]["gan_path"],
                                 "gan_X_scaling_values_{0:04d}.csv".format(config["gan"]["gan_index"])),
                            index_label="Channel")
    Y_scaling_values.to_csv(join(config["gan"]["gan_path"],
                                 "gan_Y_scaling_values_{0:04d}.csv".format(config["gan"]["gan_index"])),
                            index_label="Channel")
    trim = X_norm.shape[0] % config["gan"]["batch_size"]
    if config["gan"]["structure"] == "dense":
        gen_model = generator_dense(**config["gan"]["generator"])
        disc_model = discriminator_dense(**config["gan"]["discriminator"])
    else:
        gen_model = generator_conv(**config["gan"]["generator"])
        disc_model = discriminator_conv(**config["gan"]["discriminator"])
    optimizer = Adam(lr=0.0001, beta_1=0.5)
    loss = config["gan"]["loss"]
    gen_disc = initialize_gan(gen_model, disc_model, loss, optimizer, config["gan"]["metrics"])
    if trim > 0:
        Y_norm = Y_norm[:-trim]
        X_norm = X_norm[:-trim]
    train_gan(Y_norm, X_norm, gen_model, disc_model, gen_disc, config["gan"]["batch_size"],
              config["gan"]["generator"]["num_random_inputs"], config["gan"]["gan_path"],
              config["gan"]["gan_index"], config["gan"]["num_epochs"], config["gan"]["metrics"],
              Y_scaling_values, X_scaling_values)


if __name__ == "__main__":
    main()
