"""Model class template

This module provides a template for users to implement custom models.
You can specify '--model template' to use this model.
The class name should be consistent with both the filename and its model option.
The filename should be <model>_dataset.py
The class name should be <Model>Dataset.py
It implements a simple image-to-image translation baseline based on regression loss.
Given input-output pairs (data_A, data_B), it learns a network netG that can minimize the following L1 loss:
    min_<netG> ||netG(data_A) - data_B||_1
You need to implement the following functions:
    <modify_commandline_options>:　Add model-specific options and rewrite default values for existing options.
    <__init__>: Initialize this model class.
    <set_input>: Unpack input data and perform data pre-processing.
    <forward>: Run forward pass. This will be called by both <optimize_parameters> and <test>.
    <optimize_parameters>: Update network weights; it will be called in every training iteration.
"""
import numpy as np
import torch
from .base_model import BaseModel
from . import networks
from collections import OrderedDict
from avsg_visualization_utils import visualize_scene_feat
from avsg_utils import agents_feat_vecs_to_dicts, agents_feat_dicts_to_vecs,pre_process_scene_data
#########################################################################################


class AvsgModel(BaseModel):
    @staticmethod
    def modify_commandline_options(parser, is_train=True):
        """Add new model-specific options and rewrite default values for existing options.

        Parameters:
            parser -- the option parser
            is_train -- if it is training phase or test phase. You can use this flag to add training-specific or test-specific options.

        Returns:
            the modified parser.
        """
        parser.add_argument('--polygon_name_order', type=list,
                            default=['lanes_mid', 'lanes_left', 'lanes_right', 'crosswalks'], help='')
        parser.add_argument('--closed_polygon_types', type=list,
                            default=['crosswalks'], help='')

        parser.add_argument('--agent_feat_vec_coord_labels',
                            default=['centroid_x',  # [0]  Real number
                                     'centroid_y',  # [1]  Real number
                                     'yaw_cos',  # [2]  in range [-1,1],  sin(yaw)^2 + cos(yaw)^2 = 1
                                     'yaw_sin',  # [3]  in range [-1,1],  sin(yaw)^2 + cos(yaw)^2 = 1
                                     'extent_length',  # [4] Real positive
                                     'extent_width',  # [5] Real positive
                                     'speed',  # [6] Real non-negative
                                     'is_CAR',  # [7] 0 or 1
                                     'is_CYCLIST',  # [8] 0 or 1
                                     'is_PEDESTRIAN',  # [9]  0 or 1
                                     ],
                            type=list)

        if is_train:
            parser.set_defaults(gan_mode='lsgan',      # 'the type of GAN objective. [vanilla| lsgan | wgangp]. vanilla GAN loss is the cross-entropy objective used in the original GAN paper.')
                                netD='SceneDiscriminator',
                                netG='SceneGenerator',
                                n_epochs=10000,
                                lr=0.002,
                                lr_policy='step',
                                lr_decay_iters=1000,
                                display_freq=200,
                                update_html_freq=200)

            parser.add_argument('--agents_decoder_model', type=str,
                                default='GRU')  # 'GRU' | 'MLP'

            parser.add_argument('--lambda_L1', type=float, default=100.0, help='weight for L1 loss')
            parser.add_argument('--dim_latent_scene_noise', type=int, default=256, help='Scene latent noise dimension')
            parser.add_argument('--dim_latent_map', type=int, default=256, help='Scene latent noise dimension')
            parser.add_argument('--dim_latent_all_agents', type=int, default=256, help='')
            parser.add_argument('--dim_latent_scene', type=int, default=512, help='')
            parser.add_argument('--dim_agents_decoder_hid', type=int, default=512, help='')
            parser.add_argument('--dim_latent_polygon_elem', type=int, default=64, help='')
            parser.add_argument('--dim_latent_polygon_type', type=int, default=128, help='')
            parser.add_argument('--kernel_size_conv_polygon', type=int, default=5, help='')

            parser.add_argument('--max_points_per_poly', type=int, default=20,
                                help='Maximal number of points per polygon element')

            parser.add_argument('--n_conv_layers_polygon', type=int, default=3, help='')
            parser.add_argument('--n_point_net_layers', type=int, default=3, help='PointNet layers number')
            parser.add_argument('--gru_in_layers', type=int, default=3, help='')
            parser.add_argument('--gru_out_layers', type=int, default=3, help='')
            parser.add_argument('--gru_attn_layers', type=int, default=3, help='')
            parser.add_argument('--n_discr_out_mlp_layers', type=int, default=3, help='')
            parser.add_argument('--n_discr_pointnet_layers', type=int, default=3, help='')
            parser.add_argument('--n_layers_poly_types_aggregator', type=int, default=3, help='')
            parser.add_argument('--n_layers_sets_aggregator', type=int, default=3, help='')
            parser.add_argument('--n_layers_scene_embedder_out', type=int, default=3, help='')

            parser.add_argument('--num_agents', type=int, default=4,   help=' number of agents in a scene')

        return parser

    #########################################################################################

    def __init__(self, opt):
        """Initialize this model class.

        Parameters:
            opt -- training/test options

        A few things can be done here.
        - (required) call the initialization function of BaseModel
        - define loss function, visualization images, model names, and optimizers
        """
        BaseModel.__init__(self, opt, is_image_data=False)  # call the initialization method of BaseModel
        self.polygon_name_order = opt.polygon_name_order
        self.agent_feat_vec_coord_labels = opt.agent_feat_vec_coord_labels

        self.num_agents = opt.num_agents

        # specify the training losses you want to print out.
        # The program will call base_model.get_current_losses to plot the losses to the console and save them to the disk.
        self.loss_names = ['G_GAN', 'G_L1', 'D_real', 'D_fake']

        # specify the models you want to save to the disk.
        # The training/test scripts will call <BaseModel.save_networks> and <BaseModel.load_networks>
        if self.isTrain:
            self.model_names = ['G', 'D']
        else:  # during test time, only load G
            self.model_names = ['G']

        opt.device = self.device

        # define networks
        self.netG = networks.define_G(opt, self.gpu_ids)
        if self.isTrain:
            # define a discriminator; conditional GANs need to take both input and output images;
            # Therefore, #channels for D is input_nc + output_nc
            discriminator_in_nc = opt.dim_latent_scene
            self.netD = networks.define_D(opt, discriminator_in_nc, self.gpu_ids)

        if self.isTrain:
            # define loss functions
            self.criterionGAN = networks.GANLoss(opt.gan_mode).to(self.device)
            self.criterionL1 = torch.nn.L1Loss()
            # initialize optimizers; schedulers will be automatically created by function <BaseModel.setup>.
            self.optimizer_G = torch.optim.Adam(self.netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizer_D = torch.optim.Adam(self.netD.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
            self.optimizers.append(self.optimizer_G)
            self.optimizers.append(self.optimizer_D)
            # Our program will automatically call <model.setup> to define schedulers, load networks, and print networks

    #########################################################################################

    def set_input(self, scene_data):
        """Unpack input data from the dataloader and perform necessary pre-processing steps.

        Parameters:
            input: a dictionary that contains the data itself and its metadata information.
        """
        assert isinstance(scene_data, dict)  # assume batch_size == 1, where the sample is a dict of one scene

        is_valid, real_agents, conditioning = pre_process_scene_data(scene_data, self.num_agents,
                                                                     self.agent_feat_vec_coord_labels,
                                                                     self.polygon_name_order, self.device)
        # if there are too few agents in the scene - skip it
        if not is_valid:
            return False

        self.conditioning = conditioning
        self.real_agents = real_agents

        return True
    #########################################################################################

    def forward(self):
        """Run forward pass. This will be called by both functions <optimize_parameters> and <test>."""
        # generate the output of the generator given the input map
        self.fake_agents = self.netG(self.conditioning)

    #########################################################################################

    def backward_D(self):
        """Calculate GAN loss for the discriminator"""

        # Feed fake (generated) agents to discriminator and calculate its prediction loss
        # we use conditional GANs; we need to feed both input and output to the discriminator
        # stop backprop to the generator by detaching fake_B
        fake_agents_detached = self.fake_agents.detach()
        pred_fake = self.netD(self.conditioning, fake_agents_detached)
        self.loss_D_fake = self.criterionGAN(pred_fake, False)

        # Feed real (loaded from data) agents to discriminator and calculate its prediction loss
        pred_real = self.netD(self.conditioning, self.real_agents)
        self.loss_D_real = self.criterionGAN(pred_real, True)

        # combine loss and calculate gradients
        self.loss_D = (self.loss_D_fake + self.loss_D_real) * 0.5
        self.loss_D.backward()

    #########################################################################################

    def backward_G(self):
        """Calculate GAN and L1 loss for the generator"""
        #  the generator should fool the discriminator
        pred_fake = self.netD(self.conditioning, self.fake_agents)
        self.loss_G_GAN = self.criterionGAN(pred_fake, True)

        # Second, we want G(map) = map, since the generator acts also as an encoder-decoder for the map
        self.loss_G_L1 = self.criterionL1(self.fake_agents, self.real_agents) * self.opt.lambda_L1

        # combine loss and calculate gradients
        self.loss_G = self.loss_G_GAN + self.loss_G_L1

        self.loss_G.backward()
    #########################################################################################

    def optimize_parameters(self):
        """Update network weights; it will be called in every training iteration."""
        self.forward()  # compute fake images: G(A)
        # update D
        self.set_requires_grad(self.netD, True)  # enable backprop for D
        self.optimizer_D.zero_grad()  # set D's gradients to zero
        self.backward_D()  # calculate gradients for D
        self.optimizer_D.step()  # update D's weights
        # update G
        self.set_requires_grad(self.netD, False)  # D requires no gradients when optimizing G
        self.optimizer_G.zero_grad()  # set G's gradients to zero
        self.backward_G()  # calculate gradients for G
        self.optimizer_G.step()  # update G's weights

    #########################################################################################

    def get_visual_samples(self, dataset):
        """Return visualization images. train.py will display these images with visdom, and save the images to a HTML"""
        visuals_dict = OrderedDict()
        n_maps = 3
        n_generator_runs = 6
        map_id = 1

        for scene_data in dataset:
            is_valid, real_agents, conditioning = pre_process_scene_data(scene_data, self.num_agents,
                                                                         self.agent_feat_vec_coord_labels,
                                                                         self.polygon_name_order, self.device)
            if not is_valid:
                continue
            real_map = conditioning['map_feat']
            real_agents_feat_dicts = agents_feat_vecs_to_dicts(real_agents)
            img = visualize_scene_feat(real_agents_feat_dicts, real_map)
            pred_fake = torch.sigmoid(self.netD(conditioning, real_agents)).item()
            visuals_dict[f'map_{map_id}_real_fake_{int(100*pred_fake)}'] = img
            for i_generator_run in range(n_generator_runs):
                fake_agents_feat_vecs = self.netG(conditioning)
                fake_agents_feat_dicts = agents_feat_vecs_to_dicts(fake_agents_feat_vecs)
                img = visualize_scene_feat(fake_agents_feat_dicts, real_map)
                pred_fake = torch.sigmoid(self.netD(conditioning, fake_agents_feat_vecs)).item()
                visuals_dict[f'map_{map_id}_gen_{i_generator_run+1}_PredFake={int(100*pred_fake)}'] = img
            map_id += 1
            if map_id > n_maps:
                break
        return visuals_dict
    #########################################################################################

    #
    # def get_current_visuals(self):
    #     """Return visualization images. train.py will display these images with visdom, and save the images to a HTML"""
    #     visual_ret = OrderedDict()
    #     for name in self.visual_names:
    #         if isinstance(name, str):
    #             # Generate image:
    #             agents_feat_vecs_ = getattr(self, name)
    #             agents_feat_dicts = agents_feat_vecs_to_dicts(agents_feat_vecs_)
    #             visual_ret[name] = visualize_scene_feat(agents_feat_dicts, self.conditioning['map_feat'])
    #     return visual_ret
    # #########################################################################################
