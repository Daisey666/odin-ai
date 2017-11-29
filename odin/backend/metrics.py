from __future__ import print_function, division, absolute_import

import numpy as np
import tensorflow as tf

from odin.utils import is_number
from odin.config import get_epsilon

from .role import AccuracyValue, return_roles, DifferentialLoss, ConfusionMatrix
from .tensor import argsort, dimshuffle
from .helpers import is_tensor


EPSILON = get_epsilon()


# ===========================================================================
# Distance measurement
# ===========================================================================
def LevenshteinDistance(s1, s2):
    ''' Implementation of the wikipedia algorithm, optimized for memory
    Reference: http://rosettacode.org/wiki/Levenshtein_distance#Python
    '''
    if len(s1) > len(s2):
        s1, s2 = s2, s1
    distances = range(len(s1) + 1)
    for index2, char2 in enumerate(s2):
        newDistances = [index2 + 1]
        for index1, char1 in enumerate(s1):
            if char1 == char2:
                newDistances.append(distances[index1])
            else:
                newDistances.append(1 + min((distances[index1],
                                             distances[index1 + 1],
                                             newDistances[-1])))
        distances = newDistances
    return distances[-1]


def LER(y_true, y_pred, return_mean=True):
    ''' This function calculates the Labelling Error Rate (PER) of the decoded
    networks output sequence (out) and a target sequence (tar) with Levenshtein
    distance and dynamic programming. This is the same algorithm as commonly used
    for calculating the word error rate (WER), or phonemes error rate (PER).

    Parameters
    ----------
    y_true : ndarray (nb_samples, seq_labels)
        true values of sequences
    y_pred : ndarray (nb_samples, seq_labels)
        prediction values of sequences

    Returns
    -------
    return : float
        Labelling error rate
    '''
    if not hasattr(y_true[0], '__len__') or isinstance(y_true[0], str):
        y_true = [y_true]
    if not hasattr(y_pred[0], '__len__') or isinstance(y_pred[0], str):
        y_pred = [y_pred]

    results = []
    for ytrue, ypred in zip(y_true, y_pred):
        results.append(LevenshteinDistance(ytrue, ypred) / len(ytrue))
    if return_mean:
        return np.mean(results)
    return results


# ===========================================================================
# Losses
# ===========================================================================
@return_roles(DifferentialLoss)
def bayes_crossentropy(y_pred, y_true, nb_classes=None, reduction=tf.reduce_mean,
                       name="BayesCrossentropy"):
    with tf.variable_scope(name):
        y_pred_shape = y_pred.get_shape()
        if y_pred_shape.ndims == 1 or y_pred_shape[-1].value == 1:
            if y_pred_shape.ndims == 1:
                y_pred = tf.expand_dims(y_pred, -1)
            y_pred0 = 1. - y_pred
            y_pred = tf.concat([y_pred0, y_pred], axis=-1)
        # get number of classes
        if y_true.get_shape().ndims == 1:
            if nb_classes is None:
                raise Exception('y_pred and y_true must be one_hot encoded, '
                                'otherwise you have to provide nb_classes.')
            y_true = tf.one_hot(y_true, depth=nb_classes)
        elif nb_classes is None:
            nb_classes = y_true.get_shape()[1].value
        # avoid numerical instability with _EPSILON clipping
        y_pred = tf.clip_by_value(y_pred, EPSILON, 1.0 - EPSILON)
        # ====== check distribution ====== #
        distribution = tf.reduce_sum(y_true, axis=0)
        # probability distribution of each class
        prob_distribution = dimshuffle(distribution / tf.reduce_sum(distribution),
                                       ('x', 0))
        # we need to clip the prior probability distribution also
        prob_distribution = tf.clip_by_value(
            prob_distribution, EPSILON, 1.0 - EPSILON)
        # ====== init confusion info loss ====== #
        # weighted by y_true
        loss = y_true * tf.log(y_pred)
        loss = - 1 / nb_classes * tf.reduce_sum(loss / prob_distribution, axis=1)
        return reduction(loss)


