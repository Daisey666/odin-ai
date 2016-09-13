from __future__ import print_function, division, print_function

import re
from collections import OrderedDict

from odin.utils import struct


def _check_tag(var):
    if not hasattr(var, 'tag'):
        var.tag = struct()


def add_role(var, role):
    r"""Add a role to a given Theano variable.

    Parameters
    ----------
    var : :class:`~tensor.TensorVariable`
        The variable to assign the new role to.
    role : :class:`.VariableRole` instance

    Notes
    -----
    Some roles are subroles of others (e.g. :const:`WEIGHT` is a subrole
    of :const:`PARAMETER`). This function will not add a role if a more
    specific role has already been added. If you need to replace a role
    with a parent role (e.g. replace :const:`WEIGHT` with
    :const:`PARAMETER`) you must do so manually.

    Examples
    --------
    >>> from theano import tensor
    >>> W = tensor.matrix()
    >>> from blocks.roles import PARAMETER, WEIGHT
    >>> add_role(W, PARAMETER)
    >>> print(*W.tag.roles)
    PARAMETER
    >>> add_role(W, WEIGHT)
    >>> print(*W.tag.roles)
    WEIGHT
    >>> add_role(W, PARAMETER)
    >>> print(*W.tag.roles)
    WEIGHT

    """
    _check_tag(var)
    roles = getattr(var.tag, 'roles', [])
    # exclusively process for TRAINING and DEPLOYING mode
    if role.__class__ in (TrainingRole, DeployingRole):
        exclude_role = TrainingRole if role.__class__ is DeployingRole else DeployingRole
        roles = [r for r in roles if not isinstance(r, exclude_role)]
    else: # normali processing
        roles = [old_role for old_role in roles
                 if not isinstance(role, old_role.__class__)]
    # add a role if it isn't in the list
    if not any(isinstance(old_role, role.__class__) for old_role in roles):
        roles += [role]
    var.tag.roles = roles


def add_updates(var, key, value):
    r""" Annotate updates to a given var, hence, this updates will
    be used when create function

    """
    _check_tag(var)
    updates = getattr(var.tag, 'updates', OrderedDict())
    updates[key] = value
    var.tag.updates = updates


def add_auxiliary_variable(var, auxiliary, roles=None):
    r""" Annotate auxiliary variable to a given var

    """
    _check_tag(var)
    auxiliary_variables = getattr(var.tag, 'auxiliary_variables', [])
    add_role(auxiliary, AUXILIARY)
    if roles is not None:
        for role in roles:
            add_role(auxiliary, role)
    auxiliary_variables.append(auxiliary)
    var.tag.auxiliary_variables = list(set(auxiliary_variables))


def has_roles(var, roles, match_all=False, exact=False):
    r"""Test if a variable has given roles taking subroles into account.

    Parameters
    ----------
    var : :class:`~tensor.TensorVariable`
        Variable being queried.
    roles : an iterable of :class:`.VariableRole` instances.
    match_all : bool, optional
        If ``True``, checks if the variable has all given roles.
        If ``False``, any of the roles is sufficient.
        ``False`` by default.
    exact : bool, optional
        If ``True``, use ``==`` for comparison to get exactly same roles.
        If ``False``, use isinstance for comparison, hence, also match the
        decesdant roles.

    """
    if not hasattr(roles, '__iter__'):
        roles = [roles]
    var_roles = getattr(var.tag, 'roles', [])
    if not exact:
        matches = (any(isinstance(var_role, role.__class__) for
                       var_role in var_roles) for role in roles)
    else:
        matches = (any(var_role.__class__ == role.__class__ for
                       var_role in var_roles) for role in roles)
    return all(matches) if match_all else any(matches)


def get_roles(var):
    return getattr(var.tag, 'roles', [])


class VariableRole(object):
    """Base class for all variable roles."""

    def __eq__(self, other):
        return self.__class__ == other.__class__

    def __repr__(self):
        return re.sub(r'(?!^)([A-Z]+)', r'_\1',
                      self.__class__.__name__[:-4]).upper()


