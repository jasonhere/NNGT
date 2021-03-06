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

""" Graph data strctures in NNGT """

from collections import OrderedDict, defaultdict
import logging
import weakref
from copy import deepcopy

import numpy as np
from numpy.random import randint, uniform
import scipy.sparse as ssp
import scipy.spatial as sptl

import nngt
from nngt.lib import (InvalidArgument, nonstring_container, is_integer,
                      default_neuron, default_synapse, POS, WEIGHT, DELAY,
                      DIST, TYPE, BWEIGHT)
from nngt.lib._frozendict import _frozendict
from nngt.lib.rng_tools import _eprop_distribution
from nngt.lib.logger import _log_message


__all__ = [
    'GroupProperty',
    'NeuralPop',
]

logger = logging.getLogger(__name__)


#-----------------------------------------------------------------------------#
# NeuralPop
#------------------------
#

class NeuralPop(OrderedDict):

    """
    The basic class that contains groups of neurons and their properties.

    :ivar has_models: :obj:`bool`,
        ``True`` if every group has a ``model`` attribute.
    :ivar size: :obj:`int`,
        Returns the number of neurons in the population.
    :ivar syn_spec: :obj:`dict`,
        Dictionary containing informations about the synapses between the
        different groups in the population.
    :ivar is_valid: :obj:`bool`,
        Whether this population can be used to create a network in NEST.
    """

    # number of created populations
    __num_created = 0
    # store weakrefs to created populations
    __pops = weakref.WeakValueDictionary()

    #-------------------------------------------------------------------------#
    # Class attributes and methods

    @classmethod
    def _nest_reset(cls):
        '''
        Reset the _to_nest bool and potential parent networks.
        '''
        for pop in cls.__pops.valuerefs():
            if pop() is not None:
                pop()._to_nest = False
                for g in pop().values():
                    g._to_nest = False
                if pop().parent is not None:
                    pop().parent._nest_gids = None

    @classmethod
    def from_network(cls, graph, *args):
        '''
        Make a NeuralPop object from a network. The groups of neurons are
        determined using instructions from an arbitrary number of
        :class:`~nngt.properties.GroupProperties`.
        '''
        return cls(parent=graph, graph=graph, group_prop=args)
    
    @classmethod
    def from_groups(cls, groups, names=None, syn_spec=None, parent=None,
                    with_models=True):
        '''
        Make a NeuralPop object from a (list of) :class:`~nngt.NeuralGroup`
        object(s).

        .. versionchanged:: 0.8
            Added `syn_spec` parameter.

        Parameters
        ----------
        groups : list of :class:`~nngt.NeuralGroup` objects
            Groups that will be used to form the population.
        names : list of str, optional (default: None)
            Names that can be used as keys to retreive a specific group. If not
            provided, keys will be the position of the group in `groups`,
            stored as a string. In this case, the first group in a population
            named `pop` will be retreived by either `pop[0]` or `pop['0']`.
        parent : :class:`~nngt.Graph`, optional (default: None)
            Parent if the population is created from an exiting graph.
        syn_spec : dict, optional (default: static synapse)
            Dictionary containg a directed edge between groups as key and the
            associated synaptic parameters for the post-synaptic neurons (i.e.
            those of the second group) as value.
            If a 'default' entry is provided, all unspecified connections will
            be set to its value.
        with_model : bool, optional (default: True)
            Whether the groups require models (set to False to use populations
            for graph theoretical purposes, without NEST interaction)

        Example
        -------
        For synaptic properties, if provided in `syn_spec`, all connections
        between groups will be set according to the values.
        Keys can be either group names or types (1 for excitatory, -1 for
        inhibitory). Because of this, several combination can be available for
        the connections between two groups. Because of this, priority is given
        to source (presynaptic properties), i.e. NNGT will look for the entry
        matching the first group name as source before looking for entries
        matching the second group name as target.

        .. code-block:: python

            # we created groups `g1`, `g2`, and `g3`
            prop = {
                ('g1', 'g2'): {'model': 'tsodyks2_synapse', 'tau_fac': 50.},
                ('g1', g3'): {'weight': 100.},
                ...
            }
            pop = NeuronalPop.from_groups(
                [g1, g2, g3], names=['g1', 'g2', 'g3'], syn_spec=prop)

        Note
        ----
        If the population is not generated from an existing
        :class:`~nngt.Graph` and the groups do not contain explicit ids, then
        the ids will be generated upon population creation: the first group, of
        size N0, will be associated the indices 0 to N0 - 1, the second group
        (size N1), will get N0 to N0 + N1 - 1, etc.
        '''
        if not nonstring_container(groups):
            groups = [groups]

        for i, g in enumerate(groups):
            assert g.is_valid(), "Group number " + str(i) + " is invalid."

        gsize = len(groups)
        neurons = []
        names = [str(i) for i in range(gsize)] if names is None else names
        assert len(names) == gsize, "`names` and `groups` must have " +\
                                    "the same size."
        if syn_spec is not None:
            _check_syn_spec(syn_spec, names, groups)

        current_size = 0
        for g in groups:
            # generate the neuron ids if necessary
            ids = g.ids
            if len(ids) == 0:
                ids = list(range(current_size, current_size + g.size))
                g.ids = ids
            current_size += len(ids)
            neurons.extend(ids)
        neurons = list(set(neurons))
        pop = cls(current_size, parent=parent, with_models=with_models)
        for name, g in zip(names, groups):
            pop[name] = g
            g._pop    = weakref.ref(pop)
            g._net    = weakref.ref(parent) if parent is not None else None

        # take care of synaptic connections
        pop._syn_spec = deepcopy(syn_spec if syn_spec is not None else {})
        return pop

    @classmethod
    def uniform(cls, size, neuron_model=default_neuron, neuron_param=None,
                syn_model=default_synapse, syn_param=None, parent=None):
        ''' Make a NeuralPop of identical neurons '''
        neuron_param = {} if neuron_param is None else neuron_param.copy()
        if syn_param is not None:
            assert 'weight' not in syn_param, '`weight` cannot be set here.'
            assert 'delay' not in syn_param, '`delay` cannot be set here.'
            syn_param = syn_param.copy()
        else:
            syn_param = {}
        pop = cls(size, parent)
        pop.create_group("default", range(size), 1, neuron_model, neuron_param)
        pop._syn_spec = {'model': syn_model}
        if syn_param is not None:
            pop._syn_spec.update(syn_param)
        return pop

    @classmethod
    def exc_and_inhib(cls, size, iratio=0.2, en_model=default_neuron,
                      en_param=None, in_model=default_neuron, in_param=None,
                      syn_spec=None, parent=None):
        '''
        Make a NeuralPop with a given ratio of inhibitory and excitatory
        neurons.

        .. versionchanged:: 0.8
            Added `syn_spec` parameter.

        Parameters
        ----------
        size : int
            Number of neurons contained by the population.
        iratio : float, optional (default: 0.2)
            Fraction of the neurons that will be inhibitory.
        en_model : str, optional (default: default_neuron)
            Name of the NEST model that will be used to describe excitatory
            neurons.
        en_param : dict, optional (default: default NEST parameters)
            Parameters of the excitatory neuron model.
        in_model : str, optional (default: default_neuron)
            Name of the NEST model that will be used to describe inhibitory
            neurons.
        in_param : dict, optional (default: default NEST parameters)
            Parameters of the inhibitory neuron model.
        syn_spec : dict, optional (default: static synapse)
            Dictionary containg a directed edge between groups as key and the
            associated synaptic parameters for the post-synaptic neurons (i.e.
            those of the second group) as value. If provided, all connections
            between groups will be set according to the values contained in
            `syn_spec`. Valid keys are:
                - `('excitatory', 'excitatory')`
                - `('excitatory', 'inhibitory')`
                - `('inhibitory', 'excitatory')`
                - `('inhibitory', 'inhibitory')`
        parent : :class:`~nngt.Network`, optional (default: None)
            Network associated to this population.

        See also
        --------
        :func:`nest.Connect` for a description of the dict that can be passed
        as values for the `syn_spec` parameter.
        '''
        num_exc_neurons = int(size*(1-iratio))
        pop = cls(size, parent)
        gExc = pop.create_group(
            "excitatory", range(num_exc_neurons), 1, en_model, en_param)
        gInh = pop.create_group(
            "inhibitory", range(num_exc_neurons, size), -1, in_model, in_param)
        if syn_spec is not None:
            _check_syn_spec(
                syn_spec, ["excitatory", "inhibitory"], pop.values())
            pop._syn_spec = deepcopy(syn_spec)
        else:
            pop._syn_spec = {}
        return pop

    @classmethod
    def copy(cls, pop):
        ''' Copy an existing NeuralPop '''
        new_pop = cls.__init__(parent=pop.parent, with_models=pop.has_models)
        for name, group in pop.items():
            new_pop.create_group(
                name, group.ids, group.model, group.neuron_param)
            new_pop._syn_spec = pop.syn_spec
        return new_pop

    #-------------------------------------------------------------------------#
    # Contructor and instance attributes

    def __init__(self, size=None, parent=None, with_models=True, *args,
                 **kwargs):
        '''
        Initialize NeuralPop instance

        Parameters
        ----------
        size : int, optional (default: 0)
            Number of neurons that the population will contain.
        parent : :class:`~nngt.Network`, optional (default: None)
            Network associated to this population.
        with_models : :class:`bool`
            whether the population's groups contain models to use in NEST
        *args : items for OrderedDict parent
        **kwargs : :obj:`dict`

        Returns
        -------
        pop : :class:`~nngt.NeuralPop` object.
        '''
        self._is_valid = False
        self._desired_size = size if parent is None else parent.node_nb()
        self._size = 0
        self._parent = None if parent is None else weakref.ref(parent)
        # array of strings containing the name of the group where each neuron
        # belongs
        if self._desired_size is None:
            self._neuron_group = None
            self._max_id       = 0
        else:
            self._neuron_group = np.repeat(-1, self._desired_size)
            self._max_id       = len(self._neuron_group) - 1
        if parent is not None and 'group_prop' in kwargs:
            dic = _make_groups(parent, kwargs["group_prop"])
            self._is_valid = True
            self.update(dic)
        self._syn_spec = {}
        self._has_models = with_models
        # whether the network this population represents was sent to NEST
        self._to_nest = False
        # init the OrderedDict
        super(NeuralPop, self).__init__(*args)
        # update class properties
        self.__id = self.__class__.__num_created
        self.__class__.__num_created += 1
        self.__class__.__pops[self.__id] = self

    def __reduce__(self):
        '''
        Overwrite this function to make NeuralPop pickable.
        OrderedDict.__reduce__ returns a 3 to 5 tuple:
        - the first is the class
        - the second is the init args in Py2, empty sequence in Py3
        - the third can be used to store attributes
        - the fourth is None and needs to stay None
        - the last must be kept unchanged: odict_iterator in Py3
        '''
        state    = super(NeuralPop, self).__reduce__()
        last     = state[4] if len(state) == 5 else None
        dic      = state[2]
        od_args  = state[1][0] if state[1] else state[1]
        args     = (dic.get("_size", None), dic.get("_parent", None),
                    dic.get("_has_models", True), od_args)
        newstate = (NeuralPop, args, dic, None, last)
        return newstate

    def __getitem__(self, key):
        if isinstance(key, (int, np.integer)):
            assert key >= 0, "Index must be positive, not {}.".format(key)
            new_key = tuple(self.keys())[key]
            return OrderedDict.__getitem__(self, new_key)
        else:
            return OrderedDict.__getitem__(self, key)

    def __setitem__(self, key, value):
        if self._to_nest:
            raise RuntimeError("Populations items can no longer be modified "
                               "once the network has been sent to NEST!")
        self._validity_check(key, value)
        int_key = None
        if is_integer(key):
            new_key = tuple(self.keys())[key]
            int_key = key
            OrderedDict.__setitem__(self, new_key, value)
        else:
            OrderedDict.__setitem__(self, key, value)
            int_key = list(super(NeuralPop, self).keys()).index(key)

        # set name and parents
        value._name = key
        value._pop  = weakref.ref(self)
        value._net  = self._parent

        # update pop size/max_id
        group_size = len(value.ids)
        max_id     = np.max(value.ids) if group_size != 0 else 0
        _update_max_id_and_size(self, max_id)
        self._neuron_group[value.ids] = int_key
        if -1 in list(self._neuron_group):
            self._is_valid = False
        else:
            if self._desired_size is not None:
                self._is_valid = (self._desired_size == self._size)
            else:
                self._is_valid = True

    def _sent_to_nest(self):
        '''
        Signify to the population and its groups that the network was sent
        to NEST and that therefore properties and groups should no longer
        be modified.
        '''
        self._to_nest = True
        for g in self.values():
            g._to_nest = True

    @property
    def size(self):
        '''
        Number of neurons in this population.
        '''
        return self._size

    @property
    def parent(self):
        '''
        Parent :class:`~nngt.Network`, if it exists, otherwise ``None``.
        '''
        return None if self._parent is None else self._parent()

    @property
    def syn_spec(self):
        '''
        The properties of the synaptic connections between groups.
        Returns a :obj:`dict` containing tuples as keys and dicts of parameters
        as values.

        The keys are tuples containing the names of the groups in the
        population, with the projecting group first (presynaptic neurons) and
        the receiving group last (post-synaptic neurons).

        Example
        -------
        For a population of excitatory ("exc") and inhibitory ("inh") neurons.

        .. code-block:: python

            syn_spec = {
                ("exc", "exc"): {'model': 'stdp_synapse', 'weight': 2.5},
                ("exc", "inh"): {'model': 'static_synapse'},
                ("exc", "inh"): {'model': 'stdp_synapse', 'delay': 5.},
                ("inh", "inh"): {
                    'model': 'stdp_synapse', 'weight': 5.,
                    'delay': ('normal', 5., 2.)}
                }
            }

        .. versionadded:: 0.8
        '''
        return deepcopy(self._syn_spec)

    @syn_spec.setter
    def syn_spec(self, syn_spec):
        raise NotImplementedError('`syn_spec` is not settable yet.')

    @property
    def has_models(self):
        return self._has_models

    @property
    def is_valid(self):
        '''
        Whether the population can be used to create a NEST network.
        '''
        return self._is_valid

    #-------------------------------------------------------------------------#
    # Methods

    def create_group(self, name, neurons, ntype=1, neuron_model=None,
                     neuron_param=None):
        '''
        Create a new groupe from given properties.

        .. versionchanged:: 0.8
            Removed `syn_model` and `syn_param`.

        .. versionchanged:: 1.0
            `neurons` can be an int to signify a desired size for the group
            without actually setting the indices.
        
        Parameters
        ----------
        name : str
            Name of the group.
        neurons : int or array-like
            Desired number of neurons or list of the neurons indices.
        ntype : int, optional (default: 1)
            Type of the neurons : 1 for excitatory, -1 for inhibitory.
        neuron_model : str, optional (default: None)
            Name of a neuron model in NEST.
        neuron_param : dict, optional (default: None)
            Parameters for `neuron_model` in the NEST simulator. If None,
            default parameters will be used.
        '''
        if self._to_nest:
            raise RuntimeError("Groups can no longer be created once the "
                               "network has been sent to NEST!")
        neuron_param = {} if neuron_param is None else neuron_param.copy()
        group        = NeuralGroup(neurons, ntype=ntype,
                                   neuron_model=neuron_model,
                                   neuron_param=neuron_param, name=name)
        group._pop   = weakref.ref(self)
        group._net   = self._parent
        self[name]   = group

    def set_model(self, model, group=None):
        '''
        Set the groups' models.

        Parameters
        ----------
        model : dict
            Dictionary containing the model type as key ("neuron" or "synapse")
            and the model name as value (e.g. {"neuron": "iaf_neuron"}).
        group : list of strings, optional (default: None)
            List of strings containing the names of the groups which models
            should be updated.

        Note
        ----
        By default, synapses are registered as "static_synapse"s in NEST;
        because of this, only the ``neuron_model`` attribute is checked by
        the ``has_models`` function: it will answer ``True`` if all groups
        have a 'non-None' ``neuron_model`` attribute.

        Warning
        -------
        No check is performed on the validity of the models, which means
        that errors will only be detected when building the graph in NEST.
        '''
        if self._to_nest:
            raise RuntimeError("Models cannot be changed after the network "
                               "has been sent to NEST!")
        if group is None:
            group = self.keys()
        try:
            for key, val in model.items():
                for name in group:
                    if key == "neuron":
                        self[name].neuron_model = val
                    elif key == "synapse":
                        self[name].syn_model = val
                    else:
                        raise ValueError(
                            "Model type {} is not valid; choose among 'neuron'"
                            " or 'synapse'.".format(key))
        except:
            if model is not None:
                raise InvalidArgument(
                    "Invalid model dict or group; see docstring.")
        b_has_models = True
        if model is None:
            b_has_models = False
        for group in iter(self.values()):
            b_has_models *= group.has_model
        self._has_models = b_has_models

    def set_neuron_param(self, params, neurons=None, group=None):
        '''
        Set the parameters of specific neurons or of a whole group.

        .. versionadded:: 1.0

        Parameters
        ----------
        params : dict
            Dictionary containing parameters for the neurons. Entries can be
            either a single number (same for all neurons) or a list (one entry
            per neuron).
        neurons : list of ints, optional (default: None)
            Ids of the neurons whose parameters should be modified.
        group : list of strings, optional (default: None)
            List of strings containing the names of the groups whose parameters
            should be updated. When modifying neurons from a single group, it
            is still usefull to specify the group name to speed up the pace.

        Note
        ----
        If both `neurons` and `group` are None, all neurons will be modified.

        Warning
        -------
        No check is performed on the validity of the parameters, which means
        that errors will only be detected when building the graph in NEST.
        '''
        if self._to_nest:
            raise RuntimeError("Parameters cannot be changed after the "
                               "network has been sent to NEST!")

        if neurons is not None:  # specific neuron ids
            groups = []
            # get the groups they could belong to
            if group is not None:
                if nonstring_container(group):
                    groups.extend((self[g] for g in group))
                else:
                    groups.append(self[group])
            else:
                groups.extend(self.values())
            # update the groups parameters
            for g in groups:
                idx = np.where(np.in1d(g.ids, neurons, assume_unique=True))[0]
                # set the properties of the nodes for each entry in params
                for k, v in params.items():
                    default = np.NaN
                    if k in g.neuron_param:
                        default = g.neuron_param[k]
                    elif nngt.get_config('with_nest'):
                        try:
                            import nest
                            try:
                                default = nest.GetDefaults(g.neuron_model, k)
                            except nest.NESTError:
                                pass
                        except ImportError:
                            pass
                    vv      = np.repeat(default, g.size)
                    vv[idx] = v
                    # update
                    g.neuron_param[k] = vv
        else:  # all neurons in one or several groups
            group = self.keys() if group is None else group
            if not nonstring_container(group):
                group = [group]
            start = 0
            for name in group:
                g = self[name]
                for k, v in params.items():
                    if nonstring_container(v):
                        g.neuron_param[k] = v[start:start+g.size]
                    else:
                        g.neuron_param[k] = v
                start += g.size

    def get_param(self, groups=None, neurons=None, element="neuron"):
        '''
        Return the `element` (neuron or synapse) parameters for neurons or
        groups of neurons in the population.

        Parameters
        ----------
        groups : ``str``, ``int`` or array-like, optional (default: ``None``)
            Names or numbers of the groups for which the neural properties
            should be returned.
        neurons : int or array-like, optional (default: ``None``)
            IDs of the neurons for which parameters should be returned.
        element : ``list`` of ``str``, optional (default: ``"neuron"``)
            Element for which the parameters should be returned (either
            ``"neuron"`` or ``"synapse"``).

        Returns
        -------
        param : ``list``
            List of all dictionaries with the elements' parameters.
        '''
        if neurons is not None:
            groups = self._neuron_group[neurons]
        elif groups is None:
            groups = tuple(self.keys())
        key = "neuron_param" if element == "neuron" else "syn_param"
        if isinstance(groups, (str, int, np.integer)):
            return self[groups].properties[key]
        else:
            param = []
            for group in groups:
                param.append(self[group].properties[key])
            return param

    def get_group(self, neurons, numbers=False):
        '''
        Return the group of the neurons.
        
        Parameters
        ----------
        neurons : int or array-like
            IDs of the neurons for which the group should be returned.
        numbers : bool, optional (default: False)
            Whether the group identifier should be returned as a number; if
            ``False``, the group names are returned.
        '''
        names = np.array(tuple(self.keys()), dtype=object)
        if numbers:
            return self._neuron_group[neurons]
        else:
            if self._is_valid:
                return names[self._neuron_group[neurons]]
            else:
                groups = []
                for i in self._neuron_group[neurons]:
                    if i >= 0:
                        groups.append(names[i])
                    else:
                        groups.append(None)
                return groups

    def add_to_group(self, group_name, ids):
        '''
        Add neurons to a specific group.

        Parameters
        ----------
        group_name : str or int
            Name or index of the group.
        ids : list or 1D-array
            Neuron ids.
        '''
        if self._to_nest:
            raise RuntimeError("Groups cannot be changed after the "
                               "network has been sent to NEST!")
        idx = None
        if is_integer(group_name):
            assert 0 <= group_name < len(self), "Group index does not exist."
            idx = group_name
        else:
            idx = list(self.keys()).index(group_name)
        if ids:
            self[group_name].ids += list(ids)
            # update number of neurons
            max_id = np.max(ids)
            _update_max_id_and_size(self, max_id)
            self._neuron_group[np.array(ids)] = idx
            if -1 in list(self._neuron_group):
                self._is_valid = False
            else:
                self._is_valid = True
    
    def _validity_check(self, name, group):
        if self._has_models and not group.has_model:
            raise AttributeError(
                "This NeuralPop requires group to have a model attribute that "
                "is not `None`; to disable this, use `set_model(None)` "
                "method on this NeuralPop instance.")
        elif group.has_model and not self._has_models:
            _log_message(logger, "WARNING",
                         "This NeuralPop is not set to take models into "
                         "account; use the `set_model` method to change its "
                         "behaviour.")