@return_roles(DifferentialLoss)
def bayes_binary_crossentropy(y_pred, y_true):
    y_pred = tf.concat([1 - y_pred, y_pred], axis=-1)
    y_true = tf.one_hot(tf.cast(y_true, 'int32'), depth=2)
    return bayes_crossentropy(y_pred, y_true, nb_classes=2)


@return_roles(DifferentialLoss)
def binary_hinge_loss(predictions, targets, delta=1, log_odds=None,
                      binary=True):
    """Computes the binary hinge loss between predictions and targets.
    .. math:: L_i = \\max(0, \\delta - t_i p_i)
    Parameters
    ----------
    predictions : Theano tensor
        Predictions in (0, 1), such as sigmoidal output of a neural network
        (or log-odds of predictions depending on `log_odds`).
    targets : Theano tensor
        Targets in {0, 1} (or in {-1, 1} depending on `binary`), such as
        ground truth labels.
    delta : scalar, default 1
        The hinge loss margin
    log_odds : bool, default None
        ``False`` if predictions are sigmoid outputs in (0, 1), ``True`` if
        predictions are sigmoid inputs, or log-odds. If ``None``, will assume
        ``True``, but warn that the default will change to ``False``.
    binary : bool, default True
        ``True`` if targets are in {0, 1}, ``False`` if they are in {-1, 1}
    Returns
    -------
    Theano tensor
        An expression for the element-wise binary hinge loss
    Notes
    -----
    This is an alternative to the binary cross-entropy loss for binary
    classification problems.
    Note that it is a drop-in replacement only when giving ``log_odds=False``.
    Otherwise, it requires log-odds rather than sigmoid outputs. Be aware that
    depending on the Theano version, ``log_odds=False`` with a sigmoid
    output layer may be less stable than ``log_odds=True`` with a linear layer.
    """
    if log_odds is None:  # pragma: no cover
        raise FutureWarning(
            "The `log_odds` argument to `binary_hinge_loss` will change "
            "its default to `False` in a future version. Explicitly give "
            "`log_odds=True` to retain current behavior in your code, "
            "but also check the documentation if this is what you want.")
        log_odds = True
    if not log_odds:
        predictions = tf.log(predictions / (1 - predictions))
    if binary:
        targets = 2 * targets - 1
    predictions, targets = align_targets(predictions, targets)
    return theano.tensor.nnet.relu(delta - predictions * targets)


@return_roles(DifferentialLoss)
def multiclass_hinge_loss(predictions, targets, delta=1):
    """Computes the multi-class hinge loss between predictions and targets.
    .. math:: L_i = \\max_{j \\not = p_i} (0, t_j - t_{p_i} + \\delta)
    Parameters
    ----------
    predictions : Theano 2D tensor
        Predictions in (0, 1), such as softmax output of a neural network,
        with data points in rows and class probabilities in columns.
    targets : Theano 2D tensor or 1D tensor
        Either a vector of int giving the correct class index per data point
        or a 2D tensor of one-hot encoding of the correct class in the same
        layout as predictions (non-binary targets in [0, 1] do not work!)
    delta : scalar, default 1
        The hinge loss margin
    Returns
    -------
    Theano 1D tensor
        An expression for the item-wise multi-class hinge loss
    Notes
    -----
    This is an alternative to the categorical cross-entropy loss for
    multi-class classification problems
    """
    num_cls = predictions.shape[1]
    if targets.ndim == predictions.ndim - 1:
        targets = theano.tensor.extra_ops.to_one_hot(targets, num_cls)
    elif targets.ndim != predictions.ndim:
        raise TypeError('rank mismatch between targets and predictions')
    corrects = predictions[targets.nonzero()]
    rest = theano.tensor.reshape(predictions[(1 - targets).nonzero()],
                                 (-1, num_cls - 1))
    rest = theano.tensor.max(rest, axis=1)
    return theano.tensor.nnet.relu(rest - corrects + delta)


