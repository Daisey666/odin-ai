from numbers import Number

import tensorflow as tf
from odin.bay.random_variable import RVmeta
from odin.bay.vi.autoencoder.beta_vae import betaVAE
from odin.bay.vi.losses import get_divergence, maximum_mean_discrepancy


def _clip_binary(x, eps=1e-7):
  # this is ad-hoc value, tested 1e-8 but return NaN for RelaxedSigmoid
  # all the time
  return tf.clip_by_value(x, eps, 1. - eps)


class miVAE(betaVAE):
  r""" Mutual-information VAE

  The algorithm of MI-VAE is as following:
  ```
  1. Compute q(z,c|x) and the KL-Divergence from the prior p(z).
  2. Generatea sample (z, c) from the approximate posterior q.
  3. Compute the conditional p(x|z) and incur the reconstruction loss.
  4. Resample (z_prime, c_prime) ~ p(c,z) from the prior.
  5. Recompute the conditional p(x|z_prime, c_prime) and generate a sample x_prime.
  6. Recompute the approximate posterior q(c|x_prime) and incur the loss for the MI lower bound.
  ```

  Parameters
  ----------
  resample_zprime : a Boolean. if True, use samples from q(z|x) for z_prime
    instead of sampling z_prime from prior.
  kl_factors : a Boolean (default: True).
    If False, only maximize the mutual information of the factors code
    `q(c|X)` and the input `p(X|z, c)`, this is the original configuration
    in the paper.
    If True, encourage factorized code by pushing the KL divergence to the
    prior (multivariate diagonal normal).

  Note
  -----
  Lambda is replaced as gamma in this implementation


  References
  ----------
  Ducau, F.N., Trénous, S. "Mutual Information in Variational Autoencoders".
    (2017) https://github.com/fducau/infoVAE.
  Chen, X., Chen, X., Duan, Y., et al. (2016) "InfoGAN: Interpretable
    Representation Learning by Information Maximizing Generative
    Adversarial Nets". URL : http://arxiv.org/ abs/1606.03657.
  Ducau, F.N. Code:  https://github.com/fducau/infoVAE
  """

  def __init__(self,
               beta: float = 1.0,
               gamma: float = 1.0,
               latents: RVmeta = RVmeta(5,
                                                        'mvndiag',
                                                        projection=True,
                                                        name="Latents"),
               factors: RVmeta = RVmeta(5,
                                                        'mvndiag',
                                                        projection=True,
                                                        name='Factors'),
               resample_zprime: bool = False,
               kl_factors: bool = True,
               **kwargs):
    latents = tf.nest.flatten(latents)
    latents.append(factors)
    self.is_binary_factors = factors.is_binary
    super().__init__(beta=beta, latents=latents, **kwargs)
    self.factors = self.latents[-1]
    self.gamma = tf.convert_to_tensor(gamma, dtype=self.dtype, name='gamma')
    self.resample_zprime = bool(resample_zprime)
    self.kl_factors = bool(kl_factors)

  def decode(self, latents, training=None, mask=None, **kwargs):
    if isinstance(latents, (tuple, list)) and len(latents) > 1:
      latents = tf.concat(latents, axis=-1)
    return super().decode(latents, training=training, mask=mask, **kwargs)

  def elbo_components(self,
                      inputs,
                      training=None,
                      pX_Z=None,
                      qZ_X=None,
                      mask=None,
                      **kwargs):
    # NOTE: the original implementation does not take KL(qC_X||pC),
    # only maximize the mutual information of q(c|X)
    pX_Z, qZ_X = self.call(inputs,
                           training=training,
                           pX_Z=pX_Z,
                           qZ_X=qZ_X,
                           mask=mask,
                           **kwargs)
    pX_Z = tf.nest.flatten(pX_Z)
    qZ_X = tf.nest.flatten(qZ_X)
    llk, kl = super().elbo_components(
        inputs,
        pX_Z=pX_Z,
        qZ_X=qZ_X[:-1] if not self.kl_factors else qZ_X,
        mask=mask,
        training=training)
    # the latents, in the implementation, the author reuse z samples here,
    # but in the algorithm, z_prime is re-sampled from the prior.
    # But, reasonably, we want to hold z_prime fix to z, and c_prime is the
    # only change factor here.
    if not self.resample_zprime:
      z_prime = tf.concat([tf.convert_to_tensor(q) for q in qZ_X[:-1]], axis=-1)
      batch_shape = z_prime.shape[:-1]
    else:
      batch_shape = qZ_X[0].batch_shape
      z_prime = tf.concat(
          [q.KL_divergence.prior.sample(batch_shape) for q in qZ_X[:-1]],
          axis=-1)
    # mutual information code
    qC_X = qZ_X[-1]
    c_prime = qC_X.KL_divergence.prior.sample(batch_shape)
    if self.is_binary_factors:
      c_prime = _clip_binary(c_prime)
    # decoding
    samples = tf.concat([z_prime, c_prime], axis=-1)
    pX_Zprime = self.decode(samples, training=training)
    qC_Xprime = self.encode(pX_Zprime, training=training)[-1]
    # mutual information (we want to maximize this, hence, add it to the llk)
    mi = qC_Xprime.log_prob(c_prime)
    llk['mi'] = self.gamma * mi
    return llk, kl