# ----------------------------- #
# NeuralGroup and GroupProperty #
# ----------------------------- #

class NeuralGroup(object):

    """
    Class defining groups of neurons.

    :ivar ids: :obj:`list` of :obj:`int`
        the ids of the neurons in this group.
    :ivar neuron_type: :class:`int`
        the default is ``1`` for excitatory neurons; ``-1`` is for interneurons
    :ivar model: :class:`string`, optional (default: None)
        the name of the model to use when simulating the activity of this group
    :ivar neuron_param: :class:`dict`, optional (default: {})
        the parameters to use (if they differ from the model's defaults)

    Note
    ----
    By default, synapses are registered as ``"static_synapse"`` in NEST;
    because of this, only the ``neuron_model`` attribute is checked by the
    ``has_model`` function.

    Warning
    -------
    Equality between :class:`~nngt.properties.NeuralGroup`s only compares
    the  size and neuronal ``model`` and ``param`` attributes. This means
    that groups differing only by their ``ids`` will register as equal.
    """

    def __init__ (self, nodes=None, ntype=1, neuron_model=None, neuron_param=None,
                  name=None):
        '''
        Create a group of neurons (empty group is default, but it is not a
        valid object for most use cases).

        .. versionchanged:: 0.8
            Removed `syn_model` and `syn_param`.

        Parameters
        ----------
        nodes : int or array-like, optional (default: None)
            Desired size of the group or, a posteriori, NNGT indices of the
            neurons in an existing graph.
        ntype : int, optional (default: 1)
            Type of the neurons (1 for excitatory, -1 for inhibitory).
        neuron_model : str, optional (default: None)
            NEST model for the neuron.
        neuron_param : dict, optional (default: model defaults)
            Dictionary containing the parameters associated to the NEST model.

        Returns
        -------
        A new :class:`~nngt.core.NeuralGroup` instance.
        '''
        assert ntype in (1, -1), "`ntype` can either be 1 or -1."
        neuron_param = {} if neuron_param is None else neuron_param.copy()
        self._has_model = False if neuron_model is None else True
        self._neuron_model = neuron_model
        if nodes is None:
            self._desired_size = None
            self._ids = []
        elif nonstring_container(nodes):
            self._desired_size = None
            self._ids = list(nodes)
        elif is_integer(nodes):
            self._desired_size = nodes
            self._ids = []
        else:
            raise InvalidArgument('`nodes` must be either array-like or int.')
        self._name = "" if name is None else name
        self._nest_gids = None
        self._neuron_param = neuron_param if self._has_model else {}
        self.neuron_type = ntype
        # whether the network this group belongs to was sent to NEST
        self._to_nest = False
        # parents
        self._pop = None
        self._net = None

    def __eq__ (self, other):
        if isinstance(other, NeuralGroup):
            same_size = self.size == other.size
            same_nmodel = ((self.neuron_model == other.neuron_model)
                           * (self.neuron_param == other.neuron_param))
            return same_size*same_nmodel
        else:
            return False

    def __len__(self):
        return self.size

    @property
    def name(self):
        return self._name

    @property
    def neuron_model(self):
        return self._neuron_model

    @neuron_model.setter
    def neuron_model(self, value):
        if self._to_nest:
            raise RuntimeError("Models cannot be changed after the "
                               "network has been sent to NEST!")
        self._neuron_model = value
        self._has_model = False if value is None else self._has_model

    @property
    def neuron_param(self):
        if self._to_nest:
            return _frozendict(self._neuron_param, message="Cannot set " +
                               "neuron params after the network has been " +
                               "sent to NEST!")
        else:
            return self._neuron_param

    @neuron_param.setter
    def neuron_param(self, value):
        if self._to_nest:
            raise RuntimeError("Parameters cannot be changed after the "
                               "network has been sent to NEST!")
        self._neuron_param = value

    @property
    def size(self):
        if self._desired_size is not None:
            return self._desired_size
        return len(self._ids)

    @property
    def ids(self):
        return self._ids

    @ids.setter
    def ids(self, value):
        if self._to_nest:
            raise RuntimeError("Ids cannot be changed after the "
                               "network has been sent to NEST!")
        if self._desired_size != len(value):
            _log_message(logger, "WARNING",
                         'The length of the `ids` passed is not the same as '
                         'the initial size that was declared: {} before '
                         'vs {} now. Setting `ids` anyway, but check your '
                         'code!'.format(self._desired_size, len(value)))
        self._ids = value
        self._desired_size = None

    @property
    def nest_gids(self):
        return self._nest_gids

    @property
    def has_model(self):
        return self._has_model

    @property
    def properties(self):
        dic = {
            "neuron_type": self.neuron_type,
            "neuron_model": self._neuron_model,
            "neuron_param": deepcopy(self._neuron_param)
        }
        return dic

    def is_valid(self):
        '''
        Whether the group can be used in a population: i.e. if it has either
        a size or some ids associated to it.

        .. versionadded:: 1.0
        '''
        return (self._desired_size is not None) or self._ids