@return_roles(AccuracyValue)
def binary_accuracy(y_pred, y_true, threshold=0.5, reduction=tf.reduce_mean,
                    name="BinaryAccuracy"):
    """ Non-differentiable """
    with tf.variable_scope(name):
        if y_pred.get_shape().ndims > 1:
            y_pred = tf.reshape(y_pred, (-1,))
        if y_true.get_shape().ndims > 1:
            y_true = tf.reshape(y_true, (-1,))
        y_pred = tf.greater_equal(y_pred, threshold)
        match_values = tf.cast(tf.equal(tf.cast(y_pred, 'int32'),
                                        tf.cast(y_true, 'int32')),
                               dtype='int32')
        return reduction(match_values)


@return_roles(AccuracyValue)
def categorical_accuracy(y_pred, y_true, top_k=1, reduction=tf.reduce_mean,
                         name="CategoricalAccuracy"):
    """ Non-differentiable """
    with tf.variable_scope(name):
        if y_true.get_shape().ndims == y_pred.get_shape().ndims:
            y_true = tf.argmax(y_true, axis=-1)
        elif y_true.get_shape().ndims != y_pred.get_shape().ndims - 1:
            raise TypeError('rank mismatch between y_true and y_pred')
        if top_k == 1:
            # standard categorical accuracy
            top = tf.argmax(y_pred, axis=-1)
            y_true = tf.cast(y_true, top.dtype.base_dtype)
            match_values = tf.equal(top, y_true)
        else:
            match_values = tf.nn.in_top_k(y_pred, tf.cast(y_true, 'int32'),
                                          k=top_k)
        match_values = tf.cast(match_values, dtype='float32')
        return reduction(match_values)


@return_roles(ConfusionMatrix)
def confusion_matrix(y_pred, y_true, labels, name='ConfusionMatrix'):
    """
    Computes the confusion matrix of given vectors containing
    actual observations and predicted observations.
    Parameters
    ----------
    pred : 1-d or 2-d tensor variable
    actual : 1-d or 2-d tensor variable
    labels : array, shape = [nb_classes], int (nb_classes)
        List of labels to index the matrix. This may be used to reorder
        or select a subset of labels.
        If none is given, those that appear at least once
        in ``y_true`` or ``y_pred`` are used in sorted order.

    """
    with tf.variable_scope(name):
        from tensorflow.contrib.metrics import confusion_matrix
        if y_true.get_shape().ndims == 2:
            y_true = tf.argmax(y_true, -1)
        elif y_true.get_shape().ndims != 1:
            raise ValueError('actual must be 1-d or 2-d tensor variable')
        if y_pred.get_shape().ndims == 2:
            y_pred = tf.argmax(y_pred, -1)
        elif y_pred.get_shape().ndims != 1:
            raise ValueError('pred must be 1-d or 2-d tensor variable')
        # check valid labels
        if is_number(labels):
            labels = int(labels)
        elif hasattr(labels, '__len__'):
            labels = len(labels)
        # transpose to match the format of sklearn
        return tf.transpose(
            confusion_matrix(y_pred, y_true, num_classes=labels))


def to_llr(x, name="LogLikelihoodRatio"):
    ''' Convert a matrix of probabilities into log-likelihood ratio
    :math:`LLR = log(\\frac{prob(data|target)}{prob(data|non-target)})`
    '''
    if not is_tensor(x):
        x /= np.sum(x, axis=-1, keepdims=True)
        x = np.clip(x, 10e-8, 1. - 10e-8)
        return np.log(x / (np.cast(1., x.dtype) - x))
    else:
        with tf.variable_scope(name):
            x /= tf.reduce_sum(x, axis=-1, keepdims=True)
            x = tf.clip_by_value(x, 10e-8, 1. - 10e-8)
            return tf.log(x / (tf.cast(1., x.dtype.base_dtype) - x))