class SemiInfoVAE(miVAE):
  r""" This idea combining factorVAE (Kim et al. 2018) and
  miVAE (Ducau et al. 2017)

  # TODO
  """

  def __init__(self, alpha=1., **kwargs):
    super().__init__(**kwargs)
    self.alpha = tf.convert_to_tensor(alpha, dtype=self.dtype, name="alpha")

  @property
  def is_semi_supervised(self):
    return True

  def encode(self, inputs, training=None, mask=None, sample_shape=(), **kwargs):
    inputs = tf.nest.flatten(inputs)
    if len(inputs) > len(self.observation):
      inputs = inputs[:len(self.observation)]
    return super().encode(inputs[0] if len(inputs) == 1 else inputs,
                          training=training,
                          mask=mask,
                          sample_shape=sample_shape,
                          **kwargs)

  def _elbo(self, inputs, pX_Z, qZ_X, analytic, reverse, sample_shape, mask,
            training, **kwargs):
    y = None
    if len(inputs) > len(pX_Z):
      y = inputs[-1]
    # don't take KL of qC_X
    llk, div = super(miVAE,
                     self)._elbo(inputs,
                                 pX_Z,
                                 qZ_X[:-1] if not self.kl_factors else qZ_X,
                                 analytic=analytic,
                                 reverse=reverse,
                                 sample_shape=sample_shape,
                                 mask=mask,
                                 training=training,
                                 **kwargs)
    # the latents, in the implementation, the author reuse z samples here,
    # but in the algorithm, z_prime is re-sampled from the prior.
    # But, reasonably, we want to hold z_prime fix to z, and c_prime is the
    # only change factor here.
    if not self.resample_zprime:
      z_prime = tf.concat([tf.convert_to_tensor(q) for q in qZ_X[:-1]], axis=-1)
      batch_shape = z_prime.shape[:-1]
    else:
      batch_shape = qZ_X[0].batch_shape
      z_prime = tf.concat(
          [q.KL_divergence.prior.sample(batch_shape) for q in qZ_X[:-1]],
          axis=-1)
    # mutual information code
    qC_X = qZ_X[-1]
    c_prime = qC_X.KL_divergence.prior.sample(batch_shape)
    if self.is_binary_factors:
      c_prime = _clip_binary(c_prime)
    # decoding
    samples = tf.concat([z_prime, c_prime], axis=-1)
    pX_Zprime = self.decode(samples, training=training)
    qC_Xprime = self.encode(pX_Zprime, training=training)[-1]
    ## mutual information (we want to maximize this, hence, add it to the llk)
    if y is not None:  # label is provided
      # clip the value for RelaxedSigmoid distribution otherwise NaN
      if self.is_binary_factors:
        y = _clip_binary(y)
      ss = qC_Xprime.log_prob(y)
      if mask is not None:
        mi = qC_Xprime.log_prob(c_prime)
        mi = tf.where(tf.reshape(mask, (-1,)), self.alpha * ss, self.gamma * mi)
      else:
        mi = self.alpha * ss
    else:  # no label just use the sampled code
      mi = self.gamma * qC_Xprime.log_prob(c_prime)
    llk['mi'] = mi
    return llk, div
