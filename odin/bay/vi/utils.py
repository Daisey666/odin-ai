from __future__ import absolute_import, division, print_function

import types
import warnings
from numbers import Number
from typing import List, Union

import numpy as np
import tensorflow as tf
from odin.utils import as_tuple
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import KBinsDiscretizer
from tensorflow_probability.python.distributions import Distribution, Normal
from typing_extensions import Literal

__all__ = [
    'discretizing',
    'permute_dims',
    'traverse_dims',
    'marginalize_categorical_labels',
]


def _gmm_discretizing_predict(self, X):
  self._check_is_fitted()
  means = self.means_.ravel()
  ids = self._estimate_weighted_log_prob(X).argmax(axis=1)
  # sort by increasing order of means_
  return np.expand_dims(np.argsort(means)[ids], axis=1)


def discretizing(
    *factors: List[np.ndarray],
    independent: bool = True,
    n_bins: int = 5,
    strategy: Literal['uniform', 'quantile', 'kmeans', 'gmm'] = 'quantile',
    return_model: bool = False,
):
  r""" Transform continuous value into discrete

  Note: the histogram discretizer is equal to
    `KBinsDiscretizer(n_bins=n, encode='ordinal', strategy='uniform')`

  Arguments:
    factors : array-like or list of array-like
    independent : a Boolean, if `True` (by default), each factor (i.e. column)
      is discretize independently.
    n_bins : int or array-like, shape (n_features,) (default=5)
      The number of bins to produce. Raises ValueError if ``n_bins < 2``.
    strategy : {'uniform', 'quantile', 'kmeans', 'gmm'}, (default='quantile')
      Strategy used to define the widths of the bins.
      uniform - All bins in each feature have identical widths.
      quantile - All bins in each feature have the same number of points.
      kmeans - Values in each bin have the same nearest center of a 1D
        k-means cluster.
      gmm - using the components (in sorted order of mean) of Gaussian
        mixture to label.
  """
  encode = 'ordinal'
  # onehot - sparse matrix of one-hot encoding and
  # onehot-dense - dense one-hot encoding. Ignored features are always stacked to
  #   the right.
  # ordinal - Return the bin identifier encoded as an integer value.
  strategy = str(strategy).strip().lower()
  if 'histogram' in strategy:
    strategy = 'uniform'
  # ====== GMM base discretizer ====== #
  if 'gmm' in strategy:
    create_gmm = lambda: GaussianMixture(n_components=n_bins,
                                         max_iter=800,
                                         covariance_type='diag',
                                         random_state=1)  # fix random state

    if independent:
      gmm = []
      for f in factors[0].T:
        gm = create_gmm()
        gm.fit(np.expand_dims(f, axis=1))
        gm.predict = types.MethodType(_gmm_discretizing_predict, gm)
        gmm.append(gm)
      transform = lambda x: np.concatenate([
          gm.predict(np.expand_dims(col, axis=1)) for gm, col in zip(gmm, x.T)
      ],
                                           axis=1)
    else:
      gmm = create_gmm()
      gmm.fit(np.expand_dims(factors[0].ravel(), axis=1))
      gmm.predict = types.MethodType(_gmm_discretizing_predict, gmm)
      transform = lambda x: np.concatenate(
          [gmm.predict(np.expand_dims(col, axis=1)) for col in x.T], axis=1)
    disc = gmm
  # ====== start with bins discretizer ====== #
  else:
    disc = KBinsDiscretizer(n_bins=n_bins, encode=encode, strategy=strategy)
    if independent:
      disc.fit(factors[0])
      transform = lambda x: disc.transform(x).astype(np.int64)
    else:
      disc.fit(np.expand_dims(factors[0].ravel(), axis=-1))
      transform = lambda x: np.hstack([
          disc.transform(np.expand_dims(i, axis=-1)).astype(np.int64)
          for i in x.T
      ])
  # ====== returns ====== #
  factors = tuple([transform(i) for i in factors])
  factors = factors[0] if len(factors) == 1 else factors
  if return_model:
    return factors, disc
  return factors


def marginalize_categorical_labels(X: tf.Tensor,
                                   n_classes: int,
                                   dtype: tf.DType = tf.float32):
  """
  Example:
  ```
  # shape: [batch_size * n_labels, n_labels]
  y = marginalize_categorical_labels(batch_size=inputs[0].shape[0],
                                     num_classes=n_labels,
                                     dtype=self.dtype)
  # shape: [batch_size * n_labels, n_dims]
  X = [tf.repeat(i, n_labels, axis=0) for i in inputs]
  ```
  """
  n = X.shape[0]
  if n is None:
    n = tf.shape(X)[0]
  y = tf.expand_dims(tf.eye(n_classes, dtype=dtype), axis=0)
  y = tf.repeat(y, n, axis=0)
  y = tf.reshape(y, (-1, n_classes))
  X = tf.repeat(X, n_classes, axis=0)
  return X, y


