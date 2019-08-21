from __future__ import absolute_import, division, print_function

import copy
import inspect
from types import ModuleType
from typing import Callable

from six import string_types
from tensorflow.python.keras import Model, Sequential
from tensorflow.python.keras import layers as layer_module
from tensorflow.python.keras.engine import base_layer_utils
from tensorflow.python.keras.layers import Activation, Dense, Layer
from tensorflow.python.keras.utils import tf_utils
from tensorflow.python.util import nest
from tensorflow.python.util.tf_export import keras_export

__all__ = ['AdvanceModel', 'Identity', 'Parallel']


class AdvanceModel(Model):
  """ The advance model improving the serialization and deserialization of
  complex Model

  Parameters
  ----------
  parameters : {`dict` or `None`}
    recording the arguments given to `__init__`, recommend passing
    the `locals()` dictionary
  """

  def __init__(self, parameters=None, name=None):
    super(AdvanceModel, self).__init__(name=name)
    self.supports_masking = True
    self._build_input_shape = None

    if parameters is None:
      parameters = {}
    else:
      parameters = dict(parameters)
    kwargs = parameters.pop('kwargs', {})
    parameters.update(kwargs)
    parameters.pop('self', None)
    parameters.pop('__class__', None)
    # tricky recursive reference of overriding classes
    parameters.pop('parameters', None)
    self._parameters = parameters

  @property
  def custom_objects(self):
    """ This property could be overrided to provide custom Layer class
    Expect a dictionary mapping from class name to the class itself for
    deserialization, or, list of modules
    """
    return {}

  @property
  def parameters(self):
    return dict(self._parameters)

  # if default is enable, it mean the build method is not overrided
  # and won't be called during __call__
  @base_layer_utils.default
  def build(self, input_shape=None):
    if self._is_graph_network:
      self._init_graph_network(self.inputs, self.outputs, name=self.name)
    else:
      if input_shape is None:
        if self._build_input_shape is None:
          raise ValueError('You must provide an `input_shape` argument.')
        else:
          input_shape = self._build_input_shape
      input_shape = tuple(input_shape)
      self._build_input_shape = input_shape
      super(AdvanceModel, self).build(input_shape)
    self.built = True

  def __call__(self, inputs, *args, **kwargs):
    # This to make sure input_shapes is recorded after every call
    if self._build_input_shape is None:
      input_list = nest.flatten(inputs)
      input_shapes = None
      if all(hasattr(x, 'shape') for x in input_list):
        input_shapes = nest.map_structure(lambda x: x.shape, inputs)
      self._build_input_shape = input_shapes
    return super(AdvanceModel, self).__call__(inputs, *args, **kwargs)

  def get_config(self):
    source = inspect.getsource(self.__class__)
    default_keys = []
    for t in type.mro(type(self)):
      default_keys += dir(t)

    attributes = {}
    layer_attributes = {}
    for key, val in self.__dict__.items():
      if isinstance(val, Layer):
        layer_attributes[id(val)] = key
      elif 'self.' + key in source and key not in default_keys:
        try:
          attr = getattr(self, key)
          if not inspect.ismethod(attr) and \
            not isinstance(attr, property) and \
              not isinstance(attr, classmethod):
            attributes[key] = val
        except AttributeError:
          pass

    layer_configs = []
    for layer in self.layers:
      layer_configs.append({
          'class_name': layer.__class__.__name__,
          'config': layer.get_config(),
          'attribute': layer_attributes.get(id(layer), None)
      })

    config = {
        'name': self.name,
        'layers': copy.deepcopy(layer_configs),
        'parameters': self.parameters,
        'build_input_shape': self._build_input_shape,
        'attributes': attributes
    }
    return config

  @classmethod
  def from_config(cls, config, custom_objects=None):
    if 'name' in config:
      name = config['name']
      build_input_shape = config['build_input_shape']
      layer_configs = config['layers']
      parameters = config['parameters']
      attributes = config['attributes']
    else:
      name = None
      build_input_shape = None
      layer_configs = config
      parameters = {}
      attributes = {}
    # create new instance
    if 'name' in inspect.getfullargspec(cls.__init__).args:
      parameters['name'] = name
    model = cls(**parameters)
    # set all the attributes
    for key, val in attributes.items():
      setattr(model, key, val)
    # preprocessing the custom_objects
    if custom_objects is None:
      custom_objects = {}
    if hasattr(model, 'custom_objects'):
      model_objects = model.custom_objects
      if isinstance(model_objects, (tuple, list)):
        for obj in model_objects:
          if isinstance(obj, type):
            custom_objects[obj.__name__] = obj
          elif isinstance(obj, ModuleType):
            for i, j in inspect.getmembers(obj):
              if isinstance(j, type) and issubclass(j, Layer):
                custom_objects[i] = j
          elif inspect.isfunction(obj) or inspect.ismethod(obj):
            custom_objects[obj.__name__] = obj
          else:
            raise ValueError(
                "Cannot process value with type %s for custom_objects" +
                " (module, type or callable are supported)" % str(type(obj)))
      elif isinstance(model_objects, dict):
        custom_objects.update(model_objects)
      else:
        raise ValueError(
            "Class %s should return custom_objects of type dictionary or list,"
            " but the returned value is %s" %
            (str(cls), str(type(model_objects))))
    # deserialize all layers
    layers = []
    for layer_config in layer_configs:
      attr = layer_config.pop('attribute')
      try:
        layer = layer_module.deserialize(layer_config,
                                         custom_objects=custom_objects)
      except ValueError:
        layer = None
      layers.append((attr, layer))
    # build if necessary
    if not model.inputs and build_input_shape is not None:
      model.build(build_input_shape)
    # check if all layers is deserialized, if any Layer is missing
    # that mean the Layer is externally added later after build,
    # then we add it back again to the Model (this logic might be fault)
    if len(model.layers) != len(layers):
      raise RuntimeError("No support for this case")
    return model


class Identity(Layer):

  def __init__(self, name=None):
    super(Identity, self).__init__(name=name)
    self.supports_masking = True

  def call(self, inputs, training=None):
    return inputs

  def compute_output_shape(self, input_shape):
    return input_shape


@keras_export('keras.models.Sequential', 'keras.Sequential')
class Parallel(Sequential):
  """ Similar design to keras `Sequential` but simultanously applying
  all the layer on the input and return all the results.

  This layer is important for implementing multitask learning.
  """

  def call(self, inputs, training=None, mask=None, **kwargs):  # pylint: disable=redefined-outer-name
    if self._is_graph_network:
      if not self.built:
        self._init_graph_network(self.inputs, self.outputs, name=self.name)
      return super(Parallel, self).call(inputs, training=training, mask=mask)

    outputs = []
    for layer in self.layers:
      # During each iteration, `inputs` are the inputs to `layer`, and `outputs`
      # are the outputs of `layer` applied to `inputs`. At the end of each
      # iteration `inputs` is set to `outputs` to prepare for the next layer.
      kw = {}
      argspec = self._layer_call_argspecs[layer].args
      if 'mask' in argspec:
        kw['mask'] = mask
      if 'training' in argspec:
        kw['training'] = training
      # support custom keyword argument also
      for k, v in kwargs.items():
        if k in argspec:
          kw[k] = v

      o = layer(inputs, **kw)
      outputs.append(o)

    return tuple(outputs)

  def compute_output_shape(self, input_shape):
    shape = []
    for layer in self.layers:
      shape.append(layer.compute_output_shape(input_shape))
    return tuple(shape)

  def compute_mask(self, inputs, mask):
    outputs = self.call(inputs, mask=mask)
    return [o._keras_mask for o in outputs]