class GroupProperty:

    """
    Class defining the properties needed to create groups of neurons from an
    existing :class:`~nngt.GraphClass` or one of its subclasses.

    :ivar size: :class:`int`
        Size of the group.
    :ivar constraints: :class:`dict`, optional (default: {})
        Constraints to respect when building the
        :class:`~nngt.properties.NeuralGroup` .
    :ivar neuron_model: :class:`string`, optional (default: None)
        name of the model to use when simulating the activity of this group.
    :ivar neuron_param: :class:`dict`, optional (default: {})
        the parameters to use (if they differ from the model's defaults)
    """

    def __init__ (self, size, constraints={}, neuron_model=None,
                  neuron_param={}, syn_model=None, syn_param={}):
        '''
        Create a new instance of GroupProperties.

        Notes
        -----
        The constraints can be chosen among:
            - "avg_deg", "min_deg", "max_deg" (:class:`int`) to constrain the
              total degree of the nodes
            - "avg/min/max_in_deg", "avg/min/max_out_deg", to work with the
              in/out-degrees
            - "avg/min/max_betw" (:class:`double`) to constrain the betweenness
              centrality
            - "in_shape" (:class:`nngt.geometry.Shape`) to chose neurons inside
              a given spatial region

        Examples
        --------
        >>> di_constrain = { "avg_deg": 10, "min_betw": 0.001 }
        >>> group_prop = GroupProperties(200, constraints=di_constrain)
        '''
        self.size = size
        self.constraints = constraints
        self.neuron_model = neuron_model
        self.neuron_param = neuron_param
        self.syn_model = syn_model
        self.syn_param = syn_param


