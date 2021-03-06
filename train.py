"""General-purpose training script for image-to-image translation.

* To run training:
$ python -m avsg_train
 --dataset_mode avsg  --model avsg --data_path_train datasets/avsg_data/sample  --data_path_val datasets/avsg_data/sample

* Replace --data_path_train and for --data_path_val larger datasets

* To run only on CPU add: --gpu_ids -1

* To use wandb logging,
run $ wandb login

* Name the experiment with --name



This script works for various models (with option '--model') and
different datasets (with option '--dataset_mode').
You need to specify the dataset ('--data_path_train'), experiment name ('--name'), and model ('--model').
It first creates model, dataset, and visualizer given the option.
It then does standard network training. During the training, it also visualize/save the images, print/save the loss plot, and save models.
The script supports continue/resume training. Use '--continue_train' to resume your previous training.


Note: if you get CUDA Unknown error, try $ apt-get install nvidia-modprobe
"""
import time

from data.data_func import create_dataloader, get_next_batch_cyclic
from models import create_model
from options.train_options import TrainOptions
from util.visualizer import Visualizer

# -------------------------------------------------------------------
if __name__ == '__main__':
    run_start_time = time.time()
    opt = TrainOptions().parse()  # get training options
    train_data_gen = create_dataloader(opt, data_path=opt.data_path_train)
    val_data_gen = create_dataloader(opt, data_path=opt.data_path_val)

    model = create_model(opt)  # create a model given opt.model and other options
    opt.device = model.device
    model.setup(opt)  # regular setup: load and print networks; create schedulers
    model.train()
    visualizer = Visualizer(opt)  # create a visualizer that display/save images and plots

    start_time = time.time()
    for i in range(opt.n_iter):
        iter_start_time = time.time()  # timer for entire epoch
        conditioning = None
        real_actors = None
        for i_step in range(opt.n_steps_D):
            scenes_batch = get_next_batch_cyclic(train_data_gen)
            conditioning, real_actors = scenes_batch['conditioning'], scenes_batch['agents_feat_vecs']
            model.optimize_discriminator(opt, real_actors, conditioning)

        for i_step in range(opt.n_steps_G):
            scenes_batch = get_next_batch_cyclic(train_data_gen)
            conditioning, real_actors = scenes_batch['conditioning'], scenes_batch['agents_feat_vecs']
            model.optimize_generator(opt, real_actors, conditioning)

        # update learning rates (must be after first model update step):
        model.update_learning_rate()

        # print training losses and save logging information to the log file and wandb charts:
        if i % opt.print_freq == 0:
            visualizer.print_current_metrics(model, i, opt, conditioning, val_data_gen, run_start_time)
        # Display visualizations:
        if i > 0 and i % opt.display_freq == 0:
            visualizer.display_current_results(model, i, opt, conditioning, real_actors, val_data_gen)

        # cache our latest model every <save_latest_freq> iterations:
        if i > 0 and i % opt.save_latest_freq == 0:
            print(f'saving the latest model (iteration {i + 1})')
            save_suffix = f'iter_{i + 1}' if opt.save_by_iter else 'latest'
            model.save_networks(save_suffix)

        print(f'End of iteration {i + 1}/{opt.n_iter}'
              f', iter run time {(time.time() - iter_start_time):.2f} sec')
    visualizer.wandb_run.finish()