# ===========================================================================
# Speech task metrics
# ===========================================================================
def to_llh(x):
    ''' Convert a matrix of probabilities into log-likelihood
    :math:`LLH = log(prob(data|target))`
    '''
    if not is_tensor(x):
        x /= np.sum(x, axis=-1, keepdims=True)
        x = np.clip(x, 10e-8, 1. - 10e-8)
        return np.log(x)
    else:
        x /= tf.reduce_sum(x, axis=-1, keepdims=True)
        x = tf.clip_by_value(x, 10e-8, 1. - 10e-8)
        return tf.log(x)


def Cavg(y_llr, y_true, cluster_idx=None,
         Ptar=0.5, Cfa=1., Cmiss=1.,
         probability_input=False):
    ''' Fast calculation of Cavg (for only 1 clusters)

    Parameters
    ----------
    y_llr: (nb_samples, nb_classes)
        log likelihood ratio: llr = log (P(data|target) / P(data|non-target))
    y_true: numpy array of shape (nb_samples,)
        Class labels.
    cluster_idx: list,
        Each element is a list that represents a particular language
        cluster and contains all class labels that belong to the cluster.
    Ptar: float, optional
        Probability of a target trial.
    Cfa: float, optional
        Cost for False Acceptance error.
    Cmiss: float, optional
        Cost for False Rejection error.
    probability_input: boolean
        if True, `y_llr` is the output probability from softmax and perform
        llr transform for `y_llr`

    Returns
    -------
    cluster_cost: numpy array of shape (n_clusters,)
        It contains average percentage costs for each cluster as defined by
        NIST LRE-15 language detection task. See
        http://www.nist.gov/itl/iad/mig/upload/LRE15_EvalPlan_v22-3.pdf
    total_cost: float
        An average percentage cost over all clusters.

    '''
    # ====== For tensorflow ====== #
    if is_tensor(y_llr) and is_tensor(y_true):
        if probability_input:
            y_llr = tf.log(y_llr / (1 - y_llr))
        thresh = np.log(Cfa / Cmiss) - np.log(Ptar / (1 - Ptar))
        nb_classes = y_llr.get_shape()[1].value
        if isinstance(y_true, (list, tuple)):
            y_true = np.asarray(y_true)
        if y_true.get_shape().ndims == 1:
            y_true = tf.one_hot(y_true, depth=nb_classes, axis=-1)
        y_true = tf.cast(y_true, y_llr.dtype.base_dtype)
        # ====== statistics ====== #
        # invert of y_true, False Negative mask
        y_false = 1. - y_true
        y_positive = tf.cast(tf.greater_equal(y_llr, thresh),
                             y_llr.dtype.base_dtype)
        # invert of y_positive
        y_negative = tf.cast(tf.less(y_llr, thresh), y_llr.dtype.base_dtype)
        distribution = tf.clip_by_value(
            tf.reduce_sum(y_true, axis=0), 10e-8, 10e8) # no zero values
        # ====== Pmiss ====== #
        miss = tf.reduce_sum(y_true * y_negative, axis=0)
        Pmiss = 100 * (Cmiss * Ptar * miss) / distribution
        # ====== Pfa ====== # This calculation give different results
        fa = tf.reduce_sum(y_false * y_positive, axis=0)
        Pfa = 100 * (Cfa * (1 - Ptar) * fa) / distribution
        Cavg = tf.reduce_mean(Pmiss) + tf.reduce_mean(Pfa) / (nb_classes - 1)
        return Cavg
    # ====== for numpy ====== #
    if probability_input:
        y_llr = np.clip(y_llr, 10e-8, 1. - 10e-8)
        y_llr = np.log(y_llr / (1. - y_llr))
    if cluster_idx is None:
        cluster_idx = [list(range(0, y_llr.shape[-1]))]
    # ensure everything is numpy ndarray
    y_true = np.asarray(y_true)
    y_llr = np.asarray(y_llr)
    # threshold
    thresh = np.log(Cfa / Cmiss) - np.log(Ptar / (1 - Ptar))
    cluster_cost = np.zeros(len(cluster_idx))
    for k, cluster in enumerate(cluster_idx):
        L = len(cluster) # number of languages in a cluster
        fa = 0
        fr = 0
        for lang_i in cluster:
            N = np.sum(y_true == lang_i, dtype='float32') # number of samples for lang_i
            N = max(N, 1.) # prevent divide by 0, which give NaN return
            for lang_j in cluster:
                if lang_i == lang_j:
                    err = np.sum(y_llr[y_true == lang_i, lang_i] < thresh) / N
                    fr += err
                else:
                    err = np.sum(y_llr[y_true == lang_i, lang_j] >= thresh) / N
                    fa += err
        # Calculate procentage
        cluster_cost[k] = 100 * (Cmiss * Ptar * fr + Cfa * (1 - Ptar) * fa / (L - 1)) / L
    total_cost = np.mean(cluster_cost)
    return cluster_cost, total_cost