def _make_groups(graph, group_prop):
    '''
    Divide `graph` into groups using `group_prop`, a list of group properties
    @todo
    '''
    pass


# ----------- #
# Connections #
# ----------- #

class Connections:

    """
    The basic class that computes the properties of the connections between
    neurons for graphs.
    """

    #-------------------------------------------------------------------------#
    # Class methods

    @staticmethod
    def distances(graph, elist=None, pos=None, dlist=None, overwrite=False):
        '''
        Compute the distances between connected nodes in the graph. Try to add
        only the new distances to the graph. If they overlap with previously
        computed distances, recomputes everything.

        Parameters
        ----------
        graph : class:`~nngt.Graph` or subclass
            Graph the nodes belong to.
        elist : class:`numpy.array`, optional (default: None)
            List of the edges.
        pos : class:`numpy.array`, optional (default: None)
            Positions of the nodes; note that if `graph` has a "position"
            attribute, `pos` will not be taken into account.
        dlist : class:`numpy.array`, optional (default: None)
            List of distances (for user-defined distances)

        Returns
        -------
        new_dist : class:`numpy.array`
            Array containing *ONLY* the newly-computed distances.
        '''
        n = graph.node_nb()
        elist = graph.edges_array if elist is None else elist
        if dlist is not None:
            assert isinstance(dlist, np.ndarray), "numpy.ndarray required in "\
                                                  "Connections.distances"
            graph.set_edge_attribute(DIST, value_type="double", values=dlist)
            return dlist
        else:
            pos = graph._pos if hasattr(graph, "_pos") else pos
            # compute the new distances
            if graph.edge_nb():
                ra_x = pos[elist[:,0], 0] - pos[elist[:,1], 0]
                ra_y = pos[elist[:,0], 1] - pos[elist[:,1], 1]
                ra_dist = np.sqrt( np.square(ra_x) + np.square(ra_y) )
                #~ ra_dist = np.tile( , 2)
                # update graph distances
                graph.set_edge_attribute(DIST, value_type="double",
                                         values=ra_dist, edges=elist)
                return ra_dist
            else:
                return []

    @staticmethod
    def delays(graph=None, dlist=None, elist=None, distribution="constant",
               parameters=None, noise_scale=None):
        '''
        Compute the delays of the neuronal connections.

        Parameters
        ----------
        graph : class:`~nngt.Graph` or subclass
            Graph the nodes belong to.
        dlist : class:`numpy.array`, optional (default: None)
            List of user-defined delays).
        elist : class:`numpy.array`, optional (default: None)
            List of the edges which value should be updated.
        distribution : class:`string`, optional (default: "constant")
            Type of distribution (choose among "constant", "uniform",
            "lognormal", "gaussian", "user_def", "lin_corr", "log_corr").
        parameters : class:`dict`, optional (default: {})
            Dictionary containing the distribution parameters.
        noise_scale : class:`int`, optional (default: None)
            Scale of the multiplicative Gaussian noise that should be applied
            on the weights.

        Returns
        -------
        new_delays : class:`scipy.sparse.lil_matrix`
            A sparse matrix containing *ONLY* the newly-computed weights.
        '''
        elist = np.array(elist) if elist is not None else elist
        if dlist is not None:
            assert isinstance(dlist, np.ndarray), "numpy.ndarray required in "\
                                                  "Connections.delays"
            num_edges = graph.edge_nb() if elist is None else elist.shape[0]
            if len(dlist) != num_edges:
                raise InvalidArgument("`dlist` must have one entry per edge.")
        else:
            parameters["btype"] = parameters.get("btype", "edge")
            parameters["use_weights"] = parameters.get("use_weights", False)
            dlist = _eprop_distribution(graph, distribution, elist=elist,
                                        **parameters)
        # add to the graph container
        if graph is not None:
            graph.set_edge_attribute(
                DELAY, value_type="double", values=dlist, edges=elist)
        return dlist

    @staticmethod
    def weights(graph=None, elist=None, wlist=None, distribution="constant",
                parameters={}, noise_scale=None):
        '''
        Compute the weights of the graph's edges.
        @todo: take elist into account

        Parameters
        ----------
        graph : class:`~nngt.Graph` or subclass
            Graph the nodes belong to.
        elist : class:`numpy.array`, optional (default: None)
            List of the edges (for user defined weights).
        wlist : class:`numpy.array`, optional (default: None)
            List of the weights (for user defined weights).
        distribution : class:`string`, optional (default: "constant")
            Type of distribution (choose among "constant", "uniform",
            "lognormal", "gaussian", "user_def", "lin_corr", "log_corr").
        parameters : class:`dict`, optional (default: {})
            Dictionary containing the distribution parameters.
        noise_scale : class:`int`, optional (default: None)
            Scale of the multiplicative Gaussian noise that should be applied
            on the weights.

        Returns
        -------
        new_weights : class:`scipy.sparse.lil_matrix`
            A sparse matrix containing *ONLY* the newly-computed weights.
        '''
        parameters["btype"] = parameters.get("btype", "edge")
        parameters["use_weights"] = parameters.get("use_weights", False)
        elist = np.array(elist) if elist is not None else elist
        if wlist is not None:
            assert isinstance(wlist, np.ndarray), "numpy.ndarray required in "\
                                                  "Connections.weights"
            num_edges = graph.edge_nb() if elist is None else elist.shape[0]
            if len(wlist) != num_edges:
                raise InvalidArgument("`wlist` must have one entry per edge.")
        else:
            wlist = _eprop_distribution(graph, distribution, elist=elist,
                                        **parameters)
        # for normalize by the inhibitory weight factor
        if graph is not None and graph.is_network():
            if not np.isclose(graph._iwf, 1.):
                adj = graph.adjacency_matrix(types=True, weights=False)
                keep = (adj[elist[:, 0], elist[:, 1]] < 0).A1
                wlist[keep] *= graph._iwf
            
        # add to the graph container
        bwlist = (np.max(wlist) - wlist if np.any(wlist)
                  else np.repeat(0., len(wlist)))
        if graph is not None:
            graph.set_edge_attribute(
                WEIGHT, value_type="double", values=wlist, edges=elist)
            graph.set_edge_attribute(
                BWEIGHT, value_type="double", values=bwlist, edges=elist)
        return wlist

    @staticmethod
    def types(graph, inhib_nodes=None, inhib_frac=None):
        '''
        @todo

        Define the type of a set of neurons.
        If no arguments are given, all edges will be set as excitatory.

        Parameters
        ----------
        graph : :class:`~nngt.Graph` or subclass
            Graph on which edge types will be created.
        inhib_nodes : int, float or list, optional (default: `None`)
            If `inhib_nodes` is an int, number of inhibitory nodes in the graph
            (all connections from inhibitory nodes are inhibitory); if it is a
            float, ratio of inhibitory nodes in the graph; if it is a list, ids
            of the inhibitory nodes.
        inhib_frac : float, optional (default: `None`)
            Fraction of the selected edges that will be set as refractory (if
            `inhib_nodes` is not `None`, it is the fraction of the nodes' edges
            that will become inhibitory, otherwise it is the fraction of all
            the edges in the graph).

        Returns
        -------
        t_list : :class:`~numpy.ndarray`
            List of the edges' types.
        '''
        t_list = np.repeat(1., graph.edge_nb())
        edges = graph.edges_array
        num_inhib = 0
        idx_inhib = []
        if inhib_nodes is None and inhib_frac is None:
            graph.new_edge_attribute("type", "double", val=1.)
            return t_list
        else:
            n = graph.node_nb()
            if inhib_nodes is None:
                # set inhib_frac*num_edges random inhibitory connections
                num_edges = graph.edge_nb()
                num_inhib = int(num_edges*inhib_frac)
                num_current = 0
                while num_current < num_inhib:
                    new = randint(0,num_edges,num_inhib-num_current)
                    idx_inhib = np.unique(np.concatenate((idx_inhib, new)))
                    num_current = len(idx_inhib)
                t_list[idx_inhib.astype(int)] *= -1.
            else:
                # get the dict of inhibitory nodes
                num_inhib_nodes = 0
                idx_nodes = {}
                if nonstring_container(inhib_nodes):
                    idx_nodes = {i: -1 for i in inhib_nodes}
                    num_inhib_nodes = len(idx_nodes)
                if isinstance(inhib_nodes, np.float):
                    if inhib_nodes > 1:
                        raise InvalidArgument(
                            "Inhibitory ratio (float value for `inhib_nodes`) "
                            "must be smaller than 1.")
                        num_inhib_nodes = int(inhib_nodes*n)
                if is_integer(inhib_nodes):
                    num_inhib_nodes = int(inhib_nodes)
                while len(idx_nodes) != num_inhib_nodes:
                    indices = randint(0,n,num_inhib_nodes-len(idx_nodes))
                    di_tmp = { i:-1 for i in indices }
                    idx_nodes.update(di_tmp)
                for v in edges[:,0]:
                    if v in idx_nodes:
                        idx_inhib.append(v)
                idx_inhib = np.unique(idx_inhib)
                # set the inhibitory edge indices
                for v in idx_inhib:
                    idx_edges = np.argwhere(edges[:,0]==v)
                    n = len(idx_edges)
                    if inhib_frac is not None:
                        idx_inh = []
                        num_inh = n*inhib_frac
                        i = 0
                        while i != num_inh:
                            ids = randint(0,n,num_inh-i)
                            idx_inh = np.unique(np.concatenate((idx_inh,ids)))
                            i = len(idx_inh)
                        t_list[idx_inh] *= -1.
                    else:
                        t_list[idx_edges] *= -1.
            graph.set_edge_attribute("type", value_type="double", values=t_list)
            return t_list


