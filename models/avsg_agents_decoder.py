
import torch
from torch import linalg as LA
import torch.nn as nn
import torch.nn.functional as F
from models.avsg_net_moudules import MLP


#########################################################################################
def project_to_agent_feat(raw_vec):
    # Project the generator output to the feature vectors domain:
    agent_feat = torch.cat([
        # Coordinates 0,1 are centroid x,y - no need to project
        raw_vec[0:2],
        # Coordinates 2,3 are yaw_cos, yaw_sin - project to unit circle
        raw_vec[2:4] / LA.vector_norm(raw_vec[2:4], ord=2),
        # Coordinates 4,5,6 are extent_length, extent_width, speed project to positive numbers
        F.softplus(raw_vec[4:7]),
        # Coordinates 7,8,9 are one-hot vector - project to 3-simplex
        F.softmax(raw_vec[7:10], dim=0)
    ])
    return agent_feat
#########################################################################################


class DecoderUnit(nn.Module):

    def __init__(self, opt, dim_context, dim_out):
        super(DecoderUnit, self).__init__()
        dim_hid = dim_context
        self.device = opt.device
        self.dim_hid = dim_hid
        self.dim_out = dim_out
        self.gru = nn.GRUCell(dim_hid, dim_hid)
        self.input_mlp = MLP(d_in=dim_hid,
                             d_out=dim_hid,
                             d_hid=dim_hid,
                             n_layers=opt.gru_in_layers,
                             device=self.device)
        self.out_mlp = MLP(d_in=dim_hid,
                           d_out=dim_out,
                           d_hid=dim_hid,
                           n_layers=opt.gru_out_layers,
                           device=self.device)
        self.attn_mlp = MLP(d_in=dim_hid,
                           d_out=dim_hid,
                           d_hid=dim_hid,
                           n_layers=opt.gru_attn_layers,
                           device=self.device)
        self.agent_feat_vec_coord_labels = opt.agent_feat_vec_coord_labels
        assert self.agent_feat_vec_coord_labels == ['centroid_x', 'centroid_y', 'yaw_cos', 'yaw_sin',
                                                    'extent_length', 'extent_width', 'speed',
                                                    'is_CAR', 'is_CYCLIST', 'is_PEDESTRIAN']

    def forward(self, context_vec, prev_hidden):
        attn_scores = self.attn_mlp(prev_hidden)
        # the input layer takes in the attention-applied context concatenated with the previous out features
        attn_weights = F.softmax(attn_scores, dim=0)
        attn_applied = attn_weights * context_vec
        gru_input = attn_applied
        gru_input = self.input_mlp(gru_input)
        gru_input = F.relu(gru_input)
        hidden = self.gru(gru_input.unsqueeze(0), prev_hidden.unsqueeze(0))
        hidden = hidden[0]
        output_feat = self.out_mlp(hidden)

        # Project the generator output to the feature vectors domain:
        agent_feat = project_to_agent_feat(output_feat)
        return agent_feat, hidden
#########################################################################################


class AgentsDecoderGRU(nn.Module):
    # based on:
    # * Show, Attend and Tell: Neural Image Caption Generation with Visual Attention  https://arxiv.org/abs/1502.03044\
    # * https://github.com/sgrvinod/a-PyTorch-Tutorial-to-Image-Captioning
    # * https://pytorch.org/tutorials/intermediate/seq2seq_translation_tutorial.html
    # * https://towardsdatascience.com/image-captions-with-attention-in-tensorflow-step-by-step-927dad3569fa

    def __init__(self, opt, device):
        super(AgentsDecoderGRU, self).__init__()
        self.device = device
        self.dim_latent_scene = opt.dim_latent_scene
        self.dim_agents_decoder_hid = opt.dim_agents_decoder_hid
        self.agent_feat_vec_coord_labels = opt.agent_feat_vec_coord_labels
        self.dim_agent_feat_vec = len(opt.agent_feat_vec_coord_labels)
        self.num_agents = opt.num_agents
        self.decoder_unit = DecoderUnit(opt,
                                        dim_context=self.dim_latent_scene,
                                        dim_out=self.dim_agent_feat_vec)

    def forward(self, scene_latent, n_agents):
        prev_hidden = scene_latent
        agents_feat_vec_list = []
        for i_agent in range(n_agents):
            agent_feat, next_hidden = self.decoder_unit(
                context_vec=scene_latent,
                prev_hidden=prev_hidden)
            prev_hidden = next_hidden
            agents_feat_vec_list.append(agent_feat)
        return agents_feat_vec_list


# # Sample hard categorical using "Straight-through" , returns one-hot vector
# stop_flag = F.gumbel_softmax(logits=stop_score, tau=1, hard=True)
# if i_agent > 0 and stop_flag > 0.5:
#     # Stop flag is ignored at i=0, since we want at least one agent (including the AV)  in the scene
#     break
# else:
#########################################################################################
##############################################################################################

class AgentsDecoderMLP(nn.Module):

    def __init__(self, opt, device):
        super(AgentsDecoderMLP, self).__init__()
        self.device = device
        self.dim_latent_scene = opt.dim_latent_scene
        self.dim_agents_decoder_hid = opt.dim_agents_decoder_hid
        self.agent_feat_vec_coord_labels = opt.agent_feat_vec_coord_labels
        self.dim_agent_feat_vec = len(opt.agent_feat_vec_coord_labels)
        self.num_agents = opt.num_agents

        self.decoder = MLP(d_in=self.dim_latent_scene,
                           d_out=self.dim_agent_feat_vec * self.num_agents,
                           d_hid=self.dim_agents_decoder_hid,
                           n_layers=4,
                           device=self.device)

    def forward(self, scene_latent, n_agents):
        assert n_agents == self.num_agents
        out_vec = self.decoder(scene_latent)

        agents_feat_vec_list = []
        for i_agent in range(n_agents):
            output_feat = out_vec[i_agent * self.dim_agent_feat_vec:(i_agent + 1) * self.dim_agent_feat_vec]
            # Project the generator output to the feature vectors domain:
            agent_feat = project_to_agent_feat(output_feat)
            agents_feat_vec_list.append(agent_feat)
        return agents_feat_vec_list


#########