def compute_minDCF(fpr, fnr, Cmiss=1, Cfa=1, Ptrue=0.5):
    """ Estimating the min value of the  detection
    cost function (DCF)

    Parameters
    ----------
    fpr: array, [n_samples]
        miss rate or false positive rate
    fnr: array, [n_samples]
        miss rate or false positive rate
    Cmiss: scalar
        weight for false positive mistakes
    Cfa: scalar
        weight for false negative mistakes
    Ptrue: scalar [0., 1.]
        prior probability of positive cases.

    Return
    ------
    min_DCF: scalar
        minimum value of the detection cost function for
        a given detection error trade-off curve
    Pmiss_optimum: scalar
        the correcponding miss
    Pfa_optimum: scalar
        and false alarm trade-off probabilities.

    """
    Pmiss, Pfa = fpr, fnr
    assert Pmiss.shape == Pfa.shape
    Pfalse = 1 - Ptrue
    # detection cost function vector
    DCF_vector = (Cmiss * Pmiss * Ptrue) + \
        (Cfa * Pfa * Pfalse)
    # get the optimal value and corresponding index
    min_idx = np.argmin(DCF_vector)
    min_val = DCF_vector[min_idx]
    return min_val, Pmiss[min_idx], Pfa[min_idx]


def compute_AUC(x, y, reorder=False):
    """Compute Area Under the Curve (AUC) using the trapezoidal rule

    This is a general function, given points on a curve.  For computing the
    area under the ROC-curve, see :func:`roc_auc_score`.  For an alternative
    way to summarize a precision-recall curve, see
    :func:`average_precision_score`.

    Parameters
    ----------
    x : array, shape = [n]
        x coordinates.
    y : array, shape = [n]
        y coordinates.
    reorder : boolean, optional (default=False)
        If True, assume that the curve is ascending in the case of ties, as for
        an ROC curve. If the curve is non-ascending, the result will be wrong.

    Returns
    -------
    auc : float

    Examples
    --------
    >>> import numpy as np
    >>> from sklearn import metrics
    >>> y = np.array([1, 1, 2, 2])
    >>> pred = np.array([0.1, 0.4, 0.35, 0.8])
    >>> fpr, tpr, thresholds = metrics.roc_curve(y, pred, pos_label=2)
    >>> metrics.auc(fpr, tpr)
    0.75

    """
    from sklearn.metrics import auc
    return auc(x, y, reorder)