@tf.function(autograph=True)
def permute_dims(z):
  r""" Permutation of latent dimensions Algorithm(1):

  ```
    input: matrix-(batch_dim, latent_dim)
    output: matrix-(batch_dim, latent_dim)

    foreach latent_dim:
      shuffle points along batch_dim
  ```


  Arguments:
    z : A Tensor `[batch_size, latent_dim]`

  Reference:
    Kim, H., Mnih, A., 2018. Disentangling by Factorising.
      arXiv:1802.05983 [cs, stat].
  """
  shape = z.shape
  batch_dim, latent_dim = shape[-2:]
  perm = tf.TensorArray(dtype=z.dtype,
                        size=latent_dim,
                        dynamic_size=False,
                        clear_after_read=False,
                        element_shape=shape[:-1])
  ids = tf.range(batch_dim, dtype=tf.int32)
  # iterate over latent dimension
  for i in tf.range(latent_dim):
    # shuffle among minibatch
    z_i = tf.gather(z[..., i], tf.random.shuffle(ids), axis=-1)
    perm = perm.write(i, z_i)
  return tf.transpose(perm.stack(),
                      perm=tf.concat([tf.range(1, tf.rank(z)), (0,)], axis=0))


def traverse_dims(x: Union[np.ndarray, tf.Tensor, Distribution],
                  axis: Union[int, List[int]],
                  min_val: int = -2.0,
                  max_val: int = 2.0,
                  num: int = 11,
                  n_samples: int = 1,
                  mode: Literal['linear', 'quantile', 'gaussian'] = 'linear',
                  return_indices: bool = False,
                  seed: int = 1) -> np.ndarray:
  """Traversing a dimension of a matrix between given range

  Parameters
  ----------
  x : Union[np.ndarray, tf.Tensor, Distribution]
      the array for performing dimension traverse
  axis : Union[int, List[int]]
      a single axis or list of axes for traverse (i.e. which columns of the
      last dimension are for traverse)
  min_val : int, optional
      minimum value of the traverse, by default -2.0
  max_val : int, optional
      maximum value of the traverse, by default 2.0
  num : int, optional
      number of points in the traverse, must be odd number, by default 11
  n_samples : int, optional
      number of samples selected for the traverse, by default 2
  mode : {'linear', 'quantile', 'gaussian'}, optional
      'linear' mode take linear interpolation between the `min_val` and `max_val`.
      'quantile' mode return `num` quantiles based on min and max values inferred
      from the data. 'gaussian' mode takes `num` Gaussian quantiles,
      by default 'linear'

  Returns
  -------
  np.ndarray
      the ndarray with traversed axes

  Example
  --------
  For `n_samples=2`, `num=2`, and `n_latents=2`, the return latents are:
  ```
  [[-2., 0.47],
   [ 0., 0.47],
   [ 2., 0.47],
   [-2., 0.31],
   [ 0., 0.31],
   [ 2., 0.31]]
  ```
  """
  if axis is None:
    axis = list(
        range(
            x.event_shape[-1] if isinstance(x, Distribution) else x.shape[-1]))
  axis = [int(i) for i in tf.nest.flatten(axis)]
  if len(axis) > 1:
    arr = [
        traverse_dims(x,
                      ax,
                      min_val=min_val,
                      max_val=max_val,
                      num=num,
                      n_samples=n_samples,
                      mode=mode,
                      return_indices=return_indices,
                      seed=seed) for ax in axis
    ]
    if return_indices:
      return np.concatenate([a[0] for a in arr], axis=0), \
        np.concatenate([a[1] for a in arr], axis=0)
    return np.concatenate(arr, axis=0)
  axis = axis[0]
  num = int(num)
  assert num % 2 == 1, \
    f'num must be odd number, i.e. centerred at 0, given {num}'
  n_samples = int(n_samples)
  assert num > 1 and n_samples > 0, \
    ("num > 1 and n_samples > 0, "
     f"but given: num={num} n_samples={n_samples}")
  ### check the mode
  all_mode = ('quantile', 'linear', 'gaussian')
  mode = str(mode).strip().lower()
  assert mode in all_mode, \
    f"Only support traverse mode:{all_mode}, but given '{mode}'"
  px = None
  if isinstance(x, Distribution):
    px = x
    x = px.mean()
  elif mode == 'gaussian':
    raise ValueError('A distribution must be provided for mean and stddev '
                     'in Gaussian mode.')
  ### sample
  random_state = np.random.RandomState(seed=seed)
  x_org = np.asarray(x)
  indices = random_state.choice(x.shape[0], size=n_samples, replace=False)
  x = x_org[indices]
  ### ranges
  # z_range is a matrix [n_latents, num]
  # linear range
  if mode == 'linear':
    x_range = np.linspace(min_val, max_val, num=num)
  # min-max quantile
  elif mode == 'quantile':
    x_range = np.linspace(min(x_org[:, axis]), max(x_org[:, axis]), num=num)
  # gaussian quantile
  elif mode == 'gaussian':
    dist = Normal(
        loc=tf.reduce_mean(px.mean()[:, axis]),
        scale=tf.reduce_mean(px.stddev()[:, axis]),
    )
    x_range = []
    for i in np.linspace(1e-5, 1.0 - 1e-5, num=num, dtype=np.float32):
      x_range.append(dist.quantile(i))
    x_range = np.array(x_range)
  ### traverse
  X = np.repeat(x, len(x_range), axis=0)
  indices = np.repeat(indices, len(x_range), axis=0)
  # repeat for each sample
  for i in range(n_samples):
    s = i * len(x_range)
    e = (i + 1) * len(x_range)
    X[s:e, axis] = x_range
  if return_indices:
    return X, indices
  return X