# ----- #
# Tools #
# ----- #

def _check_syn_spec(syn_spec, group_names, groups):
    gsize = len(groups)
    # test if all types syn_spec are contained
    alltypes = set(((1, 1), (1, -1), (-1, 1), (-1, -1))).issubset(
        syn_spec.keys())
    # is there more than 1 type?
    types = list(set(g.neuron_type for g in groups))
    mt_type = len(types) > 1
    # check that only allowed entries are present
    edge_keys = []
    for k in syn_spec.keys():
        if isinstance(k, tuple):
            edge_keys.extend(k)
    edge_keys = set(edge_keys)
    allkeys = group_names + types
    assert edge_keys.issubset(allkeys), \
        '`syn_spec` edge entries can only be made from {}.'.format(allkeys)
    # warn if connections might be missing
    nspec = len(edge_keys)
    has_default = len(syn_spec) > nspec
    if mt_type and nspec < gsize**2 and not alltypes and not has_default:
        _log_message(
            logger, "WARNING",
            'There is not one synaptic specifier per inter-group'
            'connection in `syn_spec` and no default model was provided. '
            'Therefore, {} or 4 entries were expected but only {} were '
            'provided. It might be right, but make sure all cases are '
            'covered. Missing connections will be set as "static_'
            'synapse".'.format(gsize**2, nspec))
    for val in syn_spec.values():
        assert 'weight' not in val, '`weight` cannot be set here.'
        assert 'delay' not in val, '`delay` cannot be set here.'


def _update_max_id_and_size(neural_pop, max_id):
    '''
    Update NeuralPop after modification of a NeuralGroup ids.
    '''
    old_max_id   = neural_pop._max_id
    neural_pop._max_id = max(neural_pop._max_id, max_id)
    # update size
    neural_pop._size   = 0
    for g in neural_pop.values():
        neural_pop._size += g.size
    # update the group node property
    if neural_pop._neuron_group is None:
        neural_pop._neuron_group = np.repeat(-1, neural_pop._max_id + 1)
    elif neural_pop._max_id >= len(neural_pop._neuron_group):
        ngroup_tmp = np.repeat(-1, neural_pop._max_id + 1)
        ngroup_tmp[:old_max_id + 1] = neural_pop._neuron_group
        neural_pop._neuron_group = ngroup_tmp