# ===========================================================================
# Role for computation
# ===========================================================================
class TrainingRole(VariableRole):
    pass
#: The variable is used for training mode (i.e enalbe dropout out, etc)
TRAINING = TrainingRole()


class DeployingRole(VariableRole):
    pass
#: Override Training role
DEPLOYING = DeployingRole()


# ===========================================================================
# Variational
# ===========================================================================
class VariationalRole(VariableRole):
    pass

#: Variational statistics
VARIATIONAL = VariationalRole()


class VariationalMean(VariationalRole):
    pass
VARIATIONAL_MEAN = VariationalMean()


class VariationalLogsigma(VariationalRole):
    pass
VARIATIONAL_LOGSIGMA = VariationalLogsigma()


# ===========================================================================
# Role for Variable
# ===========================================================================
class CostRole(VariableRole):
    pass

#: A scalar cost that can be used to train or regularize
COST = CostRole()


class ParameterRole(VariableRole):
    pass


#: A parameter of the model
PARAMETER = ParameterRole()


class ActivationParameterRole(ParameterRole):
    pass


#: A parameter of the model
ACTIVATION_PARAMETER = ActivationParameterRole()


class AuxiliaryRole(VariableRole):
    pass


#: Variables added to the graph as annotations
AUXILIARY = AuxiliaryRole()


class WeightRole(ParameterRole):
    pass


#: The weight matrices of linear transformations
WEIGHT = WeightRole()


class BiasRole(ParameterRole):
    pass


#: Biases of linear transformations
BIAS = BiasRole()


class InitialStateRole(ParameterRole):
    pass


#: Initial state of a recurrent network
INITIAL_STATE = InitialStateRole()


class FilterRole(WeightRole):
    pass


#: The filters (kernels) of a convolution operation
FILTER = FilterRole()


class DropoutRole(VariableRole):
    pass


#: Inputs with applied dropout
DROPOUT = DropoutRole()


# ===========================================================================
# Optimizer Algorithm roles
# ===========================================================================
class OptimizerStateRole(VariableRole):
    pass


#: Shared variables used in algorithms updates
OPTIMIZER_STATE = OptimizerStateRole()


class LearningRateRole(VariableRole):
    pass


LEARNING_RATE = LearningRateRole()


# ===========================================================================
# Embedding
# ===========================================================================
class EmbeddingWeights(WeightRole):
    pass


#: weights for embedding operator
EMBEDDING = EmbeddingWeights()


# ===========================================================================
# Batch normalization roles
# ===========================================================================
class BatchNormPopulationStatisticsRole(ParameterRole):
    pass

#: base role for batch normalization population statistics
BATCH_NORM_POPULATION_STATISTICS = BatchNormPopulationStatisticsRole()


class BatchNormPopulationMeanRole(BatchNormPopulationStatisticsRole):
    pass

#: mean activations accumulated over the dataset
BATCH_NORM_POPULATION_MEAN = BatchNormPopulationMeanRole()


class BatchNormPopulationInvStdRole(BatchNormPopulationStatisticsRole):
    pass

#: standard deviations of activations accumulated over the dataset
BATCH_NORM_POPULATION_INVSTD = BatchNormPopulationInvStdRole()


class BatchNormScaleParameterRole(ParameterRole):
    pass

#: role given to the scale parameter, referred to as "scale" (or "gamma") in the
# batch normalization manuscript, applied after normalizing.
BATCH_NORM_SCALE_PARAMETER = BatchNormScaleParameterRole()


class BatchNormShiftParameterRole(BiasRole):
    pass

#: role given to the shift parameter, referred to as "beta" in the
# batch normalization manuscript, applied after normalizing and scaling.
# Inherits from BIAS, because there really is no functional difference
# with a normal bias, and indeed these are the only biases present
# inside a BatchNormalizedMLP.
BATCH_NORM_SHIFT_PARAMETER = BatchNormShiftParameterRole()