def roc_curve(y_true, y_score, pos_label=None, sample_weight=None,
              drop_intermediate=True):
    """Compute Receiver operating characteristic (ROC)

    @copy from sklearn for convenience

    Note: this implementation is restricted to the binary classification task.

    Parameters
    ----------

    y_true : array, shape = [n_samples]
        True binary labels in range {0, 1} or {-1, 1}.  If labels are not
        binary, pos_label should be explicitly given.
    y_score : array, shape = [n_samples]
        Target scores, can either be probability estimates of the positive
        class, confidence values, or non-thresholded measure of decisions
        (as returned by "decision_function" on some classifiers).
    pos_label : int or str, default=None
        Label considered as positive and others are considered negative.
    sample_weight : array-like of shape = [n_samples], optional
        Sample weights.
    drop_intermediate : boolean, optional (default=True)
        Whether to drop some suboptimal thresholds which would not appear
        on a plotted ROC curve. This is useful in order to create lighter
        ROC curves.

    Returns
    -------
    fpr : array, shape = [>2]
        Increasing false positive rates such that element i is the false
        positive rate of predictions with score >= thresholds[i].
    tpr : array, shape = [>2]
        Increasing true positive rates such that element i is the true
        positive rate of predictions with score >= thresholds[i].
    thresholds : array, shape = [n_thresholds]
        Decreasing thresholds on the decision function used to compute
        fpr and tpr. `thresholds[0]` represents no instances being predicted
        and is arbitrarily set to `max(y_score) + 1`.

    Notes
    -----
    Since the thresholds are sorted from low to high values, they
    are reversed upon returning them to ensure they correspond to both ``fpr``
    and ``tpr``, which are sorted in reversed order during their calculation.

    References
    ----------
    .. [1] `Wikipedia entry for the Receiver operating characteristic
            <https://en.wikipedia.org/wiki/Receiver_operating_characteristic>`_

    Examples
    --------
    >>> import numpy as np
    >>> from sklearn import metrics
    >>> y = np.array([1, 1, 2, 2])
    >>> scores = np.array([0.1, 0.4, 0.35, 0.8])
    >>> fpr, tpr, thresholds = metrics.roc_curve(y, scores, pos_label=2)
    >>> fpr
    array([ 0. ,  0.5,  0.5,  1. ])
    >>> tpr
    array([ 0.5,  0.5,  1. ,  1. ])
    >>> thresholds
    array([ 0.8 ,  0.4 ,  0.35,  0.1 ])

    """
    from sklearn.metrics import roc_curve
    return roc_curve(y_true, y_score, pos_label,
                     sample_weight, drop_intermediate)


def prc_curve(y_true, y_probas, pos_label=None,
              sample_weight=None):
    """Compute precision-recall pairs for different probability thresholds

    Note: this implementation is restricted to the binary classification task.

    The precision is the ratio ``tp / (tp + fp)`` where ``tp`` is the number of
    true positives and ``fp`` the number of false positives. The precision is
    intuitively the ability of the classifier not to label as positive a sample
    that is negative.

    The recall is the ratio ``tp / (tp + fn)`` where ``tp`` is the number of
    true positives and ``fn`` the number of false negatives. The recall is
    intuitively the ability of the classifier to find all the positive samples.

    The last precision and recall values are 1. and 0. respectively and do not
    have a corresponding threshold.  This ensures that the graph starts on the
    x axis.

    Read more in the :ref:`User Guide <precision_recall_f_measure_metrics>`.

    Parameters
    ----------
    y_true : array, shape = [n_samples]
        True targets of binary classification in range {-1, 1} or {0, 1}.
    y_probas : array, shape = [n_samples]
        Estimated probabilities or decision function.
    pos_label : int or str, default=None
        The label of the positive class
    sample_weight : array-like of shape = [n_samples], optional
        Sample weights.

    Returns
    -------
    precision : array, shape = [n_thresholds + 1]
        Precision values such that element i is the precision of
        predictions with score >= thresholds[i] and the last element is 1.
    recall : array, shape = [n_thresholds + 1]
        Decreasing recall values such that element i is the recall of
        predictions with score >= thresholds[i] and the last element is 0.
    thresholds : array, shape = [n_thresholds <= len(np.unique(probas_pred))]
        Increasing thresholds on the decision function used to compute
        precision and recall.

    Examples
    --------
    >>> import numpy as np
    >>> from sklearn.metrics import precision_recall_curve
    >>> y_true = np.array([0, 0, 1, 1])
    >>> y_scores = np.array([0.1, 0.4, 0.35, 0.8])
    >>> precision, recall, thresholds = precision_recall_curve(
    ...     y_true, y_scores)
    >>> precision  # doctest: +ELLIPSIS
    array([ 0.66...,  0.5       ,  1.        ,  1.        ])
    >>> recall
    array([ 1. ,  0.5,  0.5,  0. ])
    >>> thresholds
    array([ 0.35,  0.4 ,  0.8 ])

    """
    from sklearn.metrics import precision_recall_curve
    return precision_recall_curve(y_true, y_probas, pos_label, sample_weight)


