#!/usr/bin/env python
#-*- coding:utf-8 -*-
#
# This file is part of the NNGT project to generate and analyze
# neuronal networks and their activity.
# Copyright (C) 2015-2017  Tanguy Fardet
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

""" Network generation for NEST """

import nngt
import nngt.generation as ng


''' Create groups with different parameters '''
# adaptive spiking neurons
base_params = {
    'E_L': -60., 'V_th': -55., 'b': 10., 'tau_w': 100.,
    'V_reset': -65., 't_ref': 1., 'g_L': 10., 'C_m': 250.
}
# oscillators
params1, params2 = base_params.copy(), base_params.copy()
params1.update({'E_L': -65., 'b': 30., 'I_e': 350., 'tau_w': 400.})
# bursters
params2.update({'b': 20., 'V_reset': -50., 'tau_w': 500.})

oscill = nngt.NeuralGroup(
    nodes=400, model='aeif_psc_alpha', neuron_param=params1,
    syn_model='tsodyks2_synapse', syn_param={'U': 0.5})
burst = nngt.NeuralGroup(
    nodes=200, model='aeif_psc_alpha', neuron_param=params2,
    syn_model='tsodyks2_synapse')
adapt = nngt.NeuralGroup(
    nodes=200, model='aeif_psc_alpha', neuron_param=base_params,
    syn_model='tsodyks2_synapse')

'''
Create the population that will represent the neuronal
network from these groups
'''
pop = nngt.NeuralPop.from_groups(
    [oscill, burst, adapt],
    names=['oscillators', 'bursters', 'adaptive'])

'''
Create the network from this population,
using a Gaussian in-degree
'''
net = ng.gaussian_degree(
    100., 15., population=pop, weights=1500.)

'''
Send the network to NEST, monitor and simulate
'''
import nngt.simulation as ns
import nest

nest.SetKernelStatus({'local_num_threads': 4})

gids = net.to_nest()

recorders, records = ns.monitor_groups(pop.keys(), net)

nest.Simulate(1000.)

ns.plot_activity(recorders, records, network=net, show=True)