def det_curve(y_true=None, y_score=None, pos_label=None, sample_weight=None,
              true_scores=None, false_scores=None):
    """Detection Error Tradeoff
    Compute error rates for different probability thresholds

    @Original implementaion from NIST
    The function is adapted to take input format same as
    NIST original code and `sklearn.metrics`

    Note: this implementation is restricted to the binary classification task.

    Parameters
    ----------
    y_true : array, shape = [n_samples]
        True targets of binary classification in range {-1, 1} or {0, 1}.
    y_score : array, shape = [n_samples]
        Estimated probabilities or decision function.
    pos_label : int, optional (default=None)
        The label of the positive class
    sample_weight : array-like of shape = [n_samples], optional
        Sample weights.
    true_scores: array, shape = [n_true_samples]
    false_scores:  array, shape = [n_false_samples]
        are detection output scores for a set of detection trials,
        given that the target hypothesis is true (false).
        (By convention, the more positive the score, the more
        likely is the target hypothesis.)

    Returns
    -------
    with `n_samples = n_true_samples + n_false_samples`
    fpr or P_miss : array, shape = [n_samples]
        A rate of false positives, or miss probabilities
    fnr or P_fa: array, shape = [n_samples]
        A rate of false negatives, or false alarm probabilities
    thresholds : array, shape = [n_samples]
        increasing score values

    References
    ----------
    .. [1] `Wikipedia entry for Detection error tradeoff
            <https://en.wikipedia.org/wiki/Detection_error_tradeoff>`_
    .. [2] `The DET Curve in Assessment of Detection Task Performance
            <http://www.itl.nist.gov/iad/mig/publications/storage_paper/det.pdf>`_
    .. [3] `2008 NIST Speaker Recognition Evaluation Results
            <http://www.itl.nist.gov/iad/mig/tests/sre/2008/official_results/>`_
    .. [4] `DET-Curve Plotting software for use with MATLAB
            <http://www.itl.nist.gov/iad/mig/tools/DETware_v2.1.targz.htm>`_

    Examples
    --------
    >>> import numpy as np
    >>> from odin import backend as K
    >>> y_true = np.array([0, 0, 1, 1])
    >>> y_scores = np.array([0.1, 0.4, 0.35, 0.8])
    >>> fps, fns, thresholds = K.metrics.det_curve(y_true, y_scores)
    >>> print(fpr)
    array([ 0.5,  0.5,  0. ])
    >>> print(fnr)
    array([ 0. ,  0.5,  0.5])
    >>> print(thresholds)
    array([ 0.35,  0.4 ,  0.8 ])
    """
    # ====== preprocessing ====== #
    if true_scores is None or false_scores is None:
        if y_true is None or y_score is None:
            raise ValueError("`y_true` and `y_score` must be specified when "
                             "`true_scores` or `false_scores` are missing.")
        if pos_label is None:
            pos_label = 1
        y_true = np.array(y_true)
        # get true scores
        true_idx = (y_true == pos_label)
        if true_scores is None:
            true_scores = y_score[true_idx]
        # get false scores
        false_idx = (y_true != pos_label)
        if false_scores is None:
            false_scores = y_score[false_idx]
        if len(false_scores) + len(true_scores) != len(y_true):
            raise RuntimeError("There are %d positive samples, and %d negative "
                               "samples, but there are total %d samples." %
                               (len(true_scores), len(false_scores), len(y_true)))
    # ====== start ====== #
    nb_true = max(true_scores.shape)
    nb_false = max(false_scores.shape)
    total = nb_true + nb_false
    dtype = true_scores.dtype
    # ====== create and sort the scores ====== #
    scores = np.empty(shape=(total, 2), dtype=dtype)
    scores[0:nb_false, 0] = false_scores
    scores[0:nb_false, 1] = 0.
    scores[nb_false:, 0] = true_scores
    scores[nb_false:, 1] = 1.
    # sort 2nd column decending order
    idx = np.argsort(scores[:, 1], axis=0, kind='mergsort')
    # now sort 1st column ascending
    idx = idx[np.argsort(scores[:, 0], axis=0, kind='mergsort')]
    scores = scores[idx]
    # ====== calculate miss and fa rate ====== #
    sumtrue = np.cumsum(scores[:, 1], axis=0, dtype='float64')
    sumfalse = nb_false - (np.arange(1, total + 1) - sumtrue)
    Pmiss = sumtrue / nb_true
    Pfa = sumfalse / nb_false
    threshold = scores[0, :]
    return Pmiss, Pfa, threshold


# ===========================================================================
# helper function
# ===========================================================================
def jacobian_regularize(hidden, params):
    """ Computes the jacobian of the hidden layer with respect to
    the input, reshapes are necessary for broadcasting the
    element-wise product on the right axis
    """
    hidden = hidden * (1 - hidden)
    L = tf.expand_dims(hidden, 1) * tf.expand_dims(params, 0)
    # Compute the jacobian and average over the number of samples/minibatch
    L = tf.reduce_sum(tf.pow(L, 2)) / hidden.shape[0]
    return tf.reduce_mean(L)


def correntropy_regularize(x, sigma=1.):
    """
    Note
    ----
    origin implementation from seya:
    https://github.com/EderSantana/seya/blob/master/seya/regularizers.py
    Copyright (c) EderSantana
    """
    return -tf.reduce_sum(tf.reduce_mean(tf.exp(x**2 / sigma), axis=0)) / tf.sqrt(2 * np.pi * sigma)


def kl_gaussian(mu, logsigma,
                prior_mu=0., prior_logsigma=0.):
    """ KL-divergence between two gaussians.
    Useful for Variational AutoEncoders. Use this as an activation regularizer

    For taking kl_gaussian as variational regularization, you can take mean of
    the return matrix

    Parameters:
    -----------
    mean, logsigma: parameters of the input distributions
    prior_mean, prior_logsigma: paramaters of the desired distribution (note the
        log on logsigma)


    Return
    ------
    matrix: (n_samples, n_features)

    Note
    ----
    origin implementation from:
    https://github.com/Philip-Bachman/ICML-2015/blob/master/LogPDFs.py
    Copyright (c) Philip Bachman
    """
    if is_number(prior_mu):
        prior_mu = tf.convert_to_tensor(prior_mu, name='prior_mu',
            dtype=mu.dtype.base_dtype)
    if is_number(prior_logsigma):
        prior_logsigma = tf.convert_to_tensor(
            prior_logsigma, name='prior_logsigma',
            dtype=logsigma.dtype.base_dtype)
    gauss_klds = 0.5 * (2 * (prior_logsigma - logsigma) +
            (tf.exp(2 * logsigma) / tf.exp(2 * prior_logsigma)) +
            (tf.pow((mu - prior_mu), 2.0) / tf.exp(2 * prior_logsigma)) - 1.0)
    return gauss_klds
